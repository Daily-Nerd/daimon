"""The #943 check runtime — stdlib only, loadable by a standalone hook.

A host's pre-action hook runs in whatever interpreter the host launched, with
no venv and no `daimon_briefing` on `sys.path`. So the part of the check
machinery that runs INSIDE a hook lives here, and this file ships
byte-identical into `_hooks/` and `hook/` the way `redact.py` does.

Two rules follow from that, both scars:

  * stdlib ONLY (scar 0049). `from . import config` looks completely ordinary
    from inside the package, passes every local test, and breaks every host
    hook. The path accessors below are therefore hand-copied mirrors of
    `config.checks_dir` / `config.log_dir`, and a probe table in
    `test_checks_runtime.py` asserts behavioral equality against the config
    functions rather than against literals.
  * the canonical file is THIS one and the copies are derivatives (scar 0049
    again, which reverses scar 0044's direction for the adapter scripts).
    Edit here, then run `scripts/sync_hooks.py`.

Nothing here raises. The hook that calls it fires before every shell action
on the host, so an exception is an action that proceeds with no record of
why. Every function returns a value that says what happened, including when
what happened is that daimon could not tell.
"""

import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

MANIFEST_NAME = "manifest.json"
FIRING_LOG_NAME = "checks.jsonl"

# Spec 2.3. `unresolved` is one outcome with many causes, and the set is
# closed: a cause outside it means a code path invented a state no surface
# knows how to render.
CAUSES = frozenset({
    "file-missing", "file-unreadable", "file-oversize", "file-binary",
    "stdin-pipe", "arg-form-unparsed", "check-timeout", "check-crashed",
    "body-hash-mismatch", "runtime-missing",
})

# A file argument daimon will read into the subject. Above this it reports
# file-oversize rather than paying to read it: the point of the cap is that
# the hook's budget is spent before the host's, not that large files are
# suspicious.
MAX_SUBJECT_FILE_BYTES = 1024 * 1024

# Scar 0022 applied to the SUBJECT rather than the pattern. `check.match` is
# caller-supplied and capped at 200 bytes, which bounds its length and says
# nothing about its backtracking. Bounding the string it runs against is what
# keeps a pathological pair from outliving the host's hook timeout, and that
# timeout is fail-open: a hook that reaches it does not block, the tool
# proceeds. This is the one known bound and it is documented as such.
MATCH_INPUT_BYTES = 4096

# The same bound at the resolver's door, which is the second way in. The
# prefilter above reads a slice; the resolver has to TOKENIZE what it was
# given, and shlex is superlinear in the length of a single argument.
# Measured, with heredocs already stripped: 0.14s at 64 KiB, 5.3s at 512 KiB,
# 21s at 1 MiB. The host hook timeout is 10s and it is fail-open, so a
# resolver that outlives it lets the action through with no record at all.
#
# It counts the command AFTER heredoc bodies are removed, because those cost
# nothing to skip: the expensive input is one long inline argument, a base64
# blob or an expanded --body. A command above this is unresolved, never
# allowed, because a command daimon declined to parse is one it can prove
# nothing about.
MAX_COMMAND_BYTES = 64 * 1024


class Manifest(NamedTuple):
    """What `load_manifest` found. `reason` is empty when the manifest was
    read; otherwise it names the state for the firing log, because "no
    manifest" and "a manifest daimon can no longer parse" are different
    facts and folding them would report an unwritten install as a broken
    one."""
    entries: list
    reason: str


class Subject(NamedTuple):
    """A materialized subject: the command string plus every file argument
    daimon could read. `path` is a temp file the CALLER must hand back to
    `discard` once the check has run."""
    path: str
    files: tuple
    command: str = ""


class Outcome(NamedTuple):
    """What one check run produced. `outcome` is one of the three spec 2.3
    values and never folds: unresolved is not clean and is not a violation.
    `exit_code` is -1 when no process ran."""
    outcome: str
    cause: str
    reason: str
    exit_code: int
    duration_ms: int


class Unresolved(NamedTuple):
    """Daimon could not prove the subject clean. Never rendered as clean and
    never counted as a violation. `reason` is for a human and may name a
    path; the firing log carries the cause and never the reason."""
    cause: str
    reason: str


