"""The one module that spawns `git` or `gh`, or reads a `.git` entry on a
project directory.

Invariant: repository-controlled state in a directory daimon did not create
never decides WHICH repository daimon answers about, WHICH config it reads, or
WHAT program runs, and every git call it makes goes through this module, so
that rule has exactly one place to live. tests/test_hostgit_census.py fails the
suite when any other module spawns git/gh, builds a `git`/`gh` argv, or touches
a `.git` path outside its short, reasoned allowlist.

Public surface (kept tiny on purpose; other packages are meant to import it):

    configure(posture=...)   select the posture for this process
    posture()                the posture in force (configure, then env, then AMBIENT)
    project(path)            -> Checkout(root, gitdir) | Refused(reason)
    origin_url(path)         -> str | None
    branch(path)             -> str | None
    sidecar(path, *args)     -> CompletedProcess   (daimon-owned team clones)
    Refused                  a project read that produced no answer, with a reason

plus the small helpers the current callers need (`user_name`, `remote_listing`,
`refs_dir`, `has_entry`, `clone`, `gh_path`, `gh_popen`, `git_available`).

Two postures, chosen once per process by `configure(posture=...)` or the
`DAIMON_GIT_POSTURE` environment variable (`ambient` | `sealed`):

- AMBIENT (the default; the CLI): git answers as it always has. The environment
  is os.environ (so an ambient GIT_DIR / GIT_WORK_TREE is still honored, which
  the project-root resolver relies on), timeouts are the callers' own, and no
  function raises: a failed read is a value (`Refused`, None, "").
- SEALED (offices, `daimon serve`, any embedder whose project directories are
  writable by a less trusted party): the checkout is found by hostgit, not by
  git. `project(path)` walks up from `path` with lstat, strictly below a
  `ceiling` (default `Path.home()`, `configure(..., ceiling=...)`), and the
  first directory holding a `.git` entry decides. That entry must be a real
  directory (not a file, not a symlink) shaped like a main gitdir (HEAD,
  objects/, refs/, no commondir). Anything else is `Refused(code)`:

      not-a-dir            the path is not an existing directory
      outside-ceiling      the path (symlinks resolved) is not below the ceiling
      no-git-below-ceiling no .git entry between the path and the ceiling
      gitfile              .git is a regular file (a pointer)
      symlink              .git, or the gitdir's config, is a symlink
      empty-gitdir         .git is not shaped like a main gitdir
      commondir            the gitdir carries a commondir pointer
      unreadable           an entry could not be inspected

  The ceiling is exclusive: the ceiling directory itself is never adopted as a
  checkout, so a host should pass the directory that CONTAINS its projects, not
  a project.

  A refusal spawns nothing, is recorded (`refusals()`) and logged once.
  What does run uses a fresh environment (PATH, LANG, LC_ALL, TMPDIR only) plus
  GIT_DIR / GIT_COMMON_DIR / GIT_WORK_TREE pinned to the validated paths, no
  system or global config, cwd = the checkout root, and config is read with
  `git config --file <gitdir>/config --no-includes`, so only that one file
  counts. The branch comes from `<gitdir>/HEAD`; `user_name` spawns nothing.
  Tradeoffs: a linked worktree or submodule (`.git` file) is refused; `url.*
  .insteadOf` rewrites do not apply to the URL read from the file.

`project()` returns a `Checkout(root, gitdir)` already, so the strict posture
can fill `gitdir` with a validated path without reshaping a single caller. In
AMBIENT `gitdir` is None: git decides, and hostgit does not pretend to know.

Everything that actually forks goes through `_spawn` (run to completion) or
`_spawn_async` (a Popen for the worldcheck fan-out). Tests replace them with a
recorder and assert on the argv, env, cwd and timeout built here.

Stdlib only, and it imports nothing else from the package, so any layer may
use it.
"""

from __future__ import annotations

import atexit
import enum
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

POSTURE_ENV = "DAIMON_GIT_POSTURE"

