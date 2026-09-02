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

SCOPE, slice 1: the ruling/refutation and amendment lanes render this
project's own buckets only. Slice 3 (`foreign_counts`) widens that to every
OTHER project too, but as INTEGERS ONLY — text stays behind an explicit
`--all-projects` reserved for slice 4. That split is not caution, it is
scar 0055: the serializer captures tool_result payloads, so inside an agent
session CLI stdout is checkpoint input. Printing another bucket's record
text copies that plaintext into THIS project's checkpoint, where `forget`
in the origin project can never reach it. Same doctrine `recall.py` already
applies when it widens: counts by project, never content, because crossing
projects stays user-invoked.

The REQUEST lane is the deliberate exception, and always has been: an ask
addressed to this project has its `opened` row in the SENDER's bucket, so an
inbox is inherently cross-bucket — there is no single-bucket reading of "asks
addressed to me" any more than `requests.inbox_listing` (its shipped panel
precedent) has one. That is not scar 0055's violation, it is the sentence
right after it: the scar's own closing lines say the shipped request panel
"shows only asks addressed to THIS project" and is bounded by exactly that.
Rendering an ask addressed to you is rendering your own mail, not a foreign
bucket's plaintext. What scar 0055 still forbids stands untouched: a FOREIGN
project's own record text (its rulings, its refutations, its amendments, or
its outgoing asks to someone else) stays behind the explicit flag.
"""

from __future__ import annotations

from . import amendments, config, refutations, requests, store


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
    """Asks addressed to this project that no human has answered.

    Sourced from `requests.recipient_join`, the cross-bucket inbox join —
    never `requests.records`, which folds only this bucket's own file. An
    ask addressed here has its `opened` row in the SENDER's bucket, so a
    per-bucket fold cannot see it; `recipient_join` already keeps the two
    directions apart (requests.py:871-874), excluding this project's own
    outgoing asks. `requests.inbox_listing` is the shipped precedent for
    consuming it, including this same `state not in _SENDER_MOVABLE` filter.
    """
    records = requests.recipient_join(project_dir=project_dir)
    # `seq` breaks the `waiting_since` tie on append order (see
    # `_order_key`), and that order lives in whichever bucket actually wrote
    # the `opened` row — the record's own `from_slug`, or this bucket itself
    # for a self-addressed ask (`from_slug` is empty for those). Cache each
    # origin bucket's event order so a bucket with several addressed asks is
    # read once, not once per record.
    seq_cache: dict[str, list] = {}

    def _seq(record, rid) -> int:
        origin = record.get("from_slug") or slug
        if origin not in seq_cache:
            seq_cache[origin] = [row.get("request_id") for row in
                                 requests.events(project_dir=origin)]
        seen = seq_cache[origin]
        return seen.index(rid) if rid in seen else 0

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
            _seq(record, rid)))
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


# ---- slice 3: foreign counts -----------------------------------------------
#
# `queue` answers "what is waiting on me, HERE". `foreign_counts` answers a
# different question: how much is waiting on the human in every OTHER
# project — as INTEGERS ONLY. Scar 0055 governs this surface completely: the
# serializer captures tool_result payloads, so printing a foreign bucket's
# own record text into a `daimon decide` run inside THIS project copies that
# plaintext into THIS project's checkpoint, where the origin's `forget` can
# never reach it. So nothing below may return a record, an id, or text —
# only a count per owning slug.


def _strip_plaintext(row: dict) -> dict:
    """Presence sentinel for every field `requests._PLAINTEXT_FIELDS` names:
    `"x"` if the original held non-whitespace content, `""` otherwise. Every
    other key passes through untouched. Applied at the READ boundary, before
    folding, so a foreign bucket's ask/why/note/evidence/from_label never
    travels through this project's process as real text.

    A sentinel, not a dropped field: `requests.fold`'s read-boundary shape
    check (requests.py:333) rejects an `opened` row whose `ask` is empty, so
    dropping the field would silently undercount and disagree with what
    `decide` shows inside the owning project.
    """
    out = dict(row)
    for field in requests._PLAINTEXT_FIELDS:
        if field in out:
            out[field] = "x" if str(out.get(field) or "").strip() else ""
    return out


def _foreign_request_counts(slug: str | None) -> dict[str, int]:
    """Every foreign project's waiting request count, in ONE fleet-wide pass.

    A request's rows can span two buckets — the `opened` row in the
    sender's, a verdict or suppression in the recipient's — so computing
    this per foreign project via `queue`/`recipient_join` would each rescan
    every bucket in the fleet: O(N^2). Here every bucket
    (`requests._bucket_slugs()`) is read exactly once, rows are grouped by
    `request_id` across buckets, and each group is folded once — reusing
    `requests.fold` rather than a second counting state machine, so this
    lane can never drift from the transitions `queue`'s own request lane
    already trusts (suppression, rejection terminality, the human-only
    verdict re-check).
    """
    by_id: dict[str, list] = {}
    for bucket in requests._bucket_slugs():
        try:
            rows = requests.events(project_dir=bucket)
        except Exception:
            continue
        for row in rows:
            # `events` already filtered every row through `_REQUEST_ID_RE`
            # (requests.py:285-288), so the id is present and well-formed
            # here — no second guard, which would be unreachable.
            rid = str(row.get("request_id") or "")
            by_id.setdefault(rid, []).append(_strip_plaintext(row))
    counts: dict[str, int] = {}
    for rows in by_id.values():
        try:
            folded = requests.fold(rows)
        except Exception:
            continue
        for record in folded.values():
            to = str(record.get("to") or "")
            if not to or to == slug:
                continue  # this project's own inbox is `queue`'s, not ours
            if record.get("state") not in requests._SENDER_MOVABLE:
                continue
            if record.get("suppressed"):
                continue
            counts[to] = counts.get(to, 0) + 1
    return counts


def _ledger_bucket_slugs() -> list[str]:
    """Every project bucket directory under the checkpoint root.

    Unlike the request lane, rulings/refutations and amendments are
    genuinely single-bucket ledgers — a foreign bucket's own record lives
    only in that bucket, never split across two. So every bucket has to be
    checked directly rather than joined; a bucket with no refutations.jsonl
    or amendments.jsonl yet simply reads back `{}` from `.records()`.
    """
    try:
        return [child.name for child in
                sorted(config.checkpoint_dir().iterdir()) if child.is_dir()]
    except OSError:
        return []


def _foreign_ledger_counts(slug: str | None) -> dict[str, int]:
    """Candidate rulings/refutations and verified amendments in every OTHER
    bucket, read directly (single-bucket lanes) — fail-open per bucket, per
    lane, matching `queue`'s own degradation posture. Only `state` survives
    past the read: the record itself (subject, verdict, evidence text) is
    dropped before this returns.
    """
    counts: dict[str, int] = {}
    for bucket in _ledger_bucket_slugs():
        if bucket == slug:
            continue
        try:
            for record in refutations.records(project_dir=bucket).values():
                if record.get("state") == "candidate":
                    counts[bucket] = counts.get(bucket, 0) + 1
        except Exception:
            pass
        try:
            for record in amendments.records(project_dir=bucket).values():
                if record.get("state") == "verified":
                    counts[bucket] = counts.get(bucket, 0) + 1
        except Exception:
            pass
    return counts


def foreign_queues(*, project_dir=None) -> list[tuple[str, dict]]:
    """Slice 4: every OTHER bucket's own queue, as TEXT, behind the explicit
    `--all-projects` the caller typed. [(slug, queue-result), ...], buckets
    with nothing waiting and nothing suppressed omitted.

    Composed PER BUCKET, never as one global fold, and that is the whole
    design: each bucket's request lane runs its own `recipient_join` (its
    own orphan gate, its own suppression semantics) and its ledger lanes
    read its own files, exactly as `queue` does for the local project. A
    single fold across buckets would lose the recipient the staleness anchor
    belongs to and delete the join that stops an agent from marking a foreign
    ask done. Every printed command carries `--slug=<slug>` (the `=` form,
    since a slug starts with '-'), so it runs from wherever the person is
    standing. Fail-open per bucket, matching `foreign_counts`.

    Scar 0055 still governs the DEFAULT surface: nothing here is reached
    without the flag, and the caller typing it is the user-invoked crossing
    `recall --all-projects` already is."""
    own = store.project_slug(project_dir)
    # A project can be waited on before it has ever written a bucket of its
    # own: its mail sits in the SENDER's ledger, addressed by slug. So the
    # candidates are every bucket directory plus every `to` an opened row
    # names, ids only, no text read here.
    candidates = {b for b in _ledger_bucket_slugs() if not b.startswith(".")}
    for bucket in requests._bucket_slugs():
        try:
            rows = requests.events(project_dir=bucket)
        except Exception:
            continue
        for row in rows:
            if row.get("event") == "opened" and str(row.get("to") or ""):
                candidates.add(str(row.get("to")))
    out: list[tuple[str, dict]] = []
    for bucket in sorted(candidates):
        if bucket == own:
            continue
        try:
            result = queue(project_dir=bucket)
        except Exception:
            continue
        rows = result.get("rows") or []
        suppressed = (result.get("excluded") or {}).get("suppressed") or 0
        if not rows and not suppressed:
            continue
        for row in rows:
            row["commands"] = [(label, f"{command} --slug={bucket}")
                               for label, command in row.get("commands") or []]
        out.append((bucket, result))
    return out


def foreign_counts(*, project_dir=None) -> dict[str, int]:
    """{"slug": waiting_count} for every OTHER project's decide queue —
    integers only, never records, ids, or text. This project's own slug is
    excluded; its own queue is `queue()` above.

    Fail-open at both lane boundaries: an unreadable foreign bucket
    degrades that bucket's contribution, never the whole count, and never
    `queue`'s own local result.
    """
    slug = store.project_slug(project_dir)
    counts: dict[str, int] = {}
    try:
        for foreign_slug, n in _foreign_request_counts(slug).items():
            counts[foreign_slug] = counts.get(foreign_slug, 0) + n
    except Exception:
        pass
    try:
        for foreign_slug, n in _foreign_ledger_counts(slug).items():
            counts[foreign_slug] = counts.get(foreign_slug, 0) + n
    except Exception:
        pass
    return counts
