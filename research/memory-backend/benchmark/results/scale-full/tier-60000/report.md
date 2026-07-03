# State-Tracking Benchmark Report (M0.3)

Multi-turn conversations with overrides. Deterministic grading against
authored ground truth. `csl` and `summary` consolidate via the same
model + symmetric prompts at the same budget — so their gap isolates
structured-CSL vs prose. `rag-append` is naive retrieval; `raw` is the
uncapped ceiling. `graphiti` is the temporal-KG adoption arm (Zep engine,
bi-temporal edge invalidation) — present only when run with --with-graphiti.

| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok | Compression |
|--------|-------------|--------------|-----------|-------------|----------|-------------|
| raw | 0.737 | 0.545 | 0.409 | 0.947 | 66613 | 1.00x |
| csl | 0.526 | 0.545 | 0.000 | 0.526 | 622 | 107.03x |
| summary | 0.368 | 0.364 | 0.000 | 0.368 | 452 | 147.24x |
| rag-append | 0.763 | 0.773 | 0.136 | 0.763 | 300 | 222.04x |

**Decisive (override accuracy, equal budget): CSL beats prose summary** (CSL 0.545 vs summary 0.364, Δ=+0.182).

Override accuracy is the discriminating metric: it measures whether the
memory reflects the CURRENT value after a change, not a stale one.