PROJECT_TIMEOUT = 2      # seconds: a project read must never stall a hook
ORIGIN_TIMEOUT = 5       # seconds: remote lookup default (callers may pass theirs)
SIDECAR_TIMEOUT = 15     # seconds: local plumbing in a team sidecar


class Posture(enum.Enum):
    AMBIENT = "ambient"
    SEALED = "sealed"


@dataclass(frozen=True)
class Checkout:
    """A project checkout as hostgit resolved it. `root` is the work tree top;
    `gitdir` is the validated git directory, or None when git was left to
    decide it (AMBIENT)."""

    root: str
    gitdir: str | None = None


@dataclass(frozen=True)
class Refused:
    """A project read that gave no answer. `reason` is a short stable code
    ("git-failed" in AMBIENT: git missing, slow, or exited non-zero)."""

    reason: str


_configured: Posture | None = None
_ceiling: str | None = None
_refusal_log: dict[tuple[str, str], None] = {}


def _coerce(value: Posture | str) -> Posture:
    if isinstance(value, Posture):
        return value
    try:
        return Posture(str(value).strip().lower())
    except ValueError:
        raise ValueError(
            f"unknown git posture {value!r}: use 'ambient' or 'sealed'"
        ) from None


def configure(*, posture: Posture | str | None, ceiling=None) -> None:
    """Select the posture for this process; None goes back to the env/default.
    An explicit choice beats `DAIMON_GIT_POSTURE`. `ceiling` is the trust root
    SEALED never walks above (default `Path.home()`; None keeps the default).
    Configure before the first project read: callers memoize what they ask."""
    global _configured, _ceiling
    _configured = None if posture is None else _coerce(posture)
    _ceiling = None if ceiling is None else os.fspath(ceiling)


def ceiling() -> str:
    """The SEALED trust root, symlinks resolved."""
    return os.path.realpath(_ceiling if _ceiling is not None else Path.home())


def posture() -> Posture:
    """The posture in force. A bad `DAIMON_GIT_POSTURE` value is an error, not
    a quiet fallback to AMBIENT."""
    if _configured is not None:
        return _configured
    raw = os.environ.get(POSTURE_ENV)
    if raw is None or not raw.strip():
        return Posture.AMBIENT
    try:
        return _coerce(raw)
    except ValueError:
        raise ValueError(
            f"{POSTURE_ENV}={raw!r}: use 'ambient' or 'sealed'") from None


def _reset() -> None:
    """Back to an unconfigured process. Test hook (conftest calls it)."""
    global _configured, _ceiling
    _configured = None
    _ceiling = None
    _warned.clear()
    _refusal_log.clear()


# ---- the spawn seam -------------------------------------------------------


