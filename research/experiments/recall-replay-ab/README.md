# Recall Replay A/B — an instrument for testing recall-scoring hypotheses

A deterministic offline rig for answering one question cheaply and honestly:
**would this proposed change to recall actually inject better rows than what
we ship today?**

Arm A is shipped `recall.suggest()`, untouched. Arm B is a **variant** — the
hypothesis under test, supplied by the experimenter as a pluggable callable.
Both arms replay the same real historical prompts, back-to-back, against the
same time-filtered snapshot of the checkpoint store, through a faithful
replica of `cli._cmd_recall_inject`'s downstream post-filter (briefing-session
exclusion, per-session seen-file cooldown, #451 content-key dedup, #452 age
gate). What comes out is the user-felt injection surface each arm would have
produced, prompt by prompt, plus side-blind judging units for the rows where
the arms disagree.

The harness holds **no opinion about what recall should do**. It is the
measuring device, not the bet.

## Self-check (synthetic fixtures, zero real data)

    uv run --project ../../../plugin python replay_ab.py --verify

`verify.py` builds a synthetic daimon home through the real write path and
asserts the rig's executable claims: determinism (two runs byte-identical),
the identity variant reproducing arm A exactly, the snapshot time filter, the
per-session cooldown, machine-prompt skipping, blind-file hygiene, and — via
a fixture-only silencing variant defined inside `verify.py` — that a B arm
which drops rows emits correct diff and judging artifacts. Run it before and
after touching the harness.

## Real run (maintainer's machine only)

    uv run --project ../../../plugin python replay_ab.py \
        --dataset ~/.daimon/logs/replay-prompts.jsonl \
        --daimon-home ~/.daimon \
        --variant variants:none \
        --out results/run-01

The source store is READ-ONLY (files are copied into a temp snapshot dir);
the recall db, team dir, seen dir, log dir and env file are all pinned to the
temp dir for the whole run.

## Inputs

- `--dataset PATH` — JSONL, one row per historical prompt:
  `{"prompt": "...", "ts": <epoch or ISO-8601>, "session": "<session id>",
  "project": "<project dir the session ran in>"}`. `project` (also accepted:
  `project_dir` / `cwd`) is optional per row when `--project` supplies a
  default. NEVER committed.
- `--daimon-home PATH` — flat checkpoint store to snapshot from
  (default `~/.daimon`).
- `--variant SPEC` — arm B. A builtin name (`none`) or `module:function` /
  `path/to/file.py:function`. Default `none`.
- `--sweep "a,b,c"` — parameter values handed to the variant, one arm B per
  value (arms are labelled `B@<value>`). Omit for a single unparameterised
  arm `B`. Use it when the hypothesis has a knob to tune.
- `--out DIR` — results directory. PRIVATE except `summary.json`.
- `--seed` (default 470), `--holdout-min` (default 20).

## Adding a variant

A variant is one callable:

```python
def my_hypothesis(ctx, suggest) -> list[dict]:
    ...
```

`suggest()` runs the shipped call for this prompt and returns its match list;
keyword arguments override that call's inputs. So the hypothesis can be

- **output-side** — `return [m for m in suggest() if keep(m)]` (a gate, a
  re-rank, a truncation over what recall already selected)
- **input-side** — `return suggest(prompt=rewrite(ctx["prompt"]))` (a
  different query, a wider fetch, a different exclusion set)
- or both.

`ctx` carries `prompt`, `terms`, `project`, `session`, `ts`, `param` (this
arm's sweep value, a string, or `None`) and `db_path` (the snapshot recall db,
for a variant that needs its own lookups).

Three rules keep the comparison interpretable: rows must come from
`suggest()` (never fabricate — judging units are built from arm output, and a
synthesised row has no provenance to judge); no side effects on the db, the
seen state, the env, or any `daimon_briefing` module constant; deterministic
for a given `(ctx, param)`, or the determinism check fails.

Register it in `variants.py` next to `none`, or keep it out of the repo and
pass `--variant ~/scratch/my_idea.py:my_hypothesis`.

Start every session by confirming `--variant none` reports zero diff prompts.
Under the identity variant B *is* A, so any disagreement there is a harness
bug, not a finding.

## Outputs (in `--out`)

- `diffs.jsonl` — one row per prompt where arms disagree at any sweep value:
  prompt text, ts, session, per-arm a_only/b_only/common injections.
  PRIVATE (carries prompt text).
