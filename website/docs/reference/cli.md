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
| `daimon hooks install <host>` | Ship the host hook scripts (Windsurf, Codex) from the package. `list` / `status` inspect; `status` also audits this project's check manifest against its ledger, and exits non-zero on either kind of drift. |
| `daimon skill install <host>` | Install the daimon agent skill into a host's skill directory. Re-run after upgrades. |
| `daimon check sync` | Rebuild `~/.daimon/checks` — the manifest of armed checks a host hook reads, and one file per check body — from this project's ledger. Every ratify, revise, retire and forget already does this, so run it only when the manifest was damaged out of band. Safe to repeat: an unchanged manifest is not rewritten. Prints how many checks are armed, zero included. `--check` audits instead of rebuilding and writes nothing: exit 0 in step, 1 drifted, 3 when the manifest exists and cannot be read. `--json` for machines. |
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
| `daimon audit quotes` | Re-check every stored verbatim quote against its source transcript and report mismatches. Read-only — it never rewrites trust tags. Exit 0 checked clean, 1 mismatch, 3 nothing checkable. It always reports how many items it had to skip, and when that is all of them it says so instead of printing a rate over nothing. `--json` for machines. |
| `daimon audit privacy` | Prove the deletion contract: hash every plaintext field on every surface (checkpoints, rotated pointers, the event ledger, the team mirror, the recall index and its orphan snapshots) and report any forgotten value that survived. Read-only. |
| `daimon refute list\|show\|search\|guard` | Read the negative-knowledge ledger without decay. `guard` emits active exact-anchor/subject matches only; it is advisory and never blocks a command. `search` returns both polarities, labelled; `list` and `guard` stay refutation-only. Add `--json` for deliberation integrations. |
| `daimon ruling list\|show` | Read the standing rulings: human-ratified positive constraints on the same ledger, never decayed, never re-extracted. `show` includes pending agent proposals. A `list` that finds nothing exits 1 and names the bucket on stderr when the project has never been written from, and exits 0 when the project has a bucket that holds no rulings. |
| `daimon ruling checks` | What this project has armed: one row per ruling that carries a check, crossed with every host. Each row names the check's lifecycle, the intent its author asked for, the mode that host actually delivers, when it last ran, and its clean / violation / unresolved counts over the window the log still holds. A row that could not have run ends after its mode, because nothing is armed for a proposed or disarmed check and a host that cannot deliver one has no channel to run it through. Header lines report the manifest state, the firing log's own state, the timestamp the retained window starts at, and every host the hook has ever run on. Those host lines are labelled `(any project)`: the rows behind them carry no project, so hook liveness is a fact about this machine, never about this project. Read-only, and an empty answer exits 0. `--json` for machines. |
| `daimon ruling check try <id> --command "<cmd>"` | Run this ruling's check against a command you name and print the outcome. Arms nothing, logs nothing, and materializes the body outside the checks directory. `--cwd` sets the working directory relative file arguments resolve against; `--proposed` runs a pending agent revision's body instead of the armed one. Human path only, like `ratify`: it executes the body, so an interactive terminal is required and `--by agent` is refused. Same exit contract as the auditors below. |
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
| `daimon ruling propose\|ratify\|revise\|retire` | Manage standing rulings on the same ledger, with a stricter lifecycle: `ratify` shows the full text, discloses that it will render into every future session, and binds the activation to the text it displayed; a human revising an active ruling confirms the change and the ruling stays active; agent revise and retire calls record proposals while the text stands; activation refuses past the cap (`DAIMON_RULING_CAP`, default 7). Retirement needs no evidence citation. `propose` and `revise` may attach a check with `--check-body-file`, `--check-match` and `--check-intent`: a script whose bytes are stored on the ruling (a path is refused), a pattern on the command string that selects the actions it runs before, and what it asks each host for (`warn` by default). A candidate's check reads as proposed, not armed, and no host runs it. `ratify` prints the check and binds the activation to the body it showed, by hash, and materializes it for a host to run. A body that names a host-local path on any line is refused, because the check travels with the ruling and a path outside it is missing on every other machine. See [Checks at runtime](#checks-at-runtime). |

