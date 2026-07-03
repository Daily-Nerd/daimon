# State-Tracking Benchmark Report (M0.3)

Multi-turn conversations with overrides. Deterministic grading against
authored ground truth. `csl` and `summary` consolidate via the same
model + symmetric prompts at the same budget — so their gap isolates
structured-CSL vs prose. `rag-append` is naive retrieval; `raw` is the
uncapped ceiling. `graphiti` is the temporal-KG adoption arm (Zep engine,
bi-temporal edge invalidation) — present only when run with --with-graphiti.

| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok |
|--------|-------------|--------------|-----------|-------------|----------|
| raw | 0.974 | 0.955 | 0.045 | 1.000 | 184 |
| csl | 0.947 | 0.909 | 0.000 | 0.947 | 108 |
| summary | 0.895 | 0.864 | 0.045 | 0.921 | 56 |
| rag-append | 0.868 | 0.818 | 0.136 | 0.895 | 300 |

**Decisive (override accuracy, equal budget): CSL beats prose summary** (CSL 0.909 vs summary 0.864, Δ=+0.045).

Override accuracy is the discriminating metric: it measures whether the
memory reflects the CURRENT value after a change, not a stale one.