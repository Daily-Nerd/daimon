"""The grammar of a message another person sent, and the one decision that
says whether this machine can take custody of it (#1350).

A transport carries requests, answers and replies between people's machines
and deletes its own copy once this machine acknowledges a message. An
acknowledgement is therefore a transfer of custody: if it follows a message
whose stored row cannot reproduce it, the content is gone for good while the
sender was told it arrived. "Store something" and "take custody" must be two
decisions, so this module owns both and keeps them apart:

    admit(content_type, body, local_forgotten) -> Custody | Hold | Reject

Only `Custody` can lead to an acknowledgement, and that is a fact about the
types, not a rule each caller remembers: `Custody` alone carries a `row` (what
a writer would store), while `Hold` and `Reject` carry no bytes and no row at
all. A caller that holds a `Hold` has nothing it could append or acknowledge.

* `Custody`: the row by itself lets every reader reproduce the message in
  full (`bytes`), or the local person ordered the content deleted
  (`forgotten`: every closed field is kept, no prose is).
* `Hold`: this version cannot take these bytes in custody now. The transport
  keeps the message and offers it again when `ADMISSION_EPOCH` changes.
* `Reject`: these bytes can never be admitted.

The module is pure: it reads no store, no file, no environment and
no reading of the date. `local_forgotten` (the content keys the local
person forgot, `store.all_forgotten_content_keys()`; never the view's set,
which adds teammates' published tombstones) is handed in. The only sibling
modules it imports are the ones that own what it must not copy: `requests`
(which event belongs to which party, the severity and kind tables, the
request id shape and the prose limits), `redact` and `normalize`.

Grammar of a `daimon.*` body: the canonical JSON of a closed object
(`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` over
strings, small integers and booleans). `parse` decodes, re-encodes and
requires byte equality, so a body has exactly one valid encoding. Closed
fields carry no prose and a fixed shape. Prose fields (`PROSE_LIMITS`) are the
only ones redaction and forget look at. The wire body of `revised` carries the
full request, unlike the local `revised` row (only the keys that changed): a
received message must stand on its own.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Collection, Mapping, NamedTuple, Union

from . import normalize, redact, requests

# One constant, returned with every decision. A transport offers held
# messages again when it changes. Change it whenever one of these changes:
#   * the schema version or any grammar below (a version, a field, a limit),
#   * the redaction set (`redact._PATTERNS`), because a body held as a secret
#     may stop matching, or a clean one start to,
#   * the availability of signed-act verification, because a held act can
#     then be verified on arrival.
# The byte vectors pin it: `tests/fixtures/peer_messages_vectors.json`.
ADMISSION_EPOCH = "peer-admission-1"

# The relay's size bound on one message.
MAX_BODY_BYTES = 16384
_MAX_INT = 1_000_000

# Which events each content type may carry. The party each event belongs to
# is `requests.EVENT_ROLE`, the single table; this module keeps no copy of it
# and a test fails when a type here is not one of its keys.
CONTENT_TYPES: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "daimon.request/1": ("opened", "revised", "withdrawn", "severity_set"),
    "daimon.answer/1": ("accepted", "rejected", "needs_info"),
    "daimon.reply/1": ("replied",),
    "daimon.done/1": ("done",),
})
# A signed act is opaque here: a well-formed compact JWS is held until the
# verification step exists, and when it does only this row of the grammar
# changes.
SIGNED_ACT = "signed.act"

# The prose fields and their limits, read from the module that owns them.
PROSE_LIMITS: Mapping[str, int] = MappingProxyType({
    "title": requests.TITLE_CHARS,
    "ask": requests.MAX_TEXT_CHARS,
    "why": requests.MAX_TEXT_CHARS,
    "evidence": requests.MAX_TEXT_CHARS,
    "severity_reason": requests.SEVERITY_REASON_CHARS,
    "note": requests.MAX_TEXT_CHARS,
})

CUSTODY_OUTCOMES = ("bytes", "forgotten")
HOLD_REASONS = ("unknown_content_type", "unsupported_body", "secret_pattern",
                "awaits_verification")
REJECT_REASONS = ("malformed_input", "oversize", "malformed_body",
                  "malformed_act")


class PeerMessageError(ValueError):
    """The bytes are not a message of this grammar. `reason` is one of
    `HOLD_REASONS` or `REJECT_REASONS`."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Grammar(NamedTuple):
    closed: tuple[str, ...]
    closed_optional: tuple[str, ...]
    prose: tuple[str, ...]
    prose_optional: tuple[str, ...]


