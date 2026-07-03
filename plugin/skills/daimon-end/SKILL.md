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
from state it still holds. It is **provisional, belt-and-suspenders** — the
automatic reconstruction still runs and **supersedes** this one (rotation keeps
this as a `prev` pointer). So it does not need to be perfect to be useful, and it
**never replaces** the automatic path.

## What to do when invoked

1. **Emit a checkpoint JSON** from your in-context knowledge of THIS session,
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

2. **HONESTY RULE (load-bearing).** Mark `trust: "verbatim"` and include a `quote`
   ONLY if you can reproduce the EXACT transcript text. Anything from an earlier,
   **compacted/summarized** part of the session you can no longer quote verbatim →
   `trust: "inferred"`, no `quote`. Do not fabricate quotes. (This is the known
   weakness of introspection vs the full-transcript reconstruction — be honest and
   the merge/supersession handles the rest.)

3. **Write it** via the CLI (reads JSON on stdin, validates the schema, routes to
   this project + global + a per-session file, atomically, with rotation). Write
   the JSON to a temp file and pipe it:

   ```bash
   daimon write-checkpoint --project "$PWD" < /tmp/daimon-end.json
   ```

   It prints `wrote checkpoint: <path> (source: introspection)`. If it reports a
   schema-validation error, fix the JSON and retry — do not store garbage.

4. **Confirm** to the user with the printed checkpoint path.

## Rules

- **Write-only.** Do NOT exit/quit the session — that is the user's action.
- **Do not remove or disable the automatic hook.** This accelerates; it never
  replaces. The reconstruction's verbatim fidelity is still the authoritative
  source once it lands.
- Routing/validation/atomic-write live in the CLI (`write-checkpoint`) — never
  hand-write checkpoint files or duplicate store logic.
