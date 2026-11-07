"""The one definition of what a bucket name is (#1294).

A LEAF module: standard library only, nothing from this package, so
`requests`, `refutations`, `config`, `store`, `view` and `buckets` can all
import it without a cycle (the cycle is why `refutations` once carried a
mirrored copy of the recipient regex).

Invariant: every place that accepts, lists or validates a bucket name asks
`parse_bucket`. Today the only valid form is a project slug, the output of
`store.project_slug`: every char that is not `\\w` or `-` becomes `-`, so it
can never contain any other character. That is a guarantee of the slug rule,
and it is what makes a name with any other character unrepresentable as a
project (pinned by tests/test_scope.py).
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

# A directory name is at most 255 bytes, so a longer name can never be a
# bucket that exists on disk.
_BUCKET_RE = re.compile(r"[\w-]{1,255}")


@dataclass(frozen=True)
class BucketId:
    """A validated bucket name. Build one with `parse_bucket`."""

    project: str

    @property
    def name(self) -> str:
        """The directory name under the checkpoint dir."""
        return self.project


def parse_bucket(name) -> BucketId | None:
    """The only parser: a `BucketId` for a valid bucket name, else None.
    Anything that is not a string is not a bucket name."""
    if not isinstance(name, str) or not _BUCKET_RE.fullmatch(name):
        return None
    return BucketId(project=name)


# Directories the store itself keeps beside its buckets. Named here, once, as
# typed sets and never matched by pattern: a new internal directory is added
# on purpose, and anything else that is not a bucket name is a stray (see
# `classify_dir`).
CHUNK_CACHE = ".chunk-cache"
CURRENT_INTERNAL_DIRS: frozenset[str] = frozenset({CHUNK_CACHE})
# RETIRED: names daimon wrote in a past version and no longer writes. `.partials`
# held chunk partials across heals (#316, 6e18433) and was replaced by
# `.chunk-cache` (#348, 0e5db26); stores of users who ran that version still
# carry the (empty) directory, so it is not a stray.
PARTIALS = ".partials"
RETIRED_INTERNAL_DIRS: frozenset[str] = frozenset({PARTIALS})
STORE_INTERNAL_DIRS: frozenset[str] = (
    CURRENT_INTERNAL_DIRS | RETIRED_INTERNAL_DIRS)


class DirKind(enum.Enum):
    BUCKET = "bucket"      # parse_bucket accepts the name
    INTERNAL = "internal"  # a known store-internal directory
    STRAY = "stray"        # neither: never read as a bucket, reported


def classify_dir(name: str) -> DirKind:
    """What a directory directly under the checkpoint dir is. A directory is a
    bucket if and only if `parse_bucket` accepts its name."""
    if name in STORE_INTERNAL_DIRS:
        return DirKind.INTERNAL
    return DirKind.BUCKET if parse_bucket(name) is not None else DirKind.STRAY
