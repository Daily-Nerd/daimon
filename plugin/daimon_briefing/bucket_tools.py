"""The repair tools that read and annotate buckets: `bucket stamp` and
`bucket doctor` (slice 3b of "project identity survives moves").

A bucket's directory name is its permanent id; its `root` file is the one
place that binds it to a location. Both tools are human surfaces. They stat
recorded roots and walk directories, so they never run on a hook path, and they
make no decision of their own: every finding is evidence for a person, and
nothing here changes which bucket a project resolves to.

`identity` is the evidence record: where the bucket's project was when it was
stamped (`root`), the anchors that survive a move (origin URL with credentials
stripped, root commit, folder name) and the roots it has had (`history`, which
`bucket adopt` will append to). Read by `doctor` only.
"""

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import buckets, config, hostgit, store

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
                   history=()) -> None:
    """Write the identity record atomically. Raises OSError. Written AFTER the
    bucket's `root`, so it is not atomic with it; nothing decides on it, and
    `doctor` reports a transient disagreement."""
    record = {
        "version": IDENTITY_VERSION,
        "recorded": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "root": root,
        "anchors": dict(anchors),
        "history": list(history),
    }
    store._atomic_write(Path(bucket) / store._IDENTITY_NAME,
                        json.dumps(record, ensure_ascii=False, indent=2))


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
      root (anchors only for a root that exists).
    * An UNSTAMPED bucket (no root, or an empty one) gains its root only from
      an exact match: a repository under a `--scan` directory whose seed slug
      (`store.location_slug` of its realpath) IS the bucket name. Two matches
      refuse and write nothing; none leaves the bucket as it is.

    The recorded root of a stamped bucket is never rewritten, and nothing
    outside the bucket is touched, so the signed flat checkpoints and their
    receipts are byte for byte what they were.

    Returns {"applied": bool, "actions": [{"bucket", "action", ...}]} with
    action one of: identity, root, kept, unmatched, ambiguous, raced."""
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
                continue
            actions.append({"bucket": name, "action": "identity",
                            "root": root})
            if apply:
                write_identity(bucket, root, anchors_for(root))
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
            write_identity(bucket, matches[0], anchors_for(matches[0]))
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
    with the newest session; it is withheld ("worktree") for worktree-looking
    roots, because their live location would birth the folded bucket again
    until the resolver learns worktrees. No rebinding verb exists yet, so a
    candidate carries no command."""
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
    for group in found["shared_root"]:
        if any(name in found["worktree"] for name in group):
            withheld = "worktree"
            continue
        main = max(group, key=lambda n: (_newest(config.checkpoint_dir() / n),
                                         n))
        suggestions.extend({"from": n, "into": main}
                           for n in group if n != main)

    candidates: list[dict] = []
    if path is not None:
        here = anchors_for(os.path.realpath(os.fspath(path)))
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
            "candidates": candidates, "candidates_note": "evidence, not proof",
            "suggestions": suggestions, "suggestions_withheld": withheld}
