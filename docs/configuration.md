# Configuration

Daimon is configured entirely through environment variables. Every variable
resolves in the same order: the **process environment wins**, and anything not
set there falls back to the env file at `~/.daimon/env`. Override the file's
location with `DAIMON_ENV_FILE`.

The env file exists because hooks run in whatever environment the host process
happened to inherit — a GUI-launched agent has no shell profile, so shell
exports are not a reliable channel. Its format is `KEY=VALUE` lines; a leading
`export `, surrounding quotes, blank lines, and `#` comments are tolerated.
Keep it `chmod 600` — it can hold API keys.

`daimon configure` manages the LLM backend knobs (see [LLM backend](#llm-backend))
and writes them to `~/.daimon/env`. Everything else you set by editing that file
or exporting the variable.

**Boolean variables** accept `1`, `true`, `yes`, or `on` as truthy (matched
case-insensitively where noted). A handful use different conventions — kill
switches that are on unless set to `0`, or presence-based flags — and those are
called out in the "What it does" column.

## Core

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_DISABLE` | off | Kill switch. When truthy, every hook becomes a no-op — no capture, no briefing. |
| `DAIMON_ENV_FILE` | `~/.daimon/env` | Path to the env file that backs every other variable. Read from the process env only (it names the file, so it can't live inside it). |
| `DAIMON_PROJECT_DIR` | unset | Working directory of the session being briefed or serialized, used to route per-project checkpoints. Hooks pass the host's cwd through it; unset means the project is unknown and daimon falls back to the global pointer. |
| `DAIMON_SESSION_SPEAKER` | unset | The one person this session belongs to, declared by the host. Fills an item's `stated_by` when the transcript carries no per-message `said_by`, and only for items bound entirely to user messages; anything citing an assistant or tool message stays unattributed. A pure label, never a key, so it does not touch `DAIMON_AUTHOR`'s namespacing. Set it only where one session is one person for its whole life. A host that serves several people through a single session must leave it unset. |
| `DAIMON_SPEAKER_LINE` | unset | The delimiter a host writes around its own speaker line at the head of each user message, for a host that serves several people through one session but does not author the transcript. Literal character(s) or the `U+E000` spelling. When set, a user row that starts with `<delim>from=<id> name="<name>"...<delim>` gets `said_by` as `name (id)` and the line is cut from the content before anything reads it; only position zero counts, and only user rows. The value is host-declared, the same honesty as a row's own `said_by`. The host must strip the delimiter from user text before prepending its line, or a user can forge one. Unset means content is never read for attribution. |
| `DAIMON_MIN_MESSAGES` | `10` | Minimum message count before a session is worth serializing. Shorter sessions are skipped. |
| `DAIMON_TIMEOUT` | `420` | Total serialize budget in seconds, shared across retry attempts (per-attempt socket timeouts are capped to the remaining budget). Real serialize/merge calls on gateway and CLI backends run 74s–25min; keep ≥420 or slow calls and retries cannot fit. |
| `DAIMON_HUNG_AFTER` | `1800` | Seconds past which a serialize spawn that produced no result line is treated as hung/killed rather than still running. Default 30 min sits safely beyond a slow run (production serializes take 4–25 min). |

## Checkpoint store & GC

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_CHECKPOINT_DIR` | `~/.daimon/checkpoints` | Root of the per-session checkpoint store. |
| `DAIMON_CHECKPOINT_KEEP` | `100` | How many per-session checkpoint files to retain (newest-N). Older files are garbage-collected after a successful write. `0` disables GC entirely (keep forever). |
| `DAIMON_CHECKPOINT_HISTORY` | `3` | How many checkpoint pointers to retain per directory (`latest.json` plus `prev-1` … `prev-(N-1)`), so a failed serialize can fall back to a prior pointer. Minimum 1 (latest only). |
| `DAIMON_GC_PIN_IMPORTANCE` | `9` | Item-importance threshold that pins a checkpoint file against GC: a file whose max item importance reaches this survives outside the newest-N window. `0` disables pinning (pure recency window); values above 10 are clamped to 10. |

## Carry

Deterministic cross-session carry-over of unresolved items.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_CARRY` | on | Master switch for carry. On unless set to exactly `0` (any other value keeps it on). |
| `DAIMON_CARRY_FLOOR` | `0.05` | Minimum effective weight for a carried item to keep carrying. At the default, decisions expire in ~5–6 weeks (importance-graded) and escalated open questions live ~3–4 months. |
| `DAIMON_CARRY_MAX` | `8` | Cap on carried items per kind (native items never count against it or drop). Minimum 1. |

## Briefing

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_BRIEF_MAX_TOKENS` | `3000` | Token budget for the injected briefing, estimated at `len(text)//4` (no tokenizer dependency). `0` = unbounded. |
| `DAIMON_MAX_BRIEFING_DECISIONS` | `10` | Cap on decisions shown in the briefing (render-time view only — the checkpoint keeps all of them). `0` = unbounded. |
| `DAIMON_BRIEF_GLOBAL_FALLBACK` | header-only | Controls the cross-project global-pointer fallback when a project has no checkpoint of its own. Default shows a header only; set to `full` (or `1`) to inject the full foreign body. |
| `DAIMON_STALE_DAYS` | `7.0` | Age threshold (days) past which a carried item's effective last-verified stamp (its `last_verified`, else the latest resolutions.jsonl event, else `first_seen`) is stale enough for `brief` to warn about it. `0` warns on every carried item. |
| `DAIMON_PLAIN` | off | When truthy (case-insensitive), forces plain-text output — disables the rich tables/panels in `status`, `brief`, and `--help`. |
| `NO_COLOR` | unset | Presence-based, per the [NO_COLOR convention](https://no-color.org/): if the variable is set to *any* value (even empty), rich output is disabled. |

## Recall

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_RECALL_DB` | `~/.daimon/recall.db` | Location of the derived recall index (SQLite FTS). Never a source of truth — safe to delete at any time; recall rebuilds it by scanning the checkpoint and team dirs. |
| `DAIMON_RECALL_SEEN_DIR` | `~/.daimon/recall_seen` | Per-session suggestion-cooldown state so a repeated topic never re-injects. Disposable — deleting it only resets cooldowns. |

## Tenant scope

For a host that runs one daimon home with one project directory per person. A single person's machine never needs either variable. Both are read at process start, from the process env or the env file, never from prompt content, so a model cannot set them.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_TENANT_SCOPED` | off | When truthy, a caller may not choose a scope. `--slug` and `--all-projects` on `recall`, `brief` and `why` are refused with a plain error, the MCP `daimon_recall` and `daimon_brief` tools refuse `slug` and `all_projects` the same way, and `daimon projects` lists only the caller's own bucket. Refused, never silently narrowed: a caller who asked for a scope and got their own instead would read the answer as complete. |
| `DAIMON_EXTRA_READ_SLUGS` | unset | Comma-separated slugs this session may read beside its own, ambiently, with no caller argument involved. Reaches `recall`, `why` and the proactive suggestion path alike. Read only: no write path takes a slug and a session never writes to a listed scope. A hit from a listed scope names its origin project. Meant for a shared scope whose contents were already visible to everyone in it; set it only where one session is one person. |

## Team memory

Opt-in shared-memory mirror. See [docs/team.md](./team.md) for the full workflow.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_TEAM` | off | When truthy, mirror each checkpoint into the shared team dir so `brief --team` can surface teammates. Gates **writes** only — reads of the team dir are always allowed. A synced remote additionally requires the project to be in its scope allowlist (see [team.md](./team.md#which-projects-sync-the-scope-allowlist-default-closed)); out-of-scope projects mirror to the local dir only. |
| `DAIMON_AUTHOR` | git `user.name`, then OS user | Team author identity used to namespace your checkpoints. Falls back to `git config user.name`, then the OS username, then `unknown`. |
| `DAIMON_TEAM_DIR` | `~/.daimon/team` | Root of the shared team-memory mirror. |
| `DAIMON_TEAM_PROJECT` | unset | Explicit logical project path for this machine's sessions (relative, e.g. `core/api-gateway`). Overrides the sidecar's `daimon-team.toml` mapping and the origin-derived fallback when routing checkpoints under `projects/`. |
| `DAIMON_TEAM_RETENTION_DAYS` | `365` | Read-time age window: teammates' checkpoints older than this many days are skipped when reading. `0` = keep all. Never physically deletes from the shared append-only branch. |
| `DAIMON_TEAM_APPLY_FORGET` | off | Standing consent for a TEAMMATE's published forget tombstone to rewrite this machine's own checkpoints. NOT sufficient alone — the apply also requires the typed `daimon team sync --apply-forget`, because a bare `daimon team sync` is spawned detached at SessionStart. Default off: a foreign tombstone always suppresses the value on read and in the index, but deleting local belief state from someone else's hash is a decision to make knowingly — the shared branch is append-only, so there is no undo. |
| `DAIMON_LIVE_DELIVERY` | off | When truthy, an undecided request addressed to this project is delivered to a session that was already running when it arrived, at that session's next turn boundary, instead of waiting for its next SessionStart briefing. Render surface only: the ledger stays store-and-forward, the delivered nudge is the same pull-only record the next briefing would have shown, and the verbs that decide stay human-only. Once per session per revision; a `request revise` re-delivers the sharpened ask. Same daimon home only. Default off — briefing-only is the right posture for short sessions, so an always-on consumer turns this on deliberately. |

## Receipts

Opt-in signed provenance receipts (#204). When enabled, each checkpoint is
paired with a [vitni](https://github.com/Daily-Nerd/vitni) `local`-binding
receipt: an Ed25519-signed statement that binds the checkpoint's exact on-disk
bytes (`outputs_hash`) to its source transcript (`inputs_hash`), written to a
`<session>.receipt` sidecar. This makes a post-hoc edit to a checkpoint file
detectable. Receipts are fully valid offline — nothing leaves the machine.

Every step is **fail-open**: a missing CLI, missing openssl, timeout, or bad
output logs one line to `serialize.log` and proceeds without a receipt — a
receipts failure never blocks or fails a serialize or a briefing. Verify a
checkpoint on demand with `daimon verify-receipt [session]`; at briefing time a
receipt-era checkpoint whose receipt is missing or no longer matches its bytes
has its `✓ verbatim` labels degraded with a visible note.

Public-key derivation prefers the vitni CLI's `keygen` command (vitni 0.5.0+) and
falls back to openssl on older CLIs or a failed probe — so on macOS, where Apple's
LibreSSL has no Ed25519 in `openssl pkey`, receipts work once vitni ≥ 0.5.0 is
installed, with no openssl-with-Ed25519 required.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_RECEIPTS` | off | When truthy, mint a signed receipt beside each checkpoint. Default off — a new subprocess per serialize is opt-in. |
| `DAIMON_VITNI_CLI` | `vitni-verify` (on PATH) | The vitni verifier CLI used to sign/verify. A path or a name resolved on PATH. Contract: `<cli> <command>` with one JSON object on stdin and one JSON line on stdout. |
| `DAIMON_KEYS_DIR` | `~/.daimon/keys` | Where the Ed25519 signing seed (`signing.seed`, mode 0600, auto-created on first mint) and cached public key (`signing.pub.json`) live. |

## Host hooks

Serialize-throttle knobs for hosts that lack a clean session-end event. See
[docs/hosts/](./hosts/) for per-host setup.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_CODEX_SERIALIZE_ON_STOP` | on | Whether the Codex `Stop` hook serializes at all. On unless set to `0`, `false`, `no`, or `off` (case-insensitive). |
| `DAIMON_CODEX_MIN_SERIALIZE_INTERVAL` | `300` | Minimum seconds between Codex serialize spawns. `0` serializes on every `Stop`. |
| `DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL` | `300` | Minimum seconds between Windsurf serialize spawns (Windsurf has no session-end event, so capture runs on this throttle). `0` serializes every turn. |
| `DAIMON_WINDSURF_FINALIZER_QUIET_SECONDS` | `600` | Quiet period after the last Windsurf activity before a debounced finalizer serializes the trajectory's final transcript state — covers sessions whose last turns land inside the throttle window. Fractional values accepted; `0` disables the finalizer. |
| `DAIMON_WINDSURF_DIR` | `~/.daimon/windsurf` | Where the Windsurf adapter keeps the transcripts it accumulates. Read by both the hook that writes them and the `forget`/`heal` paths that delete them — change it in one place only, or the writer and the deleter stop agreeing. |
| `DAIMON_WINDSURF_STATE_DAYS` | `7` | Age window for daimon-authored Windsurf transcripts and unparsed payload dumps, reaped by `daimon heal`. A privacy bound: a forgotten value cannot be located inside prose, so this limits how long the source conversation lingers between `forget` runs. Clamped to at least 1 — unlike the other Windsurf knobs, `0` does not disable it. |

## Scar harvest

Opt-in negative-knowledge drafting (#76). At session end, daimon scans the
transcript for scar-shaped lessons — dead ends ("we tried X, it broke"),
intentional-looking weirdness worth fencing, non-obvious couplings — and
drafts *candidate* files into `<project_root>/.scars/candidates/`. The scan
is zero-LLM (pure-stdlib marker matching, English and Spanish) and
path-anchored: a hit that names no real file or directory in its own
sentence is dropped. Precision over recall — a scar system dies from noise,
not from a missed lesson.

The boundary with the [Scar](https://github.com/Daily-Nerd/Scar) project,
stated plainly: **daimon only drafts candidates.** Linting, pre-edit
injection, and promotion to active scars are Scar's job — install it in the
target repo to make the candidates useful. daimon never writes into
`.scars/` itself, never overwrites an existing candidate (a human may have
edited it), and caps how many candidates one session can emit.

Honest limits: candidates are heuristic drafts and require human review
before promotion. The harvest only runs in repos that have opted in by
having a `.scars/` directory — without one it is a silent no-op even with
the flag on. Candidate text passes through the same secret redaction as
checkpoints, so candidate files are committable.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_SCAR_HARVEST` | off | When truthy, draft scar (negative-knowledge) candidates at session end into `.scars/candidates/` — repos with a `.scars/` directory only. |

## Ops & diagnostics

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Log directory. `serialize-crash.log` honors this on both sides — the hooks that spawn the serialize child read it (process env and this file) exactly as the CLI does, because `daimon forget` deletes that file and writer and deleter must agree on where it is. `serialize.log` is the exception: the hooks still write it to `~/.daimon/logs` unconditionally, and this override only moves where the CLI (and tests) look for it. |
| `DAIMON_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Where host transcripts live (`<slug>/<session>.jsonl`). Read-only — the quote-reverification audit reads them to re-check stored quotes against their source. |

## LLM backend

Serialization needs an LLM endpoint. `daimon configure` is the intended way to
set these — and `daimon configure --init` runs the guided wizard: backend,
timeout, an immediate backend test, an optional team-memory walk (author +
remote, wired through `daimon team init`), ending with the `daimon status`
summary. Every prompt has a flag escape hatch (`--backend`, `--timeout`,
`--author`, `--team-remote`) so scripts and CI can run it non-interactively.
The URL, key, and model each fall back to a `LITELLM_*` variable if the
`DAIMON_*` form is unset.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_LLM_BACKEND` | `auto` | Transport: `auto` (litellm if credentials exist, else a named command CLI if one resolves), `litellm`, `command`, or `claude-cli`. `claude-cli` is the zero-config option: it runs a `claude` found on PATH with a built-in preset and needs nothing else set. `command` requires `DAIMON_LLM_COMMAND`. |
| `DAIMON_LLM_BASE_URL` | `http://localhost:4000` | OpenAI-compatible endpoint URL (trailing slash trimmed). Falls back to `LITELLM_BASE_URL`. |
| `DAIMON_LLM_API_KEY` | unset | API key for the endpoint. Falls back to `LITELLM_API_KEY`. |
| `DAIMON_LLM_MODEL` | unset | Model name to send. Falls back to `LITELLM_MODEL`. |
| `DAIMON_LLM_TEMPERATURE` | `0.0` | Sampling temperature for every chat call. `0.0` for deterministic extraction; some upstreams reject anything but a fixed value. |
| `DAIMON_LLM_FALLBACK` | on | When the primary backend fails, auto-fall-back to the rescue command (`DAIMON_LLM_COMMAND_FALLBACK`). Applies to both a litellm and a `command` primary. Set to `0` to disable. |
| `DAIMON_FALLBACK_MIN_SECONDS` | `DAIMON_TIMEOUT` | Minimum budget the rescue command is guaranteed on entry. The primary may have drained the shared serialize deadline retrying the very failure the rescue exists to fix, which would kill the rescue on arrival; a healthy remaining budget is never shrunk. |
| `DAIMON_LLM_STREAM` | on | Stream the litellm response so the socket timeout bounds the inter-frame gap rather than the whole completion. Without it, a long completion trips the timeout and retries from scratch. Set to `0` to disable. |
| `DAIMON_LLM_NO_CACHE` | off | When truthy, bypass gateway response caching per request — needed when a cached bad response pins a failure or runs must be statistically independent. |
| `DAIMON_LLM_BRIEFING` | off | When truthy, render the briefing via the LLM instead of the deterministic template. |
| `DAIMON_LLM_COMMAND` | unset | Full CLI invocation for the `command` backend (binary + model + flags). Required for `command`, and required for a `claude` on PATH to be used by any backend other than `claude-cli`. |
| `DAIMON_LLM_COMMAND_OUTPUT` | unset | How to extract assistant text from the command's stdout: `text` (raw stdout) or `json:<key>` (parse JSON, read `<key>`). |
| `DAIMON_LLM_COMMAND_INPUT` | `stdin` | How the prompt reaches the command backend: `stdin` (piped), `arg` (appended as the final argv element), or `file:<flag>` (written to a tempfile, then `<flag> <path>` appended). An unrecognized value logs a warning and falls back to `stdin`. |
| `DAIMON_LLM_COMMAND_FALLBACK` | unset | The one rescue CLI, used when the primary backend fails. Works for both a litellm primary and a `command` primary, which previously had no rescue direction at all. One fallback, never a chain: if the primary and this both fail the cause is almost always environmental, and a third CLI spends budget reaching the same error while making the install look better protected than it is. When unset, a litellm primary still falls back to `DAIMON_LLM_COMMAND` as before. |
| `DAIMON_LLM_COMMAND_FALLBACK_OUTPUT` | unset | Output spec for the rescue CLI, same grammar as `DAIMON_LLM_COMMAND_OUTPUT`. Carried separately because the rescue is a different binary. |
| `DAIMON_LLM_COMMAND_FALLBACK_INPUT` | `stdin` | Input spec for the rescue CLI, same grammar as `DAIMON_LLM_COMMAND_INPUT`. |

:::note[Which process receives your transcript]

A `claude` binary merely present on PATH is **not** adopted automatically. Serializing sends the full session transcript to whichever CLI is configured, so that CLI has to be named: either `DAIMON_LLM_COMMAND`, or `DAIMON_LLM_BACKEND=claude-cli` to opt into the built-in preset.

Previously an unset `DAIMON_LLM_COMMAND` plus a `claude` anywhere on PATH was enough for `auto` installs and for the litellm rescue path. If you relied on that, set one of the two variables above. `daimon configure` names the resolved binary and its path so you can see exactly which one is in use.

:::

## Serializer chunking

Long sessions are serialized in overlapping chunks whose partial checkpoints
are merged hierarchically. The defaults come from field measurements; they
only matter if your sessions routinely run very long.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_CHUNK_LINES` | `1200` | Rendered-transcript line count above which serialization switches to chunked mode. |
| `DAIMON_CHUNK_OVERLAP` | `100` | Lines of overlap between adjacent chunks, so an item straddling a boundary is seen whole by at least one chunk. |
| `DAIMON_CHUNK_CONCURRENCY` | `4` | Parallel chunk-serialize LLM calls. Minimum 1 (sequential). |
| `DAIMON_MERGE_GROUP_SIZE` | `3` | Max partial checkpoints merged per hierarchical merge call. Minimum 2. Lower to `2` if merge calls die on a gateway with a server-side request ceiling (reasoning models generating 3-way merges can exceed it; raising `DAIMON_TIMEOUT` won't help — the kill is server-side). |
