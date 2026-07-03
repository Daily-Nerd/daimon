# State-Tracking Benchmark Report (M0.3)

Multi-turn conversations with overrides. Deterministic grading against
authored ground truth. `csl` and `summary` consolidate via the same
model + symmetric prompts at the same budget — so their gap isolates
structured-CSL vs prose. `rag-append` is naive retrieval; `raw` is the
uncapped ceiling. `graphiti` is the temporal-KG adoption arm (Zep engine,
bi-temporal edge invalidation) — present only when run with --with-graphiti.

| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok | Compression |
|--------|-------------|--------------|-----------|-------------|----------|-------------|
| raw | 1.000 | 1.000 | 0.000 | 1.000 | 16094 | 1.00x |
| csl | 0.868 | 0.909 | 0.045 | 0.895 | 542 | 29.70x |
| summary | 0.763 | 0.818 | 0.045 | 0.789 | 282 | 57.11x |
| rag-append | 0.763 | 0.727 | 0.182 | 0.763 | 300 | 53.65x |

**Decisive (override accuracy, equal budget): CSL beats prose summary** (CSL 0.909 vs summary 0.818, Δ=+0.091).

Override accuracy is the discriminating metric: it measures whether the
memory reflects the CURRENT value after a change, not a stale one.