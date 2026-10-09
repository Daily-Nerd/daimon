"""Where does the recall index say an item id sits? (#1132 PR 11a).

A read-only locator over the derived recall index. It answers one question,
"which sessions hold these ids", and nothing else: the value of an item comes
from the session file the index names, through the view, never from the row
(the one exception is a row whose file is not local, which the view classifies
at read time). It opens the index with `mode=ro`, so it can neither create nor
change the file, and it never refreshes it: an id serialized since the last
rebuild is simply not located, and the caller says so. It imports nothing of
the package, so `view` and `recall` can both sit above it.

`SCHEMA_VERSION` is the index layout both sides must agree on. `recall` writes
it into the file it builds and imports it from here; the locator refuses a
file that says anything else, because an older layout may name columns this
query does not know.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = "10"

# SQLite's default cap on host parameters is far above this; the chunk keeps one
# statement small, not legal.
_CHUNK = 500

_COLUMNS = ("item_id", "session_id", "created", "project_slug", "kind",
            "text", "quote", "trust", "author")


class Located(NamedTuple):
    """`rows` maps an id to its index rows, newest first; an id the index does
    not hold has no key. `notes` is `("index_unavailable",)` when the index
    could not be read at all (missing file, other layout, not a database)."""

    rows: dict
    notes: tuple = ()


def _uri(path) -> str:
    return "file:" + urllib.parse.quote(str(path)) + "?mode=ro"


def locate(db_path, slug: str, ids) -> Located:
    """The index rows of `slug` whose `item_id` is in `ids`. Any trouble with
    the file is a note, never a raise: the index is derived and disposable."""
    wanted = list(dict.fromkeys(i for i in ids if i))
    if not wanted:
        return Located({})
    try:
        conn = sqlite3.connect(_uri(Path(db_path)), uri=True)
    except sqlite3.Error:
        return Located({}, ("index_unavailable",))
    try:
        conn.row_factory = sqlite3.Row
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if meta.get("schema_version") != SCHEMA_VERSION:
            return Located({}, ("index_unavailable",))
        found: dict[str, list] = {}
        for start in range(0, len(wanted), _CHUNK):
            chunk = wanted[start:start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            cursor = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM items"
                f" WHERE project_slug = ? AND item_id IN ({marks})"
                " ORDER BY created DESC, id DESC", (slug, *chunk))
            for row in cursor:
                found.setdefault(row["item_id"], []).append(dict(row))
    except sqlite3.Error:
        return Located({}, ("index_unavailable",))
    finally:
        conn.close()
    return Located({key: tuple(rows) for key, rows in found.items()})
