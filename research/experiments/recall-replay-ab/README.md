# #470 Stage-1 Replay A/B — IDF Mass Gate

Deterministic offline A/B: replay real historical prompts through
`recall.suggest()` + a replica of cli's recall-inject post-filter, gate OFF
(arm A) vs ON (arm B), per prompt back-to-back on the same snapshot db.
Design rationale: https://github.com/Daily-Nerd/daimon/issues/470

## Self-check (synthetic fixtures, zero real data)

    uv run --project ../../../plugin python replay_ab.py --verify

## Real run (maintainer's machine only)

    uv run --project ../../../plugin python replay_ab.py \
        --dataset ~/.daimon/logs/replay-prompts.jsonl \
        --daimon-home ~/.daimon \
        --sweep "6,8,10,12" \
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
- `--sweep "6,8,10,12"` — `_IDF_MIN_MASS` values for arm B.
- `--out DIR` — results directory. PRIVATE except `summary.json`.
- `--seed` (default 470), `--holdout-min` (default 20).

## Outputs (in `--out`)

- `diffs.jsonl` — one row per prompt where arms disagree at any sweep value:
  prompt text, ts, session, per-threshold a_only/b_only/common injections.
  PRIVATE (carries prompt text).
- `judging.jsonl` / `judging-holdout.jsonl` — side-blind judging units
  (stable id, prompt, injection line; deterministic shuffled order, NO arm
  labels). The judge reads these only.
- `key.jsonl` — id -> arm mapping per threshold + holdout flag. The judge
  never opens this file.
- `summary.json` — per-threshold aggregate counts + corpus metadata. No
  prompt text; the only artifact safe to share.
- `run-meta.json` — runtime/wall-clock (kept out of `summary.json` so the
  determinism self-check can byte-compare every analytical artifact).

## Pre-registered protocol (fixed before any real-corpus run)

Comparison is B-replay vs A-replay ONLY, identical harness both arms, no
external baselines. Ship criteria:

1. **Per-slot precision**: B's judged per-slot precision strictly above A's
   on the same corpus.
2. **Zero loss**: zero judged-relevant A injections lost (i.e. no a_only
   injection judged relevant). Dispute rule: a disputed judgment gets ONE
   re-judge with fresh context; still disputed = counts as relevant.
3. **Volume**: B's injection volume <= A's.

Threshold rule: the shipped `_IDF_MIN_MASS` is the LARGEST sweep value
satisfying guards 2 and 3; criterion 1 is reported at that value. Results are
stratified by prompt salient-term count (2-3 vs 4+). Judging is side-blind
(`judging.jsonl` only; `key.jsonl` stays closed until judgments are final).
If diff volume allows (>= `--holdout-min` diff prompts), a seeded random half
of diff prompts is held out (`judging-holdout.jsonl`) for the final report.

## Expected runtime (real corpus)

~2200 prompts over ~45 checkpoint files: prompts are replayed in global ts
order and grouped by snapshot equivalence class (the qualifying-file set), so
the index rebuilds ~45 times (~1s each), plus (1 + sweep size) suggest calls
per prompt (~5-15 ms each). With a 4-value sweep expect roughly **3-6
minutes** end to end.
