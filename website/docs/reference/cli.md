---
sidebar_position: 1
---

# CLI reference

Every daimon verb, grouped by what you are trying to do. Each command's
`--help` carries the full flag surface; this page is the map.

## Set up

| command | what it does |
| --- | --- |
| `daimon configure` | Detect the resolved LLM backend and fill gaps in `~/.daimon/env`. `--test` runs a live round-trip. |
| `daimon hooks install <host>` | Ship the host hook scripts (Windsurf, Codex) from the package. `list` / `status` inspect. |
| `daimon skill install <host>` | Install the daimon agent skill into a host's skill directory. Re-run after upgrades. |
| `daimon heal` | Re-serialize the most recent failed session when it is safe to do so. |
| `daimon mcp serve` | Serve the daimon tools over MCP (stdio). |

## Brief

| command | what it does |
| --- | --- |
| `daimon brief` | Render the briefing from the latest checkpoint — where you left off, trust-tagged. `--team` adds teammates' latest; `--slug <s>` reads another project's bucket explicitly. The briefing also carries a one-line count of decisions waiting on you, for example `3 decisions waiting on you here (2 elsewhere) - daimon decide`, pointing at `daimon decide`. |
| `daimon recall "query"` | Full-text search across local + team checkpoint history. `--json` for rows, `--all-projects` to widen. |
| `daimon handoff "Do X first. Beware: Y."` | Leave an authored baton for the next session — it renders above every briefing section and never competes with ranked items. `--clear` retracts; a new baton supersedes the old. |

## Check

| command | what it does |
| --- | --- |
| `daimon why <item-id>` | The trust inspector: show every evidence axis behind one item — independent capture, provenance, source, byte-integrity, current support, quote-check outcome, lifecycle, corroboration. `--source` adds one bounded, redacted source window; `--json` for machines. Item ids come from `daimon recall` or `daimon loops`. |
| `daimon verify-receipt` | Verify a checkpoint's signed provenance receipt (full cryptographic check via the vitni CLI). |
| `daimon reverify <id>` | Assert a carried item is still true — evidence-gated, resets its staleness clock. Also the reject half of a supersession candidate. |
| `daimon audit quotes` | Re-check every stored verbatim quote against its source transcript and report mismatches. Read-only — it never rewrites trust tags. |
| `daimon audit privacy` | Prove the deletion contract: hash every plaintext field on every surface (checkpoints, rotated pointers, the event ledger, the team mirror, the recall index and its orphan snapshots) and report any forgotten value that survived. Read-only. |
| `daimon refute list\|show\|search\|guard` | Read the negative-knowledge ledger without decay. `guard` emits active exact-anchor/subject matches only; it is advisory and never blocks a command. `search` returns both polarities, labelled; `list` and `guard` stay refutation-only. Add `--json` for deliberation integrations. |
| `daimon ruling list\|show` | Read the standing rulings: human-ratified positive constraints on the same ledger, never decayed, never re-extracted. `show` includes pending agent proposals. |
| `daimon serve` | Open the [read-only local viewer](viewer.md) on localhost — search as recall, per-entry "why" pages, refutations, diff, check strip, print view. Nothing writes. |
| `daimon relations list\|show\|confirm\|reject\|retract` | The [typed relation ledger](relations.md): machines propose, only a person confirms, and deciding needs an interactive terminal. Candidates never render on an entry surface. |

The auditors share one exit contract, so a script can act on the answer:

| exit | meaning |
| --- | --- |
| `0` | proven clean — every surface was scanned and nothing was found |
| `1` | residue found; the report names the surface and the hash (never the text) |
| `3` | cannot prove — a surface could not be read, or nothing was in scope to scan. Never treat this as clean |

`--project <dir>` scopes to one project, `--all` audits every local project
(each against its own tombstones); the two are mutually exclusive.

## Correct

