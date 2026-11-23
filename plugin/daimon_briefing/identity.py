"""Project identity: which bucket a project is, decided by what the buckets
record and claimed once per operation (#1384).

A bucket's directory name is its permanent id; its `root` file is the one
binding between that bucket and a location. `resolve` answers which bucket an
absolute location belongs to from those records, `store.bucket_of` asks it for
every site that names a bucket, and `claim` is the one place an operation binds
a location to its bucket: it resolves, and when nothing is bound it BIRTHS the
bucket (built whole in `.birth` with its root inside, renamed into place), then
returns a `BucketRef` that every function below receives instead of re-deriving
the name. Slice 4a built the plumbing with the answer unchanged; 4b makes the
answer the recorded root, with its repair verbs (`adopt`, `remap`) and the
notice that a location is unbound.

Strict for STAMPED buckets only: a stamped bucket is never returned for a
location its root does not name. An unstamped bucket keeps today's behavior
until its first write stamps it.
"""

from __future__ import annotations

import contextlib
import contextvars
import errno
import hashlib
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import NamedTuple

from . import config, hostgit, scope


class BucketMismatch(Exception):
    """The bucket a `BucketRef` was claimed for now records ANOTHER location.

    Deliberately NOT an `OSError`: every writer treats `OSError` as "best
    effort, return False", and a disagreement about which bucket a write
    belongs to must not be absorbed there. It is logged and fails the
    operation. It means `adopt` or `remap` rebound the bucket while the
    operation held its ref (or a stamped bucket was handed to a location its
    root does not name); an unstamped bucket never raises it."""


class BucketClaimFailed(Exception):
    """A bucket could not be claimed for a location: every name it could be
    born under is held by another location, or the birth rename keeps failing
    for a reason other than "that name is taken". Never an `OSError` either,
    for the reason `BucketMismatch` is not; the verb decorator, capture and
    the hooks let it through."""


# What a best-effort handler must let through. A handler that logs and carries
# on (`except Exception`) names these first and re-raises, so a write that
# landed in the wrong bucket, or never got one, fails the operation loudly.
LOUD = (BucketMismatch, BucketClaimFailed)


class BucketRef(str):
    """A claimed bucket: a `str` whose VALUE is the project LOCATION, carrying
    the bucket name as `.slug`.

    A string on purpose: `Path(ref).name`, `config.git_branch(ref)`,
    `hostgit.declared_slug(ref)`, `harvest.run(project_root=ref)`, hashing and
    equality with the location text all keep working, so a ref crosses every
    signature that took a project directory. `bound` is False for a ref routed
    by bucket slug (`--slug`): its value is the slug text, it names no
    directory, and no root is ever recorded for it.

    Constructed only by `claim`; a census test pins that."""

    slug: str
    bound: bool
    home: str

    def __new__(cls, location: str, slug: str, *, bound: bool = True,
                home: str | None = None):
        ref = super().__new__(cls, location)
        ref.slug = slug
        ref.bound = bound
        # The location the bucket's `root` records: the same as the value,
        # except for a linked worktree, which uses its main repository's.
        ref.home = home or location
        return ref


class Resolution(NamedTuple):
    """What the owner answers for one location: the bucket name (None when the
    location is unknown, or every name it could be born under is taken) and
    whether the location is BOUND to it (a recorded root, or a legacy
    directory nothing else claims) or would be born under it."""

    slug: str | None
    bound: bool


# ---- the shared index: one scan per operation, nothing kept after it ------------


class _Holder:
    index: dict[str, list[str]] | None = None


_HOLDER: contextvars.ContextVar[_Holder | None] = contextvars.ContextVar(
    "daimon_identity_index", default=None)


@contextlib.contextmanager
def operation():
    """One operation (a CLI verb, a host read, a claim): every resolution
    inside it shares ONE scan of the recorded roots, built on first need. The
    index lives only as long as the `with`: nothing is kept between operations,
    so `adopt`, `remap` and `merge` take effect at the next one in a
    long-lived host, and a nested operation joins the outer one. A CLI verb,
    a host read and every MCP tool call open one; a blocking server verb
    opens none itself, so each request does."""
    if _HOLDER.get() is not None:
        yield
        return
    token = _HOLDER.set(_Holder())
    try:
        yield
    finally:
        _HOLDER.reset(token)