- `judging.jsonl` / `judging-holdout.jsonl` — side-blind judging units
  (stable id, prompt, injection line; deterministic shuffled order, NO arm
  labels). The judge reads these only.
- `key.jsonl` — id -> arm mapping per arm + holdout flag. The judge never
  opens this file.
- `summary.json` — per-arm aggregate counts + corpus metadata. No prompt
  text; the only artifact safe to share.
- `run-meta.json` — runtime/wall-clock (kept out of `summary.json` so the
  determinism self-check can byte-compare every analytical artifact).

## The method: pre-register before you run

This is the part worth reusing. **Write the criteria down, in the issue,
before the first real-corpus run** — otherwise the parameter value and the
success bar get chosen after seeing the numbers, and the result means
nothing. Template:

1. **Comparison** — B-replay vs A-replay ONLY, identical harness both arms,
   no external baselines. The harness is the constant; the variant is the
   only thing that moves.
2. **Ship criteria** — a conjunction, each clause falsifiable. The #470 set,
   reusable as-is for any "inject fewer, better rows" hypothesis:
   - *Per-slot precision*: B's judged per-slot precision strictly above A's
     on the same corpus.
   - *Zero loss* (guard): zero judged-relevant A injections lost — i.e. no
     `a_only` injection judged relevant. Dispute rule: a disputed judgment
     gets ONE re-judge with fresh context; still disputed = counts as
     relevant.
   - *Volume* (guard): B's injection volume <= A's.
3. **Parameter rule** — if the variant has a knob, say in advance how its
   shipped value is chosen. #470: the largest sweep value satisfying both
   guards; criterion 1 is reported at that value.
4. **Stratification** — declare the strata before the run (#470: prompt
   salient-term count, 2-3 vs 4+). Post-hoc strata are how a refuted result
   gets rescued.
5. **Side-blind judging** — judge `judging.jsonl` only; `key.jsonl` stays
   closed until every judgment is final. The judge must not be able to infer
   the arm from the file it reads (`verify.py` asserts this).
6. **Holdout** — if diff volume allows (>= `--holdout-min` diff prompts), a
   seeded random half of diff prompts is held out; the holdout split is what
   the final report is decided on. The other half is where you are allowed to
   look while forming intuitions.
7. **A pre-registered refutation is a result.** Record it, delete the code,
   keep the instrument.

## Worked example: #470, the IDF mass gate — REFUTED

<https://github.com/Daily-Nerd/daimon/issues/470>

**Hypothesis.** A recall match on two *common* words is vocabulary
coincidence, not prior work. So gate sessions whose matched terms carry too
little summed IDF (per-project document frequency, `ln(n_items/df)`), and
recall should get quieter and more precise.

**What happened.** Built, pre-registered, replayed over the maintainer's real
prompt corpus, 237 blind-judged injection pairs. On the pre-registered
holdout split, arm B's per-slot precision was **worse than arm A at every
threshold** — 17.3% / 16.9% / 15.9% / 12.3% at thresholds 6/8/10/12 against
A's 18.8% — and the zero-loss guard failed at every threshold. The diagnostic
is the interesting part: the gate dropped judged-relevant items at a *higher*
rate than the items it kept (threshold 12: 26.0% relevant among dropped vs
17.0% among kept). Summed term rarity is slightly **anti-correlated** with
relevance in this corpus. The shared vocabulary that actually signals prior
work on the same thing is the common working vocabulary; the rare tokens are
disproportionately incidental — identifier fragments, one-off filenames,
pasted-log tokens.

An earlier iteration failed differently and is worth remembering: gating
*during* candidate selection freed slots that got backfilled, and the gate's
own exempt class (pinned rows, open questions) was first in that queue, so
the noise gate made recall **louder** — 344 injections in arm A vs 374 in arm
B, with 86 of the 123 B-only injections being open questions.

**Outcome.** The gate did not ship and its machinery was removed. Both dead
ends are recorded in `.scars/candidates/idf-rarity-weighting-refuted.md`.
This harness is what the arc produced that was worth keeping.

## Expected runtime (real corpus)

~2200 prompts over ~45 checkpoint files: prompts are replayed in global ts
order and grouped by snapshot equivalence class (the qualifying-file set), so
the index rebuilds ~45 times (~1s each), plus (1 + number of arms) suggest
calls per prompt (~5-15 ms each). With a 4-value sweep expect roughly **3-6
minutes** end to end.
