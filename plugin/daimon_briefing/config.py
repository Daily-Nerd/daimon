"""Env-driven configuration. DAIMON_* takes precedence; LLM vars fall back to LITELLM_*.

Each variable resolves process env first, then `~/.daimon/env` (override the
file location with DAIMON_ENV_FILE). The file exists because hooks run in
whatever environment the host process happened to inherit — a GUI-launched
Claude Code has no shell profile, so shell exports are not a reliable channel.
File format: KEY=VALUE lines; `export ` prefix, surrounding quotes, blank
lines, and `#` comments are tolerated. Keep it chmod 600 — it holds API keys.
"""

import getpass
import logging
import os
import subprocess
from pathlib import Path
from typing import overload

log = logging.getLogger(__name__)


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


def brief_global_fallback() -> bool:
    """#96: brief's cross-project global-pointer fallback is header-only by
    default — a fresh project's briefing that is 100% another project's
    content reads as contamination no matter how it is labeled (two field
    reports on the #94 arc). Opt in to the full foreign body with
    DAIMON_BRIEF_GLOBAL_FALLBACK=full (or 1)."""
    return (_get("DAIMON_BRIEF_GLOBAL_FALLBACK") or "") in ("full", "1")


def carry_enabled() -> bool:
    """Deterministic cross-session carry (#33 Phase 2). Default ON — it fixes a
    measured defect (multicycle run-01: whole-item loss under LLM-mediated
    carry). DAIMON_CARRY=0 is the kill switch."""
    return (_get("DAIMON_CARRY") or "1") != "0"


def chunk_cache_enabled() -> bool:
    """#48: content-addressed chunk-extraction cache. Default ON — same risk
    class as the #314 partials it replaces (correctness fully keyed; failure
    mode = re-pay the call). DAIMON_CHUNK_CACHE=0 is the kill switch."""
    return (_get("DAIMON_CHUNK_CACHE") or "1") != "0"


def chunk_cache_days() -> int:
    """#48: chunk-cache rotation window, days. Cached chunk output is
    PRE-redaction (forced by #125 quote verification ordering), so the window
    is a privacy bound, not just a disk bound — 3 days covers the heal/retry
    win while keeping exposure short. Override with DAIMON_CHUNK_CACHE_DAYS."""
    try:
        return int(_get("DAIMON_CHUNK_CACHE_DAYS") or "3")
    except ValueError:
        return 3


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


def stale_days() -> float:
    """Age threshold (days) before a carried item's EFFECTIVE last-verified
    age (#215: last_verified stamp, else the latest resolutions.jsonl event
    ts, else first_seen — in that priority) is stale enough for `daimon
    brief` to warn about it. Agreement between two agent-written sources
    (the checkpoint restating a carried item) is not corroboration — this is
    the budget past which that agreement alone stops being enough. Default
    7.0 days; DAIMON_STALE_DAYS overrides, garbage falls back to the default
    (fail-open, same try/except-float shape as carry_floor)."""
    try:
        return max(0.0, float(_get("DAIMON_STALE_DAYS") or "7.0"))
    except ValueError:
        return 7.0


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


def team_apply_forget() -> bool:
    """#600 slice B: may a TEAMMATE's forget rewrite this machine's own
    checkpoints? Default NO — a foreign tombstone suppresses on read and in
    the index (the admit_foreign posture), but never deletes local belief
    state. Turning this on hands every member of the sidecar a delete
    primitive over your memory, and the shared branch is append-only, so
    there is no undo; that is a decision to make knowingly, not one to
    inherit from a `git pull`. Same default-closed posture as #279 scope.

    Standing consent only — it is NOT sufficient on its own. The apply also
    needs `daimon team sync --apply-forget`, because a bare `daimon team
    sync` is spawned DETACHED at SessionStart (lib.spawn_team_sync, stdout
    to DEVNULL) exactly like heal: a setting alone would delete local belief
    state unattended and silently."""
    return _flag("DAIMON_TEAM_APPLY_FORGET")


