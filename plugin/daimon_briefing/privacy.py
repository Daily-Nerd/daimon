"""Read-only tombstone residue audit (`daimon audit privacy`).

#583 shipped because forget's deletion contract was written against the
wrong surface set, and a passing test asserted the residue. This module
verifies the contract instead of trusting it: hash every plaintext field
on every surface and intersect with the forget ledger's tombstone set.

Read-only is load-bearing: sqlite opens use mode=ro URIs (a plain connect
CREATES a missing db), and the recall public API is never touched
(_ensure_fresh rebuilds). Findings carry hashes, never the text — audit
output gets re-serialized into checkpoints, so printing the value would
re-capture the thing the user deleted.
"""
import json
import sqlite3
import time
from pathlib import Path

from . import config, normalize, store

# Plaintext-bearing item fields. forget currently hashes only `text`
# (cli._cmd_forget, store.scrub_content_key, recall rebuild) — auditing
# quote/scene as well is deliberate: it detects the residue forget cannot
# yet reach, which is this tool's reason to exist.
_FIELDS = ("text", "quote", "scene")


def _hashes(item: dict) -> set[str]:
    out: set[str] = set()
    for field in _FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            out.add(normalize.content_key(value))
    return out


def _checkpoint_candidates() -> tuple[list[Path], list[Path]]:
    """(json surfaces, unknown files) across the flat dir + bucket dirs.

    Unlike store._plaintext_surfaces, files the walk does NOT recognise are
    returned, never dropped: a `latest.json.bak-*` full-checkpoint copy is
    exactly how plaintext escapes a suffix-filtered walk. `.chunk-cache/` is
    scanned separately at store level; `events.jsonl` is scanned as a note
    ledger, not an item surface."""
    known, unknown = [], []
    d = config.checkpoint_dir()
    try:
        entries = list(d.iterdir())
    except OSError:
        return [], []
    for entry in entries:
        try:
            if entry.is_file():
                if entry.suffix == ".json":
                    known.append(entry)
                elif entry.name != ".pointer.lock":
                    unknown.append(entry)
            elif entry.is_dir() and entry.name != ".chunk-cache":
                for p in entry.iterdir():
                    if not p.is_file():
                        continue
                    if p.suffix == ".json":
                        known.append(p)
                    elif p.name not in ("events.jsonl", ".pointer.lock"):
                        unknown.append(p)
        except OSError:
            unknown.append(entry)
    return known, unknown


