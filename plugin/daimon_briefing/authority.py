"""The one owner of "what authority tier does this row carry?" (#1301).

Whether a ledger row is a human act, an agent act or a mechanical one used to
be decided in every ledger module, each with its own lookup into its own copy
of a channel table, and in the events ledger through a separate set of
`source` values. A copy that was missed answered differently from the rest.

`of_row(ledger, row)` is the single answer. In this change it returns exactly
what those lookups returned: the row's CHANNEL (its `source` for the events
ledger) mapped through that ledger's table, and `None` for a channel the
ledger does not know. It never reads the row's stored `authority` field: that
is the row's claim about itself, and authority is a property of the observed
write path (see `channels.py`).

The tables live here. The ledger modules keep their `CHANNEL_AUTHORITY` and
`CHANNELS` names only as bindings to the same objects, for importers outside
the repo that pin them; nothing in the package reads through those names, and
`tests/test_authority_census.py` fails the build when something does.

A later change that alters what counts as human alters it in this file only.

The same file also owns what a handsal checker answer means (#1374). It holds
the checker's reason class table, a vendored copy, and maps an answer to an
outcome (`recheck_outcome`, `machine_status`). It reads who signed an act the
checker proved (`performer_of`). Both are pure: nothing here runs the checker
or stores anything. The file stays one module because the table, the outcome
and the signer are read by the same future caller and change with the same
pin.

It also owns what a person signs (#1377): the one builder of the decision
handed to the signing side (`build_decision`) and the one answer to "can this
text be shown whole at signing?" (`signable_problem`, a count of what
handsal's renderer will show). The write boundary of each ledger asks that
same answer when text enters, so a ruling or an ask that cannot be signed is
refused when it is written and never reaches the builder.
"""

from __future__ import annotations

import base64
import dataclasses
import enum
import functools
import hashlib
import json
import re
from collections.abc import Mapping
from importlib import resources
from typing import NamedTuple, Union

from . import channels as _channels

# Event-ledger `source` values whose tier is HUMAN. `ui` and `cli-tty` are
# peers of `cli`, not replacements: the ledger is append-only and every row
# already written says `cli`, so the set widens and never renames. `cli` is
# the legacy human source. Widening it widens WHO counts, never WHAT counts:
# callers counting events must still filter on `kind` themselves (the
# lifecycle fold reads lifecycle kinds only, a plain count does not).
HUMAN_EVENT_SOURCES = frozenset({"cli", "cli-tty", "ui"})
# Sources a machine path names itself with. They are agent tier; which of the
# two a row carries (`serializer` verified a claim, `agent` made one) is a
# credit category the stats fold reads off the source, not a tier.
AGENT_EVENT_SOURCES = frozenset({"agent", "serializer"})

# The rulings table is one object shared by the three ledgers built on it
# (rulings and refutations, requests, amendments): a channel any of them
# admits, all of them admit.
_RULING_TABLE = _channels.merged_authority({"mechanical": "mechanical"})

_TABLES: dict[str, dict[str, str]] = {
    "refutations": _RULING_TABLE,
    "requests": _RULING_TABLE,
    "amendments": _RULING_TABLE,
    # Only the shared four: an agent proposes, a human decides. No
    # `mechanical` tier and no importer channels.
    "trust": _channels.merged_authority(),
    # `serializer`/`lab-import` are relations' own two importer channels.
    "relations": _channels.merged_authority(
        {"serializer": "agent", "lab-import": "agent"}),
    "events": {
        **{source: "human" for source in HUMAN_EVENT_SOURCES},
        **{source: "agent" for source in AGENT_EVENT_SOURCES},
    },
}

# The row field that names the write path, per ledger.
_FIELD = {"events": "source"}


def table(ledger: str) -> dict[str, str]:
    """The ledger's channel -> tier mapping, the live object. For the ledger
    modules' re-export of their historical constant; read tiers through
    `of_channel` / `of_row`."""
    return _TABLES[ledger]


