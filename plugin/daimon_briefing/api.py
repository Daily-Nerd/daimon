"""The read-side surface other programs may import (#1132 PR 6a, PR 7b).

Re-exports, plus the one thing daimon owns about its own output: how to read a
briefing back. A consumer gets the judged read (`view.lookup`, `view.match`), the item
receipt (`why`, rendered by `why_lines`), the envelope reader, the cheap
receipt check, `masked` (a ledger's prose judged for a printer), the proposals queue, the requests listings, the transcript exclusion
contract (`EXCLUDED_ROWS`, `admitted_messages`, #1178), serialize liveness
(`serializes`, #1179) and the pure slug transform without reaching into
modules whose layout may move, and
`parse_briefing` for the text `daimon brief` prints. Write verbs are not part
of this surface; callers that write keep importing the write modules.

`project_slug` is `store.project_slug`: the character transform that names a
bucket. Resolving WHICH directory a value means is `config.resolve_project_dir`
(#948), which the view's functions apply themselves.

Serialize liveness (`serializes`, #1179). One row per session, keyed by ONE
canonical session key whatever the host calls it:

    Host         id the host passes              canonical key
    Claude Code  session uuid                    uuid
    Codex        bare session id or rollout stem bare id
    Kimi         the `--session` id              that id
    Windsurf     trajectory id                   trajectory id
    Hermes       `session_id` of on_session_end  `session_id`

A drain waits until no row is in `LIVE_STATES`. An id that reads `unknown`
(`not-found`) before the host's own deadline means "not started yet", never
"gone": a host that spawns `daimon serialize` itself has no spawn line, and the
child stamps only after its preflight. State and reason codes are the contract;
the notes are prose. A successful Hermes capture writes no log line, so pass
`since` to read it as `succeeded`.

Heal (`heal_plan`, `heal_all`, #1179). `heal_plan` is a pure read: the sessions
a heal would repair and the ones it cannot, each skip with a stable `reason`
(`retry-exhausted`, `unrecoverable`, `hung`, `admission-refused`, `repairing`,
`project-unknown`),
the same codes `daimon status` reports. It never writes and never reaps. The
scope is the project of `project_dir`; `sessions=[...]` narrows it (any name a
session goes by works, as for `serializes`); `everywhere=True` is machine-wide.
A call that names none of the three plans nothing and says so.

`heal_all` plans, then starts `daimon heal --all` DETACHED with the host's own
interpreter and returns at once: one serialize at a time, under one machine-wide
lock, each session retried once (`force=True` ignores that). Its `status`:
`started`, `completed` (`wait=True`, with one `SerializeRow` per target in
`outcomes`), `nothing-to-heal`, `already-running` (another heal holds the lock)
or `launch-failed`; it never raises. Watch a started run with
`serializes(sessions=[the target ids])`, and read `unknown` there as "not
started yet" until your own deadline, never as "gone". The child runs with the
configuration the caller uses. Outside a sandbox that is a copy of the
caller's environment (every DAIMON_* name included) with the effective
checkpoint and log directories on top. Inside a sandbox its DAIMON_* names come
from the sandbox settings alone: the process env's DAIMON_* names do not reach
it and it reads no env file.

`presumed_dead_before` (epoch) is a host assertion that no serialize of
`project_dir` started before that time can still be alive, so a killed run
stops reading as live. Pass it only where a restart kills the children (a
container or pod). NEVER on a laptop: there the detached children outlive the
host, and the assertion would heal a serialize that is still running. Without
it, a killed run frees itself after `hung_after_seconds`. It is ignored with a
note under `everywhere=True`.

`api` is read-side with one named exception (design D23): `heal_all` is a
launcher. It starts daimon's own serialize repair and writes no cognitive
content; nothing else here starts a process.

Render (`render_briefing`, `briefing_text`, #1180). The MACHINE briefing, the
same bytes the MCP `daimon_brief` tool returns: always the deterministic render,
never the LLM one, never changed by a terminal or `DAIMON_PLAIN`. It returns
`RenderedBriefing(text, state, notes)`; `state` is `briefing`, `closed`,
`no-checkpoint`, `empty` or `unavailable`, and `notes` are the ledger-health
lines the text already carries. It never raises: a failure is `unavailable`
with a note naming the exception type and an empty text. `project_dir=None`
means DAIMON_PROJECT_DIR, else the working directory (a read of one project is
harmless; `heal_all` plans nothing without a scope, because a machine-wide
repair is not). `loops_pointer` defaults off: the MCP tool turns it on when
the project is the caller's own, so pass `True` to match it byte for byte.

The bytes are stable only under these conditions, which `testing.sandbox`
enforces for a host test:
  - pass `project_dir` (None reads the environment and the cwd);
  - pass `now` (the stale marks and ages read the clock);
  - the project is not inside a git working tree (the resolver walks up to the
    repo top) and not at or below the home directory (ruling layers and `~`
    paths would change the text);
  - the settings (budgets, caps, tenant flags, capture host, the check dir)
    come from the sandbox mapping, not the process environment.

Round trip: for `briefing` and `closed`, `parse_briefing(text).header` is
`marks.GREETING`, every `standing_rulings` entry starts with `marks.RULING_MARK`,
every `items` entry starts with a mark in `marks.ITEM_MARKS`, and every note is
in `warnings`. For `no-checkpoint` and `empty` the header is the orientation
line and the notes are still `warnings`. The withheld trailer is dropped by the
parser. `BRIEFING_FORMAT` is an integer that changes only when what
`parse_briefing` extracts changes; it is never printed into a briefing.

`parse_briefing` is stdlib only and is built from `marks`, the constants the
renderers write with, so the format and its reader cannot drift apart.
"""

from dataclasses import dataclass

from .briefing import RenderedBriefing, briefing_text, render_briefing
from .display import withheld_json, withheld_marker
from .heal import HealPlan, HealReport, HealSkip, HealTarget
from .heal_host import heal_all, heal_plan
from .inspector import human_lines as why_lines
from .inspector import inspect_item as why
from .serialize_liveness import (LIVE_STATES, SERIALIZE_STATES, SerializeRow,
                                 Serializes, serialize_rows, serializes)
from .marks import FORMAT_VERSION as BRIEFING_FORMAT
from .marks import ITEM_MARKS, RULING_MARK, WARNING_MARKERS
from .pending import queue
from .requests import inbox, inbox_listing, join, listing
from .store import project_slug, read_meta
from .surfaces import Writer
from .transcript import EXCLUDED_ROWS, Admitted, admitted_messages
from .view import ledger_health, lookup, masked, match, receipt_state

__all__ = ("lookup", "match", "why", "why_lines", "read_meta", "withheld_marker", "withheld_json", "queue", "listing",
           "inbox_listing", "inbox", "join", "ledger_health", "project_slug",
           "receipt_state", "Writer", "masked", "EXCLUDED_ROWS", "Admitted",
           "admitted_messages", "serializes", "serialize_rows", "SerializeRow",
           "Serializes", "SERIALIZE_STATES", "LIVE_STATES", "HealPlan",
           "HealReport", "HealTarget", "HealSkip", "heal_plan", "heal_all",
           "RenderedBriefing", "render_briefing", "briefing_text",
           "BRIEFING_FORMAT", "Briefing", "parse_briefing")


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