# ---- config mirrors (scar 0043: copy the QUIRKS, not the intent) ----------


def _env_file_path() -> Path:
    """Mirror of daimon_briefing.config._env_file_path."""
    raw = os.environ.get("DAIMON_ENV_FILE")
    return Path(raw).expanduser() if raw else Path.home() / ".daimon" / "env"


def _env_file_values() -> dict:
    """Mirror of daimon_briefing.config._file_values — KEY=VALUE lines, with
    the `export ` prefix, surrounding quotes, blank lines and `#` comments
    tolerated. Copied rather than imported because hooks run standalone;
    config.py is the CANONICAL form and this copy must follow it line-form
    for line-form."""
    try:
        text = _env_file_path().read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _config_get(name: str) -> str:
    """Mirror of daimon_briefing.config._get: process env WINS, env file is
    the fallback. Present-but-empty in the process env is a value and
    shadows the file, exactly as on the package side."""
    val = os.environ.get(name)
    if val is not None:
        return val
    return _env_file_values().get(name) or ""


def checks_dir() -> Path:
    """Where the manifest and the materialized bodies live, resolved the way
    the WRITER resolves it.

    `checks.sync` writes through config.checks_dir(); this reads. A path
    resolved any other way here has the hook reading an empty directory
    while the CLI reports N checks armed, and neither side can see the
    split — the same writer/reader gap scar 0043 records one directory over.
    So this reproduces config.checks_dir() exactly, including the parts that
    look like bugs: no strip (so "   " is a relative directory named three
    spaces, and the reader must agree), and the env-file fallback, which is
    the channel a GUI-launched host actually uses."""
    raw = _config_get("DAIMON_CHECKS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "checks"


def log_dir() -> Path:
    """Mirror of config.log_dir(), same reasoning as checks_dir(): the
    firing log sits under it and `daimon forget` purges through the config
    accessor."""
    raw = _config_get("DAIMON_LOG_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".daimon" / "logs"


# ---- manifest -------------------------------------------------------------


def load_manifest(path=None) -> Manifest:
    """Read the armed-check manifest. Never raises, never partially fails.

    An absent manifest is the ordinary state of an install that has armed
    nothing, so it reads as empty with `no-manifest` rather than as an
    error. A manifest that exists and cannot be parsed is a different fact
    and gets its own reason: both allow the action, and only one of them
    means daimon wrote something it can no longer read."""
    target = Path(path) if path is not None else checks_dir() / MANIFEST_NAME
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Manifest([], "no-manifest")
    except (OSError, UnicodeDecodeError):
        return Manifest([], "manifest-unreadable")
    try:
        data = json.loads(raw)
    except ValueError:
        return Manifest([], "manifest-unreadable")
    if not isinstance(data, list):
        return Manifest([], "manifest-unreadable")
    # A row that is not an object is dropped rather than fatal: one bad row
    # must not disarm every other project's checks.
    return Manifest([row for row in data if isinstance(row, dict)], "")


def armed_for(cwd, manifest) -> list:
    """The manifest entries armed for this working directory.

    `project_dir` on an entry is already the resolved physical root (the
    writer runs it through config.resolve_project_dir, which walks to the
    git toplevel). Here the cwd is realpath'd to meet it, so a project
    reached through a symlink still arms, and the prefix test carries an
    explicit separator so /srv/appliance is not governed by a ruling made
    for /srv/app."""
    entries = getattr(manifest, "entries", manifest)
    if not isinstance(entries, list) or not isinstance(cwd, str) or not cwd:
        return []
    try:
        here = os.path.realpath(cwd)
    except (OSError, ValueError):
        return []
    out = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        root = entry.get("project_dir")
        if not isinstance(root, str) or not root:
            continue
        bounded = root if root.endswith(os.sep) else root + os.sep
        if here == root or here.startswith(bounded):
            out.append(entry)
    return out


def matches(entry, command) -> bool:
    """The cheap prefilter: does this entry's regex hit the command string?

    Unanchored and case-sensitive, per the spec, and read over at most
    MATCH_INPUT_BYTES bytes of the command. An unusable pattern matches
    nothing instead of raising: a hand-edited manifest can carry a regex the
    writer would have refused, and that must not become a traceback on every
    shell action."""
    if not isinstance(entry, dict) or not isinstance(command, str):
        return False
    pattern = entry.get("match")
    if not isinstance(pattern, str) or not pattern:
        return False
    subject = command.encode("utf-8", "replace")[:MATCH_INPUT_BYTES].decode(
        "utf-8", "ignore")
    try:
        return re.search(pattern, subject) is not None
    except (re.error, RecursionError):
        return False


# ---- resolver (spec 3.2) --------------------------------------------------

SUBJECT_SEPARATOR = "--- daimon:subject ---"

# Flags whose value is a path to read. `-F` is gh's short form of
# --body-file on `pr create` / `issue create` AND its field flag on `api`,
# so it appears in both tables and the value's shape decides.
_PATH_FLAGS = ("--body-file", "--notes-file", "-F")
_FIELD_FLAGS = ("-F", "--field")
_KNOWN_FLAGS = frozenset(_PATH_FLAGS) | frozenset(_FIELD_FLAGS)

# A heredoc puts the body INSIDE the command string, so `--body-file -` is
# resolvable after all. The terminator may be quoted (no expansion), and
# `<<-` allows a tab-indented terminator line.
#
# The lazy body is a scan for the terminator, not an ambiguous alternation,
# so scar 0022's backtracking shape does not apply. The bound that does apply
# is upstream: this runs only on a command the prefilter already matched.
_HEREDOC_RE = re.compile(
    r"<<-?[ \t]*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2(?=\s|$)", re.DOTALL)

# A heredoc is replaced by a marker token rather than by whitespace, so the
# token stream still says WHERE the redirect stood. NUL is in it because a
# command string carrying one cannot be executed anyway, which makes a
# collision with real text impossible rather than unlikely.
_HEREDOC_MARK = "\x00daimon-heredoc-"

# Tokens that end one simple command and begin the next. `&` is here for the
# same reason the others are: what follows it is a different command, and a
# heredoc on one side of it does not feed an argument on the other.
_SEGMENT_BREAKS = frozenset({"&&", "||", ";", "|", "&"})


def _heredoc_index(token):
    """The heredoc a marker token stands for, or None for a real argument."""
    if token.startswith(_HEREDOC_MARK) and token.endswith("\x00"):
        try:
            return int(token[len(_HEREDOC_MARK):-1])
        except ValueError:
            return None
    return None


def _segments(tokens):
    """The command split into simple commands: (tokens, heredoc indexes, fed
    by a pipe).

    A heredoc redirect belongs to the command it is attached to, and nothing
    else in the string can claim it. Without this split, `cat <<EOF ... EOF`
    followed by a governed command hands the governed command text it never
    reads — and if that text is clean, the record says the real body was
    proven safe."""
    out = []
    current: list = []
    marks: list = []
    piped = False
    for token in tokens:
        if token in _SEGMENT_BREAKS:
            out.append((current, marks, piped))
            current, marks, piped = [], [], token == "|"
            continue
        index = _heredoc_index(token)
        if index is None:
            current.append(token)
        else:
            marks.append(index)
    out.append((current, marks, piped))
    return out


def _split_attached(token):
    """`--body-file=x` -> ("--body-file", "x", True); anything else is left
    for the two-token form.

    The single-dash case comes FIRST and does not need an `=`. pflag, the
    flag library gh uses, accepts a shorthand value attached to its flag, so
    `-Fbody.md` is the same command as `-F body.md` and `-Fkey=@p` the same
    as `-F key=@p`. Splitting here rather than at the use sites means the
    field and the dash rules below apply to both spellings unchanged: an
    attached form daimon did not split fell through every branch and left
    the subject holding only the command string, which reports CLEAN for a
    file that was never opened."""
    if (token.startswith("-") and not token.startswith("--")
            and len(token) > 2 and token[:2] in _KNOWN_FLAGS):
        value = token[2:]
        # pflag treats a leading `=` after a shorthand as the separator, so
        # `-F=body.md` names the file body.md and not a field with an empty
        # name. Following it keeps the two spellings one command.
        return token[:2], value[1:] if value.startswith("=") else value, True
    if token.startswith("-") and "=" in token:
        head, _, tail = token.partition("=")
        if head in _KNOWN_FLAGS:
            return head, tail, True
    return token, "", False


def _walk(tokens):
    """(flag, value, previous token) for each step of the command.

    `flag` is None for a token that names no known flag, and `value` is None
    when a known flag ran off the end with nothing after it. One generator,
    two readers: the dash-consumer count below and the resolver itself, so
    the two can never disagree about what a token means."""
    index = 0
    while index < len(tokens):
        previous = tokens[index - 1] if index else ""
        flag, value, attached = _split_attached(tokens[index])
        if flag not in _KNOWN_FLAGS:
            index += 1
            yield None, tokens[index - 1], previous
            continue
        if not attached:
            index += 1
            if index >= len(tokens):
                yield flag, None, previous
                return
            value = tokens[index]
        index += 1
        yield flag, value, previous


def _read_file(raw, base):
    """The file's text, or the Unresolved cause that stopped it. Relative
    paths resolve against the action's working directory, because that is
    what the shell would have done."""
    path = raw if os.path.isabs(raw) else os.path.join(base, raw)
    # One answer about one file. A stat that decides the cap and an open that
    # happens afterwards are two answers, and they disagree whenever the file
    # changes in between; reading one byte past the cap and judging THAT
    # cannot disagree with itself, and it never holds more than the cap.
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_SUBJECT_FILE_BYTES + 1)
    except FileNotFoundError:
        return Unresolved("file-missing", f"{raw} does not exist")
    except OSError as exc:
        return Unresolved("file-unreadable", f"{raw}: {exc.strerror or exc}")
    except ValueError as exc:
        # A NUL in the path: `open` rejects it before the OS ever sees it, and
        # it raises ValueError rather than OSError, so it walks straight past
        # the clause above. The command string comes from a host payload and
        # can carry one.
        return Unresolved("file-unreadable", f"{raw}: {exc}")
    if len(data) > MAX_SUBJECT_FILE_BYTES:
        return Unresolved(
            "file-oversize",
            f"{raw} is over the {MAX_SUBJECT_FILE_BYTES} byte cap")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return Unresolved("file-binary", f"{raw} is not UTF-8 text")


def _subject_text(command, reads) -> str:
    parts = [command, "\n", SUBJECT_SEPARATOR, "\n"]
    for flag, source, text in reads:
        parts.append(f"--- daimon:file {flag} {source} ---\n")
        parts.append(text)
        if text and not text.endswith("\n"):
            parts.append("\n")
    return "".join(parts)


def _resolve_segment(tokens, marks, piped, heredocs, base, reads):
    """Read one simple command's file arguments into `reads`.

    Returns None when the segment is fine, or the Unresolved that stopped it.
    A heredoc binds only from `marks`, which holds the redirects that stood
    inside THIS command, so text belonging to a neighbour can never be
    presented as what the governed command reads."""
    # A marker token is minted by `_take` and nothing else, but the command
    # string arrives from a host payload and can spell one out. An index no
    # heredoc answers to is simply not a heredoc; without this the bind below
    # reads past the end of the list and the resolver raises, which is the
    # one thing this module promises never to do.
    marks = [index for index in marks if 0 <= index < len(heredocs)]
    # Counted per segment: binding is only safe with exactly one candidate on
    # each side, and the sides are this command's, not the whole string's.
    consumers = sum(1 for flag, value, _ in _walk(tokens)
                    if flag is not None and value == "-")
    for flag, value, previous in _walk(tokens):
        if flag is None:
            # Row 6: a form outside the table is never assumed harmless.
            token = value
            if token == "-" and previous.startswith("-"):
                return Unresolved(
                    "arg-form-unparsed",
                    f"{previous} reads standard input in a form daimon does "
                    "not parse")
            if token.startswith("@") and len(token) > 1:
                return Unresolved(
                    "arg-form-unparsed",
                    f"{token} names a file in a form daimon does not parse")
            continue
        if value is None:
            return Unresolved("arg-form-unparsed",
                              f"{flag} was given no value")

        if value == "-":
            if len(marks) == 1 and consumers == 1:
                reads.append((flag, "<heredoc>", heredocs[marks[0]]))
                continue
            if not marks:
                if piped:
                    return Unresolved(
                        "stdin-pipe",
                        f"{flag} reads standard input and a pipe fills it, so "
                        "the bytes come from a process daimon cannot see")
                # A heredoc elsewhere in the string belongs to the command it
                # is attached to, never to this one. Borrowing it would build
                # the subject from text this command never reads, and if THAT
                # text is clean the record says the real body was proven safe.
                return Unresolved(
                    "arg-form-unparsed",
                    f"{flag} reads standard input and this command carries no "
                    "heredoc of its own, so daimon cannot see what fills it")
            # Inside one command the counts still have to be one and one:
            # which heredoc feeds which argument is not something the token
            # stream answers, and a guess is the same defect one scope down.
            return Unresolved(
                "arg-form-unparsed",
                f"this command carries {len(marks)} heredoc(s) and "
                f"{consumers} argument(s) reading standard input; daimon "
                "binds one to one or not at all")
        raw = value
        if flag in _FIELD_FLAGS and "=" in value:
            _, _, rhs = value.partition("=")
            if not rhs.startswith("@"):
                # A literal field value names no file. Reporting unresolved
                # here would make every `gh api -F name=x` unprovable for
                # nothing.
                continue
            raw = rhs[1:]
        elif flag in _FIELD_FLAGS and value.startswith("@"):
            raw = value[1:]
        elif flag not in _PATH_FLAGS:
            continue
        if not raw:
            return Unresolved("arg-form-unparsed",
                              f"{flag} was given an empty path")
        text = _read_file(raw, base)
        if isinstance(text, Unresolved):
            return text
        reads.append((flag, raw, text))
    return None


def resolve(command, cwd):
    """The action's subject, or the cause daimon could not build one.

    Subject = the command string, a separator, then every resolved file's
    bytes under a header naming the flag it came from. Written to a 0o600
    file in the SYSTEM temp dir: a file under ~/.daimon would have to be
    declared in the surface registry and carry a deletion story, and this
    one lives for the length of one exec.

    Fail-closed and first-failure-wins. One argument daimon cannot read
    means it cannot prove the subject clean, whatever the others say."""
    if not isinstance(command, str):
        return Unresolved("arg-form-unparsed", "the command is not a string")
    base = cwd if isinstance(cwd, str) and cwd else os.getcwd()

    heredocs: list = []

    def _take(match):
        heredocs.append(match.group(3))
        return f" {_HEREDOC_MARK}{len(heredocs) - 1}\x00 "

    text = _HEREDOC_RE.sub(_take, command)
    # Bounded BEFORE the lexer, which is the expensive part. Above the cap
    # daimon declines rather than spending the host's whole hook budget on
    # tokenizing one enormous argument and then being overtaken by a timeout
    # that lets the action through.
    if len(text.encode("utf-8", "replace")) > MAX_COMMAND_BYTES:
        return Unresolved(
            "arg-form-unparsed",
            f"the command is too long to resolve within the check budget "
            f"(over {MAX_COMMAND_BYTES} bytes with heredocs removed)")
    # Every heredoc BODY is gone by now, so a remaining newline separates two
    # commands exactly as a semicolon does. Spelling it as one lets a single
    # lexer pass find every boundary; the lexer folds a bare newline into
    # whitespace and would otherwise run two commands together. A newline
    # inside a quoted argument is rewritten too, which changes that argument's
    # text and nothing daimon reads from it.
    text = text.replace("\r\n", "\n").replace("\n", " ; ")
    try:
        # punctuation_chars makes the shell operators their own tokens while
        # keeping quotes intact, which is what the segment split needs.
        lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""  # shlex.split's setting: `#` is not a comment
        tokens = list(lexer)
    except ValueError as exc:
        return Unresolved("arg-form-unparsed",
                          f"the command could not be tokenized: {exc}")

    reads: list = []
    for seg_tokens, marks, piped in _segments(tokens):
        outcome = _resolve_segment(seg_tokens, marks, piped, heredocs, base,
                                   reads)
        if outcome is not None:
            return outcome

    # mkstemp inside the try, not before it: a temp dir that is full or
    # read-only fails HERE, and a raise from this function is an action that
    # proceeds with no record of why.
    path = ""
    try:
        handle_fd, path = tempfile.mkstemp(prefix="daimon-check-",
                                           suffix=".subject")
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(_subject_text(command, reads))
        # mkstemp is already umask-independent; stated again so the mode is
        # a property of this file rather than of the stdlib's default.
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            if path:
                os.unlink(path)
        except OSError:
            pass
        # Not a cause the spec's table anticipated: the subject could not be
        # built because daimon's own temp dir failed. It is unresolved either
        # way, and `check-crashed` is the cause that says the machinery, not
        # the argument, is what went wrong.
        return Unresolved("check-crashed",
                          f"the subject file could not be written: {exc}")
    return Subject(path, tuple((flag, source) for flag, source, _ in reads),
                   command)


def discard(subject) -> None:
    """Remove a materialized subject. Never raises: it runs in a `finally`
    on a path that already has an outcome to report."""
    path = getattr(subject, "path", None)
    if not isinstance(path, str) or not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


# ---- runner (spec 3.3) ----------------------------------------------------

DEFAULT_TIMEOUT = 5.0

# What a check body inherits, plus every LC_* and the three DAIMON_CHECK_*
# variables the runner sets. A ratified body is human-armed and runs as the
# user, so this is not a sandbox and does not pretend to be one. It is a blast
# radius: the body's job is to read one subject file, and handing it every
# token in the host session widens what a mistake in someone else's script can
# reach, for nothing. PATH and HOME stay because a body that cannot find its
# own tools reports check-crashed and looks like a defect in the check.
_ENV_KEEP = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})


