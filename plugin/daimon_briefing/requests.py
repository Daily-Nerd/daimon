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
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping, NamedTuple

from . import (buckets, clock, config, display, jsonl, normalize, policy,
               redact, refutations, scope, store, surfaces)
# One channel doctrine for every ledger: authority is a property of the WRITE
# PATH, never a caller's claim about itself. `authority.py` owns the tier a
# channel carries, so a future channel tier ("ui", "signed") stays consistent
# across ledgers instead of forking per module.
from .authority import channels as known_channels
from .authority import of_channel, of_row
from .refutations import CHANNEL_LABEL
from .surfaces import Writer

VERSION = 1
# #961: the approval-requirement a request carries. `info` is answerable from
# the recipient's own artifacts and owes no accept; `work` asks the recipient
# to change something or spend effort and waits on a person. Two values,
# never a free string — the same discrete-enum posture the channel table and
# EVENTS already hold this module to.
KINDS = frozenset({"info", "work"})
# The fold's fallback for a row that carries no readable `kind` at all — every
# request minted before this field existed, and any other unreadable value.
# Nothing is reclassified by the field's absence (the amendment's own words),
# and the unreadable case defaults toward MORE scrutiny, never less.
DEFAULT_KIND = "work"
# The closed event vocabulary, frozen in PR 1 so the format cannot drift as
# the arc lands: `surfaced` (the recipient's brief stamped this ask) and
# `verdict_surfaced` (the sender's brief stamped the answer) are written by
# the inbox and return-path PRs, but their fold handling ships here — an
# older reader that dropped them would silently re-render decided work.
#
# #694 PR 3 widens it ONCE, deliberately, with `done_verified` — the
# session-end byte-check confirming an agent's `done` evidence quote against
# the transcript. The format is frozen PER-PR, not for the whole arc (the
# design's own implementation note): an older reader drops the unknown row
# and keeps rendering "done (claimed, unverified)" — the safe direction.
#
# #756 widens it a second time, on the same terms, with `delivered`: the
# live-delivery render stamped this ask into a running session. Same safe
# direction on an older reader — the row drops, the record reads as
# undelivered, and the worst case is a repeated nudge rather than a
# swallowed ask. It is an attention row like `surfaced`, never a state.
# #801 widens it a third time, on the same terms, with `verdict_delivered`:
# the live-delivery render stamped this VERDICT into a running SENDER session.
# Same safe direction on an older reader — the row drops, the record reads as
# undelivered, and the worst case is a repeated nudge rather than a swallowed
# verdict. An attention row like `surfaced`, never a state.
# #1117 widens it a fifth time with `replied` (progress note): an older
# reader drops the row and nothing else changes. Its guards live in the fold.
#
# #1346 widens it a sixth and seventh time: `withdrawn` (the sender takes its
# own ask back) and `withdrawn_delivered` (the live stamp that told the
# recipient once). An older reader drops both rows: the withdrawn ask reads as
# still open there, the direction every earlier widening already fails in.
#
# #1348 widens it an eighth time with `severity_set` (the sender changes how
# much a request matters). An older reader drops the row and the request keeps
# the severity it had: attention order only, the same safe direction.
EVENTS = frozenset({
    "opened", "revised", "severity_set",
    "surfaced", "verdict_surfaced", "delivered", "verdict_delivered",
    "owed_delivered", "withdrawn_delivered",
    "needs_info", "accepted", "rejected", "done",
    "suppressed", "done_verified", "replied", "withdrawn",
})
# #1346: who may write each event, said ONCE. The fold applies an act only
# when its row sits in its role's bucket (`row_holds_role`), and the write
# path refuses the wrong side from the same table, so the two cannot drift.
#   sender     the project that opened the ask: opened, revised, withdrawn,
#              severity_set
#   recipient  the project it was sent to: the verdicts, `done`, `suppressed`
#   either     not an act on the ask: `replied` is labelled by origin (see
#              `_reply_from_sender`), and the stamps record what a surface
#              showed, from whichever bucket showed it
# A self-addressed ask is one project holding both roles. A new event without
# an entry here fails `test_every_event_has_a_role`.
ROLE_SENDER = "sender"
ROLE_RECIPIENT = "recipient"
ROLE_EITHER = "either"
EVENT_ROLE = {
    "opened": ROLE_SENDER, "revised": ROLE_SENDER, "withdrawn": ROLE_SENDER,
    "severity_set": ROLE_SENDER,
    "accepted": ROLE_RECIPIENT, "rejected": ROLE_RECIPIENT,
    "needs_info": ROLE_RECIPIENT, "done": ROLE_RECIPIENT,
    "suppressed": ROLE_RECIPIENT,
    "replied": ROLE_EITHER, "done_verified": ROLE_EITHER,
    "surfaced": ROLE_EITHER, "delivered": ROLE_EITHER,
    "owed_delivered": ROLE_EITHER, "withdrawn_delivered": ROLE_EITHER,
    "verdict_surfaced": ROLE_EITHER, "verdict_delivered": ROLE_EITHER,
}


def row_holds_role(role: str, *, origin: str, from_slug: str, to: str,
                   home_slugs: frozenset = frozenset()) -> bool:
    """Whether a row written in bucket `origin` may act in `role` on a request
    founded in `from_slug` and addressed `to`. `""` is the composer's own
    bucket on both sides, exactly as `_stamped` writes it, so a local founder
    (`from_slug == ""`) owns every local row and a foreign one owns the rows
    stamped with its slug.

    Self-addressed (the founder's bucket IS the recipient) holds both roles.
    For a local founder that bucket is one of `home_slugs`, the composer's own
    slug and aliases; for a foreign founder it is `from_slug` itself."""
    if role == ROLE_EITHER:
        return True
    from_sender = origin == from_slug
    if role == ROLE_SENDER:
        return from_sender
    if not from_sender:
        return True
    founder_slugs = ({from_slug} | buckets.aliases_for(from_slug)
                     if from_slug else home_slugs)
    return to in founder_slugs

# What a folded record may render as. `stale` is derived from consumption
# (PR 3's baton count), never appended — an expiry that writes a row would
# make attention decay indistinguishable from a verdict. `suppressed` is
# deliberately NOT here: it is a marker on a record that still has a state.
RENDER_STATES = frozenset({
    "open", "needs-info", "accepted", "rejected", "done", "stale",
    "withdrawn",
})
_STATE_BY_EVENT = {
    "needs_info": "needs-info",
    "accepted": "accepted",
    "rejected": "rejected",
    "done": "done",
}
# Verdicts and suppression: the human-only half of the verb table (D8).
_HUMAN_ONLY = frozenset({"needs_info", "accepted", "rejected", "suppressed"})

# #1333: what a row did, as the fold decided it. ONE closed set of effects, and
# every reason belongs to exactly one effect, so a branch of the fold picks the
# reason and the effect follows from the table: nothing downstream can name a
# fifth outcome, and no printer works one out from a state diff or a verb name.
#   landed         the row moved the record (or recorded a fact on it)
#   held_as_claim  an agent's completion, held until a person decides
#   ignored        a no-op: the row is real but changed nothing, and says why
#   discarded_claim  a `revised` row cleared a pending claim (kept, superseded)
EFFECT_LANDED = "landed"
EFFECT_HELD = "held_as_claim"
EFFECT_IGNORED = "ignored"
EFFECT_DISCARDED = "discarded_claim"
EFFECTS = frozenset({EFFECT_LANDED, EFFECT_HELD, EFFECT_IGNORED,
                     EFFECT_DISCARDED})
# reason -> (effect, the one line a verb prints). `{id}` and `{revision}` are
# the only holes; `effect_line` fills them from the record, never from state.
_EFFECT_REASONS: dict[str, tuple[str, str]] = {
    "opened": (EFFECT_LANDED, "{id}: request recorded"),
    "duplicate_open": (
        EFFECT_IGNORED, "{id}: no-op, a request with this id was already "
        "opened and the first one stands"),
    "revised": (EFFECT_LANDED, "{id}: revision recorded"),
    "claim_discarded": (
        EFFECT_DISCARDED,
        "{id}: revision recorded, and the completion claim that answered "
        "revision {revision} was discarded; it stays on the record as a "
        "superseded claim"),
    "not_revisable": (
        EFFECT_IGNORED, "{id}: no-op, a settled request is not revised"),
    "revision_cap": (
        EFFECT_IGNORED, "{id}: no-op, every revision is already used"),
    "needs_human_channel": (
        EFFECT_IGNORED, "{id}: no-op, this verdict needs a human channel "
        "and the row changed nothing"),
    "record_rejected": (
        EFFECT_IGNORED, "{id}: no-op, the request was rejected and a "
        "rejection is final; the sender can open a new one with "
        "--supersedes"),
    "already_done": (
        EFFECT_IGNORED, "{id}: no-op, the request is already done and a "
        "verdict does not reopen it"),
    "already_done_unverified": (
        EFFECT_IGNORED, "{id}: no-op, the request is already done and its "
        "completion is claimed, unverified; only the session-end byte-check "
        "clears that, a verdict does not"),
    "suppressed": (
        EFFECT_LANDED, "{id}: suppressed from the briefing panel; it stays "
        "listed, and any verdict reverses it"),
    "already_suppressed": (
        EFFECT_IGNORED, "{id}: no-op, it was already suppressed from the "
        "briefing panel"),
    "accepted": (EFFECT_LANDED, "{id}: accepted"),
    "claim_ratified": (
        EFFECT_LANDED, "{id}: accepted, which ratified the completion claim "
        "already on file; the request is done"),
    "rejected": (
        EFFECT_LANDED, "{id}: rejected; final for this request, and the "
        "sender can open a new one with --supersedes"),
    "needs_info": (
        EFFECT_LANDED, "{id}: needs-info recorded; the sender is asked for "
        "more"),
    "needs_info_again": (
        EFFECT_LANDED, "{id}: needs-info recorded again; the request was "
        "already waiting on the sender"),
    "done_needs_evidence": (
        EFFECT_IGNORED, "{id}: no-op, a completion without evidence never "
        "lands"),
    "awaits_person": (
        EFFECT_HELD, "{id}: completion claimed, and still waits on a person "
        "— `daimon request accept {id}` lands it as done, `daimon "
        "request reject {id} --note \"<why>\"` sends it back"),
    "done": (EFFECT_LANDED, "{id}: done"),
    "done_claimed": (
        EFFECT_LANDED, "{id}: done, claimed and unverified until the "
        "session-end byte-check confirms the evidence quote"),
    "claim_verified": (
        EFFECT_LANDED, "{id}: the session-end byte-check confirmed the "
        "completion quote"),
    "no_claim_to_verify": (
        EFFECT_IGNORED, "{id}: no-op, there is no completion claim for the "
        "byte-check to confirm"),
    "reply_recorded": (
        EFFECT_LANDED, "{id}: reply recorded; the request stays accepted "
        "and owed until `done`"),
    "reply_empty": (EFFECT_IGNORED, "{id}: no-op, a reply needs a note"),
    "reply_not_permitted": (
        EFFECT_IGNORED, "{id}: no-op, this channel cannot reply"),
    "reply_needs_accept": (
        EFFECT_IGNORED, "{id}: no-op, a reply only attaches to an accepted "
        "request"),
    "reply_from_sender": (
        EFFECT_IGNORED, "{id}: no-op, the sender cannot reply to its own "
        "ask; `daimon request revise` adds to it"),
    # #1346: the role table and the sender's retraction.
    "not_from_sender": (
        EFFECT_IGNORED, "{id}: no-op, only the sender can do this and the "
        "row was not written on the sender's side"),
    "not_from_recipient": (
        EFFECT_IGNORED, "{id}: no-op, this is the recipient's decision and "
        "the row was written on the sender's side; the sender takes its own "
        "ask back with `daimon request withdraw`"),
    "withdrawn": (
        EFFECT_LANDED, "{id}: withdrawn; final for this request, and the "
        "sender can open a new one with --supersedes"),
    "claim_withdrawn": (
        EFFECT_DISCARDED, "{id}: withdrawn, and the completion claim on file "
        "stays on the record as a superseded claim; final for this request, "
        "and the sender can open a new one with --supersedes"),
    "already_withdrawn": (
        EFFECT_IGNORED, "{id}: no-op, the request was already withdrawn"),
    "not_withdrawable": (
        EFFECT_IGNORED, "{id}: no-op, a finished request is not withdrawn: "
        "it is already done or rejected"),
    "record_withdrawn": (
        EFFECT_IGNORED, "{id}: no-op, the sender withdrew this request and "
        "a withdrawal is final; the sender can open a new one with "
        "--supersedes"),
    # #1348: the sender's severity.
    "severity_set": (EFFECT_LANDED, "{id}: severity recorded"),
    "severity_unreadable": (
        EFFECT_IGNORED, "{id}: no-op, the severity row names no level this "
        "build knows, or a level without its reason, and changed nothing"),
    "severity_settled": (
        EFFECT_IGNORED, "{id}: no-op, a settled request keeps the severity "
        "it had"),
}


def _note_effect(current: dict, row: dict, reason: str, **detail) -> None:
    """Record, on the record, what THIS row did (#1333). Called once per
    consumed row at the branch that decides its fate. `reason` names the
    branch; the effect is looked up, never passed, so a branch cannot claim
    an effect its reason does not belong to. A row without an `event_id` (a
    hand-edited one) has nothing to key by and is not recorded."""
    event_id = str(row.get("event_id") or "")
    if event_id:
        current["row_effects"][event_id] = {
            "effect": _EFFECT_REASONS[reason][0], "reason": reason, **detail}


def effect_line(request_id: str, effect: dict | None) -> str:
    """The one line a write verb prints for the row it wrote, from the effect
    the fold recorded for it (#1333). Empty when the fold recorded none (the
    row is an orphan in the fold that was asked). The words live HERE, beside
    the vocabulary, so no CLI branch can say something the fold did not."""
    if not effect:
        return ""
    template = _EFFECT_REASONS.get(str(effect.get("reason") or ""))
    if template is None:
        return ""
    return template[1].format(id=request_id,
                              revision=effect.get("revision", 0))
# #1026. Channels whose author is DERIVED from the environment this process
# runs in (`config.author()`: DAIMON_AUTHOR, then git user.name, then the OS
# user), so naming a different person per act is meaningless there — one
# terminal, one person. The per-act author is refused on these, which is what
# stops a shell-out from signing an act as somebody else: `cli-tty` carries
# human authority and is still refused, so the split here is not authority,
# it is whether the channel has an environment identity to derive from at all.
_CLI_CHANNELS = frozenset({"cli-agent", "cli-tty"})
# States a sender-side event may still move. Everything else is settled by a
# human verdict or by a completion claim, and re-opening it from the sender
# side would be exactly the assertion the wedge principle forbids.
_SENDER_MOVABLE = frozenset({"open", "needs-info"})
# States in which the SENDER is still owed something: an ask nobody answered,
# one sent back for more, one accepted and not yet done. `done`, `rejected`
# and `withdrawn` are settled for good. Distinct from `_SENDER_MOVABLE` (which
# excludes `accepted` on purpose, see its note) and from `is_stale`, which asks
# whether the RECIPIENT looked and did nothing. Two features ask this same
# question and read this one set: wall-clock overdue (#1281) and a severity
# change (#1348).
_SENDER_OWED = frozenset({"open", "needs-info", "accepted"})
# Receiver consent for `info` asks starts at this `order` (ns since the epoch,
# 2026-11-08T02:00:00Z). An agent verdict (`accepted`, `done`) on an `info`
# ask whose row is ordered BEFORE it keeps the state the old rule gave it, so
# refolding a ledger written earlier never changes a past verdict. From this
# order on the same rule as `work` applies: an agent's `accepted` lands only
# under a `verb=accept` ruling of the receiving project, and an agent's `done`
# on an ask nobody has accepted stays a pending claim.
RECEIVER_CONSENT_FROM_NS = 1_794_103_200_000_000_000
# #1346: the role rule (`EVENT_ROLE`) starts at this `order` (ns since the
# epoch, 2026-11-15T20:17:11Z, later than every row in any ledger written
# before the rule and earlier than any row a build with it can write). A row
# ordered BEFORE it keeps the meaning it had when written: some ledgers hold
# a human `accepted` or an agent `done` in the sender's own bucket, which
# the old fold applied, and refolding them under the rule would reopen
# requests that were closed. Append-only history is not reinterpreted. From
# this order on a wrong-role row is inert. Same shape as
# `RECEIVER_CONSENT_FROM_NS`, same `_written_before` helper.
ROLE_RULE_FROM_NS = 1_794_770_231_000_000_000
# The events that existed when the rule began, FROZEN: only they can have old
# ledger rows the exemption protects. An event added later (`withdrawn`,
# `withdrawn_delivered` came with the rule itself) has no history before the
# cutoff, so a row of it dated earlier is forged or hand-edited and gets no
# old rule. Never add to this set.
_PRE_ROLE_RULE_EVENTS = frozenset({
    "opened", "revised", "surfaced", "verdict_surfaced", "delivered",
    "verdict_delivered", "owed_delivered", "needs_info", "accepted",
    "rejected", "done", "suppressed", "done_verified", "replied",
})
# #885. States in which the RECIPIENT owes work. Deliberately NOT folded into
# `_SENDER_MOVABLE`: widening that set would let a sender-side `revised` event
# reopen an accepted record, which is exactly the assertion the wedge
# principle forbids and which the frozenset above exists to prevent. The two
# sets answer different questions and diverge at exactly one state — for the
# SENDER `accepted` settles the ask, for the RECIPIENT it is where the work
# starts and `done` is the close. Sharing one frozenset made `accept`
# indistinguishable from `suppress` from the recipient's side while meaning
# the opposite thing.
_RECIPIENT_OWED = frozenset({"accepted"})

# #1344: who may close an accepted request, said once. Every text an agent
# reads before deciding whether to close derives from these three strings: the
# live-delivery hook line, the owed panel and `request done --help` import them,
# and the two skills (markdown cannot import) carry CLOSE_RULE verbatim, which
# tests/test_close_rule.py pins. Accept, reject and needs-info need a person
# (accept has the one ruling-covered agent path); the last sentence is the exception a plain "done needs no human" would hide.
CLOSE_RULE = (
    "Closing an accepted request needs no human: run `daimon request done "
    "<id> --evidence ...` the moment evidence exists, in the same turn, not "
    "at session end. Only `accept` has an agent path, under a covering "
    "ruling; reject and needs-info are a person's. On an ask nobody "
    "accepted, an agent `done` is only a pending claim that lands when a "
    "person accepts.")
CLOSE_RULE_SHORT = ("Accepted and owed. Close one now, no human needed: "
                    "`daimon request done <id> --evidence …`")
CLOSE_RULE_HELP = ("An agent may run this on an accepted ask without asking "
                   "the human.")
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
    # #1348: beside `revised`: a same-`order` tie with a verdict folds the
    # severity first, on the pre-verdict state, which is harmless either way.
    "severity_set": 1,
    "surfaced": 2,
    "verdict_surfaced": 2,
    "delivered": 2,
    "verdict_delivered": 2,
    "owed_delivered": 2,
    "suppressed": 3,
    "done": 4,
    "needs_info": 5,
    "accepted": 6,
    "rejected": 7,
    # #1346: after the verdicts, so a same-`order` tie ends the way the
    # record's own terminal rules say: a finished `done` and a `rejected`
    # fold first and the withdraw is ignored, while a same-instant `accepted`
    # folds first and the sender's retraction still lands.
    "withdrawn": 8,
    # #1117: after every verdict, so a same-`order` verdict folds FIRST.
    "replied": 9,
    # Verification always answers a `done` row that already landed; ranked
    # last so a same-`order` tie (test clocks) can never process it first.
    "done_verified": 10,
    "withdrawn_delivered": 2,
}
_REQUEST_ID_RE = re.compile(r"q-[0-9a-f]{12}")


def is_request_id(value) -> bool:
    """Whether `value` is the shape of a request id. The one public name for
    the check, so a module that must recognise an id (a message received from
    another machine) neither imports the private pattern nor copies it."""
    return isinstance(value, str) and _REQUEST_ID_RE.fullmatch(value) is not None


# A recipient is validated for SHAPE only, by `scope.parse_bucket` (the one
# bucket-name parser, #1294) — whether the slug names a bucket that exists is
# the CLI's check (it can offer near-matches and an --anyway override; this
# module must stay writable for a project not yet initialized).
_SPACE_RE = re.compile(r"\s+")
MAX_TEXT_CHARS = 2000
# #1127: the display bound for an ask shown as a title or a skimmable line.
# One limit for every surface (decide headline, briefing panels, hook inject).
ASK_CHARS = 160
# #1131: the sender-set title is a few words. Bounded at the write edge like
# every other field, and REFUSED when over (never cut: a cut title is a
# sentence the sender did not write), exactly as an over-long ask is.
TITLE_CHARS = 80
# #1348: how much a request matters to its sender. ONE ordered table, most
# severe first: argparse choices, the write boundary, the fold, every sort and
# filter, the MCP schema enum and the docs all read it, and a census test fails
# when another module spells a level. `normal` is "no severity" and the only
# default; it is set in the fold's `opened` branch and nowhere else.
SEVERITIES = ("critical", "high", "normal", "low")
DEFAULT_SEVERITY = "normal"
# The reason a non-normal level carries: one line, REFUSED past this (never
# cut), a separate constant from `TITLE_CHARS` because it changes for its own
# reason (the briefing byte budget).
SEVERITY_REASON_CHARS = 80
_LABEL_MAX = 64
# The verify_done transcript-speaker role, same bound amendments.py's
# _ROLE_MAX uses for the identical session-end byte-check shape.
_ROLE_MAX = 64