def invalidate() -> None:
    """Forget the open operation's index. Whoever writes a root (a claim, a
    verb) calls this, so a bucket bound a moment ago is found by the next
    resolution in the same operation. A miss is never stored: the index only
    ever holds what a scan found."""
    holder = _HOLDER.get()
    if holder is not None:
        holder.index = None


def read_root(bucket) -> str | None:
    """The non-empty recorded root of a bucket directory, or None when it is
    absent, empty or unreadable (all three mean unstamped)."""
    try:
        text = (Path(bucket) / "root").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return text or None


def _store_root() -> Path | None:
    """The checkpoint directory, or None when configuration cannot say (a
    corrupt env file, an unresolvable `~`). A resolution is a question about
    the store, not about its configuration: when the store cannot be located
    the answer is the plain seed, unbound, and the caller's own join of
    `config.checkpoint_dir()` raises the fault where that caller already
    handles it. Resolution itself never raises (hooks call it)."""
    try:
        return config.checkpoint_dir()
    except Exception:  # noqa: BLE001 - a config fault is not a resolution answer, see above
        return None


def _scan() -> dict[str, list[str]]:
    """{normcase(root): [bucket names]} over every bucket directory. One pass,
    `scandir` entries (no extra stat per bucket) and one small read per bucket.
    Never raises; a store that cannot be listed is an empty index."""
    out: dict[str, list[str]] = {}
    root = _store_root()
    if root is None:
        return out
    try:
        entries = os.scandir(root)
    except OSError:
        return out
    with entries:
        for entry in sorted(entries, key=lambda e: e.name):
            try:
                if not entry.is_dir():
                    continue
            except OSError:
                continue
            if scope.classify_dir(entry.name) is not scope.DirKind.BUCKET:
                continue
            recorded = read_root(entry.path)
            if recorded:
                out.setdefault(os.path.normcase(recorded), []).append(
                    entry.name)
    return out


def _index() -> dict[str, list[str]]:
    holder = _HOLDER.get()
    if holder is None:
        return _scan()
    if holder.index is None:
        holder.index = _scan()
    return holder.index


def _pure() -> bool:
    """Tenant scope and SEALED never look at any bucket but the one their own
    location names: another bucket's record is not theirs to read (scar 0071),
    and the project directories of a SEALED host are writable by a less
    trusted party. An unreadable posture setting is treated the same way: it
    is an error elsewhere, never a reason to widen what resolution reads."""
    try:
        return (config.tenant_scoped()
                or hostgit.posture() is hostgit.Posture.SEALED)
    except Exception:  # noqa: BLE001 - an undecidable scope is the narrow one
        return True


def _alias_sources() -> frozenset[str]:
    from . import buckets  # local: buckets imports store, which imports this
    return frozenset(buckets.alias_map())


def _birth_names(location: str, seed: str) -> list[str]:
    """The names a location may be born under, in order: its seed, then the
    seed with 6 and with 12 hex digits of the sha256 of its normalized real
    path. A suffixed name is kept inside the 255 characters a bucket name may
    have."""
    try:
        real = os.path.realpath(location)
    except ValueError:  # an embedded NUL: resolution never raises
        real = location
    digest = hashlib.sha256(
        os.path.normcase(real).encode("utf-8", "surrogatepass")).hexdigest()
    return [seed] + [f"{seed[:255 - 1 - n]}-{digest[:n]}" for n in (6, 12)]


def _free_name(root: Path, location: str, seed: str) -> str | None:
    """The first birth name nothing holds: no entry of that name on disk (a
    directory, a file, a dangling link) and not the source of an alias row (a
    merged-away bucket's name must never come back, or recall and requests
    fold the new project into the old one). None when all three are taken."""
    aliases: frozenset[str] | None = None
    for name in _birth_names(location, seed):
        if os.path.lexists(root / name):
            continue
        if aliases is None:
            aliases = _alias_sources()
        if name in aliases:
            continue
        return name
    return None