def body_name(entry) -> str:
    """The materialized body's file name: the ruling id and the head of the
    hash it was ratified with. Two names for one ruling means the pin moved,
    and the stale one is swept at the next sync."""
    return f"{entry.get('ruling_id', '')}-{str(entry.get('sha256', ''))[:12]}.sh"


def body_path(entry, base=None) -> Path:
    return (Path(base) if base is not None else checks_dir()) / body_name(entry)


def _kill_group(proc) -> None:
    """start_new_session put the check in its own process group, so a body
    that backgrounded something is killed WITH it. Killing only the direct
    child leaves a grandchild holding the pipe, and the read that follows
    blocks past the host's hook timeout, which is fail-open: the action
    proceeds and nothing says why."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, AttributeError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _first_line(text) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def run(entry_or_body, subject, *, cwd=None, timeout=None) -> Outcome:
    """Run one check against one subject and say what happened.

    `entry_or_body` is a manifest entry (whose `sha256` is re-checked against
    the file on disk before exec) or a plain path to a body the caller just
    materialized. Never raises: every path returns an Outcome, because the
    hook that calls this fires before every shell action and an exception is
    an action that proceeds with no record of why."""
    started = time.monotonic()
    try:
        return _run(entry_or_body, subject, cwd, timeout, started)
    except Exception as exc:  # noqa: BLE001 — an outcome is mandatory
        return Outcome("unresolved", "check-crashed",
                       f"the check runner failed: {type(exc).__name__}: {exc}",
                       -1, int((time.monotonic() - started) * 1000))


def _run(entry_or_body, subject, cwd, timeout, started) -> Outcome:
    def out(outcome, cause, reason, exit_code=-1):
        return Outcome(outcome, cause, reason, exit_code,
                       int((time.monotonic() - started) * 1000))

    ruling_id = ""
    pinned = ""
    if isinstance(entry_or_body, dict):
        ruling_id = str(entry_or_body.get("ruling_id") or "")
        pinned = str(entry_or_body.get("sha256") or "")
        override = entry_or_body.get("body_path")
        path = Path(override) if override else body_path(entry_or_body)
    else:
        path = Path(entry_or_body)

    try:
        body = path.read_bytes()
    except OSError as exc:
        return out("unresolved", "check-crashed",
                   f"the check body could not be read: {exc.strerror or exc}")
    if pinned and hashlib.sha256(body).hexdigest() != pinned:
        # Someone edited the materialized body. Never a silent skip: an
        # unresolved outcome is visible, a skip looks like a clean run.
        return out("unresolved", "body-hash-mismatch",
                   "the check body on disk is not the one this ruling was "
                   "ratified with; run `daimon check sync`")

    env = {name: value for name, value in os.environ.items()
           if name in _ENV_KEEP or name.startswith("LC_")}
    env["DAIMON_CHECK_SUBJECT"] = str(getattr(subject, "path", "") or "")
    env["DAIMON_CHECK_COMMAND"] = str(getattr(subject, "command", "") or "")
    env["DAIMON_CHECK_RULING"] = ruling_id
    budget = (float(timeout) if isinstance(timeout, (int, float))
              and timeout > 0 else DEFAULT_TIMEOUT)

    try:
        proc = subprocess.Popen(
            ["sh", str(path)],
            cwd=str(cwd) if cwd else None,
            env=env,
            # Scar 0034: no stdin PIPE, so nothing here can close a pipe and
            # make the communicate() below raise on a flush. /dev/null is
            # also what keeps a body that reads stdin from blocking on an
            # inherited terminal until the budget expires.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )
    except FileNotFoundError:
        return out("unresolved", "runtime-missing",
                   "no POSIX sh on PATH, so no check can run on this host")
    except OSError as exc:
        return out("unresolved", "check-crashed",
                   f"the check could not be started: {exc.strerror or exc}")

    try:
        _, err = proc.communicate(timeout=budget)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            proc.communicate(timeout=1)
        except Exception:  # noqa: BLE001 — the outcome is already decided
            pass
        return out("unresolved", "check-timeout",
                   f"the check did not finish within {budget:g}s")

    code = proc.returncode
    reason = _first_line(err)
    if code == 0:
        return out("clean", "", "", 0)
    if code == 1:
        return out("violation", "",
                   reason or "the check reported a violation and gave no "
                             "reason", 1)
    # 126 and 127 land here with sh's own first stderr line, which is how a
    # body that reaches outside itself becomes observable: the slice 1 path
    # refusal catches a bare single-token path, and a one-line `sh /host.sh`
    # passes it.
    return out("unresolved", "check-crashed",
               reason or f"the check exited {code}", code)


# ---- firing log (spec 3.4) ------------------------------------------------

# The whole row, and nothing else. Spec 3.4: no command text, no paths, no
# subject. `decision_emitted` is what the hook actually wrote to the host, so
# a reader can tell a check that RAN from a check that was honored — only
# `deny` under `enforce` closes that gap.
FIRING_KEYS = ("ts", "ruling_id", "host", "mode", "outcome", "cause",
               "decision_emitted", "duration_ms")

# Every value `cause` may hold. The manifest reasons join the runner's causes
# because "nothing was armed here" is a firing the stats surface has to be
# able to count, and it is the difference between "armed, never fired" and
# "clean".
LOG_CAUSES = CAUSES | {"no-manifest", "no-match", "manifest-unreadable"}


def log_firing(row, path=None) -> bool:
    """Append one row to the firing log. Returns whether it landed.

    The row is PROJECTED onto FIRING_KEYS rather than filtered, so a caller
    that hands over the whole outcome record (reason and command included)
    cannot leak them here. `cause` is the one field where free text could
    otherwise reach a log declared to hold no plaintext, so a value outside
    the declared set is recorded as `unknown`: the state is still signalled,
    it just cannot bring a path along.

    Never raises. A hook that cannot write its log still has an action to
    allow or deny, and losing the decision over the record would be the
    wrong trade."""
    try:
        source = row if isinstance(row, dict) else {}
        cause = str(source.get("cause") or "")
        stamped = {
            "ts": str(source.get("ts") or "") or time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ruling_id": str(source.get("ruling_id") or ""),
            "host": str(source.get("host") or ""),
            "mode": str(source.get("mode") or ""),
            "outcome": str(source.get("outcome") or ""),
            "cause": cause if cause in LOG_CAUSES else (cause and "unknown"),
            "decision_emitted": str(source.get("decision_emitted") or ""),
            "duration_ms": int(source.get("duration_ms") or 0),
        }
        target = (Path(path) if path is not None
                  else log_dir() / FIRING_LOG_NAME)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stamped, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError):
        return False
