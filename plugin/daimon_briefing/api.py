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

`declare_project` is `hostgit.declare`: a host under the SEALED git posture
states a project's root, bucket slug and optional origin URL once per process
(it raises under the default ambient posture). It is configuration, not a
write: the declared root still passes the sealed shape check and the config
allowlist gate.

`project_slug` is `store.location_slug`: the pure character transform that
seeds a bucket name from a location. Resolving WHICH directory a value means is `config.resolve_project_dir`
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
`project-unknown`, `import`),
the same codes `daimon status` reports. An `import` skip is an attempt that
`daimon import` opened; heal never retries one, that command's own resume does. It never writes and never reaps. The
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

`api` is read-side with three named exceptions. `heal_all` (design D23) is a
launcher: it starts daimon's own serialize repair and writes no cognitive
content; nothing else here starts a process. `write_handoff` is the one
content write. `render_briefing` records delivery: it stamps `surfaced` on the
request and verdict cards its text printed whole, and writes the world-evidence
rows it ran (see Render below); `preview_briefing` is the read that writes
nothing.

Handoff (`write_handoff`). `write_handoff(project, text)` leaves the baton the
next briefing leads with, for a project given as a path. It is ALWAYS agent
tier: it never asks the terminal, so a host process that holds a TTY still
writes an agent row, and `daimon brief` prints it as "written by an agent,
unverified", never as a person's note. The size limit is `HANDOFF_MAX_CHARS`
and `HANDOFF_MAX_BYTES` (UTF-8; read both before building the text). Over
either raises `HandoffTooLong`, which carries `.limit`, `.length`,
`.byte_limit` and `.size`; nothing is truncated or written. A text with a
character a terminal acts on (a C0 control other than newline and tab, DEL, a
C1 control, U+2028 or U+2029) raises `HandoffUnprintable`. An empty text
raises `ValueError`; `HandoffNotRecorded` means daimon is disabled, the project
is unknown or the ledger is unwritable. Latest wins.

The baton leads every briefing, whichever host renders it (the CLI, the MCP
tool, `render_briefing`, the Hermes hook): `briefing.prepare` reads it once.
Its block is a title line, one `\u2192 ` line per line of the baton, and a blank
line. The title names the writer: `HANDOFF (left deliberately by previous
session, <ts>):` for a person, `HANDOFF (written by an agent, unverified,
<ts>):` for an agent or a source no table knows. `parse_briefing` returns it as
`Briefing.handoff` (`Handoff(text, ts, tier)`, tier `human` or `agent`) and
keeps its lines out of every other field (format 3).

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

