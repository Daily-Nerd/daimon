---
id: 0
type: fence
title: The action-recall seen file is atomic but NOT locked, and the lost update is deliberate
severity: medium
confidence: 0.85
created: 2026-09-14
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/__init__.py
  - pattern: "_save_seen_atomic"
evidence:
  - note: "#1031, the action-recall surface"
expires:
  condition: "a measured case where one repeated recall line actually cost something, or a surface that writes state a repeat would corrupt rather than duplicate"
  review_after: 2027-03-14
status: candidate
---

`_save_seen_atomic` writes the per-session cooldown file temp-then-rename. It
does NOT take a lock, and that gap is on purpose rather than unfinished.

The action surface fires before a SHELL ACTION, and a host runs several of
those at once. Two concurrent runs can both read the same cooldown state and
both write, and the second write wins: whatever the first one recorded is
gone. The cost of that is bounded and small, exactly one repeated suggestion
line, because the state being lost is "I already said this".

What `os.replace` buys is the other failure, which is not bounded. A partial
write read back by `_load_seen` parses as corrupt, `_load_seen` falls open to
empty state, and the session loses its WHOLE cooldown rather than one entry.
So the rename is load-bearing and the missing lock is not.

Do not add a lock here to "finish the job". A lock in front of every shell
action buys one suppressed duplicate line and pays for it with a contended
file on the critical path of every command the agent runs, on a surface whose
entire contract is that it costs nothing when it has nothing to say. If a
future surface writes state where a lost update means something worse than a
repeat, that surface needs its own writer, not this one widened.