def short_ask(text) -> str:
    """#1127: bound an ask to `ASK_CHARS` for a title or skimmable line. An
    ask is up to `MAX_TEXT_CHARS` chars on one line, so a raw print is a wall.
    Whitespace collapses; a short text is returned whole; else the first
    sentence if it fits (and is not a stub); else a cut at the last word
    boundary (when one sits in the back half) with an ellipsis. Never longer
    than `ASK_CHARS`. The cut itself lives in `display.shorten` (#1129)."""
    return display.shorten(text, ASK_CHARS, sentence=True)


def headline(record) -> str:
    """#1131: the one owner of what a request shows in a title position.

    The sender's title when it set one, else the bounded ask. The title was
    already bounded at the write edge; it is bounded again here because this
    reads a folded record, and a ledger line edited on disk must not widen a
    headline. Every surface that titles a request calls this, so none of
    them can disagree about which text leads."""
    title = str((record or {}).get("title") or "").strip()
    if title:
        return display.shorten(title, ASK_CHARS)
    return short_ask((record or {}).get("ask"))


_SEVERITY_RANK = {level: i for i, level in enumerate(SEVERITIES)}
# What `_severity_of` returns for a severity part that cannot be read.
_UNREADABLE = object()


def severity_rank(level: str) -> int:
    """#1348: where `level` sorts, most severe first. Raises `KeyError` on a
    level outside `SEVERITIES`: a caller holding an unknown level has a bug,
    and a silent default would hide it."""
    return _SEVERITY_RANK[level]


def severity_line(record: dict) -> str:
    """#1348: the ONE renderer of a request's severity. Nothing for `normal`
    (the absence of a severity renders as absence, like `blocking: false`),
    else a line such as `  High: release waits on this`. Every surface that
    shows it calls this. Indexes the folded fields directly: the fold gives
    every record both, so a reader-side default would only hide a record the
    fold did not build. The reason is bounded again here because the fold
    reads ledger lines that can be edited on disk (the `headline` precedent)."""
    level = record["severity"]
    if level == DEFAULT_SEVERITY:
        return ""
    reason = display.shorten(record["severity_reason"], SEVERITY_REASON_CHARS)
    return f"  {level.capitalize()}: {reason}"


def _severity_of(row: dict):
    """#1348: the ONE reader of a row's severity part, so a ledger line edited
    on disk or written by another build cannot ride into the fold half-formed.

    `None` when the row carries no `severity` key (valid on `opened`, where it
    means the default). `(level, reason)` when it is readable: a non-normal
    level with a non-empty reason, or `normal` with none. `_UNREADABLE` for
    anything else (an unknown level, a missing reason, a reason on `normal`)."""
    if "severity" not in row:
        return None
    level, reason = row.get("severity"), row.get("severity_reason")
    if not isinstance(level, str) or level not in _SEVERITY_RANK:
        return _UNREADABLE
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        return _UNREADABLE
    reason = _SPACE_RE.sub(" ", reason).strip()
    if (level == DEFAULT_SEVERITY) == bool(reason):
        return _UNREADABLE
    return level, reason


def _attention_sorted(rows: list[dict]) -> list[dict]:
    """#1348: the ONE order every capped recipient-side attention feed uses
    (the decision and owed panels and their live-delivery twins), sorted in
    place: today's newest-first recency order, then a STABLE sort by severity.
    Within a level the order is exactly what it was, and a ledger with no
    severities sorts as it always did. The cap applies after this, so a
    critical ask beats newer normal ones for the slots and the overflow line
    counts the rest."""
    rows.sort(key=_recency, reverse=True)
    rows.sort(key=lambda r: severity_rank(r["severity"]))
    return rows


def _listing_order(rows, min_severity=None) -> list[dict]:
    """#1348: the ONE order and filter of the two listing verbs (`request
    list`, `request inbox`): undecided first, as before, then most severe,
    then today's recency order. `min_severity` keeps that level and anything
    more severe; an unknown level is refused here, once, for the CLI, MCP and
    the host API, which all reach this through the same keyword."""
    if min_severity is not None and min_severity not in _SEVERITY_RANK:
        raise RequestError(
            f"min severity must be one of: {', '.join(SEVERITIES)}")
    floor = None if min_severity is None else severity_rank(min_severity)
    kept = [r for r in rows
            if floor is None or severity_rank(r["severity"]) <= floor]
    return sorted(kept, key=lambda r: (r["state"] not in _SENDER_MOVABLE,
                                       severity_rank(r["severity"]),
                                       *_recency(r)))


def _validated_severity(level, reason) -> tuple[str, str]:
    """#1348: the write boundary of a severity, once, for `open_request` and
    `set_severity`. Returns `(level, reason)` ready for a row: a level outside
    the table is refused, `normal` carries no reason, every other level needs
    one, and a reason over `SEVERITY_REASON_CHARS` is REFUSED, never cut."""
    if level not in _SEVERITY_RANK:
        raise RequestError(
            f"severity must be one of: {', '.join(SEVERITIES)}")
    reason = _scrub("severity_reason", reason, required=False,
                    limit=SEVERITY_REASON_CHARS)
    if level == DEFAULT_SEVERITY:
        if reason:
            raise RequestError(
                f"{DEFAULT_SEVERITY} is the default severity and carries no "
                "reason")
        return level, ""
    if not reason:
        raise RequestError(f"a {level} severity needs a one-line reason")
    return level, reason


# Every field of a ledger row that can hold plaintext (#645 discipline).
# One declaration, two consumers: `forget_content_key` decides which records
# a deletion reaches, and `privacy.audit_project` decides which fields it
# hashes when proving the deletion happened.
#
# `author` is deliberately absent: it is a person's name, not item text, and
# matching a tombstone against it would let one forgotten value delete every
# record a given author ever wrote. `to` is absent for the same reason in
# reverse — it is a filesystem-derived slug, not authored prose.
#
# #1026: `act_author` IS here, and the difference from `author` above is the
# reason, not an oversight. `author` is this process's own derived identity,
# the same value on every row it writes, so a tombstone matching it would
# empty the ledger of the one person who owns the machine. `act_author` is
# free prose a multi-person host supplies per act, never derived here — it is
# the one field on this surface that names a person who is NOT the operator,
# so the audit must hash it, and reaching every act somebody signed is what a
# forget aimed at that name is FOR on a surface that exists to serve several
# people. `pending._strip_plaintext` reads this same `prose` column, so a
# foreign bucket's names stop at the read boundary for free (scar 0055).
_LEDGER = "requests.jsonl"


class RequestError(ValueError):
    """A requested ledger transition is invalid or cannot be persisted."""


class RequestTooLong(RequestError):
    """The over-cap branch of `_text`, and ONLY that branch — a required-but-
    empty field stays a plain `RequestError`. Carries `field` and `limit` so
    the CLI boundary can name a field-appropriate destination (#916, mirrors
    the #902 handoff refusal) without re-parsing the message string. A
    caller matching on `RequestError` still catches this by construction."""

    def __init__(self, message: str, *, field: str, limit: int):
        super().__init__(message)
        self.field = field
        self.limit = limit


def _path(project_dir=None):
    # #948: resolve BEFORE slugging, the same way the CLI resolves --project.
    # Without this a host calling in with "<repo>/plugin" wrote a bucket the
    # CLI's read of the same path never looked at. A bucket slug handed in by
    # `--slug` routing or a bucket-iterating reader passes through untouched.
    slug = store.project_slug(config.resolve_project_dir(project_dir))
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "requests.jsonl"


def _text(name: str, value, *, required: bool = True,
          limit: int = MAX_TEXT_CHARS) -> str:
    out = _SPACE_RE.sub(" ", str(value or "")).strip()
    if required and not out:
        raise RequestError(f"{name} is required")
    if len(out) > limit:
        raise RequestTooLong(
            f"{name} is too long ({len(out)} > {limit} characters)",
            field=name, limit=limit)
    return out


def _scrub(name: str, value, *, required: bool = True,
           limit: int = MAX_TEXT_CHARS) -> str:
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


def _row_order(row: dict) -> int:
    """The row's nanosecond `order`; a row written before the stamp existed
    (or with an unreadable one) counts as 0. `ts` is this value rendered at
    second resolution, so recency is compared on this, never on `ts`."""
    try:
        return int(row.get("order") or 0)
    except (TypeError, ValueError):
        return 0


def _recency(record: dict) -> tuple[int, str]:
    """The sort key for every reader that orders folded requests by recency:
    the nanosecond stamp of the last row folded in, the id only to break an
    exact tie. One key, so no panel can fall back to the
    second-resolution `updated_at` on its own."""
    return (int(record.get("updated_order") or 0), record["request_id"])


def _act_author(channel: str, author) -> str:
    """#1026: the scrubbed per-act author, or "" when nobody was named.

    The gate, not a formatter — it refuses before any row is built, so a
    caller that names someone on a channel with no per-act identity gets a
    refusal instead of a row that silently records the process instead of
    the person. `None` short-circuits untouched: every existing caller
    passes nothing, and the channel check must not start failing calls that
    never mentioned an author (an unknown channel still reaches `_stamp`'s
    own channel error, which names the right mistake).

    Called from `_stamp`, which every writer on this module's public path
    goes through, and from exactly one other place: `accept`, whose agent
    branch writes a pre-stamped row and so never reaches `_stamp` at all.
    Duplicating it into the other verbs buys nothing a test can tell apart
    and is one more copy to drift.

    Blank after scrubbing is the same as absent, so the caller cannot write
    an empty `act_author` key: in an append-only stream the ABSENCE of a key
    is data (scar 0042), and `""` on the row would be indistinguishable from
    a row that lost the value.
    """
    if author is None:
        return ""
    value = _scrub("act_author", author, required=False, limit=_LABEL_MAX)
    if not value:
        return ""
    if of_channel("requests", channel) != "human" or channel in _CLI_CHANNELS:
        raise RequestError(
            "a per-act author names the person behind THIS act, and this "
            "channel derives its author from the environment instead — one "
            "terminal, one person; name the author only from an in-process "
            f"human writer, or leave it unset: this call arrived through "
            f"{channel!r}")
    return value


def _stamp(event: str, request_id: str, channel: str,
           *, now_ns: int | None = None, event_id: str | None = None,
           author: str | None = None) -> dict:
    if event not in EVENTS:
        raise RequestError(f"unknown request event: {event}")
    if not is_request_id(request_id):
        raise RequestError(f"invalid request id: {request_id!r}")
    if channel not in known_channels("requests"):
        raise RequestError(
            f"channel must be one of: {', '.join(sorted(known_channels('requests')))}")
    # Derived, never accepted: no way to name one channel and claim another's
    # authority.
    authority = of_channel("requests", channel)
    # #1026: gated BEFORE the row exists, so a refusal leaves nothing for a
    # caller to append by accident.
    act_author = _act_author(channel, author)
    order, event_id = clock.stamp_identity(now_ns, event_id)
    row = {
        "version": VERSION,
        "ts": _ts(order),
        "order": order,
        "event_id": event_id,
        "event": event,
        "request_id": request_id,
        "channel": channel,
        "authority": authority,
        # The PROCESS identity, unchanged by #1026: the row records both, so
        # a reader can tell a host-attributed name from a derived one.
        "author": config.author(),
    }
    if act_author:
        row["act_author"] = act_author
    return row


