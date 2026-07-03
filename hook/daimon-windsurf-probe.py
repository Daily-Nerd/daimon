#!/usr/bin/env python3
"""Windsurf Cascade hook probe — capture the REAL hook payload before we build.

The Windsurf adapter (#35) needs ground truth: the Cascade Hooks docs
(https://docs.windsurf.com/windsurf/cascade/hooks) are marked draft, so before
writing `daimon-windsurf-hooks.py` we capture what a real `post_cascade_response`
event actually delivers — payload shape, transcript_path location, and a sample
of the transcript's JSONL step format.

HOW TO RUN (takes ~5 minutes):

1. Save this file anywhere, e.g. `~/daimon-windsurf-probe.py`.
2. Register it as a Cascade hook (user-level hooks JSON — see the docs page
   above for the exact file location on your platform), for the
   `post_cascade_response` event, command: `python3 ~/daimon-windsurf-probe.py`.
   If your Windsurf build offers `post_cascade_response_with_transcript`,
   register that variant instead (richer payload).
3. Open Windsurf, run ONE short Cascade turn (any prompt).
4. Send back the folder `~/daimon-windsurf-probe/` (it contains the captured
   payload and a small transcript sample — a few lines, nothing sensitive
   beyond your one test prompt).

The probe is fail-open: it always exits 0 and never blocks Cascade.

SECOND MODE — state.vscdb scan (run directly in a terminal, no hook needed):

    python3 daimon-windsurf-probe.py --scan-vscdb [TRAJECTORY_ID] [--db PATH]...

Windsurf/Devin stores Cascade state in VS Code-style sqlite databases
(`state.vscdb`, table ItemTable). This mode reports which keys hold
conversation data — key names, value sizes, and (when TRAJECTORY_ID is given)
a small head of the matching value so the adapter's parser can be built
against the real turn structure. Whole conversation blobs are NEVER copied.
Without --db it scans the default Devin/Windsurf storage roots.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path.home() / "daimon-windsurf-probe"
TRANSCRIPT_SAMPLE_LINES = 10
_MATCH_HEAD_CHARS = 400
_KEY_HINT = ("cascade", "chat", "trajector", "conversation", "memor")


def _default_dbs():
    """Known state.vscdb locations across the rebrand (Devin) and the legacy
    name (Windsurf), macOS + Linux layouts. Globs are cheap; absent roots
    yield nothing."""
    roots = []
    for app in ("Devin", "Windsurf"):
        roots.append(Path.home() / "Library" / "Application Support" / app / "User")
        roots.append(Path.home() / ".config" / app / "User")
    dbs = []
    for root in roots:
        dbs.extend(root.glob("globalStorage/state.vscdb"))
        dbs.extend(root.glob("workspaceStorage/*/state.vscdb"))
    return dbs


def _read_item_table(db_path: Path):
    """Yield (key, value-bytes) rows, read-only. A locked/foreign db yields
    nothing — the probe never writes and never raises."""
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            yield from conn.execute("SELECT key, value FROM ItemTable")
        finally:
            conn.close()
    except sqlite3.Error:
        return


def _scan_vscdb(trajectory_id: str | None, db_paths) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [f"vscdb scan {stamp}  trajectory_id={trajectory_id or '-'}"]
    dbs = [Path(p).expanduser() for p in db_paths] if db_paths else _default_dbs()
    for db in dbs:
        lines.append(f"\n=== {db}  (exists={db.is_file()})")
        if not db.is_file():
            continue
        for key, value in _read_item_table(db):
            raw = value if isinstance(value, (bytes, bytearray)) else str(value).encode()
            text = raw.decode("utf-8", errors="replace")
            interesting = any(h in key.lower() for h in _KEY_HINT)
            hit = bool(trajectory_id) and trajectory_id in text
            if hit:
                idx = text.find(trajectory_id)
                head = text[max(0, idx - 100):idx + _MATCH_HEAD_CHARS]
                lines.append(f"MATCH {key}  ({len(raw)} bytes)")
                lines.append(f"  …{head}…")
            elif interesting:
                lines.append(f"key {key}  ({len(raw)} bytes)")
    report = OUT_DIR / f"vscdb-scan-{stamp}.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"daimon probe: scan written to {report}")
    return 0


def main() -> int:
    if "--scan-vscdb" in sys.argv[1:]:
        args = sys.argv[1:]
        args.remove("--scan-vscdb")
        dbs = []
        while "--db" in args:
            i = args.index("--db")
            dbs.append(args[i + 1])
            del args[i:i + 2]
        trajectory_id = args[0] if args else None
        return _scan_vscdb(trajectory_id, dbs)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    raw = sys.stdin.read()
    (OUT_DIR / f"payload-{stamp}.raw").write_text(raw, encoding="utf-8")

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        (OUT_DIR / f"payload-{stamp}.note").write_text(
            "stdin was not JSON — raw capture only\n", encoding="utf-8")
        return 0

    (OUT_DIR / f"payload-{stamp}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # Any *path* field that exists on disk gets a head sample — the adapter
    # needs the transcript's per-line step schema, not the whole conversation.
    if isinstance(payload, dict):
        for key, value in payload.items():
            if not (isinstance(value, str) and "path" in key.lower()):
                continue
            p = Path(value).expanduser()
            if not p.is_file():
                continue
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    head = [next(f) for _ in range(TRANSCRIPT_SAMPLE_LINES)]
            except StopIteration:
                head = p.read_text(encoding="utf-8",
                                   errors="replace").splitlines(keepends=True)
            except OSError:
                continue
            (OUT_DIR / f"sample-{key}-{stamp}.txt").write_text(
                "".join(head), encoding="utf-8")

    print(f"daimon probe: captured to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — a probe must never break the host
        sys.exit(0)