def home_of(location: str) -> str:
    """The location a bucket is bound to for `location`: its main repository
    when it is a linked worktree of one (step 1), else itself. The physical
    folder stays the session's location (worldcheck and branch stamps read the
    real working tree); only the BINDING follows the main."""
    if not os.path.isabs(location):
        return location  # a slug or a relative name is no folder to ask about
    return config.worktree_main(location) or location


def resolve(location) -> Resolution:
    """The owner's answer for an absolute, already resolved `location`.

    0. SEALED and declared: the slug the host declared.
    2. The seed's own bucket records this location: that bucket.
    3. Exactly one bucket anywhere records this location (the shared index;
       skipped under tenant scope and SEALED, and two hits count as none:
       `bucket doctor` names them).
    4. The seed's directory exists and carries no root: a legacy bucket,
       today's behavior until its first write stamps it.
    5. Unbound: the first free birth name, which is what a read answers (it
       finds nothing) and what a claim creates.

    1. (before 2, AMBIENT only) A linked worktree continues as its MAIN
       repository (`home`), when that repository's own worktree list names
       this exact path; see `hostgit.linked_main`.

    A stamped bucket is never returned for a location its root does not name.
    No git for an ordinary folder (one lstat), no stat of any recorded root,
    no refusal."""
    from . import store  # local: store imports this module

    text = str(location) if location else ""
    seed = store.location_slug(text)
    if not seed:
        return Resolution(None, False)
    declared = hostgit.declared_slug(text)
    if declared:
        return Resolution(declared, True)
    root = _store_root()
    if root is None:
        return Resolution(seed, False)
    text = home_of(text)
    seed = store.location_slug(text)
    assert seed  # a non-empty absolute location always has a seed
    own = root / seed
    recorded = read_root(own)
    if recorded and store.root_eq(recorded, text):
        return Resolution(seed, True)
    if not _pure():
        hits = _index().get(os.path.normcase(text), [])
        if len(hits) == 1:
            return Resolution(hits[0], True)
    if recorded is None and os.path.isdir(own):
        return Resolution(seed, True)
    return Resolution(_free_name(root, text, seed), False)


# The one line shown when a location is unbound. Fixed text: no bucket name, no
# project name, nothing derived from the repository or the store, so what an
# agent reads cannot carry another project's facts. It sends the question to
# the USER: the survey it names prints other projects' names, which belongs on
# a person's terminal, never in a transcript.
UNBOUND_NOTICE = (
    "No memory is bound to this folder yet. If this project moved from "
    "another folder or machine, ask the user to run "
    "`daimon bucket doctor --path .` themselves.")


def is_unbound(project) -> bool:
    """Whether `project` is an absolute location no bucket is bound to: exactly
    the resolver's step 5, the one trigger of the notice. False for anything
    that is not a location (a slug, nothing), and always False under tenant
    scope and SEALED, where resolution never looks past the location's own
    bucket and so cannot tell a moved project from a new one. A read: nothing
    is created."""
    text = str(project) if project else ""
    if not text or not os.path.isabs(text) or _pure():
        return False
    if "\0" in text:  # a NUL names no folder
        return False
    answer = resolve(text)
    return answer.slug is not None and not answer.bound


# The second line, for a linked worktree whose OWN folder already has a bucket
# from before it shared its main repository's. Fixed text like the first: no
# bucket name, nothing from the store, and the survey it names is the user's.
SPLIT_NOTICE = (
    "This worktree has an earlier memory of its own, separate from its main "
    "repository's. To fold it in, ask the user to run "
    "`daimon bucket doctor` themselves.")


def has_split_bucket(project) -> bool:
    """Whether `project` is a linked worktree bound to its main repository
    while its own folder still records a bucket of its own (the memory it had
    before step 1 existed). False for anything that is not such a worktree,
    and under tenant scope and SEALED, where nothing is derived. A read."""
    text = str(project) if project else ""
    if not text or not os.path.isabs(text) or _pure() or "\0" in text:
        return False
    if home_of(text) == text:
        return False
    root = _store_root()
    if root is None:
        return False
    from . import store

    own = store.location_slug(text)
    assert own  # a non-empty absolute location always has a seed
    recorded = read_root(root / own)
    return bool(recorded and store.root_eq(recorded, text)
                and resolve(text).slug != own)


