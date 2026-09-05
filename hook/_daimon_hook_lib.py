"""Shared stdlib-only helpers for the daimon host-adapter hooks.

The hook scripts (Claude Code / Codex / Gemini SessionStart + SessionEnd) run
as standalone scripts from their own install dir, inside whatever interpreter
the host invokes — they CANNOT import the daimon_briefing package, which lives
in an isolated uv-tool venv. This module holds the helpers all three adapters
would otherwise duplicate verbatim. It is loaded by same-dir lookup:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _daimon_hook_lib as lib

Host-specific behavior deliberately stays in the individual hooks — Gemini's
pure-JSON stdout, Codex's additionalContext envelope + Stop throttling, and
Codex's mtime-only age line. Only what is genuinely identical lives here.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Prefer the `daimon` command; fall back to the deprecated `daimon-briefing`
# alias so a stale hook meeting a renamed binary (or vice-versa) still resolves.
FALLBACKS = [
    Path.home() / ".local" / "bin" / "daimon",
    Path.home() / ".local" / "bin" / "daimon-briefing",
]

LOG_DIR = Path.home() / ".daimon" / "logs"

# #605: the crash sink grows only on crashes, but it grew FOREVER — nothing
# reaped it and, until forget started purging it, nothing deleted it either.
# The cap bounds the plaintext that can accumulate between two forgets. Trim
# keeps the TAIL because `status` reports the LAST crash: the newest
# traceback is the one anyone is debugging.
CRASH_LOG_MAX_BYTES = 262144
CRASH_LOG_KEEP_BYTES = 65536


def _load_redact():
    """Load the shipped redaction module (#104) from THIS file's own directory,
    where `daimon hooks install` places redact.py. File-location import (never
    `import redact`) so it never depends on sys.path state and never collides
    with an unrelated top-level module. None when absent — a stale install
    missing redact.py; the hook write sites then SKIP rather than persist raw
    text (#109). Patterns live ONLY in redact.py (scar 0022) — never copied
    here, and a test keeps the shipped copy byte-identical to the package's."""
    path = Path(__file__).resolve().parent / "redact.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_daimon_hooks_redact", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 — a broken module must never crash a hook
        return None


_REDACT = _load_redact()


def redaction_available() -> bool:
    """True when the shipped redaction module loaded. A hook MUST gate its
    write sites on this and skip when False: #104's disk guarantee (a quoted
    secret never persists) outranks accumulation/probe availability (#109)."""
    return _REDACT is not None


def redact_text(text: str) -> str:
    """Best-effort capture-time secret scrub, delegating to the shipped redact
    module (#104). Returns text UNCHANGED when the module is unavailable — the
    caller gates on redaction_available() first, so that path never persists.
    The module's own per-pattern fail-open guarantees a scrub, never a raise."""
    if _REDACT is None:
        return text
    scrubbed, _counts = _REDACT.redact_text(text)
    return scrubbed


def disabled() -> bool:
    """True when the DAIMON_DISABLE kill switch is set (1/true/yes/on)."""
    return os.environ.get("DAIMON_DISABLE", "").strip() in ("1", "true", "yes", "on")


def resolve_cli():
    """Locate the daimon CLI: `daimon`, then the deprecated `daimon-briefing`
    alias, then the well-known ~/.local/bin fallbacks. None when nothing resolves."""
    return (
        shutil.which("daimon")
        or shutil.which("daimon-briefing")
        or next((str(p) for p in FALLBACKS if p.exists()), None)
    )


def payload() -> dict:
    """Hook payload from stdin ({session_id, transcript_path, cwd, ...}).

    Unparseable/empty stdin degrades to {} — the caller then behaves exactly as
    it did before per-project routing (global latest), instead of dying."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def checkpoint_dir() -> Path:
    raw = os.environ.get("DAIMON_CHECKPOINT_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "checkpoints"


def slug(project_dir: str) -> str | None:
    """cwd -> filesystem-safe slug, same scheme as daimon_briefing.store.project_slug.

    Duplicated here because the hooks are standalone stdlib-only scripts that
    cannot import the package (isolated uv-tool venv). Keep in sync with
    daimon_briefing.store.project_slug."""
    s = (project_dir or "").strip()
    if not s:
        return None
    return re.sub(r"[^\w-]", "-", s) or None


def created_epoch(created) -> float | None:
    """Epoch for a checkpoint's ISO-8601 `created` stamp, or None if absent/bad.

    Duplicated here because the hooks are stdlib-only and cannot import the
    package. Keep in sync with daimon_briefing.store._created_epoch."""
    if not isinstance(created, str):
        return None
    try:
        ts = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return ts.replace(tzinfo=timezone.utc).timestamp()


def age_line(latest: Path) -> str:
    """Human age of the checkpoint, so a stale briefing is visibly stale. Age
    prefers the written `created` stamp (which survives pointer rotation) and
    falls back to file mtime for legacy checkpoints (#93)."""
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    epoch = created_epoch(data.get("created"))
    ref = epoch if epoch is not None else latest.stat().st_mtime
    secs = max(0, time.time() - ref)
    if secs < 3600:
        age = f"{int(secs // 60)}m"
    elif secs < 86400:
        age = f"{secs / 3600:.1f}h"
    else:
        age = f"{secs / 86400:.1f}d"
    session_id = data.get("session_id", "?")
    return f"(checkpoint: {session_id}, written {age} ago)"


def project_env(cwd, host=None):
    """Child env with code-owned project/host capture hints when known."""
    if not cwd and not host:
        return None
    env = dict(os.environ)
    if cwd:
        env["DAIMON_PROJECT_DIR"] = cwd
    if host:
        env["DAIMON_CAPTURE_HOST"] = host
    return env


def log(line: str) -> None:
    """Append a UTC-timestamped line to serialize.log. Never raises — logging
    must not break a hook. The caller supplies the host tag as part of `line`."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (LOG_DIR / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


def spawn_heal(cli, cwd) -> None:
    """Fire a one-shot self-heal of the most recent FAILED serialize, DETACHED so
    it adds ~0 latency and never blocks the session (#26). Fail-open: a heal that
    can't start must not disturb the briefing. The child routes by the failed
    session's OWN project (recovered from serialize.log), NOT this cwd —
    DAIMON_PROJECT_DIR is forwarded only so heal honors log/checkpoint overrides."""
    if cli is None:
        return
    try:
        subprocess.Popen(
            [cli, "heal"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survive the exiting parent
            env=project_env(cwd),
        )
    except OSError:
        pass


def team_dir() -> Path:
    raw = os.environ.get("DAIMON_TEAM_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "team"


def has_team_remote() -> bool:
    """Cheap gate for the opportunistic team sync (#113): any team-dir entry
    that looks like a sidecar clone (has a .git). Pure dir scan — no git, no
    package import — so the check costs ~nothing when the team feature is
    unused. Never raises."""
    try:
        return any(
            p.is_dir() and p.name != "local" and (p / ".git").exists()
            for p in team_dir().iterdir()
        )
    except OSError:
        return False


def spawn_team_sync(cli, cwd) -> None:
    """Fire `daimon team sync` DETACHED at SessionStart (#113), mirroring
    spawn_heal: ~0 latency, NEVER blocks the briefing, fail-open. Gated on
    has_team_remote() so machines that never ran `daimon team init` pay only
    a directory scan."""
    if cli is None or not has_team_remote():
        return
    try:
        subprocess.Popen(
            [cli, "team", "sync"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # survive the exiting parent
            env=project_env(cwd),
        )
    except OSError:
        pass


_ORPHAN_MAX_AGE_SECONDS = 14 * 24 * 3600  # 14 days — bounds the sweep's directory scan

# Duplicated from daimon_briefing.ledger's _RESULT_ERR_RE / _HEAL_TRANSCRIPT_RE /
# _LEDGER_OK_RE (this module is stdlib-only and cannot import the package — see
# module docstring). Keep in sync with ledger.py if those line shapes change.
_LOG_RESULT_OK_RE = re.compile(r"^wrote checkpoint: (.+?) \(took \d+s\)")
_LOG_RESULT_ERR_RE = re.compile(r"^error: .*?(?: after (\d+)s)?$")
_LOG_ERR_TRANSCRIPT_RE = re.compile(r"\(transcript: (.+?)\)(?: after \d+s|$)")


def hung_after_seconds() -> int:
    """Age (seconds) past which a serialize heartbeat is treated as hung or
    killed rather than still-running. A second copy of
    daimon_briefing.config.hung_after_seconds(), because hooks cannot import
    the package; tests lock the two to behavioral equality.

    Reads the PROCESS env only. The package form also consults ~/.daimon/env,
    so a DAIMON_HUNG_AFTER set only in that file is invisible here — the same
    documented boundary as LOG_DIR, which the hooks hardcode for serialize.log
    while the CLI honors DAIMON_LOG_DIR. A disagreement costs at most a
    delayed or an extra sweep, never a lost transcript. crash_log_path() is
    the one exception: that file is inside the deletion contract (#605), so
    the writer must at least read the process env the deleter reads."""
    try:
        return int(os.environ.get("DAIMON_HUNG_AFTER") or "1800")
    except ValueError:
        return 1800


def _in_flight_stems() -> set:
    """Transcript stems (session ids) with a LIVE serialize right now (#545).

    A serialize stamps a heartbeat named for its session (ledger.touch_heartbeat)
    and only writes its checkpoint at the very end, so between those two moments
    the transcript looks exactly like a never-captured orphan: no checkpoint on
    disk, nothing in the failure ledger. sweep_orphans then spawned a SECOND
    full serialize of the same transcript, and whichever finished last
    overwrote the other — last-writer-wins, uncorrelated with quality (field
    case: the surviving checkpoint had 28 decisions against the discarded
    one's 32).

    sweep_orphans' own idempotency argument does not cover this: the #185
    identical-bytes guard compares against an EXISTING checkpoint, and an
    unfinished serialize has not written one yet, so the guard cannot fire.

    A STALE stamp is deliberately not in-flight — that is a crashed serialize
    and heal's territory, and treating it as live would make the transcript
    permanently unsweepable, strictly worse than the double-spawn this
    prevents. Same liveness bar as ledger.serialize_in_flight, but keyed
    per-session (the stamp's filename) rather than per-project (its content),
    so a live serialize for one session never starves an orphaned sibling.

    Fails open to an empty set on any read error, matching
    _failed_session_stems: a missing heartbeat dir must never block the
    genuine #185/#188 recovery sweep."""
    stems = set()
    try:
        ceiling = hung_after_seconds()
        now = time.time()
        for p in (LOG_DIR / "heartbeats").iterdir():
            try:
                if now - p.stat().st_mtime <= ceiling:
                    stems.add(p.name)
            except OSError:
                continue
    except OSError:
        return set()
    return stems


def _serialize_in_flight(transcript_path) -> bool:
    """True when a LIVE serialize is already running for this transcript
    (#813). Fails OPEN — an unreadable heartbeat dir must never block a
    genuine capture, which is the same direction `_in_flight_stems` takes."""
    try:
        return Path(transcript_path).stem in _in_flight_stems()
    except Exception:  # noqa: BLE001 — a broken guard must not cost a capture
        return False



def _failed_session_stems() -> set:
    """Transcript stems (session ids) whose LATEST serialize.log outcome is a
    recorded failure — a lightweight, read-only echo of
    daimon_briefing.ledger._session_ledger's error/success fold, scoped to just
    what sweep_orphans needs (#299): a transcript that was ATTEMPTED and FAILED
    belongs to `heal` (already capped at one retry, #15/#26), not to this sweep,
    which owns only the never-attempted case (#185/#188). A later success for
    the same stem clears the failure, matching the ledger's own fold.

    Fails open to an empty set on any read/parse error, so a broken or missing
    ledger never blocks the genuine #185/#188 recovery sweep — it just leaves
    the sweep unable to tell 'failed' from 'never attempted', which is exactly
    today's behavior without this check."""
    try:
        text = (LOG_DIR / "serialize.log").read_text(encoding="utf-8")
        state = {}
        for line in text.splitlines()[-200:]:  # tail is plenty; matches ledger.py
            line = line.strip()
            m = _LOG_RESULT_OK_RE.match(line)
            if m:
                state[Path(m.group(1)).stem] = "success"
                continue
            if _LOG_RESULT_ERR_RE.match(line):
                tm = _LOG_ERR_TRANSCRIPT_RE.search(line)
                if tm:
                    state[Path(tm.group(1)).stem] = "error"
        return {stem for stem, outcome in state.items() if outcome == "error"}
    except Exception:  # noqa: BLE001 — fail-open, see docstring
        return set()


def sweep_orphans(cli, cwd, session_id, transcript_path) -> None:
    """Catch-up sweep for a transcript that was never captured because the
    host's own end-of-session capture path silently missed it. Two callers,
    same shared logic (host-agnostic by design — nothing below is Claude- or
    Codex-specific):

    - Claude Code (#185): resuming a dead session forks it into a NEW session
      id with its own transcript file, but the host can fail to fire
      SessionEnd for that fork (e.g. the IDE window is killed again before a
      clean exit) — there is then nothing daimon can hook at ITS end.
    - Codex (#188): the Stop hook throttles serialize per session
      (`DAIMON_CODEX_MIN_SERIALIZE_INTERVAL`), so a session whose final
      activity lands inside the throttle window and never resumes is never
      serialized again.

    Either way, recovery has to happen here, at the NEXT session's start,
    instead of at the missed end-of-session event.

    Scans the directory the CURRENT session's own transcript lives in (its
    siblings are every other session ever run against this project) for the
    most recently modified transcript that looks uncaptured: no checkpoint on
    disk for it at all, or a checkpoint OLDER than the transcript (written
    before the session actually ended) — EXCLUDING any candidate the ledger
    already shows as attempted-and-failed (#299, via _failed_session_stems):
    that transcript is heal's territory now (capped at one retry, #15/#26),
    not an un-attempted orphan, so the sweep must not re-spawn it every
    session start for up to 14 days. Spawns AT MOST ONE detached serialize per
    session start — the newest remaining candidate — the exact same way
    `daimon-session-end.py` does.

    Idempotent by construction, so the mtime heuristic alone is enough: the
    spawned serialize hits the #185 identical-bytes guard and no-ops if the
    transcript actually WAS captured, and the existing too-short skip handles
    noise/tiny files. Fail-open — this must run AFTER briefing emission and
    never affect it, so every error here is swallowed, at most logged."""
    if cli is None or not transcript_path:
        return
    try:
        current = Path(transcript_path)
        directory = current.parent
        if not directory.is_dir():
            return
        cutoff = time.time() - _ORPHAN_MAX_AGE_SECONDS
        ckpt_dir = checkpoint_dir()
        failed_stems = _failed_session_stems()
        in_flight_stems = _in_flight_stems()
        best_path = None
        best_mtime = None
        for candidate in directory.glob("*.jsonl"):
            if candidate.stem == session_id or candidate == current:
                continue  # never sweep the session that is starting right now
            try:
                mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                continue  # outside the bounded scan window
            if candidate.stem in failed_stems:
                continue  # attempted and failed -> heal's job, not an orphan (#299)
            if candidate.stem in in_flight_stems:
                continue  # serialize RUNNING right now -> not an orphan yet (#545)
            ckpt_path = ckpt_dir / f"{candidate.stem}.json"
            try:
                if ckpt_path.stat().st_mtime >= mtime:
                    continue  # already captured at/after this transcript's mtime
            except OSError:
                pass  # no checkpoint on disk at all -> orphan candidate
            if best_mtime is None or mtime > best_mtime:
                best_path, best_mtime = candidate, mtime
        if best_path is None:
            return
        spawn_serialize(cli, str(best_path), project_env(cwd))
        # Ledger-shaped spawn line: ledger._SPAWN_RE already lists the
        # `session-start:` prefix (for the #26 retry marker), and the `spawned`
        # verb keeps this distinct from that one-retry-ever marker. Being a
        # first-class ledger citizen means a catch-up child that hangs or
        # crashes past the ceiling surfaces in `daimon status`, and — thanks
        # to the trailing (transcript: ...) group (#28), same shape as
        # daimon-session-end.py's spawn line — stays healable instead of
        # invisible. The child's own result line (wrote checkpoint / skipped /
        # error) resolves the pair exactly like a session-end spawn.
        log(
            f"session-start: spawned serialize for {best_path.stem} "
            f"(reason: catch-up-orphan, project: {cwd or '?'}) "
            f"(transcript: {best_path})"
        )
    except Exception as exc:  # noqa: BLE001 — the sweep must never break the briefing
        log(f"session-start: catch-up sweep failed ({type(exc).__name__}: {exc})")


def _env_file_path() -> Path:
    """Mirror of daimon_briefing.config._env_file_path."""
    raw = os.environ.get("DAIMON_ENV_FILE")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "env"


def _env_file_values() -> dict:
    """Mirror of daimon_briefing.config._file_values — KEY=VALUE lines, with
    the `export ` prefix, surrounding quotes, blank lines and `#` comments
    tolerated. Copied rather than imported because hooks run standalone and
    cannot import the package; config.py is the CANONICAL form and this copy
    must follow it line-form for line-form. A behavioral-equality test over
    a probe table of line shapes guards the drift."""
    try:
        text = _env_file_path().read_text(encoding="utf-8")
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


def _config_get(name: str) -> str:
    """Mirror of daimon_briefing.config._get: process env WINS, env file is
    the fallback. Present-but-empty in the process env is a value, so it
    shadows the file exactly as it does on the package side (scar 0036 in
    reverse — the file is consulted more often than anyone expects)."""
    val = os.environ.get(name)
    if val is not None:
        return val
    return _env_file_values().get(name) or ""


def _config_flag(name: str) -> bool:
    """Mirror of daimon_briefing.config._flag. Same four words, so an operator
    who learned `DAIMON_DISABLE=true` does not discover that this one alone
    insists on `1`."""
    return _config_get(name).strip() in ("1", "true", "yes", "on")


def crash_log_path() -> Path:
    """The crash sink, resolved the way the DELETER resolves it.

    serialize-crash.log is inside the deletion contract (#605): `daimon
    forget` purges it through config.log_dir(). A path resolved any other way
    here has the child filling one directory while the purge reports cleanly
    on another — silently, since a one-file purge reporting zero is
    indistinguishable from an empty log dir. That is the #607 writer/deleter
    split, one directory over.

    So this reproduces config.log_dir() EXACTLY, including the parts that
    look like bugs: no strip (config does not strip, so "   " is a relative
    directory named three spaces and the writer must agree), and the env-file
    fallback, which is the channel a GUI-launched host actually uses — shell
    exports never reach it. Resolved per call, like config's, so a moved HOME
    cannot split the two.

    serialize.log keeps the hardcoded LOG_DIR: nothing deletes it, so nothing
    has to agree about where it is (hung_after_seconds' documented boundary)."""
    raw = _config_get("DAIMON_LOG_DIR")
    base = Path(raw).expanduser() if raw else Path.home() / ".daimon" / "logs"
    return base / "serialize-crash.log"


def trim_crash_log(path) -> None:
    """Cap the crash sink at CRASH_LOG_MAX_BYTES, keeping its last
    CRASH_LOG_KEEP_BYTES (#605). Called at the spawn seams — the only moments
    daimon touches this file — so the bound costs no separate reaper.

    Rewritten IN PLACE rather than through a temp-and-rename: the file is an
    open append target for any child still running, and replacing the inode
    would send those writes to a file nobody reads. The cut lands mid-line;
    harmless, because _crash_log_info anchors on the `--- crash ` header and
    ignores everything before the last one.

    Accepted, deliberately: in place means unlocked, so a crash written by a
    concurrent child between the read() and the truncate() is cut away. That
    is diagnostics loss, bounded to the rare overlap of a spawn with another
    child's death throes, and the alternative (a lock in a fail-open hook
    seam, or the inode swap above) costs more than the traceback is worth.
    llm._log_backend_stderr made the same call for backend-stderr.log.

    Best-effort and silent: the log is diagnostics, a capture is the product,
    and a trim that raises into a spawn seam would cost the capture."""
    try:
        size = path.stat().st_size
        if size <= CRASH_LOG_MAX_BYTES:
            return
        with path.open("r+b") as f:
            f.seek(size - CRASH_LOG_KEEP_BYTES)
            tail = f.read()
            f.seek(0)
            f.write(tail)
            f.truncate()
    except Exception:  # noqa: BLE001 — diagnostics must never block a spawn
        pass


def spawn_serialize(cli, transcript_path, env):
    """Spawn `daimon serialize <transcript>` DETACHED so the hook returns
    immediately (serialization is a 30s+ LLM call). Raises OSError on spawn
    failure so the caller can log its own host-tagged diagnostic.

    Returns False when the spawn was SKIPPED because a serialize for this same
    transcript is already in flight (#813); any other return means it spawned.
    Callers must test `is False` and log a skip line instead of a spawn line —
    see the note on the sentinel below.

    The CLI logs its result lines to serialize.log first-class (FR #27), so we
    DON'T capture the child's stdout here — that double-logged results. stderr
    goes to a SEPARATE crash log to preserve uncaught tracebacks without
    duplicating the CLI's `error:` result lines in serialize.log.

    #813, the in-flight guard: #545 established that two serializes of one
    transcript are last-writer-wins and uncorrelated with quality, built
    `_in_flight_stems` for it, and wired it to `sweep_orphans` alone. The two
    Codex hook spawn paths never consulted it, so a `Stop` child still running
    when `SessionEnd` fires produced two full runs of the same transcript. The
    check belongs HERE, with the spawn, so every caller inherits it rather than
    each one having to remember.

    Keyed on the transcript STEM, never on a host payload's session id. The CLI
    derives its session id from the filename (`cli:233`, `session_id =
    path.stem`) and that is the name `ledger.touch_heartbeat` stamps, while a
    Codex payload's `session_id` is a different string entirely. Keying on the
    payload id would match no heartbeat ever and leave a guard that looks
    implemented and does nothing.

    Known narrowing, not a total fix: the child stamps its first heartbeat
    after interpreter start and its own preflight, so two spawns inside that
    startup window both see an empty set and both run. Stamping from the PARENT
    would close it and open something worse — a child that dies before its
    first touch would leave a stamp that blocks the session for the whole
    hung-after ceiling. The observed race is minutes wide; this narrows it to
    seconds, and `heal` remains the answer for the rest.
    """
    if _serialize_in_flight(transcript_path):
        return False
    crash = crash_log_path()
    crash.parent.mkdir(parents=True, exist_ok=True)
    trim_crash_log(crash)
    with crash.open("a", encoding="utf-8") as crashf:
        subprocess.Popen(
            [cli, "serialize", transcript_path],
            stdin=subprocess.DEVNULL,
            # #939: a container host's only observable surface is the stream
            # its runtime captures, so a result line in serialize.log alone is
            # byte-identical to a dead feature. None inherits fd 1, which in a
            # container belongs to the runtime's log pipe and stays open for
            # the life of the container, so the DETACHED child still reaches
            # it. Off by default: on a terminal host this prints capture
            # results into the user's shell minutes after the session ended.
            # stdout rather than stderr because #194 made stderr the crash
            # surface; see the comment below and scar candidate
            # serialize-outcomes-must-not-mirror-to-stderr.
            stdout=None if _config_flag("DAIMON_LOG_STDOUT")
            else subprocess.DEVNULL,
            stderr=crashf,
            start_new_session=True,  # survive the exiting parent
            env=env,
        )
