# Run Metrics

## Generation

Using **GPT 5.6 Sol High** in Codex — 20 minutes. 56 requests, 5.85 million tokens, 96.8% cache hit rate. **$1.94**. Final design: [slide-deck-chat-gpt-5.6-high-codex.pdf](./slide-deck-chat-gpt-5.6-high-codex.pdf).

Using **NexDoc Design** — 17 minutes. **$0.54**. Final design: [slide-deck-nexdoc-design.pdf](./slide-deck-nexdoc-design.pdf).

Using **Claude Code with Fable 5.1** as the main agent and Opus and Sonnet as assistants — 1 hour 40 minutes. 280+ requests, 31.6 million tokens, 96.1% cache hit rate. **$36.81**. Final design: [slide-deck-claude-fable.pdf](./slide-deck-claude-fable.pdf).

Using **Claude Code with Opus 5** as the main agent and Opus and Sonnet as assistants — 55 minutes. 260+ requests, 28.1 million tokens, 96.1% cache hit rate. **$29.11**. Final design: [slide-deck-claude-opus-5.pdf](./slide-deck-claude-opus-5.pdf).

## Judging (OpenRouter, 8 September 2026)

`run_evals.py --all`: 48 deck-vs-deck battles (6 generator pairs × 2 A/B positions × 4 judges). Each request included all 15 matching slide pairs. 720 slide-level judgments written to `data/battles.csv`. Existing Fable/GPT/NexDoc battles were skipped; only the 24 Claude Opus 5 matchups were newly judged.

| Judge | OpenRouter slug | Effort | Battles | Tokens | OpenRouter cost |
| --- | --- | --- | ---: | ---: | ---: |
| Gemini 3.8 Flash | `google/gemini-3.8-flash` | high | 12 | 509k | $0.57 |
| Muse Spark 1.3 max | `meta/muse-spark-1.3` | max | 12 | 1.40M | $2.04 |
| Grok 4.6 high | `x-ai/grok-4.6` | high | 12 | 1.02M | $2.52 |
| Opus 5 max | `anthropic/claude-opus-5` | max | 12 | 1.21M | $7.46 |
| **Total** | | | **48** | **~4.1M** | **$12.59** |

The Opus 5 add-on ran in two passes: the first skipped the original 24 results and hit Muse 502s plus Gemini 20MB / in-flight 402s; a serial retry completed the 13 remaining battles.

## Combined

| Stage | Cost |
| --- | ---: |
| Generate four decks | $68.40 |
| Judge 48 pairwise battles | $12.59 |
| **Total this benchmark run** | **$80.99** |