def session_root(path) -> str:
    """The folder a SESSION opens for `path`: absolute, symlinks collapsed,
    normalized to the git toplevel (`config.resolve_project_dir`), real-pathed,
    and for a linked worktree the main repository its bucket is bound to.

    The one derivation of "which folder is this project". `adopt`, `remap` and
    `doctor --path` bind, check and print THIS, never a typed spelling: a
    root recorded as `<repo>/plugin` would be a folder no session ever opens
    (sessions resolve to `<repo>`), so the project would stay unbound."""
    return home_of(os.path.realpath(
        config.resolve_project_dir(os.fspath(path), allow_slug=False)))


def bucket_for(location) -> str | None:
    """The bucket name `store.bucket_of` answers for an absolute location."""
    return resolve(location).slug


def worktree_binds_to(location, slug: str) -> bool:
    """Whether `location` is a live linked worktree whose sessions now resolve
    to the bucket `slug`: the one case where a folder that exists on disk
    cannot birth the bucket it once recorded, because it answers its main
    repository's. False for every other location."""
    text = str(location) if location else ""
    if not text or not os.path.isabs(text) or "\0" in text:
        return False
    if _pure() or home_of(text) == text:
        return False
    answer = resolve(text)
    return answer.bound and answer.slug == slug


def bind(project_dir) -> BucketRef | None:
    """Resolve `project_dir` and name its bucket through the owner, creating
    nothing: the ref for an operation that must not make a bucket (a repair
    of a bucket that may not exist, a read). None when the project is
    unknown, or when every name it could be born under is taken. A ref passed
    in is returned unchanged."""
    if isinstance(project_dir, BucketRef):
        return project_dir
    from . import store  # local: store imports this module

    resolved = store._resolved(project_dir)
    slug = store.bucket_of(resolved)
    if not slug:
        return None
    text = str(resolved)
    return BucketRef(text, slug, bound=os.path.isabs(text),
                     home=home_of(text) if os.path.isabs(text) else text)


# Errnos that say "that name is taken" after a birth rename. Anything else is
# a failing filesystem (an open handle on NTFS, a permissions error), retried
# and then raised, never taken for "someone else has it".
_TAKEN = frozenset({errno.ENOTEMPTY, errno.EEXIST})
_RENAME_ATTEMPTS = 3


