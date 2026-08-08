# Field-tested backends and models

Which model/backend combinations actually work with daimon's serializer — measured
in real use, not assumed. One row per (model, backend path) combination someone has
run in the field.

Two rules keep this a source of truth instead of a vibes page:

1. **Measured, not self-reported.** The quality column is the *verbatim downgrade
   rate*: the share of fresh verbatim claims whose quote failed verification against
   the transcript and got downgraded to inferred. The verifier computes it; the
   model's own opinion of its output is never accepted. See the recipe below.
2. **Dated and versioned, or it doesn't count.** Every row carries the daimon
   version and test date. Old rows age visibly instead of lying forever.

Rows are contributed by PR — daimon has no telemetry by design, so nothing here is
collected automatically. Add your combination with the recipe below.

## Matrix

| Model | Backend path | daimon | Downgrade rate (sample) | Date | Notes |
|-------|--------------|--------|-------------------------|------|-------|
| MIXED — claude-haiku-4-5 (litellm proxy) + claude-cli sessions, per-session attribution lost | see notes | 0.13.0 | 29% of 51 fresh verbatim claims, 3 sessions — per-session range 6%–77% | 2026-07-10 | maintainer dev box. The backend changed between these sessions and checkpoints don't record which one serialized them, so this row CANNOT be split — kept as a worked example of why unattributed samples are near-useless and why the serializer needs a backend/model stamp. Replace with attributed rows once stamping ships. |
| MIXED — maintainer lifetime aggregate, backends varied over five months (mostly claude-cli) | see notes | 0.13.0–0.27.0 | 12.2% of 3703 fresh verbatim claims, 160 sessions | 2026-08-07 | maintainer dev box, dogfooding this repo. Not one (model, backend) pair — published as the lifetime baseline, attributed by month and checkpoint format instead: 2026-07 13.7% (n=2217) → 2026-08 9.8% (n=1476); by format D-012 21.2%, D-013 16.5%, D-014 10.1%, D-016 16.1%, D-017 9.9%, D-018 10.6% (n=1274). Deduplicated by session id — see the aggregate note below. |
| _your model_ | _anthropic / openai-compatible / claude-cli / command_ | | | | |

**Attribution rule (learned filling the first row):** a row is only valid if every
sampled checkpoint is known to come from that exact (model, backend) pair. Until
checkpoints carry a serializer stamp, that means "the backend did not change during
the sample window" — verify before counting, or your row blends combinations.
0.15.0+ checkpoints carry that stamp directly: `llm_backend` (and `llm_model`,
when config actually knows one) is recorded at serialize time, so attribution
no longer has to be reconstructed from memory of when the backend changed.

Reading the numbers: a downgrade is the **verifier catching a misquote**, not data
loss — the item survives as `[~ inferred]` with the failed-check stamp. Lower is
better; the interesting signals are the level *and* the variance. Single-session
rates on small claim counts (< 20) are noisy — say so in the row.

## The lifetime aggregate, and how to reproduce it

The 12.2% row is every fresh verbatim claim this project's own store had checked
as of 2026-08-07 — 3703 claims across 160 sessions on one maintainer machine —
counted with the same `quote_verified` stamps the recipe below reads. Two things
keep it honest:

- **Deduplicate by session id.** Checkpoints rotate (`latest.json` →
  `prev-N.json`), so a naive glob counts the same session more than once. Here
  the rate happened to be identical before and after dedup, but that is luck,
  not a property — dedup anyway.
- **It is a baseline, not a benchmark.** Single machine, a single project
  (daimon developing itself), mixed backends across five months of versions.
  The portable signal is the trend, not the level: per-format rates fall from
  D-012's 21.2% to roughly 10% on D-017/D-018 as serializer-side verification
  hardened.

## Row-filling recipe

The downgrade rate is stamped on every fresh checkpoint: `verify_quotes` marks each
fresh verbatim claim `quote_verified: true` (hit) or downgrades it to
`trust: "inferred"` + `quote_verified: false` (miss). Count both on your newest
checkpoints — note the filter is on the *stamp*, not on `trust` (downgraded items
are no longer `verbatim`, which is exactly why filtering by trust would hide them).
Group by the `(llm_backend, llm_model)` stamp pair (0.15.0+) so a mixed batch of
checkpoints can never blend combinations by accident — pre-0.15.0 checkpoints
carry no stamp at all and fall into an `(unstamped)` bucket, which is a signal to
verify attribution by hand rather than a combination you can cite in a row:

```python
import json, sys
from collections import defaultdict

def items(c):
    w, e = c.get("working_context", {}), c.get("epistemic_snapshot", {})
    for k in ("open_questions", "recent_decisions"):
        yield from (w.get(k) or [])
    if isinstance(w.get("active_topic"), dict):
        yield w["active_topic"]
    for k in ("strong_beliefs", "uncertainties", "contradictions_flagged"):
        yield from (e.get(k) or [])

groups = defaultdict(lambda: [0, 0])  # (backend, model) -> [claims, downgraded]
for path in sys.argv[1:]:
    cp = json.load(open(path))
    key = (cp.get("llm_backend") or "(unstamped)", cp.get("llm_model") or "(no model)")
    fresh = [i for i in items(cp) if isinstance(i, dict)
             and not i.get("carried_from") and i.get("quote_verified") is not None]
    bad = sum(1 for i in fresh if i["quote_verified"] is False)
    g = groups[key]
    g[0] += len(fresh)
    g[1] += bad

for (backend, model), (claims, bad) in sorted(groups.items()):
    print(f"{backend}/{model}: {claims} claims, {bad} downgraded")
```

Run it over `~/.daimon/checkpoints/<project>/latest.json` and the `prev-*.json`
siblings, sum across several sessions (single sessions are too noisy), and open a
PR with the row — one row per printed group, never a hand-merged total across
groups. Only checkpoints from 0.13.0+ carry trustworthy per-checkpoint quote
stamps (`quote_verified: false` became a fresh-only signal then; older carried
items can hold stale stamps).

Serialize *reliability* (does the run complete at all) is a different axis from
quote fidelity — if your combination fails outright, that's an issue report with
`~/.daimon/logs/serialize.log` attached, not a matrix row.