def channels(ledger: str) -> frozenset[str]:
    """The channels `ledger` admits."""
    return frozenset(_TABLES[ledger])


def of_channel(ledger: str, channel) -> str | None:
    """The tier `channel` carries in `ledger`, or None for one it does not
    know. For callers that hold the channel of an act rather than a row (a
    write boundary, a gate). Anything but a string is not a channel."""
    return _TABLES[ledger].get(str(channel or ""))


def of_row(ledger: str, row: dict) -> str | None:
    """The authority tier of one row of `ledger`: `human`, `agent`,
    `mechanical`, or None when the row's channel is absent or unknown.

    Rows are read off disk, so the channel can be missing or not a string;
    neither table has an empty-string key, so an unusable channel misses
    exactly as it always did."""
    return of_channel(ledger, row.get(_FIELD.get(ledger, "channel")))


# ---- handsal checker answers (#1374) ----------------------------------------

class HandsalPin(NamedTuple):
    commit: str
    table_sha256: str


# The one pin: the handsal commit (tag v0.4.2) the vendored table was copied
# from and the sha256 of that file. Bumping it means replacing the file and
# both values together; `tests/test_authority_checker.py` fails otherwise and
# compares the old table with the new one.
HANDSAL_PIN = HandsalPin(
    commit="09dc029c2d738c4346d05ea7bf2d9f57b338eb61",
    table_sha256=(
        "48825e0ac5ebbe2da361cf3c06a4efd1611b17a8ea043d07e11d2cedb96cd75a"),
)

# The checker command whose answers carry an act, and so the table entry that
# is read before the default one.
_RECHECK_COMMAND = "recheck-act"

# `machine_status` for an `enrolled` answer: these reasons mean "this machine
# is not set up yet". Every other reason, an unknown one included, is damage.
NOT_SET_UP_CODES = frozenset({
    "pin_missing", "pin_probe_unavailable", "pin_probe_unprotected",
    "store_missing"})


class CheckerFailure(enum.Enum):
    """A run that produced no usable answer. The value names what happened."""
    NONZERO_EXIT = "nonzero_exit"
    UNPARSEABLE = "unparseable_output"
    BINARY_MISSING = "binary_missing"


class Fault(enum.Enum):
    """Why an undetermined outcome is a defect to report, not a plain "could
    not tell"."""
    BAD_REQUEST = "bad_request"              # class `input`: daimon asked wrong
    UNEXPECTED_CLASS = "unexpected_class"    # a class recheck-act never gives
    UNKNOWN_CODE = "unknown_code"            # not in the table
    UNMAPPED_CLASS = "unmapped_class"        # a class daimon has no outcome for
    UNCONFIRMED_OK = "unconfirmed_ok"        # reason ok without rechecked:true
    MALFORMED_ANSWER = "malformed_answer"    # parsed, but no usable reason
    NONZERO_EXIT = "nonzero_exit"
    UNPARSEABLE = "unparseable_output"
    BINARY_MISSING = "binary_missing"


class MachineStatus(enum.Enum):
    ENROLLED = "enrolled"
    NOT_SET_UP = "not_set_up"
    BROKEN = "broken"


@dataclasses.dataclass(frozen=True)
class Proven:
    """The checker accepted `act` (the JWS that was checked). Only
    `recheck_outcome` builds one."""
    act: str

    @property
    def reason(self) -> str:
        return "ok"


@dataclasses.dataclass(frozen=True)
class Refuted:
    """The checker found the act invalid."""
    reason: str


@dataclasses.dataclass(frozen=True)
class Undetermined:
    """The checker could not say. Not "false": it says nothing about the act.
    `fault` is None for an answer that is plainly "not now" (environment,
    membership) and names the defect otherwise."""
    reason: str
    fault: Fault | None


Outcome = Union[Proven, Refuted, Undetermined]
CheckerResult = Union[Mapping, CheckerFailure]

