"""The repair tools that read, annotate and rebind buckets: `bucket stamp`,
`bucket doctor` (slice 3b of "project identity survives moves") and `bucket
adopt` and `bucket remap` (slice 4b).

A bucket's directory name is its permanent id; its `root` file is the one
place that binds it to a location. Both tools are human surfaces. They stat
recorded roots and walk directories, so they never run on a hook path, and they
make no decision of their own: every finding is evidence for a person. `stamp`
and `doctor` change nothing about which bucket a project resolves to; `adopt`
and `remap` are the only verbs that rewrite a bucket's `root`, the one binding,
and only on explicit request: nothing rebinds automatically.

`identity` is the evidence record: where the bucket's project was when it was
stamped (`root`), the anchors that survive a move (origin URL with credentials
stripped, root commit, folder name) and the roots it has had (`history`, which
`bucket adopt` will append to). Read by `doctor` only.
"""

import contextlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import buckets, config, hostgit, identity, jsonl, scope, store

IDENTITY_VERSION = 1

# How far below a `--scan` directory a repository is looked for. Developer
# trees are `~/code/<repo>` or `~/code/<org>/<repo>` and rarely deeper; the
# bound is what keeps an explicit scan of a home directory from walking a disk.
SCAN_DEPTH = 4
# Directories that are never repositories and are expensive to walk.
_SCAN_SKIP = frozenset({"node_modules", ".git", ".venv", "venv",
                        "__pycache__"})


class StampRefused(RuntimeError):
    """The request cannot be carried out as asked; nothing was written."""


# ---------------------------------------------------------------------------
# the identity record
# ---------------------------------------------------------------------------


def public_origin(url) -> str | None:
    """`url` with any credentials removed, or None for nothing. A remote can be
    `https://user:token@host/...`, and the record is plain text beside the
    bucket's other files, so the userinfo never reaches it. The scp-like form
    (`git@host:path`) has no secret in it and is kept."""
    text = str(url or "").strip()
    if not text:
        return None
    if "://" not in text:
        return text
    parts = urlsplit(text)
    if "@" not in parts.netloc:
        return text
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query,
                       parts.fragment))


def anchors_for(path) -> dict:
    """The evidence a location offers that survives a move: its origin URL,
    its root commit, and its folder name. Each git answer is None when the
    location is not a repository, does not exist, or git could not say; the
    folder name is always there."""
    text = os.fspath(path)
    exists = os.path.isdir(text)
    return {
        "origin": public_origin(hostgit.origin_url(text)) if exists else None,
        "root_commit": hostgit.root_commit(text) if exists else None,
        "folder": os.path.basename(text.rstrip("/\\")) or text,
    }


