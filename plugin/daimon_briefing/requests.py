"""Cross-project request ledger — one project's ask of another (#694).

A request is a record in the SENDER's bucket: "project X asks project Y to
do a thing, and here is why". The recipient never writes here. It discovers
the ask by READ-THROUGH at brief time and answers it with verdict rows in
its OWN `requests.jsonl`, referencing the request id — so every logical
request spans two buckets by construction and the joined record is a
read-time join (PR 2). Nobody writes a foreign ledger, and deletion happens
once at the source: read-through has no copies to chase.

This module is the object and its per-bucket fold. It follows the
refutations/amendments shape rather than events.jsonl: its own append-only
stream (scar 0025 — events.jsonl folds latest-wins per ref and its `source`
is a caller's claim about itself), an OBSERVED channel recorded on every
row, a deterministic full-pass fold, and rewrite deletion that reaches the
bytes.

Authority is asymmetric and the wedge is the point: any channel may ask,
revise, or report completion, but a VERDICT — accept, reject, needs-info —
is human-only, enforced at the write boundary AND re-checked in the fold.
`suppressed` is human-only for the same reason: an agent that could mute an
addressed ask from its own project's attention would have a soft-reject with
no record. Suppression is panel attention only — the record stays visible in
`request list` — and any later verdict lands normally and reverses it.

Rejection is sticky per id: a human verdict may never be buried by a later
sender event. A sender who still needs the thing opens a NEW request citing
`supersedes`, so "asking again" is an append-only fact with visible lineage
rather than a rewritten record. Revision is capped at three per record
lifetime for the same reason `_MAX_OPEN_PROPOSALS` caps ruling proposals:
without it, revise is a nag loop the recipient cannot stop.

`stale` is DERIVED at render time (PR 3), never written. No field may carry
another project's item text: the prose here is the ask, its rationale, a
human verdict note, and a completion quote — all length-capped, all
reachable by forget by value.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from . import config, normalize, policy, redact, store
# One channel doctrine for every ledger: authority is a property of the WRITE
# PATH, never a caller's claim about itself. Importing the table keeps a
# future channel tier ("ui", "signed") consistent across ledgers instead of
# forking per module.
from .refutations import CHANNEL_AUTHORITY, CHANNEL_LABEL

VERSION = 1
# The closed event vocabulary, frozen in PR 1 so the format cannot drift as
# the arc lands: `surfaced` (the recipient's brief stamped this ask) and
# `verdict_surfaced` (the sender's brief stamped the answer) are written by
# the inbox and return-path PRs, but their fold handling ships here — an
# older reader that dropped them would silently re-render decided work.
EVENTS = frozenset({
    "opened", "revised",
    "surfaced", "verdict_surfaced",
    "needs_info", "accepted", "rejected", "done",
    "suppressed",
})
# What a folded record may render as. `stale` is derived from consumption
# (PR 3's baton count), never appended — an expiry that writes a row would
# make attention decay indistinguishable from a verdict. `suppressed` is
# deliberately NOT here: it is a marker on a record that still has a state.
RENDER_STATES = frozenset({
    "open", "needs-info", "accepted", "rejected", "done", "stale",
})
_STATE_BY_EVENT = {
    "needs_info": "needs-info",
    "accepted": "accepted",
    "rejected": "rejected",
    "done": "done",
}
# Verdicts and suppression: the human-only half of the verb table (D8).
_HUMAN_ONLY = frozenset({"needs_info", "accepted", "rejected", "suppressed"})
# States a sender-side event may still move. Everything else is settled by a
# human verdict or by a completion claim, and re-opening it from the sender
# side would be exactly the assertion the wedge principle forbids.
_SENDER_MOVABLE = frozenset({"open", "needs-info"})
# Per-brief render budget (D2). Over-cap renders a loud "+N more waiting"
# line in PR 2; silent truncation of addressed asks is the one forbidden
# failure, so the cap lives beside the overflow count it produces.
RENDER_CAP = 3
# Revisions per record lifetime (D7), fold-enforced and CLI-refused. Out of
# revisions is not a dead end: D6's supersede opens a new record with the
# lineage attached.
MAX_REVISIONS = 3
_EVENT_RANK = {
    # Same-`order` ambiguity fails toward the human verdict that closes the
    # loop: a same-instant reject beats an accept, and both beat a
    # completion claim. Attention rows sort before verdicts so a verdict
    # landing in the same instant still reverses a suppression.
    "opened": 0,
    "revised": 1,
    "surfaced": 2,
    "verdict_surfaced": 2,
    "suppressed": 3,
    "done": 4,
    "needs_info": 5,
    "accepted": 6,
    "rejected": 7,
}
_REQUEST_ID_RE = re.compile(r"q-[0-9a-f]{12}")
# store.project_slug's output: every non-word char munged to '-'. Validated
# here for SHAPE only — whether the slug names a bucket that exists is the
# CLI's check (it can offer near-matches and an --anyway override; this
# module must stay writable for a project not yet initialized).
_SLUG_RE = re.compile(r"[\w-]{1,255}")
_SPACE_RE = re.compile(r"\s+")
_MAX_TEXT = 2000
_LABEL_MAX = 64

# Every field of a ledger row that can hold plaintext (#645 discipline).
# One declaration, two consumers: `forget_content_key` decides which records
# a deletion reaches, and `privacy.audit_project` decides which fields it
# hashes when proving the deletion happened.
#
# `author` is deliberately absent: it is a person's name, not item text, and
# matching a tombstone against it would let one forgotten value delete every
# record a given author ever wrote. `to` is absent for the same reason in
# reverse — it is a filesystem-derived slug, not authored prose.
_PLAINTEXT_FIELDS = ("ask", "why", "note", "evidence", "from_label")


class RequestError(ValueError):
    """A requested ledger transition is invalid or cannot be persisted."""


def _path(project_dir=None):
    slug = store.project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "requests.jsonl"


def _text(name: str, value, *, required: bool = True,
          limit: int = _MAX_TEXT) -> str:
    out = _SPACE_RE.sub(" ", str(value or "")).strip()
    if required and not out:
        raise RequestError(f"{name} is required")
    if len(out) > limit:
        raise RequestError(
            f"{name} is too long ({len(out)} > {limit} characters)")
    return out


def _scrub(name: str, value, *, required: bool = True,
           limit: int = _MAX_TEXT) -> str:
    """Cap, redact, then cap AGAIN — placeholder expansion can grow the
    persisted bytes past what the raw text passed, and the bytes that
    persist are the ones the cap exists to bound."""
    out = _text(name, value, required=required, limit=limit)
    out, _ = redact.redact_text(out)
    return _text(name, out, required=required, limit=limit)


def _sender_label(project_dir) -> str:
    """The sender's own display label (D4): the basename of its directory,
    captured at WRITE time because `store.project_slug` is irreversible — a
    recipient reading the joined record cannot recover a readable name from
    the slug, and daimon has no registry to look one up in."""
    base = os.path.basename(str(project_dir or "").rstrip("/\\"))
    return _scrub("from_label", base, required=False, limit=_LABEL_MAX)


def make_id(sender_slug: str, ask: str, why: str, opened_ts: str) -> str:
    """Stable logical id from the sender + the redacted ask identity + time.

    The slug is in the hash because the inbox joins rows from every bucket on
    id alone: two projects sending textually identical asks must never mint
    the same id. `opened_ts` is in it because a deliberate re-ask is a NEW
    record, not a collision with the one already answered.
    """
    raw = (f"{sender_slug}\0{ask.casefold()}\0{why.casefold()}\0"
           f"{opened_ts}")
    return "q-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _ts(order: int) -> str:
    return datetime.fromtimestamp(order / 1_000_000_000, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _stamp(event: str, request_id: str, channel: str,
           *, now_ns: int | None = None, event_id: str | None = None) -> dict:
    if event not in EVENTS:
        raise RequestError(f"unknown request event: {event}")
    if not _REQUEST_ID_RE.fullmatch(str(request_id or "")):
        raise RequestError(f"invalid request id: {request_id!r}")
    if channel not in CHANNEL_AUTHORITY:
        raise RequestError(
            f"channel must be one of: {', '.join(sorted(CHANNEL_AUTHORITY))}")
    # Derived, never accepted: no way to name one channel and claim another's
    # authority.
    authority = CHANNEL_AUTHORITY[channel]
    order = time.time_ns() if now_ns is None else int(now_ns)
    return {
        "version": VERSION,
        "ts": _ts(order),
        "order": order,
        "event_id": event_id or uuid.uuid4().hex,
        "event": event,
        "request_id": request_id,
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
    """Append one admitted lifecycle row. Never mutates another bucket."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    admitted = policy.admit_row(
        row, redact_fields=("ask", "why", "note", "evidence", "from_label",
                            "author"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    if path is None or not path.exists():
        return []
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if (not isinstance(row, dict)
                or row.get("event") not in EVENTS
                or not _REQUEST_ID_RE.fullmatch(str(row.get("request_id") or ""))):
            continue
        copy = dict(row)
        copy["_line"] = index
        rows.append(copy)
    return rows


def fold(rows: list[dict]) -> dict[str, dict]:
    """Fold this bucket's rows into current records, deterministic under
    reorder.

    Full-pass, never latest-wins: the ask, its revisions, and the verdict are
    all witness data, and a later event must not erase the record of an
    earlier one — the stream keeps every row; the fold states what holds NOW.

    Per-bucket only. The cross-bucket join (orphan rule, supersede lineage,
    suppress filter) is the read-time composer PR 2 builds ON TOP of this;
    here a lifecycle row whose `opened` lives in another bucket is an orphan:
    inert in the fold, visible in the raw audit.
    """
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
        q_id = row["request_id"]
        event = row["event"]
        authority = CHANNEL_AUTHORITY.get(row.get("channel"))
        current = out.get(q_id)
        if event == "opened":
            if current is not None:
                continue  # duplicate logical open, first writer wins
            # Read-boundary shape check (the write boundary is not the
            # boundary that matters — events() is deliberately tolerant, and
            # a row edited on disk must not ride into the render).
            if not _SLUG_RE.fullmatch(str(row.get("to") or "")):
                continue
            if not str(row.get("ask") or "").strip():
                continue
            out[q_id] = {
                "request_id": q_id,
                "state": "open",
                "to": str(row.get("to") or ""),
                "to_human": row.get("to_human") is True,
                "blocking": row.get("blocking") is True,
                "ask": str(row.get("ask") or ""),
                "why": str(row.get("why") or ""),
                "evidence": str(row.get("evidence") or ""),
                "from_label": str(row.get("from_label") or ""),
                "supersedes": str(row.get("supersedes") or ""),
                "note": "",
                "opened_by": authority,
                "opened_channel": row.get("channel"),
                "opened_author": row.get("author"),
                "verdict_by": None,
                "verdict_label": None,
                "verdict_at": None,
                "done_by": None,
                "done_claimed": False,
                "done_evidence": "",
                "suppressed": False,
                # {revision epoch: earliest surfaced ts} — D1's write-once
                # dedup key, consulted by the stamp PR 2 adds.
                "surfaced": {},
                "verdict_surfaced_at": None,
                "revision": 0,
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "history_count": 1,
            }
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert here
        if event in _HUMAN_ONLY and authority != "human":
            # The fold re-check the write boundary cannot be trusted for: a
            # row appended off-path or edited on disk claiming an agent
            # channel is fully inert, verdict or suppression alike.
            continue
        if event in _STATE_BY_EVENT and current["state"] == "rejected":
            # D6: rejection is terminal for this id. Re-proposal is a NEW
            # record citing `supersedes`, so the rejected verdict stays on
            # the record instead of being overwritten by the next ask.
            continue
        if event == "surfaced":
            # Attention rows never move the record's rendered age: a brief
            # that merely showed the card must not make an untouched ask
            # sort as freshly updated. Earliest row of an epoch wins — the
            # ordered pass means the first one seen for that revision is it.
            current["history_count"] += 1
            current["surfaced"].setdefault(current["revision"], row.get("ts"))
            continue
        if event == "verdict_surfaced":
            current["history_count"] += 1
            if current["verdict_surfaced_at"] is None:
                current["verdict_surfaced_at"] = row.get("ts")
            continue
        if event == "suppressed":
            current["history_count"] += 1
            current["suppressed"] = True
            continue
        if event == "revised":
            if current["state"] not in _SENDER_MOVABLE:
                continue
            if current["revision"] >= MAX_REVISIONS:
                continue  # D7: revision 4+ is inert, not a silent success
            # Scar 0042: replace only the keys the caller actually set. In an
            # append-only stream the ABSENCE of a key is data, so a revise
            # that names only `why` must not clear the ask.
            for key in ("ask", "why", "evidence"):
                if key in row:
                    current[key] = str(row.get(key) or "")
            current["revision"] += 1
            current["state"] = "open"
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            continue
        if event == "done" and not str(row.get("evidence") or "").strip():
            # D8: `done` is the one either-channel state move, and its price
            # is evidence. A row without it never lands.
            continue
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        current["state"] = _STATE_BY_EVENT[event]
        # D5: a verdict (or a completion) supersedes a suppression — that IS
        # the reversal path, which is why no `unsuppress` verb exists.
        current["suppressed"] = False
        if event == "done":
            current["done_by"] = authority
            # An agent's completion claim renders as claimed-and-unverified
            # until the session-end byte-check confirms the quote (PR 3).
            current["done_claimed"] = authority != "human"
            current["done_evidence"] = str(row.get("evidence") or "")
        else:
            current["verdict_by"] = authority
            current["verdict_label"] = CHANNEL_LABEL.get(row.get("channel"))
            current["verdict_at"] = row.get("ts")
            if "note" in row:
                current["note"] = str(row.get("note") or "")
    return out


def records(project_dir=None) -> dict[str, dict]:
    return fold(events(project_dir=project_dir))


def get(request_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(request_id)


def listing(project_dir=None) -> list[dict]:
    """Every record in this bucket, undecided first — the `request list`
    order. Suppressed records are HERE by construction (D5): suppression
    takes away panel placement, never visibility."""
    return sorted(
        records(project_dir=project_dir).values(),
        key=lambda r: (r["state"] not in _SENDER_MOVABLE,
                       r.get("updated_at") or "", r["request_id"]))


def renderable(project_dir=None) -> dict:
    """The records still awaiting attention: {"rows": [...], "overflow": N}.

    Newest first, capped at RENDER_CAP with the remainder COUNTED — the
    panel PR 2 builds renders the overflow loudly, because silently dropping
    an addressed ask is the one failure this feature cannot have. Suppressed
    records are filtered here and only here: that is the whole effect of
    suppression, and it is why the verb had to be human-only.
    """
    rows = [r for r in records(project_dir=project_dir).values()
            if r["state"] in _SENDER_MOVABLE and not r["suppressed"]]
    rows.sort(key=lambda r: (r.get("updated_at") or "", r["request_id"]),
              reverse=True)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def open_request(*, to: str, ask: str, why: str, channel: str,
                 blocking: bool = False, to_human: bool = False,
                 evidence: str = "", supersedes: str = "",
                 project_dir=None) -> str:
    """Open a request addressed to another project's slug.

    Slug SHAPE is validated here; whether it names a bucket that exists is
    the CLI's check — a project that has not serialized yet has no bucket,
    and refusing to record the ask at the module seam would make the ledger
    unusable exactly when a team is onboarding.
    """
    if not _SLUG_RE.fullmatch(str(to or "")):
        raise RequestError(f"invalid recipient slug: {to!r}")
    supersedes = str(supersedes or "").strip()
    if supersedes and not _REQUEST_ID_RE.fullmatch(supersedes):
        raise RequestError(f"invalid superseded request id: {supersedes!r}")
    slug = store.project_slug(project_dir)
    if not slug:
        raise RequestError("project unknown; requests are recorded in the "
                           "sender's own bucket")
    ask = _scrub("ask", ask)
    why = _scrub("why", why)
    evidence = _scrub("evidence", evidence, required=False)
    order = time.time_ns()
    q_id = make_id(slug, ask, why, _ts(order))
    if get(q_id, project_dir=project_dir) is not None:
        raise RequestError(
            f"{q_id} already exists — this exact ask was opened in the same "
            "second; revise it or wait a moment to open a second one")
    row = _stamp("opened", q_id, channel, now_ns=order)
    row.update({
        "to": to,
        "to_human": bool(to_human),
        "blocking": bool(blocking),
        "ask": ask,
        "why": why,
        "from_label": _sender_label(project_dir),
    })
    if evidence:
        row["evidence"] = evidence
    if supersedes:
        row["supersedes"] = supersedes
    if not append(row, project_dir=project_dir):
        raise RequestError(
            "request not written (daimon disabled, project unknown, or "
            "ledger unwritable)")
    return q_id


def revise(request_id: str, *, channel: str, ask: str | None = None,
           why: str | None = None, evidence: str | None = None,
           project_dir=None) -> None:
    """Answer a needs-info, or sharpen an open ask. Capped at MAX_REVISIONS."""
    current = _require(request_id, project_dir)
    if current["state"] not in _SENDER_MOVABLE:
        raise RequestError(
            f"{request_id} is {current['state']}; a settled request is not "
            "revised — open a new one with --supersedes to ask again")
    if current["revision"] >= MAX_REVISIONS:
        raise RequestError(
            f"{request_id} has used all {MAX_REVISIONS} revisions; open a "
            f"new request with `--supersedes {request_id}` so the lineage "
            "stays visible")
    row = _stamp("revised", request_id, channel)
    # Scar 0042: only the keys the caller SET. `None` means unchanged, and
    # the fold reads key presence as intent — forging a key here would clear
    # the field the caller never mentioned.
    if ask is not None:
        row["ask"] = _scrub("ask", ask)
    if why is not None:
        row["why"] = _scrub("why", why)
    if evidence is not None:
        row["evidence"] = _scrub("evidence", evidence, required=False)
    if not any(key in row for key in ("ask", "why", "evidence")):
        raise RequestError(
            "revision changes nothing; provide a new ask, why, or evidence")
    if not append(row, project_dir=project_dir):
        raise RequestError("revision not written")


def _answering(request_id: str, project_dir) -> dict | None:
    """The local record a recipient-side row answers, or None when this
    bucket cannot see it.

    Shape-checked, never resolved. A request lives in the SENDER's bucket, so
    the recipient answering one has nothing local to resolve against: an
    answer to a foreign id is written here and stays an orphan — inert in
    this bucket's fold, visible in the raw audit — until the read-time join
    (PR 2) pairs it with its origin. Refusing what cannot be resolved would
    make the recipient side unimplementable.
    """
    if not _REQUEST_ID_RE.fullmatch(str(request_id or "")):
        raise RequestError(f"invalid request id: {request_id!r}")
    return get(request_id, project_dir=project_dir)


def _require(request_id: str, project_dir) -> dict:
    """The local record, or a refusal — for the SENDER-side verbs, which
    cannot act without one (a revision needs the ask it is replacing and the
    revision count it is bounded by)."""
    current = get(request_id, project_dir=project_dir)
    if current is None:
        raise RequestError(f"unknown request: {request_id}")
    return current


def _verdict(event: str, request_id: str, *, channel: str, note: str = "",
             project_dir=None) -> None:
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise RequestError(
            f"a {_STATE_BY_EVENT.get(event, event)} verdict requires a human "
            f"channel; this call arrived through {channel!r}")
    current = _answering(request_id, project_dir)
    if current is not None and current["state"] == "rejected":
        raise RequestError(
            f"{request_id} was rejected, and a rejection is final for that "
            "record; the sender can open a new request with "
            f"`--supersedes {request_id}`")
    row = _stamp(event, request_id, channel)
    note = _text("note", note, required=False)
    if note:
        row["note"] = note
    if not append(row, project_dir=project_dir):
        raise RequestError("verdict not written")


def accept(request_id: str, *, channel: str, note: str = "",
           project_dir=None) -> None:
    _verdict("accepted", request_id, channel=channel, note=note,
             project_dir=project_dir)


def reject(request_id: str, *, channel: str, note: str = "",
           project_dir=None) -> None:
    _verdict("rejected", request_id, channel=channel, note=note,
             project_dir=project_dir)


def needs_info(request_id: str, *, channel: str, note: str = "",
               project_dir=None) -> None:
    _verdict("needs_info", request_id, channel=channel, note=note,
             project_dir=project_dir)


def suppress(request_id: str, *, channel: str, note: str = "",
             project_dir=None) -> None:
    """Drop a record out of the briefing panel — human-only, panel-only.

    Not a verdict and not a state: the record stays in `request list`, and
    any later accept/reject/needs-info lands normally and reverses this.
    Human-only because an agent able to mute an addressed ask would hold a
    soft-reject that never appears as one.
    """
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise RequestError(
            "suppressing an addressed request requires a human channel; this "
            f"call arrived through {channel!r}")
    _answering(request_id, project_dir)
    row = _stamp("suppressed", request_id, channel)
    note = _text("note", note, required=False)
    if note:
        row["note"] = note
    if not append(row, project_dir=project_dir):
        raise RequestError("suppression not written")


def done(request_id: str, *, channel: str, evidence: str,
         project_dir=None) -> None:
    """Report the ask as satisfied. Either channel, evidence required — an
    agent's claim renders as claimed-and-unverified until the session-end
    byte-check confirms the quote (PR 3)."""
    current = _answering(request_id, project_dir)
    if current is not None and current["state"] == "rejected":
        raise RequestError(
            f"{request_id} was rejected; a rejected request cannot be "
            "completed")
    row = _stamp("done", request_id, channel)
    row["evidence"] = _scrub("evidence", evidence)
    if not append(row, project_dir=project_dir):
        raise RequestError("completion not written")


def plaintext_values(row: dict) -> list[str]:
    """Every plaintext value this row carries, in declaration order.

    The forget TARGETING pool reads this instead of hand-copying the field
    tuple — the same one-declaration discipline row_content_keys below gives
    the deleter and the auditor."""
    out: list[str] = []
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def row_content_keys(row: dict) -> set[str]:
    """Canonical keys for every plaintext field this row carries (#645).

    The one reader of _PLAINTEXT_FIELDS besides plaintext_values, so the
    deleter below and `privacy.audit_project` cannot drift apart about what
    counts as plaintext on this surface."""
    out: set[str] = set()
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.add(normalize.content_key(value))
    return out


def _rewrite_without(doomed: set[str], project_dir=None) -> list[str]:
    """Rewrite the ledger dropping every row of the doomed record ids.

    Raw LINES, never `events()` output — that reader is tolerant and would
    silently delete rows a future daimon added (the scar 0025/0042 shape: a
    forgiving read feeding a write). Atomic or nothing."""
    doomed.discard("")
    if not doomed:
        return []
    path = _path(project_dir)
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
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
                and str(row.get("request_id") or "") in doomed):
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


def forget_content_key(content_key: str, *, project_dir=None) -> list[str]:
    """Remove every record holding `content_key` in a plaintext field.

    The rewrite path exists because this ledger carries plaintext by design
    (the ask, its rationale, verdict notes, completion quotes), so it is in
    the checkpoint's deletion category — a removal must reach the bytes
    (#578 posture). Matches on the same canonical whole-value key the
    checkpoint splice uses, never on substring containment. Every row of a
    matched record goes, including verdict rows written here by this side.

    Deletion is local by design and that is the whole point of read-through:
    the sender forgetting its ask removes it from every reader at once,
    because no reader ever held a copy. Rows THIS bucket wrote about a
    foreign request are this bucket's own prose and go the same way; the
    foreign origin rows are the other side's to delete.
    """
    doomed = {
        str(row.get("request_id") or "")
        for row in events(project_dir=project_dir)
        if content_key in row_content_keys(row)
    }
    return _rewrite_without(doomed, project_dir=project_dir)
