# Track A — CRP Confabulation Experiment (Runnable Harness)

**Purpose:** measure whether the Cognitive Resumption Protocol reconstructs prior session state *faithfully*, or *confabulates* confident-but-false state. This is Daimon's load-bearing bet — a kill here (False-Memory Rate ≥ 20%) overrides the whole project.

Full rationale: [`../../findings/03-crp-reconstruction.md`](../../findings/03-crp-reconstruction.md). Gate definition: [`../../../docs/VALIDATION.md`](../../../docs/VALIDATION.md) Track A.

This harness turns the 6-step protocol into "plug in 5 real sessions → get a number."

---

## What you provide

5 of your **own real past AI sessions** (Claude/ChatGPT/Cursor exports). Only you have these. Each must have ≥20 turns, ≥2 unresolved open questions, ≥1 explicit decision, ≥1 emotional/frustration signal. Drop them as `sessions/S1.txt … S5.txt` (create the `sessions/` dir; it is git-ignored — your conversations never get committed).

## The loop (per session)

```
raw session ──[human]──▶ ground-truth.json        (the answer key — YOU write this)
raw session ──[LLM: prompts/01-serialize]──▶ checkpoint.json   (AI-generated cognitive state)
checkpoint.json ──[LLM: prompts/02-reconstruct]──▶ reconstruction.md  (the "resumed self")
ground-truth.json + reconstruction.md ──[human label]──▶ session-SN.score.json
all score files ──[scoring/score.py]──▶ RR / FMR / OR + verdict
```

Step order matters: **write the ground truth BEFORE you read the reconstruction**, or you will unconsciously grade on a curve.

## Automated path (LiteLLM)

Instead of pasting prompts by hand, drive steps 2–3 against your LiteLLM gateway. See [`../README.md`](../README.md) for setup (`kubectl port-forward`, env vars), then:

```bash
uv run runner.py --all          # serialize -> reconstruct for every sessions/*.txt
```

Steps 1 (ground truth) and 4 (scoring) stay manual — that's what keeps it blind.

## Step-by-step (manual or automated)

1. **Ground truth (human).** For each session, copy `schema/ground-truth.template.json` → `runs/SN/ground-truth.json` and fill it from the raw transcript. List every unresolved open question, every explicit decision, every stated belief/uncertainty, the active topic. This is the answer key.
2. **Serialize (AI).** Run [`prompts/01-serialize.md`](./prompts/01-serialize.md) on the raw session with your model. Save output → `runs/SN/checkpoint.json`. Conforms to [`schema/cognitive-state.schema.json`](./schema/cognitive-state.schema.json).
3. **Reconstruct (AI).** Run [`prompts/02-reconstruct.md`](./prompts/02-reconstruct.md) on the **checkpoint only** (not the raw session). Save → `runs/SN/reconstruction.md`.
4. **Score (human).** Copy `scoring/session.template.json` → `runs/SN/session-SN.score.json`. For each ground-truth item mark `recalled: true/false`. For each claim the reconstruction surfaced, mark `grounded: true/false` (ungrounded = a false memory). Also record each item's `trust` class (verbatim/inferred) — this tests D-006.
5. **Compute.** `uv run scoring/score.py runs/*/session-*.score.json` → per-session + aggregate RR/FMR/OR, the D-006 breakdown, and the Build/Pivot/Kill verdict.
6. **Cycle-degradation (only if step 5 passes).** Take one session, feed its reconstruction back in as if it were a new raw session, re-serialize, re-reconstruct, re-score. Does FMR roughly double? See `scoring/score.py --cycle`.

## The bars (from VALIDATION.md)

| Verdict | Condition |
|---|---|
| **Pass** | RR ≥ 70% AND FMR ≤ 10%, across all 5 |
| **Pivot** | FMR 10–20% — viable with confidence-scoring + confirm UX |
| **Kill** | FMR ≥ 20% on any 2 of 5, OR mean RR < 50% |

`score.py` prints the verdict automatically.

## What this also tests (free signal)

- **D-006 (extractive pinning):** the score sheet tags each item `verbatim` vs `inferred`. `score.py` reports FMR/RR split by trust class. Hypothesis: verbatim-pinned items survive reconstruction materially better. If true, it confirms pinning load-bearing facts is the right defense.
- **Lost-in-the-middle:** if you vary checkpoint-item ordering between runs, you can observe the position effect directly.

## Layout

```
track-a/
  README.md                          ← you are here
  schema/
    cognitive-state.schema.json       ← the checkpoint contract (RFC §5.1 + trust classes)
    ground-truth.template.json        ← fillable human answer key
  prompts/
    01-serialize.md                   ← raw session → checkpoint
    02-reconstruct.md                 ← checkpoint → resumed-self narrative
  scoring/
    rubric.md                         ← how to label items (read before scoring)
    session.template.json             ← per-session scoring input
    score.py                          ← computes RR/FMR/OR + verdict (stdlib only; uv run)
  sessions/   (git-ignored)           ← YOUR raw transcripts
  runs/       (git-ignored)           ← per-session artifacts + scores
```

Nothing in `sessions/` or `runs/` is committed — see `.gitignore`. Your conversations stay local. Only the harness (prompts, schema, scorer) lives in git.