def append(row: dict, project_dir=None, *, writer: Writer) -> bool:
    """Append one admitted lifecycle row. Never mutates another bucket."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    # #1131: the registry's scalar prose columns plus `author` (a person's
    # name, redacted but deliberately not forgettable, see above), so a
    # plaintext field added to the registry cannot be stored unredacted.
    admitted = policy.admit_row(
        row, redact_fields=(*surfaces.scalar_prose_fields(_LEDGER), "author"))
    try:
        store.ensure_bucket(project_dir)  # #1337: dir + root
        return jsonl.append_as(path, admitted, writer)
    except jsonl.Refused:
        raise  # only when the run surfaces refusals (jsonl.append_as)
    except OSError:
        return False


def events(project_dir=None, *, rows=None) -> list[dict]:
    """Read valid ledger rows best-effort; malformed lines never sink reads.
    `rows` hands in the raw rows of a `jsonl.read` the caller already did
    (the snapshot's single read), filtered exactly as a fresh read is."""
    if rows is None:
        path = _path(project_dir)
        if path is None or not path.exists():
            return []
        rows = jsonl.read(path).rows
    out = []
    for index, row in enumerate(rows):
        if (not isinstance(row, dict)
                or row.get("event") not in EVENTS
                or not is_request_id(row.get("request_id"))):
            continue
        copy = dict(row)
        # `_line` is the row's index among the rows `jsonl.read` returned
        # (torn and garbage lines do not count); it is only a read-order
        # tie-break, so its absolute value is not part of any contract.
        copy["_line"] = index
        out.append(copy)
    return out


def _covered_by_open_policy(row: dict, policies) -> bool:
    """#961 slice 5: whether `row` (an agent-channel `opened` row proposing
    `kind="info"`) is authorized by one of the `(sender, to, kind, verb, by,
    ruling_id, sha256, active_from, active_until)` INTERVALS in `policies` —
    the set `refutations.request_policy_history` resolved for the row's own
    ORIGIN bucket (the sender, ratified against ITS OWN ruling ledger; see
    `fold`'s `open_policies` parameter).

    Mirrors `_covered_by_policy` exactly, matching on `to` instead of
    `sender` (the field a `verb=open` policy tuple carries; `sender` is
    always `""` on that shape, `_policy_tuple`'s own docstring). The row's
    own `under_ruling`/`policy_sha256` are a STAMP a non-human channel wrote
    about itself, never the gate on their own: this only lands when the
    INJECTED history independently names the same ruling id and hash AND the
    ROW's own `order` falls inside the interval during which that exact
    grant was active — never merely "was ever active at some point"."""
    ruling_id = str(row.get("under_ruling") or "")
    sha = str(row.get("policy_sha256") or "")
    if not ruling_id or not sha:
        return False
    try:
        row_order = int(row.get("order") or 0)
    except (TypeError, ValueError):
        return False
    to = str(row.get("to") or "")
    for entry in policies:
        sender, entry_to, kind, verb, by, entry_ruling, entry_sha, since, until = entry
        if (entry_to == to and kind == "info" and verb == "open"
                and by == "agent" and entry_ruling == ruling_id
                and entry_sha == sha and row_order >= since
                and (until is None or row_order < until)):
            return True
    return False


def _kind_of(row: dict,
             open_policies: Mapping[str, frozenset] | None = None) -> str:
    """The approval-requirement kind an `opened` row carries (#961).

    Read from a row in exactly one place, this branch of `fold` — no other
    event re-folds `kind` — so gating it here gates the field for the
    record's entire lifetime, not one snapshot of it. Three rules, kept in
    one place because a caller wanting to lower this ask's approval bar has
    three doors to try, and closing two of them would just relabel the hole:

    1. A row minted before this field existed, or one that otherwise carries
       no readable member of `KINDS`, reads as `work` — nothing is
       reclassified by absence, and the unreadable case defaults toward MORE
       scrutiny, never less. `isinstance(value, str)` guards a non-scalar
       `kind` (a list, a dict) from raising inside `value in KINDS` — every
       other expression in this branch is total over arbitrary JSON, and
       this one must be too, or one poisoned line in one bucket takes down
       every read surface for that bucket (records, listing, renderable,
       status_counts, and both `--json` surfaces cross-bucket).
    2. A `to_human` ask reads as `work` regardless of what is stored.
    3. `kind == "info"` requires the OPENER's channel to carry human
       authority — the same `authority.of_row` lookup the `_HUMAN_ONLY`
       re-check below uses for verdict/suppression events. `open_request`
       already refuses a non-human channel and the `to_human` combination at
       the WRITE boundary, but `requests.append` is public, takes an
       arbitrary dict, and validates no payload field — so a caller that
       skips `open_request` (or a back-dated duplicate `opened` row for an
       id that already exists) could otherwise mint or reclassify an `info`
       ask with no person ever touching it. Rules 2 and 3 belong at THIS
       boundary, not only the write one, for exactly that reason. `ui` and
       `signed` both map to `"human"`, so the legitimate in-process human
       writer pays nothing for this check.

    #961 slice 5: an AGENT-channel row proposing `kind="info"` is no longer
    unconditionally forced to `work` — inside the SAME non-human branch, an
    agent-authored row is `info` when a `verb=open` ruling in the row's own
    ORIGIN bucket covers it. `open_slug` is read directly off the row's own
    `_origin_slug` (`_founder_by_id`'s own stamp), never a second pre-pass
    (scar 0084): the SAME founder row decides both `kind` and which ledger
    governs it. `open_policies` defaults toward MORE scrutiny — `None`/`{}`
    (a caller injecting nothing, or the row's own origin having no entry)
    leaves every agent-opened `info` row reading as `work`, unchanged from
    before this slice."""
    value = row.get("kind")
    kind = value if isinstance(value, str) and value in KINDS else DEFAULT_KIND
    if row.get("to_human") is True:
        return DEFAULT_KIND
    authority = of_row("requests", row)
    if authority != "human":
        if authority == "agent" and kind == "info" and open_policies:
            open_slug = str(row.get("_origin_slug") or "")
            if _covered_by_open_policy(row, open_policies.get(open_slug,
                                                              frozenset())):
                return "info"
        return DEFAULT_KIND
    return kind


def _stamped(rows: list[dict], origin_slug: str) -> list[dict]:
    """#1083: stamp `_origin_slug` on every row, OVERWRITING whatever the
    disk row already carried under that key.

    `events()` copies a persisted line whole (`copy = dict(row)`, above)
    and `policy.admit_row` scrubs only named text fields — never
    `_origin_slug` — so a row written into a project's own `requests.jsonl`
    that plants this key reads back with it. Before this fix, only
    `recipient_join`'s foreign branch ever stamped, which meant every OTHER
    composer (and `recipient_join`'s own local branch) trusted whatever a
    row claimed about its own origin. The invariant now is: every composer
    calls this on every row it hands to `fold`, and no gate ever reads an
    unstamped value — a local row always gets `""`, the only value
    `_covered_by_policy`/`_founder_by_id` ever treat as "no cross-bucket
    sender to match"."""
    for row in rows:
        row["_origin_slug"] = origin_slug
    return rows


def _founder_by_id(ordered: list[dict],
                   open_policies: Mapping[str, frozenset] | None = None,
                   ) -> dict[str, tuple[str, str, str]]:
    """#961 slice 4 review round 2 (C1): the ONE founder resolution for
    every request id, resolved in a PRE-PASS over every `opened` row before
    `fold`'s main pass applies any lifecycle event against it — replacing
    two separate pre-passes (`_founder_kind_by_id`, identity's `kind` only;
    `_founder_origin_by_id`, origin only) that used to answer "who sent
    this" independently and could disagree.

    That disagreement was reachable: the origin-only pass had NO
    read-boundary shape check, so a shape-invalid `opened` row (an empty
    `ask`, say) planted in a stranger's bucket at an earlier `order` than
    the genuine founder could never actually FOUND the record — `fold`'s
    own founder branch below re-applies the identical shape check and
    would skip it, and the identity-only pass already skipped it too — but
    the origin-only pass read `_origin_slug` off it anyway, since it never
    checked shape at all. A request could therefore fold with `kind` and
    `to`/`ask` from the genuine founder while its ORIGIN (`from_slug`, and
    every policy match keyed on it) came from an entirely different,
    invalid row a stranger controlled. One pre-pass, one shape check, one
    row decides both fields, closes the gap by construction: the two
    answers cannot drift apart because there is only one answer now.

    The founder-plus-disagreement rule for `kind` itself is unchanged from
    slice 1 (`fold`'s own inline comment on the founder branch has the full
    reasoning) — only WHEN it runs moves, and only `kind` disagrees; a
    valid duplicate `opened` row can force `kind` to `DEFAULT_KIND` but
    never moves `origin_slug` off whatever the FIRST valid row set, the
    same "first writer wins" identity rule the record's `to`/`ask` already
    follow. Under the single-pass version a lifecycle event (an `accepted`
    row, say) processed BEFORE a later `opened` duplicate saw
    `current["kind"]` as whatever the founder alone said, even when that
    later duplicate would go on to disagree and force it to `work` — see
    #961 slice 3 review item 3's own history for the exploit this closed.

    Returns `{request_id: (kind, origin_slug, opened_under_ruling)}` (#961
    slice 5 widens the tuple by one field: the covering ruling id when an
    AGENT-authority founder resolved to `kind="info"` via `open_policies`,
    `""` otherwise — including every human-opened `info`, which carries no
    ruling). `origin_slug` reads
    `_origin_slug`, a transient field a row never persists on disk
    (`append` never writes it, and any value a row's own text carried
    under that key is overwritten before `fold` ever sees it) — the
    composer always stamps it (`_stamped`, above), on every row it hands
    to `fold`, never only the ones it happens to care about. `records()`
    and `sender_join()` stamp `""` on every row they gather — a per-bucket
    or sender-side fold has no cross-bucket sender to name — so every
    founder they fold reads back `""` here: empty, never a real slug,
    which is exactly what `_covered_by_policy` needs — an unstamped-
    equivalent context must cover NOTHING, not accidentally match a policy
    whose sender happens to be the reader's own project. `recipient_join`
    stamps the real foreign bucket a row came from, or `""` for its own
    local rows.

    `ordered` is `fold`'s own sorted list, passed in rather than re-sorted
    here, so this stays exactly as deterministic under reorder as `fold`
    already is — the pre-pass and the main pass share one sort, not two
    that could drift apart."""
    founders: dict[str, tuple[str, str, str]] = {}
    for row in ordered:
        if row.get("event") != "opened":
            continue
        q_id = str(row.get("request_id") or "")
        if q_id not in founders:
            # Same read-boundary shape check `fold`'s founder branch makes:
            # a row failing it is never a founder — for kind OR origin, now
            # that one row decides both — and the NEXT valid `opened` row
            # for this id (if any) is the one that founds it, in every
            # pass alike.
            if scope.parse_bucket(str(row.get("to") or "")) is None:
                continue
            if not str(row.get("ask") or "").strip():
                continue
            kind = _kind_of(row, open_policies)
            row_authority = of_row("requests", row)
            # #961 slice 5: the ruling id only when THIS founder row is the
            # reason `kind == "info"` — a human-opened info carries none, so
            # `opened_under_ruling` never renders on one (#766's own
            # "rendering is a write" reasoning: a ruling-classified ask must
            # never look identical to a human-opened one, nor vice versa).
            ruling = (str(row.get("under_ruling") or "")
                     if kind == "info" and row_authority == "agent" else "")
            founders[q_id] = (kind, str(row.get("_origin_slug") or ""),
                             ruling)
            continue
        authority = of_row("requests", row)
        if (authority == "human"
                and _kind_of(row, open_policies) != founders[q_id][0]):
            founders[q_id] = (DEFAULT_KIND, founders[q_id][1], "")
    return founders


def _covered_by_policy(row: dict, origin_slugs, policies, *,
                       kind: str) -> bool:
    """#961 slice 4, widened by #1083: whether `row` (an `accepted` event
    landing on an ask of this `kind`, `info` or `work`) is authorized by one
    of the `(sender, kind,
    verb, by, ruling_id, sha256, active_from, active_until)` INTERVALS in
    `policies` — the set `refutations.request_policy_history` resolved for
    the RECIPIENT this record is addressed to, injected by the caller (one
    of `fold`'s composers).

    The row's own `under_ruling`/`policy_sha256` are a STAMP a non-human
    channel wrote about itself, never the gate on their own — the same
    reasoning the deleted `--by human` flag was refused for (#512). This
    only lands when the INJECTED history independently names the same
    ruling id and hash AND the ROW's own `order` falls inside the interval
    during which that exact grant was active — never merely "was ever
    active at some point" — so a row claiming coverage the caller's own
    ruling ledger never granted AT THAT TIME is inert regardless of what it
    says about itself. `active_until` of `None` means the interval is (or,
    at the end of the ruling ledger's own history, was) still open: any row
    order at or after `active_from` matches.

    `origin_slugs` (#1083: widened from a single `origin_slug` string to a
    set) is the identity a policy's `sender` must match — `sender in
    origin_slugs`, never `==`. For a record whose founder is genuinely
    FOREIGN (`recipient_join`'s cross-bucket case), the caller passes a
    single-element set naming that real sender, unchanged in effect from
    the old string-equality check. For a record whose founder is LOCAL to
    the reading composer AND addressed to a DIFFERENT project (`listing()`/
    `sender_join()`'s ordinary "sent abroad" case), the caller widens this
    to `{own_slug} | buckets.aliases_for(own_slug)` — this project's own
    identity is genuinely who sent it, and a ruling ratified before a
    migration may still name the OLD slug. A SELF-addressed record (`to ==
    own_slug`) never gets this widening, even from a composer that passes
    `own_slug` — `fold`'s own gate applies the fallback only when
    `current["to"] != own_slug`; see its docstring for why. An empty set —
    no widening applies and no real foreign origin either (`records()`,
    `recipient_join`'s own self-addressed rows, or any local record read by
    a composer that passes no `own_slug` at all) — matches nothing
    STRUCTURALLY: no ruling's `sender` is ever the empty string
    (`refutations._policy` refuses one at the write boundary), so the loop
    below can never find an entry whose `sender` is `""`, the same
    non-match `_founder_by_id`'s docstring already relies on."""
    ruling_id = str(row.get("under_ruling") or "")
    sha = str(row.get("policy_sha256") or "")
    if not ruling_id or not sha:
        return False
    try:
        row_order = int(row.get("order") or 0)
    except (TypeError, ValueError):
        return False
    for entry in policies:
        # #961 slice 5: `_policy_tuple` widened by one field (`to`, index 1,
        # `""` on every `verb=accept` grant) — unpacked and ignored here,
        # the field `_covered_by_open_policy` reads instead.
        (sender, _to, entry_kind, verb, by, entry_ruling, entry_sha, since,
         until) = entry
        if (sender in origin_slugs and entry_kind == kind
                and verb == "accept"
                and by == "agent" and entry_ruling == ruling_id
                and entry_sha == sha and row_order >= since
                and (until is None or row_order < until)):
            return True
    return False


def _written_before(row: dict, effective_from_ns: int) -> bool:
    """Whether `row` was written before a rule that took effect at
    `effective_from_ns` (`RECEIVER_CONSENT_FROM_NS`, `ROLE_RULE_FROM_NS`).
    A row with no readable positive `order` is NOT before it: an unreadable
    order never buys the old rule, and a bool read off disk is not one."""
    raw = row.get("order")
    if isinstance(raw, bool):
        return False
    try:
        order = int(raw or 0)
    except (TypeError, ValueError):
        return False
    return 0 < order < effective_from_ns


def _epoch_of(row: dict, fallback: int) -> int:
    """#1117: the epoch a reply row names, else `fallback`; a bool, negative
    or string read off disk never becomes an epoch."""
    value = row.get("revision")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return fallback


def _reply_from_sender(current: dict, row: dict, own_slug: str) -> bool:
    """#1117: whether the request's SENDER wrote this `replied` row, by the
    transient `_origin_slug`. A FOREIGN founder is the sender; a LOCAL one is
    the composer's own bucket (origin ""), unless self-addressed. `records()`
    has no `own_slug`, so it skips the local half."""
    origin = str(row.get("_origin_slug") or "")
    if current["from_slug"]:
        return origin == current["from_slug"]
    return bool(own_slug) and current["to"] != own_slug and origin == ""


@dataclass(frozen=True)
class FoldInputs:
    """#1338: everything `fold` consults besides the rows, in ONE immutable
    bundle that only `fold_inputs` constructs. `fold` takes it with no
    default, so a composer cannot leave a piece out or build it slightly
    differently from the others. See `fold`'s docstring for what each field
    means: `policies` (flat accept history), `policies_by_to` (per-recipient
    accept history, `None` = use the flat set), `own_slug`, `open_policies`
    (per-origin `verb=open` history), `home_slugs` (#1346, the local bucket
    for the role check)."""
    policies: frozenset
    policies_by_to: Mapping[str, frozenset] | None
    own_slug: str
    open_policies: Mapping[str, frozenset]
    # #1346: the composer's own bucket and its aliases, the founder's side
    # when a founder is local (`from_slug == ""`). `own_slug` cannot serve:
    # it is `""` for LOCAL and RECIPIENT on purpose (it widens covered
    # accepts) while the role check needs to know which bucket "local" is.
    home_slugs: frozenset


class Perspective(Enum):
    """Whose rows a composer folds, which decides where the rulings that
    cover them live. Named, never implied: two composers that need
    different inputs say so here instead of differing silently.

    LOCAL: this bucket's own rows (`records`), every founder local.
    SENDER: rows of asks this project SENT (`sent`, `listing`); the covering
        accept ruling lives in each RECIPIENT's ledger, resolved per `to`.
    RECIPIENT: rows of asks sent TO this project (`recipient_join`); accept
        rulings are this project's own, open rulings are each origin's.
    FLEET: one request group from the fleet-wide decide tally
        (`pending._foreign_request_counts`), read for its founder's `to` and
        origin."""
    LOCAL = "local"
    SENDER = "sender"
    RECIPIENT = "recipient"
    FLEET = "fleet"


class PolicyReads:
    """One memoized `request_policy_history` read per bucket for the life of
    a composer call (or, for the fleet tally, the whole fleet pass), so many
    asks to the same project never re-read its ruling ledger.

    A read that raises reads as `frozenset()`: no grant, so every agent
    verdict on that bucket falls back to needing a human channel. That is
    the deliberate direction already documented on `_policies_by_to` (fail
    toward MORE scrutiny, never less).

    #1338: also the ONE owner of recipient identity. A bucket named by a
    pre-migration alias is resolved FORWARD through `buckets.alias_map()`
    before its ledger is read, so every perspective (sender, recipient,
    fleet) answers the same for an ask addressed to an alias. The cache is
    keyed by the name as given."""

    def __init__(self) -> None:
        self._cache: dict = {}
        self._canonical_of: dict[str, str] | None = None

    def of(self, project_dir) -> frozenset:
        if project_dir not in self._cache:
            if self._canonical_of is None:
                self._canonical_of = buckets.alias_map()
            target = (self._canonical_of.get(project_dir, project_dir)
                      if isinstance(project_dir, str) else project_dir)
            try:
                self._cache[project_dir] = (
                    refutations.request_policy_history(project_dir=target))
            except Exception:
                self._cache[project_dir] = frozenset()
        return self._cache[project_dir]


def _home_slugs(project_dir) -> frozenset:
    """The composer's own bucket plus every alias that reaches it: what a LOCAL
    founder's `to` is compared with to tell a self-addressed ask (#1346)."""
    mine = store.project_slug(config.resolve_project_dir(project_dir)) or ""
    return frozenset({mine} | buckets.aliases_for(mine)) - {""}


def fold_inputs(rows: list[dict], *, perspective: Perspective,
                project_dir=None, policy: frozenset | None = None,
                reads: PolicyReads | None = None) -> FoldInputs:
    """#1338: the ONE place that decides what `fold` is told about rulings.

    `rows` are the rows about to be folded (already `_stamped`); the builder
    reads the ruling ledgers they need. `project_dir` is the composer's own
    project. `policy` lets `records` hand in the ruling history its view
    already read. `reads` shares ledger reads across calls (the fleet tally).

    The per-perspective semantics are exactly what each composer built by
    hand before this existed; the module-level comments on `fold` and on the
    composers explain why each direction differs."""
    reads = reads if reads is not None else PolicyReads()
    home = (frozenset() if perspective is Perspective.FLEET
            else _home_slugs(project_dir))
    if perspective is Perspective.LOCAL:
        return FoldInputs(
            policies=frozenset(),
            policies_by_to=MappingProxyType(_policies_by_to(rows, reads)),
            own_slug="",
            open_policies=MappingProxyType(
                {"": policy if policy is not None else reads.of(project_dir)}),
            home_slugs=home)
    if perspective is Perspective.SENDER:
        return FoldInputs(
            policies=frozenset(),
            policies_by_to=MappingProxyType(_policies_by_to(rows, reads)),
            own_slug=store.project_slug(
                config.resolve_project_dir(project_dir)) or "",
            open_policies=MappingProxyType({"": reads.of(project_dir)}),
            home_slugs=home)
    if perspective is Perspective.RECIPIENT:
        my_slug = store.project_slug(config.resolve_project_dir(project_dir))
        open_policies = {"": reads.of(my_slug)}
        for row in rows:
            if row.get("event") == "opened":
                origin = str(row.get("_origin_slug") or "")
                if origin and origin not in open_policies:
                    open_policies[origin] = reads.of(origin)
        return FoldInputs(
            policies=reads.of(project_dir), policies_by_to=None, own_slug="",
            open_policies=MappingProxyType(open_policies), home_slugs=home)
    founder = next((r for r in rows if r.get("event") == "opened"), {})
    to = str(founder.get("to") or "")
    origin = str(founder.get("_origin_slug") or "")
    return FoldInputs(
        policies=reads.of(to) if to else frozenset(), policies_by_to=None,
        own_slug="",
        open_policies=MappingProxyType(
            {origin: reads.of(origin) if origin else frozenset()}),
        home_slugs=home)


def _supersede_claim(current: dict, meta: dict, row: dict,
                     answered_revision: int) -> None:
    """#1333: move a PENDING completion claim off the record into
    `superseded_claims`, never silently, and clear every field that said the
    ask was answered. Shared by the two sender acts that discard a claim, a
    `revised` row (the ask changed) and a `withdrawn` one (the ask is gone):
    one move, so a claim cannot leave a record without a trace by either."""
    current["superseded_claims"].append({
        "evidence": current["done_evidence"],
        "by": current["done_by"],
        "author": meta.get("author"),
        "claimed_at": meta.get("ts"),
        "verified_at": current["done_verified_at"],
        "revision": answered_revision,
        "superseded_at": row.get("ts"),
    })
    current["done_pending"] = False
    current["done_claimed"] = False
    current["done_evidence"] = ""
    current["done_by"] = None
    current["done_verified_at"] = None


def fold(rows: list[dict], inputs: FoldInputs) -> dict[str, dict]:
    """Fold this bucket's rows into current records, deterministic under
    reorder.

    Full-pass, never latest-wins: the ask, its revisions, and the verdict are
    all witness data, and a later event must not erase the record of an
    earlier one — the stream keeps every row; the fold states what holds NOW.

    Per-bucket only. The cross-bucket join (orphan rule, supersede lineage,
    suppress filter) is the read-time composer PR 2 builds ON TOP of this;
    here a lifecycle row whose `opened` lives in another bucket is an orphan:
    inert in the fold, visible in the raw audit.

    #1083: `policies_by_to`, when given, resolves the covered-accept gate
    (`_covered_by_policy`) PER RECORD, keyed by that record's own `to` —
    never `policies` unioned across every recipient a batch of rows might
    address, which would let one recipient's ruling validate an accept
    addressed to an unrelated one (`_covered_by_policy`'s tuples carry no
    recipient field to guard against that). `None` (every caller before
    this PR, and `recipient_join`/`pending`, whose rows are always
    addressed to exactly one recipient — themselves) keeps the old flat
    `policies` behavior. `own_slug`, when given, is this composer's own
    bucket identity — used only when a record's founder is LOCAL (`from_
    slug` is `""`) AND the record is addressed to a DIFFERENT project
    (`current["to"] != own_slug`), the fallback `_covered_by_policy` widens
    against `{own_slug} | buckets.aliases_for(own_slug)`; see that
    function's own docstring. A SELF-addressed record (`to == own_slug`)
    never gets this fallback, regardless of whether `own_slug` was passed:
    the fold must never be more permissive than `accept()`'s own write
    boundary, which already refuses to authorize a self-addressed accept
    under any ruling (a self-referential grant is the same "agent lowering
    its own ask's approval bar" pattern `_kind_of` already forbids for
    `kind == "info"`, generalized here to a `work` ruling).

    #961 slice 5: `open_policies`, a `dict[origin_slug, frozenset]` of
    `request_policy_history`-shaped intervals, keyed by the ORIGIN bucket a
    founder row's own `_origin_slug` names (`""` = local to this composer).
    Threaded straight through to `_founder_by_id` (the one place `kind` is
    ever resolved) — never consulted a second time inside this function.
    `None`/`{}` (every caller before this slice) leaves every agent-opened
    `kind="info"` row reading as `work`, the negative control this
    parameter's own default preserves by construction.

    #1338: those four inputs arrive as ONE `FoldInputs`, built only by
    `fold_inputs`; there is no way to call `fold` without all of them."""
    policies = inputs.policies
    policies_by_to = inputs.policies_by_to
    own_slug = inputs.own_slug
    open_policies = inputs.open_policies
    def _integer(row, key, default=0):
        try:
            return int(row.get(key) or default)
        except (TypeError, ValueError):
            return default

    ordered = sorted(rows, key=lambda row: (
        _row_order(row),
        _EVENT_RANK.get(str(row.get("event") or ""), 99),
        str(row.get("event_id") or ""),
        _integer(row, "_line"),
    ))
    # #961 slice 3 review item 3, merged with #961 slice 4 review round 2
    # (C1): resolved BEFORE the main pass below touches a single lifecycle
    # event, from the COMPLETE set of `opened` rows — see `_founder_by_id`'s
    # own docstring for why a kind (or an origin) resolved mid-stream let a
    # later duplicate revoke it out from under an already-landed row, and
    # why `kind` and `origin_slug` are resolved from the SAME founder row
    # rather than two independent passes that could disagree.
    founders = _founder_by_id(ordered, open_policies)
    out: dict[str, dict] = {}
    # #1333: who made the pending claim and when, per request, so a `revised`
    # row that clears it can keep it. Local to the pass: it is only ever read
    # by the one branch that discards, never rendered or returned.
    claim_meta: dict[str, dict] = {}
    for row in ordered:
        q_id = row["request_id"]
        event = row["event"]
        authority = of_row("requests", row)
        current = out.get(q_id)
        if event == "opened":
            if current is not None:
                _note_effect(current, row, "duplicate_open")
                continue  # duplicate logical open, first writer wins
                # otherwise; `kind`/`from_slug` are already resolved for
                # every id by `founders` above, so there is nothing left
                # for this branch to reconcile (#961 review item 3 moved
                # that here; round 2 C1 merged origin into the same pass).
            # Read-boundary shape check (the write boundary is not the
            # boundary that matters — events() is deliberately tolerant, and
            # a row edited on disk must not ride into the render).
            if scope.parse_bucket(str(row.get("to") or "")) is None:
                continue
            if not str(row.get("ask") or "").strip():
                continue
            founder_kind, founder_origin, founder_ruling = founders.get(
                q_id, (DEFAULT_KIND, "", ""))
            out[q_id] = {
                "request_id": q_id,
                "state": "open",
                "to": str(row.get("to") or ""),
                "to_human": row.get("to_human") is True,
                "blocking": row.get("blocking") is True,
                # #1348: the ONE place the default severity is set. Every
                # record carries both, so `--json`, MCP and the host API
                # return an explicit `normal` and no consumer owns a default.
                # The row's own severity is applied below, when readable.
                "severity": DEFAULT_SEVERITY,
                "severity_reason": "",
                "kind": founder_kind,
                # #961 slice 4 review round 2 (C1): the SAME founder
                # resolution `_covered_by_policy` matches against, so a
                # composer that reads this off the record (`recipient_join`)
                # can never disagree with what the fold itself already
                # decided. "" for `records()`/`sender_join()`, which always
                # stamp "" (#1083, `_stamped`) — see `_founder_by_id`.
                "from_slug": founder_origin,
                # #961 slice 5: the covering ruling id, or "" — never
                # rendered identically to a human-opened `info` (#766).
                "opened_under_ruling": founder_ruling,
                "ask": str(row.get("ask") or ""),
                "why": str(row.get("why") or ""),
                "evidence": str(row.get("evidence") or ""),
                "from_label": str(row.get("from_label") or ""),
                "supersedes": str(row.get("supersedes") or ""),
                "note": "",
                "opened_by": authority,
                "opened_channel": row.get("channel"),
                "opened_author": row.get("author"),
                # #1026: the person a multi-person host named for THIS act,
                # or None for every row the CLI wrote and every row minted
                # before the field existed. Read exactly as plainly as
                # `opened_author` beside it, and for the same reason — both
                # are attribution a forged row could equally claim, so
                # gating one and not the other would only look like proof.
                "opened_act_author": row.get("act_author"),
                "verdict_by": None,
                "verdict_label": None,
                "verdict_at": None,
                # #1026: who decided, when a host named them. Set beside
                # `verdict_by` in the verdict landing below, so the #1021
                # `done` guard and the sticky-`rejected` guard keep an inert
                # duplicate from re-signing a verdict somebody else made.
                "verdict_act_author": None,
                # #961 slice 3: who landed the CURRENT `accepted` state,
                # "human" or "agent" — never written to disk, recomputed on
                # every fold pass from the authority of the row that landed
                # it, the same derivation `verdict_by` already uses. Not a
                # legacy-default field the way `kind` is: an `accepted` row
                # was human-only before this slice, so every pre-existing
                # accepted record already replays through the SAME channel
                # authority check and lands "human" with no special case.
                # None until an `accepted` event actually lands (see the
                # generic verdict landing below); meaningless on any other
                # state, and left stale (not cleared) if the record later
                # moves past `accepted` the same way `verdict_at` is.
                "accepted_by": None,
                # #961 slice 4: which ruling authorized a non-human accept on
                # a `work` record, or None — for a human accept, for the
                # slice-3 `info` exception (no ruling involved), and until an
                # `accepted` event lands at all. Left stale, not cleared, on
                # the same terms as `accepted_by` above.
                "accepted_under": None,
                "done_by": None,
                # #1026: who reported it done, when a host named them. Set
                # beside `done_by` in the `done` landing below.
                "done_act_author": None,
                "done_claimed": False,
                "done_evidence": "",
                # #978: a completion CLAIMED by a non-human channel on a
                # `work` record no person has accepted yet — the ask stays
                # owed, `done_claimed`/`done_evidence`/`done_by` carry the
                # claim, but `state` does not move to `done`. See the `done`
                # event branch below for the write side and `is_stale` for
                # why a pending claim never decays.
                "done_pending": False,
                # #1333: completion claims a `revised` row cleared, oldest
                # first. A claim never leaves a record without a trace: the
                # sender's revise happens in the sender's session, and the
                # person who accepts later has no other way to see that a
                # claim existed and answered an older ask.
                "superseded_claims": [],
                # #1333: {event_id: {"effect", "reason", ...}}, one entry per
                # lifecycle row this fold consumed, written by `_note_effect`
                # at the branch that decided the row's fate.
                "row_effects": {},
                # #694 PR 3 (D8): set only by a `done_verified` row landing
                # on a currently-claimed completion — never on disk, the
                # session-end byte-check's own timestamp.
                "done_verified_at": None,
                # #1117: progress notes, oldest first; never moves state.
                "replies": [],
                "suppressed": False,
                # {revision epoch: earliest surfaced ts} — D1's write-once
                # dedup key, consulted by the stamp PR 2 adds.
                "surfaced": {},
                # #756: {revision epoch: {session id: earliest delivered ts}}.
                # One level deeper than `surfaced` on purpose — a brief
                # renders an ask once per epoch, but live delivery owes it to
                # every session running in that epoch, so the session id is
                # part of the write-once key rather than a field beside it.
                "delivered": {},
                # #803: {revision epoch: earliest verdict_surfaced ts}, the
                # same shape as `surfaced` above. It was a single timestamp on
                # the premise that "a verdict is terminal once it lands", but
                # needs-info is a verdict state and is not terminal: answering
                # it with a revise and receiving a real verdict afterwards is
                # the ordinary path, and the decay clock kept running from the
                # needs-info, so the acceptance expired before it was shown.
                "verdict_surfaced": {},
                # #801: {revision epoch: {session id: earliest ts}} — the same
                # shape as `delivered`, one level deeper than `verdict_surfaced`
                # for the same reason: a brief renders the verdict card once per
                # epoch, but a live nudge owes it to every session running in
                # that epoch.
                "verdict_delivered": {},
                # #885: {revision epoch: {session id: earliest ts}} — the
                # recipient's OWED lane, the same shape as `delivered` and
                # distinct from it for the same reason `verdict_delivered` is
                # distinct from `verdict_surfaced`. Accepting never bumps the
                # revision (only `revise` does, and it refuses a decided
                # state), so an ask already delivered while `open` carries a
                # `delivered` stamp for this very epoch. Reusing that key
                # would drop the accepted card with no error and no log line.
                "owed_delivered": {},
                # #1117: replies the sender's surfaces showed, from the
                # optional `reply_event_id` on the stamp rows. Mappings used
                # as sets: `request list --json` and MCP `json.dumps` the fold.
                "replies_surfaced": {},
                "replies_delivered": {},
                # #1346: the sender's retraction. `withdrawn_from` is the
                # state it took the ask out of, so the recipient who had
                # ACCEPTED it can be told once. These are NOT `verdict_*`:
                # those mean "the recipient decided". Each later landing of
                # an attention row is `withdrawn_delivered`, {session: ts}.
                "withdrawn_by": None,
                "withdrawn_at": None,
                "withdrawn_from": None,
                "withdrawn_delivered": {},
                "revision": 0,
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "updated_order": _row_order(row),
                "history_count": 1,
            }
            # #1131: present only when the sender set one, so a request
            # without a title folds to exactly the record it always did.
            title = str(row.get("title") or "")
            if title:
                out[q_id]["title"] = title
            # #1348: a severity part this build cannot read never drops the
            # ask: it lands as the default, and the effect says why.
            parsed = _severity_of(row)
            if parsed is _UNREADABLE:
                _note_effect(out[q_id], row, "opened", severity="unreadable")
            else:
                if parsed is not None:
                    out[q_id]["severity"], out[q_id]["severity_reason"] = (
                        parsed)
                _note_effect(out[q_id], row, "opened")
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert here
        # #1346: an act is applied only from the bucket of the role that owns
        # it. The write path proves the caller's side, but `append` is public
        # and a row can be hand-edited or written by an older build, so the
        # reader decides again, from the one table both read.
        role = EVENT_ROLE.get(event, ROLE_EITHER)
        if not ((event in _PRE_ROLE_RULE_EVENTS
                 and _written_before(row, ROLE_RULE_FROM_NS))
                or row_holds_role(
                    role, origin=str(row.get("_origin_slug") or ""),
                    from_slug=current["from_slug"], to=current["to"],
                    home_slugs=inputs.home_slugs)):
            _note_effect(current, row, "not_from_sender"
                         if role == ROLE_SENDER else "not_from_recipient")
            continue
        if event in _HUMAN_ONLY and authority != "human":
            # The fold re-check the write boundary cannot be trusted for: a
            # row appended off-path or edited on disk claiming an agent
            # channel is fully inert, verdict or suppression alike.
            #
            # Receiver consent (one rule for both kinds). An `accepted` row
            # from an agent channel lands only when the receiving project
            # consented to exposing what it knows: an active `verb=accept`
            # ruling of THIS kind, naming the record's sender, whose id and
            # hash the INJECTED `policies` set independently confirms for the
            # row's own `order` (`_covered_by_policy`'s own docstring: the
            # stamp the row carries about itself is never the gate). `info`
            # and `work` differ in nothing here: answering an `info` ask
            # exposes the receiver's knowledge to the sender, so it needs the
            # same human-ratified cover a `work` accept does. Gated on
            # `current["kind"]`, the value THIS fold already decided for the
            # founder row (`_kind_of`'s own authority check), never on the
            # accepted ROW's own fields: an `accepted` event carries no
            # `kind`. A `to_human` ask is already forced to `work` by
            # `_kind_of` and is audience-for-a-person, so no ruling reaches
            # it. `authority == "agent"` explicitly, never `!= "human"`:
            # that looser test also passes for `mechanical` and for a
            # channel string `authority.of_row` does not recognize.
            #
            # The one carve-out is by ORDER, not by kind: an agent `accepted`
            # row on an `info` ask ordered before `RECEIVER_CONSENT_FROM_NS`
            # was written under the old rule and keeps landing, so refolding
            # an existing ledger never rewrites a past verdict.
            covered = False
            covered_before_rule = (
                event == "accepted" and authority == "agent"
                and current["kind"] == "info"
                and current["state"] in _SENDER_MOVABLE
                and _written_before(row, RECEIVER_CONSENT_FROM_NS))
            if (event == "accepted" and authority == "agent"
                    and not current["to_human"]
                    and current["state"] in _SENDER_MOVABLE):
                # #1083: the sender identity a policy must name. A genuinely
                # FOREIGN founder (`recipient_join`'s cross-bucket case)
                # names itself; a LOCAL founder sent to a DIFFERENT project
                # (`listing()`/`sender_join()`'s ordinary "sent abroad"
                # case) falls back to this composer's OWN identity, widened
                # to every alias a pre-migration ruling might still name —
                # never the reverse (an `own_slug` never substitutes for a
                # genuinely foreign `from_slug`).
                #
                # `current["to"] != own_slug` is required, not incidental:
                # a SELF-addressed ask (`to == own_slug`) must stay
                # human-only regardless of any ruling, the same rule
                # `accept()`'s own write boundary already holds (a
                # self-addressed `origin_slug == ""` there returns `None`
                # from `_resolve_covering_ruling` before a dry run ever
                # runs) — the fold must never be more permissive than the
                # verb. Without this qualifier, a project that ratified a
                # ruling naming ITSELF as `sender` (a self-referential
                # grant, the same "agent lowering its own ask's approval
                # bar" pattern `_kind_of` rule 3 already forbids for `info`,
                # generalized here to `work`) could have its own agent
                # accept its own self-addressed ask — a forged raw row the
                # write boundary refuses to ever create honored anyway.
                origin_slug = current["from_slug"]
                if origin_slug:
                    origin_slugs = frozenset({origin_slug})
                elif own_slug and current["to"] != own_slug:
                    origin_slugs = (frozenset({own_slug})
                                    | buckets.aliases_for(own_slug))
                else:
                    origin_slugs = frozenset()
                # #1083: resolved PER RECORD from `current["to"]`'s own
                # ruling ledger when a map was given — never the single flat
                # `policies` set across a whole batch of records that might
                # address different recipients (see `fold`'s own docstring
                # on why unioning would be a cross-recipient authority
                # leak). `recipient_join`/`pending` pass no map: every
                # record they fold is already addressed to exactly one
                # recipient, so the flat set they inject already answers
                # the per-record question.
                row_policies = (
                    policies_by_to.get(current["to"], frozenset())
                    if policies_by_to is not None else policies)
                covered = _covered_by_policy(
                    row, origin_slugs, row_policies, kind=current["kind"])
            if not (covered or covered_before_rule):
                _note_effect(current, row, "needs_human_channel")
                continue
        if event in _STATE_BY_EVENT and current["state"] == "rejected":
            # D6: rejection is terminal for this id. Re-proposal is a NEW
            # record citing `supersedes`, so the rejected verdict stays on
            # the record instead of being overwritten by the next ask.
            _note_effect(current, row, "record_rejected")
            continue
        if event in _STATE_BY_EVENT and current["state"] == "withdrawn":
            # #1346: a withdrawal is final for this id, as a rejection is. The
            # sender opens a new ask with `--supersedes`.
            _note_effect(current, row, "record_withdrawn")
            continue
        if event in ("accepted", "rejected") and current["state"] == "done":
            # #1021: `done` is terminal for a VERDICT row, the same shape the
            # rule above gives `rejected` — a finished record stays finished.
            # Duplicate verdict rows are ordinary (a decide surface replayed
            # after a host restart, a second press), and without this the
            # second `accepted` finds `done_pending` already cleared, falls
            # through to the generic landing and puts the record back on the
            # recipient's owed list. Worse, it also discards the session-end
            # byte-check: `done_verified` below requires `done` or a pending
            # claim, so a record knocked off `done` renders claimed and
            # unverified for good.
            #
            # Only the two verdict events, and deliberately NOT the whole of
            # `_STATE_BY_EVENT`. `needs_info` on a `done` record is a person
            # asking for more, the one deliberate reopen this contract has,
            # and `revised` (handled above, gated on `_SENDER_MOVABLE`) is
            # the sender's own reopen the wedge principle names. Counted in
            # history because the row is real, but `updated_at` stays put for
            # the same reason `surfaced` leaves it alone: a duplicate must
            # not make a settled record sort as freshly updated.
            current["history_count"] += 1
            # #1333: the byte-check is named only while there is something
            # for it to clear. A human `done` was never claimed.
            _note_effect(current, row, "already_done_unverified"
                         if current["done_claimed"] else "already_done")
            continue
        if event == "withdrawn":
            # #1346: the sender takes its own ask back. Not a verdict, so no
            # `verdict_*` field is set, and open to either authority: the
            # fold's role gate above already proved it is the sender's row.
            # A finished request stays finished, and a rejection stays final.
            if current["state"] == "withdrawn":
                _note_effect(current, row, "already_withdrawn")
                continue
            if current["state"] in ("done", "rejected"):
                _note_effect(current, row, "not_withdrawable")
                continue
            current["withdrawn_from"] = current["state"]
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            current["updated_order"] = _row_order(row)
            if current["done_pending"]:
                _supersede_claim(current, claim_meta.pop(q_id, {}), row,
                                 current["revision"])
                _note_effect(current, row, "claim_withdrawn")
            else:
                _note_effect(current, row, "withdrawn")
            current["state"] = "withdrawn"
            current["withdrawn_by"] = authority
            current["withdrawn_at"] = row.get("ts")
            if "note" in row:
                current["note"] = str(row.get("note") or "")
            continue
        if event == "replied":
            # #1117: no state moves, so `continue` BEFORE the generic
            # `_STATE_BY_EVENT` indexing (KeyError on every read otherwise).
            # `append` is public: the reader decides. A dropped row bumps
            # neither `updated_at` (drives every sort) nor `history_count`.
            note = str(row.get("note") or "").strip()
            refusal = ("reply_empty" if not note
                       else "reply_not_permitted"
                       if authority not in ("human", "agent")
                       else "reply_needs_accept"
                       if current["state"] != "accepted"
                       else "reply_from_sender"
                       if _reply_from_sender(current, row, own_slug)
                       else "")
            if refusal:
                _note_effect(current, row, refusal)
                continue
            entry = {
                "ts": row.get("ts"),
                "note": note,
                "channel": row.get("channel"),
                "authority": authority,
                "author": row.get("author"),
                "act_author": row.get("act_author"),
                "event_id": str(row.get("event_id") or ""),
                "revision": _epoch_of(row, current["revision"]),
            }
            evidence = str(row.get("evidence") or "").strip()
            if evidence:  # scar 0042: the ABSENCE of a key is data
                entry["evidence"] = evidence
            current["replies"].append(entry)
            _note_effect(current, row, "reply_recorded")
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            current["updated_order"] = _row_order(row)
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
            # Keyed by the epoch the row was written IN: the stream is ordered,
            # so `current["revision"]` here is the revision at that moment. Old
            # rows therefore replay into their own epoch with no migration.
            current["verdict_surfaced"].setdefault(current["revision"],
                                                   row.get("ts"))
            # #1117: the row may name the reply it showed; the epoch anchor
            # above keeps the FIRST stamp, so expiry never restarts.
            reply_id = str(row.get("reply_event_id") or "").strip()
            if reply_id:
                current["replies_surfaced"].setdefault(reply_id, row.get("ts"))
            continue
        if event == "verdict_delivered":
            # Same posture as `delivered`, sender side. A row that lost its
            # session id is inert rather than epoch-wide, so one malformed row
            # cannot starve every live session of a verdict it never reached.
            current["history_count"] += 1
            session = str(row.get("session") or "").strip()
            if session:
                current["verdict_delivered"].setdefault(current["revision"], {}) \
                    .setdefault(session, row.get("ts"))
                reply_id = str(row.get("reply_event_id") or "").strip()
                if reply_id:  # #1117: per session, like the epoch stamp
                    current["replies_delivered"].setdefault(session, {}) \
                        .setdefault(reply_id, row.get("ts"))
            continue
        if event == "owed_delivered":
            # #885, same posture as `delivered` and `verdict_delivered`: an
            # attention row, counted in history, never moving `updated_at`,
            # and inert rather than epoch-wide when it lost its session id.
            current["history_count"] += 1
            session = str(row.get("session") or "").strip()
            if session:
                current["owed_delivered"].setdefault(current["revision"], {}) \
                    .setdefault(session, row.get("ts"))
            continue
        if event == "withdrawn_delivered":
            # #1346, same posture as `owed_delivered`: an attention row the
            # recipient's live delivery wrote after telling a session the
            # sender withdrew an ask it had accepted. Once per session.
            current["history_count"] += 1
            session = str(row.get("session") or "").strip()
            if session:
                current["withdrawn_delivered"].setdefault(session,
                                                          row.get("ts"))
            continue
        if event == "delivered":
            # Same attention-row posture as `surfaced`: counted in history,
            # never moving `updated_at`. A row that lost its session id is
            # inert rather than epoch-wide — deduping every session at once
            # off one malformed row would silently starve the live sessions
            # it never actually reached.
            current["history_count"] += 1
            session = str(row.get("session") or "").strip()
            if session:
                current["delivered"].setdefault(current["revision"], {}) \
                    .setdefault(session, row.get("ts"))
            continue
        if event == "suppressed":
            current["history_count"] += 1
            # #1333: a second `suppress` finds the record already muted. It
            # still counts in history (the row is real), but it is a no-op
            # and says so rather than reading as a second suppression.
            _note_effect(current, row, "already_suppressed"
                         if current["suppressed"] else "suppressed")
            current["suppressed"] = True
            continue
        if event == "severity_set":
            # #1348: the sender changes how much the ask matters. Not a
            # `revised`: that row moves needs-info back to open, discards a
            # pending claim, uses up a revision and opens a new epoch, and a
            # change of importance does none of those. This moves no state,
            # revision, epoch, claim or recency; the row is counted in history.
            parsed = _severity_of(row)
            if parsed is None or parsed is _UNREADABLE:
                _note_effect(current, row, "severity_unreadable")
                continue
            if current["state"] not in _SENDER_OWED:
                _note_effect(current, row, "severity_settled")
                continue
            current["severity"], current["severity_reason"] = parsed
            current["history_count"] += 1
            _note_effect(current, row, "severity_set")
            continue
        if event == "revised":
            if current["state"] not in _SENDER_MOVABLE:
                _note_effect(current, row, "not_revisable")
                continue
            if current["revision"] >= MAX_REVISIONS:
                # D7: revision 4+ is inert, not a silent success
                _note_effect(current, row, "revision_cap")
                continue
            answered_revision = current["revision"]
            # Scar 0042: replace only the keys the caller actually set. In an
            # append-only stream the ABSENCE of a key is data, so a revise
            # that names only `why` must not clear the ask.
            for key in ("ask", "why", "evidence"):
                if key in row:
                    current[key] = str(row.get(key) or "")
            # #1131: a title key is the sender's intent (scar 0042); an
            # empty one clears it, back to the record with no title.
            if "title" in row:
                title = str(row.get("title") or "")
                if title:
                    current["title"] = title
                else:
                    current.pop("title", None)
            current["revision"] += 1
            current["state"] = "open"
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            current["updated_order"] = _row_order(row)
            if current["done_pending"]:
                # #978 review round 1 (F1), gated per review round 2 (B1): a
                # `revised` row on a record with a PENDING AGENT claim clears
                # the claim, on every revise, not only one that touches
                # `ask`. The event is the sender opening the record back up,
                # and the recipient's claim was scoped to the ask that was
                # live when it was made — a sender revising the ask (a
                # `bump the tag` claim under a since-revised `delete the tag
                # instead` ask), or only softening `why` while the same
                # claim rides along regardless, is the same act from this
                # side of the fold: the ask the claim answered no longer
                # stands as it was. Live delivery already re-nudges the
                # recipient on a revise (it opens a new revision epoch, so
                # `delivered`/`surfaced` restart), so the recipient is told
                # again and can re-answer if the claim still holds.
                #
                # The gate on `done_pending` (not on the event alone) is
                # load-bearing: a human completion is never a claim
                # (`done_pending` is always False for one, set only by the
                # non-human landing branch above), yet a record can reach a
                # `revised` row from a state that carries a human `done`'s
                # evidence — `done` (human) -> `needs_info` (human, allowed
                # from ANY prior state but `rejected`) re-opens the state
                # into `_SENDER_MOVABLE`, and an unconditional clear here
                # erased that human's `done_by`/`done_evidence` on the
                # revise that followed. `done_verified_at` resets alongside
                # the claim it verified (N1): left standing, it would assert
                # "byte-checked" over evidence that no longer exists.
                #
                # #1333: cleared, but never silently. The claim moves to
                # `superseded_claims` with the revision it answered, so the
                # person who decides later can see it existed. Gated on the
                # same `done_pending` as the clear above, for the same
                # reason: a human `done` is not a claim and is not moved.
                _supersede_claim(current, claim_meta.pop(q_id, {}), row,
                                 answered_revision)
                _note_effect(current, row, "claim_discarded",
                             revision=answered_revision)
            else:
                _note_effect(current, row, "revised")
            continue
        if event == "done" and not str(row.get("evidence") or "").strip():
            # D8: `done` is the one either-channel state move, and its price
            # is evidence. A row without it never lands.
            _note_effect(current, row, "done_needs_evidence")
            continue
        if (event == "done" and authority != "human"
                and current["state"] in _SENDER_MOVABLE
                and (current["kind"] == "work"
                     or not _written_before(row, RECEIVER_CONSENT_FROM_NS))):
            # #978: the #961 amendment's own sentence — an agent answering an
            # ask stays owed until a person accepts, rejects, or ratifies a
            # reclassification — was never wired into this fold. Without this
            # branch the row below falls straight through to the generic
            # `_STATE_BY_EVENT` landing and moves `open` (or `needs-info`)
            # directly to `done`, the same one-row escape #972 closed for
            # `kind` itself. An already-`accepted` record is UNCHANGED (its
            # state is outside `_SENDER_MOVABLE`): this branch only narrows
            # what a bare `done` event does for an ask nobody has decided yet.
            #
            # Both kinds: answering an `info` ask exposes the receiver's
            # knowledge, which an agent may only do after an accept that
            # receiver consent covers, so a `done` that skips the accept is a
            # claim until a person decides. An `info` row ordered before
            # `RECEIVER_CONSENT_FROM_NS` was written under the old rule and
            # keeps landing, so a refold never rewrites a past verdict.
            #
            # `history_count`/`updated_at` still bump — a claim is real
            # activity on the record — but `state` does not move, and
            # `suppressed` is left untouched rather than cleared the way a
            # genuine landing below does: a human muted this ask on
            # purpose, and an agent's own unverified claim is not the
            # verdict that reverses it (see `is_stale` for the matching call
            # on staleness — a pending claim never decays either). A second
            # claim overwrites the first, the same latest-wins posture the
            # generic `done` landing already gives `done_evidence`.
            current["history_count"] += 1
            current["updated_at"] = row.get("ts") or current["updated_at"]
            current["updated_order"] = _row_order(row)
            current["done_by"] = authority
            current["done_claimed"] = True
            current["done_evidence"] = str(row.get("evidence") or "")
            current["done_pending"] = True
            claim_meta[q_id] = {"author": row.get("author"),
                                "ts": row.get("ts")}
            _note_effect(current, row, "awaits_person")
            continue
        if event == "done_verified":
            # #694 PR 3 (D8): the session-end byte-check confirmed the
            # agent's `done` evidence quote. Certifies TRANSCRIPTION, not
            # truth — clears the "claimed, unverified" qualifier without
            # moving state or any verdict field. Inert unless the record is
            # CURRENTLY a claimed completion: a stray/duplicate row, one
            # answering a human `done` (never claimed), or one that arrived
            # before any `done` at all changes nothing.
            #
            # #978: a completion claim the fold has not yet landed as
            # `done` (`done_pending`) is exactly as verifiable as one that
            # has — the byte-check runs against the evidence quote, not
            # against whether a person has accepted it — so it qualifies
            # here too, on the same terms, without moving `state` or
            # `done_pending` itself.
            if current["done_claimed"] and (
                    current["state"] == "done" or current["done_pending"]):
                current["history_count"] += 1
                current["done_claimed"] = False
                current["done_verified_at"] = row.get("ts")
                _note_effect(current, row, "claim_verified")
            else:
                _note_effect(current, row, "no_claim_to_verify")
            continue
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        current["updated_order"] = _row_order(row)
        # #1333: decided here, from the state BEFORE the move, because the
        # move below is what erases the difference between an accept and an
        # accept that ratified a claim, and a first needs-info from a repeat.
        if event == "accepted":
            landed = "claim_ratified" if current["done_pending"] else "accepted"
        elif event == "needs_info":
            landed = ("needs_info_again" if current["state"] == "needs-info"
                      else "needs_info")
        elif event == "done":
            landed = "done" if authority == "human" else "done_claimed"
        else:
            landed = "rejected"
        _note_effect(current, row, landed)
        if event == "accepted" and current["done_pending"]:
            # #978: an accept landing on a completion claim ratifies the
            # claim itself, not a to-do still to be started — the record
            # settles as `done`, not `accepted`, carrying both the claim
            # fields (already on the record, untouched below) and this
            # verdict's own fields (set in the `else` branch beneath).
            current["state"] = "done"
        else:
            current["state"] = _STATE_BY_EVENT[event]
        # D5: a verdict (or a completion) supersedes a suppression — that IS
        # the reversal path, which is why no `unsuppress` verb exists.
        current["suppressed"] = False
        if event == "done":
            current["done_by"] = authority
            current["done_act_author"] = row.get("act_author")
            # An agent's completion claim renders as claimed-and-unverified
            # until the session-end byte-check confirms the quote (PR 3).
            current["done_claimed"] = authority != "human"
            current["done_evidence"] = str(row.get("evidence") or "")
            current["done_pending"] = False
        else:
            current["verdict_by"] = authority
            current["verdict_act_author"] = row.get("act_author")
            current["verdict_label"] = CHANNEL_LABEL.get(
                str(row.get("channel") or ""))
            current["verdict_at"] = row.get("ts")
            if event == "accepted":
                # #961 slice 3: derived from THIS row's own authority, same
                # as `verdict_by` above — never from a stored field, so a
                # pre-existing (human-only-era) accepted record replays
                # through this exact check and reads "human" with no
                # legacy branch needed.
                current["accepted_by"] = "human" if authority == "human" \
                    else "agent"
                # #961 slice 4: which ruling authorized THIS accept. Reached
                # here only when the row already passed the fold's own
                # coverage gate above, so an agent accept at this point is
                # either covered by a ruling or an `info` accept ordered
                # before receiver consent began (which carries none and
                # reads None). None for a human accept too.
                current["accepted_under"] = (
                    str(row.get("under_ruling") or "") or None
                    if authority == "agent" and (
                        current["kind"] == "work"
                        or not _written_before(row, RECEIVER_CONSENT_FROM_NS))
                    else None)
            if "note" in row:
                current["note"] = str(row.get("note") or "")
            # #978: `accepted`/`rejected` settle the claim along with the
            # ask — `done_pending` clears either way, the record is no
            # longer awaiting THIS decision. `needs_info` is deliberately
            # exempt: the person asked for more before deciding, and the
            # claim already on file still stands until they do.
            if event != "needs_info":
                current["done_pending"] = False
    return out


def _policies_by_to(rows: list[dict],
                    reads: PolicyReads) -> dict[str, frozenset]:
    """#1083: `request_policy_history` resolved PER RECIPIENT — keyed by the
    literal `to` a founder `opened` row in `rows` carries — never a single
    project-wide read injected as one flat set across a whole batch of
    records that might address DIFFERENT recipients. Mirrors `pending.
    _foreign_request_counts`'s own per-recipient cache (pending.py:355-365)
    exactly: one read per distinct `to`, and `_covered_by_policy`'s policy
    tuples carry no recipient field, so mixing two recipients' rulings into
    one shared set would let recipient A's ruling validate an accept
    addressed to unrelated recipient B — a real cross-recipient authority
    leak, not a cosmetic gap. `records()`, `listing()`, and `sender_join()`
    are the callers: `recipient_join` and `pending` fold rows already
    addressed to exactly one recipient (themselves), so a single flat
    `FoldInputs.policies` already answers the per-record
    question for them and this map is not needed there.

    A `to` that is itself a pre-migration alias is resolved forward by
    `PolicyReads.of`, the one owner of that resolution (the cache here is
    still keyed by the raw `to`, matching what the fold gate looks up with
    `current["to"]`). Any read error or missing ledger reads as
    `frozenset()` for that recipient — fail toward more scrutiny, never
    less, the posture `_foreign_request_counts` and `request_policy_
    history` itself already hold."""
    cache: dict[str, frozenset] = {}
    for row in rows:
        if row.get("event") != "opened":
            continue
        to = str(row.get("to") or "")
        if not to or to in cache:
            continue
        cache[to] = reads.of(to)
    return cache


def records(project_dir=None, *, rows=None, policy=None) -> dict[str, dict]:
    # `rows` hands in the raw rows of a `jsonl.read` of this bucket's ledger
    # and `policy` the `request_policy_history` of its ruling ledger, both
    # from the view's single read of the bucket (#1132 PR 10a). Without them
    # each is read here.
    #
    # #1083: a local, per-bucket fold — every founder here has no
    # cross-bucket sender to name, so every row is stamped "" (`_stamped`),
    # overwriting whatever a planted `_origin_slug` a disk row might
    # otherwise carry. `policies_by_to` is passed for shape parity with
    # `listing()`/`sender_join()` (harmless: the only `to` this composer
    # ever sees is a SELF-addressed one, and no `own_slug` is passed here,
    # so `_covered_by_policy`'s local-founder fallback never activates — a
    # self-addressed accept must stay human-only, the same rule `accept()`'s
    # own write boundary already holds; `fold`'s own `to != own_slug`
    # comment on the gate has the full reasoning). Never `own_slug`: unlike
    # `listing()`/`sender_join()`, every accept this composer could ever
    # fold IS the self-addressed case (a genuinely foreign accept never
    # lands in this project's own file at all), so there is no legitimate
    # use for the fallback here at all.
    stamped = _stamped(events(project_dir=project_dir, rows=rows), "")
    return fold(stamped, fold_inputs(
        stamped, perspective=Perspective.LOCAL, project_dir=project_dir,
        policy=policy))


def get(request_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(request_id)


def listing(project_dir=None, *, min_severity=None) -> list[dict]:
    """`listed(project_dir).rows`: the listing for the callers that have no
    use for the notes. See `listed`."""
    return listed(project_dir, min_severity=min_severity).rows


def listed(project_dir=None, *, min_severity=None) -> "Inbox":
    """Every record in this bucket, undecided first — the `request list`
    order, and the notes of the cross-bucket read behind it. Suppressed records are HERE by construction (D5): suppression
    takes away panel placement, never visibility.

    #798: read through the sender join, not the bucket-local fold. A request's
    rows span two buckets — `opened` here, the verdict in the recipient's — so
    folding only this bucket reported every ask this project SENT as `open`
    forever after it was decided, while `inbox` and the verdict panel (which do
    join) reported it correctly. The panel's own overflow line points here.
    Local suppression survives the join; the recipient's never crosses it.

    #1083: folded with no `policies` at all until this fix, the ONE fold
    call site of the three `_sender_rows`-fed/cross-bucket composers that
    injected none. Fixed with `policies_by_to` (PER RECORD, keyed by each
    record's own recipient — this composer's own rows can address several
    DIFFERENT recipients at once, so a single flat set would either match
    nothing or, unioned across recipients, leak one recipient's ruling onto
    an ask addressed to another) rather than a flat `_request_policy_
    history(project_dir)` — that call resolves THIS project's own ledger,
    never the recipient's, where a covering ruling for an ask THIS project
    SENT actually lives. `own_slug` lets a record SENT ABROAD (`to` names a
    DIFFERENT project) match its own local founder through `_covered_by_
    policy`'s fallback — never a self-addressed record (`to == own_slug`),
    which `fold`'s own gate excludes regardless: that must stay human-only,
    the same rule `accept()`'s write boundary already holds for it."""
    folded, notes = _sent_fold(project_dir)
    return Inbox(_listing_order(folded.values(), min_severity), notes)


def _sent_fold(project_dir) -> tuple[dict[str, dict], tuple]:
    """The sender-join fold `listed` renders: every request this project
    sent, with its recipient's verdict joined in, and the read's notes. The
    ONE fold behind every sender-side read and decision (#1299), so what a
    verb checks and what `request list` shows cannot disagree."""
    grouped = _sender_group(project_dir)
    rows = [row for group in grouped.by_id.values() for row in group]
    inputs = fold_inputs(rows, perspective=Perspective.SENDER,
                         project_dir=project_dir)
    return (_sender_perspective(fold(rows, inputs), inputs.own_slug),
            grouped.notes)


# The one line a sender-side surface prints for a claim whose text is withheld.
WITHHELD_CLAIM_LINE = ("Done: a completion is pending the recipient's accept "
                       "(the answer shows once they accept)")


def _sender_perspective(folded: dict[str, dict], own_slug: str) -> dict:
    """The sender's view of a fold (#1339): a completion claim the recipient's
    person has not accepted yet is not the sender's to read.

    For an `info` ask the accept IS the recipient's consent to share what that
    project knows, so the claim's text stays out of every record a sender-side
    composer (`listed`, `sent`, `sent_record`) hands to a surface. The ONE place
    this is decided: the renderers, the JSON listing and the panels all read
    these records and none of them needs its own `if sender`. The record keeps
    `done_pending` and gains `done_evidence_withheld`, so a surface says "a
    completion is pending" instead of guessing from an empty string. A claim a
    revision discarded was never accepted either, so it is withheld the same
    way. Once a person accepts, `done_pending` clears and the text shows
    unchanged. A SELF-addressed ask has no other party: the sender is the
    recipient and keeps the text (the `to == own_slug` test the fold uses for
    the same question)."""
    for record in folded.values():
        if record["to"] == own_slug:
            continue
        if record["done_pending"]:
            record["done_evidence"] = ""
            record["done_evidence_withheld"] = True
        for claim in record["superseded_claims"]:
            claim["evidence"] = ""
            claim["withheld"] = True
    return folded


def written_effect_line(request_id: str, event_id: str, project_dir=None,
                        record: dict | None = None) -> str:
    """The line a write verb prints for the row it just wrote (#1333): the
    fold's own word for that row's effect, or "" when no fold this project can
    read holds the row. `record` is the one the verb already rendered; when it
    cannot see the row (the bucket-local fold misses a verdict that lives in
    another bucket, the sender join misses an answer this project wrote to a
    foreign ask) the other two composers are asked in turn, so the cross-bucket
    path gets the same line as the local one."""
    for found in (record,
                  lambda: get(request_id, project_dir=project_dir),
                  lambda: recipient_join(project_dir=project_dir).get(
                      request_id),
                  lambda: sent_record(request_id, project_dir=project_dir)):
        candidate = found() if callable(found) else found
        effect = ((candidate or {}).get("row_effects") or {}).get(event_id)
        if effect:
            return effect_line(request_id, effect)
    return ""


def sent_record(request_id: str, project_dir=None) -> dict | None:
    """One request this project sent, as `request list` renders it: through
    the sender join, so the recipient's verdict is in it. `get` is the
    bucket-local fold and cannot see that verdict (#1299)."""
    return _sent_fold(project_dir)[0].get(request_id)


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
    rows.sort(key=_recency,
              reverse=True)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def recipient_in_scope(to: str, own_slug: str) -> bool:
    """Whether a caller whose bucket is `own_slug` may address an ask to `to`.
    Always, off tenant scope. On a tenant-scoped home (DAIMON_TENANT_SCOPED)
    only its own bucket and the host-declared DAIMON_EXTRA_READ_SLUGS: the
    same scope every read surface holds, so a tenant cannot address another
    tenant's project, whose agent would then answer from that tenant's
    knowledge."""
    if not config.tenant_scoped():
        return True
    return to == own_slug or to in config.extra_read_slugs()


def open_request(*, to: str, ask: str, why: str, channel: str,
                 blocking: bool = False, to_human: bool = False,
                 evidence: str = "", supersedes: str = "",
                 kind: str = DEFAULT_KIND, author: str | None = None,
                 title: str = "", severity: str = DEFAULT_SEVERITY,
                 severity_reason: str = "", project_dir=None) -> str:
    """Open a request addressed to another project's slug.

    Slug SHAPE is validated here; whether it names a bucket that exists is
    the CLI's check — a project that has not serialized yet has no bucket,
    and refusing to record the ask at the module seam would make the ledger
    unusable exactly when a team is onboarding.

    #961: `kind` is the approval requirement, assigned once, here, by the
    SENDER. Only a person may assign `info` — an agent lowering its own
    ask's approval bar is exactly the assertion the wedge principle forbids
    — so this is enforced at this library boundary, not only in the CLI a
    caller could otherwise route around. `--to-human` is audience, not
    approval requirement, and can never be paired with `info`: a person is
    always the one who reads it.

    #961 slice 5: an AGENT channel MAY assign `kind="info"` when a
    human-ratified `verb=open` ruling in THIS project's OWN ledger covers
    `to` — resolved the same two-phase way `accept()`'s agent path resolves
    a `verb=accept` grant (H2 review fix): the row that is about to be
    written is stamped with the covering `(ruling_id, policy_sha256)` and
    re-checked against the order-aware `request_policy_history` BEFORE
    `append` ever runs, so a current-state grant that does not actually
    cover this row's own order (clock skew against the machine that
    ratified it) is refused here rather than landing a row the fold goes on
    to silently treat as `work` forever. The row that lands is the SAME
    stamped row the dry run checked, never re-stamped afterward.

    #1026: `author` names the person behind THIS act, for an in-process
    writer serving several people through one channel. Refused on a channel
    that derives its author from the environment (`_act_author`), so a
    shell-out can never sign an ask as somebody else.

    #1131: `title` is an optional few-word headline the sender sets. It is
    validated here, once: whitespace collapsed, redacted, and REFUSED past
    `TITLE_CHARS` (as an over-long ask is; a cut title is words the sender
    did not write). `supersedes` does not inherit one: the replacement
    carries its own.

    #1348: `severity` is how much the outcome matters to the sender, from
    `SEVERITIES`; any level but `normal` needs a one-line `severity_reason`
    (refused past `SEVERITY_REASON_CHARS`, never cut) and `normal` takes none.
    Validated here, once. Either channel may set it: it lowers no approval
    bar. A `normal` request writes exactly the row it always did, and
    `supersedes` does not carry a severity over.
    """
    project_dir = config.resolve_project_dir(project_dir)
    if scope.parse_bucket(str(to or "")) is None:
        raise RequestError(f"invalid recipient slug: {to!r}")
    supersedes = str(supersedes or "").strip()
    if supersedes and not is_request_id(supersedes):
        raise RequestError(f"invalid superseded request id: {supersedes!r}")
    # #961 review finding 5: an unknown channel is a channel-shaped problem,
    # not a kind-shaped one. `_stamp` re-checks this too (the row is not
    # written until it runs), but that happens after every check below, so a
    # caller with a bogus channel and `kind="info"` would otherwise be told
    # about `info`'s human-only rule rather than about the typo.
    if channel not in known_channels("requests"):
        raise RequestError(
            f"channel must be one of: {', '.join(sorted(known_channels('requests')))}")
    if kind not in KINDS:
        raise RequestError(f"kind must be one of: {', '.join(sorted(KINDS))}")
    # `--to-human` stays refused unconditionally and first, before any
    # ruling resolution runs — audience, never approval requirement, and no
    # ruling reaches it (unchanged from before this slice).
    if to_human and kind == "info":
        raise RequestError(
            "--to-human addresses that project's person, which is always "
            "the work approval requirement; it cannot be opened as kind "
            "info")
    slug = store.project_slug(project_dir)
    if not slug:
        raise RequestError("project unknown; requests are recorded in the "
                           "sender's own bucket")
    if not recipient_in_scope(to, slug):
        raise RequestError(config.TENANT_SCOPE_REFUSAL)
    ask = _scrub("ask", ask)
    why = _scrub("why", why)
    evidence = _scrub("evidence", evidence, required=False)
    title = _scrub("title", title, required=False, limit=TITLE_CHARS)
    severity, severity_reason = _validated_severity(severity, severity_reason)
    order = clock.now_ns()
    q_id = make_id(slug, ask, why, _ts(order))
    if get(q_id, project_dir=project_dir) is not None:
        raise RequestError(
            f"{q_id} already exists — this exact ask was opened in the same "
            "second; revise it or wait a moment to open a second one")
    row = _stamp("opened", q_id, channel, now_ns=order, author=author)
    row.update({
        "to": to,
        "to_human": bool(to_human),
        "blocking": bool(blocking),
        "kind": kind,
        "ask": ask,
        "why": why,
        "from_label": _sender_label(project_dir),
    })
    if evidence:
        row["evidence"] = evidence
    if title:
        row["title"] = title
    if severity != DEFAULT_SEVERITY:
        row["severity"] = severity
        row["severity_reason"] = severity_reason
    if supersedes:
        row["supersedes"] = supersedes
    if kind == "info" and of_channel("requests", channel) != "human":
        authority = of_channel("requests", channel)
        covering = None
        skew = False
        if authority == "agent":
            covering = _resolve_covering_open_policy(to, project_dir)
            if covering is not None:
                ruling_id, sha = covering
                row["under_ruling"] = ruling_id
                row["policy_sha256"] = sha
                history = refutations.request_policy_history(
                    project_dir=project_dir)
                if not _covered_by_open_policy(row, history):
                    covering = None
                    skew = True
                    row.pop("under_ruling", None)
                    row.pop("policy_sha256", None)
        if covering is None:
            detail = (
                " — an active ruling grants this recipient coverage right "
                "now, but the write would land outside the interval that "
                "ruling has been active for (commonly a clock skew between "
                "this machine and the one that ratified it); retry once "
                "the clocks agree, or"
                if skew else "; open it as kind work (the default), run "
                "this from a human channel, or have a human ratify a "
                "`verb=open` ruling naming this recipient, so")
            raise RequestError(
                "kind info lowers this request's own approval requirement, "
                "and only the sender's human channel may assign it"
                f"{detail} this call arrived through {channel!r}")
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError(
            "request not written (daimon disabled, project unknown, or "
            "ledger unwritable)")
    return q_id


def _resolve_covering_open_policy(to: str, project_dir):
    """#961 slice 5: the `(ruling_id, policy_sha256)` of an active
    `verb=open` ruling in THIS project's OWN ledger that lets its own agent
    open an ask to `to` as `kind="info"`, or None. Mirrors `_resolve_
    covering_ruling` exactly — current-state (`active_request_policies`),
    the write boundary's own question; the dry run in `open_request` re-
    checks the exact stamped row against the order-aware `request_policy_
    history` before ever appending it."""
    if not to:
        return None
    for entry in refutations.active_request_policies(project_dir=project_dir):
        sender, entry_to, kind, verb, by, ruling_id, sha = entry
        if (entry_to == to and kind == "info" and verb == "open"
                and by == "agent"):
            return ruling_id, sha
    return None


def revise(request_id: str, *, channel: str, ask: str | None = None,
           why: str | None = None, evidence: str | None = None,
           author: str | None = None, title: str | None = None,
           project_dir=None) -> str:
    """Answer a needs-info, or sharpen an open ask. Capped at MAX_REVISIONS.

    Returns the `event_id` of the row written (#1333), the key the fold's
    `row_effects` names that row's effect under. Every write verb below does
    the same.

    #1131: `title=None` keeps the title (a revise that sharpens the ask does
    not touch the headline); a value replaces it; `""` clears it. Same
    key-presence rule as the other fields."""
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
    row = _stamp("revised", request_id, channel, author=author)
    # Scar 0042: only the keys the caller SET. `None` means unchanged, and
    # the fold reads key presence as intent — forging a key here would clear
    # the field the caller never mentioned.
    if ask is not None:
        row["ask"] = _scrub("ask", ask)
    if why is not None:
        row["why"] = _scrub("why", why)
    if evidence is not None:
        row["evidence"] = _scrub("evidence", evidence, required=False)
    if title is not None:
        row["title"] = _scrub("title", title, required=False,
                              limit=TITLE_CHARS)
    if not any(key in row for key in ("ask", "why", "evidence", "title")):
        raise RequestError(
            "revision changes nothing; provide a new ask, why, evidence, "
            "or title")
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError("revision not written")
    return row["event_id"]


def withdraw(request_id: str, *, reason: str, channel: str,
             author: str | None = None, project_dir=None) -> str:
    """#1346: the sender takes its own ask back. Either channel, because the
    sender retracting what it asked is the sender's act, not a verdict: an
    agent whose own follow-up write failed must be able to say so, and
    `reject` was only ever the recipient's word. The reason is required and
    travels in `note`, which the recipient reads.

    Moves open, needs-info, accepted and pending-claim asks to the terminal
    `withdrawn`. A request that is already done, rejected or withdrawn
    changes nothing and the refusal says which. Returns the row's `event_id`
    like every write verb."""
    why = _text("note", reason)
    record = _sent_or_refuse(request_id, "withdraw", project_dir)
    if record["state"] in ("done", "rejected", "withdrawn"):
        raise RequestError(
            f"{request_id} is {record['state']}; "
            + ("a finished request is not withdrawn"
               if record["state"] == "done"
               else "a rejection is final"
               if record["state"] == "rejected"
               else "a withdrawal is already on record")
            + f"; open a new request with `--supersedes {request_id}` to "
            "ask again")
    row = _stamp("withdrawn", request_id, channel, author=author)
    row["note"] = why
    if not append(row, project_dir=project_dir, writer=Writer.HUMAN):
        raise RequestError("withdrawal not written")
    return row["event_id"]


def _sent_or_refuse(request_id: str, act: str, project_dir) -> dict:
    """The request this project SENT, as `request list` renders it, or the
    refusal a sender-only act (`withdraw`, `severity`) gives: one for an ask
    that was sent TO this project (only its sender may), one for an id nobody
    knows."""
    record = sent_record(request_id, project_dir=project_dir)
    if record is None:
        if (is_request_id(request_id)
                and recipient_join(project_dir=project_dir).get(request_id)):
            raise RequestError(
                f"{request_id} was sent to this project, and only its sender "
                f"can {act} it; to decline it run `daimon request reject`")
        raise RequestError(f"unknown request: {request_id}")
    return record


def set_severity(request_id: str, level: str, *, reason: str = "",
                 channel: str, author: str | None = None,
                 project_dir=None) -> str:
    """#1348: the sender changes how much its own ask matters, through its own
    event and not `revise` (a `revised` row would move needs-info back to
    open, discard a pending claim, use a revision and re-deliver the ask live,
    and none of that is a change of importance). Either channel: severity
    lowers no approval bar.

    Allowed while the sender is still owed something (`_SENDER_OWED`: open,
    needs-info, accepted); a done, rejected or withdrawn request keeps the
    severity it had. `normal` clears a raised level. A change that changes
    nothing is refused, as `revise` refuses one. Returns the row's `event_id`
    like every write verb."""
    level, reason = _validated_severity(level, reason)
    record = _sent_or_refuse(request_id, "change its severity", project_dir)
    if record["state"] not in _SENDER_OWED:
        raise RequestError(
            f"{request_id} is {record['state']}; a settled request keeps "
            "the severity it had")
    if (level, reason) == (record["severity"], record["severity_reason"]):
        raise RequestError(f"{request_id} already has that severity; "
                           "severity changes nothing")
    row = _stamp("severity_set", request_id, channel, author=author)
    row["severity"] = level
    if reason:
        row["severity_reason"] = reason
    if not append(row, project_dir=project_dir, writer=Writer.HUMAN):
        raise RequestError("severity not written")
    return row["event_id"]


def _answering(request_id: str, project_dir, event: str) -> dict | None:
    """The local record a recipient-side row answers, or None when this
    bucket cannot see it.

    Shape-checked, never resolved. A request lives in the SENDER's bucket, so
    the recipient answering one has nothing local to resolve against: an
    answer to a foreign id is written here and stays an orphan — inert in
    this bucket's fold, visible in the raw audit — until the read-time join
    (PR 2) pairs it with its origin. Refusing what cannot be resolved would
    make the recipient side unimplementable.
    """
    if not is_request_id(request_id):
        raise RequestError(f"invalid request id: {request_id!r}")
    current = get(request_id, project_dir=project_dir)
    # #1346: a record found in THIS bucket was sent by this project. The role
    # table the fold reads says whether `event` may be written from the
    # sender's side, and refusing here, with the verb that does fit, beats
    # writing a row the fold would only mark inert.
    if current is not None and not row_holds_role(
            EVENT_ROLE[event], origin="", from_slug=current["from_slug"],
            to=current["to"], home_slugs=_home_slugs(project_dir)):
        raise RequestError(
            f"{request_id} is a request this project sent, and "
            f"{_EVENT_VERB.get(event, event)} is the recipient's call; to "
            "take your own ask back run `daimon request withdraw "
            f"{request_id} --reason \"...\"`")
    return current


# What the sender-side refusal calls each recipient act.
_EVENT_VERB = {"accepted": "accepting", "rejected": "rejecting",
               "needs_info": "asking for more", "done": "closing it",
               "suppressed": "suppressing"}


def _require(request_id: str, project_dir) -> dict:
    """The sent record as `request list` renders it (sender join), or a
    refusal — for the SENDER-side verbs, which cannot act without one (a
    revision needs the ask it is replacing and the revision count it is
    bounded by) and must decide on the recipient's verdict too (#1299)."""
    current = sent_record(request_id, project_dir=project_dir)
    if current is None:
        raise RequestError(f"unknown request: {request_id}")
    return current


def _write_verdict_row(event: str, request_id: str, channel: str, note: str,
                       project_dir, row: dict | None = None,
                       author: str | None = None, **stamp) -> str:
    """`stamp` (#961 slice 4) carries `under_ruling`/`policy_sha256` for a
    ruling-covered agent accept — a truthy extra becomes a row field, an
    absent or falsy one is never written at all, the same "only what the
    caller actually set" contract `revise` already holds this ledger to
    elsewhere (an empty string on the row would be indistinguishable from
    one a hand-edited row lost).

    `row` (#961 slice 4 review round 2, H2): the EXACT already-stamped row a
    caller dry-ran through the fold before deciding to write, so the row
    that lands carries the identical `order` the dry run checked coverage
    against — never a second `_stamp()` call minting a fresh order after
    the decision was already made on a different one. `None` (every other
    caller) stamps fresh here, unchanged.

    `author` (#1026) reaches the fresh stamp only. The one caller that hands
    in a `row` is the agent ruling-coverage path in `accept`, whose channel
    can never carry a per-act author in the first place — `_act_author`
    refuses it there before `accept` does anything at all."""
    row = dict(row) if row is not None else _stamp(event, request_id, channel,
                                                   author=author)
    note = _text("note", note, required=False)
    if note:
        row["note"] = note
    for key, value in stamp.items():
        if value:
            row[key] = value
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError("verdict not written")
    return row["event_id"]


def _verdict(event: str, request_id: str, *, channel: str, note: str = "",
             author: str | None = None, project_dir=None) -> str:
    if of_channel("requests", channel) != "human":
        state = _STATE_BY_EVENT.get(event, event)
        article = "an" if state[:1] in "aeiou" else "a"
        raise RequestError(
            f"{article} {state} verdict requires a human channel; this call "
            f"arrived through {channel!r}")
    current = _answering(request_id, project_dir, event)
    if current is not None and current["state"] == "rejected":
        raise RequestError(
            f"{request_id} was rejected, and a rejection is final for that "
            "record; the sender can open a new request with "
            f"`--supersedes {request_id}`")
    return _write_verdict_row(event, request_id, channel, note, project_dir,
                              author=author)


def _resolve_covering_ruling(sender: str, kind: str, project_dir):
    """#961 slice 4: the `(ruling_id, policy_sha256)` of an active ruling
    that lets THIS project's agent accept an ask of `kind` (`info` or `work`)
    from `sender`, or None. Reads this project's OWN rulings — the write boundary and the
    fold gate are asymmetric only in WHEN each runs, not in what each
    checks: both resolve through `refutations.active_request_policies`, so a
    ruling that stops covering (revised narrower, retired) refuses a NEW
    accept at both boundaries the same way #943's check binding already
    does for a hash mismatch."""
    if not sender:
        return None
    for entry in refutations.active_request_policies(project_dir=project_dir):
        # #961 slice 5: widened by one field (`to`, index 1) — unpacked and
        # ignored here.
        entry_sender, _to, entry_kind, verb, by, ruling_id, sha = entry
        if (entry_sender == sender and entry_kind == kind
                and verb == "accept" and by == "agent"):
            return ruling_id, sha
    return None


def accept(request_id: str, *, channel: str, note: str = "",
           author: str | None = None, project_dir=None) -> str:
    """Land the addressed request as accepted.

    Human-only for an ask nothing covers, as every verdict verb has always
    been. An agent channel may record THIS one verdict for an addressed ask
    (`info` or `work`, not `to_human`, which is always audience-for-a-person
    and never agent-acceptable regardless of any ruling) still `open` or
    `needs-info` when an active `verb=accept` ruling of THIS project covers
    the ask's own sender for the ask's own kind. For `info` that ruling is the
    receiving project's consent to expose what it knows; the same predicate
    (`_covered_by_policy`) decides it here and in the fold.
    Checked HERE, at the write boundary, so a caller gets a clear refusal
    naming the human command instead of a silently inert row; the fold
    (`_HUMAN_ONLY`'s own exception) re-checks the identical rule
    independently, because `requests.append` is public and a caller skipping
    this function could otherwise mint the row directly.

    Resolved through `recipient_join` when the local per-bucket fold misses,
    not only `_answering`'s bucket-local `get` — the ORDINARY case this
    exists for is a project accepting a FOREIGN ask, which has no local
    `opened` row (the same blind spot #978's `_report`/`request done`
    guidance already worked around: requests.py:490-514 in that PR). A
    local-only check would refuse every legitimate cross-bucket agent
    accept while doing nothing for a forged local one, exactly backwards.
    Paid only on the non-human path — the in-process human writer's cost is
    unchanged.

    #961 slice 4 review round 2 (H2): the `work` branch no longer trusts
    `_resolve_covering_ruling`'s CURRENT-STATE answer on its own. It stamps
    the row it is about to write, then dry-runs the identical predicate the
    fold will apply later — `_covered_by_policy` against `request_policy_
    history`'s order-aware intervals — before ever calling `append`. A
    current-state grant that would not actually cover THIS row's own order
    is refused HERE, with a message, instead of landing a row the fold goes
    on to silently treat as inert forever. The one way this fires in
    practice is clock skew: this machine's `time.time_ns()` behind the
    machine that ratified the ruling, so the accept's own stamped `order`
    falls before the interval's `active_from`. The row that finally lands
    is the SAME stamped row the dry run checked — never re-stamped with a
    fresh order after the decision, which would reopen the identical gap.

    #1026: `author` is gated HERE, and this is the ONE verb that needs its
    own gate. Every other writer reaches `_stamp`, which refuses a per-act
    author on a channel that derives one — but the agent path below writes
    through `_write_verdict_row(row=synthetic)`, which by construction never
    stamps, so `_stamp` is not on that path at all. Without this line an
    agent caller naming a person would land a row that silently drops the
    name and records the process instead: no refusal, no error, a verdict
    attributed to the wrong identity. Gated before the joins and the ruling
    resolution too, so the refusal costs none of that work.
    """
    # The gate, not the value: the human path hands `author` on to
    # `_verdict` -> `_stamp`, which is what actually writes it.
    _act_author(channel, author)
    authority = of_channel("requests", channel)
    if authority != "human":
        # #961 slice 3 review item 6: `authority == "agent"` explicitly,
        # never `!= "human"` alone — that looser test also lets a
        # `mechanical` channel (real, valid, just never named by this
        # contract) or an unrecognized channel string (`authority is
        # None`, also `!= "human"`) through to the checks below, and a
        # caller reaching THAT point can land the accept. A channel this
        # contract never named must never be able to claim the one it did.
        if authority != "agent":
            raise RequestError(
                "an accepted verdict requires a human channel; this call "
                f"arrived through {channel!r}")
        current = _answering(request_id, project_dir, "accepted")
        if current is None:
            current = recipient_join(project_dir=project_dir).get(
                request_id)
        covered = False
        stamp = {}
        synthetic = None
        skew = False
        origin_slug = ""
        kind = str((current or {}).get("kind") or "")
        if (current is not None and current.get("state") in _SENDER_MOVABLE
                and not current.get("to_human")):
            # One rule for both kinds: the receiving project's own ruling
            # must cover this sender (`info` ask: answering it exposes this
            # project's knowledge, so the project's person consents first).
            if kind in KINDS:
                origin_slug = str(current.get("from_slug") or "")
                covering = _resolve_covering_ruling(
                    origin_slug, kind, project_dir)
                if covering is not None:
                    ruling_id, sha = covering
                    # The dry run: the EXACT row about to land, checked
                    # against the EXACT predicate (`_covered_by_policy`) and
                    # the EXACT order-aware history (`request_policy_
                    # history`) the fold will apply on every later re-fold —
                    # never `active_request_policies` alone again.
                    synthetic = _stamp("accepted", request_id, channel)
                    synthetic["under_ruling"] = ruling_id
                    synthetic["policy_sha256"] = sha
                    history = refutations.request_policy_history(
                        project_dir=project_dir)
                    # #1083: `_covered_by_policy` widened its second
                    # argument from a single `origin_slug` string to a SET
                    # of candidate identities (own-slug/alias fallback for a
                    # local founder) — this call site never needs that
                    # fallback (a self-addressed `origin_slug == ""` already
                    # returns `None` from `_resolve_covering_ruling` above,
                    # before `synthetic`/`covering` are ever set, so this
                    # line only ever runs for a genuinely foreign sender),
                    # but it still has to pass the new shape, or `sender in
                    # origin_slug` would silently do SUBSTRING matching on a
                    # bare string instead of set membership.
                    if _covered_by_policy(
                            synthetic, frozenset({origin_slug}), history,
                            kind=kind):
                        covered = True
                        stamp = {"under_ruling": ruling_id,
                                "policy_sha256": sha}
                    else:
                        skew = True
        if not covered:
            if skew:
                detail = (
                    " — an active ruling grants this sender coverage right "
                    "now, but the write would land outside the interval "
                    "that ruling has been active for (commonly a clock skew "
                    "between this machine and the one that ratified it); "
                    "retry once the clocks agree, or")
            elif kind == "info" and origin_slug:
                detail = (
                    "; an agent accepting an info ask exposes this "
                    "project's knowledge to "
                    f"{(current or {}).get('from_label') or origin_slug}, and only "
                    "this project's human may consent to that: have a human "
                    "ratify a `verb=accept` `kind=info` `by=agent` ruling "
                    f"naming sender {origin_slug}, or")
            elif origin_slug:
                detail = (
                    "; an agent may accept an ask only when an active "
                    "ruling of this project covers it: have a human ratify "
                    f"a `verb=accept` `kind={kind}` `by=agent` ruling "
                    f"naming sender {origin_slug}, or")
            else:
                detail = (
                    "; an agent may accept only an addressed ask that is "
                    "still open or needs-info and that an active ruling of "
                    "this project covers for its sender, so")
            raise RequestError(
                "an accepted verdict requires a human channel; this call "
                f"arrived through {channel!r}{detail} this ask needs "
                f"`daimon request accept {request_id}` from a human "
                "channel")
        return _write_verdict_row("accepted", request_id, channel, note,
                                  project_dir, row=synthetic, **stamp)
    return _verdict("accepted", request_id, channel=channel, note=note,
                    author=author, project_dir=project_dir)


def reject(request_id: str, *, channel: str, note: str = "",
           author: str | None = None, project_dir=None) -> str:
    return _verdict("rejected", request_id, channel=channel, note=note,
                    author=author, project_dir=project_dir)


def needs_info(request_id: str, *, channel: str, note: str = "",
               author: str | None = None, project_dir=None) -> str:
    return _verdict("needs_info", request_id, channel=channel, note=note,
                    author=author, project_dir=project_dir)


def suppress(request_id: str, *, channel: str, note: str = "",
             author: str | None = None, project_dir=None) -> str:
    """Drop a record out of the briefing panel — human-only, panel-only.

    Not a verdict and not a state: the record stays in `request list`, and
    any later accept/reject/needs-info lands normally and reverses this.
    Human-only because an agent able to mute an addressed ask would hold a
    soft-reject that never appears as one.
    """
    if of_channel("requests", channel) != "human":
        raise RequestError(
            "suppressing an addressed request requires a human channel; this "
            f"call arrived through {channel!r}")
    _answering(request_id, project_dir, "suppressed")
    row = _stamp("suppressed", request_id, channel, author=author)
    note = _text("note", note, required=False)
    if note:
        row["note"] = note
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError("suppression not written")
    return row["event_id"]


def done(request_id: str, *, channel: str, evidence: str,
         author: str | None = None, project_dir=None) -> str:
    """Report the ask as satisfied. Either channel, evidence required — an
    agent's claim renders as claimed-and-unverified until the session-end
    byte-check confirms the quote (PR 3).

    #1026: `author` names the person who reported it, and only a channel
    with a per-act identity may carry one — an agent's claim is signed by
    the process that made it, as it always was. Refused by `_stamp` below,
    which is the one chokepoint every row on this path passes through."""
    current = _answering(request_id, project_dir, "done")
    if current is not None and current["state"] == "rejected":
        raise RequestError(
            f"{request_id} was rejected; a rejected request cannot be "
            "completed")
    row = _stamp("done", request_id, channel, author=author)
    row["evidence"] = _scrub("evidence", evidence)
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError("completion not written")
    return row["event_id"]


def _reply_refusal(request_id: str, state: str) -> str:
    """#1117: why a reply was refused, naming the verb that carries text there."""
    hints = {
        "open": (f"accept it first with `daimon request accept {request_id} "
                 f"--note ...`, or report it satisfied with `daimon request "
                 f"done {request_id} --evidence ...`"),
        "needs-info": (f"the sender is waiting on you; `daimon request "
                       f"needs-info {request_id} --note ...` carries more "
                       "text, and `accept --note` takes it on"),
        "rejected": ("a rejected request takes no more text; the sender "
                     "opens a new one"),
        "done": "it is already closed; `done --evidence` carried the answer",
        "withdrawn": ("the sender withdrew it, and a withdrawal is final; "
                      "there is nothing left to report on"),
    }
    return (f"{request_id} is {state}, and a reply only attaches to an "
            f"accepted request: {hints.get(state, 'wait for the accept')}")


def reply(request_id: str, note: str, evidence: str | None = None, *,
          channel: str, author: str | None = None, project_dir=None) -> str:
    """#1117: send the sender a progress note on a request this project has
    ACCEPTED, without moving its state. Either channel. The state check goes
    through `recipient_join`, never `_answering` (bucket-local, scar 0056);
    the fold re-checks every rule because `append` is public. Fresh stamp
    (scar 0090), records the epoch it answers. Note first, so an empty note
    is the refusal a caller sees."""
    text = _scrub("note", note)
    proof = _scrub("evidence", evidence, required=False)
    if not is_request_id(request_id):
        raise RequestError(f"invalid request id: {request_id!r}")
    record = recipient_join(project_dir=project_dir).get(request_id)
    if record is None:
        if get(request_id, project_dir=project_dir) is not None:
            raise RequestError(
                f"{request_id} is a request this project sent; reply is a "
                "recipient verb, so only the project it was sent to can "
                "reply. To add to your own ask, use `daimon request revise`")
        raise RequestError(f"unknown request: {request_id}")
    if record["state"] != "accepted":
        raise RequestError(_reply_refusal(request_id, record["state"]))
    row = _stamp("replied", request_id, channel, author=author)
    row["note"] = text
    if proof:
        row["evidence"] = proof
    row["revision"] = record["revision"]
    if not append(row, project_dir=project_dir,
                  writer=Writer.HUMAN):
        raise RequestError("reply not written")
    return row["event_id"]


def verify_done(request_id: str, *, role: str, project_dir=None) -> bool:
    """Record the session-end byte-check outcome for an agent's `done`
    evidence quote (channel `mechanical`), written to THIS bucket — where
    the `done` row it verifies lives (#694 PR 3, D8).

    No `get()` gate, unlike `amendments.verify`: a `done` row this pass
    verifies may be an ORPHAN in this bucket's own per-bucket fold above (a
    foreign ask this project answered as the recipient — its `opened` row
    lives in the sender's bucket). The raw row is what matters; the fold's
    `done_verified` handling and the composer's read-through are what make
    the flag meaningful to whichever side is watching. The role is the
    transcript speaker the quote was found under, bounded to _ROLE_MAX —
    verification certifies transcription, not truth, and the render never
    drops that qualifier just because a role was recorded."""
    row = _stamp("done_verified", request_id, "mechanical")
    row["evidence_role"] = _text("role", role)[:_ROLE_MAX]
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def plaintext_values(row: dict) -> list[str]:
    """Every plaintext value this row carries, in declaration order.

    The forget TARGETING pool reads this instead of hand-copying the field
    tuple — the same one-declaration discipline row_content_keys below gives
    the deleter and the auditor."""
    return surfaces.prose_values(surfaces.bucket_ledger(_LEDGER).prose, row,
                                 scalars_only=True)


def row_content_keys(row: dict) -> set[str]:
    """Canonical keys for every plaintext field this row carries (#645).

    The one reader of the registry's `prose` column besides plaintext_values, so the
    deleter below and `privacy.audit_project` cannot drift apart about what
    counts as plaintext on this surface."""
    return {normalize.content_key(value) for value in surfaces.prose_values(
        surfaces.bucket_ledger(_LEDGER).prose, row)}


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

    def drop(line, row):
        if (isinstance(row, dict)
                and str(row.get("request_id") or "") in doomed):
            return None
        return line

    # #1138: "\n"-only split, unparseable lines kept verbatim (jsonl.rewrite).
    try:
        if not jsonl.rewrite(path, drop):
            return []
    except OSError:
        return []
    return sorted(doomed)


# ---- PR 2: the join composer -----------------------------------------------
#
# Verdict/suppress/done rows land in the RECIPIENT's own bucket, referencing
# a foreign request id — orphans in that bucket's per-bucket fold above
# (`fold`'s own docstring: "here a lifecycle row whose `opened` lives in
# another bucket is an orphan"). The composer is the read-time join that
# pairs them with their origin (D0), in both directions: `sender_join` (a
# sender's own asks, joined with whatever their recipient decided) and
# `recipient_join` (everything addressed to this project, scanning every
# other known bucket for the origin row). Both reduce to the same move: read
# the raw rows of the two buckets a record spans, and hand the union to
# `fold` — the per-bucket fold above already knows what to do with an
# `opened` row and a same-id verdict row that arrive from different buckets,
# because it keys everything by `request_id` alone.


def _without_suppression(rows: list[dict]) -> list[dict]:
    """D5's named composer filter: drop `suppressed` rows before folding, so
    a sender-side join never learns its own ask was muted from the
    recipient's panel attention — it reads as still open/undecided instead,
    then goes stale on the normal schedule (PR 3)."""
    return [row for row in rows if row.get("event") != "suppressed"]


def _foreign_requests(slug: str):
    """`(read, skip, degraded)` for another bucket's requests ledger, through
    the one foreign seam (`store.foreign_ledger`): the registry's foreign
    column leaves out a ledger that cannot be proven and reads around one
    with only torn lines, saying so."""
    got = store.foreign_ledger(slug, "requests.jsonl")
    return got.read, got.skip, got.degraded


def _sender_rows(project_dir) -> dict[str, list]:
    """`_sender_group(project_dir).by_id`; see it."""
    return _sender_group(project_dir).by_id


def _sender_group(project_dir) -> Join:
    """This bucket's rows, plus the recipient's rows for every request it SENT,
    grouped by request id. The traversal both sender-side surfaces share (#798).

    Foreign suppression is dropped HERE, not by the callers: a `suppressed` row
    in the recipient's bucket is that project's own panel-attention housekeeping
    and must never cross the join (D5). Local rows are returned untouched, so a
    record THIS project suppressed keeps its visibility.

    #895: the verdict is not necessarily in the recipient's bucket. A human
    decides from whatever directory they are standing in, and the row lands
    THERE, so every bucket with a ledger is read once and its rows for the
    ids this project sent abroad are joined. A project with nothing sent
    abroad (only self-addressed asks, or only answers to foreign asks) reads
    its own bucket and nothing else.

    #1083: every row is stamped `_origin_slug` before it is grouped
    (`_stamped`) — local rows get `""`, a foreign recipient's rows get that
    recipient's own slug — overwriting whatever a planted value a disk row
    might otherwise carry. `recipient_join` already stamped its own foreign
    branch; this composer, and its local branch, did not, until this fix."""
    # #948: one resolution, shared with the CLI. Everything below keys
    # on the project, so a caller standing in a subdir must not answer
    # for a bucket of its own.
    sender_slug = store.project_slug(config.resolve_project_dir(project_dir))
    if not sender_slug:
        return Join({}, ())
    by_id: dict[str, list] = {}
    abroad: set[str] = set()
    for row in _stamped(events(project_dir=sender_slug), ""):
        rid = str(row.get("request_id") or "")
        by_id.setdefault(rid, []).append(row)
        if row.get("event") == "opened":
            to_slug = str(row.get("to") or "")
            if to_slug and to_slug != sender_slug:
                abroad.add(rid)
    if not abroad:
        return Join(by_id, ())
    skipped = degraded = 0
    for slug in _bucket_slugs():
        if slug == sender_slug:
            continue  # already covered by this bucket's own rows above
        read, skip, torn = _foreign_requests(slug)
        if skip:
            skipped += 1   # #1132 PR 10a: said, never read as "decided nothing"
            continue
        degraded += torn
        foreign = [row for row in _stamped(events(project_dir=slug,
                                                  rows=read.rows), slug)
                   if str(row.get("request_id") or "") in abroad]
        for row in _without_suppression(foreign):
            by_id[str(row.get("request_id") or "")].append(row)
    return Join(by_id, _skipped_notes(skipped, degraded))


def sender_join(project_dir=None) -> dict[str, dict]:
    """`sent(project_dir).by_id`: the records, for the callers that have no
    use for the notes. See `sent`."""
    return sent(project_dir).by_id


def sent(project_dir=None) -> Join:
    """Every request this project SENT, joined with whatever its recipient
    decided (D0) — the per-bucket `records()` above only ever sees this
    project's OWN rows, and a verdict lives in the recipient's bucket.
    Suppression is filtered before the fold runs (D5), local rows included:
    this feeds the verdict PANEL, and suppression is exactly panel attention.

    #1083: `policies_by_to`/`own_slug`, the same per-recipient resolution
    `listing()` now uses (its own docstring has the full reasoning) — this
    composer shares `_sender_rows`'s traversal and can fold requests
    addressed to several different recipients in one call."""
    grouped = _sender_group(project_dir)
    rows = _without_suppression(
        [row for group in grouped.by_id.values() for row in group])
    inputs = fold_inputs(rows, perspective=Perspective.SENDER,
                         project_dir=project_dir)
    return Join(_sender_perspective(fold(rows, inputs), inputs.own_slug),
                grouped.notes)


def _bucket_slugs() -> list[str]:
    """Every project bucket that has ever written a request, scanning
    `checkpoints/*/requests.jsonl` directly rather than `store.list_buckets()`
    (which requires a checkpoint pointer): a project can send or answer a
    request before it has ever serialized a session, and such a bucket must
    still be discoverable. One `exists()` stat per subdir, so a project with
    checkpoints but no requests ledger costs almost nothing."""
    return [child.name for child in store.bucket_dirs().buckets
            if (child / "requests.jsonl").exists()]


def _skipped_notes(skipped: int, degraded: int = 0) -> tuple:
    """`sender-skipped` for `skipped` buckets left out of a cross-bucket
    read and `sender-degraded` for `degraded` ones read around, or nothing."""
    scoped = config.tenant_scoped()
    out = []
    if skipped:
        out.append(display.sender_skipped_note(skipped, scoped))
    if degraded:
        out.append(display.sender_degraded_note(degraded, scoped))
    return tuple(out)


class Join(NamedTuple):
    """`join`'s answer: the folded records addressed to this project and the
    notes the read carries (`sender-skipped`, worded by `display`)."""
    by_id: dict
    notes: tuple


class Inbox(NamedTuple):
    """`inbox`'s answer: the records in `request inbox` order and the notes."""
    rows: list
    notes: tuple


def recipient_join(project_dir=None) -> dict[str, dict]:
    """`join(project_dir).by_id`: the records addressed TO this project, for
    the callers that have no use for the notes. See `join`."""
    return join(project_dir).by_id


def join(project_dir=None) -> Join:
    """Every request addressed TO this project, joined across every bucket
    that might hold its origin (D0) — this project's own bucket holds only
    the verdict/attention rows it wrote, orphaned in its own per-bucket fold
    above; the `opened`/`revised` rows live in whichever bucket sent the ask.
    Scans every bucket with a requests ledger (`_bucket_slugs`). Cost is the
    PR 2 deliverable measured and budgeted separately.

    This project's OWN bucket also holds every request it SENT (its own
    `opened` rows), and those must never read as its own inbox — only a
    LOCAL `opened` row whose `to` names this project (self-addressed) or a
    verdict/attention row with NO local `opened` row at all (an orphan
    answering a foreign ask, D0's other half) belongs here.

    Every folded record carries a transient `from_slug` — the bucket whose
    `opened` row founded it — so a supersedes reference can be resolved back
    to its origin (`supersedes_label`) without a second scan.

    #895: a verdict does not necessarily live in this bucket either. A human
    decides from whatever directory they are standing in, so the row can sit
    in a THIRD bucket that holds no `opened` for the id: an orphan in that
    bucket's own fold. The scan already reads every bucket; it now keeps such
    orphan groups when the id is one addressed here. A bucket's rows for an
    id this project is not party to are still discarded, and a row claiming
    an agent channel is still refused by the fold's authority re-check, so
    widening the read adds no write reach anywhere."""
    # #948: one resolution, shared with the CLI. Everything below keys
    # on the project, so a caller standing in a subdir must not answer
    # for a bucket of its own.
    my_slug = store.project_slug(config.resolve_project_dir(project_dir))
    if not my_slug:
        return Join({}, ())
    # #963: an ask minted before 0.42.0 is addressed to the LITERAL-path slug
    # this project used to have, and `to` is the whole recipient join. Without
    # the alias every such ask silently leaves the inbox the day the bucket
    # moves — the ask is still open, still owed, and nobody is shown it.
    # `mine` is the recipient identity everywhere below, never `my_slug`
    # alone; the aliases come from the migration receipt, so a project that
    # never migrated pays one absent-file read and gets the set it had.
    mine = {my_slug} | set(buckets.aliases_for(my_slug))
    # #1083: stamp `_origin_slug` on every local row too, before this
    # project's own bucket is even filtered into inbox vs. outgoing —
    # overwrites whatever a planted value a disk row might otherwise carry,
    # the same invariant `_sender_rows` now holds for the opposite
    # direction. `""` is the local convention `_founder_by_id` reads.
    own_by_id: dict[str, list] = {}
    for row in _stamped(events(project_dir=my_slug), ""):
        own_by_id.setdefault(str(row.get("request_id") or ""), []).append(row)
    by_id: dict[str, list] = {}
    for rid, rows in own_by_id.items():
        opened = next((r for r in rows if r.get("event") == "opened"), None)
        if opened is not None and str(opened.get("to") or "") not in mine:
            continue  # this project's own OUTGOING ask — never its own inbox
        by_id[rid] = rows
    origin_of: dict[str, str] = {}
    orphans: list[tuple[str, list]] = []
    skipped = degraded = 0
    for slug in _bucket_slugs():
        # A bucket this project MOVED OUT OF is not a foreign sender. Left
        # standing by a merge that could not empty it, its rows would
        # otherwise come back labeled as asks from a stranger.
        if slug in mine:
            continue
        # #1132 PR 10a: a sender whose ledger cannot be proven is SKIPPED with
        # a code, never read as "sent nothing". The registry's foreign column
        # decides (a torn ledger is read around, an unreadable one is left
        # out), so the verb and the panel cannot disagree.
        foreign, skip, torn = _foreign_requests(slug)
        if skip:
            skipped += 1
            continue
        degraded += torn
        # #961 slice 4, widened by #1083: stamp the origin bucket onto every
        # row read from THIS foreign bucket, at read time — covers both the
        # `elif` branch below and an orphan row (a verdict recorded in a
        # THIRD bucket, #895), which used to reach the merged set unstamped.
        # `fold`'s own `_founder_by_id` pre-pass reads it back off the
        # founder row. `_origin_slug` is transient, in-memory only (never
        # part of what `append`/`events` persist or read), the same posture
        # `events()` already gives `_line`.
        grouped: dict[str, list] = {}
        for row in _stamped(events(project_dir=slug, rows=foreign.rows), slug):
            grouped.setdefault(str(row.get("request_id") or ""), []).append(row)
        for rid, rows in grouped.items():
            opened = next((r for r in rows if r.get("event") == "opened"),
                         None)
            if opened is None:
                orphans.append((rid, rows))  # decided elsewhere, maybe ours
            elif str(opened.get("to") or "") in mine:
                by_id.setdefault(rid, []).extend(rows)
                origin_of[rid] = slug
    for rid, rows in orphans:
        if rid in origin_of:
            by_id[rid].extend(rows)
    all_rows = [row for group in by_id.values() for row in group]
    # #961 slice 4 review round 2 (C1): `from_slug` is no longer set here
    # from `origin_of` — that dict is keyed by whichever bucket LAST
    # satisfied "has an opened row addressed to mine" while iterating
    # `_bucket_slugs()`, an order with no relationship to which `opened`
    # row is the genuine, shape-valid founder (or even to `order` at all).
    # `fold` itself now resolves origin from the SAME shape-checked,
    # order-sorted founder pre-pass it uses for `kind` (`_founder_by_id`)
    # and writes it onto the record as `from_slug` directly, so this
    # composer reads it back rather than computing a second, disagreeing
    # answer. `origin_of` still does its original, narrower job just above:
    # deciding whether an id is addressed to `mine` at all, so a verdict
    # recorded in a THIRD bucket with no local `opened` row (#895) still
    # gets included — a stray match there can add at most an inert orphan
    # row to the merged set, never mislabel who a record is from, now that
    # only the founder resolution decides that.
    #
    # #1083: no `policies_by_to` map needed here, unlike `listing()`/
    # `sender_join()` — every record this composer ever folds is already
    # addressed to exactly one recipient (this project itself, by
    # construction: the `to in mine` filter above), so the single flat
    # `_request_policy_history(project_dir)` already answers the
    # per-record question. No `own_slug` either: the only LOCAL founder
    # (`from_slug == ""`) this composer can ever fold is a self-addressed
    # ask (`to == my_slug`), and that must stay human-only regardless of
    # any ruling, the same rule `accept()`'s own write boundary already
    # holds for it — `fold`'s own gate comment on `to != own_slug` has the
    # full reasoning. A genuinely FOREIGN founder already carries its own
    # non-empty `from_slug`, stamped above, and needs no fallback at all.
    #
    # #961 slice 5: `open_policies` is the opposite direction from
    # `policies` above — a `verb=open` ruling lives in the SENDER's own
    # bucket, never this project's. One entry per DISTINCT foreign origin
    # `origin_of` already named (resolved ONCE per bucket, not once per
    # row — the same discipline `request_policy_history` itself needed
    # after the H3 fix), plus `""` for the rare self-addressed case (this
    # project's own agent opening an `info` ask to itself), pointed at this
    # project's own ledger the same way `_open_policies_local` reads it.
    return Join(fold(all_rows, fold_inputs(
        all_rows, perspective=Perspective.RECIPIENT, project_dir=project_dir)),
        _skipped_notes(skipped, degraded))


def inbox(project_dir=None, *, min_severity=None) -> Inbox:
    """Every request addressed to this project, undecided first — the
    `request inbox` order, and the notes of the join that produced it.
    Suppressed records are HERE by construction (D3/D5): suppression takes
    away panel placement, never visibility. #1348: severity sorts between
    undecided-first and recency, and `min_severity` keeps that level and
    anything more severe."""
    got = join(project_dir)
    return Inbox(_listing_order(got.by_id.values(), min_severity), got.notes)


def inbox_listing(project_dir=None, *, min_severity=None) -> list[dict]:
    """`inbox(project_dir).rows`: the listing for the callers that have no use
    for the notes (anamnesis iterates it in-process)."""
    return inbox(project_dir, min_severity=min_severity).rows


def _deserves_attention(record: dict, project_dir=None) -> bool:
    """The one predicate deciding whether an addressed ask still deserves
    AMBIENT attention: undecided, unsuppressed, not stale. Named once
    because it has two consumers — the decision panel (`decision_
    renderable`, #961 slice 3 narrowed this from `inbox_renderable`) and
    live delivery (`deliverable`, #756) — and the design's whole claim is
    that delivery is a second door onto the record the panel would have
    rendered. The day the two filters disagree, one of them is nudging
    about an ask the other already decided was not worth attention."""
    return (record["state"] in _SENDER_MOVABLE and not record["suppressed"]
            and not is_stale(record, project_dir=project_dir))


def _is_owed(record: dict, project_dir=None) -> bool:
    """#885: whether an addressed ask is work this project has AGREED to and
    not yet closed. The recipient-side twin of `_deserves_attention`, and the
    reason the two are separate functions rather than one widened frozenset.

    Suppression still reaches here — it is the human's one lever over ambient
    placement, and an accepted ask ignoring it would make `suppress` the only
    verb the owed lane refuses. Staleness deliberately does NOT: `is_stale`
    measures an ask AWAITING a decision decaying out of attention, and an
    accepted one is not waiting on anybody, it is owed. Work does not expire
    by being ignored. `is_stale` already returns False for every state
    outside `_SENDER_MOVABLE`; not calling it says so at this level too."""
    return record["state"] in _RECIPIENT_OWED and not record["suppressed"]


def owed_renderable(project_dir=None) -> dict:
    """#885: the recipient-side OWED panel data: {"rows": [...],
    "overflow": N} — asks this project accepted and has not closed with
    `done`, newest first, capped at RENDER_CAP with the remainder COUNTED.

    Deliberately a second panel rather than more rows in `inbox_renderable`:
    these are not a decision the human owes, they are work the project owes,
    and the verb differs. An undecided ask offers accept and reject; an
    accepted one offers `done`. Mixing them would ask the reader to sort out
    which is which from the state glyph alone."""
    got = join(project_dir)
    rows = [r for r in got.by_id.values()
            if _is_owed(r, project_dir=project_dir)]
    _attention_sorted(rows)
    return _with_notes({"rows": rows[:RENDER_CAP],
                        "overflow": max(0, len(rows) - RENDER_CAP)},
                       got.notes)


def _with_notes(entry: dict, notes: tuple) -> dict:
    """A renderable's entry with the join's notes, present only when there
    is something to say (a sender left out is said, never a silently shorter
    list)."""
    if notes:
        entry["notes"] = notes
    return entry


def needs_owed_delivered_stamp(record: dict, session: str) -> bool:
    """#885: whether THIS session has already been nudged about this OWED
    ask this revision epoch. Its own namespace, never `delivered`: see the
    fold's `owed_delivered` comment for why sharing that key silently
    swallows every ask the session saw before it was accepted."""
    epoch = (record.get("owed_delivered") or {}).get(record.get("revision")) or {}
    return str(session or "").strip() not in epoch


def stamp_owed_delivered(request_id: str, session: str,
                         project_dir=None) -> bool:
    """Write an `owed_delivered` row to THIS project's own bucket (the
    RECIPIENT's), recording that the live surface nudged this owed ask into
    `session`. Channel `mechanical`, same posture as `stamp_delivered`: the
    render pipeline stamped what it did, nobody asserted anything."""
    session = str(session or "").strip()
    if not session:
        raise RequestError("owed_delivered stamp requires a session id")
    row = _stamp("owed_delivered", request_id, "mechanical")
    row["session"] = session
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def needs_withdrawn_delivered_stamp(record: dict, session: str) -> bool:
    """#1346: whether THIS session was already told the sender withdrew this
    ask. Write-once per (request id, session); a withdrawal is final, so there
    is no revision epoch to key by."""
    return (str(session or "").strip()
            not in (record.get("withdrawn_delivered") or {}))


def stamp_withdrawn_delivered(request_id: str, session: str,
                              project_dir=None) -> bool:
    """Write a `withdrawn_delivered` row to THIS project's own bucket (the
    RECIPIENT's), recording that the live surface told `session` the sender
    withdrew an ask this project had accepted. Channel `mechanical`, the same
    posture as `stamp_owed_delivered`: the surface stamped what it showed."""
    session = str(session or "").strip()
    if not session:
        raise RequestError("withdrawn_delivered stamp requires a session id")
    row = _stamp("withdrawn_delivered", request_id, "mechanical")
    row["session"] = session
    return append(row, project_dir=project_dir, writer=Writer.EMITTER)


def withdrawn_deliverable(session: str, project_dir=None) -> dict:
    """#1346: the asks this project had ACCEPTED that the sender then
    withdrew, which `session` has not yet been told about. `{"rows": [...],
    "overflow": N}`.

    Only an accepted ask needs the notice: one that was merely open or
    waiting on the sender never became this project's work, so it leaves the
    decision lane quietly. An accepted ask would leave the owed lane with no
    word, and an agent mid-work would keep going. It is told for
    `VERDICT_PANEL_SESSIONS` serialized sessions past the withdrawal, then the
    notice expires (the record stays readable in `request inbox`). No session
    id means no dedup key, so it returns nothing."""
    session = str(session or "").strip()
    if not session:
        return {"rows": [], "overflow": 0}
    rows = [r for r in recipient_join(project_dir=project_dir).values()
            if r["state"] == "withdrawn" and r["withdrawn_from"] == "accepted"
            and needs_withdrawn_delivered_stamp(r, session)
            and store.sessions_since_count(
                r["withdrawn_at"], project_dir) < VERDICT_PANEL_SESSIONS]
    rows.sort(key=_recency, reverse=True)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def owed_deliverable(session: str, project_dir=None) -> dict:
    """#885: the asks this project has ACCEPTED and not closed that `session`
    has not yet been nudged with. `{"rows": [...], "overflow": N}`.

    Ordering, cap, dedup-before-cap and the counted remainder all follow
    `deliverable` deliberately, so a live nudge and the next brief agree
    about which owed work matters. No session id means no dedup key, and
    re-nudging every turn forever is worse than not nudging."""
    session = str(session or "").strip()
    if not session:
        return {"rows": [], "overflow": 0}
    rows = [r for r in recipient_join(project_dir=project_dir).values()
            if _is_owed(r, project_dir=project_dir)
            and needs_owed_delivered_stamp(r, session)]
    _attention_sorted(rows)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def inbox_renderable(project_dir=None) -> dict:
    """Every kind of recipient-side attention data: {"rows": [...],
    "overflow": N} — requests addressed to this project still awaiting a
    decision, newest first, capped at RENDER_CAP with the remainder
    COUNTED. Suppressed AND stale records are filtered here and only here
    (mirrors `renderable` above, extended by D3): that is the entire effect
    suppress/staleness is allowed to have on this data — both stay fully
    visible in `inbox_listing`.

    #961 slice 3: no longer the decision panel's own composer — that is
    `decision_renderable`, which narrows this same `_deserves_attention`
    filter by excluding `kind == "info"` before its cap. This function has
    no production caller left as of that slice; kept because it is still
    the correct "every kind, capped, unfiltered by approval requirement"
    shape, and several tests exercise it directly."""
    rows = [r for r in recipient_join(project_dir=project_dir).values()
            if _deserves_attention(r, project_dir=project_dir)]
    _attention_sorted(rows)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def decision_renderable(project_dir=None) -> dict:
    """#961 slice 3: the human DECISION view of the recipient panel —
    `inbox_renderable` narrowed to asks that actually owe a person a
    decision. `{"rows": [...], "overflow": N}`.

    Every ask that waits on this project's person, `info` included: an
    `info` ask is answered by the recipient's agent only after an accept its
    own person consented to (an active `verb=accept` ruling covering the
    sender), so until one lands it is a decision a human owes. Composed
    from `_deserves_attention`, the predicate it shares with live delivery
    (`deliverable`).

    The cap counts only rows that could actually appear."""
    got = join(project_dir)
    rows = [r for r in got.by_id.values()
            if _deserves_attention(r, project_dir=project_dir)]
    _attention_sorted(rows)
    return _with_notes({"rows": rows[:RENDER_CAP],
                        "overflow": max(0, len(rows) - RENDER_CAP)},
                       got.notes)


def deliverable(session: str, project_dir=None) -> dict:
    """#756: the asks addressed to this project that `session` has not yet
    been delivered THIS revision epoch — the panel's own filter, narrowed by
    what this one session has already seen. `{"rows": [...], "overflow": N}`.

    Ordering and cap follow the panel deliberately (newest first, RENDER_CAP)
    so a live nudge and the next brief agree about which asks matter. The
    dedup filter runs BEFORE the cap: capping first would let three
    already-delivered asks hide a fourth that nobody has seen.

    #800: the remainder is COUNTED, the same shape and for the same reason as
    `renderable()` above — silently dropping an addressed ask is the one
    failure this feature cannot have, and this path inherited the cap without
    the signal that made it safe. The count is taken AFTER the dedup filter,
    so it never reports asks this session has already been shown.

    No session id means no dedup key, and re-nudging every turn forever is
    worse than not nudging — so it returns nothing rather than everything."""
    session = str(session or "").strip()
    if not session:
        return {"rows": [], "overflow": 0}
    rows = [r for r in recipient_join(project_dir=project_dir).values()
            if _deserves_attention(r, project_dir=project_dir)
            and needs_delivered_stamp(r, session)]
    _attention_sorted(rows)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


# ---- #694 PR 3: D3 — consumption-based expiry, derived, never written -----

# Recipient side: an open/needs-info record goes `stale` once this many
# distinct non-introspection RECIPIENT sessions have serialized past the
# surfaced anchor with no verdict. Named, not the baton's inlined 2
# (store.sessions_since_count's threshold is caller-chosen).
STALE_AFTER_SESSIONS = 3
# Sender side: a decided record (accepted/rejected/needs-info/done) leaves
# the sender's verdict PANEL after this many distinct non-introspection
# SENDER sessions have serialized past THIS EPOCH's `verdict_surfaced`
# stamp (#803). The record
# itself never changes — only ambient panel placement.
VERDICT_PANEL_SESSIONS = 2
# States the sender-side verdict panel surfaces: everything settled enough
# for the sender to be told about it. "open" is deliberately absent — an
# ask nobody has acted on yet has nothing to report.
_VERDICT_STATES = frozenset({"needs-info", "accepted", "rejected", "done"})


def is_stale(record: dict, project_dir=None) -> bool:
    """D3 recipient side: True once an open/needs-info record's surfaced
    anchor — the first `surfaced` ts of its LATEST revision epoch — has aged
    past STALE_AFTER_SESSIONS distinct non-introspection sessions serialized
    for `project_dir` (the RECIPIENT's own bucket) with no verdict landing
    in between. Never surfaced -> no anchor -> never decays. Every other
    state is a permanent fact — a verdict or a completion never goes stale.

    #978: a `done_pending` record never decays either, even though its
    `state` is still `open`/`needs-info` — the staleness clock exists to
    catch an ask nobody looked at, and this one WAS looked at: the
    recipient's agent already answered with a completion claim, and what it
    is waiting on now is a person's accept or reject, not the recipient
    going quiet.

    Both kinds anchor on `surfaced`: an `info` ask waits in the same decision
    panel a `work` ask does (receiver consent), so the card a person was shown
    is the attention the clock measures."""
    if record.get("state") not in _SENDER_MOVABLE:
        return False
    if record.get("done_pending"):
        return False
    anchor = (record.get("surfaced") or {}).get(record.get("revision"))
    if not anchor:
        return False
    return store.sessions_since_count(anchor, project_dir) >= STALE_AFTER_SESSIONS


# ---- #1281: wall-clock overdue, the sender's side of liveness --------------



def _ts_seconds(ts) -> float | None:
    """A ledger `ts` (`_ts`'s UTC second format) as epoch seconds, or None
    when it does not parse. A ledger line is editable on disk, so a bad one
    reads as unknown rather than raising out of a render."""
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def last_movement_at(record: dict) -> str | None:
    """The ts of the last thing that MOVED this record: the fold keeps it in
    `updated_at`, which opened, revised, accepted, needs-info, replied and a
    done claim all advance, and which attention stamps (surfaced, delivered)
    and a duplicate verdict deliberately leave alone."""
    return record.get("updated_at") or None


def age_seconds(record: dict, now_ns: int | None = None) -> float | None:
    """Seconds since `last_movement_at`, None when that is unknown. `now` is
    the injected clock (scar 0016), never the wall clock directly."""
    moved = _ts_seconds(last_movement_at(record))
    if moved is None:
        return None
    now = clock.now_ns() if now_ns is None else int(now_ns)
    return max(0.0, now / 1_000_000_000 - moved)


def is_overdue(record: dict, now_ns: int | None = None) -> bool:
    """#1281: True when the sender is still owed something on this record
    (open, needs-info or accepted) and nothing has moved it for
    `config.request_overdue_days()`. Derived on every read, never written, and
    independent of any recipient session: the clock alone advances it.

    A record whose last movement cannot be read is NOT called overdue: the
    flag asserts an age, and an unknown one is not one."""
    if record.get("state") not in _SENDER_OWED:
        return False
    age = age_seconds(record, now_ns)
    return age is not None and age >= config.request_overdue_days() * 86_400


def liveness(record: dict, now_ns: int | None = None) -> dict:
    """The three derived fields `request list --json` adds to a row."""
    now = clock.now_ns() if now_ns is None else int(now_ns)
    return {"last_movement_at": last_movement_at(record),
            "age_seconds": age_seconds(record, now),
            "overdue": is_overdue(record, now)}


def render_state(record: dict, project_dir=None) -> str:
    """The state LABEL for `request list`/`inbox` (D3): `stale` in place of
    `open`/`needs-info` once `is_stale`, else the record's real state.
    Recomputed fresh on every call — `stale` is never written to disk."""
    return "stale" if is_stale(record, project_dir=project_dir) \
        else str(record.get("state") or "open")


# #1117: the panel is skeleton the trimmer cannot drop: one capped line.
_REPLY_LINE_MAX = 160


def latest_reply_line(record: dict) -> str:
    """#1117: the one line the panel and the live nudge spend on a record's
    replies ("" when none). Label keyed on AUTHORITY, never the channel string
    (the `done (claimed, unverified)` convention); a reply older than the
    record's revision says so; earlier replies are counted, not printed."""
    replies = record.get("replies") or []
    if not replies:
        return ""
    latest = replies[-1]
    note = display.shorten(latest.get("note"), _REPLY_LINE_MAX, hard=True)
    label = ("Reply (agent, unverified)" if of_row("requests", latest) == "agent"
             else "Reply")
    line = f"{label}: {note}"
    if latest.get("revision", 0) < record.get("revision", 0):
        line += f" (on revision {latest.get('revision', 0)})"
    if len(replies) > 1:
        line += f" (+{len(replies) - 1} earlier)"
    return line


def needs_verdict_surfaced_stamp(record: dict) -> bool:
    """D1, sender side: whether a `verdict_surfaced` row already exists for
    this record's CURRENT revision epoch — the same key the recipient's
    `surfaced` stamp uses (#803).

    It was write-once per RECORD, on the premise that a verdict is terminal
    once it lands. `needs-info` is a verdict state and is not terminal, so a
    record can carry a verdict, a revise, and then a second verdict; keying
    per record meant the second one was never owed a look."""
    return record.get("revision") not in (record.get("verdict_surfaced") or {})


def stamp_verdict_surfaced(request_id: str, project_dir=None, *,
                           reply_event_id: str | None = None) -> bool:
    """Write a `verdict_surfaced` row to THIS project's own bucket (the
    SENDER's) — its brief observed and rendered the verdict card. Channel
    `mechanical`, same posture as `stamp_surfaced`. Write-once is the
    caller's job (`needs_verdict_surfaced_stamp` first) — the fold's
    earliest-wins tie-break absorbs a concurrent-brief duplicate for free."""
    row = _stamp("verdict_surfaced", request_id, "mechanical")
    if reply_event_id:  # #1117: rides on the same event, only when set
        row["reply_event_id"] = str(reply_event_id)
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def unseen_reply_id(record: dict, session: str | None = None) -> str | None:
    """#1117: the event id of the record's LATEST reply when the asking
    surface has not shown it yet, else None (`session=None` asks the brief, a
    session id asks live delivery). The OR clause that lets a late reply back
    into a panel its record expired out of: expiry measures the decision."""
    replies = record.get("replies") or []
    if not replies:
        return None
    latest = str(replies[-1].get("event_id") or "")
    if session is None:
        seen = record.get("replies_surfaced") or {}
    else:
        seen = (record.get("replies_delivered") or {}).get(
            str(session).strip()) or {}
    return None if latest in seen else latest


def verdict_panel_expired(record: dict, project_dir=None) -> bool:
    """D3 sender side: True once VERDICT_PANEL_SESSIONS distinct non-
    introspection sessions have serialized for `project_dir` (the SENDER's
    own bucket) past this record's CURRENT epoch stamp. This epoch never
    stamped -> never expired: the panel still owes the sender its first
    look at THIS verdict, even if an earlier one was seen and decayed."""
    # The anchor is THIS epoch's stamp: a verdict that arrives after a revise
    # decays from when IT was seen, never from when an earlier one was (#803).
    anchor = (record.get("verdict_surfaced") or {}).get(record.get("revision"))
    if not anchor:
        return False
    return (store.sessions_since_count(anchor, project_dir)
            >= VERDICT_PANEL_SESSIONS)


def needs_verdict_delivered_stamp(record: dict, session: str) -> bool:
    """#801: whether THIS session has already been nudged with this record's
    verdict in its CURRENT revision epoch. Write-once per (request id, epoch,
    session), the recipient path's key exactly — a revise opens a new epoch, so
    a verdict that lands after one gets its own delivery rather than being
    swallowed by the stamp of an earlier one (#803's lesson, same shape)."""
    epoch = (record.get("verdict_delivered") or {}).get(record.get("revision")) or {}
    return str(session or "").strip() not in epoch


def stamp_verdict_delivered(request_id: str, session: str,
                            project_dir=None, *,
                            reply_event_id: str | None = None) -> bool:
    """Write a `verdict_delivered` row to THIS project's own bucket (the
    SENDER's), recording that the live surface nudged this verdict into
    `session`. Channel `mechanical`, same posture as `stamp_delivered`.

    Deliberately distinct from `verdict_surfaced`: that one records the BRIEF
    rendering the verdict card, and one surface must never suppress the other
    or a live nudge would cost the sender its fuller view at next session."""
    session = str(session or "").strip()
    if not session:
        raise RequestError("verdict_delivered stamp requires a session id")
    row = _stamp("verdict_delivered", request_id, "mechanical")
    row["session"] = session
    if reply_event_id:  # #1117: rides on the same event, only when set
        row["reply_event_id"] = str(reply_event_id)
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def verdict_deliverable(session: str, project_dir=None) -> dict:
    """#801: the verdicts on asks THIS project sent that `session` has not yet
    been nudged with. `{"rows": [...], "overflow": N}`.

    The sender-side counterpart to `deliverable`, over `sender_join` rather
    than `recipient_join` — a verdict on an ask this project SENT is
    structurally unreachable from the recipient path, which is why live
    delivery was one-directional. Same filters as the sender's panel (verdict
    states, not expired), the same dedup-before-cap ordering, and the same
    counted remainder (#800): capping first would let three already-nudged
    verdicts hide a fourth nobody has seen.

    No session id means no dedup key, so it returns nothing rather than
    re-nudging every turn forever."""
    session = str(session or "").strip()
    if not session:
        return {"rows": [], "overflow": 0}
    rows = [r for r in sender_join(project_dir=project_dir).values()
            if r["state"] in _VERDICT_STATES
            and ((not verdict_panel_expired(r, project_dir=project_dir)
                  and needs_verdict_delivered_stamp(r, session))
                 or unseen_reply_id(r, session))]  # #1117
    rows.sort(key=_recency,
              reverse=True)
    return {"rows": rows[:RENDER_CAP], "overflow": max(0, len(rows) - RENDER_CAP)}


def verdict_renderable(project_dir=None) -> dict:
    """The sender-side verdict panel data: {"rows": [...], "overflow": N} —
    requests THIS project SENT whose recipient gave a verdict or completion
    claim, read through `sender_join` (so suppression stays hidden, D5),
    newest first, capped at RENDER_CAP with the remainder COUNTED. Expired
    verdicts (D3) are filtered here and only here — the record stays fully
    readable through `sender_join`, only ambient panel attention decays."""
    got = sent(project_dir)
    rows = [r for r in got.by_id.values()
            if r["state"] in _VERDICT_STATES
            and (not verdict_panel_expired(r, project_dir=project_dir)
                 or unseen_reply_id(r))]  # #1117: expiry measures the decision
    rows.sort(key=_recency,
              reverse=True)
    return _with_notes({"rows": rows[:RENDER_CAP],
                        "overflow": max(0, len(rows) - RENDER_CAP)},
                       got.notes)


def overdue_renderable(project_dir=None, shown_ids=frozenset()) -> dict:
    """#1281: the sender-side overdue panel data, `{"rows": [...],
    "overflow": N}`. Asks THIS project sent that are still owed (open,
    needs-info, accepted with no done) and have not moved for the threshold,
    read through `sent` so a sender-suppressed ask stays out (D5). Longest
    waiting first, because the oldest is the one most likely forgotten, capped
    at RENDER_CAP with the remainder COUNTED.

    `shown_ids` are the requests the verdict panel already printed in this
    brief: they are dropped BEFORE the cap, so a repeat never costs a slot
    (the dedup-before-cap order `verdict_deliverable` documents)."""
    got = sent(project_dir)
    now = clock.now_ns()
    rows = [r for r in got.by_id.values()
            if r["request_id"] not in shown_ids and is_overdue(r, now)]
    rows.sort(key=_recency)
    return _with_notes({"rows": rows[:RENDER_CAP],
                        "overflow": max(0, len(rows) - RENDER_CAP)},
                       got.notes)


def needs_surfaced_stamp(record: dict) -> bool:
    """D1: whether a `surfaced` row already exists for this record's CURRENT
    revision epoch. The composer already computes `surfaced` (PR 1's fold) as
    {epoch: earliest ts}; this just names the check the stamp write consults."""
    return record.get("revision") not in (record.get("surfaced") or {})


def stamp_surfaced(request_id: str, project_dir=None) -> bool:
    """Write a `surfaced` row to THIS project's own bucket, referencing a
    foreign request id (D1) — the recipient's brief observed and rendered
    the card. Channel `mechanical`: nobody asserted anything, the render
    pipeline stamped what it did. Write-once is the caller's job
    (`needs_surfaced_stamp` before calling this) — a concurrent-brief
    duplicate is at worst a second row the fold's earliest-wins tie-break
    absorbs for free."""
    row = _stamp("surfaced", request_id, "mechanical")
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def needs_delivered_stamp(record: dict, session: str) -> bool:
    """#756: whether THIS session has already been delivered this record's
    CURRENT revision epoch. Write-once per (request id, epoch, session) — a
    revise opens a new epoch, so every live session re-receives the
    sharpened ask, which is the behaviour the design asked for."""
    epoch = (record.get("delivered") or {}).get(record.get("revision")) or {}
    return str(session or "").strip() not in epoch


def stamp_delivered(request_id: str, session: str, project_dir=None) -> bool:
    """Write a `delivered` row to THIS project's own bucket (the
    RECIPIENT's), recording that the live-delivery surface rendered this ask
    into `session`. Channel `mechanical`, same posture as `stamp_surfaced`:
    nobody asserted anything, the render pipeline stamped what it did.
    Write-once is the caller's job (`needs_delivered_stamp` first); the
    fold's earliest-wins tie-break absorbs a concurrent duplicate for free.

    The session id is required, not optional: it is half the dedup key, and
    a stamp without it would read as "delivered" to sessions that never saw
    the ask."""
    session = str(session or "").strip()
    if not session:
        raise RequestError("delivered stamp requires a session id")
    row = _stamp("delivered", request_id, "mechanical")
    row["session"] = session
    return append(row, project_dir=project_dir,
                  writer=Writer.EMITTER)


def supersedes_label(record: dict) -> str:
    """The `supersedes` render value, or "" when the record cites none.
    Appends "(record forgotten)" when the superseded id no longer resolves
    in its own origin bucket (D0's supersedes extension) — a re-ask must
    never silently look like a first ask, even after deletion at the source.
    Resolves against `record["from_slug"]`, the origin `recipient_join`
    stamps on every record it returns."""
    target = str(record.get("supersedes") or "")
    if not target:
        return ""
    origin = record.get("from_slug") or ""
    if origin and get(target, project_dir=origin) is None:
        return f"{target} (record forgotten)"
    return target


def status_counts(project_dir=None) -> dict:
    """{"open_sent": N, "awaiting_you": N} for `daimon status`'s one-line
    summary (#694 PR 3). Read through the composer (`sender_join`/
    `recipient_join`) rather than the per-bucket fold, so the counts agree
    with the two ambient panels rather than the pre-PR-2 per-bucket view.

    A full honest count, not an attention-filtered one: suppressed and
    stale records still await a decision, so they count here even though
    neither panel renders them."""
    out_join, in_join = sent(project_dir), join(project_dir)
    open_sent = sum(1 for r in out_join.by_id.values()
                    if r["state"] in _SENDER_MOVABLE)
    awaiting = sum(1 for r in in_join.by_id.values()
                   if r["state"] in _SENDER_MOVABLE)
    # `notes`: what the two joins left out or read around, once each.
    notes = tuple(dict.fromkeys([*out_join.notes, *in_join.notes]))
    return {"open_sent": open_sent, "awaiting_you": awaiting, "notes": notes}


@jsonl.reaching(_path, lambda row, key: key in row_content_keys(row))
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
