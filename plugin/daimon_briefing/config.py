"""Env-driven configuration. DAIMON_* takes precedence; LLM vars fall back to LITELLM_*.

Each variable resolves process env first, then `~/.daimon/env` (override the
file location with DAIMON_ENV_FILE). The file exists because hooks run in
whatever environment the host process happened to inherit — a GUI-launched
Claude Code has no shell profile, so shell exports are not a reliable channel.
File format: KEY=VALUE lines; `export ` prefix, surrounding quotes, blank
lines, and `#` comments are tolerated. Keep it chmod 600 — it holds API keys.
"""

import getpass
import os
import subprocess
from pathlib import Path


def _env_file_path() -> Path:
    raw = os.environ.get("DAIMON_ENV_FILE")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "env"


def _file_values() -> dict:
    """Parse the env file. Re-read per call — processes are short-lived and a
    cache would leak between tests; the file is a handful of lines."""
    path = _env_file_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _get(name: str) -> str | None:
    """One variable: process env wins; env file is the fallback."""
    val = os.environ.get(name)
    if val is not None:
        return val
    return _file_values().get(name)


def _flag(name: str) -> bool:
    return (_get(name) or "").strip() in ("1", "true", "yes", "on")


def is_disabled() -> bool:
    """Kill switch — when set, all hooks become no-ops."""
    return _flag("DAIMON_DISABLE")


