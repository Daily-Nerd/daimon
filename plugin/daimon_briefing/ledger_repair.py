"""Ledger repair and the one single-key scrub forget shares with it (#1132 2c-2)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from . import config, jsonl, normalize, store, surfaces


class Purged(NamedTuple):
    """What `forget_quarantined_lines` did: envelope rows removed and kept."""
    purged: int
    kept: int


def _bucket_dir(project_dir) -> Path | None:
    # #948: resolve BEFORE slugging, like every ledger path in this package.
    slug = store.project_slug(config.resolve_project_dir(project_dir))
    return config.checkpoint_dir() / slug if slug else None


def sidecars(bucket: Path) -> list[Path]:
    """The quarantine sidecar files a bucket holds, by name."""
    try:
        return sorted(p for p in bucket.iterdir()
                      if p.is_file() and surfaces.is_quarantine_sidecar(p.name))
    except OSError:
        return []


def _spellings(value: str) -> tuple[str, ...]:
    """A value as it sits inside a torn JSON line: escaped for a string
    (quotes, backslashes, newlines), with and without \\uXXXX for non-ASCII."""
    return (value, json.dumps(value, ensure_ascii=False)[1:-1],
            json.dumps(value)[1:-1])


def matched_keys(text: str, keys) -> set[str]:
    """The members of `keys` a quarantined line carries: the canonical key of
    the whole line, or of any JSON string literal inside it."""
    return set(keys) & ({normalize.content_key(text)}
                        | normalize.literal_content_keys(text))


def holds_forgotten_key(text: str, keys) -> bool:
    return bool(matched_keys(text, keys))


def forget_quarantined_lines(content_key: str, *, text: str = "",
                             project_dir=None) -> Purged:
    """Purge the quarantine-sidecar envelope rows that hold a forgotten value.

    Two matchers, either one purges a row. With `text` (forget has the value)
    a row whose `text` contains the value, raw or JSON-escaped, goes. Without
    it (repair has only the tombstone) a row goes when the whole line or a
    JSON string literal inside it folds to `content_key`: matching is by key
    only, so a value fused into a longer string of a torn row is not found.
    Every other row, and every line that is not an envelope row, is written
    back untouched. Returns how many rows went and how many stayed."""
    bucket = _bucket_dir(project_dir)
    if bucket is None or not content_key:
        return Purged(0, 0)
    spellings = _spellings(text) if text else ()
    purged = kept = 0

    def drop(line, row):
        nonlocal kept
        body = row.get("text") if isinstance(row, dict) else None
        if not isinstance(body, str):
            return line
        if (any(s in body for s in spellings)
                or holds_forgotten_key(body, {content_key})):
            return None
        kept += 1
        return line

    def write(target, blob):
        store._atomic_write(target, blob, errors="surrogateescape")

    for path in sidecars(bucket):
        before = kept
        try:
            purged += jsonl.rewrite(path, drop, write=write)
        except OSError:
            kept = before
    return Purged(purged, kept)
