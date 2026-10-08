"""The one place ledger text is split into rows and rewritten (#1138).

Every ledger row is `json.dumps(..., ensure_ascii=False)`, so U+2028, U+2029
and U+0085 sit in it RAW. `str.splitlines()` breaks on those (and on \\x0b,
\\x0c, \\x1c-\\x1e), so a rewriter that split with it tore the row and then
dropped the fragments as "unparseable" — a permanent delete on an unrelated
forget. A JSON row never contains a raw "\\n" (compact `json.dumps` escapes
it), so "\\n" is the only separator this module honours.

Stdlib only. Readers stay on their own tolerant paths for now; this module
is what REWRITERS and scans use, because a rewrite must not lose a byte. It is
also the one place a ledger is APPENDED to (`append_lines`, #1132): the
torn-tail heal, the single write and the lock live here, not in each ledger
module.
"""

from __future__ import annotations

import enum
import errno
import functools
import json
import os
import re
import stat
import time
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import ModuleType
from typing import NamedTuple, Union

from . import display, surfaces
from .surfaces import WritePosture, Writer

# Annotated before the import (#842): the try branch alone infers a Module, so
# the except branch's None reads as a type error rather than as the degrade it
# is. Every use site honors it with an `if _fcntl` guard.
_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX: the lock degrades to a no-op
    _fcntl = None

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


LOCK_NAME = ".pointer.lock"   # dotfile: invisible to _session_files (.json
                              # filter) and _pointer_stems (_POINTER_RE)
_LOCK_TRIES = 50              # x 20ms = ~1s bounded wait, then fail open
_LOCK_INTERVAL = 0.02


@contextmanager
def dir_lock(d: Path) -> Iterator[bool]:
    """Serialize a critical section over the files of directory `d` (#31).

    flock on a sidecar dotfile with a bounded wait; yields whether the lock
    was actually acquired. Fail-open everywhere (no fcntl, unwritable or
    missing dir, contention past the wait): the caller proceeds unguarded,
    which is exactly the pre-lock behavior. The sidecar is opened with the
    builtin `open`, never `Path.open`: it carries no content, and the
    write-audit guard must not record it as a write.

    It is a sidecar, not a lock on the ledger's own fd, because `rewrite`
    swaps the inode with os.replace and a lock on the old inode would not
    exclude a writer holding the new one."""
    if _fcntl is None:
        yield False
        return
    fh = None
    held = False
    try:
        fh = open(d / LOCK_NAME, "a+")
        for _ in range(_LOCK_TRIES):
            try:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                time.sleep(_LOCK_INTERVAL)
    except OSError:
        pass
    try:
        yield held
    finally:
        if fh is not None:
            try:
                if held:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                fh.close()
            except OSError:
                pass


def ledger_lock(path: Path):
    """`dir_lock` over the directory a ledger lives in. Per-directory, not
    per-file: the sidecar is the one `.pointer.lock` the registry already
    declares, and contention between ledgers of one bucket is negligible."""
    return dir_lock(path.parent)


def _unterminated(path: Path) -> bool:
    """True when the file's last byte is not "\\n": the last append died
    before writing its terminator. A missing, empty or unreadable file is
    not torn."""
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def _judge(posture, path: Path):
    """`posture` as the WritePosture to apply and the Posture it came from
    (None for a bare value). A callable is called here, so a caller that
    hands one gets its judgement made at the moment the write happens,
    under the ledger lock when there is one."""
    if callable(posture):
        posture = posture()
    if isinstance(posture, Posture):
        return posture.write, posture
    return posture, None


def _apply(write: WritePosture, judged, path: Path) -> bool:
    """True when the append should go ahead. REFUSE raises, SKIP records
    usage and returns False, so no caller can mistake either for a write."""
    if write is WritePosture.PROCEED:
        return True
    if write is WritePosture.SKIP:
        _note_skip(path.name, judged.health.value if judged else "unproven")
        return False
    if judged is None:
        raise Refused(path.name, "unproven", "", "run: daimon status")
    raise judged.refusal()


