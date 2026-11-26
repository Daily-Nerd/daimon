---
sidebar_position: 7
description: "What a host process can call: rulings from a host, the stability promise, the sealed git surface and the briefing readers."
---

# Python API

A host process that embeds daimon calls the library directly. This page lists the calls it can rely on.

## Rulings from a host process

The CLI mints two channels only: `cli-tty` (an interactive terminal) and `cli-agent` (`--by agent`). It never grows a flag for the other two human channels, `ui` and `signed`, because a flag an agent could pass from a shell would be a self-declared human channel. Those two exist for a host process that holds the authority itself, an operator it verified out of band, and they are written through the library, in process:

- `daimon_briefing.refutations.ratify(refutation_id, channel="signed", note="...", project_dir=...)` activates a proposed ruling. `channel` is the channel the host observed, one of `ui` or `signed`; any other name is refused. Cite the proof of the operator's action in the ruling's evidence (`url:`, or `receipt:` once the signature exists). The optional `check_sha256=` pins the check body the operator saw, the way the rule-text key does; a ruling with no check activates with it empty. A ruling that carries a check activates only through a ratify call that passes the pin of the check it displayed; an in-process revise from a human channel that supplies a new check arms it as given, so the host owns that confirmation.
- `daimon_briefing.refutations.listing(states={"active"}, polarity="ruling", project_dir=...)` and `daimon_briefing.briefing.active_rulings(project_dir)` read the active set, each row with `subject`, `scope`, `anchors`, `activation_channel`, `evidence`, and the rule text itself. `anchors` are free strings set with `ruling propose --anchor`; a host that enforces per message matches on them and decides what happens itself.
- These two readers now differ. `listing` stays scoped to this project's own bucket, and only its rows feed the check manifest a host hook reads, so a check never arms twice under the same project. `active_rulings` is the merged view a briefing renders: this project's own rows plus every active ruling from a layer above it, layers first, own wins on a shared id, an inherited request-for-a-policy row dropped rather than rendered, and each layer row tagged `inherited_from` with the absolute directory it came from. `daimon ruling list --inherited` is the CLI form of that merged read: without the flag it stays own-bucket only, and with it the layer rows are appended after this project's own, each one carrying `inherited_from` in `--json` too; a project's own row never carries that key at all.
- `daimon_briefing.briefing.rulings_read(project_dir)` is the sibling a host reads when an empty answer is not enough: `active_rulings` always returns a plain list and folds every failure into an empty one, so a project that was never identified, a mis-resolved path, an unreadable ledger, and a project with genuinely no rulings all read exactly the same. `rulings_read` never raises and always returns one of four `state` values alongside the same `rows` and the `path` it resolved to: `"unresolved"` (the ledger path itself could not be worked out at all, a config problem, `path` is None here and only here), `"no-bucket"` (the path resolved, but nothing has ever been written from this project), `"unreadable"` (the bucket exists but the ledger could not be read), or `"read"` (a normal read, including a clean empty bucket). `active_rulings` is built on top of it, never raises either, and cannot drift from it.

The record renders as `ratified (signed)` or `ratified (ui)`, never as human-ratified without the tier. Nothing local is unforgeable: a caller with machine access can drive a UI or allocate a terminal. What the channel earns is provenance, not proof; forgery costs deliberate impersonation instead of one word, and the channel stays auditable afterwards.

In the two calls above, and in the refutation, request, amendment and relation ledger helpers behind them, `project_dir` is resolved the same way the CLI resolves `--project`: made absolute, symlinks collapsed, then normalized to the git toplevel. A host standing in a subdirectory of a repository therefore reads the same bucket the CLI reads from the repository root. `daimon_briefing.config.resolve_project_dir(path)` is the public function that returns the directory a path routes to. The checkpoint store resolves the same way, at every public entry point that takes a `project_dir`, so a checkpoint and a ruling written from the same directory in the same process land in one bucket. `daimon_briefing.store.project_bucket(path)` returns the bucket name the store will use, for a host that wants to check before it writes. The one function that stays literal is `store.project_slug`, the character transform `daimon slug` prints, which has to answer for a path daimon has never seen.

### What a host can rely on

daimon is pre-1.0. A release that breaks something described here arrives as a **minor** version bump, not a major one, so the version number alone will not warn you. Read that sentence twice if you are used to the usual meaning of a minor release.

Stability is not the promise we can keep at this stage. Visibility is. Any change to the calls above, to the accepted channel names, to the rendered activation strings, or to the row fields listed below ships as a breaking commit, which means it appears under `### ⚠ BREAKING CHANGES` in `CHANGELOG.md` with prose saying what moved and what to do about it.

So: pin an exact version, and read that section of `CHANGELOG.md` before you move to a new one.

The row fields a host reads are the record id, its state, subject, scope, anchors, the rendered activation and the channel it arrived through, the evidence list, and the rule text. Both readers above return the same set.

Deliberately not covered: the order rows come back in, the wording of diagnostics and log lines, the message text of a raised error (match on the exception type instead), and anything not named on this page. Importable is not the same as documented.