| command | what it does |
| --- | --- |
| `daimon resolve <id or text>` | Mark an item resolved — append-only event; the item stops carrying. `--dry-run` previews the match; `--by agent --evidence "<quote>"` claims a close that is byte-checked at session end. |
| `daimon anchor <file> <symbol>` | Bind a cognitive item to a code symbol; briefings then warn when the anchored code drifts. |
| `daimon refute add\|ratify\|revise\|overturn` | Manage scoped negative knowledge in its own append-only ledger. Agent writes remain candidates; only an explicit human ratification activates a guard, and `ratify` requires the human path — an interactive terminal with `--by` omitted. Revisions require a new typed evidence citation, whose shape is checked but never resolved or verified, and reset an active refutation to candidate until it is ratified again. Agent overturns remain proposals. |
| `daimon ruling propose\|ratify\|revise\|retire` | Manage standing rulings on the same ledger, with a stricter lifecycle: `ratify` shows the full text, discloses that it will render into every future session, and binds the activation to the text it displayed; a human revising an active ruling confirms the change and the ruling stays active; agent revise and retire calls record proposals while the text stands; activation refuses past the cap (`DAIMON_RULING_CAP`, default 7). Retirement needs no evidence citation. |

### Rulings from a host process

The CLI mints two channels only: `cli-tty` (an interactive terminal) and `cli-agent` (`--by agent`). It never grows a flag for the other two human channels, `ui` and `signed`, because a flag an agent could pass from a shell would be a self-declared human channel. Those two exist for a host process that holds the authority itself, an operator it verified out of band, and they are written through the library, in process:

- `daimon_briefing.refutations.ratify(refutation_id, channel="signed", note="...", project_dir=...)` activates a proposed ruling. `channel` is the channel the host observed, one of `ui` or `signed`; any other name is refused. Cite the proof of the operator's action in the ruling's evidence (`url:`, or `receipt:` once the signature exists).
- `daimon_briefing.refutations.listing(states={"active"}, polarity="ruling", project_dir=...)` and `daimon_briefing.briefing.active_rulings(project_dir)` read the active set, each row with `subject`, `scope`, `anchors`, `activation_channel`, `evidence`, and the rule text itself. `anchors` are free strings set with `ruling propose --anchor`; a host that enforces per message matches on them and decides what happens itself.

The record renders as `ratified (signed)` or `ratified (ui)`, never as human-ratified without the tier. Nothing local is unforgeable: a caller with machine access can drive a UI or allocate a terminal. What the channel earns is provenance, not proof; forgery costs deliberate impersonation instead of one word, and the channel stays auditable afterwards.

## Forget

| command | what it does |
| --- | --- |
| `daimon forget <id or text>` | Remove one item's content from disk and index, leaving a hash-only tombstone. The deletion survives re-serialization of the original transcript. |

## Coordinate

A request lives in the sender's own project bucket; the recipient answers
with decision rows in its own bucket. The folded record is a read-time join —
nobody ever writes into another project's ledger.

| command | what it does |
| --- | --- |
| `daimon request open --to <dir> --ask "…" --why "…"` | Ask another project for something. `--to` takes the recipient's project **directory**, not its slug (a real slug starts with `-`, which argparse reads as an option — `--to=<slug>` also works). Validated against `daimon projects`, with near-match suggestions on a typo; `--anyway` records the ask against a project that has never serialized on this machine. `--blocking` and `--to-human` are flags on the record. Either channel. |
| `daimon request revise <id> [--ask] [--why] [--evidence]` | Answer a needs-info, or sharpen an open ask. Either channel; capped at 3 revisions per record lifetime — past the cap, open a new request with `--supersedes <id>` to keep the lineage visible. |
| `daimon request accept\|reject\|needs-info <id> [--note]` | Land a decision. Human-only — requires an interactive terminal. `reject` is final for that record; the sender supersedes with a new request rather than asking again. |
| `daimon request suppress <id> [--note]` | Drop a request out of the recipient's own briefing panel. Human-only; the record stays in `list`/`inbox`, and any later decision reverses it. |
| `daimon request done <id> --evidence "<quote>"` | Report the ask as satisfied. Either channel; an agent's claim renders `done (claimed, unverified)` until the recipient's next session-end byte-checks the evidence quote against its transcript. A human `done` renders plainly. |
| `daimon request list` | This project's own sent requests, undecided first. `--json` for machines. |
| `daimon request inbox` | Requests addressed TO this project, from every sender, undecided first — including ones the briefing panel dropped for attention. `--json` for machines. |

