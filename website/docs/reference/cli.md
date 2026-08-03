---
sidebar_position: 1
---

# CLI reference

Every daimon verb, grouped by what you are trying to do. Each command's
`--help` carries the full flag surface; this page is the map.

## The daily loop

| command | what it does |
| --- | --- |
| `daimon brief` | Render the briefing from the latest checkpoint — where you left off, trust-tagged. `--team` adds teammates' latest; `--slug <s>` reads another project's bucket explicitly. |
| `daimon recall "query"` | Full-text search across local + team checkpoint history. `--json` for rows, `--all-projects` to widen. |
| `daimon handoff "Do X first. Beware: Y."` | Leave an authored baton for the next session — it renders above every briefing section and never competes with ranked items. `--clear` retracts; a new baton supersedes the old. |
| `daimon loops` | List open, addressable loop items with their ids — the read counterpart to `resolve`'s write path. |
| `daimon status` | Checkpoint presence and age, last serialize outcome, health warnings. `--suppressed` lists withheld resolved items. |

## Closing and correcting

| command | what it does |
| --- | --- |
| `daimon resolve <id or text>` | Mark an item resolved — append-only event; the item stops carrying. `--dry-run` previews the match; `--by agent --evidence "<quote>"` claims a close that is byte-checked at session end. |
| `daimon reverify <id>` | Assert a carried item is still true — evidence-gated, resets its staleness clock. Also the reject half of a supersession candidate. |
| `daimon forget <id or text>` | Remove one item's content from disk and index, leaving a hash-only tombstone. The deletion survives re-serialization of the original transcript. |
| `daimon log --text "…"` | Append a freeform timeline event to the project's event log — zero-LLM, audit-trail only. |

## Trust and audit

| command | what it does |
| --- | --- |
| `daimon verify-receipt` | Verify a checkpoint's signed provenance receipt (full cryptographic check via the vitni CLI). |
| `daimon audit-quotes` | Re-check every stored verbatim quote against its source transcript and report mismatches. Read-only — it never rewrites trust tags. |
| `daimon anchor <file> <symbol>` | Bind a cognitive item to a code symbol; briefings then warn when the anchored code drifts. |

## Setup and operations

| command | what it does |
| --- | --- |
| `daimon configure` | Detect the resolved LLM backend and fill gaps in `~/.daimon/env`. `--test` runs a live round-trip. |
| `daimon hooks install <host>` | Ship the host hook scripts (Windsurf, Codex) from the package. `list` / `status` inspect. |
| `daimon skill install <host>` | Install the daimon agent skill into a host's skill directory. Re-run after upgrades. |
| `daimon team init\|sync\|status` | Shared team memory via a sidecar repo — default-closed routing, shape-redacted before anything syncs. |
| `daimon stats` | Local usage and capture aggregates — nothing is transmitted; sharing the output is a deliberate paste. `--json` for machines. |
| `daimon heal` | Re-serialize the most recent failed session when it is safe to do so. |
| `daimon projects` | List every project daimon holds a checkpoint for, with topic teasers. |

## Internals (invoked by hooks, documented for completeness)

| command | what it does |
| --- | --- |
| `daimon serialize <transcript>` | Turn a transcript file into a checkpoint — the SessionEnd hooks call this; running it by hand backfills one. |
| `daimon write-checkpoint` | Store a checkpoint supplied as JSON on stdin — the in-session introspection path. Trust is code-clamped: nothing on this path can claim `verbatim`, because there is no transcript to verify against. |
| `daimon recall-inject` | The per-prompt suggestion backend behind the recall hook: prompt on stdin, zero to two prior-work lines out, exit 0 always. |
| `daimon mcp serve` | Serve the daimon tools over MCP (stdio). |

## Briefing annotations, decoded

The briefing marks every line; the full trust story lives in
[trust classes](../concepts/trust-classes.md). Quick key:

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]` — how the item was captured.
- `[carried]` — inherited from an earlier session, not fresh context.
- `[≈ corroborated ×N]` — N independent sessions witnessed the claim.
- `[✓ world-checked]` — a live probe agreed with this claim during this brief.
- `HANDOFF (…)` — an authored baton from the previous session; it outranks everything below it.
- `— because …` — the decision's stated reasoning, captured only when the transcript states it.