The text carries what `daimon brief` prints beside the body, from the same
composer (#1402): the baton, the format-version note (a warning line), the
standing rulings, the decision count, the four request panels (requests
waiting on you, decisions on requests you sent, requests you owe, requests you
sent that nobody is moving) and the withheld trailer. They read the
requested project's own ledgers only: a named bucket or another project's
path shows none. `RenderedBriefing.printed` is the cards printed whole.
`render_briefing` commits their `surfaced` stamps at its return, because this
API has no later seam and the return is its delivery boundary: the sender's
"seen" means the text reached the caller. A collapsed panel (the budget cut it
to a count) stamps nothing. `preview_briefing` takes the same arguments,
returns the same text and writes no stamp and no world-evidence row, for a
caller that reads a briefing without handing it to an agent.

The bytes are stable only under these conditions, which `testing.sandbox`
enforces for a host test:
  - pass `project_dir` (None reads the environment and the cwd);
  - pass `now` (the stale marks and ages read the clock);
  - the project is not inside a git working tree (the resolver walks up to the
    repo top) and not at or below the home directory (ruling layers and `~`
    paths would change the text);
  - the settings (budgets, caps, tenant flags, capture host, the check dir)
    come from the sandbox mapping, not the process environment.
  - DAIMON_WORLDCHECK is off (with it on, a carried claim the world has
    contradicted is marked, and the ledger rows are written, #1279);
  - the requests ledgers of this project and of the projects it exchanges
    requests with are part of the input (the panels read them), and `now`
    also sets the overdue ages. A `surfaced` stamp does not change the panel
    bytes of the next read.

Round trip: for `briefing` and `closed`, `parse_briefing(text).header` is
`marks.GREETING`, every `standing_rulings` entry starts with `marks.RULING_MARK`,
every `items` entry starts with a mark in `marks.ITEM_MARKS`, and every note is
in `warnings`. For `no-checkpoint` and `empty` the header is the orientation
line (also when standing rulings lead the text: their title is never the
header) and the notes are still `warnings`. The request panels, the decision
count and the withheld trailer are fields of their own (format 4): `requests`,
`verdicts`, `owed` and `overdue` (a `Panel` each), `decisions` and `withheld`,
each None when the text has no such section. A panel is a block from its
frozen header to the next blank line, read by its header and never by a mark
(the baton and a request card both start with an arrow); the first block of a
name wins and a repeat is swallowed whole. `BRIEFING_FORMAT` is an integer that
changes only when what `parse_briefing` extracts changes; it is never printed
into a briefing.

`parse_briefing` is stdlib only and is built from `marks`, the constants the
renderers write with, so the format and its reader cannot drift apart.
"""

import re as _re
from dataclasses import dataclass

from .briefing import (RenderedBriefing, briefing_text, preview_briefing,
                       render_briefing)
from .display import withheld_json, withheld_marker
from .heal import HealPlan, HealReport, HealSkip, HealTarget
from .handoff import MAX_BYTES as HANDOFF_MAX_BYTES
from .handoff import MAX_CHARS as HANDOFF_MAX_CHARS
from .handoff import HandoffNotRecorded, HandoffTooLong, HandoffUnprintable
from .handoff import write as write_handoff
from .heal_host import heal_all, heal_plan
from .hostgit import declare as declare_project
from .inspector import human_lines as why_lines
from .inspector import inspect_item as why
from .serialize_liveness import (LIVE_STATES, SERIALIZE_STATES, SerializeRow,
                                 Serializes, serialize_rows, serializes)
from .marks import FORMAT_VERSION as BRIEFING_FORMAT
from . import marks as _marks
from .marks import ITEM_MARKS, RULING_MARK, WARNING_MARKERS
from .pending import queue
from .requests import inbox, inbox_listing, join, listing
from .store import location_slug as project_slug
from .store import read_meta
from .surfaces import Writer
from .transcript import EXCLUDED_ROWS, Admitted, admitted_messages
from .view import ledger_health, lookup, masked, match, receipt_state

__all__ = ("lookup", "match", "why", "why_lines", "read_meta", "withheld_marker", "withheld_json", "queue", "listing",
           "inbox_listing", "inbox", "join", "ledger_health", "project_slug",
           "receipt_state", "Writer", "masked", "EXCLUDED_ROWS", "Admitted",
           "admitted_messages", "serializes", "serialize_rows", "SerializeRow",
           "Serializes", "SERIALIZE_STATES", "LIVE_STATES", "HealPlan",
           "HealReport", "HealTarget", "HealSkip", "heal_plan", "heal_all",
           "RenderedBriefing", "render_briefing", "preview_briefing",
           "briefing_text",
           "BRIEFING_FORMAT", "declare_project", "HANDOFF_MAX_CHARS",
           "HANDOFF_MAX_BYTES", "HandoffTooLong", "HandoffUnprintable",
           "HandoffNotRecorded", "write_handoff", "Handoff", "PanelCard",
           "Panel", "DecisionCount", "Withheld", "Briefing",
           "parse_briefing")


@dataclass(frozen=True)
class Handoff:
    """The baton block of a briefing: its `text` (the lines behind the arrow
    mark, joined by newlines), the `ts` it was written and the `tier` the
    title names, `human` or `agent` (an agent's baton, or one whose source no
    table knows, reads `agent`)."""

    text: str
    ts: str
    tier: str


@dataclass(frozen=True)
class PanelCard:
    """One card of a request panel: the `request_id` (the first id on the
    line, which daimon wrote before any foreign text), the card `line` as
    printed with its mark, and its indented `detail` lines in order with the
    indent removed."""

    request_id: str
    line: str
    detail: tuple = ()


@dataclass(frozen=True)
class Panel:
    """A request panel as printed. `cards` are in print order; `more` is the
    `(+N more ...)` overflow count; `cut` the cards a collapsed panel lost to
    the budget (0 when it printed whole); `lines` is every line of the block
    after its header, as printed, so a consumer that wants the text has it."""

    cards: tuple = ()
    more: int = 0
    cut: int = 0
    lines: tuple = ()


@dataclass(frozen=True)
class DecisionCount:
    """The decision count line: `here` waiting on this project, `elsewhere`
    waiting in other projects (0 when the line names none)."""

    here: int
    elsewhere: int = 0


@dataclass(frozen=True)
class Withheld:
    """The withheld trailer: how many resolved and quarantined items the
    briefing left out. A count a teammate's checkpoint added is included."""

    resolved: int = 0
    quarantined: int = 0


@dataclass(frozen=True)
class Briefing:
    """A briefing split by line kind. `header` is the first line that is not
    a warning, `standing_rulings` start with the ruling mark, `items` with an
    item mark, and `warnings` carry a warning marker anywhere. Any other line
    outside the blocks below is not kept. `handoff` is the baton block that
    leads a briefing (format 3), or None; its lines are only ever the baton's
    text, so what a baton says can never read as a header, a ruling, an item
    or a warning. `raw` is the text as given.

    Format 4 adds the request furniture, each None when the section was not
    in the text: `requests`, `verdicts`, `owed` and `overdue` (a `Panel`
    each), `decisions` (the count line) and `withheld` (the trailer). A panel
    is a block that opens at its frozen header and closes at the next blank
    line; everything inside it lands in that panel and nowhere else (a
    column-0 warning line also lands in `warnings`, which only daimon can
    write there)."""

    header: str = ""
    standing_rulings: tuple = ()
    items: tuple = ()
    warnings: tuple = ()
    raw: str = ""
    handoff: Handoff | None = None
    requests: Panel | None = None
    verdicts: Panel | None = None
    owed: Panel | None = None
    overdue: Panel | None = None
    decisions: DecisionCount | None = None
    withheld: Withheld | None = None


_HANDOFF_TITLE = _re.compile(
    "^" + _re.escape(_marks.HANDOFF_LEAD) + "(" + _re.escape(_marks.HANDOFF_BY_HUMAN) + "|"
    + _re.escape(_marks.HANDOFF_BY_AGENT) + r"), (.*)\):$")
_ARROW = _marks.HANDOFF_LINE_MARK + " "
_REQUEST_ID = _re.compile(_marks.REQUEST_ID_PATTERN)
_MORE = _re.compile(r"^\(\+(\d+) more ")
_CUT = _re.compile(r"^\((\d+) cut for budget, see: ")


def _template(template: str, **groups: str) -> str:
    """`template` as a regex source: its literal text escaped and each
    `{name}` replaced by the group source in `groups`."""
    source = _re.escape(template)
    for name, group in groups.items():
        source = source.replace(_re.escape("{" + name + "}"), group)
    return source


_ELSEWHERE = "(?:" + _template(_marks.DECISION_ELSEWHERE,
                               n=r"(?P<e>\d+)") + ")?"
_COUNT = [_re.compile("^" + _template(t, n=r"(?P<n>\d+)",
                                      elsewhere=_ELSEWHERE) + "$")
          for t in (_marks.DECISION_COUNT_PLURAL,
                    _marks.DECISION_COUNT_SINGULAR)]
_RESOLVED = _re.compile(r"^(\d+) " + _re.escape(_marks.WITHHELD_RESOLVED))
_QUARANTINED = _re.compile(r"^(\d+) " + _re.escape(_marks.WITHHELD_QUARANTINED))
_PANEL_FIELDS = {"request": "requests", "verdict": "verdicts", "owed": "owed",
                 "overdue": "overdue"}


class _Block:
    """The panel being read: its fields so far. `discard` swallows a repeat."""

    def __init__(self, name: str, discard: bool):
        self.name, self.discard = name, discard
        self.cards: list = []        # [request_id, line, [detail...]]
        self.more = self.cut = 0
        self.lines: list = []

    def take(self, raw: str, stripped: str, warnings: list) -> None:
        """One non-blank line of the block, whatever it says."""
        if self.discard:
            return
        self.lines.append(raw)
        if raw.startswith(_marks.WARNING_MARK):
            warnings.append(stripped)
        elif not raw.startswith(_marks.PANEL_INDENT):
            found = _REQUEST_ID.search(raw)
            if found:
                self.cards.append([found.group(0), raw, []])
        else:
            text = raw[len(_marks.PANEL_INDENT):].strip()
            if (more := _MORE.match(text)):
                self.more = int(more.group(1))
            elif (cut := _CUT.match(text)):
                self.cut = int(cut.group(1))
            elif text != _marks.OWED_CLOSE_LINE and self.cards:
                self.cards[-1][2].append(text)

    def panel(self) -> Panel:
        return Panel(tuple(PanelCard(i, line, tuple(d))
                           for i, line, d in self.cards),
                     self.more, self.cut, tuple(self.lines))


def parse_briefing(text: str) -> Briefing:
    """Split `daimon brief` text into header, standing rulings, items,
    warnings, the baton and, from format 4, the request furniture. Tolerant:
    a line of none of those kinds is dropped, never an error. The warning
    markers are checked first for every line, so a note above the greeting
    never becomes the header.

    Block state is by header, never by mark: `→` is both the baton's line
    mark and the request card mark, and `✓` both an owed card and an
    accepted verdict, so only the block says which. A panel header opens a
    block only before the first item (daimon prints the panels above the
    sections), and only the first block of each name is kept: a repeat is
    swallowed whole, so text a peer got into the briefing can neither replace
    a real panel nor leak lines into the rulings or the items. This is sound
    because every foreign field a panel prints is one physical line, so a
    blank line or a line start inside a block is always daimon's."""
    header = ""
    rulings: list = []
    items: list = []
    warnings: list = []
    handoff = None
    open_baton: tuple | None = None    # (tier, ts, lines) while in the block
    block: _Block | None = None
    panels: dict = {}
    seen_panels: set = set()
    decisions = None
    resolved = quarantined = None
    item_seen = False
    for line in text.splitlines():
        stripped = line.strip()
        if open_baton is not None:
            if stripped.startswith(_ARROW):
                open_baton[2].append(stripped[len(_ARROW):])
                continue
            handoff = Handoff("\n".join(open_baton[2]), open_baton[1],
                              open_baton[0])
            open_baton = None
        if block is not None:
            if stripped:
                block.take(line, stripped, warnings)
                continue
            if not block.discard:
                panels[_PANEL_FIELDS[block.name]] = block.panel()
            block = None
            continue
        if not stripped:
            continue
        titled = (_HANDOFF_TITLE.match(stripped)
                  if handoff is None and not header else None)
        if titled:
            tier = "human" if titled[1] == _marks.HANDOFF_BY_HUMAN else "agent"
            open_baton = (tier, titled[2], [])
            continue
        name = _marks.PANEL_HEADERS.get(stripped) if line == stripped else None
        if name and not item_seen:
            block = _Block(name, name in seen_panels)
            seen_panels.add(name)
            continue
        if stripped == _marks.RULINGS_HEADER:
            continue
        if decisions is None and not item_seen:
            for rx in _COUNT:
                counted = rx.match(stripped)
                if counted:
                    found = counted.groupdict()
                    decisions = DecisionCount(int(found.get("n") or 1),
                                              int(found.get("e") or 0))
                    break
            if decisions is not None:
                continue
        if resolved is None and (m := _RESOLVED.match(stripped)):
            resolved = int(m.group(1))
            continue
        if quarantined is None and (m := _QUARANTINED.match(stripped)):
            quarantined = int(m.group(1))
            continue
        if any(marker in stripped for marker in WARNING_MARKERS):
            warnings.append(stripped)
            continue
        if stripped.startswith(RULING_MARK):
            rulings.append(stripped)
            continue
        if not header:
            header = stripped
            continue
        if stripped.startswith(ITEM_MARKS):
            items.append(stripped)
            item_seen = True
    if open_baton is not None:
        handoff = Handoff("\n".join(open_baton[2]), open_baton[1],
                          open_baton[0])
    if block is not None and not block.discard:
        panels[_PANEL_FIELDS[block.name]] = block.panel()
    withheld = (Withheld(resolved or 0, quarantined or 0)
                if resolved is not None or quarantined is not None else None)
    return Briefing(header, tuple(rulings), tuple(items), tuple(warnings),
                    text, handoff, decisions=decisions, withheld=withheld,
                    **panels)
