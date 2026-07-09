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


def project_env(cwd):
    """Child env with DAIMON_PROJECT_DIR set to `cwd`, or None to inherit the
    parent env unchanged (pre-routing behavior when no cwd is known)."""
    return {**os.environ, "DAIMON_PROJECT_DIR": cwd} if cwd else None


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


def sweep_orphans(cli, cwd, session_id, transcript_path) -> None:
    """Catch-up sweep (#185) for a `claude --resume` fork's never-captured
    transcript: resuming a dead session forks it into a NEW session id with its
    own transcript file, but the host can fail to fire SessionEnd for that fork
    (e.g. the IDE window is killed again before a clean exit) — there is then
    nothing daimon can hook at ITS end, so recovery has to happen here, at the
    NEXT session's start instead.

    Scans the directory the CURRENT session's own transcript lives in (its
    siblings are every other session ever run against this project) for the
    most recently modified transcript that looks uncaptured: no checkpoint on
    disk for it at all, or a checkpoint OLDER than the transcript (written
    before the session actually ended). Spawns AT MOST ONE detached serialize
    per session start — the newest candidate — the exact same way
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


def spawn_serialize(cli, transcript_path, env) -> None:
    """Spawn `daimon serialize <transcript>` DETACHED so the hook returns
    immediately (serialization is a 30s+ LLM call). Raises OSError on spawn
    failure so the caller can log its own host-tagged diagnostic.

    The CLI logs its result lines to serialize.log first-class (FR #27), so we
    DON'T capture the child's stdout here — that double-logged results. stderr
    goes to a SEPARATE crash log to preserve uncaught tracebacks without
    duplicating the CLI's `error:` result lines in serialize.log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with (LOG_DIR / "serialize-crash.log").open("a", encoding="utf-8") as crashf:
        subprocess.Popen(
            [cli, "serialize", transcript_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=crashf,
            start_new_session=True,  # survive the exiting parent
            env=env,
        )
