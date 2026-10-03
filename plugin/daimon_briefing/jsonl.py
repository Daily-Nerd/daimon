"""The one place ledger text is split into rows and rewritten (#1138).

Every ledger row is `json.dumps(..., ensure_ascii=False)`, so U+2028, U+2029
and U+0085 sit in it RAW. `str.splitlines()` breaks on those (and on \\x0b,
\\x0c, \\x1c-\\x1e), so a rewriter that split with it tore the row and then
dropped the fragments as "unparseable" — a permanent delete on an unrelated
forget. A JSON row never contains a raw "\\n" (compact `json.dumps` escapes
it), so "\\n" is the only separator this module honours.

Stdlib only. Readers stay on their own tolerant paths for now; this module
is what REWRITERS and scans use, because a rewrite must not lose a byte.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

# A past `scrub_event_fields` split such a row and rejoined the fragments
# with "\n". A row can hold several separators, so the rejoin accumulates;
# the bound keeps a wall of unrelated torn rows from going quadratic.
_MAX_FRAGMENTS = 32
_REJOIN = " "


def _is_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except (ValueError, RecursionError):
        return False


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return False
    return True


def split_rows(text: str) -> list[str]:
    """Ledger text -> row strings. Splits on "\\n" only, drops blank lines,
    and rejoins adjacent fragments of a row an older scrub tore apart.

    A fragment run is rejoined only when the pieces do not parse alone and
    the greedy concatenation (joined with U+2028, the separator that was
    most likely there) parses as one JSON object. A split boundary always
    falls inside a JSON string, so a torn tail followed by a healed append
    never satisfies that: the second line would have to close the first
    line's open string. Lines that never join are returned untouched."""
    pieces = text.split("\n")
    rows: list[str] = []
    i = 0
    n = len(pieces)
    while i < n:
        line = pieces[i]
        if not line.strip():
            i += 1
            continue
        if line.lstrip().startswith("{") and not _parses(line):
            joined = line
            for j in range(i + 1, min(n, i + _MAX_FRAGMENTS)):
                joined = joined + _REJOIN + pieces[j]
                if _is_object(joined):
                    line, i = joined, j
                    break
        rows.append(line)
        i += 1
    return rows


def read_rows(path: Path, *, errors: str = "surrogateescape") -> list[str]:
    """`split_rows` over a file. The default decoding round-trips undecodable
    bytes (surrogateescape) so a rewrite can put them back byte for byte;
    a scan that wants "cannot check" for a non-UTF-8 file passes
    errors="strict" and gets the UnicodeDecodeError (a ValueError)."""
    return split_rows(path.read_text(encoding="utf-8", errors=errors))


def rewrite(path: Path, transform: Callable[[str, object], str | None]) -> int:
    """Atomically rewrite a ledger row by row. Returns the number of rows
    dropped or changed; 0 means the file was not touched.

    `transform(line, row)` runs for each line that parses as JSON and returns
    the line to write (return `line` itself to keep it byte-identical), a new
    string to replace it, or None to drop the row. A line that does not parse
    is NEVER passed to it and NEVER dropped: it is written back verbatim.

    Raises OSError when the file cannot be read or the swap fails; the ledger
    is then untouched and the temp file removed. Staged beside the ledger and
    swapped with os.replace, so a crash leaves the old file or the new one."""
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    out: list[str] = []
    changed = 0
    for line in split_rows(text):
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            out.append(line)
            continue
        new = transform(line, row)
        if new is None:
            changed += 1
            continue
        if new != line:
            changed += 1
        out.append(new)
    if not changed:
        return 0
    tmp = path.with_name(path.name + ".forget-tmp")
    try:
        tmp.write_text("".join(row + "\n" for row in out), encoding="utf-8",
                       errors="surrogateescape")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return changed
