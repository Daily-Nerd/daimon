# Track C — Epistemic-Graph Pipeline (Runnable Harness)

**Purpose:** measure whether Daimon's belief-contradiction feature can hit usable precision — and, decisively, whether it treats *belief evolution* as growth rather than as a contradiction to nag about.

This harness tests the **D-005 architecture**, not raw NLI. Raw NLI over dialogue is already known to score **23.94% precision** ([DECODE](https://arxiv.org/abs/2012.13391)); re-measuring it would only re-confirm a floor. So Track C runs the pipeline we'd actually ship and keeps raw flagging as a **baseline arm** to prove the lift.

Rationale: [`../../findings/04-epistemic-graph.md`](../../findings/04-epistemic-graph.md). Gate: [`../../../docs/VALIDATION.md`](../../../docs/VALIDATION.md) Track C.

---

## The pipeline (D-005)

```
conversation ─[prompts/01-extract]─▶ clean claims     (Claimify-style disambiguation gate)
clean claims ─[temporal KG]─▶ validity intervals       (a later different-stance claim SUPERSEDES the earlier)
intervals ─[flag rule]─▶ contradiction ONLY on OVERLAP  (sequential change = evolution, NOT a flag)
```

The insight: converting "is this a contradiction?" (semantically hard, ~24% precision) into "do these validity intervals overlap?" (mostly mechanical). Belief *evolution* = supersession-with-timestamp; *contradiction* = overlapping intervals.

## Try it now (no data needed)

```
uv run pipeline/run.py fixtures/evolution-pairs.json
```

Expected: the **pipeline** correctly supersedes the evolution pair (EMR 0%) while the **baseline** flags it as a contradiction (EMR 100%) — a +100pt lift. Verdict PASS. That single run demonstrates the whole thesis.

## Automated path (LiteLLM)

Drive Stage-1 extraction against your LiteLLM gateway instead of pasting. See [`../README.md`](../README.md) for setup, then:

```bash
uv run extract.py --session corpus/S1.txt --timestamp 1 --out runs/S1.claims.json
```

Merging, `is_belief` audit, and gold/evolution labeling stay manual.

## With real data

1. **Corpus.** Reuse 2–3 sessions from Track A that share a topic where your position changed over time. Drop them in `corpus/` (git-ignored). The corpus **must contain ≥3 genuine belief evolutions** — if your real sessions lack them, add synthetic evolution pairs (see `fixtures/`). Include hedges/sarcasm as extraction noise.
2. **Extract (Stage 1).** Run [`prompts/01-extract.md`](./prompts/01-extract.md) per session. Merge into `runs/<pair>.run.json`, add timestamps + ids, audit `is_belief`.
3. **Label.** Mark `gold_contradictions` and `evolution_pairs` per [`scoring/rubric.md`](./scoring/rubric.md).
4. **Run.** `uv run pipeline/run.py runs/<pair>.run.json` → BEP, FCR, EMR, lift, verdict.

## Metrics + bars (from VALIDATION.md, revised per D-005)

- **BEP** — Belief Extraction Precision (did the gate keep only real beliefs?)
- **FCR** — False Contradiction Rate (flags not in the gold set)
- **EMR** — Evolution Misclassification Rate (evolution pairs wrongly flagged) — *the decisive metric*
- **Lift** — pipeline FCR/EMR vs the raw baseline arm

| Verdict | Condition (pipeline arm) |
|---|---|
| **Pass** | BEP ≥ 75% AND FCR ≤ 20% AND EMR ≤ 20% AND clear lift over baseline |
| **Pivot** | FCR 20–40% or EMR 20–40% — viable with a confidence threshold + "did your view change?" confirm UX |
| **Kill** | FCR ≥ 40% OR EMR ≥ 40%, OR no lift (the temporal-KG complexity isn't earning its keep) |

## Sequencing note

Run Track A first. A Track A kill (the CRP confabulates) makes Track C moot — there's no reliable memory to build a belief graph on. This harness is ready when Track A gives a green/pivot signal.

## Layout

```
track-c/
  README.md                       ← you are here
  prompts/01-extract.md           ← Stage 1: Claimify-style extraction gate
  pipeline/
    run.py                        ← temporal-KG engine + baseline arm + scorer (uv run; stdlib)
    belief.schema.json            ← run-file contract
  scoring/
    rubric.md                     ← how to label is_belief / gold / evolution
    run.template.json             ← fillable run file
  fixtures/
    evolution-pairs.json          ← synthetic self-test (committed; not personal data)
  corpus/  (git-ignored)          ← YOUR real sessions
  runs/    (git-ignored)          ← per-run files
```

Only the harness + synthetic fixture are committed. Your conversations stay local.
