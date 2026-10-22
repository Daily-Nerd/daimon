---
id: 0
type: landmine
title: An MCP tool argument that selects a bucket must reach view.model_read, and a consumer that reports on the project path needs the raw argument
severity: high
confidence: 0.85
created: 2026-10-21
authors: ["Kibukx"]
anchors:
  - path: plugin/daimon_briefing/mcp_tools.py
  - path: plugin/daimon_briefing/mcp_server.py
evidence:
  - note: "tests/test_mcp_model_scope.py walks every declared tool argument; an argument in neither view.MODEL_SELECTORS nor the test's NON_SELECTORS fails, and every declared selector must be refused without DAIMON_MODEL_CROSS_PROJECT"
  - note: "tests/test_mcp_server.py::test_status_tool_matches_cli_payload: passing the RESOLVED project to status_payload changed identity.cwd from the process cwd to the project, so status takes ModelRead.project_arg (the argument as given)"
expires:
  condition: "the MCP tools take a Scope parameter in place of loose slug, all_projects and project arguments"
  review_after: 2027-04-21
status: candidate
---

On the model surface the host chooses the read scope, never the caller. The
refusal used to be an `if tenant_scoped() and slug` in each handler, keyed on
the argument NAME, so `daimon_brief {"project": <other dir>}` went through. The
decision is now `view.model_read(slug=, all_projects=, project=)`: it asks
whether the request leaves the own bucket, whatever argument expressed it, and
refuses unless DAIMON_MODEL_CROSS_PROJECT is set (never under tenant scope).

A new tool argument that can address a bucket goes into `view.MODEL_SELECTORS`
and through `mcp_tools._read`; a handler must not test a selector itself. Do not
hand a consumer the resolved path when it reports on the argument:
`status_payload` stamps `identity.cwd` from what it is given, so it takes
`ModelRead.project_arg`. The tool descriptions in `mcp_server._tool_descriptors`
offer the selectors only under the grant; the refusal stands for a model that
asks anyway.
