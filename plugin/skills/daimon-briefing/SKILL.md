---
name: daimon-briefing
description: Dream-briefing — surfaces a "while you were away / here's where we left off" briefing at the start of a resumed session, reconstructed from a cognitive checkpoint written at the end of the prior session. Use to recall open loops, decisions, and facts whose state may have changed outside the AI session (e.g. a PR you merged yourself).
---

# Daimon Dream-Briefing

This plugin keeps continuity across sessions. At the **end** of each session it
serializes the transcript into a cognitive checkpoint (open loops, decisions,
beliefs, with extractively-pinned verbatim quotes). At the **start** of the next
session it injects a skimmable briefing so you resume from a faithful prior state
instead of a confident guess.

## What it surfaces, in order

1. **Verify before trusting** — items whose state may have changed *outside* this
   session (a PR you said you'd merge, a deploy, a file edited elsewhere). These come
   first because they are the gap that produces confident-but-wrong assertions.
2. **Open loops** — questions left unresolved at the end of last session.
3. **Decisions made** — explicit choices, including assistant-side fixes/diagnoses.
4. **Active topic / beliefs / uncertainties.**

Each item is marked `✓ verbatim` (pinned to an exact quote — trust it) or
`~ inferred` (paraphrased — treat with appropriate caution).

## Automatic behavior

You do not need to invoke anything. The briefing appears automatically on the first
turn of a new session if a prior checkpoint exists. Checkpoints are written
automatically when a session ends.

## Manual trigger

To re-read the latest briefing on demand, run the bundled CLI:

```bash
daimon brief
```

## Configuration

See the plugin README. Key knobs: `DAIMON_DISABLE=1` (kill switch),
`DAIMON_CHECKPOINT_DIR`, `DAIMON_MIN_MESSAGES`, `DAIMON_LLM_*` (falling back to
`LITELLM_*`), `DAIMON_LLM_BACKEND=command` + `DAIMON_LLM_COMMAND` (headless-CLI fallback).

## Scope (Slice 1)

Local-file checkpoints, single-pass serialization, no Honcho. Long-transcript recall
(chunking) is Slice 2; Honcho-backed cross-session recall is Slice 3.