def append_lines(path: Path, lines: Iterable[str], *,
                 posture: "PostureArg", lock: bool = True) -> int:
    """Append `lines` (each already serialised, no terminator) to a ledger.
    Returns how many were written.

    `posture` is required and says what a write does with this ledger's
    health (#1132 PR 10b): PROCEED appends, REFUSE raises `Refused` with
    nothing written, SKIP writes nothing and returns 0. It is a
    `WritePosture` (a cure, which needs no judging), a `Posture` the caller
    already read, or a zero-argument callable (`lazy_posture`) that is called
    HERE, under the ledger lock, so the health it judges is the health the
    append lands on.

    The bytes are built once and written in ONE call on an unbuffered
    O_APPEND handle, so a crash leaves at most one torn tail and two
    appenders cannot interleave inside a row. Appending onto an
    unterminated tail would fuse two rows into one unparseable line, so a
    torn tail is first TERMINATED with "\\n" (never truncated: the torn
    bytes stay on disk for `ledger repair` to move out, and `read` counts
    them torn, not split). A missing or empty file needs no heal.

    Text is encoded with surrogateescape, so a line carrying undecodable
    bytes writes them back as they were. OSError propagates as it did from
    each ledger's own appender; the caller decides what a failed append
    means.

    `lock=False` skips the `.pointer.lock` sidecar, for a directory that is
    not a bucket: a team dir is committed by `teamsync._commit_own` and would
    carry the lock file to every teammate, and `logs/` declares no such
    surface."""
    body = [line + "\n" for line in lines]
    if not body:
        return 0
    data = "".join(body).encode("utf-8", errors="surrogateescape")
    with ledger_lock(path) if lock else nullcontext():
        write, judged = _judge(posture, path)
        if not _apply(write, judged, path):
            return 0
        if _unterminated(path):
            data = b"\n" + data
        with path.open("ab", buffering=0) as handle:
            view = memoryview(data)
            while view:
                view = view[handle.write(view):]
    return len(body)


def append(path: Path, row: dict, *, posture: "PostureArg",
           lock: bool = True) -> int:
    """`append_lines` for one row, dumped with ensure_ascii=False like every
    ledger writer."""
    return append_lines(path, [json.dumps(row, ensure_ascii=False)],
                        posture=posture, lock=lock)


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
    good rows. `detail` is an errno name or a short reason, NEVER content.
    `undecodable` counts the lines holding undecodable bytes, a subset of
    `garbage`, so a scan that must say "cannot check" for a non-UTF-8 file
    can tell it from a line that is merely not JSON."""
    health: Health
    rows: list
    torn: int = 0
    split: int = 0
    garbage: int = 0
    detail: str = ""
    undecodable: int = 0

    @property
    def cannot_scan(self) -> str:
        """Why a scan or a strict read cannot vouch for this file, or "".

        A transient failure and an OS-level failure (the detail is the errno
        name) mean the file was not read, and an undecodable byte means a
        line could not be read, so "nothing found" proves nothing. A line
        that is merely not JSON (a conflict marker, stray text) is skipped
        and does not count: the same line was skipped before `read` existed.
        """
        if self.health is Health.TRANSIENT:
            return self.detail
        if self.health is Health.UNREADABLE and self.detail != "garbage":
            return self.detail
        return "undecodable" if self.undecodable else ""


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


ROW, TORN, GARBAGE = "row", "torn", "garbage"


def classify_line(line: str) -> tuple[str, object]:
    """One line -> (kind, parsed row). A row parses as a JSON object; TORN
    opens like one (`{`) and does not parse; anything else is GARBAGE,
    undecodable bytes included. `read` and `partition` both judge by this."""
    if _UNDECODABLE.search(line):
        return GARBAGE, None
    try:
        row = json.loads(line)
    except (ValueError, RecursionError):
        return (TORN if line.lstrip().startswith("{") else GARBAGE), None
    return (ROW, row) if isinstance(row, dict) else (GARBAGE, None)


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
    torn = garbage = undecodable = 0
    for line in lines:
        kind, row = classify_line(line)
        if kind == ROW:
            rows.append(row)
        elif kind == TORN:
            torn += 1
        else:
            garbage += 1
            if _UNDECODABLE.search(line):
                undecodable += 1
    if garbage:
        health, detail = Health.UNREADABLE, "garbage"
    elif torn or split:
        health = Health.DEGRADED
        detail = "+".join(n for n, c in (("torn", torn), ("split", split)) if c)
    else:
        health, detail = Health.OK, ""
    return Read(health, rows, torn, split, garbage, detail, undecodable)


class Reached(list):
    """What a forget deleter removed, and whether it REACHED its ledger (#1132
    PR 10b, D10.5). A `list` of the removed ids, so every `== [...]` assertion
    in the suites holds, with three facts about the ledger it left behind.

    `reached` is True only when the ledger was PROVEN and the value is gone
    from every row that can be read. A plaintext ledger with a torn line is
    NOT reached: `rewrite` writes a line it cannot parse back verbatim, and
    that line may hold the value. `state` is what `read` judged afterwards and
    `torn` how many torn lines it holds."""

    def __init__(self, ids=(), *, reached: bool = True,
                 state: "Health | None" = None, torn: int = 0,
                 detail: str = "", unscannable: str = ""):
        super().__init__(ids)
        self.reached = reached
        self.state = Health.OK if state is None else state
        self.torn = torn
        self.detail = detail
        self.unscannable = unscannable


def judge_reach(path, removed, holds: Callable[[dict], bool], *,
                plaintext: bool = True) -> Reached:
    """`removed` as a `Reached`, judged by reading `path` again: the ledger
    must not be TRANSIENT or UNREADABLE, must hold no torn line when it
    carries plaintext, and no row it can read may still `hold` the value.
    Judging the ledger afterwards, rather than trusting a deleter's own
    bookkeeping, is what makes a failed rewrite on a healthy ledger visible."""
    if path is None:
        return Reached(removed, state=Health.ABSENT)
    got = read(path)
    if got.health is Health.ABSENT:
        return Reached(removed, state=Health.ABSENT)
    unproven = got.health in (Health.TRANSIENT, Health.UNREADABLE)
    unread = plaintext and got.torn > 0
    still = any(holds(row) for row in got.rows)
    return Reached(removed, reached=not (unproven or unread or still),
                   state=got.health, torn=got.torn, detail=got.detail,
                   unscannable=got.cannot_scan)


def reaching(path_of: Callable, holds: Callable, *, plaintext: bool = True):
    """Decorate a forget deleter `fn(key, *, project_dir=None, ...)` so it
    returns a `Reached`. `path_of(project_dir)` names its ledger and
    `holds(row, key)` says whether a row still carries the value. A dry run
    returns what the deleter returned."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(key, *args, **kwargs):
            removed = fn(key, *args, **kwargs)
            if kwargs.get("dry_run"):
                return removed
            return judge_reach(path_of(kwargs.get("project_dir")), removed,
                               lambda row: holds(row, key),
                               plaintext=plaintext)
        return inner
    return wrap


