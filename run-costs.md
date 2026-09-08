# Run Metrics

## Generation

Using **GPT 5.6 Sol High** in Codex — 20 minutes. 56 requests, 5.85 million tokens, 96.8% cache hit rate. **$1.94**. Final design: [slide-deck-chat-gpt-5.6-high-codex.pdf](./slide-deck-chat-gpt-5.6-high-codex.pdf).

Using **NexDoc Design** — 17 minutes. **$0.54**. Final design: [slide-deck-nexdoc-design.pdf](./slide-deck-nexdoc-design.pdf).

Using **Claude Code with Fable** as the main agent and Opus and Sonnet as assistants — 1 hour 40 minutes. 280+ requests, 31.6 million tokens, 96.1% cache hit rate. **$36.81**. Final design: [slide-deck-claude-fable.pdf](./slide-deck-claude-fable.pdf).

## Judging (OpenRouter, 8 September 2026)

`run_evals.py --all`: 24 deck-vs-deck battles (3 generator pairs × 2 A/B positions × 4 judges). Each request included all 15 matching slide pairs. 360 slide-level judgments written to `data/battles.csv`.

| Judge | OpenRouter slug | Effort | Battles | Tokens | OpenRouter cost |
| --- | --- | --- | ---: | ---: | ---: |
| Gemini 3.8 Flash | `google/gemini-3.8-flash` | high | 6 | 252k | $0.28 |
| Muse Spark 1.3 max | `meta/muse-spark-1.3` | max | 6 | 826k | $1.23 |
| Grok 4.6 high | `x-ai/grok-4.6` | high | 6 | 514k | $1.24 |
| Opus 5 max | `anthropic/claude-opus-5` | max | 6 | 594k | $3.43 |
| **Total** | | | **24** | **~2.2M** | **$6.18** |

Wall time for the successful matrix was on the order of ~15–20 minutes of parallel OpenRouter calls (concurrency 3), plus retries for an Anthropic 2000px many-image limit and one in-flight credit 402.

## Combined

| Stage | Cost |
| --- | ---: |
| Generate three decks | $39.29 |
| Judge 24 pairwise battles | $6.18 |
| **Total this benchmark run** | **$45.47** |