def team_dir() -> Path:
    """Root of the shared team-memory mirror. Sibling of the checkpoint dir under
    ~/.daimon by default; DAIMON_TEAM_DIR overrides (tests point it under tmp so no
    test can touch the developer's real ~/.daimon/team)."""
    raw = _get("DAIMON_TEAM_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "team"


def windsurf_state_dir() -> Path:
    """Root of the Windsurf adapter's own state (#607). The adapter writes
    FULL RAW TRANSCRIPTS here when Cascade gives it no native transcript, so
    this is daimon-authored plaintext and inside the deletion contract —
    unlike Codex rollouts or Claude Code JSONL, which daimon reads by path
    and never copies. DAIMON_WINDSURF_DIR overrides (tests point it under
    tmp; the hook hardcodes ~/.daimon/windsurf because it must run with no
    package import at all)."""
    raw = _get("DAIMON_WINDSURF_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "windsurf"


def windsurf_state_days() -> int:
    """#607: age window for daimon-authored Windsurf transcripts and unparsed
    payload dumps. A privacy bound, not a disk bound: a forgotten value
    cannot be located inside prose (the tombstone is a hash, never the
    text), so between forgets this window is what limits how long the
    source conversation lingers. 7 days rather than the chunk cache's 3 —
    the transcript is what quote provenance resolves against, and
    provenance already degrades to `absent-local` rather than failing when
    it is gone. Override with DAIMON_WINDSURF_STATE_DAYS.

    Clamped at 1 (the checkpoint_history / carry_max posture). Every other
    DAIMON_WINDSURF_* knob reads 0 as "disable", so an unclamped 0 here
    would make the cutoff `now` and delete the LIVE capture buffer at the
    next heal — reading an ambiguous 0 as "delete everything" is the wrong
    direction for a knob a user reaches for to turn something off."""
    try:
        return max(1, int(_get("DAIMON_WINDSURF_STATE_DAYS") or "7"))
    except ValueError:
        return 7


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


def ruling_cap() -> int:
    """#693: hard cap on ACTIVE rulings, enforced at activation (refusing
    past it forces an explicit retire-or-decline decision) rather than at
    render, so the always-present briefing section can never silently
    truncate. Default 7 is a render-budget default, not a claim about how
    many rules a user has. DAIMON_RULING_CAP overrides."""
    try:
        return max(1, int(_get("DAIMON_RULING_CAP") or "7"))
    except ValueError:
        return 7


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
    JSON per session, {"origins": [...], "content_keys": [...]} — the
    checkpoints already suggested AND the content keys already injected, so
    neither a repeated topic nor the same claim carried by a second checkpoint
    re-injects (#451). Pre-#451 files are a flat list of origins and still read
    (see cli._load_seen). Disposable like the recall db — deleting it only
    resets cooldowns. DAIMON_RECALL_SEEN_DIR overrides (tests -> tmp)."""
    raw = _get("DAIMON_RECALL_SEEN_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "recall_seen"


def team_project() -> str | None:
    """Explicit logical team-project path for this machine's sessions (#200):
    a relative path like 'core/api-gateway'. Tier-1 override in teamproject's
    resolution — explicit local intent beats the daimon-team.toml mapping and
    the origin-derived fallback. Unset = resolve from git origin as usual."""
    val = (_get("DAIMON_TEAM_PROJECT") or "").strip()
    return val or None


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


def session_speaker() -> str | None:
    """WHO is talking to this session, as the host declares it out of band
    (DAIMON_SESSION_SPEAKER). None when unset or blank. Never raises.

    Same trust class as `author`'s DAIMON_AUTHOR — a host stating a fact about
    the session it is running, not a model asserting an identity — but a pure
    LABEL, and that difference is the whole reason this variable exists rather
    than reusing DAIMON_AUTHOR. `author` is a KEY with five load-bearing uses
    (the admit_foreign trust clamp, the recall index dedup key, the team
    sidecar directory name), so setting it per person would re-key the store
    and split one machine's memory across directories. `stated_by` is rendered
    and nothing else.

    THE HOST'S OBLIGATION: set this only where one session is one person for
    its whole life. A host serving several people through a single session must
    NOT set it — it has no single answer, and the honest answer is absence.
    Where the host can attribute per message it should set `said_by` on the
    message instead (#893); that path is precise and always wins over this one.
    """
    return (_get("DAIMON_SESSION_SPEAKER") or "").strip() or None


# ---- tenant scope (#899): the host, never the caller, chooses a read scope --


def tenant_scoped() -> bool:
    """When set, every caller-chosen cross-project address is refused: `--slug`
    and `--all-projects` on the CLI, `slug` and `all_projects` on the MCP
    tools, and `daimon projects` lists only the caller's own bucket.

    For a host running one daimon home with one project directory per person,
    those surfaces are cross-tenant read and enumerate primitives, one prompt
    injection away from every tenant's memory. A single person's machine
    never needs this. Read at process start through the same accessor as
    every other flag: process env or env file, never prompt content."""
    return _flag("DAIMON_TENANT_SCOPED")


# One refusal, both surfaces (CLI and MCP): a caller who asked for a scope and
# silently got their own instead would read the answer as complete.
TENANT_SCOPE_REFUSAL = (
    "this daimon home is tenant-scoped (DAIMON_TENANT_SCOPED): a caller may "
    "not choose a scope (slug, all_projects); reads stay in this project's "
    "own scope plus any host-declared DAIMON_EXTRA_READ_SLUGS")


def extra_read_slugs() -> tuple[str, ...]:
    """Slugs the host lets this session read AMBIENTLY, beside its own
    (DAIMON_EXTRA_READ_SLUGS, comma separated), for a shared scope whose
    contents were already visible to everyone in it. Read only: no write path
    takes a slug, and a session never writes to a listed scope.

    The dangerous half of cross-project reads was never that two scopes are
    readable; it was that the CALLER picked the slug. This is the host
    picking, out of band, in the trust class of DAIMON_AUTHOR. Stripped,
    de-duplicated, order kept; an entry that is not slug-shaped is dropped
    rather than widened into a path or a glob."""
    out: list[str] = []
    for raw in (_get("DAIMON_EXTRA_READ_SLUGS") or "").split(","):
        tok = raw.strip()
        if not tok or tok in out:
            continue
        if not all(ch.isalnum() or ch in "_-" for ch in tok):
            continue
        out.append(tok)
    return tuple(out)


# ---- signed provenance receipts (#204): opt-in vitni local-binding receipts ----


def receipts_enabled() -> bool:
    """Opt-in (DAIMON_RECEIPTS=1, default OFF): mint a vitni `local`-binding
    receipt beside each checkpoint so a post-hoc edit to the artifact is
    detectable. Gates the mint path only; every step is fail-open — a receipts
    failure must never block or fail a serialize/brief (#204)."""
    return _flag("DAIMON_RECEIPTS")


def vitni_cli() -> str:
    """The vitni verifier CLI (#204). DAIMON_VITNI_CLI overrides; default
    'vitni-verify' resolved on PATH. Contract: `<cli> <command>` with one JSON
    object on stdin and one JSON line on stdout."""
    return (_get("DAIMON_VITNI_CLI") or "").strip() or "vitni-verify"


def keys_dir() -> Path:
    """Where the #204 Ed25519 signing seed (signing.seed, 0600) and cached
    public key (signing.pub.json) live. Default ~/.daimon/keys; DAIMON_KEYS_DIR
    overrides (tests point it under tmp so no test can touch real keys)."""
    raw = _get("DAIMON_KEYS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "keys"


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


def capture_host() -> str | None:
    """Trusted host hint forwarded by installed capture hooks (#594)."""
    return _get("DAIMON_CAPTURE_HOST") or None


def claude_projects_dir() -> Path:
    """Where host transcripts live: ~/.claude/projects/<slug>/<session>.jsonl.
    The #125 audit reads (never writes) these to re-check stored quotes against
    their source. Overridable so tests point it at a tmp fixture instead of the
    developer's real transcripts."""
    raw = _get("DAIMON_CLAUDE_PROJECTS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".claude" / "projects"


# `None` is returned for exactly one input, `None` itself. Every str, `""`
# included, comes back as a str (the git failure path returns `raw` unchanged),
# so a caller passing a definite str never has to narrow the result.
@overload
def resolve_project_root(raw: str) -> str: ...
@overload
def resolve_project_root(raw: None) -> None: ...
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


def git_branch(project_dir) -> str | None:
    """Current branch name for a project working dir at capture time (#222), or
    None on ANY failure/ambiguity — never raises, never returns an empty string:
    not a project dir (falsy input), not a git repo, git binary missing,
    timeout, an unborn HEAD (a fresh `git init` with no commits — rev-parse
    fails "ambiguous argument"), or a DETACHED HEAD (rev-parse --abbrev-ref
    prints the literal "HEAD" for that case, which is not a branch name).

    Lives here, not in store.py: store stays free of the git/subprocess
    dependency (the same reasoning `resolve_project_root` above documents).
    Short timeout — this runs on every checkpoint write, including from hooks
    on session end, which must never block on a slow/hung git.
    """
    if not project_dir:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    if not branch or branch == "HEAD":
        return None
    return branch


def min_messages() -> int:
    try:
        return int(_get("DAIMON_MIN_MESSAGES") or "10")
    except ValueError:
        return 10


def timeout_seconds() -> int:
    """Total serialize budget (seconds) — shared across retry attempts, with
    per-attempt socket timeouts capped to the remaining budget. Real serialize
    and merge calls on the zero-config claude backend run 74s-25min in
    production (#284); 420 is the field-derived floor — a lower budget cannot
    fit even one slow call, let alone a retry."""
    try:
        return int(_get("DAIMON_TIMEOUT") or "420")
    except ValueError:
        return 420


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


def scene_traces_enabled() -> bool:
    """Opt-in experiment (#317): serializer asks for a per-item `scene` —
    1-2 sentences of episodic context — gated on the LongMemEval harness
    before it can become default."""
    return _flag("DAIMON_SCENE_TRACES")


def worldcheck_enabled() -> bool:
    """Opt-in (#365): brief-time spot-check of carried PR/issue-state claims
    against reality via read-only `gh` probes. Default OFF (#318 flag
    precedent): `daimon brief` runs on the SessionStart hook's latency-
    critical injection path (8s subprocess budget inside a 10s hook budget),
    and even budget-bounded network probes there must be deliberately chosen.
    Flag off, the render path never touches the worldcheck module — output
    stays byte-identical (test-pinned)."""
    return _flag("DAIMON_WORLDCHECK")


def live_delivery_enabled() -> bool:
    """Opt-in (#756): deliver an undecided request into a LIVE session at its
    next turn boundary, instead of making it wait for that session's next
    SessionStart brief. Default OFF (#318 flag precedent): briefing-only is
    the correct posture for short sessions, and the #125 noise budget errs
    toward silence — an always-on consumer, whose sessions outlive the asks
    addressed to them, turns this on deliberately. Flag off, the per-prompt
    path never reads the request ledger at all."""
    return _flag("DAIMON_LIVE_DELIVERY")


def heal_escalation_enabled() -> bool:
    """Opt-in (#360): `daimon heal` escalates its re-serialize to
    perspective-diverse extraction — N passes over the transcript from
    distinct perspectives, merged by the ordinary merge pass. Multiplies the
    LLM token cost of a heal by ~the perspective count on the user's own
    backend, so it ships default OFF (#318 flag precedent). Gates the HEAL
    path only — the session-end default serialize never escalates, flag or
    no flag."""
    return _flag("DAIMON_HEAL_ESCALATION")


def llm_no_cache() -> bool:
    """Per-request bypass of gateway response caching (LiteLLM `no-cache`) —
    needed when a cached bad response pins a failure or when runs must be
    statistically independent."""
    return _flag("DAIMON_LLM_NO_CACHE")


def llm_stream() -> bool:
    """#531: request streamed responses from the litellm backend. Default ON —
    a non-streaming request emits zero bytes until the whole completion is
    done, which turns DAIMON_TIMEOUT into a hard ceiling on total generation
    time; merge-sized completions cross it at normal throughput and get
    killed healthy. Set 0 for strict upstreams that reject `stream` or
    `stream_options` (restores the exact pre-#531 body)."""
    return (_get("DAIMON_LLM_STREAM") or "1").strip() in ("1", "true", "yes", "on")


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


def fallback_min_seconds() -> int:
    """#341: minimum budget the command fallback is guaranteed on entry.
    The shared deadline is drained by the primary retrying the very gateway
    failure the fallback exists to rescue — handing the fallback the
    remainder made it dead on arrival (field data: entered 6+ times, rescued
    0). Defaults to timeout_seconds(): the operator has already declared how
    long one backend call may take. Override with DAIMON_FALLBACK_MIN_SECONDS."""
    raw = _get("DAIMON_FALLBACK_MIN_SECONDS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return timeout_seconds()


def resample_min_seconds() -> int:
    """#553: minimum budget the #118 validation resample is guaranteed on
    entry. Exactly the failure #341 fixed for the command fallback, one path
    over: the shared deadline is drained by the attempt whose output just got
    rejected, so handing the resample the remainder made it dead on arrival
    (field case: 262s left against a merge that had taken 371s). Defaults to
    timeout_seconds() for the same reason fallback_min_seconds() does — the
    operator has already declared how long one backend call may take.
    Override with DAIMON_RESAMPLE_MIN_SECONDS."""
    raw = _get("DAIMON_RESAMPLE_MIN_SECONDS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return timeout_seconds()


def llm_command() -> str | None:
    """Full CLI invocation for the command backend (binary + model + flags).
    How the prompt reaches it is controlled separately by
    llm_command_input() — stdin by default, but 'arg' and 'file:<flag>' let
    it land in argv instead (#58)."""
    return _get("DAIMON_LLM_COMMAND") or None


def llm_command_output() -> str | None:
    """How to extract assistant text from the command's stdout:
    'text' (raw stdout) | 'json:<key>' (parse JSON, read <key>)."""
    return _get("DAIMON_LLM_COMMAND_OUTPUT") or None


def _command_input_spec(var: str) -> str:
    """Shared parser for the command-input axis, used by both the primary
    (DAIMON_LLM_COMMAND_INPUT) and the #475 rescue
    (DAIMON_LLM_COMMAND_FALLBACK_INPUT). One parser, so a fix to the
    normalization rules can never apply to only one of the two commands."""
    val = (_get(var) or "stdin").strip()
    if val == "stdin" or val == "arg":
        return val
    if val.startswith("file:"):
        # Normalize the flag: "file:  --prompt-file " would otherwise survive
        # into argv as "  --prompt-file" — not an injection risk, but a silent
        # misconfiguration most CLIs won't match. A flag that strips to empty
        # is the empty-flag case in disguise and falls through to the warning.
        flag = val[len("file:"):].strip()
        if flag:
            return f"file:{flag}"
    log.warning(
        "%s=%r not recognized (expected stdin|arg|file:<flag>) "
        "— falling back to stdin", var, val,
    )
    return "stdin"


def llm_command_input() -> str:
    """How the prompt reaches the command backend: 'stdin' (default —
    piped via subprocess input=, current/original behavior) | 'arg' (appended
    as the final raw argv element, never string-interpolated into the command
    template) | 'file:<flag>' (written to a tempfile, then '<flag> <path>'
    appended to argv). Needed for headless CLIs that don't read stdin at all
    (e.g. the Devin CLI panics on a piped, promptless invocation — #58).

    An unrecognized value fails OPEN to 'stdin' (matching the fail-open
    precedent of the sibling llm_command_output() axis, where an unknown
    spec silently falls through to the 'text' branch) rather than raising —
    a typo here must not turn every chat() call into a crash. Unlike the
    output axis, this logs a warning: the input axis is easier to get wrong
    silently (a bad output spec still returns *something*; a bad input spec
    on a stdin-only CLI runs the command with an empty argv-facing prompt).
    """
    return _command_input_spec("DAIMON_LLM_COMMAND_INPUT")


def llm_command_fallback() -> str | None:
    """The ONE rescue CLI, for every backend (#475).

    Before #475, DAIMON_LLM_COMMAND meant two different things depending on
    DAIMON_LLM_BACKEND: the primary when the backend was `command`, the
    rescue when it was litellm. That overload is why a `command` install had
    no rescue direction at all — the variable that would have named its
    fallback was already naming its primary.

    One fallback per config, never a chain: if the primary and this both
    fail, the cause is almost always environmental (auth, host,
    workspace-trust) and a third CLI spends budget to reach the same error
    while making the install *look* better protected than it is."""
    return _get("DAIMON_LLM_COMMAND_FALLBACK") or None


def llm_command_fallback_output() -> str | None:
    """Output spec for the rescue CLI — same grammar as
    llm_command_output(). Carried separately because the rescue is a
    different binary: chaining a CLI that emits 'json:result' to one that
    emits raw text is the ordinary case, not the exotic one."""
    return _get("DAIMON_LLM_COMMAND_FALLBACK_OUTPUT") or None


def llm_command_fallback_input() -> str:
    """Input spec for the rescue CLI — same grammar as llm_command_input(),
    same #58 reasoning, its own variable for the same reason as the output
    axis above."""
    return _command_input_spec("DAIMON_LLM_COMMAND_FALLBACK_INPUT")
