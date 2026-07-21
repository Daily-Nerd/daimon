# MCP server (read-only)

`daimon mcp serve` exposes daimon's memory as an MCP tool surface over stdio —
for hosts that speak MCP but have no hook system daimon can attach to. It is
opt-in (nothing registers it for you), read-only (four tools, no writes), and
pure standard library (no extra dependency, same as the rest of daimon).

```bash
daimon mcp serve   # blocks, serves JSON-RPC on stdio until EOF
```

## Tools

| Tool | What it returns |
|------|-----------------|
| `daimon_recall` | Search results with full provenance: trust class (`verbatim` = exact quote, `inferred` = model conclusion), author, supersession state, origin project slug |
| `daimon_brief` | The latest briefing for the current project — deterministic render, trust-tagged, resolutions withheld |
| `daimon_projects` | Every project daimon has memory for: slug, session, branch, last topic |
| `daimon_status` | Capture health: checkpoint freshness, last serialize result, outstanding failures, alarms — same payload as `daimon status --json` |

All four carry `readOnlyHint`. Tool-level failures (bad arguments, missing
FTS5) come back as `isError` results the agent can read; they never kill the
server.

## Scoping rules

The server inherits daimon's cross-project discipline:

- **Reads are project-scoped.** The project is resolved from the process
  working directory, or `DAIMON_PROJECT_DIR` when set — put one of them in
  your host's MCP config.
- **No implicit fallback.** A project with no checkpoint gets
  `no checkpoint for this project` plus a pointer to `daimon_projects` —
  never another project's content. Crossing projects is always explicit:
  pass a `slug` to `daimon_brief` or `daimon_recall`.
- **Kill switch honored.** With `DAIMON_DISABLE=1` the server exits cleanly
  without serving, so a disabled daimon never breaks host startup.
- **Usage stays local.** Each call writes one `mcp:<tool>` line to daimon's
  local usage log (the same `daimon stats` counters as the CLI). Nothing is
  transmitted.

## Registering with a host

Claude Code (CLI):

```bash
claude mcp add daimon -- daimon mcp serve
```

Generic stdio MCP config (Windsurf, Cursor, and most others accept this
shape):

```json
{
  "mcpServers": {
    "daimon": {
      "command": "daimon",
      "args": ["mcp", "serve"],
      "env": { "DAIMON_PROJECT_DIR": "/path/to/your/project" }
    }
  }
}
```

If your host launches MCP servers from the project directory you can omit
`DAIMON_PROJECT_DIR` — the working directory resolves the same way.

Note: on hosts where daimon's hooks already run (Claude Code, Windsurf,
Codex), the hook briefing is the richer integration — the MCP server is for
reads on demand and for hosts without hooks. Running both is fine; the tools
are read-only.