def _spawn(argv, env, cwd, timeout, stdin=None) -> subprocess.CompletedProcess:
    """Run argv to completion, output captured as text. The only place a git
    command is forked; raises whatever subprocess raises, callers contain it."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout,
        env=env, cwd=cwd, stdin=stdin,
    )


def _spawn_async(argv, env, cwd, stdin):
    """Popen for the parallel worldcheck probes: stdout piped, stderr dropped,
    stdin a pipe (`subprocess.PIPE`) or /dev/null."""
    return subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=stdin,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


# ---- argv and env, built in one place -------------------------------------


_scratch: str | None = None


def _scratch_root() -> str | None:
    """A private, daimon-owned directory (mode 0700) created once per process
    and removed at exit. None when one cannot be made."""
    global _scratch
    if _scratch is None:
        try:
            root = tempfile.mkdtemp(prefix="daimon-hostgit-")
        except OSError:
            return None
        atexit.register(shutil.rmtree, root, ignore_errors=True)
        _scratch = root
    return _scratch


def _hooks_dir() -> str:
    """The empty directory every git call names as `core.hooksPath`, so no
    hook can run whatever a repository's own config says. git treats a
    hooksPath with no such hook file as "no hook"; `/dev/null` behaves the same
    on git 2.50 (checked), so it is the fallback when no scratch dir exists."""
    root = _scratch_root()
    if root is None:
        return os.devnull
    path = os.path.join(root, "no-hooks")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        return os.devnull
    return path


def _hardening(*, sidecar: bool = False) -> list[str]:
    """The `-c` options in front of EVERY git subcommand hostgit runs: no
    filesystem-monitor program, no hooks. Team sidecar calls (which fetch and
    push) also refuse the `ext::` transport."""
    out = ["-c", "core.fsmonitor=false", "-c", f"core.hooksPath={_hooks_dir()}"]
    if sidecar:
        out += ["-c", "protocol.ext.allow=never"]
    return out


def _argv(*args, path=None, options=(), sidecar=False) -> list[str]:
    """`git <hardening> [-C path] [options...] args...`"""
    head = ["git", *_hardening(sidecar=sidecar)]
    if path is not None:
        head += ["-C", str(path)]
    return [*head, *options, *(str(a) for a in args)]


def _project_env() -> dict[str, str]:
    """AMBIENT: the process environment, copied. Ambient GIT_* is honored."""
    return dict(os.environ)


def _sidecar_env() -> dict[str, str]:
    """Never block on a credential prompt: a missing credential is a fast
    failure, not a hang until the timeout."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


_SPAWN_FAILURES = (OSError, subprocess.SubprocessError, ValueError)


def _read(argv, *, cwd=None, timeout) -> str | None:
    """Run a project read; stdout on rc 0, else None. Never raises."""
    try:
        proc = _spawn(argv, _project_env(), cwd, timeout)
    except _SPAWN_FAILURES:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


# ---- SEALED: finding and validating the checkout -------------------------

_SEALED_PASS = ("PATH", "LANG", "LC_ALL", "TMPDIR")
_REFUSAL_CAP = 256


def _sealed() -> bool:
    return posture() is Posture.SEALED


def refusals() -> list[tuple[str, str]]:
    """(path, code) for every checkout SEALED refused in this process."""
    return list(_refusal_log)


def _refuse(path, code: str) -> Refused:
    key = (os.fspath(path), code)
    if key not in _refusal_log:
        if len(_refusal_log) < _REFUSAL_CAP:
            _refusal_log[key] = None
        log.warning("hostgit: git checkout %s refused (%s)", key[0], code)
    return Refused(code)


