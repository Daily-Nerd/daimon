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

- AMBIENT (the default; the CLI): git answers exactly as it always has. The
  argv is the one the callers used to build inline, the environment is
  os.environ (so an ambient GIT_DIR / GIT_WORK_TREE is still honored, which the
  project-root resolver relies on), timeouts are the callers' own, and no
  function raises: a failed read is a value (`Refused`, None, "").
- SEALED (offices and any embedder whose project directories are writable by a
  less trusted party): not built yet. Selecting it raises NotImplementedError
  rather than silently answering as AMBIENT would, because falling open is the
  one thing a strict posture must never do.

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

import enum
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

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


def _coerce(value: Posture | str) -> Posture:
    if isinstance(value, Posture):
        return value
    try:
        return Posture(str(value).strip().lower())
    except ValueError:
        raise ValueError(
            f"unknown git posture {value!r}: use 'ambient' or 'sealed'"
        ) from None


def configure(*, posture: Posture | str | None) -> None:
    """Select the posture for this process; None goes back to the env/default.
    An explicit choice beats `DAIMON_GIT_POSTURE`."""
    global _configured
    chosen = None if posture is None else _coerce(posture)
    if chosen is Posture.SEALED:
        raise NotImplementedError(_SEALED_MESSAGE)
    _configured = chosen


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
    global _configured
    _configured = None


_SEALED_MESSAGE = (
    "the SEALED git posture is not available in this release: hostgit only "
    "implements AMBIENT. Unset DAIMON_GIT_POSTURE (or configure "
    "posture='ambient') until it ships; daimon refuses to fall back to "
    "AMBIENT on its own."
)


def _gate() -> None:
    if posture() is Posture.SEALED:
        raise NotImplementedError(_SEALED_MESSAGE)


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


def _argv(*args, path=None, options=()) -> list[str]:
    """`git [-C path] [options...] args...`"""
    head = ["git"]
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


# ---- project checkouts ----------------------------------------------------


def project(path) -> Checkout | Refused:
    """The checkout `path` belongs to: git's own answer for its work-tree top."""
    _gate()
    out = _read(_argv("rev-parse", "--show-toplevel", path=path),
                timeout=PROJECT_TIMEOUT)
    top = (out or "").strip()
    return Checkout(root=top) if top else Refused("git-failed")


def origin_url(path, timeout: float = ORIGIN_TIMEOUT) -> str | None:
    """`origin`'s URL, or None on any failure (no git, not a repo, no remote)."""
    _gate()
    out = _read(_argv("remote", "get-url", "origin", path=path), timeout=timeout)
    return (out or "").strip() or None


def branch(path) -> str | None:
    """The checked-out branch name, or None: not a repo, unborn or DETACHED HEAD
    (rev-parse prints the literal "HEAD" for that, which is not a branch)."""
    _gate()
    out = _read(_argv("rev-parse", "--abbrev-ref", "HEAD", path=path),
                timeout=PROJECT_TIMEOUT)
    name = (out or "").strip()
    return None if not name or name == "HEAD" else name


def remote_listing(path) -> str | None:
    """`git remote -v` output, or None when git itself failed. An empty string
    means git answered and listed no remote, which is a different fact."""
    _gate()
    return _read(_argv("remote", "-v", path=path), timeout=PROJECT_TIMEOUT)


def user_name(cwd) -> str:
    """`git config user.name` as seen from `cwd`, "" when unset or on failure."""
    _gate()
    out = _read(_argv("config", "user.name"), cwd=cwd, timeout=PROJECT_TIMEOUT)
    return (out or "").strip()


def has_entry(path) -> bool:
    """True when `path` itself holds a `.git` entry (file, dir or symlink target)."""
    _gate()
    return (Path(path) / ".git").exists()


def refs_dir(path) -> Path | None:
    """The git dir whose refs/heads is authoritative for `path`, or None.
    In a linked WORKTREE `.git` is a file pointing at <common>/worktrees/<x>,
    whose refs live back in the common dir; reading the worktree dir instead
    would report every branch gone."""
    _gate()
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


def gh_path() -> str | None:
    return shutil.which("gh")


def git_available() -> bool:
    return shutil.which("git") is not None


def gh_popen(argv, cwd):
    """Start a `gh` probe (argv[0] is the resolved gh) in `cwd`; the caller
    reaps it. gh finds the repository itself from `cwd`'s remotes."""
    _gate()
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


def sidecar(path, *args, timeout: float = SIDECAR_TIMEOUT,
            options=()) -> subprocess.CompletedProcess:
    """One git call pinned to the team sidecar at `path` (`git -C path ...`).
    `options` are global options placed before the command (`-c k=v`)."""
    _gate()
    return _run_sidecar(_argv(*args, path=path, options=options), timeout)


def clone(url, dest, *, timeout: float) -> subprocess.CompletedProcess:
    """`git clone -- url dest`: the sidecar's birth, no repo exists to pin yet."""
    _gate()
    return _run_sidecar(_argv("clone", "--", url, dest), timeout)