Two panels ride the same-project CLI `brief` only — never `--slug`, the
global-pointer fallback, or MCP. The recipient sees "Requests waiting on
you"; the sender sees "Decisions on requests you sent". Each is capped at 3
cards with a loud `+N more …` overflow line naming the command that shows
the rest — never a silent drop. Suppression is recipient-side attention
only: the sender's panel still reads a suppressed request as "surfaced,
undecided". An unanswered request renders `stale` after 3 recipient
sessions pass with no decision; a decided one leaves the sender's panel
after 2 sender sessions. Attention decays — records never delete, and both
stay fully visible in `list`/`inbox`.

`daimon status` adds a one-line summary, `requests: N open sent, M
awaiting you`, silent when both are zero.

The [MCP server](mcp.md) exposes the recipient-side view as the read-only
`requests_inbox` tool. `daimon_brief` never carries request content, and no
request write verb is reachable over MCP.

## Status

| command | what it does |
| --- | --- |
| `daimon status` | Checkpoint presence and age, last serialize outcome, health warnings. `--suppressed` lists withheld resolved items. |
| `daimon stats` | Local usage and capture aggregates — nothing is transmitted; sharing the output is a deliberate paste. Includes a receipt-probe line with lifetime totals (attempted, eligible, confirmed, contradicted, skipped, cured) when receipts are configured. `--json` for machines. |
| `daimon log --text "…"` | Append a freeform timeline event to the project's event log — zero-LLM, audit-trail only. |
| `daimon loops` | List open, addressable loop items with their ids — the read counterpart to `resolve`'s write path. |
| `daimon decide` | List what is waiting on YOU, each with the one command that closes it — the human-side mirror of `loops`. Reads records that already exist and writes nothing at all, so opening it never changes what the agent is shown. Scoped to this project; other projects arrive as counts. `--all-projects` adds every other project's queue as text, composed per project, each command routed with `--slug=<slug>` so it runs from here. The ten human-only verbs (`request accept`, `reject`, `needs-info`, `suppress`; `amend ratify`, `reject`; `ruling ratify`, `retire`; `refute ratify`, `overturn`) take that flag as routing only. Both are refused while `DAIMON_TENANT_SCOPED` is set. The briefing already carries this count on a single line, pointing back to this command. |
| `daimon projects` | List every project daimon holds a checkpoint for, with topic teasers. |
| `daimon slug <path>` | Print the checkpoint directory name daimon derives from a project path. No store, config, or ledger access, so it answers even for a path daimon has never written to. |
| `daimon team init\|sync\|status` | Shared team memory via a sidecar repo — default-closed routing, shape-redacted before anything syncs. |

The slug rule, stated exactly: leading and trailing whitespace is stripped
first, then every character that is not a Unicode word character or `-`
becomes `-`. Underscores, accented letters, and non-Latin scripts survive the
fold; an empty or whitespace-only input has no slug. Example: `/Users/x/my.proj`
becomes `-Users-x-my-proj`. This is not the scheme Claude Code uses for
`~/.claude/projects`. The two agree on slashes, dots, and spaces, and
disagree on `_` (daimon keeps it, Claude Code folds it to `-`). A host that
reimplements the rule can check its copy against `daimon slug` directly. A
path that starts with `-` needs `--` before it (`daimon slug -- -Users-x`),
the same escape any positional argument needs for a leading dash.

## Internals (invoked by hooks, documented for completeness)

| command | what it does |
| --- | --- |
| `daimon serialize <transcript>` | Turn a transcript file into a checkpoint — the SessionEnd hooks call this; running it by hand backfills one. |
| `daimon write-checkpoint` | Store a checkpoint supplied as JSON on stdin — the in-session introspection path. Trust is code-clamped: nothing on this path can claim `verbatim`, because there is no transcript to verify against. |
| `daimon recall-inject` | The per-prompt suggestion backend behind the recall hook: prompt on stdin, zero to two prior-work lines out, exit 0 always. |

## Briefing annotations, decoded

The briefing marks every line; the full trust story lives in
[trust classes](../concepts/trust-classes.md). Quick key:

- `[✓ verbatim]` / `[~ inferred]` / `[? untagged]` — how the item was captured.
- `[carried]` — inherited from an earlier session, not fresh context.
- `[≈ corroborated ×N]` — N independent sessions witnessed the claim.
- `[✓ world-checked]` — a live probe agreed with this claim during this brief.
- `HANDOFF (…)` — an authored baton from the previous session; it outranks everything below it.
- `— because …` — the decision's stated reasoning, captured only when the transcript states it.