### Checks at runtime

Ratifying a ruling that carries a check writes two things under `~/.daimon/checks`: a manifest naming every armed check and the project directory it belongs to, and one file per check holding the exact body the ruling stores. Every ledger write that can arm or disarm a check rebuilds them; `daimon check sync` rebuilds them on demand, and so does `daimon hooks install`, which prints what it found.

On a host with a pre-action hook installed, a matching shell command runs its armed checks before it executes. The hook reads the manifest, keeps the checks whose project directory contains the action's working directory, and runs the ones whose pattern matches the command string. It always exits 0 and writes either one JSON object or nothing: the deny is a decision the hook makes deliberately, never an exit code that a crash could produce by accident.

### What a check gets on each host

What the author asked for is an intent. What a host delivers is a mode, and it is the weaker of the two.

| intent | Claude Code | Codex | Windsurf |
| --- | --- | --- | --- |
| `enforce` | `enforce` | `enforce` | `unsupported` |
| `warn` | `warn` | `record-only` | `unsupported` |
| `record-only` | `record-only` | `record-only` | `unsupported` |

`enforce` returns the host's structured deny and the command does not run. `warn` allows the command and shows the reason. `record-only` says nothing to the host and leaves the run in the log. `unsupported` is what a host gets when daimon has not measured how it delivers a decision at all: Cascade documents a `pre_run_command` event, but nothing measured says what it does with one, so the whole Windsurf column stays `unsupported` rather than claiming an enforcement nobody has seen delivered.

Codex is the interesting cell. It documents no channel for a warning, so `warn` degrades to `record-only` there: the check still runs and the log still says so, and daimon does not claim to have shown the author something the host never rendered.

When more than one armed check matches a command, the strongest FAILING mode decides. An `enforce` check that passed does not block the command for a `warn` check that did not, and the message names every check that failed at that mode or above. A weaker check's reason stays in the log: `record-only` asked for the log and nothing else, and a neighbour that failed harder does not carry it out on its behalf.

A check runs against a **subject**: the command string, a separator, then the contents of every file argument daimon could resolve, each under a header naming the flag it came from. The subject goes to a temporary file at mode 600 and is removed after the run. Its path arrives in `DAIMON_CHECK_SUBJECT`, alongside `DAIMON_CHECK_COMMAND` and `DAIMON_CHECK_RULING`; the working directory is the action's own, and standard input is `/dev/null`.

What the resolver reads:

| form in the command | what daimon does |
| --- | --- |
| `--body-file <path>`, `--body-file=<path>`, `-F <path>`, `-F<path>` | reads the file |
| `--notes-file <path>`, `--notes-file=<path>` | reads the file |
| `-F key=@<path>`, `--field key=@<path>`, `-Fkey=@<path>` | reads the file |
| `<flag> -` with exactly one heredoc in the SAME command | reads the heredoc text |
| `<flag> -` fed by a pipe | unresolved, cause `stdin-pipe` |
| `<flag> -` whose own command carries no heredoc | unresolved, cause `arg-form-unparsed` |
| a command with more than one heredoc or more than one `<flag> -` | unresolved, cause `arg-form-unparsed` |
| any other `@<path>` or `<flag> -` | unresolved, cause `arg-form-unparsed` |

A value attached to a short flag is the same command as a detached one, so `-Fbody.md` is read exactly as `-F body.md` is.

A heredoc belongs to the command it is attached to, and to no other. daimon splits the command string at `&&`, `||`, `;`, `|` and newlines, and a heredoc in one of those pieces can only be read for an argument in that same piece. So `cat <<EOF > note.txt ... EOF` followed by `gh pr create -F -` is unresolved rather than checked against the text `cat` was given. Inside one command the counts still have to be one and one: which heredoc feeds which argument is not a question the command string answers, and a wrong guess would build the subject from text the action never sends.