def _scan_json_surface(path: Path, slug: str, keys: set[str],
                       surface: str, by_payload_only: bool = False) -> tuple[list[dict], bool | None]:
    """Findings + membership. None membership = unreadable (unscannable).

    Membership mirrors store.project_surfaces: bucket location OR payload
    project_slug — but unreadable files are SURFACED here, not silently
    excluded, because "could not check" must never fold into "clean".

    When by_payload_only=True, membership is solely by payload project_slug.
    This prevents author-dir-name collisions from false-matching team files."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    if not isinstance(payload, dict):
        return [], None
    if by_payload_only:
        if payload.get("project_slug") != slug:
            return [], False
    else:
        if path.parent.name != slug and payload.get("project_slug") != slug:
            return [], False
    findings: list[dict] = []
    for section, key in store._ITEM_LISTS:
        for item in ((payload.get(section) or {}).get(key) or []):
            if not isinstance(item, dict):
                continue
            for h in _hashes(item) & keys:
                findings.append({"path": str(path),
                                 "item_id": item.get("id"),
                                 "content_hash": h,
                                 "surface": surface})
    return findings, True


def _orphan_index_files() -> list[Path]:
    """Crashed rebuilds leave `recall.db.<pid>.tmp` (+`-journal`) beside the
    index — near-complete plaintext snapshots (four live multi-MB examples
    existed on the dev machine the day this was specced). They are the same
    sqlite shape, so they get the same scan, not a hand-wave."""
    db = config.recall_db()
    try:
        return sorted(p for p in db.parent.glob(db.name + ".*") if p.is_file())
    except OSError:
        return []


def _scan_recall_db(db_path: Path, slug: str,
                    keys: set[str]) -> tuple[list[dict], list[dict], bool | None]:
    """Scan the derived index WITHOUT the recall API (which rebuilds).

    A row match is only real residue if the stored fingerprint equals a
    freshly computed one — forget deletes recall rows LAZILY at the next
    rebuild, so a stale index legitimately still holds the value. Classing
    that as residue would cry wolf after every single forget."""
    from . import recall  # local import: only _fingerprint (pure read) is used
    if not db_path.exists():
        return [], [], True     # absent index holds nothing — vacuously clean
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            meta = dict(conn.execute("SELECT key, value FROM meta"))
            rows = conn.execute(
                "SELECT item_id, text, quote, scene, project_slug, author"
                " FROM items").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return [], [], None
    current = meta.get("fingerprint") == recall._fingerprint()
    union = store.all_forgotten_content_keys()
    self_author = store.project_slug(config.author())
    residue: list[dict] = []
    informational: list[dict] = []
    for item_id, text, quote, scene, row_slug, author in rows:
        hashes = _hashes({"text": text, "quote": quote, "scene": scene})
        if row_slug == slug:
            hit, surface = hashes & keys, "recall-index-residue"
        elif row_slug is None:
            hit, surface = hashes & union, "unattributed"
        elif author and store.project_slug(str(author)) != self_author:
            # Foreign-author rows answer to the machine-global union — the
            # shipped inbound gate's semantics (recall.py:244), mirrored.
            hit, surface = hashes & union, "recall-index-residue"
        else:
            continue    # another local project's row — its own audit's job
        for h in hit:
            finding = {"path": str(db_path), "item_id": item_id,
                       "content_hash": h, "surface": surface}
            if current:
                residue.append(finding)
            else:
                finding["surface"] = "stale-index-pending-rebuild"
                informational.append(finding)
    return residue, informational, True


def audit_project(project_dir=None) -> dict:
    slug = store.project_slug(project_dir)
    keys = store.forgotten_content_keys(project_dir=project_dir)
    result = {"slug": slug, "findings": [], "informational": [],
              "unscannable": [], "surfaces_scanned": 0,
              "zero_surfaces": False}
    if not slug:
        result["zero_surfaces"] = True
        result["cache"] = {"entries": 0, "oldest_days": None}
        return result
    known, unknown = _checkpoint_candidates()
    result["unscannable"].extend(str(p) for p in unknown)
    members = 0
    for path in known:
        findings, member = _scan_json_surface(path, slug, keys, "checkpoint")
        if member is None:
            result["unscannable"].append(str(path))
        elif member:
            members += 1
            result["findings"].extend(findings)
    result["surfaces_scanned"] = members
    result["zero_surfaces"] = members == 0
    try:
        team_files = sorted(config.team_dir().rglob("*.json"))
    except OSError:
        team_files = []
    for path in team_files:
        findings, member = _scan_json_surface(path, slug, keys, "team-copy",
                                              by_payload_only=True)
        if member is None:
            result["unscannable"].append(str(path))
        elif member:
            result["findings"].extend(findings)
    res, info, readable = _scan_recall_db(config.recall_db(), slug, keys)
    if readable is None:
        result["unscannable"].append(str(config.recall_db()))
    else:
        result["findings"].extend(res)
        result["informational"].extend(info)
    for orphan in _orphan_index_files():
        res, info, readable = _scan_recall_db(orphan, slug, keys)
        if readable is None:
            result["unscannable"].append(str(orphan))
            continue
        for f in res + info:
            f["surface"] = "orphan-tmp"
            result["findings"].append(f)
    # Notes: hashed WHOLE — catches a note that IS the value verbatim; a note
    # merely containing it is undetectable by hash (stated report limitation).
    events = config.checkpoint_dir() / slug / "events.jsonl"
    if events.exists():
        try:
            for line in events.read_text(encoding="utf-8").splitlines():
                try:
                    evt = json.loads(line)
                except ValueError:
                    continue
                note = evt.get("note") if isinstance(evt, dict) else None
                if isinstance(note, str) and note.strip():
                    h = normalize.content_key(note)
                    if h in keys:
                        result["findings"].append({
                            "path": str(events),
                            "item_id": evt.get("item_ref"),
                            "content_hash": h, "surface": "events-note"})
        except OSError:
            result["unscannable"].append(str(events))
    # Chunk cache: value-level detection impossible (cache keyed by chunk
    # text, values are substrings). Store-level honesty: entry count + real
    # oldest age — the reaper runs only on WRITES, so never assert bounded.
    cache_dir = config.checkpoint_dir() / ".chunk-cache"
    entries, oldest = 0, None
    try:
        for p in cache_dir.iterdir():
            if p.is_file():
                entries += 1
                age = (time.time() - p.stat().st_mtime) / 86400
                oldest = age if oldest is None else max(oldest, age)
    except OSError:
        pass
    result["cache"] = {"entries": entries, "oldest_days": oldest}
    return result
