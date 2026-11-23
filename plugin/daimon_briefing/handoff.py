"""The baton (#523): the one owner of its size cap and of the in-process write.

`daimon handoff` and `api.write_handoff` write the same ref-less `handoff`
event (scar 0025: it must never name an item). They differ in tier. The CLI
writes what it observed of the terminal (`cli/_observe.py`); `write` is for a
host that holds no person at the keyboard and always writes agent tier,
whatever terminal the process happens to have: it never asks the terminal.
"""

from __future__ import annotations

from . import config, store
from .surfaces import Writer

# #523: a baton is small on purpose: "do X first, beware Y", not a second
# checkpoint. Over-cap input is REFUSED, never silently truncated: it is an
# authored artifact and the author trims it.
MAX_CHARS = 2000


class HandoffTooLong(ValueError):
    """The baton is over `MAX_CHARS`. Nothing was written."""

    def __init__(self, length: int):
        self.length = length
        self.limit = MAX_CHARS
        super().__init__(
            f"baton exceeds {MAX_CHARS} chars ({length}): a handoff is "
            "\"do X first, beware Y\", not a second checkpoint; trim it")


class HandoffNotRecorded(RuntimeError):
    """The event was not appended (daimon disabled, project unknown or the
    ledger unwritable)."""


def write(project, text: str) -> None:
    """Leave `text` as the baton of `project` (a path), at the agent tier.

    Latest wins, as for the CLI. Raises `ValueError` for an empty baton,
    `HandoffTooLong` over `MAX_CHARS`, `config.ProjectWriteRefused` for a
    project that only resolved elsewhere because home is a git repository,
    and `HandoffNotRecorded` when nothing was appended."""
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to hand off: the baton text is empty")
    if len(text) > MAX_CHARS:
        raise HandoffTooLong(len(text))
    target = config.resolve_project_dir_for_write(str(project),
                                                  allow_slug=False)
    if not store.append_event(
            "", "active", note=text, kind="handoff", project_dir=target,
            writer=Writer.HUMAN, source=store.observed_event_source(False)):
        raise HandoffNotRecorded(
            "handoff not recorded (daimon disabled or project unknown)")
