"""Project identity: which bucket a project is, claimed once per operation.

Slice 4a of "project identity survives moves" (#1384). This module is the
plumbing that lets ONE owner decide every bucket name; it changes no answer.

`store.bucket_of` names a bucket. `claim` is the one place an operation binds a
location to that name: it resolves the project, asks the owner, creates the
bucket, and returns a `BucketRef` that every function below receives instead of
re-deriving the name. A later slice changes what the owner answers (a recorded
root instead of the path's slug) and nothing that holds a ref changes with it.

In 4a the owner answers exactly what `store.project_slug` always did, so a claim
is today's `ensure_bucket` done at the top of the operation instead of in the
middle of it.
"""

from __future__ import annotations

import os

from . import config


class BucketMismatch(Exception):
    """The directory in use is not the one a `BucketRef` was claimed for.

    Deliberately NOT an `OSError`: every writer treats `OSError` as "best
    effort, return False", and a disagreement about which bucket a write
    belongs to must not be absorbed there. It is logged and fails the
    operation. In 4a it means only that the owner now names another bucket for
    the ref's location than the one the ref carries; the recorded-root check
    that lets a lossy twin be refused arrives with resolution in 4b, so
    existing twins keep sharing a bucket exactly as before."""


class BucketClaimFailed(Exception):
    """A bucket could not be claimed for a location. Raised by the claim in 4b
    when every candidate name is taken; defined here so the surfacing wiring
    (the verb decorator, capture, the hooks) is complete before the claim can
    raise it."""


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

    def __new__(cls, location: str, slug: str, *, bound: bool = True):
        ref = super().__new__(cls, location)
        ref.slug = slug
        ref.bound = bound
        return ref


def bind(project_dir) -> BucketRef | None:
    """Resolve `project_dir` and name its bucket through the owner, creating
    nothing: the ref for an operation that must not make a bucket (a repair
    of a bucket that may not exist, a read). None when the project is
    unknown. A ref passed in is returned unchanged."""
    if isinstance(project_dir, BucketRef):
        return project_dir
    from . import store  # local: store imports this module

    resolved = store._resolved(project_dir)
    slug = store.bucket_of(resolved)
    if not slug:
        return None
    return BucketRef(str(resolved), slug,
                     bound=os.path.isabs(str(resolved)))


def claim(project_dir) -> BucketRef | None:
    """`bind`, then make the bucket: the ref for an operation that writes. None
    when the project is unknown, the same answer `store.ensure_bucket` gave.
    A ref passed in is returned unchanged, so claiming twice is the same as
    claiming once.

    Birth is today's: create the directory and record the root of the first
    writer. A failure creating the directory is NOT raised here; the writer's
    own `ensure_bucket(ref)` retries the same `mkdir` inside the `OSError`
    handler every writer already has, so a read-only store still ends as that
    writer's best-effort `False`. Under the kill switch nothing is created."""
    if isinstance(project_dir, BucketRef):
        return project_dir
    from . import store

    ref = bind(project_dir)
    if ref is None:
        return None
    if not config.is_disabled():
        try:
            store.ensure_bucket(ref)
        except OSError:
            return ref
        store.record_bucket_root(ref)
    return ref


def verify(ref: BucketRef) -> None:
    """Raise `BucketMismatch` unless the owner still names `ref.slug` for the
    ref's location. A slug-routed ref names no location and always verifies."""
    if not ref.bound:
        return
    from . import store

    now = store.bucket_of(str(ref))
    if now != ref.slug:
        raise BucketMismatch(
            f"bucket {ref.slug!r} was claimed for this location but the "
            f"owner now names {now!r}")