_BASE = ("v", "type", "org", "request_id", "revision", "requester", "event_id")
_FULL_REQUEST = Grammar(
    _BASE + ("addressee", "kind_requested", "blocking", "severity"),
    ("supersedes",), ("ask", "why"), ("title", "severity_reason"))
_NOTE = Grammar(_BASE, (), (), ("note",))
_GRAMMARS: Mapping[str, Grammar] = MappingProxyType({
    "opened": _FULL_REQUEST,
    "revised": _FULL_REQUEST,
    "withdrawn": _NOTE,
    "severity_set": Grammar(_BASE + ("severity",), (), (),
                            ("severity_reason",)),
    "accepted": _NOTE,
    "rejected": _NOTE,
    "needs_info": _NOTE,
    "replied": Grammar(_BASE, (), ("note",), ("evidence",)),
    "done": Grammar(_BASE, (), ("evidence",), ()),
})

_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_EVENT_ID = re.compile(r"[0-9a-f]{32}")
_B64URL = re.compile(r"[A-Za-z0-9_-]+")


def _is_int(value) -> bool:
    return type(value) is int and 0 <= value <= _MAX_INT


def _is_str(value, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


_CLOSED_SHAPE = {
    "v": lambda x: _is_int(x) and x == 1,
    "type": lambda x: isinstance(x, str),
    "org": lambda x: _is_str(x, _TOKEN),
    "requester": lambda x: _is_str(x, _TOKEN),
    "addressee": lambda x: _is_str(x, _TOKEN),
    "request_id": requests.is_request_id,
    "supersedes": requests.is_request_id,
    "revision": _is_int,
    "event_id": lambda x: _is_str(x, _EVENT_ID),
    "kind_requested": lambda x: isinstance(x, str) and x in requests.KINDS,
    "blocking": lambda x: type(x) is bool,
    "severity": lambda x: isinstance(x, str) and x in requests.SEVERITIES,
}


@dataclass(frozen=True)
class Message:
    """A parsed `daimon.*` body: closed fields and prose fields apart."""
    content_type: str
    type: str
    closed: Mapping[str, object]
    prose: Mapping[str, str]


@dataclass(frozen=True)
class Custody:
    """The only outcome that can lead to an acknowledgement. `row` is what a
    writer stores: with `bytes` it holds the whole body, with `forgotten` only
    the closed fields."""
    row: Mapping[str, object]
    epoch: str

    @property
    def outcome(self) -> str:
        return str(self.row["outcome"])


@dataclass(frozen=True)
class Hold:
    """No bytes are kept. `closed_fields` and `fields` (the names of the prose
    fields that matched a redaction rule, never their text) let a human see
    that the message exists."""
    reason: str
    epoch: str
    body_sha256: str
    closed_fields: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({}))
    fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reject:
    reason: str
    epoch: str


Decision = Union[Custody, Hold, Reject]


