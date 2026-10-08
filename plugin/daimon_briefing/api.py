"""The read-side surface other programs may import (#1132 PR 6a, PR 7b).

Re-exports, plus the one thing daimon owns about its own output: how to read a
briefing back. A consumer gets the judged read (`view.lookup`, `view.match`),
the envelope reader, the cheap receipt check, the proposals queue, the requests listings and the pure
slug transform without reaching into modules whose layout may move, and
`parse_briefing` for the text `daimon brief` prints. Write verbs are not part
of this surface; callers that write keep importing the write modules.

`project_slug` is `store.project_slug`: the character transform that names a
bucket. Resolving WHICH directory a value means is `config.resolve_project_dir`
(#948), which the view's functions apply themselves.

`parse_briefing` is stdlib only and is built from `marks`, the constants the
renderers write with, so the format and its reader cannot drift apart.
"""

from dataclasses import dataclass

from .display import withheld_json, withheld_marker
from .marks import ITEM_MARKS, RULING_MARK, WARNING_MARKERS
from .pending import queue
from .requests import inbox, inbox_listing, join, listing
from .store import project_slug, read_meta
from .surfaces import Writer
from .view import ledger_health, lookup, match, receipt_state

__all__ = ("lookup", "match", "read_meta", "withheld_marker", "withheld_json", "queue", "listing",
           "inbox_listing", "inbox", "join", "ledger_health", "project_slug",
           "receipt_state", "Writer", "Briefing", "parse_briefing")


@dataclass(frozen=True)
class Briefing:
    """A briefing split by line kind. `header` is the first line that is not
    a warning, `standing_rulings` start with the ruling mark, `items` with an
    item mark, and `warnings` carry a warning marker anywhere. Any other line
    is not kept. `raw` is the text as given."""

    header: str = ""
    standing_rulings: tuple = ()
    items: tuple = ()
    warnings: tuple = ()
    raw: str = ""


def parse_briefing(text: str) -> Briefing:
    """Split `daimon brief` text into header, standing rulings, items and
    warnings. Tolerant: a line of none of those kinds is dropped, never an
    error. The warning markers are checked first for every line, so a note
    above the greeting never becomes the header."""
    header = ""
    rulings: list = []
    items: list = []
    warnings: list = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if any(marker in stripped for marker in WARNING_MARKERS):
            warnings.append(stripped)
            continue
        if not header:
            header = stripped
            continue
        if stripped.startswith(RULING_MARK):
            rulings.append(stripped)
        elif stripped.startswith(ITEM_MARKS):
            items.append(stripped)
    return Briefing(header, tuple(rulings), tuple(items), tuple(warnings),
                    text)
