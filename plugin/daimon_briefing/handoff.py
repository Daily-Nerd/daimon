"""The baton (#523): the one owner of its size caps, its text check and the
in-process write.

`daimon handoff` and `api.write_handoff` write the same ref-less `handoff`
event (scar 0025: it must never name an item). They differ in tier. The CLI
writes what it observed of the terminal (`cli/_observe.py`); `write` is for a
host that holds no person at the keyboard and always writes agent tier,
whatever terminal the process happens to have: it never asks the terminal.
"""

from __future__ import annotations

from . import config, display, store
from .surfaces import Writer

# #523: a baton is small on purpose: "do X first, beware Y", not a second
# checkpoint. Over-cap input is REFUSED, never silently truncated: it is an
# authored artifact and the author trims it.
MAX_CHARS = 2000
# The same cap in UTF-8 bytes, which is what the brief's budget is measured
# in: 2000 four-byte characters would otherwise be 8 KB of a 9.5 KB budget.
# A third of the default budget is the most a baton may take.
MAX_BYTES = 3000


class HandoffTooLong(ValueError):
    """The baton is over `MAX_CHARS` or `MAX_BYTES`. Nothing was written."""

    def __init__(self, length: int, size: int):
        self.length = length
        self.size = size
        self.limit = MAX_CHARS
        self.byte_limit = MAX_BYTES
        super().__init__(
            f"baton exceeds {MAX_CHARS} chars or {MAX_BYTES} bytes "
            f"({length} chars, {size} bytes): a handoff is \"do X first, "
            "beware Y\", not a second checkpoint; trim it")


class HandoffUnprintable(ValueError):
    """The baton carries a character a terminal acts on (a C0 control other
    than newline, DEL, a C1 control, U+2028 or U+2029). Nothing was written."""


class HandoffNotRecorded(RuntimeError):
    """The event was not appended (daimon disabled, project unknown or the
    ledger unwritable)."""


def validate(text) -> str:
    """The baton text as it will be stored: stripped. Raises `ValueError` for
    an empty one, `HandoffUnprintable` for one a terminal would act on and
    `HandoffTooLong` over either cap. The one check the CLI and the
    in-process writer share."""
    text = (text or "").strip()
    if not text:
        raise ValueError("nothing to hand off: the baton text is empty")
    if display.has_unprintable(text):
        raise HandoffUnprintable(
            "baton carries a control character (a terminal escape, a "
            "carriage return, DEL, a C1 control or U+2028/U+2029); only "
            "newline and tab are allowed")
    size = len(text.encode("utf-8"))
    if len(text) > MAX_CHARS or size > MAX_BYTES:
        raise HandoffTooLong(len(text), size)
    return text


def write(project, text: str) -> None:
    """Leave `text` as the baton of `project` (a path), at the agent tier.

    Latest wins, as for the CLI. Raises what `validate` raises,
    `config.ProjectWriteRefused` for a project that only resolved elsewhere
    because home is a git repository, and `HandoffNotRecorded` when nothing
    was appended."""
    text = validate(text)
    target = config.resolve_project_dir_for_write(str(project),
                                                  allow_slug=False)
    if not store.append_event(
            "", "active", note=text, kind="handoff", project_dir=target,
            writer=Writer.HUMAN, source=store.observed_event_source(False)):
        raise HandoffNotRecorded(
            "handoff not recorded (daimon disabled or project unknown)")
