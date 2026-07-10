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

A briefing can also show a staleness warning: `N carried item(s) unverified
for >N days — world-check before repeating as true`. A carried item that
keeps getting restated session after session is not corroborated just
because it agrees with itself — check the world (code, git, issue tracker)
before treating it as current fact.

## Automatic behavior

You do not need to invoke anything. The plugin wires the host's native session
hooks: a checkpoint is written automatically when a session **ends**, and the
briefing appears automatically at the **start** of the next session if a prior
checkpoint exists. Between those, a lightweight **proactive recall** watches your
prompts and surfaces a one-line "you worked on this before" pointer when the
current prompt overlaps a prior open loop — without re-suggesting anything the
start-of-session briefing already carried.

## Manual trigger

To re-read the latest briefing on demand, run the bundled CLI:

```bash
daimon brief
```

## Configuration

See the plugin README. Key knobs: `DAIMON_DISABLE=1` (kill switch),
`DAIMON_CHECKPOINT_DIR`, `DAIMON_MIN_MESSAGES`, `DAIMON_LLM_*` (falling back to
`LITELLM_*`), `DAIMON_LLM_BACKEND=command` + `DAIMON_LLM_COMMAND` (headless-CLI
fallback), `DAIMON_LLM_COMMAND_INPUT=stdin|arg|file:<flag>` (how the prompt
reaches a command backend that doesn't read stdin, e.g.
`file:--prompt-file` for the Devin CLI).

## What ships today

Daimon is self-contained and host-agnostic — no server, no external memory
backend, stdlib-only at runtime. The capabilities behind the briefing:

- **Checkpoint → briefing loop.** Session end serializes the transcript into a
  per-project JSON checkpoint; session start reconstructs it into the briefing.
- **Chunked extraction.** Long transcripts are split into overlapping chunks,
  serialized pass-by-pass, then merged — so recall holds up on long sessions
  instead of degrading as the transcript grows.
- **Deterministic carry.** Unresolved open loops that still matter are carried
  forward into the next checkpoint by exact term overlap (no LLM in the carry
  step) and marked `[carried]` so you can see a loop survived from an earlier
  session rather than being freshly observed. When a carried item goes too
  long without anyone actually re-checking it against the world, the brief
  surfaces a staleness warning naming how many days it's been riding
  unverified.
- **Proactive recall.** A per-prompt pointer to prior work when your current
  prompt overlaps an open loop (see *Automatic behavior*).
- **Trust classing.** Every item is `✓ verbatim` (pinned to an exact quote) or
  `~ inferred` (paraphrased), so you know what to trust literally.
- **Code-drift detection.** `daimon anchor <file> <symbol>` binds a checkpoint
  item to a code symbol; the briefing flags it under **CODE DRIFT — verify
  before trusting** when that symbol's body changes or disappears (offline,
  stdlib `ast`).
- **Scars.** An optional session-end pass harvests negative-knowledge signals
  (abandoned approaches, landmines) into the repo's `.scars/` directory.
- **Status & self-heal.** `daimon status` reports checkpoint/briefing health;
  a failed capture self-heals on the next session start.
