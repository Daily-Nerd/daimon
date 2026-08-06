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
                       surface: str) -> tuple[list[dict], bool | None]:
    """Findings + membership. None membership = unreadable (unscannable).

    Membership mirrors store.project_surfaces: bucket location OR payload
    project_slug — but unreadable files are SURFACED here, not silently
    excluded, because "could not check" must never fold into "clean"."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], None
    if not isinstance(payload, dict):
        return [], None
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


def audit_project(project_dir=None) -> dict:
    slug = store.project_slug(project_dir)
    keys = store.forgotten_content_keys(project_dir=project_dir)
    result = {"slug": slug, "findings": [], "informational": [],
              "unscannable": [], "surfaces_scanned": 0,
              "zero_surfaces": False, "cache": {}}
    if not slug:
        result["zero_surfaces"] = True
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
    return result
