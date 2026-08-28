---
id: 0
type: landmine
title: Detecting read_latest's global fallback by path existence misses the torn own-pointer
severity: high
confidence: 0.95
created: 2026-08-28
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/store.py
  - path: plugin/daimon_briefing/hooks.py
  - path: plugin/daimon_briefing/cli/__init__.py
  - pattern: "fallback_used|project_latest_path\\([^)]*\\)[^\\n]*exists\\(\\)"
evidence:
  - note: "#787 — brief rendered a foreign body on a torn own pointer"
  - note: "#784 — the SessionStart injection rendered another project's checkpoint"
expires:
  condition: "read_latest reports whether it fell back, instead of callers inferring it"
  review_after: 2027-02-28
status: candidate
---

`store.read_latest()` falls back to the global pointer (the most recent checkpoint
of ANY project) in TWO different cases, and they do not look alike from outside.
The obvious one is a project with no bucket, where its `latest.json` does not
exist. The second is a project whose own pointer EXISTS but fails to parse: the
torn-pointer branch treats it as absent and falls through to the global pointer
anyway (`store.py:1376-1403`).

So the natural detection, comparing `store.project_latest_path(project)` against
`.exists()`, is blind to the second case. It concludes the checkpoint is local
while the caller is in fact holding another project's data. `daimon brief` shipped
that detection and its #96 foreign-body suppression therefore did not fire on a
torn own-pointer (#787); both call sites now decide before the read instead.

Do NOT reintroduce that detection in a new caller. Ask `read_latest` not to fall
back in the first place, by passing `fallback=` computed from policy (the injection
hook after #784), or read the own pointer with `fallback=False` first and let its
absence be the answer (`brief` after #787). Deciding before the read cannot be fooled by
either case. Note the policy has a second half: when the project is UNKNOWN the
fallback must stay ON, because there is no per-project pointer to prefer and
nothing is foreign to a session with no project identity. A fix that suppresses
the fallback unconditionally passes the leak test and silently removes the
briefing from every pre-routing host; the full suite caught exactly that.