## Reading a briefing

`api.render_briefing(project_dir, now=None, loops_pointer=False)` returns the text `daimon_brief` returns over MCP: the baton, a format-version warning, the standing rulings, the decision count, the four request panels, the body and the withheld trailer, from the same composer `daimon brief` uses. It returns a `RenderedBriefing(text, state, notes, ..., printed)` and never raises; a failure is the `unavailable` state. The panels read the requested project's own request ledgers.

Reading is not free of bookkeeping here. A host API has no later seam, so the return is the delivery boundary: `render_briefing` marks the request and decision cards its text printed whole as seen by their senders (a `surfaced` row in this project's own ledger), and records the world-evidence rows it ran when `DAIMON_WORLDCHECK` is on. A card the byte budget collapsed to a count is not marked. `api.preview_briefing` takes the same arguments, returns the same text and `printed` cards and writes nothing, for a caller that reads a briefing without handing it to an agent. `api.briefing_text` is the text of `render_briefing`, so it marks cards too.

`api.parse_briefing(text)` reads a briefing back (`api.BRIEFING_FORMAT` is its version, 4). Besides `header`, `standing_rulings`, `items`, `warnings` and `handoff`, it returns the request furniture, each field `None` when the text has no such section:

| Field | What it holds |
|---|---|
| `requests`, `verdicts`, `owed`, `overdue` | A `Panel` each: `cards` (a `PanelCard` with the `request_id`, the card `line` and its indented `detail` lines), `more` (the `(+N more …)` count), `cut` (cards a collapsed panel lost to the budget) and `lines` (every line of the block). |
| `decisions` | A `DecisionCount(here, elsewhere)`. |
| `withheld` | A `Withheld(resolved, quarantined)`. |

A panel is read as a block from its title to the next blank line, by its title and never by a mark, so a baton arrow and a request card arrow cannot be mixed up, and text a peer wrote inside a card stays in that card. The format-version warning is one of `warnings`. The bytes of `render_briefing` are stable under the conditions its docstring lists, which now include the requests ledgers of the project and of the projects it exchanges requests with, and the `now` that sets the overdue ages.

## Embedding hosts: the sealed git surface

A program that embeds daimon and works in directories written by a less trusted
party selects the sealed posture (`DAIMON_GIT_POSTURE=sealed`, or
`hostgit.configure(posture="sealed", ceiling=...)` before the first project
read) and then does all of its git through `daimon_briefing.hostgit`. These
calls exist only under that posture, except `project`, which answers in both.

| Call | What it does |
|---|---|
| `project(path)` | The checkout `path` belongs to, or a refusal code. Runs the shape check and the config allowlist gate before it returns a checkout. |
| `run(checkout, *args)` | One git call on a checkout `project` returned, from a fixed table of subcommands (`diff`, `show`, `log`, `diff-tree`, `diff-index`, `diff-files`, `status`, `init`, `rev-parse`, `ls-files`, `ls-tree`, `cat-file`, `symbolic-ref`, `for-each-ref`, `rev-list`, `add`). The gate runs again on every call. A refused checkout answers with exit code 125, a hang with 124. |
| `init(path)` | Creates a repository with an empty template directory and returns `project(path)`. |
| `fetch_local(lead, member, branch, member_name)` | Fetches the member clone's branch into the lead as `refs/members/<member_name>/<branch>`, local transport only, no tags, no submodules. Both repositories pass the gate. |
| `declare(root, slug=..., origin_url=...)` | States a project's identity once per process. Also exposed as `api.declare_project`. It is data: the root still passes the gate. |

Refusal codes: `not-a-dir`, `outside-ceiling`, `no-git-below-ceiling`,
`gitfile`, `symlink`, `empty-gitdir`, `commondir`, `unreadable`,
`worktree-config`, `alternates`, `config-unreadable`, `config-key:<key>` (a
config key outside the allowlist), `checkout-changed`, `init-failed`.

The allowlist includes `branch.<name>.vscode-merge-base`, which VS Code uses
as branch-selection metadata. Other editor keys require their own review.

A sealed checkout refusal appears in the project's briefing with its reason,
including when the project has no checkpoint. The diagnostic belongs to the
current project and response. It also appears in `daimon status`. Git-backed
world checks keep the `git-refused` skip reason when the checkout is refused.

| Host or surface | Refusal diagnostic delivery |
|---|---|
| Claude Code, Codex, Gemini CLI | Session-start briefing through the shared CLI. |
| Kimi Code | First-prompt briefing through the shared CLI. |
| Hermes | Shared in-process briefing preparation and rendering. |
| Python API, MCP | Shared machine briefing. |
| Windsurf | CLI or MCP briefing. Automatic briefing injection by the capture hook is unsupported. |

To keep git calls in one place, call the census over your own tree from a test:

```python
from daimon_briefing import testing

testing.assert_single_git_owner(host_module_paths, allowlist)
```

`testing.git_spawn_sites(paths)` returns the sites it found, as
`{(module, function, primitive): line numbers}`.
