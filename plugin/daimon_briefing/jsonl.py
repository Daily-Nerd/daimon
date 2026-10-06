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

import enum
import errno
import json
import os
import re
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

# A past `scrub_event_fields` split such a row and rejoined the fragments
# with "\n". A row can hold several separators, so the rejoin accumulates;
# the bound keeps a wall of unrelated torn rows from going quadratic.
_MAX_FRAGMENTS = 32
_REJOIN = "\u2028"  # LINE SEPARATOR, escaped so it is visible


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
    return _split(text)[0]


def _split(text: str) -> tuple[list[str], int]:
    """`split_rows` plus how many rows it had to rejoin, for the health read."""
    pieces = text.split("\n")
    rows: list[str] = []
    rejoined = 0
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
                    rejoined += 1
                    break
        rows.append(line)
        i += 1
    return rows, rejoined


def read_rows(path: Path, *, errors: str = "surrogateescape") -> list[str]:
    """`split_rows` over a file. The default decoding round-trips undecodable
    bytes (surrogateescape) so a rewrite can put them back byte for byte;
    a scan that wants "cannot check" for a non-UTF-8 file passes
    errors="strict" and gets the UnicodeDecodeError (a ValueError)."""
    return split_rows(path.read_text(encoding="utf-8", errors=errors))


class Health(str, enum.Enum):
    """What a ledger file is, judged per line (#1132). `str` so a payload
    carries the value without a custom encoder."""
    ABSENT = "absent"          # ENOENT: no file, no bucket
    OK = "ok"                  # every line is a row
    DEGRADED = "degraded"      # torn lines or split rows, folded around
    TRANSIENT = "transient"    # still failing after the retries; never repair
    UNREADABLE = "unreadable"  # non-transient OSError or a garbage line


class Read(NamedTuple):
    """`read`'s answer. `rows` is whatever could be parsed (split rows
    rejoined in memory), so a degraded or unreadable file still yields its
    good rows. `detail` is an errno name or a short reason, NEVER content."""
    health: Health
    rows: list
    torn: int = 0
    split: int = 0
    garbage: int = 0
    detail: str = ""


_TRANSIENT_ERRNOS = frozenset({errno.EAGAIN, errno.EBUSY, errno.EINTR,
                               errno.ETIMEDOUT})
_WINERROR_SHARING = frozenset({32, 33})  # sharing / lock violation
# macOS: the file's bytes live in the cloud and reading would download them.
# `stat.SF_DATALESS` only exists on newer Pythons and only on macOS.
_SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
# surrogateescape maps an undecodable byte to one of these lone surrogates.
_UNDECODABLE = re.compile("[\udc80-\udcff]")