def checkpoint_dir() -> Path:
    raw = _get("DAIMON_CHECKPOINT_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "checkpoints"


def checkpoint_history() -> int:
    """How many checkpoint pointers to retain per directory: latest.json plus
    prev-1.json .. prev-(N-1).json. Default 3; 1 disables history (latest only).
    Feeds #26 self-healing: a failed serialize can fall back to a prev pointer."""
    try:
        return max(1, int(_get("DAIMON_CHECKPOINT_HISTORY") or "3"))
    except ValueError:
        return 3


def carry_enabled() -> bool:
    """Deterministic cross-session carry (#33 Phase 2). Default ON — it fixes a
    measured defect (multicycle run-01: whole-item loss under LLM-mediated
    carry). DAIMON_CARRY=0 is the kill switch."""
    return (_get("DAIMON_CARRY") or "1") != "0"


def carry_floor() -> float:
    """Minimum #78 effective weight for a carried item to keep carrying.
    Default 0.05: decisions expire ~5-6 weeks (importance-graded), escalated
    open questions live ~3-4 months — calibrated against scoring.TYPE_RULES."""
    try:
        return float(_get("DAIMON_CARRY_FLOOR") or "0.05")
    except ValueError:
        return 0.05


def carry_max() -> int:
    """Cap on CARRIED items per kind (native items never count or drop)."""
    try:
        return max(1, int(_get("DAIMON_CARRY_MAX") or "8"))
    except ValueError:
        return 8


def checkpoint_keep() -> int:
    """How many per-session checkpoint files (<session_id>.json) to retain in the
    flat store dir. Newest-N by the #93 `created` stamp (file mtime fallback);
    older files are GC'd opportunistically after a successful write. Default 100;
    0 disables GC entirely (keep forever). Deliberately generous so #33's merged
    checkpoint history keeps a deep well of per-session files to reconstruct from."""
    try:
        return max(0, int(_get("DAIMON_CHECKPOINT_KEEP") or "100"))
    except ValueError:
        return 100


def gc_pin_importance() -> int:
    """Item-importance threshold that pins a checkpoint file against GC (#31):
    a file whose max item importance reaches this survives outside the newest-N
    window — an importance-10 decision must not die to 100 newer trivia
    (rational forgetting weighs NEED, not just recency). Default 9; 0 disables
    pinning (pure recency window, the pre-#31 behavior)."""
    try:
        return min(10, max(0, int(_get("DAIMON_GC_PIN_IMPORTANCE") or "9")))
    except ValueError:
        return 9


def max_briefing_decisions() -> int:
    """Cap on decisions shown in the briefing (render-time view). Default 10; 0 =
    unbounded. The checkpoint keeps ALL decisions — this bounds only the injected
    briefing, whose sole unbounded-growth axis is the decisions list."""
    try:
        return max(0, int(_get("DAIMON_MAX_BRIEFING_DECISIONS") or "10"))
    except ValueError:
        return 10


# ---- team memory (#111): opt-in shared mirror + author identity ----


def team_enabled() -> bool:
    """Opt-in (DAIMON_TEAM=1, default OFF): mirror each checkpoint into the shared
    team dir so `brief --team` can surface teammates. Gates WRITES only — reads of
    the team dir are always allowed."""
    return _flag("DAIMON_TEAM")


def team_dir() -> Path:
    """Root of the shared team-memory mirror. Sibling of the checkpoint dir under
    ~/.daimon by default; DAIMON_TEAM_DIR overrides (tests point it under tmp so no
    test can touch the developer's real ~/.daimon/team)."""
    raw = _get("DAIMON_TEAM_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "team"


def recall_db() -> Path:
    """Location of the derived recall index (#112). NEVER source of truth —
    safe to delete at any time; recall rebuilds it by scanning the local flat
    store + team dir. Lives BESIDE the checkpoint dir under ~/.daimon, not
    inside it: the flat store's GC / pointer scans own that namespace, and a
    foreign file there is one landmine nobody needs. DAIMON_RECALL_DB overrides
    (tests point it under tmp so no test can clobber the real index)."""
    raw = _get("DAIMON_RECALL_DB")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "recall.db"


def brief_max_tokens() -> int:
    """Token budget for the injected plain briefing (#79), estimated at
    len(text)//4 — no tokenizer dependency. 0 = unbounded. Default 3000: a
    briefing that eats a fifth of a small context window stops being a briefing.
    DAIMON_BRIEF_MAX_TOKENS overrides."""
    raw = _get("DAIMON_BRIEF_MAX_TOKENS")
    try:
        n = int(raw) if raw is not None else 3000
    except ValueError:
        return 3000
    return max(0, n)


def recall_seen_dir() -> Path:
    """Per-session suggestion-cooldown state for recall-inject (#125): one small
    JSON per session listing the checkpoints already suggested, so a repeated
    topic never re-injects. Disposable like the recall db — deleting it only
    resets cooldowns. DAIMON_RECALL_SEEN_DIR overrides (tests -> tmp)."""
    raw = _get("DAIMON_RECALL_SEEN_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "recall_seen"


def team_retention_days() -> int:
    """Read-time age window for teammates' checkpoints (#113): read_team skips
    files older than this many days. 0 = keep all. Default 365 — deliberately
    generous; retention NEVER physically deletes from the shared append-only
    branch (deletes race appends, the spike verdict)."""
    try:
        return max(0, int(_get("DAIMON_TEAM_RETENTION_DAYS") or "365"))
    except ValueError:
        return 365


def _git_user_name() -> str:
    """`git config user.name` in the current dir, or "" on ANY failure (not a repo,
    git missing, timeout, unset). Same subprocess style as resolve_project_root —
    the git dependency lives HERE in the policy layer, never in store (pure file-ops)."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def author() -> str:
    """Team author identity for namespacing: DAIMON_AUTHOR env → `git config
    user.name` → getpass.getuser(), falling to "unknown" if all fail. Never raises.

    Not cached: a checkpoint write happens once per session-end, so the single git
    call per write is negligible, and a process-level cache would only leak stale
    identity between tests."""
    name = (_get("DAIMON_AUTHOR") or "").strip()
    if not name:
        name = _git_user_name()
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return name or "unknown"


def log_dir() -> Path:
    """Where the session-end hook writes serialize.log. The hook hardcodes
    ~/.daimon/logs; this override exists so the CLI (and tests) can point
    `status` somewhere else."""
    raw = _get("DAIMON_LOG_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "logs"


def project_dir() -> str | None:
    """Working directory of the session being briefed/serialized (per-project
    checkpoint routing). Hooks pass the host payload's cwd through this var;
    unset = project unknown = pre-routing behavior."""
    return _get("DAIMON_PROJECT_DIR") or None


def resolve_project_root(raw: str | None) -> str | None:
    """Normalize a project dir to its git toplevel so a subdir session maps to the
    ONE repo bucket (#74).

    Checkpoint identity is keyed on the (slugged) project dir. A session run from a
    subdirectory of a repo — e.g. `daimon/plugin/`, which is not its own git repo —
    would otherwise slug to a different bucket than the repo root and fork a separate
    checkpoint history. Resolving to `git rev-parse --show-toplevel` at ingress keeps
    every session in the repo pointing at the same bucket.

    This lives in config (the resolution/policy layer) on purpose: store.py stays
    pure file-ops with no git/subprocess dependency.

    Falsy `raw` passes through unchanged (None must keep falling back to the global
    pointer — an unknown project is not invented into a dir). On ANY git failure —
    not a repo, git binary missing, timeout, OS error, dir gone — `raw` is returned
    UNCHANGED, preserving exact pre-normalization behavior. Never raises.
    """
    if not raw:
        return raw
    try:
        result = subprocess.run(
            ["git", "-C", raw, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return raw
    if result.returncode != 0:
        return raw
    top = result.stdout.strip()
    return top or raw


def min_messages() -> int:
    try:
        return int(_get("DAIMON_MIN_MESSAGES") or "10")
    except ValueError:
        return 10


def timeout_seconds() -> int:
    try:
        return int(_get("DAIMON_TIMEOUT") or "120")
    except ValueError:
        return 120


def hung_after_seconds() -> int:
    """Age (seconds) past which a serialize spawn with NO result line is treated
    as hung/killed rather than still-running. Serialize runs 4-25 min in
    production, so the default (1800 = 30 min) sits safely beyond a slow run.
    Override with DAIMON_HUNG_AFTER."""
    try:
        return int(_get("DAIMON_HUNG_AFTER") or "1800")
    except ValueError:
        return 1800


def chunk_lines() -> int:
    """Rendered-transcript line count above which serialization goes chunked
    (armC). 1200 matches the recall cliff measured in the D-007 probe."""
    try:
        return int(_get("DAIMON_CHUNK_LINES") or "1200")
    except ValueError:
        return 1200


def chunk_overlap() -> int:
    try:
        return int(_get("DAIMON_CHUNK_OVERLAP") or "100")
    except ValueError:
        return 100


def chunk_concurrency() -> int:
    """Parallel chunk-serialize calls. Gateway calls are generation-bound
    (~minutes each); sequential chunking makes long sessions unusable."""
    try:
        return max(1, int(_get("DAIMON_CHUNK_CONCURRENCY") or "4"))
    except ValueError:
        return 4


def merge_group_size() -> int:
    """Max partials per hierarchical merge call. K=3 keeps every merge call at
    the proven 3-chunk size from issue #28 where 6-chunk merges DNF at 900s."""
    try:
        return max(2, int(_get("DAIMON_MERGE_GROUP_SIZE") or "3"))
    except ValueError:
        return 3


def llm_briefing() -> bool:
    """Opt-in: render the briefing via LLM instead of the deterministic template."""
    return _flag("DAIMON_LLM_BRIEFING")


def scar_harvest_enabled() -> bool:
    """Opt-in: draft scar candidates from the transcript at session-end (#76)."""
    return _flag("DAIMON_SCAR_HARVEST")


def llm_no_cache() -> bool:
    """Per-request bypass of gateway response caching (LiteLLM `no-cache`) —
    needed when a cached bad response pins a failure or when runs must be
    statistically independent."""
    return _flag("DAIMON_LLM_NO_CACHE")


def llm_base_url() -> str:
    return (
        _get("DAIMON_LLM_BASE_URL")
        or _get("LITELLM_BASE_URL")
        or "http://localhost:4000"
    ).rstrip("/")


def llm_api_key() -> str | None:
    return _get("DAIMON_LLM_API_KEY") or _get("LITELLM_API_KEY")


def llm_model() -> str | None:
    return _get("DAIMON_LLM_MODEL") or _get("LITELLM_MODEL")


def llm_temperature() -> float:
    """Sampling temperature sent with every chat call. Default 0.0 for
    deterministic extraction; some upstreams (e.g. kimi-k2.6) reject anything
    but a fixed value — set this to whatever the model demands."""
    try:
        return float(_get("DAIMON_LLM_TEMPERATURE") or "0.0")
    except ValueError:
        return 0.0


def llm_backend() -> str:
    """Which LLM transport: 'auto' (default — litellm if credentials exist,
    else a command CLI if one resolves), 'litellm', 'command', or 'claude-cli'."""
    return (_get("DAIMON_LLM_BACKEND") or "auto").strip()


def llm_fallback() -> bool:
    """When the litellm backend fails, auto-fall-back to a command backend.
    Default ON — this is the gateway-failure resilience. Set 0 to disable."""
    return (_get("DAIMON_LLM_FALLBACK") or "1").strip() in ("1", "true", "yes", "on")


def llm_command() -> str | None:
    """Full CLI invocation for the command backend (binary + model + flags).
    The prompt is piped via stdin, never argv."""
    return _get("DAIMON_LLM_COMMAND") or None


def llm_command_output() -> str | None:
    """How to extract assistant text from the command's stdout:
    'text' (raw stdout) | 'json:<key>' (parse JSON, read <key>)."""
    return _get("DAIMON_LLM_COMMAND_OUTPUT") or None
