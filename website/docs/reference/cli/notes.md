---
sidebar_position: 10
description: "Behavior of daimon commands beyond their flags: refusals, exit codes, what each command writes and how the request panels behave."
---

# Command notes

What each command does beyond its flags. The flags, defaults and usage of every command are on the [generated group pages](index.md).

## Set up

### `daimon configure`

Detect the resolved LLM backend and fill gaps in `~/.daimon/env`. `--test` runs a live round-trip.

### `daimon hooks install [host]`

Ship the host hook scripts (Windsurf, Codex, Kimi) from the package. With no host, daimon detects what this machine runs, prints one line per host with the channel that serves it, and installs: silently for a single host, after one confirmation for several, and never without a terminal unless `--all` is passed. Also re-arms every ruling layer above this project, so a check ratified in a layer above it arms here too, even on a machine that has never synced that layer directly; a layer line prints only when the layer actually armed something. `remove [host]` unregisters what daimon registered, and `--all` covers every detected host. `list` / `status` inspect; `status` also audits this project's check manifest against its ledger, and exits non-zero on either kind of drift.

### `daimon skill install [host]`

Install the daimon agent skill into a host's skill directory. Same detection as `hooks install` when no host is named, and the same `--all`. Re-run after upgrades.

### `daimon skill status`

Audit each installed skill against what this CLI would write now: CURRENT, STALE, MISSING, BROKEN, UNREADABLE, NOT INSTALLED, PLUGIN or LEFTOVER, per host and scope, for both the generated `daimon` skill and the bundled `daimon-end`. Non-zero exit on drift, `--json` for machines. The skill is a static copy of content that moves with the CLI, so this is how you find out that a re-run after an upgrade was missed. A host served by the daimon plugin reports PLUGIN and needs nothing; a file left behind under one reports LEFTOVER, and its repair is `uninstall`, not `install`.

### `daimon check sync`

Rebuild `~/.daimon/checks`, the manifest of armed checks a host hook reads, and one file per check body, from this project's ledger, and from every ruling layer above it, so a layer's check re-arms here without a human ever running this command from inside the layer's own project on this machine. Every ratify, revise, retire and forget already does this, so run it only when the manifest was damaged out of band. Safe to repeat: an unchanged manifest is not rewritten. Prints how many checks are armed, zero included, with one further line per layer that armed something or failed to sync. `--check` audits instead of rebuilding and writes nothing, grading every layer above this project the same way: exit 0 in step, 1 drifted (this project's manifest, or a layer's), 3 when a manifest exists and cannot be read. `--json` for machines, with a `layers` key present only when a layer contributed something.

### `daimon heal`

Re-serialize the most recent failed session when it is safe to do so. A session refused because its project's events ledger could not be read is held back, with the reason and what to run, and its single retry is not spent. Once that ledger reads again each run takes one of them. A session whose project cannot be recovered, for a transcript filed under a project directory, is skipped with that reason instead of being checkpointed to the global directory; `daimon serialize <transcript> --project <dir>` files it. A retry writes its project and transcript on its own log line, so a busy log no longer loses them.

### `daimon import --project <dir> [--sessions N] [--transcript PATH ...]`

Serialize past transcripts of one project in one bounded run, the first fill for a project daimon has not captured yet. With no `--transcript` it discovers the project's transcripts on disk under the Claude Code and Codex roots, matched on the working directory each file recorded (the Claude Code directory name is a lossy slug and is never the match), and keeps the N newest by session end (`--sessions N`, default 5). The skips named below come out of those N and nothing older takes their place, so the same command rebuilds the same plan and you can read off which files a run touches; the plan then accounts for every transcript it read, one line per host (`claude-code: 5 of 14 transcripts under ~/.claude/projects recorded this project (7 recorded another project, 2 recorded no directory)`, or `codex: no transcripts under ~/.codex/sessions, ~/.codex/archived_sessions`), counts only and never another project's name, so an empty plan says whether the project was never recorded, the host deleted the files, or the files record another directory; when matches reach beyond the N it adds `window: 3 newest of 41 matched planned or skipped; raise --sessions to reach older history`, and `window: all 3 newest of 41 matched already captured; raise --sessions to reach older history` when every one of the N is already captured. A host root the user cannot list is one plan line (`codex root unreadable: <path>`) and the other roots still count. The plan then names the hosts discovery does not reach, one line: `not discovered: windsurf, kimi (records no project, pass --transcript); gemini (no registered root); hermes (no transcript file)`. Windsurf and Kimi record no project, so those go in by `--transcript`. Gemini CLI writes a transcript but daimon registers no root for it, and Hermes writes no transcript file, so neither can be imported yet, by discovery or by `--transcript`; a Gemini file given by hand is refused by name, exit 2. Giving `--transcript` and `--sessions` together is a usage error. It prints the plan before any model call: each session with its date, message count and about how many calls it will cost (one per chunk of the rendered transcript, plus the merges; a validation resample or a rescue on the fallback backend adds calls the plan cannot see), the total, a note when the session ends before the project's latest live capture (it is filed as history, `latest` stays, nothing is carried) or when the project came from the flag, and every transcript left out with the reason: already has a checkpoint, already captured (a success line in `serialize.log`, which outlives a pruned checkpoint), the live session or a file modified in the last ten minutes, too short, missing, unreadable. `--plan` stops there. Refusals exit 2 before any spend: a transcript whose rows record another working directory, one that records none and sits outside the project's Claude Code root (a Windsurf or Kimi transcript records none and is accepted only when `--project` is typed on the command line, since nothing in the file can confirm it), two files naming one session, the kill switch, an events ledger that cannot be read. The run goes oldest first, writes an `import:` marker in `serialize.log` before each serialize, and stops before the session whose planned calls would take the spent count past `--max-calls` (default: the planned total; spent is what each written checkpoint's `capture_cost.calls` stamp says, so a run passes the printed number only by one session's own retries and rescue), exit 0 with a resume hint, or at the first failed serialize of any kind, exit 1 with the hint. Each line reports the tokens the stamp recorded, or `tokens unreported`, the quotes verification downgraded, and the fallback marker; the summary prints on every exit after spend with the spent calls beside the planned ones and names every session the fallback produced. Running the same command again rebuilds the plan and skips done sessions at no cost; a session is paid for twice only if `serialize.log` is deleted or `DAIMON_LOG_DIR` changes while its checkpoint is also gone. The checkpoints carry `capture_door: "import"` and their success lines end in `[import]`; `daimon heal` never retries an import, though the session-start sweep can still spawn a killed Claude Code or Codex import inside its 14 day window once its heartbeat is stale.

