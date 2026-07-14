# daimon retrieval benchmark

A reproducible harness that measures daimon's recall on a public long-horizon
memory benchmark — **LongMemEval-S** (ICLR 2025). It answers one question we
otherwise cannot answer honestly: *how good is recall?*

The harness feeds benchmark session histories through daimon's **real** serializer
and answers questions using **only** what `daimon recall` surfaces. There is no
bypass of the product to flatter the number.

## Reporting policy

This is the part that matters more than any single figure:

- **We publish only numbers we measured ourselves**, with the exact harness
  version, backend, model, prompt version, seed, and dataset checksum attached.
  Every result file under `results/` carries that full config stamp.
- **Third-party figures are labeled as their publishers' claims**, never
  reproduced or presented as ours. If we quote another tool's LongMemEval number,
  it is cited as their claim under their setup — not a head-to-head we ran.
- **The number is meaningless without its backend.** Serializer quality varies by
  model (measured in this repo). A recall figure is always reported together with
  the backend/model that produced it; a figure with no backend stamp is not a
  daimon benchmark result.
- **We report the trade, not just the win.** daimon trades some raw recall for
  verifiability (provenance, trust tags, quote-checking). The honest framing
  publishes both the recall number and that trade — including the efficiency
  story (`avg_injected_tokens`), which is what the recall buys you at answer time.

## What is measured

For each question, every haystack session is serialized into a checkpoint (the
same `serialize_strict` call the SessionEnd hook makes), written to an isolated
store, and indexed. The question is then run through `recall.search`. The unit of
retrieval is the **session**: a hit means a retrieved item's source session is one
of the question's evidence sessions (`answer_session_ids`).

| Metric | Definition |
| --- | --- |
| **Recall@k** | fraction of gold (evidence) sessions in the top-k retrieved sessions, averaged over scored questions |
| **Hit@k** | 1 if any gold session is in the top-k, else 0 (a laxer success rate) |
| **MRR** | mean of 1 / rank-of-first-gold-session |
| **avg_injected_tokens** | estimated tokens of the top-k item texts a briefing injects — daimon's efficiency story, not a quality metric (≈4 chars/token estimate, not exact) |

Abstention questions (`*_abs`, no evidence session) are **excluded** from recall
scoring — there is nothing to retrieve — and counted separately, never scored as
zero.

### Known measurement choices (recorded in every result)

- **`min_messages` is lowered to 2** for the benchmark so short evidence sessions
  enter the index. The product's live default is **10**; in production, sessions
  shorter than that are skipped. This is a real limitation, surfaced here rather
  than hidden — the run config records the value used.
- **Carry is a separate axis.** By default carry is off: each session's
  checkpoint stands alone, giving a clean session→id mapping for scoring.
  `--carry` turns on cross-session carry (a real product feature: prior
  unresolved items fold forward into later checkpoints), recorded as
  `carry: "on"` in the config stamp and cached under separate keys, so carry-on
  vs carry-off Recall@k / MRR are comparable on the same sample and backend.
  Scoring stays honest under carry: a retrieved carried copy is credited to the
  session that *originated* the item (its `carried_from`), never to the later
  session hosting the copy, and each session counts at most once — no
  double-counting a gold session. The fold is applied in listed-session order
  (the exact state the product sees), so carry-on runs stay deterministic at any
  `--workers` value.
- **Determinism** holds given the seed and a pinned backend. Backends with
  non-zero temperature introduce serializer variance; the configured temperature
  rides in daimon's own config.

## Running it

From the `plugin/` directory (uv-managed environment):

```bash
# smoke tier (default 50 questions)
uv run python -m tests.bench.run --suite longmemeval-s --sample 50

# tiny end-to-end check
uv run python -m tests.bench.run --suite longmemeval-s --sample 5 --workers 8

# full 500-question suite (opt-in — expensive)
uv run python -m tests.bench.run --suite longmemeval-s --sample 0
```

The dataset (~277 MB) is **downloaded on demand** from HuggingFace
(`xiaowu0162/longmemeval-cleaned`, MIT license) and **never vendored** into the
repo. Its SHA-256 is pinned in `longmemeval_s.sha256` on first download
(trust-on-first-use) and verified on every later run; a mismatch is a hard error.

**Cost control.** Serializing a session is the one expensive step. The harness
caches each serialized checkpoint keyed by `(session content hash, backend, model,
prompt version)` under `.cache/`, so re-runs pay the LLM only for sessions never
seen under the current config. A backend/model/prompt change misses on purpose — a
cached checkpoint from a different pipeline is a different measurement.

The backend/model come from daimon's own config (`~/.daimon/env` /
`DAIMON_LLM_*`). No secret (API key, base URL) is ever written to a result file.

## Results

Baseline artifacts live under `results/`, each a self-contained JSON with its
config stamp, aggregate metrics, cost accounting, and per-question detail. A
manual GitHub Actions workflow (`.github/workflows/benchmark.yml`,
`workflow_dispatch`) reproduces a run so releases can refresh the baseline — it is
**not** run on every PR (cost).

## Dataset license

LongMemEval is released under the **MIT license**
(github.com/xiaowu0162/LongMemEval). The dataset is fetched at run time; this repo
vendors none of it.