# ---- #1132 PR 10b: the write exits judge the ledger first ------------------

ADMISSION_PREFIX = "error: admission refused: "
"""How a refused admission begins in serialize.log. The one place the class
is detected: both log folds read it from here (the hook library mirrors the
literal, and a test pins the two equal)."""


class Refused(OSError):
    """A write that was not made because its ledger is not proven (REFUSE).

    An OSError on purpose: every module appender already turns an OSError
    into its own "not written" answer, so a refusal can never be mistaken
    for a write. `str(exc)` is the canonical body, the same text on every
    channel. `admission` marks a refused admission, the one class that is an
    ordinary failed serialize and is retried by heal."""

    def __init__(self, name: str, state: str, detail: str, hint: str,
                 admission: bool = False):
        self.name = name
        self.state = state
        self.detail = detail
        self.hint = hint
        self.admission = admission
        shown = f" ({detail})" if detail and not admission else ""
        prefix = "admission refused: " if admission else ""
        super().__init__(f"{prefix}{name} is {state}{shown}; {hint}")


class Posture(NamedTuple):
    """What a ledger is and what a writer of one class does about it."""
    health: Health
    detail: str
    undecodable: int
    write: WritePosture
    name: str = ""
    unscannable: str = ""
    admission: bool = False

    def refusal(self) -> "Refused":
        hint = display.ledger_hint(self.name, self.health.value, self.detail,
                                   self.unscannable)
        return Refused(self.name, self.health.value, self.detail, hint,
                       self.admission)


PostureArg = Union[WritePosture, Posture, Callable[[], Union[WritePosture,
                                                              Posture]]]


def posture(path: Path, name: str, writer: Writer) -> Posture:
    """Judge `path` once and resolve what a `writer` does with it. `name` is
    the ledger's file name, the key of its registry row. A ledger is PROVEN
    when it is OK, ABSENT or DEGRADED and UNPROVEN when it is TRANSIENT or any
    UNREADABLE (an OS error, an undecodable byte, a garbage line): the
    registry says what each writer class does with an unproven one."""
    result = read(path)
    write = surfaces.write_posture(surfaces.write_row(name), writer,
                                   result.health.value)
    return Posture(result.health, result.detail, result.undecodable, write,
                   name, result.cannot_scan, writer is Writer.ADMISSION)


def lazy_posture(path: Path, writer: Writer,
                 name: str | None = None) -> Callable[[], Posture]:
    """A zero-argument judgement of `path` for `append_lines`, which calls it
    under the ledger lock right before the write."""
    return lambda: posture(path, name or path.name, writer)


# A person at the CLI must be told why a write was refused; a library caller
# (anamnesis, a hook, a pod) keeps the contract every module appender has
# always had, "not written" as False. So the refusal is swallowed into False
# unless the run opted in, and `cli.main` is the one place that does.
_SURFACE_REFUSALS: ContextVar[bool] = ContextVar("daimon_surface_refusals",
                                                 default=False)


