#!/usr/bin/env python3
"""Blind pairwise design evals via OpenRouter.

Feeds all 15 matching slide pairs in one request and writes one JSON result
per battle (plus an aggregated results/eval_results.json). Designer identities
are stored only in metadata — the judge prompt never names the generators.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from itertools import combinations
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
DATA_DIR = ROOT / "data"
BATTLES_CSV = DATA_DIR / "battles.csv"
AGGREGATE_JSON = RESULTS_DIR / "eval_results.json"
PROMPT_PATH = ROOT / "eval-prompt.md"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DESIGNERS: dict[str, dict[str, str]] = {
    "nexdoc-design": {
        "id": "nexdoc-design",
        "label": "NexDoc Design",
        "folder": "nexdoc-design",
    },
    "claude": {
        "id": "claude",
        "label": "Claude (Fable)",
        "folder": "claude",
    },
    "GPT": {
        "id": "GPT",
        "label": "ChatGPT 5.6 Sol High (Codex)",
        "folder": "GPT",
    },
}

JUDGES: dict[str, dict[str, Any]] = {
    "muse": {
        "key": "muse",
        "label": "Muse Spark 1.3 max",
        "model": "meta/muse-spark-1.3",
        "effort": "max",
    },
    "grok": {
        "key": "grok",
        "label": "Grok 4.6 high",
        "model": "x-ai/grok-4.6",
        "effort": "high",
    },
    "gemini": {
        "key": "gemini",
        "label": "Gemini 3.8 Flash",
        "model": "google/gemini-3.8-flash",
        # Flash has no max; high is the strongest supported effort.
        "effort": "high",
    },
    "opus": {
        "key": "opus",
        "label": "Opus 5 max",
        "model": "anthropic/claude-opus-5",
        "effort": "max",
    },
}

SLIDE_COUNT = 15
MAX_RETRIES = 4
TIMEOUT_SECONDS = 900
# Anthropic many-image requests reject any dimension above 2000px.
MAX_IMAGE_DIM = 2000
CSV_FIELDS = [
    "brief_id",
    "model_a",
    "model_b",
    "judge_model",
    "winner",
    "judge_platform",
    "position_swap",
    "battle_id",
]


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def slide_path(designer_id: str, slide: int) -> Path:
    folder = ROOT / DESIGNERS[designer_id]["folder"]
    return folder / f"proof-{slide:02d}.png"


_IMAGE_CACHE: dict[tuple[str, int], str] = {}
_IMAGE_LOCK = threading.Lock()


def encode_image(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        width, height = image.size
        scale = min(1.0, MAX_IMAGE_DIM / max(width, height))
        if scale < 1.0:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        data = buffer.getvalue()
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


def encode_slide(designer_id: str, slide: int) -> str:
    key = (designer_id, slide)
    with _IMAGE_LOCK:
        cached = _IMAGE_CACHE.get(key)
    if cached:
        return cached
    encoded = encode_image(slide_path(designer_id, slide))
    with _IMAGE_LOCK:
        _IMAGE_CACHE[key] = encoded
    return encoded


def message_text(raw: dict[str, Any]) -> str:
    message = (raw.get("choices") or [{}])[0].get("message") or {}
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
    parsed = message.get("parsed")
    if parsed is not None:
        parts.append(json.dumps(parsed) if not isinstance(parsed, str) else parsed)
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n".join(part for part in parts if part).strip()


def extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model response")
    return json.loads(cleaned[start : end + 1])


def normalize_winner(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace("_", " ").replace("-", " ")
    if value in {"design a", "a", "designer a"}:
        return "Design A"
    if value in {"design b", "b", "designer b"}:
        return "Design B"
    if value in {"tie", "draw", "equal"}:
        return "Tie"
    raise ValueError(f"Unrecognized winner value: {raw!r}")


def normalize_slides(payload: dict[str, Any]) -> list[dict[str, Any]]:
    slides = payload.get("slides")
    if not isinstance(slides, list) or len(slides) != SLIDE_COUNT:
        raise ValueError(f"Expected {SLIDE_COUNT} slide objects, got {type(slides)} / {len(slides) if isinstance(slides, list) else 'n/a'}")

    normalized = []
    for index, item in enumerate(slides, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Slide {index} is not an object")
        slide_no = int(item.get("slide", index))
        if slide_no != index:
            raise ValueError(f"Expected slide {index}, got {slide_no}")
        normalized.append(
            {
                "slide": slide_no,
                "winner": normalize_winner(item.get("winner")),
                "reasoning": str(item.get("reasoning") or "").strip(),
            }
        )
    return normalized


def battle_id(judge_key: str, design_a: str, design_b: str, swapped: bool) -> str:
    swap = "swap" if swapped else "ab"
    return f"{judge_key}__{design_a}_vs_{design_b}__{swap}"


def battle_path(bid: str) -> Path:
    return RESULTS_DIR / f"{bid}.json"


def build_user_content(prompt: str, design_a: str, design_b: str) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"{prompt.rstrip()}\n\n"
                "The 15 matching slide pairs follow. For each slide you will see "
                "Design A then Design B. Compare only matching slides. Do not infer "
                "or mention who generated either deck. Respond with JSON only."
            ),
        }
    ]
    for slide in range(1, SLIDE_COUNT + 1):
        content.append({"type": "text", "text": f"Design A — Slide {slide}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": encode_slide(design_a, slide)},
            }
        )
        content.append({"type": "text", "text": f"Design B — Slide {slide}"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": encode_slide(design_b, slide)},
            }
        )
    return content


def post_openrouter(api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nexdoc/nxd-design-evals",
            "X-Title": "NexDoc DesignEval",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    error = payload.get("error")
    if error:
        message = error.get("message") if isinstance(error, dict) else str(error)
        code = error.get("code") if isinstance(error, dict) else None
        raise RuntimeError(f"OpenRouter error {code}: {message}")
    return payload


def openrouter_chat(
    api_key: str,
    model: str,
    effort: str,
    content: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        # Max effort can consume most of this budget; keep headroom for the JSON.
        "max_tokens": 64000,
        "reasoning": {"effort": effort, "exclude": True},
        "usage": {"include": True},
        "response_format": {"type": "json_object"},
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return post_openrouter(api_key, payload)
        except RuntimeError as exc:
            last_error = exc
            message = str(exc).lower()
            if payload.get("response_format") and (
                "response_format" in message or "json_object" in message
            ):
                print("    dropping response_format and retrying", flush=True)
                payload.pop("response_format", None)
                continue
            retryable = any(
                token in str(exc)
                for token in ("402", "408", "409", "429", "500", "502", "503", "504", "520", "522", "524")
            )
            if not retryable or attempt == MAX_RETRIES:
                raise
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {error_body[:2000]}")
            lowered = error_body.lower()
            if payload.get("response_format") and (
                "response_format" in lowered or "json_object" in lowered
            ):
                print("    dropping response_format and retrying", flush=True)
                payload.pop("response_format", None)
                continue
            retryable = exc.code in {402, 408, 409, 429, 500, 502, 503, 504, 520, 522, 524}
            if not retryable or attempt == MAX_RETRIES:
                raise last_error from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == MAX_RETRIES:
                raise
        sleep_for = min(60, 2 ** attempt) + random.random()
        print(f"    retry {attempt}/{MAX_RETRIES} after {sleep_for:.1f}s: {last_error}", flush=True)
        time.sleep(sleep_for)

    raise last_error or RuntimeError("OpenRouter request failed")


def resolve_winner_id(winner: str, design_a: str, design_b: str) -> str:
    if winner == "Design A":
        return design_a
    if winner == "Design B":
        return design_b
    return "Tie"


def run_battle(
    api_key: str,
    prompt: str,
    judge: dict[str, Any],
    design_a: str,
    design_b: str,
    swapped: bool,
    force: bool,
) -> dict[str, Any]:
    bid = battle_id(judge["key"], design_a, design_b, swapped)
    path = battle_path(bid)
    if path.exists() and not force:
        print(f"  skip existing {path.name}", flush=True)
        return json.loads(path.read_text())

    print(
        f"  running {judge['label']} | A={design_a} B={design_b} | swap={swapped}",
        flush=True,
    )
    started = time.time()
    last_parse_error: Exception | None = None
    raw: dict[str, Any] = {}
    text = ""
    slides: list[dict[str, Any]] = []
    for parse_attempt in range(1, 3):
        raw = openrouter_chat(
            api_key=api_key,
            model=judge["model"],
            effort=judge["effort"],
            content=build_user_content(prompt, design_a, design_b),
        )
        text = message_text(raw)
        try:
            slides = normalize_slides(extract_json(text))
            last_parse_error = None
            break
        except (ValueError, json.JSONDecodeError) as exc:
            last_parse_error = exc
            fail_dir = RESULTS_DIR / "_failures"
            fail_dir.mkdir(exist_ok=True)
            (fail_dir / f"{bid}__attempt{parse_attempt}.json").write_text(
                json.dumps({"error": str(exc), "text": text, "raw": raw}, indent=2)[:200000]
                + "\n"
            )
            print(
                f"    parse failed ({exc}); saved raw response, retrying"
                if parse_attempt == 1
                else f"    parse failed again ({exc})",
                flush=True,
            )
    if last_parse_error:
        raise last_parse_error
    usage = raw.get("usage") or {}
    result = {
        "id": bid,
        "metadata": {
            "judge_key": judge["key"],
            "judge_label": judge["label"],
            "judge_model": judge["model"],
            "reasoning_effort": judge["effort"],
            "design_a": design_a,
            "design_a_label": DESIGNERS[design_a]["label"],
            "design_b": design_b,
            "design_b_label": DESIGNERS[design_b]["label"],
            "position_swap": swapped,
            "slide_count": SLIDE_COUNT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.time() - started, 2),
            "usage": usage,
            "openrouter_id": raw.get("id"),
        },
        "slides": slides,
        "raw_text": text,
    }
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"    wrote {path.name} in {result['metadata']['elapsed_seconds']}s "
        f"tokens={usage.get('total_tokens', '?')} cost={usage.get('cost', '?')}",
        flush=True,
    )
    return result


def collect_battles() -> list[dict[str, Any]]:
    battles = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        if path.name == AGGREGATE_JSON.name:
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("slides") and payload.get("metadata"):
            battles.append(payload)
    return battles


def write_aggregate(battles: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "designers": DESIGNERS,
        "judges": {
            key: {k: v for k, v in spec.items()}
            for key, spec in JUDGES.items()
        },
        "battle_count": len(battles),
        "battles": [
            {k: v for k, v in battle.items() if k != "raw_text"}
            for battle in battles
        ],
    }
    AGGREGATE_JSON.write_text(json.dumps(payload, indent=2) + "\n")


def write_battles_csv(battles: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = []
    for battle in battles:
        meta = battle["metadata"]
        for slide in battle["slides"]:
            rows.append(
                {
                    "brief_id": slide["slide"],
                    "model_a": meta["design_a"],
                    "model_b": meta["design_b"],
                    "judge_model": meta["judge_model"],
                    "winner": resolve_winner_id(
                        slide["winner"], meta["design_a"], meta["design_b"]
                    ),
                    "judge_platform": "openrouter",
                    "position_swap": str(meta["position_swap"]).lower(),
                    "battle_id": battle["id"],
                }
            )
    with BATTLES_CSV.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_csv_list(value: str | None, valid: set[str], label: str) -> list[str]:
    if not value:
        return sorted(valid, key=lambda item: list(valid).index(item) if item in valid else item)
    items = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in items if item not in valid]
    if unknown:
        raise SystemExit(f"Unknown {label}: {', '.join(unknown)}. Valid: {', '.join(sorted(valid))}")
    return items


def planned_jobs(
    judge_keys: list[str],
    designer_ids: list[str],
    include_swap: bool,
    smoke: bool,
) -> list[tuple[dict[str, Any], str, str, bool]]:
    pairs = list(combinations(designer_ids, 2))
    jobs: list[tuple[dict[str, Any], str, str, bool]] = []
    swaps = [False, True] if include_swap else [False]

    if smoke:
        # Two cheap/fast-ish checks covering two judges and two designer pairs.
        return [
            (JUDGES["gemini"], "nexdoc-design", "claude", False),
            (JUDGES["grok"], "nexdoc-design", "GPT", False),
        ]

    for judge_key in judge_keys:
        judge = JUDGES[judge_key]
        for left, right in pairs:
            for swapped in swaps:
                design_a, design_b = (right, left) if swapped else (left, right)
                jobs.append((judge, design_a, design_b, swapped))
    return jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run NexDoc DesignEval pairwise battles via OpenRouter")
    parser.add_argument("--smoke", action="store_true", help="Run two battles to verify the pipeline")
    parser.add_argument("--all", action="store_true", help="Run the full 3-designer x 4-judge x position-swap matrix")
    parser.add_argument("--judges", help="Comma-separated judge keys: muse,grok,gemini,opus")
    parser.add_argument("--designers", help="Comma-separated designer ids: nexdoc-design,claude,GPT")
    parser.add_argument("--no-swap", action="store_true", help="Skip position-swapped rematches")
    parser.add_argument("--force", action="store_true", help="Re-run battles even if result JSON exists")
    parser.add_argument("--dry-run", action="store_true", help="Print planned battles without calling the API")
    parser.add_argument("--concurrency", type=int, default=3, help="Parallel OpenRouter requests (default 3)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key and not args.dry_run:
        raise SystemExit("OPENROUTER_API_KEY is not set")

    if not PROMPT_PATH.exists():
        raise SystemExit(f"Missing judge prompt: {PROMPT_PATH}")
    for designer in DESIGNERS:
        missing = [str(slide_path(designer, n)) for n in range(1, SLIDE_COUNT + 1) if not slide_path(designer, n).exists()]
        if missing:
            raise SystemExit(f"Missing slides for {designer}: {missing[0]}")

    judge_keys = parse_csv_list(args.judges, set(JUDGES), "judge")
    if args.judges:
        # Preserve caller order rather than the default key order.
        judge_keys = [item.strip() for item in args.judges.split(",") if item.strip()]
    else:
        judge_keys = list(JUDGES)

    designer_ids = parse_csv_list(args.designers, set(DESIGNERS), "designer")
    if args.designers:
        designer_ids = [item.strip() for item in args.designers.split(",") if item.strip()]
    else:
        designer_ids = list(DESIGNERS)

    if not args.smoke and not args.all and not args.judges and not args.designers:
        raise SystemExit("Pass --smoke, --all, or --judges/--designers to select a run")

    include_swap = not args.no_swap and not args.smoke
    jobs = planned_jobs(judge_keys, designer_ids, include_swap, args.smoke)
    RESULTS_DIR.mkdir(exist_ok=True)

    print(f"Planned {len(jobs)} battle(s).", flush=True)
    for judge, design_a, design_b, swapped in jobs:
        print(
            f"  - {judge['label']} | A={design_a} ({DESIGNERS[design_a]['label']}) "
            f"| B={design_b} ({DESIGNERS[design_b]['label']}) | swap={swapped}"
        )
    if args.dry_run:
        return 0

    prompt = PROMPT_PATH.read_text()
    failures: list[str] = []
    workers = max(1, args.concurrency)

    def _run(job: tuple[dict[str, Any], str, str, bool]) -> None:
        judge, design_a, design_b, swapped = job
        run_battle(api_key, prompt, judge, design_a, design_b, swapped, args.force)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run, job): job for job in jobs}
        for future in as_completed(futures):
            judge, design_a, design_b, swapped = futures[future]
            bid = battle_id(judge["key"], design_a, design_b, swapped)
            try:
                future.result()
            except Exception as exc:
                failures.append(f"{bid}: {exc}")
                print(f"    FAILED {bid}: {exc}", flush=True)

    battles = collect_battles()
    write_aggregate(battles)
    write_battles_csv(battles)
    print(f"Wrote {AGGREGATE_JSON.relative_to(ROOT)} ({len(battles)} battles) and {BATTLES_CSV.relative_to(ROOT)}")
    if failures:
        print("Failures:")
        for item in failures:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
