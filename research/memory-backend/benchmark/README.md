# Context-as-Program Benchmark

## Quick Start

```bash
# Generate dataset (already done)
python benchmark/generate_datasets.py

# Run full benchmark
python benchmark/run_benchmark.py \
    --dataset benchmark/data/conversations.jsonl \
    --output benchmark/results/ \
    --extractor-model <your-gateway-model>   # banked runs used a gpt-5-class route

# Run a small pilot (5 conversations) for quick validation
python benchmark/run_benchmark.py \
    --dataset benchmark/data/conversations.jsonl \
    --output benchmark/results/pilot/ \
    --limit 5
```

## Modules

| Module | Purpose |
|--------|---------|
| `extractor.py` | LLM-based CSL extraction with retry logic and cost tracking |
| `datasets.py` | Synthetic conversation generator (50 conversations, 6 domains) |
| `evaluate.py` | Compression metrics, QA accuracy, grading, and report generation |
| `run_benchmark.py` | One-command benchmark orchestration |

## Dataset

- **50 conversations** across 6 domains (software, product, science, medical, legal, creative)
- **Token distribution:** 10 short (~2–5K), 25 medium (~5–15K), 15 long (~15–50K)
- **Average:** ~18K tokens
- **File:** `benchmark/data/conversations.jsonl`

## Evaluation Metrics

1. **Compression ratio:** raw tokens / CSL tokens
2. **QA accuracy:** CSL answers vs. raw context answers (accuracy, completeness, tone)
3. **Statement coverage:** Which CSL primitives were extracted

## Model Access

Benchmark ran against an OpenAI-compatible LiteLLM gateway. Banked results used:
- Primary extraction: a gpt-5-class route (appears as `gpt-5-via-cliproxy` in result artifacts)
- Fallback answering: `kimi-k2.6`
- Fast tests: a haiku-class route (appears as `claude-haiku-4-5-via-meridian` in result artifacts)
