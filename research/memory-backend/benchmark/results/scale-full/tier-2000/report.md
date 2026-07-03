# State-Tracking Benchmark Report (M0.3)

Multi-turn conversations with overrides. Deterministic grading against
authored ground truth. `csl` and `summary` consolidate via the same
model + symmetric prompts at the same budget — so their gap isolates
structured-CSL vs prose. `rag-append` is naive retrieval; `raw` is the
uncapped ceiling. `graphiti` is the temporal-KG adoption arm (Zep engine,
bi-temporal edge invalidation) — present only when run with --with-graphiti.

| Method | Overall acc | Override acc | Staleness | Gold recall | ~ctx tok | Compression |
|--------|-------------|--------------|-----------|-------------|----------|-------------|
| raw | 1.000 | 1.000 | 0.000 | 1.000 | 2965 | 1.00x |
| csl | 0.921 | 0.909 | 0.091 | 0.921 | 287 | 10.34x |
| summary | 0.868 | 0.955 | 0.000 | 0.868 | 215 | 13.80x |
| rag-append | 0.816 | 0.773 | 0.182 | 0.842 | 300 | 9.88x |

**Decisive (override accuracy, equal budget): Prose summary beats CSL** (CSL 0.909 vs summary 0.955, Δ=-0.045).

Override accuracy is the discriminating metric: it measures whether the
memory reflects the CURRENT value after a change, not a stale one.