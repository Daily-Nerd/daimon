---
sidebar_position: 8
description: "How to read the marks and annotations on a briefing line."
---

# Briefing format

Every briefing line is marked. The full trust story is in [trust classes](../concepts/trust-classes.md).

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]`, how the item was captured.
- `[carried]`: inherited from an earlier session, not fresh context.
- `(noted <UTC time>, re-check before acting)`: on a carried item that names a PR, branch, file or dependency: when the note was first captured. It says nothing about the state since; see [carry](../concepts/carry.md).
- `[≈ corroborated ×N]`: N independent sessions witnessed the claim.
- `[✓ world-checked]`: a live probe agreed with this claim during this brief.
- `HANDOFF (…)`: a baton from the previous session; it outranks everything below it. The header says who wrote it: a person (`left deliberately by previous session`) or an agent (`written by an agent, unverified`). Every host shows it: `daimon brief`, the MCP brief tool, the host API and the Hermes hook.
- `— because …`: the decision's stated reasoning, captured only when the transcript states it.

## Requests, the decision count and the trailer

Around the body a briefing also carries the furniture below. It is the same text on every host, because one composer writes it.

- `⚠ checkpoint format X != current Y …`: a warning line above the greeting. The checkpoint was written by a different daimon format, so some sections may render partially.
- `N decisions waiting on you here (M elsewhere) - daimon decide`: how many decisions wait on this project and, in brackets, on other projects. `daimon decide` lists them. The wording is fixed.
- Four request panels. Each is a block that opens at its title and ends at the next blank line, and each reads this project's own request ledgers:
  - `Requests waiting on you (from other projects):` the asks other projects addressed to you. A card reads `→ <id>  <headline>`, with `[info]`, `[blocking]` and `[done claimed]` marks, then a `From:` line and, when the severity is not the default, a severity line.
  - `Decisions on requests you sent:` what the recipient decided about your asks: a mark, the state, the id and the headline, then `To:`, a `Note:`, the `Done:` evidence and the latest reply when there are any.
  - `Requests you accepted and still owe:` work you agreed to. It ends with the line that says an agent may close one with `daimon request done` without asking a person.
  - `Requests you sent that nobody is moving:` `! <id>  <headline>  [state, age in days, to <project>]`.
- A panel shows at most three cards and ends with `(+N more …)` when there are more. Under a tight byte budget every panel collapses to its title and `(N cut for budget, see: …)`, and a collapsed panel counts as not shown.
- `N resolved item(s) withheld` and `N quarantined item(s) withheld`: the last lines of a briefing, how many items it left out.

Every briefing host prints all of the above: `daimon brief` (so the Claude Code, Codex, Kimi Code and Gemini CLI hooks, which run it), the MCP `daimon_brief` tool (the route Windsurf uses), the host API's `render_briefing` and the Hermes hook. The panels and the count belong to the briefing of the project the process runs in: a read of another project (`--slug`, `--project` or an API path naming another project, or a path under the cross-project grant) shows none of them. The `(M elsewhere)` part of the count names other projects, so the MCP tool, the Hermes hook and the host API print it only when `DAIMON_MODEL_CROSS_PROJECT` is set; the CLI prints it unless the home is tenant-scoped. Only the CLI and the Hermes hook can render the body with an LLM (`DAIMON_LLM_BRIEFING`); the MCP tool and the host API are always deterministic. Anchor drift and `--team` stay on the CLI.

A card shown whole is recorded once as seen by its sender (a `surfaced` mark), when the host has received it: after the CLI flushes, after the MCP response is written, when `render_briefing` returns, when the Hermes hook returns. A card the budget collapsed is never marked. `api.preview_briefing` returns the same text and records nothing.