# handsal reason class -> (verdict, fault). `ok` is decided separately: it
# needs `rechecked: true`. Every class in the table must be a key here; a new
# class fails `test_authority_checker.py` until someone decides its outcome.
_CLASS_OUTCOME: dict[str, tuple[str, Fault | None]] = {
    "ok": ("proven", None),
    "validity": ("refuted", None),
    "environment": ("undetermined", None),
    "membership": ("undetermined", None),
    "input": ("undetermined", Fault.BAD_REQUEST),
    "time": ("undetermined", Fault.UNEXPECTED_CLASS),
    "freshness": ("undetermined", Fault.UNEXPECTED_CLASS),
    "admission": ("undetermined", Fault.UNEXPECTED_CLASS),
    "capacity": ("undetermined", Fault.UNEXPECTED_CLASS),
}
MAPPED_CLASSES = frozenset(_CLASS_OUTCOME)

_FAILURE_FAULT = {
    CheckerFailure.NONZERO_EXIT: Fault.NONZERO_EXIT,
    CheckerFailure.UNPARSEABLE: Fault.UNPARSEABLE,
    CheckerFailure.BINARY_MISSING: Fault.BINARY_MISSING,
}


def reason_classes_bytes() -> bytes:
    """The vendored table, byte for byte as handsal publishes it."""
    return (resources.files("daimon_briefing") / "_handsal"
            / "reason-classes.json").read_bytes()


@functools.lru_cache(maxsize=1)
def _load_table() -> dict:
    data = reason_classes_bytes()
    if hashlib.sha256(data).hexdigest() != HANDSAL_PIN.table_sha256:
        raise RuntimeError(
            "the packaged handsal reason class table does not match its pin "
            f"({HANDSAL_PIN.commit})")
    return json.loads(data)


def _class_of(code: str, command: str = _RECHECK_COMMAND) -> str | None:
    """The reason class of `code`: the command's own entry first, then the
    default. None for a code the table does not hold. Never read the
    answer's own `class` key for this: that is the signing device's class."""
    table = _load_table()
    own = table["by_command"].get(command, {})
    return own.get(code) or table["default"].get(code)


def _reason_of(answer: Mapping) -> str | None:
    code = answer["error"] if "error" in answer else answer.get("reason")
    return code if isinstance(code, str) and code else None


def recheck_outcome(result: CheckerResult, act: str) -> Outcome:
    """The outcome of one `recheck-act` run over `act`.

    `result` is what the caller captured: the parsed answer, or the
    `CheckerFailure` that stopped it. Only `ok` with `rechecked` exactly
    `True` is proven. A reason the table does not hold, a class with no
    mapping and every failed run are undetermined and carry a fault."""
    if isinstance(result, CheckerFailure):
        return Undetermined(result.value, _FAILURE_FAULT[result])
    if not isinstance(result, Mapping):
        return Undetermined("not_an_object", Fault.MALFORMED_ANSWER)
    code = _reason_of(result)
    if code is None:
        return Undetermined("no_reason", Fault.MALFORMED_ANSWER)
    cls = _class_of(code)
    if cls is None:
        return Undetermined(code, Fault.UNKNOWN_CODE)
    if cls not in _CLASS_OUTCOME:
        return Undetermined(code, Fault.UNMAPPED_CLASS)
    verdict, fault = _CLASS_OUTCOME[cls]
    if verdict == "proven":
        if "error" not in result and result.get("rechecked") is True:
            return Proven(act)
        return Undetermined(code, Fault.UNCONFIRMED_OK)
    if verdict == "refuted":
        return Refuted(code)
    return Undetermined(code, fault)


def machine_status(result: CheckerResult) -> MachineStatus:
    """What an `enrolled` run says about this machine. One class holds both
    "not set up yet" and "damaged", so this goes by code, not class."""
    if result is CheckerFailure.BINARY_MISSING:
        return MachineStatus.NOT_SET_UP
    if not isinstance(result, Mapping):
        return MachineStatus.BROKEN
    if result.get("enrolled") is True:
        return MachineStatus.ENROLLED
    if result.get("enrolled") is False and "error" not in result \
            and result.get("reason") in NOT_SET_UP_CODES:
        return MachineStatus.NOT_SET_UP
    return MachineStatus.BROKEN


