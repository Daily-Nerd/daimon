"""The read-side surface other programs may import (#1132 PR 6a).

Re-exports only: nothing is defined here, every name is the object of its own
module, so a consumer gets the judged read (`view.lookup`, `view.match`), the
envelope reader, the proposals queue, the requests listings and the pure slug
transform without reaching into modules whose layout may move. Write verbs are
not part of this surface; callers that write keep importing the write modules.

`project_slug` is `store.project_slug`: the character transform that names a
bucket. Resolving WHICH directory a value means is `config.resolve_project_dir`
(#948), which the view's functions apply themselves.
"""

from .pending import queue
from .requests import inbox_listing, listing
from .store import project_slug, read_meta
from .view import lookup, match

__all__ = ("lookup", "match", "read_meta", "queue", "listing",
           "inbox_listing", "project_slug")
