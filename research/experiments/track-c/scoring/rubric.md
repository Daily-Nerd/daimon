# Track C — Labeling Rubric

Three things you label per run. Be strict — the whole point is to find out if the pipeline is trustworthy, not to flatter it.

## 1. `is_belief` (per extracted claim) → drives BEP

For every claim the extraction gate produced, decide: is this a **genuine stable belief** the user holds?

- **`true`** — a real position ("microservices are wrong at our scale", "postgres over mysql").
- **`false`** — the gate leaked noise: a hedge ("maybe k8s someday"), a hypothetical, sarcasm, a joke, a question mis-read as a claim. These count against **Belief Extraction Precision**.

If too many are `false`, the disambiguation gate isn't working — that alone can fail the track.

## 2. `evolution_pairs` → drives EMR (the decisive metric)

Pairs of claims on the **same subject** where the user **legitimately changed position over time**. March: "leaning microservices." June: "monolith was right." That is growth, not contradiction. The pipeline must SUPERSEDE these (close the old interval), NOT flag them.

- List them as `[["c1","c2"], ...]`.
- Any of these the pipeline flags = an **Evolution Misclassification**. EMR ≥ 40% is a kill — it means the "intellectual mirror" is a nag that calls your growth a lie.

## 3. `gold_contradictions` → the truth set for FCR / recall

Pairs that **genuinely conflict at the same time** — beliefs that cannot both be held simultaneously and were not a sequential change of mind. Two ways this shows up:

- **Concurrent assertion:** the user says X and not-X about the same subject in the same breath / same session without retracting (use `validity: point` with equal timestamps).
- **Overlapping explicit claims:** "I've *always* thought X" vs a later "I *never* thought X" — both claim the same time range (use `validity: explicit` with overlapping ranges).

- List as `[["c5","c6"], ...]`.
- A pipeline flag **not** in this set = a **false contradiction** (FCR).
- A gold pair the pipeline **misses** = a recall miss (reported, but FCR/EMR are what gate the verdict — a trust feature must fail safe).

## Getting validity right (this is what makes or breaks the pipeline)

| The user said… | validity to assign |
|---|---|
| a position they hold going forward | `{"type":"ongoing"}` |
| an in-the-moment pick, two of them at once | `{"type":"point"}`, equal timestamps |
| "I've always / I never / until last week" | `{"type":"explicit","start":...,"end":...}` |

If two ongoing claims on the same subject are at **different** timestamps → the pipeline supersedes (evolution). If they're at the **same** timestamp, or have **overlapping explicit** ranges → it flags (contradiction). Set timestamps and validity to reflect what actually happened, then trust the engine.

## Golden rule

When unsure whether a pair is *evolution* or *contradiction*: ask "did they change their mind over time, or hold both at once?" Over-time = evolution (don't flag). At-once = contradiction (flag). The temporal distinction IS the feature.