### `daimon mcp serve`

Serve the daimon tools over MCP (stdio).

## Brief

### `daimon brief`

Render the briefing from the latest checkpoint, where you left off, trust-tagged. `--team` adds teammates' latest; `--slug <s>` reads another project's bucket explicitly. The briefing also carries a one-line count of decisions waiting on you, for example `3 decisions waiting on you here (2 elsewhere) - daimon decide`, pointing at `daimon decide`.

### `daimon recall "query"`

Full-text search across local + team checkpoint history. `--json` for rows, `--all-projects` to widen.

### `daimon handoff "Do X first. Beware: Y."`

Leave an authored baton for the next session, it renders above every briefing section and never competes with ranked items. `--clear` retracts; a new baton supersedes the old.

## Check

### `daimon why <item-id>`

The trust inspector: show every evidence axis behind one item: independent capture, provenance, source, byte-integrity, current support, quote-check outcome, lifecycle, corroboration. `--source` adds one bounded, redacted source window, refused whenever anything is forgotten on the machine, this project holds a quarantine or the trust ledger cannot be read; `--json` for machines. An id the reader may not see prints `[withheld: quarantine …]`, `[withheld: forgotten]` or `[withheld: trust ledger unreadable]` instead of its value. Item ids come from `daimon recall` or `daimon loops`.

### `daimon diff`

What changed between two retained checkpoints of this project: items added, restated, re-tagged, and every item the chain stopped carrying with the reason the record gives (resolved, superseded, or dropped when nothing says). An item withheld by a quarantine or an unreadable trust ledger gets a `withheld` row saying which generation it is withheld in; a forgotten item has no row at all. Defaults to the latest readable pair; `--from N --to M` counts generations back, where 0 is `latest.json`. `--json` for rows. Read-only, and there is no restore: a checkpoint records what a session did, so restoring one would assert a different past. Say something changed with `resolve`, `reverify` or `forget` instead.

### `daimon blame <item-id>`

How one item got here: the session that first stated it, every carry since, and every event that changed its state, in order. `daimon why` answers where a claim came from; this answers how it got here. A forgotten or quarantined item prints its marker and never its text. `--json` for machines.

### `daimon verify-receipt`

Verify a checkpoint's signed provenance receipt (full cryptographic check via the vitni CLI).

### `daimon reverify <id>`

Assert a carried item is still true: evidence-gated, it resets the staleness clock. Also the reject half of a supersession candidate. Exact id only, and a person's command. An id the briefing withholds because a person quarantined its value binds on a person's terminal only, needs `--evidence`, and prints the marker instead of the text. An id withheld because the trust ledger cannot be read is refused with the cure (`daimon trust repair`, exit 2), and a forgotten id is not found. It refuses with exit 2 while the project's events ledger cannot be read.

### `daimon audit quotes`

Re-check every stored verbatim quote against its source transcript and report mismatches. Read-only: it never rewrites trust tags. It audits what a reader may see, so a quarantined or forgotten item is neither checked, counted nor printed, and while the trust ledger cannot be read it checks nothing. Exit 0 checked clean, 1 mismatch, 3 nothing checkable. It always reports how many items it had to skip, and when that is all of them it says so instead of printing a rate over nothing. `--all` visits every project you may read. `--json` for machines, and it lists every failure, where the printed lines stop at `--top`.

### `daimon audit privacy`

Prove the deletion contract: hash every plaintext field on every surface (checkpoints, rotated pointers, the event ledger, the team mirror, the recall index and its orphan snapshots) and report any forgotten value that survived. Read-only. A value only a teammate forgot that still sits in plaintext here is reported as `SUPPRESSED-PRESENT`, and this class never changes the exit code, since scrubbing it is opt-in through `DAIMON_TEAM_APPLY_FORGET`. When some project's events ledger cannot be read the forget set may be incomplete, so the recall-index check cannot prove its result: the audit says so and exits 3.