@contextmanager
def surface_refusals() -> Iterator[None]:
    """Within this context a module appender RAISES `Refused` instead of
    answering False, so the entry point can print it and exit 2."""
    token = _SURFACE_REFUSALS.set(True)
    try:
        yield
    finally:
        _SURFACE_REFUSALS.reset(token)


def append_as(path: Path, row: dict, writer: Writer, *,
              lock: bool = True) -> bool:
    """The module appenders' write exit: judge the ledger under the lock for
    `writer`, append, and answer whether the row landed. OSError is the
    caller's "not written" (False); a REFUSE is that same False unless the run
    surfaces refusals (`surface_refusals`); a SKIP is False too, and leaves
    its usage line."""
    try:
        return append(path, row, posture=lazy_posture(path, writer),
                      lock=lock) > 0
    except Refused:
        if _SURFACE_REFUSALS.get():
            raise
        return False


def require_writable(path: Path, writer: Writer, *,
                     error: type[Exception] | None = None) -> None:
    """Refuse BEFORE a verb reads the rows it is about to act on. A verb that
    looks up a record in a ledger it cannot read answers "unknown id", which
    is a lie about a ledger that is merely unreadable. Raises `Refused` when
    the run surfaces refusals or no `error` is given, else `error(body)`, so
    a library caller meets its own typed error and never a bare OSError."""
    judged = posture(path, path.name, writer)
    if judged.write is not WritePosture.REFUSE:
        return
    exc = judged.refusal()
    if error is None or _SURFACE_REFUSALS.get():
        raise exc
    raise error(str(exc))


def _note_skip(name: str, state: str) -> None:
    """One local usage line for a machine row that was skipped because its
    ledger is not proven: `<ledger>:skipped-<state>`. Best-effort, and never
    a write to the ledger being skipped."""
    from . import config  # deferred: config imports this module's callers
    try:
        if config.is_disabled():
            return
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with (log_dir / "usage.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {name}:skipped-{state}\n")
    except OSError:
        pass


class Partition(NamedTuple):
    """`partition`'s answer: what a repair keeps and what it moves out."""
    rows: list
    torn: list
    garbage: list
    split: int
    moved: list        # (kind, line) for every torn or garbage line, file order


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
        kind, _row = classify_line(line)
        {ROW: rows, TORN: torn, GARBAGE: garbage}[kind].append(line)
        if kind != ROW:
            moved.append((kind, line))
    return Partition(rows, torn, garbage, split, moved)


def _stage(path: Path, text: str) -> None:
    """Write `text` beside `path` as `<name>.<pid>.tmp` and swap it in with
    os.replace, so a crash leaves the old file or the new one. The `.tmp`
    suffix is the one `store._reap_stale_tmps` already reaps, and the pid
    keeps two processes from staging into the same name."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def replace(path: Path, text: str, *,
            write: Callable[[Path, str], None] | None = None,
            lock: bool = True) -> None:
    """Atomically replace a ledger's whole text, under the ledger lock.

    `write(path, text)` replaces the stager for a caller that already owns
    one (store's `_atomic_write`, which the write-audit guard observes); it
    receives the surrogateescape-decoded text and must encode it the same
    way. Raises OSError when the swap fails, the ledger untouched.

    `lock=False` is for a caller that already holds `ledger_lock(path)` across
    a larger read-then-swap: flock on a second fd of the same process would
    not nest, it would stall about a second and then proceed unguarded."""
    with ledger_lock(path) if lock else nullcontext():
        (write or _stage)(path, text)


def rewrite(path: Path, transform: Callable[[str, object], str | None], *,
            write: Callable[[Path, str], None] | None = None,
            lock: bool = True) -> int:
    """Atomically rewrite a ledger row by row. Returns the number of rows
    dropped or changed; 0 means the file was not touched.

    `transform(line, row)` runs for each line that parses as JSON and returns
    the line to write (return `line` itself to keep it byte-identical), a new
    string to replace it, or None to drop the row. A line that does not parse
    is NEVER passed to it and NEVER dropped: it is written back verbatim.

    Raises OSError when the file cannot be read or the swap fails; the ledger
    is then untouched and the temp file removed. Staged beside the ledger and
    swapped with os.replace, so a crash leaves the old file or the new one.

    The read, the transform and the swap all run under the ledger lock, so an
    append (which takes the same lock) cannot land between the read and the
    swap and be lost. `write` is `replace`'s stager hook; `lock=False` is for
    a caller that already holds the ledger lock (see `replace`)."""
    with ledger_lock(path) if lock else nullcontext():
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
        (write or _stage)(path, "".join(row + "\n" for row in out))
        return changed
