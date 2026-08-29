---
id: 63
type: landmine
title: Route.OWN_ELSE_GLOBAL in capture/lifecycle/amend/mcp_tools returns another project's dict with no exception
severity: high
confidence: 0.9
created: 2026-08-29
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/capture.py
  - path: plugin/daimon_briefing/cli/lifecycle.py
  - path: plugin/daimon_briefing/cli/amend.py
  - path: plugin/daimon_briefing/mcp_tools.py
  - pattern: "Route\.OWN_ELSE_GLOBAL"
evidence:
  - note: #784 tenant leak; #795 stage 2 migration — every read in these four modules is Route.OWN by decision (persist paths and project-scoped surfaces)
  - note: pinned by tests/test_migration_manifest.py::test_own_else_global_never_appears_in_own_only_modules
expires:
  condition: "one of these modules legitimately gains a labeled global-fallback display surface, reviewed against scar 0057 (un-routed checkpoints) and 0058 (route reconstruction)"
  review_after: 2027-02-28
status: active
---

Every `read_latest_body` call in these four modules is `Route.OWN` on purpose:
capture and amend PERSIST what they read (a foreign body would be carried into
this project's bucket permanently, #784's failure mode), and lifecycle and
mcp_tools are project-scoped surfaces where an un-routed or foreign body hands
out ids no scoped write can act on. A wrong `Route.OWN_ELSE_GLOBAL` here
raises nothing and type-checks clean — it returns another project's dict, the
exact silent failure the enum migration was built to prevent. If you need the
global pointer in one of these files, you are on the wrong surface: display
callers live in cli/__init__.py and hooks.py, where the route is labeled and
gated. The manifest test pins today's sites; this scar guards the ones added
after it.
