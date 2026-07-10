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

Internal serialization-tuning knobs (chunking thresholds, overlap, concurrency,
merge-group size) are intentionally not documented here — they are load-bearing
defaults calibrated against measured behavior, not user-facing configuration.

## Core

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_DISABLE` | off | Kill switch. When truthy, every hook becomes a no-op — no capture, no briefing. |
| `DAIMON_ENV_FILE` | `~/.daimon/env` | Path to the env file that backs every other variable. Read from the process env only (it names the file, so it can't live inside it). |
| `DAIMON_PROJECT_DIR` | unset | Working directory of the session being briefed or serialized, used to route per-project checkpoints. Hooks pass the host's cwd through it; unset means the project is unknown and daimon falls back to the global pointer. |
| `DAIMON_MIN_MESSAGES` | `10` | Minimum message count before a session is worth serializing. Shorter sessions are skipped. |
| `DAIMON_TIMEOUT` | `120` | Seconds a serialize subprocess may run before it is timed out. |
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
| `DAIMON_PLAIN` | off | When truthy (case-insensitive), forces plain-text output — disables the rich tables/panels in `status`, `brief`, and `--help`. |
| `NO_COLOR` | unset | Presence-based, per the [NO_COLOR convention](https://no-color.org/): if the variable is set to *any* value (even empty), rich output is disabled. |

## Recall

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_RECALL_DB` | `~/.daimon/recall.db` | Location of the derived recall index (SQLite FTS). Never a source of truth — safe to delete at any time; recall rebuilds it by scanning the checkpoint and team dirs. |
| `DAIMON_RECALL_SEEN_DIR` | `~/.daimon/recall_seen` | Per-session suggestion-cooldown state so a repeated topic never re-injects. Disposable — deleting it only resets cooldowns. |

## Team memory

Opt-in shared-memory mirror. See [docs/team.md](./team.md) for the full workflow.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_TEAM` | off | When truthy, mirror each checkpoint into the shared team dir so `brief --team` can surface teammates. Gates **writes** only — reads of the team dir are always allowed. |
| `DAIMON_AUTHOR` | git `user.name`, then OS user | Team author identity used to namespace your checkpoints. Falls back to `git config user.name`, then the OS username, then `unknown`. |
| `DAIMON_TEAM_DIR` | `~/.daimon/team` | Root of the shared team-memory mirror. |
| `DAIMON_TEAM_PROJECT` | unset | Explicit logical project path for this machine's sessions (relative, e.g. `core/api-gateway`). Overrides the sidecar's `daimon-team.toml` mapping and the origin-derived fallback when routing checkpoints under `projects/`. |
| `DAIMON_TEAM_RETENTION_DAYS` | `365` | Read-time age window: teammates' checkpoints older than this many days are skipped when reading. `0` = keep all. Never physically deletes from the shared append-only branch. |

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

## Ops & diagnostics

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Where the session-end hook writes `serialize.log`. The hook itself hardcodes `~/.daimon/logs`; this override exists so the CLI (and tests) can point `status` elsewhere. |
| `DAIMON_CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Where host transcripts live (`<slug>/<session>.jsonl`). Read-only — the quote-reverification audit reads them to re-check stored quotes against their source. |
| `DAIMON_SCAR_HARVEST` | off | When truthy, draft scar (negative-knowledge) candidates from the transcript at session-end. |

## LLM backend

Serialization needs an LLM endpoint. `daimon configure` is the intended way to
set these. The URL, key, and model each fall back to a `LITELLM_*` variable if
the `DAIMON_*` form is unset.

| Variable | Default | What it does |
|---|---|---|
| `DAIMON_LLM_BACKEND` | `auto` | Transport: `auto` (litellm if credentials exist, else a command CLI if one resolves), `litellm`, `command`, or `claude-cli`. |
| `DAIMON_LLM_BASE_URL` | `http://localhost:4000` | OpenAI-compatible endpoint URL (trailing slash trimmed). Falls back to `LITELLM_BASE_URL`. |
| `DAIMON_LLM_API_KEY` | unset | API key for the endpoint. Falls back to `LITELLM_API_KEY`. |
| `DAIMON_LLM_MODEL` | unset | Model name to send. Falls back to `LITELLM_MODEL`. |
| `DAIMON_LLM_TEMPERATURE` | `0.0` | Sampling temperature for every chat call. `0.0` for deterministic extraction; some upstreams reject anything but a fixed value. |
| `DAIMON_LLM_FALLBACK` | on | When the litellm backend fails, auto-fall-back to a command backend (gateway-failure resilience). Set to `0` to disable. |
| `DAIMON_LLM_NO_CACHE` | off | When truthy, bypass gateway response caching per request — needed when a cached bad response pins a failure or runs must be statistically independent. |
| `DAIMON_LLM_BRIEFING` | off | When truthy, render the briefing via the LLM instead of the deterministic template. |
| `DAIMON_LLM_COMMAND` | unset | Full CLI invocation for the `command` backend (binary + model + flags). |
| `DAIMON_LLM_COMMAND_OUTPUT` | unset | How to extract assistant text from the command's stdout: `text` (raw stdout) or `json:<key>` (parse JSON, read `<key>`). |
| `DAIMON_LLM_COMMAND_INPUT` | `stdin` | How the prompt reaches the command backend: `stdin` (piped), `arg` (appended as the final argv element), or `file:<flag>` (written to a tempfile, then `<flag> <path>` appended). An unrecognized value logs a warning and falls back to `stdin`. |
