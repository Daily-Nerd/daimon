# Daimon Research Logbook

This is the working memory of the Daimon project during its pre-build phase.

## Why this exists (and why it's shaped like this)

Daimon's core thesis is **persistent memory across sessions**. We don't have Daimon yet — so we are building its memory *by hand, out of files*. This is deliberate dogfooding: if maintaining this logbook by hand is painful, that pain is a specification for what Daimon must automate. If a structure feels natural here, it is a candidate for Daimon's real schema.

Accordingly, this logbook's structure **mirrors the four memory layers** in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md):

| Daimon memory layer | File here | Role |
|---|---|---|
| Episodic (raw, chronological) | [`LOGBOOK.md`](./LOGBOOK.md) | Append-only dated journal of what happened |
| Semantic (concepts, beliefs) | [`findings/`](./findings/) + [`GLOSSARY.md`](./GLOSSARY.md) | Distilled, durable knowledge per topic |
| Decisions | [`DECISIONS.md`](./DECISIONS.md) | Load-bearing choices + rationale + reversal cost |
| Open loops | [`OPEN-QUESTIONS.md`](./OPEN-QUESTIONS.md) | What we still must investigate, status-tracked |

## The update protocol

1. **Anything happens** (research, decision, finding) → append a dated entry to `LOGBOOK.md`. Episodic first, always.
2. **A durable fact emerges** → write/extend the relevant `findings/*.md`. Distill, don't dump.
3. **A load-bearing choice is made** → record it in `DECISIONS.md` with reversal cost.
4. **A question is raised or answered** → update `OPEN-QUESTIONS.md` status.
5. **A new term of art appears** → add it to `GLOSSARY.md`.

Episodic is the source of truth for *when*. Semantic is the source of truth for *what*. Decisions is the source of truth for *why*. Open-questions is the source of truth for *what's missing*.

## Index of findings

- [`00-system-framing.md`](./findings/00-system-framing.md) — what class of system Daimon actually is
- [`01-memory-retrieval.md`](./findings/01-memory-retrieval.md) — storage + recall (the SOLVED substrate)
- [`02-crp-serialization.md`](./findings/02-crp-serialization.md) — the CRP write path (checkpointing)
- [`03-crp-reconstruction.md`](./findings/03-crp-reconstruction.md) — the CRP read path (resumption) — THE BET
- [`04-epistemic-graph.md`](./findings/04-epistemic-graph.md) — belief extraction + contradiction — THE DIFFERENTIATOR/GAMBLE
- [`05-initiative.md`](./findings/05-initiative.md) — interruption decision logic
- [`06-evidence-base.md`](./findings/06-evidence-base.md) — what the literature proves / doesn't
- [`07-hermes-honcho-delta.md`](./findings/07-hermes-honcho-delta.md) — Track B: the differentiator is already shipped (Honcho + Graphiti); what survives

## Experiments

Runnable validation harnesses live in [`experiments/`](./experiments/):
- [`experiments/track-a/`](./experiments/track-a/) — CRP confabulation experiment. Plug in 5 real sessions → get an RR/FMR/OR number + Build/Pivot/Kill verdict. Also tests D-006 (extractive pinning) as a side effect.
- [`experiments/track-c/`](./experiments/track-c/) — epistemic-graph pipeline (D-005). Temporal-KG validity-interval engine + raw-NLI baseline arm. `uv run pipeline/run.py fixtures/evolution-pairs.json` for a zero-data self-test. Reports BEP/FCR/EMR + lift + verdict.

## Status legend (used throughout)

- 🟢 **Investigated** — concluded, evidence in hand
- 🟡 **In progress** — actively being researched
- 🔴 **Open** — raised, not yet investigated
- ⚫ **Blocked** — waiting on an external input (e.g. running Hermes)