def _stage(root: Path, location: str) -> Path:
    """Build the bucket whole in `.birth/<pid>-<rand>/`, its `root` inside and
    synced, so the rename that follows publishes a bucket that already says
    whom it belongs to. Raises OSError."""
    parent = root / scope.BIRTH
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / f"{os.getpid()}-{secrets.token_hex(6)}"
    stage.mkdir()
    try:
        fd = os.open(stage / "root", os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                     0o644)
        try:
            os.write(fd, f"{location}\n".encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return stage


def _publish(stage: Path, target: Path) -> bool:
    """Rename `stage` to `target`. True when this call made the bucket, False
    when the name is taken. `target` is checked first because POSIX `rename`
    onto an EMPTY directory replaces it; after the call only the exists errnos
    mean taken. Any other failure is retried, then raised as
    `BucketClaimFailed`. The stage never outlives the call."""
    try:
        if os.path.lexists(target):
            return False
        for attempt in range(1, _RENAME_ATTEMPTS + 1):
            try:
                os.rename(stage, target)
                return True
            except FileExistsError:
                return False
            except OSError as exc:
                if exc.errno in _TAKEN:
                    return False
                if attempt == _RENAME_ATTEMPTS:
                    raise BucketClaimFailed(
                        f"could not create the bucket {target.name!r}: {exc}"
                    ) from exc
                time.sleep(0.05 * attempt)
        return False  # pragma: no cover - the loop always returns or raises
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _claim_once(text: str, res: Resolution) -> BucketRef | None:
    """One pass of the claim for `res`. The ref when the location is now bound
    to a bucket; None when the pass changed what a resolution answers (a name
    was taken, a stamp lost) and the caller must resolve again. Raises
    OSError when the store cannot be written (the caller defers it)."""
    from . import store

    root = config.checkpoint_dir()
    slug = res.slug
    assert slug is not None
    home = home_of(text)  # what the bucket records (a worktree's main)
    if res.bound:
        bucket = root / slug
        if hostgit.declared_slug(text):
            # The host named this bucket itself: first writer's root is
            # recorded, never enforced.
            bucket.mkdir(parents=True, exist_ok=True)
            store.stamp_root(bucket, home)
            invalidate()
            return BucketRef(text, slug, home=home)
        if read_root(bucket) is None:
            # An unstamped legacy directory: the first location to write
            # claims it, exclusively.
            bucket.mkdir(parents=True, exist_ok=True)
            store.stamp_root(bucket, home)
            invalidate()
            if not store.root_eq(read_root(bucket), home):
                return None  # another location stamped it first
        return BucketRef(text, slug, home=home)
    stage = _stage(root, home)
    if _publish(stage, root / slug):
        invalidate()
        return BucketRef(text, slug, home=home)
    invalidate()
    # Taken. If the winner was this very location (two processes birthing
    # together) it is ours; otherwise resolve again for the next name.
    if store.root_eq(read_root(root / slug), home):
        return BucketRef(text, slug, home=home)
    return None


def claim(project_dir) -> BucketRef | None:
    """`bind`, then make the bucket: the ref for an operation that writes.
    None when the project is unknown. A ref passed in is returned unchanged,
    so claiming twice is the same as claiming once.

    This is the ONE place a location is bound to a bucket and, when nothing is
    bound to it, the one place a bucket is BORN: resolve (one shared index for
    the whole claim), then either stamp the legacy directory that is there or
    build a new bucket in `.birth` and rename it into place (see `_publish`).
    A name another location holds is walked past (seed, `-6hex`, `-12hex`);
    when all three are held the claim raises `BucketClaimFailed`.

    A failure to WRITE the store is not raised here: the writer's own
    `ensure_bucket(ref)` retries inside the `OSError` handler every writer
    already has, so a read-only store still ends as that writer's best-effort
    `False`. Under the kill switch nothing is created."""
    if isinstance(project_dir, BucketRef):
        return project_dir
    from . import store

    resolved = store._resolved(project_dir)
    if not resolved:
        return None
    text = str(resolved)
    if not os.path.isabs(text):
        slug = store.bucket_of(text)
        if not slug:
            return None
        ref = BucketRef(text, slug, bound=False)
        if not config.is_disabled():
            try:
                (config.checkpoint_dir() / slug).mkdir(parents=True,
                                                       exist_ok=True)
            except OSError:
                pass
        return ref
    home = home_of(text)
    with operation():
        last: Resolution | None = None
        for _ in range(8):
            res = resolve(text)
            if res.slug is None:
                raise BucketClaimFailed(
                    "every name this location could be born under is taken "
                    "by another location; `daimon bucket doctor` lists the "
                    "buckets")
            if config.is_disabled():
                return BucketRef(text, res.slug, home=home)
            last = res
            try:
                claimed = _claim_once(text, res)
            except OSError:
                return BucketRef(text, res.slug, home=home)
            if claimed is not None:
                return claimed
        raise BucketClaimFailed(
            f"could not settle a bucket for this location (last tried "
            f"{last.slug if last else None!r})")


def verify(ref: BucketRef) -> None:
    """Raise `BucketMismatch` when the bucket `ref` carries records a
    DIFFERENT location than the ref's: `adopt` or `remap` rebound it while the
    operation was running, so a write would land in another project's bucket.
    An unstamped bucket verifies (nothing says otherwise), and so does a
    slug-routed ref (it names no location) and a host-declared root (the host
    named the bucket). The message names the bucket, never the other
    location."""
    if not ref.bound or hostgit.declared_slug(str(ref)):
        return
    from . import store

    recorded = read_root(config.checkpoint_dir() / ref.slug)
    if recorded and not store.root_eq(recorded, ref.home):
        raise BucketMismatch(
            f"bucket {ref.slug!r} was claimed for this location but now "
            f"records another one (rebound while this operation ran)")