# ---- who signed (#1374) ------------------------------------------------------

class ProvenActError(ValueError):
    """The act a proven outcome holds does not decode as handsal's act format.
    The checker accepted it, so this is a contract breach, not a case to
    default over."""


# The exact payload key set of a handsal act (profile ACT-01).
_PAYLOAD_KEYS = frozenset({
    "action_ref", "binding", "cost", "ext", "inputs_hash", "log_policy",
    "method", "nonce", "outputs_hash", "parent_performer_id",
    "parent_receipt_hash", "performer_id", "reason", "requester_id", "status",
    "ts", "v"})
_B64URL = re.compile(r"[A-Za-z0-9_-]*")


def _payload_of(proven: Proven) -> dict:
    """The payload of the act a proven outcome holds, or ProvenActError.
    handsal's format: the middle segment of a compact JWS, base64url without
    padding, canonical JSON (sorted keys, no spaces) of bytes 0x20 to 0x7E."""
    if not isinstance(proven, Proven):
        raise TypeError("a proven outcome is required")
    act = proven.act
    if not isinstance(act, str):
        raise ProvenActError("the proven act is not a string")
    parts = act.split(".")
    if len(parts) != 3:
        raise ProvenActError("the proven act is not a compact JWS")
    segment = parts[1]
    if not _B64URL.fullmatch(segment) or len(segment) % 4 == 1:
        raise ProvenActError("the act payload is not unpadded base64url")
    raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    if any(b < 0x20 or b > 0x7E for b in raw):
        raise ProvenActError("the act payload has a byte outside 0x20-0x7E")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ProvenActError("the act payload is not JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ProvenActError("the act payload is not handsal's key set")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("ascii")
    if canonical != raw:
        raise ProvenActError("the act payload is not canonical JSON")
    return payload


def _text_field(proven: Proven, key: str) -> str:
    value = _payload_of(proven)[key]
    if not isinstance(value, str) or not value:
        raise ProvenActError(f"the act payload's {key} is not a string")
    return value


def performer_of(proven: Proven) -> str:
    """The `performer_id` of the act a proven outcome holds: the person the
    checker tied to the signing key. Worked out on each read, stored nowhere.
    Takes the outcome, never a raw act, so it cannot run for an act the
    checker did not accept (anything else is a TypeError)."""
    return _text_field(proven, "performer_id")


def claimed_signing_time(proven: Proven) -> str:
    """The act's `ts`: the signer's own clock, which the checker does not
    vouch for. For display only: nothing orders, expires or compares by it."""
    return _text_field(proven, "ts")


# ---- what a person signs, and whether it can be shown whole (#1377) ----------

# The consumer id of every decision daimon asks a person to sign. A stored
# decision is checked against it for as long as its row lives, so it is the
# literal word, never derived from the package or the product name.
CONSUMER = "daimon"

# Verb -> (the field of the row that is the subject, the field of the row
# that is the text the person decides on). The keys are the closed list of
# daimon verbs a decision may carry; each fits handsal's `action` grammar
# (lowercase words separated by one space, 1 to 24 characters). A verb joins
# this table together with its content rule, never before. Every request
# verdict signs the ask and nothing else: what is typed with a verdict (a
# reject reason, a note) is not on the signing screen.
_VERB_SOURCES: dict[str, tuple[str, str]] = {
    "ratify": ("refutation_id", "verdict"),
    "accept": ("request_id", "ask"),
    "reject": ("request_id", "ask"),
    "done": ("request_id", "ask"),
}
VERBS = tuple(_VERB_SOURCES)

# handsal's request-block bound at the pinned commit (profile-interfaces.md
# RN-05, RN-12): at most this many source bytes and at most this many block
# lines, continuation lines included. Each source line is escaped (0x20 to
# 0x7E as is, every other byte as four characters) and an escaped line over
# RENDER_LINE_WIDTH continues on further lines, never splitting an escape.
MAX_CONTENT_BYTES = 2048
MAX_RENDERED_LINES = 32
RENDER_LINE_WIDTH = 96


class NotSignable(ValueError):
    """A decision cannot be built: the verb is not one of `VERBS`, the row
    lacks its id or text, or the text cannot be shown whole at signing."""


def content_bytes(text: str) -> bytes:
    """The bytes a person decides on: the stored string in UTF-8, exactly,
    with no trimming and no normalising. The one place text becomes content.
    daimon counts what the signer will render, it never wraps or alters it."""
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise NotSignable("the text is not valid UTF-8") from exc


def rendered_lines(content: bytes) -> int:
    """How many block lines handsal's renderer shows for `content`.

    A copy of the rule, pinned by `tests/test_authority_decision.py` against
    handsal's own render vectors. Source lines are split on LF; a final LF
    ends the last line and starts none, so empty content has no line; an
    empty source line takes one. An escaped line is packed greedily into
    lines of at most RENDER_LINE_WIDTH, an escape moving whole to the next
    line when it would pass the width."""
    if not content:
        return 0
    source = content.split(b"\n")
    if content.endswith(b"\n"):
        source.pop()
    total = 0
    for line in source:
        total += 1
        used = 0
        for byte in line:
            cost = 1 if 0x20 <= byte <= 0x7E else 4
            if used + cost > RENDER_LINE_WIDTH:
                total += 1
                used = 0
            used += cost
    return total


def signable_problem(text: str, *, what: str = "text") -> str | None:
    """None when `text` can be shown whole at signing, else one sentence that
    names the size, the limit and which limit was passed (bytes, lines, or
    both). The single owner of "does it fit": the write boundaries and the
    builder both ask here, so fitting at creation and fitting at signing
    cannot disagree."""
    try:
        content = content_bytes(text)
    except NotSignable as exc:
        return f"{what} cannot be signed: {exc}"
    problems = []
    if len(content) > MAX_CONTENT_BYTES:
        problems.append(
            f"{what} is {len(content)} bytes, over the limit of "
            f"{MAX_CONTENT_BYTES} bytes a person can be shown whole at "
            "signing")
    lines = rendered_lines(content)
    if lines > MAX_RENDERED_LINES:
        problems.append(
            f"{what} takes {lines} lines on the signing screen, over the "
            f"limit of {MAX_RENDERED_LINES} lines")
    return "; ".join(problems) or None


def build_decision(verb: str, row: Mapping) -> dict:
    """The decision handed to the signing side for `verb` on `row`:
    `{consumer, action, subject, decision_b64}`. `row` is the stored ruling
    (for `ratify`) or request (for `accept`, `reject`, `done`) as the ledger
    folds it. Pure: it runs nothing and signs nothing.

    A stored row over the size bound is refused with NotSignable rather than
    cut or summarised: a person never signs bytes they could not read."""
    sources = _VERB_SOURCES.get(verb)
    if sources is None:
        raise NotSignable(
            f"{verb!r} is not a signing verb; the verbs are "
            f"{', '.join(VERBS)}")
    id_field, text_field = sources
    subject = row.get(id_field)
    if not isinstance(subject, str) or not subject:
        raise NotSignable(f"the row has no id ({id_field})")
    text = row.get(text_field)
    if not isinstance(text, str):
        raise NotSignable(f"the row has no {text_field} to sign")
    problem = signable_problem(text, what=text_field)
    if problem is not None:
        raise NotSignable(problem)
    return {
        "consumer": CONSUMER,
        "action": verb,
        "subject": subject,
        "decision_b64": base64.b64encode(content_bytes(text)).decode("ascii"),
    }
