# Daimon Dream-Briefing — hermes plugin (Slice 2)

A **dream-briefing** is a session-*start* artifact: a skimmable "while you were
away / here's where we left off" briefing the agent shows you when you resume work,
reconstructed from a cognitive checkpoint written at the end of the prior session.

This is **Slice 2**: local-file checkpoints, no Honcho. Serialization is
single-pass for short sessions and chunked multi-pass (armC: per-chunk D-007
serialize → 01c merge with Q-STALE latest-state preference) above
`DAIMON_CHUNK_LINES` rendered lines. Failures are named (`SerializeError`
subclasses) so the CLI and logs say what actually broke.
Dogfoodable in hermes immediately, and runnable standalone on a plain transcript
file via the CLI (no hermes required).

## How it works

```
SESSION N  ── on_session_end ──►  read transcript (SessionDB)
                                  └► serialize (D-007 prompt + LLM) → validate → ~/.daimon/checkpoints/<id>.json

SESSION N+1 ── first pre_llm_call ──►  load latest checkpoint
                                       └► render briefing (deterministic template)
                                          └► return {"context": briefing}  → appended to your first user message
```

The briefing puts **open loops first**, flags items whose state may have changed
**outside the AI session** (the PR-merge gap) with a *verify before trusting* marker,
then lists decisions and beliefs. Verbatim (extractively-pinned, D-006) facts are
marked distinctly from inferred ones.

## Install (in hermes)

```bash
# From a published repo / package:
hermes plugins install owner/daimon-plugin --enable

# Local editable (development):
uv pip install -e .            # registers the hermes_agent.plugins entry point
# or copy this directory into ~/.hermes/plugins/daimon-briefing/
```

The plugin registers two hooks (`on_session_end`, `pre_llm_call`) and bundles the
user-facing skill as `daimon-briefing:daimon-briefing` (loadable via
`skill_view("daimon-briefing:daimon-briefing")`).

## Configuration

All config is via environment variables. `DAIMON_*` takes precedence; LLM settings
fall back to the Track-A `LITELLM_*` vars.

Every variable also resolves from `~/.daimon/env` when absent from the process
environment (process env always wins; override the file location with
`DAIMON_ENV_FILE`). This is how hooks get credentials: a hook inherits whatever
environment the host process was launched with — a GUI-launched Claude Code has
no shell profile — so shell exports are not a reliable channel. Format is plain
`KEY=VALUE` lines (`export ` prefix, quotes, and `#` comments tolerated):

```bash
# ~/.daimon/env  — chmod 600, it holds API keys
DAIMON_LLM_API_KEY=sk-...
DAIMON_LLM_MODEL=<model-name>
DAIMON_LLM_BASE_URL=http://localhost:4000
```

| Variable | Default | Purpose |
|---|---|---|
| `DAIMON_DISABLE` | (unset) | `1` = kill switch; hooks become no-ops |
| `DAIMON_CHECKPOINT_DIR` | `~/.daimon/checkpoints` | Where checkpoints + `latest.json` live |
| `DAIMON_LOG_DIR` | `~/.daimon/logs` | Where `status` looks for `serialize.log` (the session-end hook writes there) |
| `DAIMON_PROJECT_DIR` | (unset) | Working directory of the session, for per-project routing. When set, `serialize` also writes `<checkpoint-dir>/<project-slug>/latest.json` and `brief` prefers it (falling back to the global `latest.json`). The Claude Code hooks set this from the payload `cwd`; unset = project unknown = global-only behavior |
| `DAIMON_MIN_MESSAGES` | `10` | Skip serialization for sessions shorter than this |
| `DAIMON_TIMEOUT` | `120` | TOTAL budget (seconds) for the session-end serialize LLM work. A deadline is computed at hook start and shared across all retry attempts: per-attempt socket timeouts are capped to the remaining budget, and retries stop when it is exhausted |
| `DAIMON_CHUNK_LINES` | `1200` | Rendered-transcript line count above which serialization goes chunked (armC: per-chunk serialize → merge). 1200 matches the D-007 recall cliff |
| `DAIMON_CHUNK_OVERLAP` | `100` | Lines shared between consecutive chunks so boundary decisions aren't lost |
| `DAIMON_CHUNK_CONCURRENCY` | `4` | Parallel chunk-serialize calls. Gateway calls are generation-bound (~minutes each); sequential chunking made long sessions take chunk-count × minutes |
| `DAIMON_LLM_BRIEFING` | (unset) | `1` = render the briefing via LLM instead of the deterministic template (opt-in; adds latency on the critical path) |
| `DAIMON_LLM_BASE_URL` | `LITELLM_BASE_URL` → `http://localhost:4000` | OpenAI-compatible gateway base URL |
| `DAIMON_LLM_API_KEY` | `LITELLM_API_KEY` | Gateway API key (required to call the LLM) |
| `DAIMON_LLM_MODEL` | `LITELLM_MODEL` | Model name to use |
| `DAIMON_LLM_TEMPERATURE` | `0.0` | Sampling temperature sent with every chat call. Default 0.0 for deterministic extraction; some upstreams (e.g. kimi-k2.6) reject anything but their pinned value — set this to match |
| `DAIMON_LLM_BACKEND` | `auto` | `auto` (default) = litellm if credentials set, else a CLI; or force `litellm` | `command` | `claude-cli` |
| `DAIMON_LLM_COMMAND` | (unset) | CLI invocation for `command` backend; prompt piped via stdin |
| `DAIMON_LLM_COMMAND_OUTPUT` | `text` | `text` or `json:<key>` — how to read stdout |
| `DAIMON_LLM_FALLBACK` | `1` | auto-fall-back to a command backend when litellm fails |

