"""Team sidecar sync (#113): git plumbing for the shared team-memory mirror.

Layout (extends the #111 team dir):
    <team_dir>/local/                    Phase 1 local-only mirror (no git)
    <team_dir>/<remote-slug>/            one git CLONE per team remote (sidecar)
        authors/<author-slug>/*.json     immutable per-author checkpoint files

The multi-writer git spike verdict is LAW here:
  1. Only immutable per-author files are synced — NO mutable pointers ever land
     in the sidecar. Disjoint append-only paths make merges conflict-free by
     construction.
  2. The sidecar is a separate private repo, one directory per remote slug —
     never the user's project repo. Every git call runs with cwd (-C) pinned to
     the sidecar.
  3. ls-remote freshness gate: object transfer (fetch) happens ONLY on a remote
     HEAD-hash mismatch against the local remote-tracking ref. No blind
     fetch-before-read.

Guard rails:
  - NEVER force-push. Not under any code path.
  - A fetch that shows the old remote-tracking commit is no longer an ancestor
    of the new remote head means someone rewrote shared history: warn loudly,
    touch nothing, do NOT auto-repair.
  - Author identity is declared, not authenticated (repo ACL is the membership
    boundary), but commits give free provenance: files newly arrived in a fetch
    are cross-checked — stamped JSON `author` vs the git author who introduced
    the file — and disagreement is surfaced as a warning, never a block.

Concurrency (scar 0002 shape): two syncs racing on the same sidecar are
serialized by git's own index.lock — the loser fails a step, its report carries
a warning, and the next opportunistic sync retries. No custom locking.

All git via subprocess (stdlib), a timeout on EVERY call, cwd always the
sidecar directory.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, store

GIT_TIMEOUT = 15        # seconds — local plumbing calls
NET_TIMEOUT = 60        # seconds — ls-remote / fetch / push / clone touch the network
PUSH_RETRIES = 3        # bounded reject->integrate->push loop
STALE_LOCK_SECONDS = 600  # an index.lock older than this belongs to a dead git

_STUB_README = (
    "# daimon team memory\n\n"
    "Shared team-memory sidecar managed by `daimon team sync`.\n"
    "Only immutable per-author checkpoint files live here — do not hand-edit.\n"
)


class TeamError(Exception):
    """Real user error (rc 1): bad URL shape, init on an existing dir, clone
    failure. Everything else in sync degrades gracefully to rc 0."""


def _run_git(argv, timeout) -> subprocess.CompletedProcess:
    """Run a git argv NON-INTERACTIVELY and never raise, whatever git does.

    - GIT_TERMINAL_PROMPT=0 + stdin=DEVNULL turn a missing-credential remote
      into a fast rc != 0 instead of a blocked interactive credential prompt
      that hangs until the timeout fires.
    - A TimeoutExpired (a SubprocessError, NOT an OSError — so it would slip
      past every caller's OSError catch) is converted into a synthetic FAILED
      CompletedProcess (rc 124, the shell timeout convention). Every caller
      already maps rc != 0 to the right outcome (offline for sync, TeamError
      for init/clone), so the documented "never raises" contract holds
      uniformly under a hung remote, not just under a clean rc != 0."""
    try:
        return subprocess.run(
            argv,
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            argv, 124, stdout="", stderr=f"git timed out after {timeout}s",
        )


def _git(cwd, *args, timeout=GIT_TIMEOUT) -> subprocess.CompletedProcess:
    """One git call, cwd pinned to the sidecar, never raises (rc != 0 or timeout)."""
    return _run_git(["git", "-C", str(cwd), *args], timeout)


def _last_line(text: str) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def remote_slug(url: str) -> str:
    """Directory slug for a remote URL: scheme + trailing .git stripped, then
    the same project_slug munging every other daimon dir identity uses."""
    s = (url or "").strip()
    s = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", s)
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[: -len(".git")]
    slug = store.project_slug(s)
    if not slug:
        raise TeamError(f"bad remote url: {url!r}")
    return slug


def list_remotes() -> list[Path]:
    """Sidecar clones under the team dir: every subdir with a .git entry.
    'local' is the Phase 1 pointer-free mirror, never a clone."""
    try:
        entries = sorted(config.team_dir().iterdir())
    except OSError:
        return []
    return [
        p for p in entries
        if p.is_dir() and p.name != store._TEAM_LOCAL_REMOTE and (p / ".git").exists()
    ]


def _head(sidecar) -> str | None:
    proc = _git(sidecar, "rev-parse", "--verify", "-q", "HEAD")
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _rev(sidecar, ref) -> str | None:
    proc = _git(sidecar, "rev-parse", "--verify", "-q", ref)
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _branch(sidecar) -> str:
    """Current branch name; works on an unborn HEAD too."""
    proc = _git(sidecar, "rev-parse", "--abbrev-ref", "HEAD")
    name = proc.stdout.strip()
    return name if proc.returncode == 0 and name and name != "HEAD" else "main"


def _identity_extra(sidecar) -> list[str]:
    """`-c user.*` fallbacks for every git operation that CREATES COMMITS
    (commit AND merge). Ambient identity is preferred so commits carry REAL
    provenance (the author-vs-committer guard rail depends on it); the
    daimon-derived one fills in only when nothing is configured — an
    unconfigured machine (or an identity-less CI runner, where git's
    auto-detection fails) must not break sync."""
    if _git(sidecar, "config", "user.name").stdout.strip():
        return []
    author = config.author()
    return ["-c", f"user.name={author}",
            "-c", f"user.email={store.project_slug(author)}@daimon.invalid"]


def _commit(sidecar, message) -> subprocess.CompletedProcess:
    return _run_git(
        ["git", "-C", str(sidecar), *_identity_extra(sidecar), "commit", "-m", message],
        GIT_TIMEOUT,
    )


def _validate_remote_url(url: str) -> None:
    """Refuse URLs git would parse as something other than a plain remote.

    A leading '-' turns the URL into a git OPTION on the clone argv
    (--upload-pack=... is the classic remote-code-execution shape), and the
    ext::/fd:: pseudo-transports execute arbitrary local commands by design.
    Belt-and-braces with the '--' separator on the clone call below."""
    s = (url or "").strip()
    if not s or "\n" in s or "\r" in s:
        raise TeamError(f"invalid remote URL: {url!r}")
    if s.startswith("-"):
        raise TeamError(f"invalid remote URL (option-shaped): {url!r}")
    lowered = s.lower()
    if lowered.startswith(("ext::", "fd::")):
        raise TeamError(f"unsupported remote transport: {url!r}")


def init(url: str) -> Path:
    """`daimon team init <remote-url>`: clone the private sidecar into
    <team_dir>/<remote-slug>/. An EMPTY remote is fine — the unborn branch is
    seeded with a README-stub root commit and pushed, so every later sync has a
    branch to ls-remote against. Raises TeamError on real user errors."""
    _validate_remote_url(url)
    slug = remote_slug(url)
    if shutil.which("git") is None:
        raise TeamError("git not found on PATH — team sync needs git")
    root = config.team_dir()
    dest = root / slug
    if dest.exists():
        raise TeamError(
            f"already initialized: {dest} exists — "
            "run `daimon team sync`, or remove the directory to re-clone"
        )
    root.mkdir(parents=True, exist_ok=True)
    proc = _run_git(["git", "clone", "--", url, str(dest)], NET_TIMEOUT)
    if proc.returncode != 0:
        raise TeamError(f"git clone failed: {_last_line(proc.stderr)}")
    if _head(dest) is None:  # unborn branch: the remote was empty
        (dest / "README.md").write_text(_STUB_README, encoding="utf-8")
        _git(dest, "add", "--", "README.md")
        cp = _commit(dest, "sync: initialize team memory sidecar")
        if cp.returncode != 0:
            raise TeamError(f"could not seed root commit: {_last_line(cp.stderr or cp.stdout)}")
        push = _git(dest, "push", "-u", "origin", _branch(dest), timeout=NET_TIMEOUT)
        if push.returncode != 0:
            # Clone worked, so the remote exists — treat a failed seed push as
            # offline-ish: the next sync will push it. Not a user error.
            print(f"daimon team: seed push failed ({_last_line(push.stderr)}) — "
                  "the next `daimon team sync` will retry")
    return dest


# ---- sync ----


def _ls_remote(sidecar, branch) -> tuple[str, str | None]:
    """Freshness probe, refs only — transfers NO objects.
    ('ok', hash) | ('unborn', None) remote reachable but branch absent |
    ('offline', None) remote unreachable / no remote configured."""
    proc = _git(sidecar, "ls-remote", "origin", f"refs/heads/{branch}",
                timeout=NET_TIMEOUT)
    if proc.returncode != 0:
        return ("offline", None)
    out = proc.stdout.strip()
    if not out:
        return ("unborn", None)
    return ("ok", out.split()[0])


def _commit_own(sidecar, report) -> None:
    """Stage + commit NEW files under authors/<own-author-slug>/ ONLY. The add
    is path-scoped to the own-author dir, so other authors' paths (and any
    stray junk in the sidecar) are never touched — disjoint append-only paths
    are what make the whole scheme conflict-free."""
    own = store.project_slug(config.author()) or "unknown"
    own_dir = f"authors/{own}"
    status = _git(sidecar, "status", "--porcelain", "--", own_dir)
    if status.returncode != 0 or not status.stdout.strip():
        return
    _git(sidecar, "add", "--", own_dir)
    staged = _git(sidecar, "diff", "--cached", "--name-only")
    n = len([ln for ln in staged.stdout.splitlines() if ln.strip()])
    if not n:
        return
    cp = _commit(sidecar, f"sync: {config.author()} {n} checkpoint(s)")
    if cp.returncode == 0:
        report["committed"] = n
    else:
        report["warnings"].append(
            f"commit failed: {_last_line(cp.stderr or cp.stdout)}"
        )


def _is_ancestor(sidecar, old, new) -> bool:
    return _git(sidecar, "merge-base", "--is-ancestor", old, new).returncode == 0


def _check_author_mismatch(sidecar, old, new, report) -> None:
    """Guard rail (issue #113 comment): for files newly arrived in a fetch,
    cross-check the stamped JSON `author` against the git author who introduced
    the file. Identity is declared-not-authenticated — surface disagreement,
    never block. Compared via project_slug so stamp-vs-config munging noise
    ("Ada Lovelace" vs "Ada-Lovelace") does not false-alarm."""
    if not old:
        return  # first-ever content fetch: no arrival window to diff
    diff = _git(sidecar, "diff", "--name-only", "--diff-filter=A", old, new)
    if diff.returncode != 0:
        return
    for path in diff.stdout.splitlines():
        path = path.strip()
        if not re.fullmatch(r"authors/[^/]+/[^/]+\.json", path):
            continue
        show = _git(sidecar, "show", f"{new}:{path}")
        if show.returncode != 0:
            continue
        try:
            stamped = json.loads(show.stdout).get("author")
        except (json.JSONDecodeError, AttributeError):
            continue
        log = _git(sidecar, "log", "--diff-filter=A", "--format=%an", "-1",
                   new, "--", path)
        committer = _last_line(log.stdout)
        if not stamped or not committer:
            continue
        if store.project_slug(str(stamped)) != store.project_slug(committer):
            report["warnings"].append(
                f"author mismatch: {path} claims author '{stamped}' but was "
                f"committed by '{committer}' — identity is declared, not "
                "authenticated; verify with your team"
            )


def _integrate(sidecar, branch, report) -> bool:
    """Fetch + merge the remote branch (only ever called AFTER the ls-remote
    gate saw a mismatch, or after a push reject). Returns False when sync for
    this remote must stop (offline mid-flight, history rewrite, merge failure).
    NEVER force-pushes and never auto-repairs a rewritten remote."""
    tracking_ref = f"refs/remotes/origin/{branch}"
    old = _rev(sidecar, tracking_ref)
    fetch = _git(sidecar, "fetch", "origin", timeout=NET_TIMEOUT)
    if fetch.returncode != 0:
        report["notes"].append("offline — fetch skipped, using last-synced team state")
        return False
    report["fetched"] = True
    new = _rev(sidecar, tracking_ref)
    if new is None:
        return True  # remote branch still unborn: nothing to merge
    if old and old != new and not _is_ancestor(sidecar, old, new):
        report["warnings"].append(
            f"remote history REWRITTEN under {sidecar.name} (previous commits "
            "vanished from remote ancestry) — leaving local copy untouched; "
            "daimon never force-pushes and will not auto-repair this. "
            "Resolve with your team."
        )
        return False
    if old == new:
        return True  # nothing new arrived (raced push already integrated)
    if _head(sidecar) is None:
        merge = _git(sidecar, "checkout", "-B", branch, tracking_ref)
    else:
        # A merge creates a commit — it needs the same identity fallback as
        # _commit or it fails on identity-less machines (CI runners).
        merge = _git(sidecar, *_identity_extra(sidecar),
                     "merge", "--no-edit", tracking_ref)
    if merge.returncode != 0:
        _git(sidecar, "merge", "--abort")
        report["warnings"].append(
            f"merge failed: {_last_line(merge.stderr or merge.stdout)}"
        )
        return False
    _check_author_mismatch(sidecar, old, new, report)
    return True


def _needs_push(sidecar, branch) -> bool:
    head = _head(sidecar)
    if head is None:
        return False
    tracking = _rev(sidecar, f"refs/remotes/origin/{branch}")
    if tracking is None:
        return True  # remote branch unborn, local commits exist
    count = _git(sidecar, "rev-list", "--count", f"{tracking}..{head}")
    try:
        return int(count.stdout.strip()) > 0
    except ValueError:
        return False


def _recover_wedge(sidecar: Path, report: dict) -> bool:
    """Self-heal sidecar states a killed/timed-out git leaves behind (review
    finding: they wedged every later sync with no way out). Returns False when
    the remote must be skipped this pass.

    - MERGE_HEAD: abort the half-merge (own commits are untouched; the merge
      only ever integrates fetched refs). Abort fails -> loud manual hint.
    - index.lock: remove only when STALE (older than STALE_LOCK_SECONDS) — a
      fresh lock is a live concurrent sync (scar 0002: git's own lock IS the
      serialization; never steal it), degrade and let git report benignly."""
    try:
        if (sidecar / ".git" / "MERGE_HEAD").exists():
            _git(sidecar, "merge", "--abort")
            if (sidecar / ".git" / "MERGE_HEAD").exists():
                report["warnings"].append(
                    "unfinished merge could not be aborted — run "
                    f"`git -C {sidecar} merge --abort` (then `git reset --hard` "
                    "if that fails) and re-sync"
                )
                return False
            report["notes"].append("recovered from an interrupted merge (aborted)")
        lock = sidecar / ".git" / "index.lock"
        if lock.exists():
            age = time.time() - lock.stat().st_mtime
            if age > STALE_LOCK_SECONDS:
                lock.unlink()
                report["notes"].append(
                    f"removed stale index.lock ({int(age)}s old — a crashed git left it)"
                )
            else:
                report["notes"].append(
                    "another sync appears to be running (fresh index.lock) — deferring"
                )
    except OSError as exc:
        report["warnings"].append(f"wedge recovery failed: {exc}")
        return False
    return True


def sync_remote(sidecar: Path) -> dict:
    """One remote's full sync pass. Never raises; every outcome lands in the
    report: {slug, committed, pushed, fetched, warnings[], notes[]}.

    Order: recover wedged state -> commit own files -> ls-remote freshness gate
    (fetch+merge only on HEAD-hash mismatch) -> push, with a bounded
    reject->integrate->push retry loop (rejects are benign: another author won
    the race)."""
    report = {"slug": sidecar.name, "committed": 0, "pushed": False,
              "fetched": False, "warnings": [], "notes": []}
    if not _recover_wedge(sidecar, report):
        return report
    _commit_own(sidecar, report)
    branch = _branch(sidecar)
    state, remote_hash = _ls_remote(sidecar, branch)
    if state == "offline":
        report["notes"].append("offline — sync deferred, using last-synced team state")
        return report
    if state == "ok" and remote_hash != _rev(sidecar, f"refs/remotes/origin/{branch}"):
        if not _integrate(sidecar, branch, report):
            return report
    for _ in range(PUSH_RETRIES):
        if not _needs_push(sidecar, branch):
            break
        push = _git(sidecar, "push", "-u", "origin", branch, timeout=NET_TIMEOUT)
        if push.returncode == 0:
            report["pushed"] = True
            break
        if not _integrate(sidecar, branch, report):  # reject: integrate, retry
            return report
    else:
        report["warnings"].append(f"push still rejected after {PUSH_RETRIES} attempts")
    return report


def sync() -> list[dict]:
    """Sync every configured remote. Library entry point — the CLI prints the
    reports; the SessionStart hook spawns the CLI detached. Returns [] when git
    is absent or no remote is configured (both are sync-nothing-to-do)."""
    if not git_available():
        return []
    return [sync_remote(sidecar) for sidecar in list_remotes()]


def git_available() -> bool:
    return shutil.which("git") is not None


# ---- status ----


def _authors_seen(sidecar) -> list[str]:
    try:
        return sorted(
            p.name for p in (sidecar / "authors").iterdir() if p.is_dir()
        )
    except OSError:
        return []


def _unpushed_count(sidecar, branch) -> int:
    head = _head(sidecar)
    if head is None:
        return 0
    tracking = _rev(sidecar, f"refs/remotes/origin/{branch}")
    spec = f"{tracking}..{head}" if tracking else head
    proc = _git(sidecar, "rev-list", "--count", spec)
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def team_status() -> list[dict]:
    """Per-remote view for `daimon team status`: freshness (ls-remote when
    online, 'as of last sync' when offline), own unpushed count, authors seen.
    Never raises; [] when no remote is configured."""
    rows = []
    for sidecar in list_remotes():
        branch = _branch(sidecar)
        state, remote_hash = _ls_remote(sidecar, branch)
        tracking = _rev(sidecar, f"refs/remotes/origin/{branch}")
        if state == "offline":
            freshness = "offline — as of last sync"
        elif state == "unborn" or remote_hash == tracking:
            freshness = "fresh"
        else:
            freshness = "behind remote — run `daimon team sync`"
        rows.append({
            "slug": sidecar.name,
            "branch": branch,
            "freshness": freshness,
            "unpushed": _unpushed_count(sidecar, branch),
            "authors": _authors_seen(sidecar),
        })
    return rows


def status_line() -> str | None:
    """ONE objective line for `daimon status` (the #84 health-line pattern),
    or None when the team feature is unused — no remote, no line, no false
    alarms. Deliberately offline-cheap: no network, just local git plumbing."""
    remotes = list_remotes()
    if not remotes or not git_available():
        return None
    parts = []
    for sidecar in remotes:
        branch = _branch(sidecar)
        unpushed = _unpushed_count(sidecar, branch)
        authors = len(_authors_seen(sidecar))
        parts.append(f"{sidecar.name}: {unpushed} unpushed, "
                     f"{authors} author{'s' if authors != 1 else ''} seen")
    return f"team: {len(remotes)} remote{'s' if len(remotes) != 1 else ''} — " \
           + "; ".join(parts)
