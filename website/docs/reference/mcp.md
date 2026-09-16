---
description: "daimon mcp serve exposes memory to MCP hosts over stdio. Opt-in, read-only, five tools and no write surface, standard library only."
---

# MCP server (read-only)

`daimon mcp serve` exposes daimon's memory as an MCP tool surface over stdio —
for hosts that speak MCP but have no hook system daimon can attach to. It is
opt-in (nothing registers it for you), read-only (five tools, no writes), and
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
| `requests_inbox` | Requests other projects have addressed to this one — the read-only pull side of the [cross-project request ledger](cli.md#coordinate). `daimon_brief` never carries this content; opening, answering, or deciding a request is CLI-only. |

All five carry `readOnlyHint`. Tool-level failures (bad arguments, missing
FTS5) come back as `isError` results the agent can read; they never kill the
server.

`daimon_brief` renders the same trust tags as the CLI, so a line may also
carry the `[≈ corroborated ×N]` badge — a count of independent sessions that
witnessed the claim, on its own axis and never a higher trust class. See
[trust classes](../concepts/trust-classes.md).

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

Every host daimon adapts now registers this server as part of its own
install path — nothing to run by hand on any of them. The recall hint (the
per-prompt "you worked on this before" line) names the tool instead of the
`daimon recall "..."` shell command wherever a prompt-time recall hook
exists; where one does not, the server is still worth registering, because
the host's own agent can call the tool directly.

| Host | MCP registration | Recall hint's tool form |
|------|-------------------|--------------------------|
| Claude Code | supported | supported |
| Kimi Code | supported | supported |
| Codex | supported | supported |
| Windsurf | supported | unsupported (no prompt-time recall hook) |
| Gemini CLI | supported | unsupported (no prompt-time recall hook) |

- **Claude Code (plugin):** the [plugin install](../hosts/claude-code)
  declares this server in `.claude-plugin/plugin.json`'s `mcpServers`, so
  `daimon_recall` and its siblings are listed the moment the plugin is
  installed.
- **Claude Code (CLI, without the plugin):** `claude mcp add daimon -- daimon mcp serve`.
- **Codex:** `daimon hooks install codex` writes `[mcp_servers.daimon]` into
  `~/.codex/config.toml` alongside its `hooks.json` registration.
  `daimon hooks remove codex` takes only that table back.
- **Kimi Code:** `daimon hooks install kimi` merges `mcpServers.daimon` into
  `~/.kimi-code/mcp.json` (or `$KIMI_CODE_HOME/mcp.json`) alongside its
  `config.toml` hook registration, and its `UserPromptSubmit` command picks
  up the tool-form hint automatically once that entry exists.
- **Windsurf:** `daimon hooks install windsurf` prints an `mcpServers`
  snippet for `~/.codeium/windsurf/mcp_config.json`, the same way it already
  prints the hooks registration snippet — daimon does not write Cascade's
  own config files.
- **Gemini CLI:** the standalone `hook/gemini-hooks.py install` lifecycle
  manager registers `mcpServers.daimon` in `~/.gemini/settings.json`
  alongside its two hooks, and removes it on `uninstall`.

Generic stdio MCP config (any other MCP-speaking host):

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

Note: on hosts where daimon's hooks already run, the hook briefing is the
richer integration — the MCP server is for reads on demand and for hosts
without hooks. Running both is fine; the tools are read-only.