def _is_dataless(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_flags", 0) & _SF_DATALESS)


_ROW, _TORN, _GARBAGE = "row", "torn", "garbage"


def _classify(line: str) -> tuple[str, object]:
    """One line -> (kind, parsed row). A row parses as a JSON object; TORN
    opens like one (`{`) and does not parse; anything else is GARBAGE,
    undecodable bytes included. `read` and `partition` both judge by this."""
    if _UNDECODABLE.search(line):
        return _GARBAGE, None
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return (_TORN if line.lstrip().startswith("{") else _GARBAGE), None
    return (_ROW, row) if isinstance(row, dict) else (_GARBAGE, None)


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _transient_reason(exc: OSError) -> str:
    """The reason an OSError is worth retrying, or "" when it is not."""
    if getattr(exc, "winerror", None) in _WINERROR_SHARING:
        return "sharing"
    if exc.errno in _TRANSIENT_ERRNOS:
        return errno.errorcode[exc.errno]
    return ""


def read(path: Path, *, retries: int = 3, backoff: float = 0.05,
         sleep: Callable[[float], None] = time.sleep) -> Read:
    """Judge one ledger file, per line, without raising.

    Bytes are decoded per line with surrogateescape, so one bad byte costs
    its own line's classification and never the file's. A line is a row when
    it parses as a JSON object. Otherwise it is TORN when it opens like an
    object (`{`) and does not parse, which is what an append that died
    mid-line leaves, and GARBAGE for anything else: undecodable bytes, a
    sync-conflict marker, text that is not JSON, JSON that is not an object.
    Two adjacent fragments that parse once joined (`split_rows`) are a
    SPLIT row, rejoined in memory.

    Garbage makes the file UNREADABLE, else torn or split makes it
    DEGRADED, else OK. A failed open or read is retried `retries` times with
    `backoff` seconds between attempts; a transient errno, a Windows sharing
    violation or a cloud placeholder that still fails is TRANSIENT (never a
    reason to repair), any other OSError is UNREADABLE. ENOENT is ABSENT."""
    reason = ""
    for attempt in range(retries + 1):
        if attempt:
            sleep(backoff)
        try:
            if _is_dataless(os.stat(path)):
                reason = "dataless"
                continue
            data = _read_bytes(path)
        except FileNotFoundError:
            return Read(Health.ABSENT, [])
        except OSError as exc:
            reason = _transient_reason(exc)
            if not reason:
                name = errno.errorcode.get(exc.errno or 0, "OSError")
                return Read(Health.UNREADABLE, [], detail=name)
            continue
        break
    else:
        return Read(Health.TRANSIENT, [], detail=reason)
    lines, split = _split(data.decode("utf-8", errors="surrogateescape"))
    rows: list = []
    torn = garbage = 0
    for line in lines:
        kind, row = _classify(line)
        if kind == _ROW:
            rows.append(row)
        elif kind == _TORN:
            torn += 1
        else:
            garbage += 1
    if garbage:
        health, detail = Health.UNREADABLE, "garbage"
    elif torn or split:
        health = Health.DEGRADED
        detail = "+".join(n for n, c in (("torn", torn), ("split", split)) if c)
    else:
        health, detail = Health.OK, ""
    return Read(health, rows, torn, split, garbage, detail)


class Partition(NamedTuple):
    """`partition`'s answer: what a repair keeps and what it moves out."""
    rows: list
    torn: list
    garbage: list
    split: int = 0
    moved: list = []   # (kind, line) for every torn or garbage line, file order


def partition(text: str) -> Partition:
    """Ledger text -> the lines a repair keeps and the lines it moves out.

    Judged with the same rules as `read`: split rows are rejoined (`rows`
    holds them as one line), a line that parses as an object is a row,
    torn and garbage lines are returned apart, each verbatim, in file order.
    Lines are returned as text; undecodable bytes stay lone surrogates, so
    a caller that decodes with surrogateescape can put them back exactly."""
    lines, split = _split(text)
    rows: list[str] = []
    torn: list[str] = []
    garbage: list[str] = []
    moved: list[tuple[str, str]] = []
    for line in lines:
        kind, _row = _classify(line)
        {_ROW: rows, _TORN: torn, _GARBAGE: garbage}[kind].append(line)
        if kind != _ROW:
            moved.append((kind, line))
    return Partition(rows, torn, garbage, split, moved)


def rewrite(path: Path, transform: Callable[[str, object], str | None], *,
            write: Callable[[Path, str], None] | None = None) -> int:
    """Atomically rewrite a ledger row by row. Returns the number of rows
    dropped or changed; 0 means the file was not touched.

    `transform(line, row)` runs for each line that parses as JSON and returns
    the line to write (return `line` itself to keep it byte-identical), a new
    string to replace it, or None to drop the row. A line that does not parse
    is NEVER passed to it and NEVER dropped: it is written back verbatim.

    Raises OSError when the file cannot be read or the swap fails; the ledger
    is then untouched and the temp file removed. Staged beside the ledger and
    swapped with os.replace, so a crash leaves the old file or the new one.

    `write(path, text)` replaces that stager for a caller that already owns
    one (store's `_atomic_write`, which the write-audit guard observes); it
    receives the surrogateescape-decoded text and must encode it the same
    way."""
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
    if write is not None:
        write(path, "".join(row + "\n" for row in out))
        return changed
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
