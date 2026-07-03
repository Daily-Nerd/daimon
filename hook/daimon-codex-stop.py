#!/usr/bin/env python3
"""Codex Stop hook: checkpoint the current transcript opportunistically.

Codex currently exposes `Stop` at turn scope, not a clean session-end event.
Serializing after every turn would be expensive, so this hook is throttled by
DAIMON_CODEX_MIN_SERIALIZE_INTERVAL (default: 300 seconds per session). Set it
to 0 to serialize on every Stop, or DAIMON_CODEX_SERIALIZE_ON_STOP=0 to disable
this hook while keeping SessionStart briefing injection.

The LLM work runs detached via `daimon serialize <transcript_path>` so
the hook returns immediately. Diagnostics land in ~/.daimon/logs/serialize.log.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Shared helpers live in a same-dir sibling module (see _daimon_hook_lib.py).
# A stale/partial install may lack it: fail open with a logged one-liner rather
# than crash. _fallback_log below mirrors lib.log for exactly that window.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _daimon_hook_lib as lib
except Exception:  # noqa: BLE001 — missing/corrupt lib must never crash the hook
    lib = None

STATE_DIR = Path.home() / ".daimon" / "codex"


def _fallback_log(line: str) -> None:
    """Best-effort serialize.log write when the shared lib is unavailable
    (stale/partial install). Mirrors lib.log so a broken install still leaves a
    breadcrumb instead of a crash. Never raises."""
    try:
        log_dir = Path.home() / ".daimon" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {line}\n")
    except OSError:
        pass


def _enabled() -> bool:
    if lib.disabled():
        return False
    val = os.environ.get("DAIMON_CODEX_SERIALIZE_ON_STOP", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def _interval_seconds() -> int:
    raw = os.environ.get("DAIMON_CODEX_MIN_SERIALIZE_INTERVAL", "300").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _safe_name(session_id: str) -> str:
    return session_id.replace("/", "_").replace("\\", "_").replace("..", "_")


def _marker_path(session_id: str) -> Path:
    return STATE_DIR / f"{_safe_name(session_id)}.last-stop"


def _should_spawn(session_id: str) -> bool:
    interval = _interval_seconds()
    if interval <= 0:
        return True
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = _marker_path(session_id)
        now = time.time()
        if marker.exists() and now - marker.stat().st_mtime < interval:
            return False
        return True
    except OSError:
        return True


def _mark_spawned(session_id: str) -> None:
    if _interval_seconds() <= 0:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _marker_path(session_id).write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    if lib is None:
        _fallback_log("codex-stop: hook library missing (_daimon_hook_lib.py) - skipped")
        return 0
    if not _enabled():
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        lib.log("codex-stop: unparseable stdin payload - skipped")
        return 0

    transcript_path = payload.get("transcript_path", "")
    if not transcript_path or not Path(transcript_path).exists():
        lib.log(f"codex-stop: transcript not found ({transcript_path!r}) - skipped")
        return 0

    session_id = str(payload.get("session_id") or Path(transcript_path).stem)
    if not _should_spawn(session_id):
        lib.log(f"codex-stop: skipped serialize for {session_id} (throttled)")
        return 0

    cli = lib.resolve_cli()
    if cli is None:
        lib.log("codex-stop: `daimon` CLI not found - checkpoint skipped")
        return 0

    cwd = str(payload.get("cwd") or "").strip()
    child_env = lib.project_env(cwd)
    try:
        lib.spawn_serialize(cli, transcript_path, child_env)
        _mark_spawned(session_id)
        lib.log(f"codex-stop: spawned serialize for {session_id} (project: {cwd or '?'})")
    except OSError as exc:
        lib.log(f"codex-stop: spawn failed ({type(exc).__name__}: {exc})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        if lib is not None:
            lib.log(f"codex-stop: hook error ({type(exc).__name__}: {exc})")
        else:
            _fallback_log(f"codex-stop: hook error ({type(exc).__name__}: {exc})")
        sys.exit(0)