def _below(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError:
        return None


def _gitdir_problem(git: str) -> str | None:
    """Why `git` is not a plain main gitdir (a code), or None."""
    if _lstat(os.path.join(git, "commondir")) is not None:
        return "commondir"
    for name, want_dir in (("HEAD", False), ("objects", True), ("refs", True)):
        st = _lstat(os.path.join(git, name))
        if st is None:
            return "empty-gitdir"
        ok = stat.S_ISDIR(st.st_mode) if want_dir else stat.S_ISREG(st.st_mode)
        if not ok:
            return "empty-gitdir"
    cfg = _lstat(os.path.join(git, "config"))
    if cfg is not None and stat.S_ISLNK(cfg.st_mode):
        return "symlink"
    return None


def _inspect(directory: str) -> Checkout | str | None:
    """None when `directory` has no `.git` entry; a Checkout when it holds a
    valid main gitdir; else the refusal code."""
    dot = os.path.join(directory, ".git")
    try:
        st = os.lstat(dot)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if stat.S_ISREG(st.st_mode):
        return "gitfile"
    if not stat.S_ISDIR(st.st_mode):
        return "not-a-dir"
    return _gitdir_problem(dot) or Checkout(root=directory, gitdir=dot)


def _seal(path) -> Checkout | Refused:
    raw = os.fspath(path)
    if not os.path.isdir(raw):
        return _refuse(raw, "not-a-dir")
    real = os.path.realpath(raw)
    top = ceiling()
    if not _below(real, top):
        return _refuse(raw, "outside-ceiling")
    current = real
    while current != top:
        got = _inspect(current)
        if isinstance(got, Checkout):
            return got
        if isinstance(got, str):
            return _refuse(raw, got)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return _refuse(raw, "no-git-below-ceiling")


def _sealed_env(co: Checkout) -> dict[str, str]:
    env = {k: os.environ[k] for k in _SEALED_PASS if k in os.environ}
    env.update({
        "GIT_DIR": str(co.gitdir), "GIT_COMMON_DIR": str(co.gitdir),
        "GIT_WORK_TREE": co.root, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
    })
    return env


def _sealed_config(co: Checkout, *flags) -> subprocess.CompletedProcess | None:
    """`git config --file <gitdir>/config --no-includes <flags>` in the root
    under the sealed env, or None when git could not run."""
    argv = _argv("config", "--file", os.path.join(str(co.gitdir), "config"),
                 "--no-includes", *flags)
    try:
        return _spawn(argv, _sealed_env(co), co.root, PROJECT_TIMEOUT)
    except _SPAWN_FAILURES:
        return None


def _sealed_head_branch(co: Checkout) -> str | None:
    git = str(co.gitdir)
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8",
                  errors="replace") as handle:
            head = handle.read(512).strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    if not head.startswith(prefix):
        return None  # detached
    name = head[len(prefix):]
    if not name or "\n" in name or "\x00" in name or ".." in name:
        return None
    # An unborn branch has no ref yet; rev-parse answers nothing for it, so
    # the sealed read does too.
    if os.path.isfile(os.path.join(git, "refs", "heads", *name.split("/"))):
        return name
    try:
        with open(os.path.join(git, "packed-refs"), encoding="utf-8",
                  errors="replace") as handle:
            needle = f" refs/heads/{name}"
            if any(line.rstrip("\n").endswith(needle) for line in handle):
                return name
    except OSError:
        pass
    return None


# ---- project checkouts ----------------------------------------------------


def project(path) -> Checkout | Refused:
    """The checkout `path` belongs to. AMBIENT: git's own answer for the
    work-tree top. SEALED: the validated checkout, or a `Refused` code."""
    if _sealed():
        return _seal(path)
    out = _read(_argv("rev-parse", "--show-toplevel", path=path),
                timeout=PROJECT_TIMEOUT)
    top = (out or "").strip()
    return Checkout(root=top) if top else Refused("git-failed")


def origin_url(path, timeout: float = ORIGIN_TIMEOUT) -> str | None:
    """`origin`'s URL, or None on any failure (no git, not a repo, no remote)."""
    if _sealed():
        co = project(path)
        if isinstance(co, Refused):
            return None
        done = _sealed_config(co, "--get", "remote.origin.url")
        if done is None or done.returncode != 0:
            return None
        return done.stdout.strip() or None
    out = _read(_argv("remote", "get-url", "origin", path=path), timeout=timeout)
    return (out or "").strip() or None


def branch(path) -> str | None:
    """The checked-out branch name, or None: not a repo, unborn or DETACHED HEAD
    (rev-parse prints the literal "HEAD" for that, which is not a branch)."""
    if _sealed():
        co = project(path)
        return None if isinstance(co, Refused) else _sealed_head_branch(co)
    out = _read(_argv("rev-parse", "--abbrev-ref", "HEAD", path=path),
                timeout=PROJECT_TIMEOUT)
    name = (out or "").strip()
    return None if not name or name == "HEAD" else name


def remote_listing(path) -> str | None:
    """Every remote URL (AMBIENT: `git remote -v` output), or None when no
    answer could be had. An empty string means git answered and listed no
    remote, which is a different fact."""
    if _sealed():
        co = project(path)
        if isinstance(co, Refused):
            return None
        done = _sealed_config(co, "--get-regexp", r"^remote\..*\.url$")
        if done is None or done.returncode not in (0, 1):
            return None
        return done.stdout if done.returncode == 0 else ""
    return _read(_argv("remote", "-v", path=path), timeout=PROJECT_TIMEOUT)


