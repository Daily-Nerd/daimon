"""The part of `serialize.log` that is read, defined once.

Everything that folds the log into per-session state (`status`, `heal`, the
briefing's admission note, the host liveness read and the session-start
catch-up sweep) reads the same window: the last `WINDOW_BYTES` of the file,
starting at a line start. A failure therefore stays visible for as long as
the log keeps its line, however busy the machine is, and the bound is on
bytes so the cost of a read is bounded too.

Standard library only. The standalone hooks cannot import the package, so
they load this file from beside the hook library, like `session_transcript`.
The PACKAGE copy is canonical; `scripts/sync_hooks.py` mirrors it.
"""

from pathlib import Path

WINDOW_BYTES = 4 * 1024 * 1024


def _from_line_start(before: bytes, data: bytes) -> str:
    """Decode `data`, which was cut out of a longer log, from a line start.
    `before` is the one byte that precedes it: a newline there means the cut
    fell exactly on a line start, anything else means the first line is
    partial (possibly mid-character) and is dropped."""
    if before == b"\n":
        return data.decode("utf-8", errors="replace")
    cut_at = data.find(b"\n")
    rest = data[cut_at + 1:] if cut_at >= 0 else b""
    return rest.decode("utf-8", errors="replace")


def read(path, window: int | None = None) -> tuple[str, bool]:
    """`(text, cut)`: the last `window` bytes of the log at `path` from a line
    start (default `WINDOW_BYTES`), and whether the log was longer than that.
    The file is opened, sought and read only for the window, so the I/O is
    bounded on a long-lived machine. A missing log is no activity
    (`("", False)`); any other failure to read it raises OSError, for the
    caller to report."""
    window = WINDOW_BYTES if window is None else window
    try:
        with Path(path).open("rb") as f:
            size = f.seek(0, 2)
            start = max(0, size - window)
            f.seek(max(0, start - 1))
            data = f.read()
    except FileNotFoundError:
        return "", False
    if not start:
        return data.decode("utf-8", errors="replace"), False
    return _from_line_start(data[:1], data[1:]), True


def trim(text: str, window: int | None = None) -> tuple[str, bool]:
    """`(text, cut)`: the same window over a log already held in memory, so a
    caller handed a whole log reads exactly what `read` would have given it."""
    window = WINDOW_BYTES if window is None else window
    data = text.encode("utf-8", errors="replace")
    if len(data) <= window:
        return text, False
    tail = data[-window - 1:]
    return _from_line_start(tail[:1], tail[1:]), True