### `daimon refute list|show|search|guard`

Read the negative-knowledge ledger without decay. `guard` emits active exact-anchor/subject matches only; it is advisory and never blocks a command. `search` returns both polarities, labelled, and only matches values the reader may see; `list` and `guard` stay refutation-only. Add `--json` for deliberation integrations.

### `daimon ruling list|show`

Read the standing rulings: human-ratified positive constraints on the same ledger, never decayed, never re-extracted. `show` includes pending agent proposals, and resolves across a ruling layer above this project too, so an inherited id shows the ruling and names its layer instead of reading unknown. `list` stays scoped to this project's own bucket unless `--inherited` is passed, which adds the active rulings inherited from a layer above it (each `--json` row carries `inherited_from`); refused under `DAIMON_TENANT_SCOPED`, the same as every other caller-chosen cross-project scope. A `list` that finds nothing exits 1 and names the bucket on stderr when the project has never been written from, and exits 0 when the project has a bucket that holds no rulings.

### `daimon ruling checks`

What this project has armed: one row per ruling that carries a check, crossed with every host, including a layer's active check inherited from above this project (labelled with the layer). Each row names the check's lifecycle, the intent its author asked for, the mode that host actually delivers, when it last ran, and its clean / violation / unresolved counts over the window the log still holds. A row that could not have run ends after its mode, because nothing is armed for a proposed or disarmed check and a host that cannot deliver one has no channel to run it through. Header lines report the manifest state, the firing log's own state, the timestamp the retained window starts at, and every host the hook has ever run on. Those host lines are labelled `(any project)`: the rows behind them carry no project, so hook liveness is a fact about this machine, never about this project. Read-only, and an empty answer exits 0. `--json` for machines.

### `daimon ruling check try <id> --command "<cmd>"`

Run this ruling's check against a command you name and print the outcome. Resolves an id inherited from a layer above this project too, and names the layer in that case. Arms nothing, logs nothing, and materializes the body outside the checks directory. `--cwd` sets the working directory relative file arguments resolve against; `--proposed` runs a pending agent revision's body instead of the armed one. Human path only, like `ratify`: it executes the body, so an interactive terminal is required and `--by agent` is refused. Same exit contract as the auditors below.

### `daimon serve`

Open the [read-only local viewer](../viewer.md) on localhost, search as recall, per-entry "why" pages, refutations, diff, check strip, print view. Nothing writes.

### `daimon relations list|show|confirm|reject|retract`

The [typed relation ledger](../relations.md): machines propose, only a person confirms, and deciding needs an interactive terminal. Candidates never render on an entry surface. `list` and `show` print each endpoint's text, or its withheld marker when a person quarantined it or the trust ledger cannot be read, and leave out an edge that touches a forgotten item without counting it.

## Correct

### `daimon resolve <id or text>`

Mark an item resolved with an append-only event; the item stops carrying. The event holds the id and the status, never the item's text. `--dry-run` previews the match; `--by agent --evidence "<quote>"` claims a close that is byte-checked at session end. A text query binds only when exactly one visible item matches and no withheld item also does. Otherwise it lists the visible candidates and one line counting the withheld ones (`N withheld item(s) also match; use the exact id`, and `daimon status --suppressed` lists them). A forgotten item is never a target. A quarantined item binds by exact id on a person's terminal only and is never echoed; an agent is refused (exit 2). While the trust ledger cannot be read every id is refused with the cure (exit 2), and an unreadable events ledger refuses the write before any lookup.

### `daimon trust propose|confirm|dismiss|release|republish|list|show|repair`

