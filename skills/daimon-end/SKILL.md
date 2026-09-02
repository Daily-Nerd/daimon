---
name: daimon-end
description: In-session self-serialization — write a Daimon cognitive checkpoint NOW from what the live session still holds, before exiting. Use when ending a session and you want the next session's briefing to be fresh immediately (no wait for the automatic SessionEnd reconstruction), e.g. you plan to restart soon. Write-only; does not exit.
---

# Daimon — End-of-session self-serialization (`/daimon-end`)

The automatic SessionEnd hook reconstructs a checkpoint from the full transcript
*after* exit — accurate and verbatim-capable, but it lands 4–25 min later. If you
restart inside that window, the next session briefs from the **previous** session's
stale checkpoint.

This skill closes that window: the **live** session writes its own checkpoint now,
from state it still holds. It is provisional and **never replaces** the automatic
path — the reconstruction still runs, supersedes this checkpoint, and keeps it as
a `prev` pointer. So it does not need to be perfect to be useful.

## What to do when invoked

1. **Resolve closed loops FIRST.** If any briefed loop was closed this session,
   record it before writing the checkpoint:

   ```bash
   daimon resolve <id> --by agent --evidence "<exact contiguous transcript quote>"
   ```

   (See the daimon-briefing skill's "Closing loops" section for the full
   quote-discipline rule.) The CLI answers `claim recorded ... pending
   verification at session end` — that is the expected output, not a failure:
   the resolution is a provisional claim, byte-checked against the transcript
   when the session ends.

2. **Decide whether to hand off.** The baton is a different instrument from the
   checkpoint: the checkpoint captures full state, the baton is the ONE
   deliberate message that leads the next briefing. If the next session should
   start somewhere specific — a reply to check, a play whose timing matters, a
   decision waiting on the human — write it now:

   ```bash
   daimon handoff "<where the next session should start, and why>"
   ```

   Skipping is a valid decision when the checkpoint alone suffices; a baton
   that merely restates the checkpoint is noise. But make the decision
   consciously — a session that captures everything and hands off nothing
   leaves the next session to infer its own starting point.

3. **Emit a checkpoint JSON** from your in-context knowledge of THIS session,
   conforming exactly to the schema:

   ```json
   {
     "session_id": "introspection-<short-unique-id>",
     "working_context": {
       "active_topic": {"text": "<one line, or empty>", "trust": "inferred"},
       "open_questions": [
         {"text": "<unresolved loop>", "trust": "verbatim", "quote": "<exact transcript quote>", "external_state": true}
       ],
       "recent_decisions": [
         {"text": "<decision or assistant-side fix>", "trust": "inferred"}
       ]
     },
     "epistemic_snapshot": {
       "strong_beliefs": [{"text": "<belief>", "trust": "inferred"}],
       "uncertainties": [{"text": "<open uncertainty>", "trust": "inferred"}]
     }
   }
   ```

   Every item needs `text` + `trust`. `external_state: true` marks items whose
   state may have changed *outside* the AI session (a PR you'll merge, a deploy) —
   these surface first in the briefing.

   Volume: 5–10 items per list; prefer the ones a fresh session would get wrong.

4. **HONESTY RULE (load-bearing).** Quote only what you can reproduce exactly —
   an honest quote helps the later merge with the automatic reconstruction, and
   this path has no transcript to check against, so the CLI records every item
   as `inferred` either way (#511). Anything from an earlier,
   **compacted/summarized** part of the session you can no longer quote verbatim →
   `trust: "inferred"`, no `quote`. Do not fabricate quotes.

5. **Write it** via the CLI (reads JSON on stdin, validates the schema, routes to
   this project + global + a per-session file, atomically, with rotation). Write
   the JSON to a temp file and pipe it:

   ```bash
   daimon write-checkpoint --project "$PWD" < /tmp/daimon-end.json
   ```

   It prints `wrote checkpoint: <path> (source: introspection)`. If it reports a
   schema-validation error, fix the JSON and retry — do not store garbage.

6. **Confirm** to the user with the printed checkpoint path.

## Where things go

Seven stores, each read by a different surface. Route by what the next
session must see, and never by which command is nearest. The harness's own
memory file (MEMORY.md, AGENTS.md, GEMINI.md) is not one of these: daimon
never reads it, so a fact placed there is lost to every other host.

| What you hold | Where it goes | Who reads it back |
| --- | --- | --- |
| Facts, decisions, beliefs, open loops from THIS session | the checkpoint, step 3 above (`daimon write-checkpoint`) | the next briefing, `daimon recall`, carry |
| The ONE thing the next session should do first | `daimon handoff "<do X first, beware Y>"` (2000 chars max) | the top of the next briefing |
| A briefed loop this session closed | `daimon resolve <id> --by agent --evidence "<quote>"` | the briefing withholds it once byte-checked |
| A rule that must never decay (a threshold, a boundary, a standing constraint) | `daimon ruling propose --subject ... --verdict ... --scope ... --evidence ... --by agent` | every briefing, once a human ratifies; 7 active per project by default |
| An approach that was tried and failed | `daimon refute add ... --by agent` | `daimon refute guard` before anyone revives it |
| A briefed item that moved but did not close | `daimon amend <id> --change progressed|blocked|changed --evidence "<quote>" --by agent` | the briefing, as an unconfirmed claim until a human settles it |
| A timeline fact worth an audit line and nothing more | `daimon log --text "..."` | nothing reads it back into a briefing or recall; it is an audit trail only |

If `daimon handoff` refuses a baton as too long, the trimmed content is not
homeless: facts go in the checkpoint, rules go to `ruling propose`. Do not
move it to the harness memory file.

## Rules

- **Write-only.** Do NOT exit/quit the session — that is the user's action.
- **Do not remove or disable the automatic hook.** The reconstruction's verbatim
  fidelity is the authoritative source once it lands.
- Routing/validation/atomic-write live in the CLI (`write-checkpoint`) — never
  hand-write checkpoint files or duplicate store logic.
