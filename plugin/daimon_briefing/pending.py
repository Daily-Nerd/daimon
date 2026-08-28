"""The `decide` queue composer (#766) — what is waiting on a HUMAN.

daimon tracks the agent's open loops as first-class records; the human's half
has never had a surface. This module composes one, over records that already
exist, and it is a pure reader: it writes nothing at all.

WHAT QUALIFIES is structural, never editorial. A record belongs here only when
some verb's write path REFUSES a non-human channel — `requests._HUMAN_ONLY`,
the `CHANNEL_AUTHORITY` guards in `refutations.ratify` and `amendments`. No
guard, no entry. That test is checkable rather than a matter of taste, and it
is why an agent's own proposal can never promote itself onto this queue.

WHY IT WRITES NOTHING, including no `surfaced` stamp. That stamp is the anchor
`is_stale` measures against, and `store.sessions_since_count` counts serialized
checkpoint pointers rather than reads. A composer that stamped would make the
person READING their own queue the mechanism that ages those asks out of the
agent's panel — decay inverted into deletion.

SCOPE, slice 1: this project's buckets only. Foreign projects contribute counts
in slice 3 and text only behind an explicit `--all-projects` in slice 4. That
split is not caution, it is scar 0055: the serializer captures tool_result
payloads, so inside an agent session CLI stdout is checkpoint input. Printing
another bucket's record text copies that plaintext into THIS project's
checkpoint, where `forget` in the origin project can never reach it. Same
doctrine `recall.py` already applies when it widens: counts by project, never
content, because crossing projects stays user-invoked.
"""

from __future__ import annotations

from . import amendments, refutations, requests, store


# Requests first: someone else is blocked on them. Then quote-verified
# amendments, which are already rendering in briefings as unconfirmed claims.
# Ledger candidates last: nothing renders them yet, so nothing is misleading
# while they wait.
_KIND_RANK = {"request": 0, "amendment": 1, "ruling": 2, "refutation": 2}


def _row(*, kind, record_id, slug, headline, waiting_since,
         commands, context="", blocking=False) -> dict:
    return {
        "kind": kind,
        "id": record_id,
        "slug": slug,
        "headline": headline,
        # What the headline alone cannot carry: who is waiting, or which
        # item a claim is about. A decision needs both, and neither belongs
        # in the header line a reader scans.
        "context": context,
        "waiting_since": waiting_since,
        "blocking": blocking,
        "commands": commands,
    }


def _order_key(row: dict, seq: int) -> tuple:
    # Blocking first, then OLDEST first — a deliberate inversion of the
    # panels' newest-first (`requests.inbox_renderable`). Those are a capped
    # attention feed; this is a backlog, and the oldest undecided item is the
    # one rotting. `created_at` is second-resolution and ties routinely, so
    # append order in the ledger breaks it: in an append-only log, first
    # written IS first waiting.
    return (not row["blocking"], row["waiting_since"] or "", seq,
            _KIND_RANK.get(row["kind"], 9), row["id"])


def _request_rows(project_dir, slug) -> tuple[list, int]:
    """Asks addressed to this project that no human has answered."""
    records = requests.records(project_dir=project_dir)
    seen = [row.get("request_id") for row in requests.events(
        project_dir=project_dir)]
    rows, suppressed = [], 0
    for rid, record in records.items():
        if record.get("state") not in requests._SENDER_MOVABLE:
            continue
        if record.get("suppressed"):
            # `suppress` is human-only, so a suppressed ask is already the
            # owner's own "not now". Counted, never listed.
            suppressed += 1
            continue
        rows.append((_row(
            kind="request", record_id=rid, slug=slug,
            headline=record.get("ask") or "",
            context=(f"from {record['from_label']}"
                     if record.get("from_label") else ""),
            waiting_since=record.get("created_at") or "",
            blocking=bool(record.get("blocking")),
            commands=[
                ("accept", f"daimon request accept {rid}"),
                ("reject", f"daimon request reject {rid} --note \"<why>\""),
                ("needs-info", f"daimon request needs-info {rid}"),
            ]),
            seen.index(rid) if rid in seen else 0))
    return rows, suppressed


def _ledger_rows(project_dir, slug) -> list:
    """Agent-proposed rulings and refutations awaiting ratification.

    Polarity decides the verb family AND the header field: a ruling is read by
    its rule text, a refutation by what it refutes. `cli/_ledger.py` makes the
    same split for the same reason.
    """
    records = refutations.records(project_dir=project_dir)
    seen = [row.get("refutation_id") for row in refutations.events(
        project_dir=project_dir)]
    rows = []
    for rid, record in records.items():
        if record.get("state") != "candidate":
            continue
        ruling = record.get("polarity") == "ruling"
        family = "ruling" if ruling else "refute"
        rows.append((_row(
            kind="ruling" if ruling else "refutation",
            record_id=rid, slug=slug,
            headline=(record.get("verdict") if ruling
                      else record.get("subject")) or "",
            waiting_since=record.get("created_at") or "",
            commands=[
                ("ratify", f"daimon {family} ratify {rid}"),
                ("retire" if ruling else "overturn",
                 f"daimon {family} "
                 f"{'retire' if ruling else 'overturn'} {rid}"),
            ]),
            seen.index(rid) if rid in seen else 0))
    return rows


def _amendment_rows(project_dir, slug) -> list:
    """Quote-verified amendments only.

    A candidate has not passed the session-end byte-check, so nothing is owed
    yet — `amendments.py` forbids surfacing one outright, because an unverified
    annotation would let an agent assert state with no transcription check.
    `verified` is exactly the set the briefing already renders with a
    confirm/reject pair.
    """
    records = amendments.records(project_dir=project_dir)
    seen = [row.get("amendment_id") for row in amendments.events(
        project_dir=project_dir)]
    rows = []
    for aid, record in records.items():
        if record.get("state") != "verified":
            continue
        rows.append((_row(
            kind="amendment", record_id=aid, slug=slug,
            headline=record.get("evidence") or "",
            # An amendment is a claim ABOUT an item: the quote alone is not
            # decidable without knowing what it is claimed to change.
            context=(f"on {record.get('item_id') or '?'} "
                     f"· claims {record.get('change') or '?'}"),
            waiting_since=record.get("created_at") or "",
            commands=[
                ("confirm", f"daimon amend ratify {aid}"),
                ("reject", f"daimon amend reject {aid}"),
            ]),
            seen.index(aid) if aid in seen else 0))
    return rows


def queue(*, project_dir=None) -> dict:
    """{"rows": [...], "excluded": {...}} for this project.

    Fail-open per source: one unreadable ledger degrades that lane rather than
    emptying the queue, because a human-facing backlog that silently renders
    nothing is worse than one that renders less.
    """
    slug = store.project_slug(project_dir)
    pairs, suppressed = [], 0
    try:
        request_pairs, suppressed = _request_rows(project_dir, slug)
        pairs += request_pairs
    except Exception:
        pass
    for source in (_ledger_rows, _amendment_rows):
        try:
            pairs += source(project_dir, slug)
        except Exception:
            pass
    pairs.sort(key=lambda pair: _order_key(pair[0], pair[1]))
    return {
        "rows": [row for row, _seq in pairs],
        "excluded": {"suppressed": suppressed},
    }
