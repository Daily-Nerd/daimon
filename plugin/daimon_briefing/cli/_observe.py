"""The one place the CLI looks at the terminal to choose a channel (#1301).

Which tier a write carries is decided by the path daimon OBSERVED, never by a
claim the caller makes about itself (see `channels.py`). Observing means
asking whether a terminal is attached to stdin, and that question is asked
here and nowhere else: every CLI verb that needs a channel, a source or an
"is a person present" answer comes through this module. Library code never
reads stdin to decide a tier; it is handed the observation by its caller.

`tests/test_authority_census.py` fails the build when another module calls
`isatty()` to decide a tier.
"""

from __future__ import annotations

import sys

from .. import store


def attended() -> bool:
    """Whether a terminal is attached to stdin: a person is at the keyboard."""
    return sys.stdin.isatty()


def channel(*, agent: bool = False) -> str | None:
    """The write channel this invocation arrived through, or None when it is
    the human path without a terminal.

    `agent` is the caller's `--by agent`: a self-declaration of the NARROWER
    authority, so it is honoured. The human path is the absence of the flag
    and has to show an interactive terminal. The CLI can mint nothing
    stronger than `cli-tty`: `ui` and `signed` are in-process-only, because a
    channel an agent can reach by shelling out is the deleted `--by human`
    renamed. Each verb word-smiths its own refusal for the None."""
    if agent:
        return "cli-agent"
    return "cli-tty" if attended() else None


def event_source() -> str:
    """The `source` of an events row written from this invocation. A missing
    or unreadable stdin reads as no terminal: doubt resolves away from the
    human tier."""
    try:
        tty = sys.stdin is not None and sys.stdin.isatty()
    except (OSError, ValueError):
        tty = False
    return store.observed_event_source(tty)