A command over 64 KiB once its heredoc bodies are set aside is `arg-form-unparsed`: tokenizing one enormous inline argument costs more than the whole hook budget, and a resolver overtaken by the host's timeout lets the action through with no record at all. A long heredoc body does not count toward that, so a large PR body still resolves.

Relative paths resolve against the working directory. A file over 1 MiB is `file-oversize`, one that is not UTF-8 text is `file-binary`, and a missing or unreadable one is `file-missing` or `file-unreadable`. One argument daimon cannot read makes the whole subject unresolved, whatever the others say.

Every run ends in exactly one of three outcomes, and they never fold together:

| outcome | what it means |
| --- | --- |
| `clean` | the check ran on the full subject and exited 0 |
| `violation` | the check ran on the full subject and exited 1 — its own first stderr line is the reason |
| `unresolved` | daimon could not prove the subject clean: an argument it could not read, a check that crashed or exceeded its budget, a body that no longer hashes to what the ruling was ratified with, or no `sh` on the host |

`unresolved` is never rendered as clean and never counted as a violation.

The body runs with a minimal environment: `PATH`, `HOME`, `LANG`, the `LC_*` variables and `TMPDIR`, plus the three above. It is not a sandbox — a check you ratified runs as you, and could do anything you could. Trimming the environment only keeps a script whose job is reading one file from being handed every token in the session.

The runner carries its own budget, `DAIMON_CHECK_TIMEOUT`, five seconds by default against a host hook timeout of ten. That budget is not optional: on the hosts measured so far, a hook that reaches the host's own timeout does not block and the action proceeds, so a check without a budget of its own turns a hang into a silent allow. Overrunning kills the check and its whole process group and reports `unresolved`.

Each run appends one row to `~/.daimon/logs/checks.jsonl`: ids, outcomes, causes, durations, host and mode. No command text, no paths, no subject. The reason shown to the agent may name a path; the log does not.

The file is capped at 256 KiB. Past that the writer keeps the last 64 KiB and drops the older rows, in place, so a hook holding the file open keeps writing to the same one. The counts every surface below reports are counts over what is left, and each of them names the timestamp that window starts at.

Read that log for liveness, not for compliance. A row proves the check RAN. Only `decision_emitted: deny` under `enforce` closes the gap between a check that ran and a check that was honored.

Three surfaces read it for you. `daimon ruling checks` folds it per ruling and host, `daimon stats` reports one line across every host, and `daimon ruling show` adds a `Fired:` line to a single ruling. All three report a timestamp and counts over the retained window, never a last outcome: an append-only log is ordered by append, so the last row is not the current state. An armed check with no rows reads as `never fired`, which is a different answer from zero violations. A log daimon cannot open is a third answer again: every surface says the log is unreadable rather than claiming nothing ran.

Two conventions worth keeping. Arm a new check with intent `warn` first, so a pattern that matches more than you meant costs a warning rather than a blocked action. And run `daimon ruling check try` before you ratify — that is what it is for.

| variable | default | what it holds |
| --- | --- | --- |
| `DAIMON_CHECKS_DIR` | `~/.daimon/checks` | the manifest and the materialized bodies |
| `DAIMON_CHECK_TIMEOUT` | `5` | the runner's budget in seconds, floored at 0.5 |
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | holds `checks.jsonl` |

### Rulings from a host process

The CLI mints two channels only: `cli-tty` (an interactive terminal) and `cli-agent` (`--by agent`). It never grows a flag for the other two human channels, `ui` and `signed`, because a flag an agent could pass from a shell would be a self-declared human channel. Those two exist for a host process that holds the authority itself, an operator it verified out of band, and they are written through the library, in process:

- `daimon_briefing.refutations.ratify(refutation_id, channel="signed", note="...", project_dir=...)` activates a proposed ruling. `channel` is the channel the host observed, one of `ui` or `signed`; any other name is refused. Cite the proof of the operator's action in the ruling's evidence (`url:`, or `receipt:` once the signature exists). The optional `check_sha256=` pins the check body the operator saw, the way the rule-text key does; a ruling with no check activates with it empty. A ruling that carries a check activates only through a ratify call that passes the pin of the check it displayed; an in-process revise from a human channel that supplies a new check arms it as given, so the host owns that confirmation.
- `daimon_briefing.refutations.listing(states={"active"}, polarity="ruling", project_dir=...)` and `daimon_briefing.briefing.active_rulings(project_dir)` read the active set, each row with `subject`, `scope`, `anchors`, `activation_channel`, `evidence`, and the rule text itself. `anchors` are free strings set with `ruling propose --anchor`; a host that enforces per message matches on them and decides what happens itself.

The record renders as `ratified (signed)` or `ratified (ui)`, never as human-ratified without the tier. Nothing local is unforgeable: a caller with machine access can drive a UI or allocate a terminal. What the channel earns is provenance, not proof; forgery costs deliberate impersonation instead of one word, and the channel stays auditable afterwards.

In the two calls above, and in the refutation, request, amendment and relation ledger helpers behind them, `project_dir` is resolved the same way the CLI resolves `--project`: made absolute, symlinks collapsed, then normalized to the git toplevel. A host standing in a subdirectory of a repository therefore reads the same bucket the CLI reads from the repository root. `daimon_briefing.config.resolve_project_dir(path)` is the public function that returns the directory a path routes to. The checkpoint store resolves the same way, at every public entry point that takes a `project_dir`, so a checkpoint and a ruling written from the same directory in the same process land in one bucket. `daimon_briefing.store.project_bucket(path)` returns the bucket name the store will use, for a host that wants to check before it writes. The one function that stays literal is `store.project_slug`, the character transform `daimon slug` prints, which has to answer for a path daimon has never seen.

#### What a host can rely on

daimon is pre-1.0. A release that breaks something described here arrives as a **minor** version bump, not a major one, so the version number alone will not warn you. Read that sentence twice if you are used to the usual meaning of a minor release.

Stability is not the promise we can keep at this stage. Visibility is. Any change to the calls above, to the accepted channel names, to the rendered activation strings, or to the row fields listed below ships as a breaking commit, which means it appears under `### ⚠ BREAKING CHANGES` in `CHANGELOG.md` with prose saying what moved and what to do about it.

So: pin an exact version, and read that section of `CHANGELOG.md` before you move to a new one.

The row fields a host reads are the record id, its state, subject, scope, anchors, the rendered activation and the channel it arrived through, the evidence list, and the rule text. Both readers above return the same set.

Deliberately not covered: the order rows come back in, the wording of diagnostics and log lines, the message text of a raised error (match on the exception type instead), and anything not named on this page. Importable is not the same as documented.

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
| `daimon status` | Checkpoint presence and age, last serialize outcome, health warnings. One line reports how many checks this project has armed, how many are still proposed, and how long ago one last ran, plus a pointer to `daimon check sync` when the manifest has drifted. It appears only when this project has a check at all, and never moves the exit code. `--suppressed` lists withheld resolved items. |
| `daimon stats` | Local usage and capture aggregates — nothing is transmitted; sharing the output is a deliberate paste. Includes a receipt-probe line with lifetime totals (attempted, eligible, confirmed, contradicted, skipped, cured) when receipts are configured. Also one checks line in three wordings: nothing armed, armed but never fired, or totals (fired, clean, violation, unresolved, denied) aggregated across every host and over the window the capped firing log still holds, which the line names. `--json` for machines. |
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

The rule is a character transform applied AFTER resolution. `daimon slug` prints the transform of the literal string you hand it, which is why it answers for a path daimon has never written to. To see the root and the slug a path actually routes to, with the symlink and git-toplevel steps already applied, run `daimon status --project <path> --json` and read `identity`.

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
