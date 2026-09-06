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

import json
import os
import re
import shlex
import tempfile
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


def _split_attached(token):
    """`--body-file=x` -> ("--body-file", "x", True); anything else is left
    for the two-token form."""
    if token.startswith("-") and "=" in token:
        head, _, tail = token.partition("=")
        if head in _KNOWN_FLAGS:
            return head, tail, True
    return token, "", False


def _read_file(raw, base):
    """The file's text, or the Unresolved cause that stopped it. Relative
    paths resolve against the action's working directory, because that is
    what the shell would have done."""
    path = raw if os.path.isabs(raw) else os.path.join(base, raw)
    try:
        size = os.stat(path).st_size
    except FileNotFoundError:
        return Unresolved("file-missing", f"{raw} does not exist")
    except OSError as exc:
        return Unresolved("file-unreadable", f"{raw}: {exc.strerror or exc}")
    if size > MAX_SUBJECT_FILE_BYTES:
        return Unresolved(
            "file-oversize",
            f"{raw} is {size} bytes, over the {MAX_SUBJECT_FILE_BYTES} cap")
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return Unresolved("file-unreadable", f"{raw}: {exc.strerror or exc}")
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
        return " "

    try:
        tokens = shlex.split(_HEREDOC_RE.sub(_take, command), posix=True)
    except ValueError as exc:
        return Unresolved("arg-form-unparsed",
                          f"the command could not be tokenized: {exc}")

    reads: list = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, value, attached = _split_attached(token)
        if flag not in _KNOWN_FLAGS:
            # Row 6: a form outside the table is never assumed harmless.
            if token == "-" and index and tokens[index - 1].startswith("-"):
                return Unresolved(
                    "arg-form-unparsed",
                    f"{tokens[index - 1]} reads standard input in a form "
                    "daimon does not parse")
            if token.startswith("@") and len(token) > 1:
                return Unresolved(
                    "arg-form-unparsed",
                    f"{token} names a file in a form daimon does not parse")
            index += 1
            continue
        if not attached:
            index += 1
            if index >= len(tokens):
                return Unresolved("arg-form-unparsed",
                                  f"{flag} was given no value")
            value = tokens[index]
        index += 1

        if value == "-":
            if not heredocs:
                return Unresolved(
                    "stdin-pipe",
                    f"{flag} reads standard input and the command carries no "
                    "heredoc, so the bytes come from a process daimon cannot "
                    "see")
            reads.append((flag, "<heredoc>", heredocs.pop(0)))
            continue
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

    handle_fd, path = tempfile.mkstemp(prefix="daimon-check-",
                                       suffix=".subject")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(_subject_text(command, reads))
        # mkstemp is already umask-independent; stated again so the mode is
        # a property of this file rather than of the stdlib's default.
        os.chmod(path, 0o600)
    except OSError as exc:
        try:
            os.unlink(path)
        except OSError:
            pass
        # Not a cause the spec's table anticipated: the subject could not be
        # built because daimon's own temp dir failed. It is unresolved either
        # way, and `check-crashed` is the cause that says the machinery, not
        # the argument, is what went wrong.
        return Unresolved("check-crashed",
                          f"the subject file could not be written: {exc}")
    return Subject(path, tuple((flag, source) for flag, source, _ in reads))


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