_GITHUB_URL = re.compile(
    r"^(?:https?://(?:[^@/\s]+@)?|ssh://git@|git@)github\.com[/:]"
    r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")


def github_repo(path) -> str | None:
    """`owner/name` of `origin` when it is a github.com URL, else None."""
    url = origin_url(path)
    match = _GITHUB_URL.match(url or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


def user_name(cwd) -> str:
    """`git config user.name` as seen from `cwd`, "" when unset or on failure.
    SEALED never asks git: the name comes from DAIMON_AUTHOR or the OS user."""
    if _sealed():
        return ""
    out = _read(_argv("config", "user.name"), cwd=cwd, timeout=PROJECT_TIMEOUT)
    return (out or "").strip()


def has_entry(path) -> bool:
    """True when `path` itself holds a `.git` entry. SEALED: a real directory
    only (a file or symlink does not count)."""
    if _sealed():
        st = _lstat(os.path.join(os.fspath(path), ".git"))
        return st is not None and stat.S_ISDIR(st.st_mode)
    return (Path(path) / ".git").exists()


def refs_dir(path) -> Path | None:
    """The git dir whose refs/heads is authoritative for `path`, or None.
    In a linked WORKTREE `.git` is a file pointing at <common>/worktrees/<x>,
    whose refs live back in the common dir; reading the worktree dir instead
    would report every branch gone. SEALED: the validated gitdir only."""
    if _sealed():
        co = project(path)
        if isinstance(co, Refused):
            return None
        refs = Path(str(co.gitdir))
        return refs if (refs / "refs" / "heads").is_dir() else None
    dot = Path(path) / ".git"
    if dot.is_file():
        text = dot.read_text(encoding="utf-8", errors="replace").strip()
        if not text.startswith("gitdir:"):
            return None
        gitdir = Path(text.split(":", 1)[1].strip())
        if not gitdir.is_absolute():
            gitdir = Path(path) / gitdir
        common = gitdir / "commondir"
        if common.is_file():
            rel = common.read_text(encoding="utf-8", errors="replace").strip()
            if rel:
                gitdir = gitdir / rel
        dot = gitdir.resolve()
    # No refs/heads means this is not a readable repo (or not a repo at all):
    # answering MISSING there would fabricate a contradiction for every claim.
    return dot if (dot / "refs" / "heads").is_dir() else None


# ---- gh -------------------------------------------------------------------

# Environment a sealed gh may inherit, from `gh help environment` (gh 2.92):
# its credentials and where it keeps them, and nothing that names a repository
# (GH_REPO), an editor, a pager or a browser.
_GH_PASS = ("PATH", "LANG", "LC_ALL", "TMPDIR", "HOME", "XDG_CONFIG_HOME",
            "APPDATA", "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN",
            "GITHUB_ENTERPRISE_TOKEN", "GH_HOST", "GH_CONFIG_DIR", "GH_PATH",
            "NO_COLOR")


def gh_path() -> str | None:
    return shutil.which("gh")


def git_available() -> bool:
    return shutil.which("git") is not None


def _gh_cwd() -> str:
    """An empty daimon-owned directory: gh never sees a project as its cwd."""
    root = _scratch_root()
    if root is None:
        return tempfile.gettempdir()
    path = os.path.join(root, "gh-cwd")
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        return tempfile.gettempdir()
    return path


def gh_popen(argv, cwd):
    """Start a `gh` probe (argv[0] is the resolved gh) and return the Popen;
    the caller reaps it. AMBIENT: in `cwd` with the process environment, so gh
    finds the repository from `cwd`'s remotes. SEALED: in a private empty
    directory with an allowlisted environment; the argv must name its
    repository (`--repo owner/name`)."""
    if _sealed():
        env = {k: os.environ[k] for k in _GH_PASS if k in os.environ}
        env.update({"GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
                    "GIT_TERMINAL_PROMPT": "0"})
        return _spawn_async(list(argv), env, _gh_cwd(), subprocess.DEVNULL)
    return _spawn_async(list(argv), _project_env(), str(cwd), subprocess.DEVNULL)


# ---- team sidecars (daimon-owned clones) ----------------------------------


def _run_sidecar(argv, timeout) -> subprocess.CompletedProcess:
    """NON-INTERACTIVE and never raises on a hang: a TimeoutExpired (a
    SubprocessError, not an OSError, so it would slip past every caller's
    OSError catch) becomes a synthetic failed process, rc 124, the shell
    timeout convention. Every caller already maps rc != 0 to offline / TeamError."""
    try:
        return _spawn(argv, _sidecar_env(), None, timeout,
                      stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, stdout="", stderr=f"git timed out after {timeout}s")


_warned: set[tuple[str, str]] = set()


def _dir_problem(label: str, path) -> str | None:
    """Why `path` is not a directory only the current user can change, or None.
    lstat, so a symlink is never followed."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        return f"{label} {path} is not readable ({exc.strerror or exc})"
    if stat.S_ISLNK(st.st_mode):
        return f"{label} {path} is a symlink"
    if not stat.S_ISDIR(st.st_mode):
        return f"{label} {path} is not a directory"
    if st.st_uid != os.getuid():
        return f"{label} {path} is not owned by the current user"
    if st.st_mode & 0o022:
        return f"{label} {path} is writable by group or others"
    return None


def sidecar_problem(path) -> str | None:
    """Why `path` is not a trustworthy team sidecar, or None when it is: a real
    directory whose `.git` is a real directory, both owned by the current user
    and neither writable by group or others. POSIX ownership only: where there
    is no uid (Windows) the check has nothing to say."""
    if not hasattr(os, "getuid"):
        return None
    return (_dir_problem("sidecar", path)
            or _dir_problem("sidecar .git", os.path.join(path, ".git")))


def is_sidecar(path) -> bool:
    return sidecar_problem(path) is None


def _refuse_or_warn(path, problem: str, *, sealed: bool, argv):
    """AMBIENT logs the problem once per (path, problem) and lets the call run;
    SEALED answers a synthetic rc 125 and nothing is spawned."""
    if sealed:
        return subprocess.CompletedProcess(
            argv, 125, stdout="", stderr=f"refused: {problem}")
    key = (str(path), problem)
    if key not in _warned:
        _warned.add(key)
        log.warning("hostgit: %s", problem)
    return None


def _guard_sidecar(path, *, sealed: bool, argv=()):
    problem = sidecar_problem(path)
    if problem is None:
        return None
    return _refuse_or_warn(path, problem, sealed=sealed, argv=list(argv))


def _guard_clone(dest, *, sealed: bool, argv=()):
    """A clone's destination does not exist yet, so the directory it lands in
    is what gets checked."""
    if not hasattr(os, "getuid"):
        return None
    problem = _dir_problem("team directory", os.path.dirname(os.fspath(dest)))
    if problem is None:
        return None
    return _refuse_or_warn(dest, problem, sealed=sealed, argv=list(argv))


def sidecar(path, *args, timeout: float = SIDECAR_TIMEOUT,
            options=()) -> subprocess.CompletedProcess:
    """One git call pinned to the team sidecar at `path` (`git -C path ...`).
    `options` are global options placed before the command (`-c k=v`)."""
    argv = _argv(*args, path=path, options=options, sidecar=True)
    refused = _guard_sidecar(path, sealed=posture() is Posture.SEALED, argv=argv)
    return refused if refused is not None else _run_sidecar(argv, timeout)


def clone(url, dest, *, timeout: float) -> subprocess.CompletedProcess:
    """`git clone -- url dest`: the sidecar's birth, no repo exists to pin yet."""
    argv = _argv("clone", "--", url, dest, sidecar=True)
    refused = _guard_clone(dest, sealed=posture() is Posture.SEALED, argv=argv)
    return refused if refused is not None else _run_sidecar(argv, timeout)