### Pluggable LLM backend

By default (`auto`) the serializer uses LiteLLM when credentials are set, otherwise a headless LLM CLI if one is available (e.g. `claude` on PATH). When litellm fails (gateway down, no key),
daimon auto-falls-back to a command backend if one resolves — zero config
if Claude Code is installed. Override with any CLI:

    # codex
    DAIMON_LLM_BACKEND=command
    DAIMON_LLM_COMMAND=codex exec --json
    DAIMON_LLM_COMMAND_OUTPUT=json:...   # set to the field holding the text

    # ollama (raw text out)
    DAIMON_LLM_BACKEND=command
    DAIMON_LLM_COMMAND=ollama run llama3
    DAIMON_LLM_COMMAND_OUTPUT=text

The prompt is piped via stdin; the CLI runs isolated (`DAIMON_DISABLE=1`, temp
cwd) so an agent CLI cannot recurse into daimon's own hooks.

## Dogfood without hermes

The CLI works on any plain-text/markdown transcript, no hermes needed. It uses the
same env-driven LLM client. The command is `daimon`; `daimon-briefing` remains a
deprecated alias for one release and will be removed afterward.

```bash
export LITELLM_API_KEY=sk-...           # or DAIMON_LLM_API_KEY
export LITELLM_MODEL=<model-name>       # or DAIMON_LLM_MODEL
export LITELLM_BASE_URL=http://localhost:4000   # or DAIMON_LLM_BASE_URL

# 1. Serialize a transcript file into a checkpoint:
daimon serialize path/to/transcript.md
#   → writes ~/.daimon/checkpoints/<transcript-stem>.json and updates latest.json
#   → with DAIMON_PROJECT_DIR set, also updates <project-slug>/latest.json

# 2. Render the "while you were away" briefing from the latest checkpoint:
daimon brief
#   → prints the briefing to stdout
#   → with DAIMON_PROJECT_DIR set, prefers that project's latest.json
#     (global latest.json is the fallback)

# 3. Check whether a checkpoint actually got written (no log grepping):
daimon status [--project DIR] [--json]
#   → project checkpoint + global fallback: session id, age, path
#   → last serialize outcome from ~/.daimon/logs/serialize.log
#     (success with duration, error, or "no serialize history")
#   → project resolution: --project > DAIMON_PROJECT_DIR > cwd
#   → exit 0 if a project or global checkpoint exists, 1 if neither
#     (scripts can test existence cheaply); --json for machine-readable output
```

Transcript format: markdown with `**user**:` / `**assistant**:` role markers (or
`user:` / `assistant:`), or plain text (treated as a single user message).

## What Slice 2 does NOT do

- **No regression-gate validation yet** — chunked extraction is implemented but the
  §3 harness gates (RR ≥70%, FMR ≤10%, staleness rate), the S2 probe rerun, the
  holdout, and the 2-cycle test are still owed (Slice 2 part 2, in `research/`).
- **No Honcho** — checkpoints are local files only. Honcho-backed store + cross-session
  recall is **Slice 3**.
- **No Claimify gate / Graphiti PR** — **Slice 4**, independent.
- No proactive interruption, no multi-platform, no checkpoint versioning/rollback.
