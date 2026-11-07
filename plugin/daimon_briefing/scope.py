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
