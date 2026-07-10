# Windsurf (Cascade)

Windsurf's adapter is shipped and code-verified (native-transcript serialize,
probe-hardened), live validation in progress. No logbook entry yet documents
a complete dogfooded loop the way Claude Code's does — treat end-to-end "runs
on Windsurf" as inferred from code + unit tests until one is on record.

## Install

```sh
daimon hooks install windsurf   # copies daimon-windsurf-hooks.py, _daimon_hook_lib.py,
                                 # and redact.py to ~/.daimon/hooks/, then prints the
                                 # registration snippet for Cascade's hooks config
daimon hooks list                # hosts with packaged hook scripts
```

`daimon hooks install <host>` ships `redact.py` alongside the hook script(s)
so a standalone host adapter can scrub secrets at its own write sites
(transcript accumulation, checkpoint, event log) without importing the
venv-only `daimon_briefing` package — a test keeps this copy byte-identical
to the canonical `plugin/daimon_briefing/redact.py`. Re-run `daimon hooks
install windsurf` after every `daimon` upgrade so the installed scripts (and
the bundled `redact.py`) stay in sync with the installed CLI.

Point Windsurf's Cascade hooks config (user-level JSON — see
[the Cascade hooks docs](https://docs.windsurf.com/windsurf/cascade/hooks))
at the installed script for all **three** events: `pre_user_prompt`,
`post_cascade_response`, and `post_cascade_response_with_transcript`.

## How the one script covers three events

One script, `daimon-windsurf-hooks.py`, is registered for three Cascade hook
events:

- **Native transcript preferred:** when `post_cascade_response_with_transcript`
  is registered and Cascade's native `.jsonl` transcript
  (`~/.windsurf/transcripts/<trajectory_id>.jsonl`) exists for the trajectory,
  it is serialized directly — no accumulation needed.
- **Accumulation fallback:** `pre_user_prompt` / `post_cascade_response`
  carry no transcript path, so the adapter appends each turn to its own
  `~/.daimon/windsurf/transcripts/<trajectory_id>.md` in the same
  `**role**:`-marked shape `daimon serialize` already parses.
- **Throttled serialize:** both serialize-capable events fire every turn;
  `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` (default 300s, `0` = every turn)
  gates the spawn per trajectory, sharing one marker so registering both
  events never double-spawns.
- **Debounced finalizer:** every serialize-capable event also arms a detached
  one-shot sleeper; after `DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` (default
  600s, `0` disables) with no further activity for the trajectory, the last
  turn's sleeper serializes the final transcript state — so a session whose
  last turns landed inside the throttle window still gets captured.
- **Self-probing:** any payload shape the adapter can't handle is dumped to
  `~/.daimon/windsurf/unparsed-<event>-<stamp>.json` (at most one dump per
  event name), so the next adapter iteration has real evidence to work from
  instead of another manual probe round.
- **No briefing injection:** Cascade's hook set has no session-start-equivalent
  event, so unlike Claude Code/Codex/Gemini the briefing is not injected as
  context — it's read via the terminal (`daimon brief`). This is a permanent
  host constraint, not a bug.
- Fail-open everywhere; kill switch `DAIMON_DISABLE=1`.

Windsurf has no session-end event, so serialization runs on the throttle
above, with a debounced finalizer covering the session tail: a session whose
last turns land inside the throttle window is serialized once
`DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` (default 600) passes with no new
activity, instead of losing those turns. Set the knob to `0` to disable the
finalizer. `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL=0` remains the zero-delay
stopgap — it serializes every turn (one LLM call per turn), so nothing ever
waits on the quiet period. A knob worth setting for your first week:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env   # don't skip short first sessions
```

## Teach the agent the protocol

```sh
daimon skill install windsurf             # ~/.codeium/windsurf/skills/daimon/SKILL.md
daimon skill install windsurf --project   # .windsurf/rules/daimon.md
```

Re-run install after upgrading `daimon` to refresh the content.

## Verify

```sh
daimon status
```

Briefings are read with `daimon brief` in a terminal — not injected — so
`daimon status` (rather than a session-start prompt) is the way to confirm a
checkpoint was written after your last session.