def read_identity(bucket: Path) -> dict | None:
    """The bucket's identity record, or None when it is absent, unreadable,
    not a JSON object or of a version this code does not know. Never raises:
    a corrupt record is a finding for `doctor`, not an error."""
    try:
        record = json.loads(
            (Path(bucket) / store._IDENTITY_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("version") != IDENTITY_VERSION:
        return None
    return record


def write_identity(bucket: Path, root: str, anchors: dict, *,
                   history=(), replace: bool = True) -> bool:
    """Write the identity record. Raises OSError. Written AFTER the bucket's
    `root`, so it is not atomic with it; nothing decides on it, and `doctor`
    reports a transient disagreement.

    `replace=True` swaps an existing record atomically. `replace=False` NEVER
    touches one: the record is staged whole and linked into place, which fails
    when anything is there, and the call returns False. `bucket stamp` uses it
    because a record it could not read (torn, or a newer version carrying roots
    it does not know) is evidence it must not destroy."""
    record = {
        "version": IDENTITY_VERSION,
        "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": root,
        "anchors": dict(anchors),
        "history": list(history),
    }
    text = json.dumps(record, ensure_ascii=False, indent=2)
    path = Path(bucket) / store._IDENTITY_NAME
    if replace:
        store._atomic_write(path, text)
        return True
    return store.create_exclusive(path, text)


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------


def _repositories(scan: list[str]) -> list[str]:
    """Every repository under the `--scan` directories, as realpaths. A
    directory is a repository when it holds a `.git` entry; the walk stops
    there (a repository's own subtree is its business), never follows a
    symlink, and is bounded by SCAN_DEPTH."""
    found: dict[str, None] = {}

    def visit(directory: str, depth: int) -> None:
        if hostgit.has_entry(directory):
            found[os.path.realpath(directory)] = None
            return
        if depth >= SCAN_DEPTH:
            return
        try:
            children = sorted(
                entry.path for entry in os.scandir(directory)
                if entry.name not in _SCAN_SKIP
                and not entry.name.startswith(".")
                and entry.is_dir(follow_symlinks=False))
        except OSError:
            return  # an unreadable directory holds no repository we can name
        for child in children:
            visit(child, depth + 1)

    for directory in scan:
        visit(directory, 0)
    return list(found)


def stamp(scan=(), *, apply: bool = False) -> dict:
    """`daimon bucket stamp`: give every bucket the evidence it needs to
    survive a move. Dry run unless `apply`; a dry run is a plan computed
    without writing anything.

    * A STAMPED bucket without an identity record gains one, built from its
      root (anchors only for a root that exists). An existing record that
      cannot be read is never replaced: it is reported as `unreadable`.
    * An UNSTAMPED bucket (no root, or an empty one) gains its root only from
      an exact match: a repository under a `--scan` directory whose seed slug
      (`store.location_slug` of its realpath) IS the bucket name. Two matches
      refuse and write nothing; none leaves the bucket as it is.

    The recorded root of a stamped bucket is never rewritten, and nothing
    outside the bucket is touched, so the signed flat checkpoints and their
    receipts are byte for byte what they were.

    Returns {"applied": bool, "actions": [{"bucket", "action", ...}]} with
    action one of: identity, root, kept, unreadable, unmatched, ambiguous,
    raced."""
    scan = [os.fspath(p) for p in scan]
    for directory in scan:
        if not os.path.isdir(directory):
            raise StampRefused(f"--scan {directory!r} is not a directory")
    by_slug: dict[str, list[str]] = {}
    for repo in _repositories(scan):
        seed = store.location_slug(repo)
        if seed:
            by_slug.setdefault(seed, []).append(repo)

    actions: list[dict] = []
    for bucket in store.bucket_dirs().buckets:
        name = bucket.name
        root = store.bucket_root(name)
        if root:
            if read_identity(bucket) is not None:
                actions.append({"bucket": name, "action": "kept"})
            elif os.path.lexists(bucket / store._IDENTITY_NAME):
                # Present but unreadable: torn, or a version this code does
                # not know. Left exactly as it is and reported.
                actions.append({"bucket": name, "action": "unreadable"})
            elif not apply:
                actions.append({"bucket": name, "action": "identity",
                                "root": root})
            elif write_identity(bucket, root, anchors_for(root),
                                replace=False):
                actions.append({"bucket": name, "action": "identity",
                                "root": root})
            else:
                actions.append({"bucket": name, "action": "raced"})
            continue
        matches = sorted(by_slug.get(name, []))
        if not matches:
            actions.append({"bucket": name, "action": "unmatched"})
        elif len(matches) > 1:
            actions.append({"bucket": name, "action": "ambiguous",
                            "candidates": matches})
        elif not apply:
            actions.append({"bucket": name, "action": "root",
                            "root": matches[0]})
        elif store.stamp_root(bucket, matches[0]):
            actions.append({"bucket": name, "action": "root",
                            "root": matches[0]})
            write_identity(bucket, matches[0], anchors_for(matches[0]),
                           replace=False)
        else:
            actions.append({"bucket": name, "action": "raced"})
    return {"applied": apply, "actions": actions}


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

# Files a bucket carries that say nothing about its project's history.
_MARKERS = frozenset({store._BUCKET_ROOT_NAME, store._IDENTITY_NAME,
                      store._LEDGER_CENSUS_NAME, store._LOCK_NAME})

FINDINGS = ("dangling", "unstamped", "shared_root", "empty", "ghost",
            "worktree", "leftover", "identity_mismatch", "identity_unreadable")


def _is_empty(bucket: Path) -> bool:
    """Whether the bucket holds nothing but markers and zero-byte files."""
    try:
        for entry in bucket.iterdir():
            if entry.name in _MARKERS:
                continue
            if not entry.is_file() or entry.stat().st_size > 0:
                return False
    except OSError:
        return False
    return True


def _looks_like_worktree(root: str) -> bool:
    """Evidence that `root` is a linked worktree, never proof: its `.git` is a
    pointer file, or its path runs through `.claude/worktrees` (the shape
    agent worktrees are created in, which is also all that is left to look at
    once the directory is gone)."""
    parts = [p for p in root.replace("\\", "/").split("/") if p]
    for first, second in zip(parts, parts[1:]):
        if first == ".claude" and second == "worktrees":
            return True
    return os.path.isdir(root) and hostgit.has_gitfile(root)


def _worktree_fold(name: str, root: str | None) -> str | None:
    """The bucket a live linked worktree now resolves to, when that is another
    bucket that exists: where `name` should be folded. None for anything else
    (no root, a folder that is gone, not linked to a listed main, already the
    answer). A human surface: it may fork git."""
    if not root or not os.path.isdir(root):
        return None
    if identity.home_of(root) == root:
        return None
    answer = identity.resolve(root)
    if not (answer.bound and answer.slug and answer.slug != name):
        return None
    target = store.bucket_dir(answer.slug)
    return answer.slug if target is not None and target.is_dir() else None


def _newest(bucket: Path) -> float:
    return store._file_recency(bucket / store._LATEST) \
        if (bucket / store._LATEST).is_file() else 0.0


def doctor(path=None) -> dict:
    """`daimon bucket doctor`: a read-only survey of every bucket. Returns the
    full survey; whether names are shown is the caller's rule (a TTY only).

    findings, each a list of bucket names:
      dangling            a recorded root that is not a directory on disk
      unstamped           no root, or an empty one
      shared_root         groups of buckets whose roots are the same location
      empty               nothing but markers and zero-byte files
      ghost               empty AND dangling: nothing was ever kept here
      worktree            a root that looks like a linked worktree
      leftover            named as the source of an incomplete merge
      identity_mismatch   an identity record that neither matches the root nor
                          lists it in its history (also a transient state:
                          the record is written after the root)
      identity_unreadable an identity record that cannot be read

    With `path`, `candidates` lists DANGLING buckets whose identity anchors
    (origin URL, root commit) equal that location's, labelled evidence and not
    proof. `suggestions` offers a merge for each shared root, into the bucket
    with the newest session, and for each live linked worktree into the bucket
    it now resolves to; a shared root that LOOKS like a worktree but is not a
    listed one is withheld ("worktree"), because its live location would
    birth the folded bucket again. A candidate is where `bucket adopt`
    starts: the CLI names that command for a person at a terminal, and nothing
    here rebinds anything."""
    found: dict[str, list] = {key: [] for key in FINDINGS}
    roots: dict[str, str] = {}
    dirs = store.bucket_dirs().buckets
    incomplete = buckets.unfinished_sources()
    dangling: set[str] = set()
    for bucket in dirs:
        name = bucket.name
        root = store.bucket_root(name)
        gone = False
        if root:
            roots[name] = root
            gone = not os.path.isdir(root)
            if gone:
                dangling.add(name)
                found["dangling"].append(name)
            if _looks_like_worktree(root):
                found["worktree"].append(name)
        else:
            found["unstamped"].append(name)
        if _is_empty(bucket):
            found["empty"].append(name)
            if gone:
                found["ghost"].append(name)
        if name in incomplete:
            found["leftover"].append(name)
        identity_file = bucket / store._IDENTITY_NAME
        if identity_file.exists():
            record = read_identity(bucket)
            if record is None:
                found["identity_unreadable"].append(name)
            elif root and not (
                    store.root_eq(record.get("root"), root)
                    or any(store.root_eq(old, root)
                           for old in record.get("history") or [])):
                found["identity_mismatch"].append(name)

    groups: dict[str, list[str]] = {}
    for name, root in roots.items():
        groups.setdefault(os.path.normcase(root), []).append(name)
    found["shared_root"] = sorted(sorted(g) for g in groups.values()
                                  if len(g) > 1)

    suggestions: list[dict] = []
    withheld = None
    # A live linked worktree whose sessions now use another bucket: fold it
    # there. Its folder cannot birth the bucket again, which is what the
    # withheld suggestion below protects against.
    folds: dict[str, str] = {}
    for name in found["worktree"]:
        target = _worktree_fold(name, roots.get(name))
        if target:
            folds[name] = target
            suggestions.append({"from": name, "into": target})
    for group in found["shared_root"]:
        looks = [n for n in group if n in found["worktree"]]
        if looks:
            if any(n not in folds for n in looks):
                withheld = "worktree"
            continue
        main = max(group, key=lambda n: (_newest(config.checkpoint_dir() / n),
                                         n))
        suggestions.extend({"from": n, "into": main}
                           for n in group if n != main)

    candidates: list[dict] = []
    resolved = None
    if path is not None:
        # The folder a session opens for what was typed (the one derivation
        # adopt binds), so a candidate is looked up, and its command printed,
        # for a folder that can actually be bound.
        resolved = identity.session_root(path)
        here = anchors_for(resolved)
        for name in sorted(dangling):
            record = read_identity(config.checkpoint_dir() / name)
            if record is None:
                continue
            theirs = record.get("anchors") or {}
            matched = [key for key in ("origin", "root_commit")
                       if here.get(key) and theirs.get(key) == here[key]]
            if matched:
                candidates.append({"bucket": name, "matched": matched})

    return {"buckets": len(dirs), "findings": found,
            "path": resolved, "candidates": candidates,
            "candidates_note": "evidence, not proof",
            "suggestions": suggestions, "suggestions_withheld": withheld}


# ---------------------------------------------------------------------------
# adopt and remap
# ---------------------------------------------------------------------------


class RebindRefused(RuntimeError):
    """The rebind cannot be carried out as asked; nothing was written."""


def _write_rebind(bucket: Path, old: str, new: str) -> str:
    """Rewrite one bucket's `root` from `old` to `new`, under the bucket's
    directory lock, FAIL-CLOSED: a lock that is not held is `locked` and
    nothing is written (the hooks' lock fails open, a verb that rewrites the
    one binding must not). The root is re-read under the lock and a bucket
    that no longer records `old` is `raced`, left as it is. Returns
    `rewrite`, `locked` or `raced`.

    The old root joins the identity record's `history` and the record's root
    follows, written AFTER `root` and not atomically with it (`doctor` reports
    a transient disagreement). An existing record is extended, never rebuilt
    from scratch, so its anchors and older roots stay. Raises OSError."""
    with jsonl.dir_lock(bucket) as held:
        if not held:
            return "locked"
        if not store.root_eq(identity.read_root(bucket), old):
            return "raced"
        record = read_identity(bucket)
        history = list(record.get("history") or []) if record else []
        if old not in history:
            history.append(old)
        anchors = (record.get("anchors") or {}) if record else anchors_for(new)
        # The record FIRST, the root second. A crash between the two leaves a
        # bucket that still records `old`, so a rerun matches it again and
        # finishes (the record's write is idempotent: `old` is already in its
        # history). The other order left a moved root and a stale record that
        # nothing ever repaired.
        write_identity(bucket, new, anchors, history=history, replace=True)
        store._atomic_write(bucket / store._BUCKET_ROOT_NAME, f"{new}\n")
    identity.invalidate()
    return "rewrite"


def _stamp_unstamped(bucket: Path, real: str) -> str:
    """Adopt into a bucket that records no root: stamp it, under its lock."""
    with jsonl.dir_lock(bucket) as held:
        if not held:
            return "locked"
        if identity.read_root(bucket) is not None:
            return "raced"
        store._atomic_write(bucket / store._BUCKET_ROOT_NAME, f"{real}\n")
        record = read_identity(bucket)
        write_identity(bucket, real,
                       (record.get("anchors") or {}) if record
                       else anchors_for(real),
                       history=list(record.get("history") or [])
                       if record else [], replace=True)
    identity.invalidate()
    return "rewrite"


@contextlib.contextmanager
def _store_lock():
    """The checkpoint store's own lock, held across a rebind's collision check
    AND its write, fail-closed. The per-bucket lock cannot make "no other
    bucket records this folder" true: two adopts of different buckets onto one
    folder each hold only their own."""
    with jsonl.dir_lock(config.checkpoint_dir()) as held:
        if not held:
            raise RebindRefused(
                "could not lock the checkpoint store (another daimon process "
                "is rebinding, or it cannot be locked); nothing was changed. "
                "Try again in a moment.")
        yield


def _holder_of(real: str, besides: str) -> str | None:
    """A bucket other than `besides` whose recorded root is `real`, read fresh."""
    return next((n for n, r in sorted(store.bucket_roots().items())
                 if n != besides and store.root_eq(r, real)), None)


def collision_merge_command(name: str, bucket: str) -> str:
    """The `merge` that folds the bucket `name`, which already records the
    folder `bucket` is about to be bound to, into `bucket`. A collision needs
    that folder to exist, and `merge` refuses a source whose recorded folder is
    live (it would birth the bucket again), so the command carries `--force`:
    here the very next step is the rebind that binds the folder to `bucket`.
    One owner for the text `remap` and `adopt` both print."""
    return f"daimon bucket merge --from={name} --into={bucket} --force"


def _collision_message(name: str, bucket: str, real: str) -> str:
    return (f"the bucket {name} already records {real}. Fold it into "
            f"{bucket} first with `{collision_merge_command(name, bucket)}`, "
            f"then run this again, or the folder would answer for two buckets.")


def _bucket_dir_named(name: str) -> Path:
    if scope.parse_bucket(name) is None:
        raise RebindRefused(f"{name!r} is not a bucket name")
    bucket = config.checkpoint_dir() / name
    if not bucket.is_dir() or bucket.is_symlink():
        raise RebindRefused(f"there is no bucket {name}")
    return bucket


def _identity_blocks(bucket: Path) -> bool:
    """An identity record that exists but cannot be read is evidence a rebind
    must not destroy (torn, or a newer version carrying roots this code does
    not know)."""
    path = bucket / store._IDENTITY_NAME
    return os.path.lexists(path) and read_identity(bucket) is None


def adopt(bucket: str, path, *, apply: bool = False, force: bool = False
          ) -> dict:
    """`daimon bucket adopt`: bind bucket `bucket` to the folder `path`, the
    explicit answer when a project moved and no `remap` covers it. Dry run
    unless `apply`.

    REFUSES (nothing written) when the folder is not a directory, when ANY
    other bucket already records it (the day-one fresh bucket: `merge` it
    first, or the folder would answer for two), when the bucket's recorded
    root is a live directory (a clone, D2) unless `force`, when its identity
    record cannot be read, and when the bucket cannot be locked. Adopting what
    is already adopted is a no-op.

    The root becomes `realpath(path)`; the old one joins the identity history.
    `evidence` compares the bucket's recorded anchors with the folder's (origin
    URL, root commit, folder name): labelled evidence, never a decision."""
    target = _bucket_dir_named(bucket)
    text = os.fspath(path)
    if not os.path.isdir(text):
        raise RebindRefused(f"{text!r} is not a directory")
    typed = os.path.realpath(text)
    real = identity.session_root(text)
    if real != typed:
        # Refuse rather than quietly bind another folder than the one typed:
        # sessions open the project root, so a subdirectory would be bound to
        # nothing, and the person asked for THIS folder. Say which to use.
        raise RebindRefused(
            f"{typed} is inside a project, or is a linked worktree of one, whose "
            f"folder is {real}, the one a session binds. Adopt that folder: "
            f"--path {real}")
    holder = _holder_of(real, bucket)
    if holder:
        raise RebindRefused(_collision_message(holder, bucket, real))
    recorded = identity.read_root(target)
    if _identity_blocks(target):
        raise RebindRefused(
            f"the identity record of {bucket} cannot be read, and a rebind "
            f"will not replace evidence it cannot read")
    theirs = ((read_identity(target) or {}).get("anchors") or {})
    here = anchors_for(real)
    shared = [k for k in ("origin", "root_commit", "folder")
              if theirs.get(k) and here.get(k)]
    evidence = {"matched": [k for k in shared if theirs[k] == here[k]],
                "differs": [k for k in shared if theirs[k] != here[k]]}
    plan = {"applied": False, "bucket": bucket, "from": recorded, "to": real,
            "action": "rewrite", "evidence": evidence}
    if store.root_eq(recorded, real):
        return dict(plan, action="kept")
    seed = store.location_slug(real)
    legacy = config.checkpoint_dir() / seed if seed else None
    if (legacy is not None and seed != bucket and not force
            and os.path.isdir(legacy) and identity.read_root(legacy) is None
            and not _is_empty(legacy)):
        raise RebindRefused(
            f"the folder's own bucket {seed} has no recorded root and holds "
            f"history; once {bucket} records the folder, {seed} is never "
            f"found again. Fold it first with `daimon bucket merge "
            f"--from={seed} --into={bucket} --force`, or pass --force here to "
            f"leave it behind.")
    if recorded and not force and os.path.isdir(recorded):
        raise RebindRefused(
            f"the root recorded for {bucket} still exists on disk "
            f"({recorded}): it may be a clone that keeps its own history. "
            f"Pass --force to move it anyway.")
    if not apply:
        return plan
    with _store_lock():
        holder = _holder_of(real, bucket)  # again, under the lock
        if holder:
            raise RebindRefused(_collision_message(holder, bucket, real))
        outcome = (_write_rebind(target, recorded, real) if recorded
                   else _stamp_unstamped(target, real))
    if outcome == "locked":
        raise RebindRefused(
            f"could not lock the bucket {bucket} (another daimon process is "
            f"writing to it, or it cannot be locked); nothing was changed. "
            f"Try again in a moment.")
    if outcome == "raced":
        raise RebindRefused(
            f"the bucket {bucket} was rebound by someone else while this "
            f"ran; nothing was changed. Look at it and run again.")
    return dict(plan, applied=True)


_DRIVE_RE = re.compile(r"[A-Za-z]:(?:[\\/]|$)")


def _windows_shaped(text: str) -> bool:
    return bool(_DRIVE_RE.match(text)) or text.startswith("\\\\")


def _posix_shaped(text: str) -> bool:
    return text.startswith("/")


def _separator(text: str) -> str:
    return "/" if _posix_shaped(text) else "\\"


def _normalized_root(text, *, what: str) -> str:
    """`text` as a root for `remap`: stripped, trailing separators removed, and
    refused when it is not absolute or is so broad it would match everything
    (the filesystem root, a bare drive, a UNC prefix with no share)."""
    raw = str(text or "").strip()
    if not raw:
        raise RebindRefused(f"{what} is empty")
    if not (_posix_shaped(raw) or _windows_shaped(raw)):
        raise RebindRefused(f"{what} {raw!r} is not an absolute path")
    trimmed = raw.rstrip("/\\")
    parts = [p for p in re.split(r"[\\/]+", trimmed) if p]
    if (not trimmed or re.fullmatch(r"[A-Za-z]:", trimmed)
            or (raw.startswith("\\\\") and len(parts) < 2)):
        raise RebindRefused(
            f"{what} {raw!r} would match every bucket; name the folder "
            f"that moved")
    return trimmed


def _below(root: str, base: str) -> str | None:
    """The tail of `root` when it is `base` or lies strictly below it,
    separator-bounded (`/Users/a` never matches `/Users/abc`); else None. The
    tail starts with `base`'s own separator, or is empty for `base` itself."""
    sep = os.path.normcase(_separator(base))
    nroot, nbase = os.path.normcase(root), os.path.normcase(base)
    if nroot == nbase:
        return ""
    if nroot.startswith(nbase + sep):
        return root[len(base):]
    return None


def former_roots(project) -> tuple[str, ...]:
    """Every root the bucket `project` resolves to has recorded before its
    current one (`adopt` and `remap` append to the identity record's
    `history`). Empty for a project that never moved, an unknown bucket or an
    unreadable record. Never raises; reads one small file."""
    folder = store.bucket_dir(project)
    record = read_identity(folder) if folder is not None else None
    return tuple(str(r) for r in (record or {}).get("history") or [] if r)


def was_located_under_former_root(project, location) -> bool:
    """Whether `location` is one of `project`'s former roots or lies below one
    (separator-bounded, `normcase`-compared like every root comparison). The
    importer's question: a transcript recorded on the old machine names a
    folder that no longer exists here, so only the bucket's own history can
    say it was this project's."""
    return any(_below(str(location), root) is not None
               for root in former_roots(project))


def _mapped(root: str, old: str, new: str) -> tuple[str | None, str | None]:
    """(new root, None) for `root` under `old` moved to `new`, or (None,
    reason). Separators in the tail follow `new`'s system; a tail that already
    holds the target's separator as a name character is refused."""
    tail = _below(root, old)
    if tail is None:
        return None, "unmatched"
    src, dst = _separator(old), _separator(new)
    if src != dst:
        if dst in tail:
            return None, "separator"
        tail = tail.replace(src, dst)
    return new + tail, None


def remap(from_root, to_root, *, apply: bool = False,
          force: bool = False) -> dict:
    """`daimon bucket remap`: after a folder or username change, rewrite the
    `root` of every bucket whose root lies under `from_root` to the same place
    under `to_root`. Dry run unless `apply`; a rerun is idempotent.

    Matching is separator-bounded (`/Users/a` never matches `/Users/abc`);
    `from_root` loses a trailing separator; the filesystem root, a bare drive
    and a UNC prefix are refused, and so is a `to_root` that is not absolute or
    lies inside `from_root` (rewriting a tree into itself would map twice).

    Per bucket, one action: `rewrite`; `missing` (the mapped folder is not
    there yet, the normal case for projects not yet cloned on a fresh machine:
    skipped, not an error); `collision` (another bucket already records the
    mapped folder, or an earlier bucket of this run maps to it: skipped and
    listed with that bucket so `merge` can fold them); `separator` (the tail
    holds the target's separator as a name character); `unreadable` (an
    identity record this code cannot read); `live-source` (the old root still
    exists, so it may be a clone that keeps its history: skipped unless
    `force`); `not-a-root` (the mapped folder is not the folder a session opens
    for it, `identity.session_root`, so binding it would bind nothing);
    `locked` and `raced` (applied
    per bucket under its own lock, fail-closed). A bucket with no root is out
    of reach: `doctor` lists those.

    Returns {"applied": bool, "actions": [...]}, each action naming the
    bucket, `from` and `to`."""
    old = _normalized_root(from_root, what="--from")
    new = _normalized_root(to_root, what="--to")
    if _below(new, old) is not None:
        raise RebindRefused(
            f"--to {new!r} is {old!r} or inside it: remapping a folder onto "
            f"itself would move its buckets twice")
    # `--from` through a symlink matches the real roots too (they are stored
    # real-pathed), never silently nothing.
    bases = [old]
    real_old = os.path.realpath(old)
    if real_old != old and not _below(real_old, old):
        bases.append(real_old)
    roots = store.bucket_roots()
    claimed: dict[str, str] = {}  # normcase target -> bucket, this run
    actions: list[dict] = []
    for name in sorted(roots):
        root = roots[name]
        target: str | None = None
        why: str | None = "unmatched"
        for base in bases:
            target, why = _mapped(root, base, new)
            if target is not None or why == "separator":
                break
        if target is None:
            if why == "separator":
                actions.append({"bucket": name, "action": "separator",
                                "from": root, "to": None})
            continue
        entry = {"bucket": name, "from": root, "to": target}
        if not force and os.path.isdir(root):
            actions.append(dict(entry, action="live-source"))
            continue
        if not os.path.isdir(target):
            actions.append(dict(entry, action="missing"))
            continue
        real = os.path.realpath(target)
        entry["to"] = real
        opened = identity.session_root(real)
        if opened != real:
            actions.append(dict(entry, to=opened, action="not-a-root"))
            continue
        other = next((n for n, r in sorted(roots.items())
                      if n != name and store.root_eq(r, real)), None)
        key = os.path.normcase(real)
        other = other or claimed.get(key)
        if other is not None:
            actions.append(dict(entry, action="collision", other=other))
            continue
        if _identity_blocks(config.checkpoint_dir() / name):
            actions.append(dict(entry, action="unreadable"))
            continue
        claimed[key] = name
        actions.append(dict(entry, action="rewrite"))
    if apply and any(a["action"] == "rewrite" for a in actions):
        with _store_lock():
            live = store.bucket_roots()  # fresh, and nobody rebinds while held
            for entry in actions:
                if entry["action"] != "rewrite":
                    continue
                other = next((n for n, r in sorted(live.items())
                              if n != entry["bucket"]
                              and store.root_eq(r, entry["to"])), None)
                if other is not None:
                    entry.update(action="collision", other=other)
                    continue
                entry["action"] = _write_rebind(
                    config.checkpoint_dir() / entry["bucket"],
                    entry["from"], entry["to"])
                if entry["action"] == "rewrite":
                    live[entry["bucket"]] = entry["to"]
    return {"applied": apply, "actions": actions}