def _encode(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _require_bytes(body) -> None:
    if not isinstance(body, bytes):
        raise PeerMessageError("malformed_input")
    if len(body) > MAX_BODY_BYTES:
        raise PeerMessageError("oversize")


def _refuse_constant(name: str):
    raise ValueError(f"not JSON: {name}")


def parse(content_type: str, body: bytes) -> Message:
    """Decode a `daimon.*` body, or raise `PeerMessageError` with the reason.

    Bytes that are not a JSON object at all can never be admitted
    (`malformed_body`). A JSON object that fails the grammar (an unknown
    version, a missing or extra field, a bad shape, an over-long prose field,
    a non-canonical encoding) is one a later version may take
    (`unsupported_body`)."""
    events = (CONTENT_TYPES.get(content_type)
              if isinstance(content_type, str) else None)
    if events is None:
        raise PeerMessageError("unknown_content_type")
    _require_bytes(body)
    try:
        obj = json.loads(body.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PeerMessageError("malformed_body") from None
    try:
        canonical = _encode(obj)
    except UnicodeEncodeError:
        canonical = b""
    if not isinstance(obj, dict) or canonical != body:
        raise PeerMessageError("unsupported_body")
    kind = obj.get("type")
    grammar = (_GRAMMARS.get(kind)
               if isinstance(kind, str) and kind in events else None)
    if grammar is None or not isinstance(kind, str):
        raise PeerMessageError("unsupported_body")
    names = set(obj)
    required = set(grammar.closed) | set(grammar.prose)
    allowed = (required | set(grammar.closed_optional)
               | set(grammar.prose_optional))
    if not required <= names or not names <= allowed:
        raise PeerMessageError("unsupported_body")
    closed: dict[str, object] = {}
    prose: dict[str, str] = {}
    for name, value in obj.items():
        if name in PROSE_LIMITS:
            if (not isinstance(value, str) or not value
                    or len(value) > PROSE_LIMITS[name]):
                raise PeerMessageError("unsupported_body")
            prose[name] = value
        elif _CLOSED_SHAPE[name](value):
            closed[name] = value
        else:
            raise PeerMessageError("unsupported_body")
    return Message(content_type, kind, MappingProxyType(closed),
                   MappingProxyType(prose))


def build(content_type: str, fields: Mapping[str, object]) -> bytes:
    """The one valid encoding of `fields`, checked by the same grammar `parse`
    applies. Raises `PeerMessageError` when `fields` is not a valid message."""
    body = _encode(dict(fields))
    parse(content_type, body)
    return body


def _segment(text: str) -> bytes:
    """One canonical unpadded base64url segment, or ValueError."""
    if not _B64URL.fullmatch(text):
        raise ValueError("not base64url")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("not base64url") from None
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != text:
        raise ValueError("non-canonical base64url")
    return raw


def _parse_act(body: bytes) -> Mapping[str, object]:
    """The closed facts of a compact JWS (three canonical base64url segments,
    the first two a JSON object each), or `PeerMessageError(malformed_act)`.
    Only the key id is read; the payload is neither kept nor looked at."""
    try:
        head, payload, signature = body.decode("ascii").split(".")
        header = json.loads(_segment(head).decode("utf-8"),
                            parse_constant=_refuse_constant)
        claims = json.loads(_segment(payload).decode("utf-8"),
                            parse_constant=_refuse_constant)
        if (not isinstance(header, dict) or not isinstance(claims, dict)
                or not _segment(signature)):
            raise ValueError("not a compact JWS")
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise PeerMessageError("malformed_act") from None
    kid = header.get("kid")
    return MappingProxyType({"kid": kid} if _is_str(kid, _TOKEN) else {})


def admit(content_type: str, body: bytes,
          local_forgotten: Collection[str]) -> Decision:
    """Decide whether this machine can take custody of `body`.

    `local_forgotten` is the set of content keys (`normalize.content_key`) the
    local person forgot. It is the only forget state consulted."""
    epoch = ADMISSION_EPOCH
    try:
        if not isinstance(content_type, str):
            raise PeerMessageError("malformed_input")
        _require_bytes(body)
    except PeerMessageError as err:
        return Reject(err.reason, epoch)
    digest = hashlib.sha256(body).hexdigest()
    if content_type == SIGNED_ACT:
        try:
            closed = _parse_act(body)
        except PeerMessageError as err:
            return Reject(err.reason, epoch)
        return Hold("awaits_verification", epoch, digest, closed)
    try:
        message = parse(content_type, body)
    except PeerMessageError as err:
        if err.reason in REJECT_REASONS:
            return Reject(err.reason, epoch)
        return Hold(err.reason, epoch, digest)
    # The record is deleted as a whole, as `requests.forget_content_key`
    # deletes one: a forgotten value anywhere in it forgets all its prose.
    if any(normalize.content_key(text) in local_forgotten
           for text in message.prose.values()):
        return Custody(MappingProxyType({
            "outcome": "forgotten", "content_type": content_type,
            "body_sha256": digest, "closed": message.closed}), epoch)
    # Redaction looks at prose and only prose; a closed field is never
    # scanned and never written through it.
    secret = tuple(name for name in sorted(message.prose)
                   if redact.redact_text(message.prose[name])[1])
    if secret:
        return Hold("secret_pattern", epoch, digest, message.closed, secret)
    return Custody(MappingProxyType({
        "outcome": "bytes", "content_type": content_type,
        "body_sha256": digest,
        "body_b64": base64.b64encode(body).decode("ascii")}), epoch)
