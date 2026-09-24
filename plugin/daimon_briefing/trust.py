"""Project-scoped human quarantine ledger (#1109 Slice 1).

Every existing signal daimon has about a bad checkpoint item RANKS it down —
stale, superseded, contradicted — and none of them withholds it (`schema.py`'s
own admission-state doctrine). That is the right default for a machine
signal: an old or contradicted decision is still evidence a reader may need.
It is the wrong answer for an item a human has looked at and judged unsafe to
act on, such as a planted instruction or a fabricated decision.

This module is the discrete, human-only, LATCHED exception: a value a human
has quarantined stays quarantined until a human releases it, no matter what
machine signal fires. Nothing reads this ledger yet — it is written, folded,
and registered here only. Wiring it into the briefing, recall, suggest, the
MCP tools, carry, and `daimon_ui` is PR 2.

Identity is VALUE-keyed, not id-keyed, and this is the module's central
design fact (design doc §2): an item id a quarantine names can be bypassed
the moment the same text re-enters the checkpoint under a different id —
straight carry keeps the id, but a re-extraction below carry's identity-
inheritance bar mints a brand-new one for the same or lightly-reworded text.
`forget` solved exactly this problem already, for a harder version of it
(permanent deletion): `store.forgotten_content_keys` keys on
`normalize.content_key(text)`, never on an item id, so a forgotten value is
unreachable no matter what id a later re-extraction mints. `value_key`
below reuses that identical algorithm; `item_id` rides on the row only as an
accelerator/audit trail and is never the gate.

Authority follows `refutations.py`'s doctrine unchanged: it is a property of
the OBSERVED write channel, never a caller's claim about itself. An agent
may PROPOSE a quarantine (candidate, inert); only a human channel may
CONFIRM, DISMISS, or RELEASE one — enforced here, in the library write
function, not only at the CLI boundary, mirroring `refutations.ratify`'s own
channel-authority gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from . import channels, config, normalize, policy, redact, schema, store

VERSION = 1
EVENTS = frozenset({"quarantined", "confirmed", "dismissed", "released"})
STATES = frozenset({"candidate", "active", "dismissed", "released"})

# Only the shared four channels: an agent proposes, a human decides. No
# `mechanical` tier (there is no automatic quarantine signal, by design —
# the whole point is that only a human verdict withholds) and no importer
# channels (`serializer`/`lab-import` belong to `relations.py` alone).
CHANNEL_AUTHORITY = dict(channels.BASE_CHANNEL_AUTHORITY)
CHANNELS = frozenset(CHANNEL_AUTHORITY)
CHANNEL_LABEL = channels.CHANNEL_LABEL

# The recall-index "kind" vocabulary (`schema.ItemField.kind`), reused rather
# than restated: a quarantine's `(kind, scope_slug)` scoping exists so a
# belief and a decision that happen to read the same text after
# canonicalization do not collide (design §2).
KINDS = frozenset(field.kind for field in schema.ITEM_FIELDS)

_EVENT_RANK = {
    # A same-order tie fails toward the human verdict applying last, the
    # same posture `amendments.py` holds.
    "quarantined": 0,
    "confirmed": 1,
    "dismissed": 2,
    "released": 2,
}
_TRUST_ID_RE = re.compile(r"tr-[0-9a-f]{12}")
# Everything policy.stamp_item_ids can mint: prefix letter per list key,
# width ladder 12/16/24/40 plus the legacy 6-hex era, and the `-{n}`
# identical-text twin counter (mirrors relations.py/amendments.py).
_ITEM_ID_RE = re.compile(r"[orsuc]-[0-9a-f]{6,40}(?:-\d+)?")
# #928-style typed evidence sources, mirroring refutations._evidence's own
# vocabulary — a quarantine is a serious human verdict and deserves the same
# "cited, not verified" evidence discipline a ruling does.
_EVIDENCE_KINDS = frozenset({
    "message", "transcript", "artifact", "issue", "measurement", "url",
    "receipt",
})
_SPACE_RE = re.compile(r"\s+")
_MAX_TEXT = 2000
_MAX_EVIDENCE = 24
# A short, generic value ("done", "yes") canonicalizes identically across
# many unrelated items; refusing to quarantine anything under this floor is
# not present in `forget` (which suppresses passively) but is worth adding
# here, since a quarantine is a WRITE a human deliberately chose (design §2).
_MIN_VALUE_TEXT = 20

# Every field of a ledger row that can hold plaintext (#645 discipline, same
# declaration shape as refutations.py/amendments.py): one list, two
# consumers — the deleter below and a future privacy auditor. `value_key` is
# deliberately absent: it is a hash, never the text itself.
_PLAINTEXT_FIELDS = ("reason",)
_PLAINTEXT_LISTS = ("evidence",)


class TrustError(ValueError):
    """A requested ledger transition is invalid or cannot be persisted."""


class TrustTooLong(TrustError):
    """The over-cap branch of `_text`, and ONLY that branch — a required-but-
    empty field stays a plain `TrustError` (mirrors refutations.py's
    `RefutationTooLong`)."""

    def __init__(self, message: str, *, field: str, limit: int):
        super().__init__(message)
        self.field = field
        self.limit = limit


def _path(project_dir=None):
    # #948: resolve BEFORE slugging, the same way every other ledger here
    # does — without this a host calling in with "<repo>/plugin" writes a
    # bucket the CLI's read of the same path never looks at.
    slug = store.project_slug(config.resolve_project_dir(project_dir))
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "trust.jsonl"


def _text(name: str, value, *, required: bool = True) -> str:
    out = _SPACE_RE.sub(" ", str(value or "")).strip()
    if required and not out:
        raise TrustError(f"{name} is required")
    if len(out) > _MAX_TEXT:
        raise TrustTooLong(
            f"{name} is too long ({len(out)} > {_MAX_TEXT} characters)",
            field=name, limit=_MAX_TEXT)
    return out


def _evidence(values) -> list[str]:
    out = []
    seen = set()
    for raw in values or []:
        value = _text("evidence", raw)
        kind, separator, payload = value.partition(":")
        if (not separator or kind.casefold() not in _EVIDENCE_KINDS
                or not payload.strip()):
            raise TrustError(
                f"invalid evidence source {value!r}; use a typed source such "
                "as message:<id>, transcript:<session>, artifact:<path>, "
                "issue:<number>, measurement:<receipt>, receipt:<id>, or "
                "url:<source>")
        if value not in seen:
            seen.add(value)
            out.append(value)
    if not out:
        raise TrustError(
            "at least one --evidence source is required; name the "
            "measurement, artifact, issue, or transcript span behind the "
            "quarantine")
    if len(out) > _MAX_EVIDENCE:
        raise TrustError(
            f"too many evidence sources ({len(out)} > {_MAX_EVIDENCE})")
    return out


def value_key(text) -> str:
    """The canonical identity a quarantine targets.

    The SAME `normalize.content_key` algorithm `store.forgotten_content_keys`
    uses for forget's tombstones, so a carried or re-extracted copy of the
    same value stays quarantined regardless of which item id currently holds
    it (design §2) — reusing a proven mechanism rather than inventing one.
    Redacted and whitespace-normalized first, so a secret-shaped literal
    inside the quoted value does not itself become the persisted identity,
    and so trivial formatting differences do not mint two ids for one value.
    """
    clean, _ = redact.redact_text(str(text or ""))
    stripped = _SPACE_RE.sub(" ", clean).strip()
    if len(stripped) < _MIN_VALUE_TEXT:
        raise TrustError(
            f"quarantine text is too short ({len(stripped)} < "
            f"{_MIN_VALUE_TEXT} characters); a short, generic value risks "
            "quarantining unrelated items that canonicalize the same")
    return normalize.content_key(stripped)


def make_id(kind: str, scope_slug: str, key: str) -> str:
    """Stable id from `(kind, scope_slug, value_key)` — scoped (design §2)
    so two textually-identical items in different kinds, or different
    projects, never collide."""
    raw = f"{kind}\0{scope_slug}\0{key}"
    return "tr-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _stamp(event: str, quarantine_id: str, channel: str,
           *, now_ns: int | None = None, event_id: str | None = None) -> dict:
    if event not in EVENTS:
        raise TrustError(f"unknown trust event: {event}")
    if not _TRUST_ID_RE.fullmatch(str(quarantine_id or "")):
        raise TrustError(f"invalid quarantine id: {quarantine_id!r}")
    if channel not in CHANNELS:
        raise TrustError(
            f"channel must be one of: {', '.join(sorted(CHANNELS))}")
    # Derived, never accepted: no way to name one channel and claim
    # another's authority.
    authority = CHANNEL_AUTHORITY[channel]
    order = time.time_ns() if now_ns is None else int(now_ns)
    ts = datetime.fromtimestamp(order / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    return {
        "version": VERSION,
        "ts": ts,
        "order": order,
        "event_id": event_id or uuid.uuid4().hex,
        "event": event,
        "quarantine_id": quarantine_id,
        "channel": channel,
        "authority": authority,
        "author": config.author(),
    }


def _is_torn(path) -> bool:
    """True when the last append died before writing its terminator."""
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def append(row: dict, project_dir=None) -> bool:
    """Append one admitted lifecycle row. Never mutates another ledger."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    admitted = policy.admit_row(row, redact_fields=("reason", "author"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        store.record_bucket_root(project_dir)  # #1092: first writer wins
        with path.open("a", encoding="utf-8") as handle:
            if _is_torn(path):
                handle.write("\n")
            handle.write(json.dumps(admitted, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def events(project_dir=None) -> list[dict]:
    """Read valid ledger rows best-effort; malformed lines never sink reads."""
    path = _path(project_dir)
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError):
        return []
    rows = []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (not isinstance(row, dict)
                or row.get("event") not in EVENTS
                or not _TRUST_ID_RE.fullmatch(
                    str(row.get("quarantine_id") or ""))):
            continue
        # A hand-edited scalar `evidence` would raise inside the fold; a
        # malformed list field is a malformed ROW, dropped here like a bad
        # `event` or id (mirrors refutations.py's #970 fix).
        if row.get("evidence") is not None and not isinstance(
                row.get("evidence"), list):
            continue
        copy = dict(row)
        copy["_line"] = index
        rows.append(copy)
    return rows


def fold(rows: list[dict]) -> dict[str, dict]:
    """Fold lifecycle events into current records, deterministic under
    reorder."""
    def _integer(row, key, default=0):
        try:
            return int(row.get(key) or default)
        except (TypeError, ValueError):
            return default

    ordered = sorted(rows, key=lambda row: (
        _integer(row, "order"),
        _EVENT_RANK.get(str(row.get("event") or ""), 99),
        str(row.get("event_id") or ""),
        _integer(row, "_line"),
    ))
    out: dict[str, dict] = {}
    for row in ordered:
        tid = row["quarantine_id"]
        event = row["event"]
        current = out.get(tid)
        if event == "quarantined":
            # Read-boundary vocabulary check — a hand-edited `kind` must not
            # ride into the fold.
            if str(row.get("kind") or "") not in KINDS:
                continue
            # A dismissed or released record may be reopened by a fresh
            # proposal (same doctrine amendments.py holds for `rejected`):
            # neither verdict is a permanent lock on the (kind, scope_slug,
            # value) identity. Any other state is a duplicate, first writer
            # wins.
            if (current is not None
                    and current["state"] not in ("dismissed", "released")):
                continue
            human = (CHANNEL_AUTHORITY.get(str(row.get("channel") or ""))
                     == "human")
            state = "active" if (row.get("ratified") is True and human) \
                else "candidate"
            out[tid] = {
                "quarantine_id": tid,
                "state": state,
                "value_key": str(row.get("value_key") or ""),
                "item_id": str(row.get("item_id") or ""),
                "kind": str(row.get("kind") or ""),
                "scope_slug": str(row.get("scope_slug") or ""),
                "reason": str(row.get("reason") or ""),
                "evidence": list(row.get("evidence") or []),
                "proposed_by": row.get("authority"),
                "proposed_channel": row.get("channel"),
                "proposed_author": row.get("author"),
                "activation": (CHANNEL_LABEL.get(str(row.get("channel") or ""))
                              if state == "active" else None),
                "activation_channel": (row.get("channel")
                                       if state == "active" else None),
                "activation_author": (row.get("author")
                                      if state == "active" else None),
                "activated_at": row.get("ts") if state == "active" else None,
                # Reopening a dismissed/released record keeps the original
                # creation stamp and extends its history, same as
                # amendments.py's reopened-rejected posture.
                "created_at": (current["created_at"] if current is not None
                               else row.get("ts")),
                "updated_at": row.get("ts"),
                "history_count": (current["history_count"] + 1
                                  if current is not None else 1),
            }
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        authority = CHANNEL_AUTHORITY.get(str(row.get("channel") or ""))
        if authority != "human":
            continue  # no agent channel moves state, whatever the event says
        if event == "confirmed" and current["state"] == "candidate":
            current["state"] = "active"
            current["activation"] = CHANNEL_LABEL.get(
                str(row.get("channel") or ""))
            current["activation_channel"] = row.get("channel")
            current["activation_author"] = row.get("author")
            current["activated_at"] = row.get("ts")
        elif event == "dismissed" and current["state"] == "candidate":
            current["state"] = "dismissed"
        elif event == "released" and current["state"] == "active":
            current["state"] = "released"
    return out


def records(project_dir=None) -> dict[str, dict]:
    return fold(events(project_dir=project_dir))


def get(quarantine_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(quarantine_id)


def active_value_keys(project_dir=None) -> set[tuple[str, str]]:
    """Every `(kind, value_key)` pair currently under an ACTIVE quarantine.

    Scoped by kind, not a bare set of value keys: a decision and a belief
    that happen to canonicalize identically are two distinct quarantines
    (design §2), and collapsing them into one flat key set here would let
    quarantining one silently withhold the other once something reads this.
    Exported now, unread by anything in this PR, so the one read a future
    withholding pass (PR 2: briefing/recall/MCP/carry/`daimon_ui`) needs can
    be added without touching this module's fold again."""
    return {(record["kind"], record["value_key"])
            for record in records(project_dir=project_dir).values()
            if record["state"] == "active" and record.get("value_key")}


def propose(*, text, kind: str, reason, evidence, channel: str,
            item_id: str = "", project_dir=None,
            now_ns: int | None = None) -> str:
    """Open a quarantine. Any channel may propose; a human channel lands it
    active immediately (mirrors `amendments.propose`'s direct-ratify
    posture, and `refutations`'s `ruled` immediate activation)."""
    if kind not in KINDS:
        raise TrustError(f"kind must be one of: {', '.join(sorted(KINDS))}")
    if item_id and not _ITEM_ID_RE.fullmatch(str(item_id)):
        raise TrustError(f"invalid item id: {item_id!r}")
    scope_slug = store.project_slug(config.resolve_project_dir(project_dir))
    if not scope_slug:
        raise TrustError(
            "project is unknown; a quarantine needs a resolvable project")
    key = value_key(text)
    reason = _text("reason", reason)
    evidence = _evidence(evidence)
    tid = make_id(kind, scope_slug, key)
    existing = get(tid, project_dir=project_dir)
    if existing is not None and existing["state"] not in (
            "dismissed", "released"):
        raise TrustError(
            f"{tid} already exists for this value and kind "
            f"({existing['state']}); `daimon trust confirm {tid}`, "
            f"`daimon trust dismiss {tid}`, or `daimon trust release {tid}`")
    human = CHANNEL_AUTHORITY.get(channel) == "human"
    row = _stamp("quarantined", tid, channel, now_ns=now_ns)
    row.update({
        "value_key": key,
        "item_id": str(item_id or ""),
        "kind": kind,
        "scope_slug": scope_slug,
        "reason": reason,
        "evidence": evidence,
    })
    if human:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise TrustError(
            "quarantine not written (daimon disabled, project unknown, or "
            "ledger unwritable)")
    return tid


def _human_transition(event: str, quarantine_id: str, channel: str,
                      project_dir, now_ns: int | None) -> None:
    """Every state-moving verb funnels through here, so the human-channel
    gate lives in exactly one place — mirrors `relations._human_transition`
    and `refutations.ratify`'s own fold-level check."""
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise TrustError(
            f"{event} requires a human channel; this call arrived through "
            f"{channel!r}")
    current = get(quarantine_id, project_dir=project_dir)
    if current is None:
        raise TrustError(f"unknown quarantine: {quarantine_id}")
    if event == "confirmed" and current["state"] != "candidate":
        raise TrustError(
            f"{quarantine_id} is {current['state']}; only a candidate "
            "quarantine can be confirmed")
    if event == "dismissed" and current["state"] != "candidate":
        raise TrustError(
            f"{quarantine_id} is {current['state']}; only a candidate "
            "quarantine can be dismissed — an active one is lifted with "
            "release")
    if event == "released" and current["state"] != "active":
        raise TrustError(
            f"{quarantine_id} is {current['state']}; only an active "
            "quarantine can be released")
    row = _stamp(event, quarantine_id, channel, now_ns=now_ns)
    if not append(row, project_dir=project_dir):
        raise TrustError(f"{event} not written")


def confirm(quarantine_id: str, *, channel: str, project_dir=None,
            now_ns: int | None = None) -> None:
    _human_transition("confirmed", quarantine_id, channel, project_dir,
                      now_ns)


def dismiss(quarantine_id: str, *, channel: str, project_dir=None,
            now_ns: int | None = None) -> None:
    _human_transition("dismissed", quarantine_id, channel, project_dir,
                      now_ns)


def release(quarantine_id: str, *, channel: str, project_dir=None,
            now_ns: int | None = None) -> None:
    _human_transition("released", quarantine_id, channel, project_dir,
                      now_ns)


def plaintext_values(row: dict) -> list[str]:
    """Every scalar plaintext value this row carries (#645 discipline).

    The forget TARGETING pool reads this instead of hand-reading `reason`.
    `value_key` and `evidence` are absent: the former is a hash, and the
    latter is bounded typed tokens shared across records the same reasoning
    that keeps `refutations.py`'s `anchors`/`evidence` out of its own
    by-value menu (offering one by value would understate the deleter's
    reach)."""
    out: list[str] = []
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def row_content_keys(row: dict) -> set[str]:
    """Canonical keys for every plaintext field this row carries (#645).

    The one reader of `_PLAINTEXT_FIELDS`/`_PLAINTEXT_LISTS`, so the deleter
    below and a future privacy auditor cannot drift apart about what counts
    as plaintext on this surface."""
    out: set[str] = set()
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.add(normalize.content_key(value))
    for field in _PLAINTEXT_LISTS:
        values = row.get(field)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str) and value.strip():
                    out.add(normalize.content_key(value))
    return out


def forget_content_key(content_key: str, *, project_dir=None) -> list[str]:
    """Remove every record holding `content_key` in a plaintext field.

    This reaches the ledger's OWN prose (`reason`/`evidence`) — the same
    whole-value canonical match every other plaintext ledger here uses. It
    is deliberately NOT how a quarantined VALUE's own tombstone interacts
    with this ledger (design §5: closing out a quarantine whose target was
    separately forgotten is read-side reasoning for PR 2, once something
    reads `active_value_keys`); this function only ever removes rows whose
    `reason`/`evidence` text matches, never rows by `value_key`.

    Raw LINES, never `events()` output — that reader is deliberately
    tolerant and would silently delete rows a future daimon added (the scar
    0025/0042 shape: a forgiving read feeding a write). Atomic or nothing."""
    path = _path(project_dir)
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    doomed: set[str] = set()
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if content_key in row_content_keys(row):
            tid = str(row.get("quarantine_id") or "")
            if _TRUST_ID_RE.fullmatch(tid):
                doomed.add(tid)
    if not doomed:
        return []
    kept: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue  # torn rows are expendable; keeping one leaves bytes
        if (isinstance(row, dict)
                and str(row.get("quarantine_id") or "") in doomed):
            continue
        kept.append(line)
    tmp = path.with_name(path.name + ".forget-tmp")
    try:
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return []
    return sorted(doomed)