Quarantine a checkpoint value that is unsafe to act on; see [quarantine](../../concepts/trust-classes.md#quarantine). `propose --text "…" --kind <belief\|contradiction\|decision\|question\|topic\|uncertainty> --reason "…" --evidence <type:ref>` from an agent (`--by agent`) lands a candidate, from a person at a terminal it is active at once. `confirm` and `dismiss` decide a candidate, `release` lifts an active quarantine, and `republish` re-sends this project's quarantines to the team sidecar: each is human-only and refuses `--by agent`. `list` shows candidates first (`--team` lists what teammates published, `--json` for machines), `show <tr-id>` prints one record, and `repair` is the alias of `daimon ledger repair trust`.

### `daimon anchor <file> <symbol>`

Bind a cognitive item to a code symbol; briefings then warn when the anchored code drifts. `--attach <text>` matches visible items only (the topic included) and ignores withheld ones entirely, so a needle that matches a quarantined item names and counts nothing. The rewrite keeps every other item byte for byte.

### `daimon refute add|ratify|revise|overturn`

Manage scoped negative knowledge in its own append-only ledger. Agent writes remain candidates; only an explicit human ratification activates a guard, and `ratify` requires the human path, an interactive terminal with `--by` omitted. Revisions require a new typed evidence citation, whose shape is checked but never resolved or verified, and reset an active refutation to candidate until it is ratified again. Agent overturns remain proposals.

### `daimon ruling propose|ratify|revise|retire`

Manage standing rulings on the same ledger, with a stricter lifecycle: `ratify` shows the full text, discloses that it will render into every future session, and binds the activation to the text it displayed; a human revising an active ruling confirms the change and the ruling stays active; agent revise and retire calls record proposals while the text stands; activation refuses past the cap (`DAIMON_RULING_CAP`, default 7). Retirement needs no evidence citation. `propose` and `revise` may attach a check with `--check-body-file`, `--check-match` and `--check-intent`: a script whose bytes are stored on the ruling (a path is refused), a pattern on the command string that selects the actions it runs before, and what it asks each host for (`warn` by default). A candidate's check reads as proposed, not armed, and no host runs it. `ratify` prints the check and binds the activation to the body it showed, by hash, and materializes it for a host to run. A body that names a host-local path on any line is refused, because the check travels with the ruling and a path outside it is missing on every other machine. See [Checks at runtime](../check-runtime.md). `propose` and `revise` may also carry a `--request-policy` grant, in one of two shapes (repeatable, all four keys required, verb-branched, and mixing the two is refused): `--request-policy sender=<slug> kind=work verb=accept by=agent` lets that sender project's agent record `accept` on a `work` request this project owes it; `--request-policy to=<slug> kind=info verb=open by=agent` lets THIS project's own agent open a request to that recipient as `kind=info`, with no person touching it (no wildcard `to`). `ratify` pins the activation to the grant it showed the same way it pins a check, and `revise --no-request-policy` clears it.

### `daimon amend propose <item-id> --change progressed|blocked|changed --evidence "<quote>"`

Record an evidence-carrying state transition on an open briefed item: the loop stays open, its state moved. A forgotten item is never a target. A quarantined item, or any item while the trust ledger cannot be read, is refused on every channel (`<id> is withheld; a human decides`, exit 2). `--evidence` is a verbatim transcript quote, byte-checked against this session's transcript at session end, not resolved or verified beyond that. An agent's proposal stays invisible in the briefing until the byte-check passes, then renders flagged and unconfirmed with its own confirm/reject pair; only a human `ratify` renders it as settled. `ratify`/`reject` take one or more ids, all-or-nothing if any is unknown or in the wrong state. `list` shows every amendment for this project, candidates first.

## Forget

### `daimon forget <id or text>`

Remove one item's content from disk and index, leaving a hash-only tombstone. The deletion survives re-serialization of the original transcript. It first checks that the project's events ledger can be read and exits 2 without touching anything when it cannot, because the tombstone is what makes the deletion hold everywhere else. After the scrub it lists every surface it could not vouch for (a plaintext ledger with torn lines or unreadable bytes, a quarantine sidecar, a ledger it could not rewrite, or a failed team publish) and exits 4: scrubbed, with unreached surfaces. A ledger that holds no plaintext is only noted. Exit 1 stays the failures before anything was written. Candidates are checked like any other read. A text query binds one visible value and nothing else. A quarantined or closed candidate is one count line pointing at the exact id, a forgotten one is not listed, and a miss prints a pointer instead of the pools. An exact id on a quarantined item binds on a person's terminal only; while the trust ledger cannot be read an exact id still works on every channel, because the deletion promise outranks the outage. `--dry-run` prints `would forget <id>: <text>` for a visible target and the marker for a withheld one. Any surface it could not reach is listed as `unreached:` with its cure, and the run exits 4: a plaintext ledger, a quarantine sidecar, a cache, a log, the checkpoint copies, the team mirror, the recall index or a failed team publish. `daimon forget --resume` takes no target (giving one is a usage error, exit 2) and needs a terminal. It finishes every phase for every forget already recorded, writes the key row a forget made before this release lacks and prints one line with the count, and exits 4 for what it still cannot reach. `--only <phase>` runs one phase and `--dry-run` previews without writing. A checkpoint copy or team mirror copy that cannot be parsed keeps `forget` at exit 4 until the file is fixed by hand, and `--resume` reports it on each run. `daimon forget --republish` is its publish phase, `--resume --only publish`: it publishes every local forget the team sidecar is missing, once each, and exits 4 when that sidecar cannot be read or is over its size cap.

### `daimon unforget <id>`

Lift a forget on this machine, from a terminal only (it has no agent channel). It takes the exact id the forget receipt printed and lifts every ref that carries the key in one append. It restores nothing; a new capture of the claim is admitted again. Exit 0 only when nothing else withholds the value; otherwise exit 4 naming the class (another project on this machine, a teammate's published row, or a ledger it could not prove). The row you published stays, because a tombstone row has no retraction. `--reason` is recorded on the lift rows and `--dry-run` counts the refs without writing.

## Coordinate

A request lives in the sender's own project bucket; the recipient answers
with decision rows in its own bucket. The folded record is a read-time join , 
nobody ever writes into another project's ledger.

### `daimon request open --to <dir> --ask "…" --why "…" [--title "…"]`

Ask another project for something. `--to` takes the recipient's project **directory**, not its slug (a real slug starts with `-`, which argparse reads as an option, `--to=<slug>` also works). Validated against `daimon projects`, with near-match suggestions on a typo; `--anyway` records the ask against a project that has never serialized on this machine. `--blocking` and `--to-human` are flags on the record. `--title` is an optional headline of at most 80 characters (longer is refused, not cut) that leads wherever the request is listed; the ask stays the full text, and a request without a title shows the first sentence of its ask. `--kind work\|info` sets the approval requirement: `work` (the default) waits on a person, `info` is answerable from the recipient's own artifacts and owes no accept. Only a human channel may open one as `info`. Either channel otherwise. `--severity critical\|high\|low` with `--severity-reason "…"` (one line, 80 characters at most, refused past that and never cut; required with any level but the default) says how much the outcome matters to you, so the recipient's lists and panels sort by it. It is not `--blocking`, which says you are stalled. Leaving it out is `normal`, which shows nothing. Either channel.

### `daimon request revise <id> [--ask] [--why] [--evidence] [--title]`

Answer a needs-info, or sharpen an open ask. Revising keeps the title unless `--title` is given; `--title ""` removes it. Either channel; capped at 3 revisions per record lifetime, past the cap, open a new request with `--supersedes <id>` to keep the lineage visible.

### `daimon request withdraw <id> --reason "<why>"`

Take back a request this project sent. Either channel, because it is the sender's own act and not the recipient's decision. Moves an open, needs-info, accepted or claim-pending ask to the terminal `withdrawn` state, with `withdrawn_by`, `withdrawn_at` and the reason in `note`. On a done, rejected or already withdrawn request it changes nothing and says why. Final for that record; open a new request with `--supersedes <id>`. A recipient that had accepted the ask sees one line, `withdrawn by the sender: <reason>`, through live delivery. Only the sender can run it, and the sender's `reject`, `accept`, `needs-info`, `suppress` and `done` on its own ask are refused: each event belongs to one role, and a row from the wrong side is inert.

### `daimon request severity <id> <level> --reason "<why>"`

Change how much a request you sent matters to you (`critical`, `high`, `normal`, `low`; `normal` clears a raised level and takes no reason). Either channel. It is its own event, not a revision: it moves no state, uses none of the 3 revisions, discards no completion claim and sends no live nudge. The new level shows on the recipient's next brief, in `inbox` and in `daimon decide`. It works while the request is open, needs-info or accepted, and is refused on a done, rejected or withdrawn one, and when it would change nothing. Only the sender can run it.

### `daimon request accept|reject|needs-info <id> [--note]`

Land a decision. Human-only: requires an interactive terminal. An agent may run `accept --by agent` for an addressed ask (`info` or `work`) still open or needs-info only when an active standing ruling on this project (`verb=accept`) covers that specific sender and that kind, and the record then names the ruling. Without one, accept needs a person, for `info` as much as for `work`. `reject` is final for that record; the sender supersedes with a new request rather than asking again.

### `daimon request suppress <id> [--note]`

Drop a request out of the recipient's own briefing panel. Human-only; the record stays in `list`/`inbox`, and any later decision reverses it.

### `daimon request done <id> --evidence "<quote>"`

Report the ask as satisfied. Either channel; an agent's claim renders `done (claimed, unverified)` until the recipient's next session-end byte-checks the evidence quote against its transcript. A human `done` renders plainly. On a `work` request no person has accepted yet, an agent's `done` records the claim and leaves the request in the decision queue, awaiting `accept` or `reject`, instead of closing it.

### `daimon request reply <id> --note "<text>" [--evidence "<pointer>"]`

Send the sender a progress note on a request you accepted, without closing it. Either channel; an agent's note reaches the sender labeled `Reply (agent, unverified)`. It moves no state and works only on an accepted request; any other state is refused with the verb that fits (`accept --note`, `needs-info --note`, `done`). Run from the project that sent the request, it is refused and says so: only the recipient replies. The sender sees the latest reply in the briefing and the live nudge, even after the decision itself aged out of the panel.

### `daimon request list`

This project's own sent requests, undecided first. A recipient whose request ledger cannot be read is skipped and counted in one warning line (stderr with `--json`). Each row shows when it last moved and `OVERDUE` once nothing has moved it for `DAIMON_REQUEST_OVERDUE_DAYS` (default 7). `--json` for machines, and each row also carries `last_movement_at`, `age_seconds` and `overdue`. `--min-severity <level>` keeps that level and anything more severe; within a state, rows sort by severity before age.

### `daimon request inbox`

Requests addressed TO this project, from every sender, undecided first, including ones the briefing panel dropped for attention. A sender whose request ledger cannot be read is skipped and counted in one warning line (stderr with `--json`); the briefing panel and the MCP `requests_inbox` tool say the same. `--json` for machines. `--min-severity <level>` keeps that level and anything more severe; within a state, rows sort by severity before age.

### Requests: panels, stale, overdue and info asks

Four panels ride the same-project CLI `brief` only, never `--slug`, the
global-pointer fallback, or MCP. The recipient sees "Requests waiting on
you" and "Requests you accepted and still owe"; the sender sees "Decisions
on requests you sent" and "Requests you sent that nobody is moving". Each is capped at 3
cards with a loud `+N more …` overflow line naming the command that shows
the rest, never a silent drop. Suppression is recipient-side attention
only: the sender's panel still reads a suppressed request as "surfaced,
undecided". An unanswered request renders `stale` after 3 recipient
sessions pass with no decision; a decided one leaves the sender's panel
after 2 sender sessions. Attention decays, records never delete, and both
stay fully visible in `list`/`inbox`.

`stale` counts recipient sessions, so an ask to a project nobody opens never
reaches it. The sender has a second, wall-clock signal for that: a request
it still has open (open, needs-info, or accepted with no `done`) reads
`overdue` once nothing has moved it for `DAIMON_REQUEST_OVERDUE_DAYS` days.
Moving means opened, revised, accepted, answered with needs-info, replied to,
or claimed done; a brief that merely showed the card does not count. It is
derived when read, never written, and needs no session on the recipient's
side. The sender's brief lists the overdue asks in a panel headed "Requests
you sent that nobody is moving", capped at 3 with a counted overflow line,
and leaves out any ask the "Decisions on requests you sent" panel already
shows. Nothing runs on a timer: a script that wants to chase them can poll
`daimon request list --json` and act on `overdue`.

A request opened with `--kind info` owes no accept, so it never appears in
`daimon decide` or the recipient's "Requests waiting on you" panel above , 
there is nothing there for a person to decide. It still carries an `[info]`
marker on the recipient's "Requests you accepted and still owe" panel and
on the live-delivery line that injects it mid session. A `work` request,
the default, carries no marker on either.

Only a human channel may open a request as `kind=info`. An agent may do so only under an active `verb=open` ruling its own project ratified for that recipient; the record then reads `Kind: info (opened by agent under r-...)` on `list`, `show` and `inbox`, so a ruling-classified ask never looks like one a person opened.

`daimon status` adds a one-line summary, `requests: N open sent, M
awaiting you`, silent when both are zero.

The [MCP server](../mcp.md) exposes the recipient-side view as the read-only
`requests_inbox` tool. `daimon_brief` never carries request content, and no
request write verb is reachable over MCP.

## Status

### `daimon status`

Checkpoint presence and age, last serialize outcome, health warnings. One line reports how many checks this project has armed, counting an active check inherited from a layer above it too, how many are still proposed, and how long ago one last ran, plus a pointer to `daimon check sync` when the manifest has drifted. It appears only when this project has a check at all, and never moves the exit code. It also checks this project's ledger files without changing them: one line when all are fine, otherwise one line for each ledger with torn, split or unreadable lines, or with a value you forgot still in it (a count, never the value), and one line naming any `.jsonl` file daimon does not recognize. Each of those lines ends with what to do about it and where the file is: retry for a transient failure, check permissions for an OS error, or `daimon ledger repair <name>` for torn or garbage lines (`daimon trust repair` for the trust ledger). A further line names each project in scope whose events ledger cannot be read, because the forget set may then be missing one of its tombstones; a tenant-scoped home names none. A line for each project in scope counts the sessions refused because its events ledger cannot be read (`admission refused`), with the reason, what to run and where the file is. While every failed session is such a refusal, the silent-capture banner is not shown. When the forgotten-value check cannot run, it says so instead of reporting zero. `--json` and the MCP status tool carry the same result in a `ledgers` field. None of it moves the exit code. `--suppressed` lists what the briefing holds back: resolved loops with the event that closed them, and quarantined values by item id and record, never their text. A value you forgot is not listed at all. When the trust ledger cannot be read it says how many items are held back instead of reporting none, and when the list cannot be built it prints one error line and exits 2.

### `daimon stats`

Local usage and capture aggregates. Nothing is transmitted; sharing the output is a deliberate paste. Includes a receipt-probe line with lifetime totals (attempted, eligible, confirmed, contradicted, skipped, cured) when receipts are configured. Also one checks line in three wordings: nothing armed, armed but never fired, or totals (fired, clean, violation, unresolved, denied) aggregated across every host and over the window the capped firing log still holds, which the line names; the armed count includes an active check inherited from a layer above this project. Also a worldcheck section: per claim class, how many notes of this project were contradicted at least once out of the notes checked at least once, beside the per-check confirmed and contradicted counts of the same project. Both counts cover the same window, which opens at the first recorded outcome and which the section names; check lines written before the log named a project are counted apart as `unscoped`, for none; a class with no recorded outcome reads `none recorded`, never `0 of 0`. Also one tokens line in the store section: the tokens the captures of the last 14 days cost (total, prompt, completion), summed from each checkpoint's `capture_cost` stamp, beside how many of those captures reported nothing (the backend printed no usage), how many predate the stamp, and how many records were written with no model call at all (`daimon write-checkpoint`); a dollar figure follows only when at least one capture reported a cost and every reporting capture carried one, which today only the `claude-cli` backend prints. Also a quote roles line: whose words this project's fresh verified quotes are, by message role, read off the `binding.roles` list the receipt records at verification (user only, assistant only, tool involved, mixed, with counts and shares), beside the quotes that cannot answer: pre-field (a message-bound receipt written before the roles list existed) and unbound (a transcript-scan receipt with no roles list: written before the field, or captured with no per-message transcript). A quote bound to messages takes the roles of those messages; a quote verified by transcript scan takes the channels that can account for it, which is how tool output and the quotes of a `daimon serialize <file>` run (one user-role message, no message ids) get a role at all. A quote that both channels hold word for word reads as mixed on a scan receipt, since either could account for it. Carried copies are left out, so each session's words weigh once. Role is the message channel, never a person. `--json` for machines.

### `daimon log --text "…"`

Append a freeform timeline event to the project's event log, zero-LLM, audit-trail only.

### `daimon loops`

List open, addressable loop items with their ids and ages, a `stale` marker on carried items past the staleness budget. The read counterpart to `resolve`'s write path. `--stale` lists only those stale carried items, the set the briefing may drop.

### `daimon decide`

List what is waiting on YOU, each with the one command that closes it, the human-side mirror of `loops`. Reads records that already exist and writes nothing at all, so opening it never changes what the agent is shown. Scoped to this project; other projects arrive as counts. `--all-projects` adds every other project's queue as text, composed per project, each command routed with `--slug=<slug>` so it runs from here. The ten human-only verbs (`request accept`, `reject`, `needs-info`, `suppress`; `amend ratify`, `reject`; `ruling ratify`, `retire`; `refute ratify`, `overturn`) take that flag as routing only. Both are refused while `DAIMON_TENANT_SCOPED` is set. The briefing already carries this count on a single line, pointing back to this command.

### `daimon projects`

List every project daimon holds a checkpoint for, with topic teasers. A topic that is quarantined, forgotten or cannot be checked shows as `?` (`null` in `--json`), the same as a project with no topic. A project whose trust ledger cannot be read is counted in one warning line after the table (`--json` keeps stdout a JSON array and prints the warning on stderr; a tenant-scoped home says it without the count). When the list cannot be built it prints one error line and exits 2.

### `daimon slug <path>`

Print the checkpoint directory name daimon derives from a project path. No store, config, or ledger access, so it answers even for a path daimon has never written to.

### `daimon bucket migrate`

Move the bucket a pre-0.42.0 daimon wrote for this project into the one this daimon reads. Two path shapes are affected: a symlink anywhere in the path, and a subdirectory of a git repository written through the library rather than the CLI. Renames when the new bucket does not exist yet, merges line by line when it does. A pointer already in the new bucket is never removed or displaced; old pointers fill the free slots. Safe to run twice. Exit 1 means the move did not finish and names each thing left with its remedy; exit 2 refuses a `--project` containing `..`, and refuses `--project` at all on a tenant-scoped home. `--dry-run` prints the plan and writes nothing; `--json` prints the record it appends to `~/.daimon/checkpoints/migrations.jsonl`, or `{"refused": "..."}`.

### Linked worktrees

A session opened in a linked worktree uses the bucket of its main repository, so the history, rulings and requests of one project stay in one place. The main repository's own worktree list is the authority: daimon asks `git worktree list --porcelain -z` (so git 2.36 or newer; older git leaves the worktree with a bucket of its own) and binds only when that list names this exact folder. A `.git` file that points at some other repository's admin directory binds nothing. Ordinary repositories never run this command. It is skipped under SEALED, which keeps refusing a `.git` file (a host that needs a worktree there calls `declare`), and on a tenant-scoped home. Two limits are documented, not mitigated: a stale list entry left by moving a worktree (until `git worktree prune`) still names its old path, and a worktree deleted before a late `heal` runs falls back to a bucket of its own, which `bucket doctor` then lists. Because the bucket is shared, the main's latest checkpoint can come from a session on a worktree's branch. A worktree that already has a bucket from before this change keeps it until you fold it with `bucket merge`; its brief says so in one fixed line that names `daimon bucket doctor`.

### `daimon bucket merge --from=BUCKET --into=BUCKET`

Fold one bucket into another, once, on purpose: the repair for a split that already exists (a worktree bucket, a ghost bucket, a fresh bucket born before you rebound). Ledger lines the target lacks are appended. The newest session of `--from` becomes the latest of `--into` when it is newer, the way one more session in `--into` would, which can rotate the oldest pointer of `--into` out of its chain. A pointer that fits no slot is dropped and listed by session id; the flat signed session files keep their bytes. `--from` is then removed and an alias is recorded so readers follow it. Refused (exit 2) while the root recorded for `--from` still exists on disk or when it has none (nothing then proves its project is gone), unless `--force`; a live linked worktree whose sessions already use `--into` is not refused, because nothing can create `--from` again; also when `--into` does not exist, or when the two names are the same directory or a symlink. Exit 1 means something was left behind and names it. Dry run unless `--apply`. Bucket names usually start with `-`, so write `--from=NAME`. Human-only: refused on a tenant-scoped home. `--json` prints the record.

### `daimon bucket doctor [--path DIR]`

Read-only survey of every bucket: recorded roots that no longer exist, buckets with no root, two buckets with one root, empty and ghost buckets, worktree-looking roots, buckets left by an unfinished merge, identity records that disagree with the root. With `--path`, it also lists dangling buckets whose recorded origin URL or root commit match that folder, labelled evidence and not proof. Bucket names, candidates and merge suggestions are printed only to a terminal; anywhere else you get counts. When a bucket's folder is gone or none is recorded, it ends with a line pointing at [Move daimon to a new machine or folder](../../guides/move-to-a-new-machine.md). A bucket whose recorded root is a live linked worktree is offered a merge into the bucket its main repository uses; a root that only looks like a worktree (a `.claude/worktrees` folder no repository lists) gets no suggestion, because its live folder would create the folded bucket again. Refused on a tenant-scoped home and under SEALED. `--json` follows the same rule.

### `daimon bucket stamp [--scan DIR ...]`

Run it on the old machine before copying the store. Every stamped bucket gains an identity record (origin URL without credentials, root commit, folder name). An unstamped bucket gains its root only when exactly one repository under a `--scan` directory has that bucket's name; two matches refuse and write nothing. A recorded root is never rewritten and signed checkpoints are never touched. Dry run unless `--apply`. Exit 1 when a bucket needed a person (two matches, a lost race). Refused on a tenant-scoped home.

### `daimon bucket adopt --bucket=BUCKET --path=DIR`

Bind one bucket to the folder its project lives in now: the explicit answer when a project moved and `remap` does not cover it. The bucket keeps its name and every signed file; only its recorded root changes, and the old one is kept in its identity history. Refused (exit 2) when `--path` is not a directory, when it is inside a project (the folder a session opens is the git toplevel, and the error names it: adopt that one), when another bucket already records that folder (the fresh bucket born before you rebound: fold it with `bucket merge` first, or the folder would answer for two buckets), when the bucket's recorded root still exists on disk (a clone that keeps its own history) unless `--force`, when the folder's own bucket has no recorded root and holds history that the rebind would leave behind (fold it with `bucket merge` first, or `--force`), when its identity record cannot be read, and when the store or bucket cannot be locked. Two adopts onto one folder cannot both win: the collision is checked again under a store lock. Adopting what is already adopted is a no-op. Refused under SEALED, like on a tenant-scoped home. Dry run unless `--apply`. A terminal also shows the root the bucket left and a comparison of its recorded anchors with the folder (evidence, not proof); anywhere else those are left out. Refused on a tenant-scoped home. `--json` prints the record.

### `daimon bucket remap --from=DIR --to=DIR`

After a folder or username change, rewrite the recorded root of every bucket under `--from` to the same place under `--to`. Matching stops at a separator, so `/Users/a` never matches `/Users/abc`; a trailing separator on either side is dropped; paths between systems work (`C:\Users\a` to `/Users/b`) because separators in the tail follow `--to`. A bucket whose new folder does not exist yet (a project you have not cloned on this machine) is skipped and listed, which is not an error. A bucket whose new folder already belongs to another bucket is skipped and listed with the `bucket merge` that folds the two (it carries `--force`, because that folder exists and `merge` otherwise refuses to remove a bucket whose folder is live; run `remap` again after it), and so is one whose new folder is not the folder a session opens (`not-a-root`, for example a subdirectory of a repository) and one whose old root still exists (`live-source`, a clone kept: `--force` moves it anyway). `--from` may be given through a symlink. Refused (exit 2) for `--from /`, a bare drive, an empty value, a relative `--to`, and a `--to` inside `--from`. Each bucket is rewritten under its own lock and re-read first; one that is locked or changed meanwhile is listed and the run exits 1, as it does for a collision. Safe to run twice. Buckets with no recorded root are out of reach; `bucket doctor` lists them. Dry run unless `--apply`. Bucket names and roots print only to a terminal, counts otherwise. Refused on a tenant-scoped home. `--json` follows the same rule.

### `daimon ledger repair <name>`

Heal one bucket ledger (`events`, `trust`, `refutations`, `amendments`, `requests`, ...) that `daimon status` reports as degraded or unreadable. Rejoins rows split across lines, moves every torn or unparseable line into `<name>.quarantined-lines` next to the ledger (one JSON row per line, undecodable bytes kept as `\xNN` escapes), then re-runs forget for every forgotten key of the project, which reaches values a forget missed while their row was split. Safe to run twice; an already-clean ledger prints `nothing to repair`. It prints how many rows the sidecar now holds, because a value fused into a torn row needs a manual review of that file, and after repairing `events` it says how many refused sessions wait for `daimon heal`, which takes one per run. A forget later purges matching rows from the sidecar too, and unrelated fragments stay there. Exit 1 when the ledger is transient or cannot be rewritten (nothing is changed), exit 2 for an unknown name. `--dry-run` reports the same counts and writes nothing. `daimon trust repair` is the same verb for `trust`.

### `daimon team init|sync|status`

Shared team memory via a sidecar repo, default-closed routing, shape-redacted before anything syncs.

### The slug rule

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

The rule is a character transform applied AFTER resolution. `daimon slug` prints the transform of the literal string you hand it, which is why it answers for a path daimon has never written to. To see the root and the slug a path actually routes to, with the symlink and git-toplevel steps already applied, run `daimon status --project <path> --json` and read `identity`.

## Internals

### `daimon serialize <transcript>`

Turn a transcript file into a checkpoint, the SessionEnd hooks call this; running it by hand backfills one.

### `daimon write-checkpoint`

Store a checkpoint supplied as JSON on stdin, the in-session introspection path. Trust is code-clamped: nothing on this path can claim `verbatim`, because there is no transcript to verify against.

### `daimon recall-inject`

The per-prompt suggestion backend behind the recall hook: prompt on stdin, zero to two prior-work lines out, exit 0 always.

### `daimon action-recall`

The pre-shell-action recall backend: a shell command on stdin, zero or one prior-work line out, exit 0 always. A separate process from the pre-action check that can deny a command, so a fault here never drops a deny.

### `daimon request-inject`

The per-prompt live-delivery backend: prints undecided cross-project asks this session has not already been shown, exit 0 always.
