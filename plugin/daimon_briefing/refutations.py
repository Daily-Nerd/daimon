"""Project-scoped negative-knowledge ledger (#573).

Refutations are not checkpoint items.  They describe approaches that lost
under named evidence and scope, so their lifetime cannot depend on checkpoint
carry, ranking, or an LLM re-emitting the wording.  This module owns a separate
append-only stream and a deterministic lifecycle fold.

The write path is deliberately zero-LLM.  Agent assertions remain candidates;
only explicit human ratification (or a future typed mechanical verifier) makes
one active.

Evidence is CITED, not verified (#576).  `_evidence` validates the shape of a
`kind:payload` source and nothing else: it does not resolve the reference, does
not check that the measurement or artifact exists, and cannot establish that it
entails the verdict.  That is why evidence text alone never activates a
permanent negative guard, and why every surface that renders a source says so.

#693 widened this ledger, deliberately, to BOTH polarities: rulings are
human-ratified standing constraints — positive records — sharing the id
space, fields, deletion, and audit machinery.  Polarity lives in the founding
event name (`ruled` vs `asserted`), derived at fold time, never in a
caller-supplied field, and the lifecycle is polarity-asymmetric: no agent
path changes what a ruling renders.
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


VERSION = 1
EVENTS = frozenset({
    "asserted", "ratified", "activated", "revised",
    "overturn-proposed", "overturned",
    # #693: polarity lives in the FOUNDING EVENT NAME. An older reader's
    # events() drops unknown names and its fold treats the orphan lifecycle
    # rows as inert, so an old install never renders a ruling as a
    # refutation — it simply does not see it.
    "ruled", "revision-proposed",
})
STATES = frozenset({"candidate", "active", "overturned"})
AUTHORITIES = frozenset({"agent", "human", "mechanical"})
# Authority is a property of the WRITE PATH, never a caller's claim about
# itself. `--by human` was a flag whose only function was to let the caller
# assert its own authority, which is the echo-defense hole (#512) and the
# self-assigned-identity hole (scar 0032) one layer up: an actor acting as
# witness for its own claim.
#
# A channel is OBSERVED by the writer and recorded on every row, so authority
# follows from it rather than from an argument. The CLI can observe only two of
# these. `ui` and `signed` are reachable exclusively from an in-process writer,
# which is what forces a future UI to be a WRITER rather than a wrapper around
# the deleted flag: if the CLI could emit `ui`, an agent shelling out could too.
#
# Nothing local is unforgeable. A caller with machine access can allocate a pty
# or drive a UI. The claim this earns is not proof but provenance: forgery costs
# deliberate impersonation instead of one word, and the channel stays auditable
# afterwards. That is strictly more than the zero bits recorded before.
CHANNEL_AUTHORITY = {
    "cli-agent": "agent",
    "cli-tty": "human",
    "ui": "human",
    "signed": "human",
    "mechanical": "mechanical",
}
CHANNELS = frozenset(CHANNEL_AUTHORITY)
# What each channel is allowed to say about itself when rendered. Never
# "human-ratified" unqualified: the tier is the honest part.
CHANNEL_LABEL = {
    "cli-agent": "agent-proposed",
    "cli-tty": "ratified (interactive)",
    "ui": "ratified (ui)",
    "signed": "ratified (signed)",
    "mechanical": "mechanically-activated",
}


def _channel_of(row: dict) -> str:
    """A row's channel as a lookup key for CHANNEL_AUTHORITY / CHANNEL_LABEL.

    Rows are read off disk, so the field can be absent or non-str. Neither map
    carries an empty-string key, so an unusable channel misses on "" exactly
    as a None missed before, and the lookup still yields None. Deriving the
    key here keeps that reasoning in one place rather than at twelve call
    sites.
    """
    return str(row.get("channel") or "")


_EVENT_RANK = {
    # Same-order ambiguity fails toward less authority: ratification before a
    # revision leaves the revision candidate; overturn remains last.
    # `revision-proposed` ties with `overturn-proposed` deliberately and the
    # tie is meaningless — they write different fold keys, and rank only
    # breaks same-`order` ties (same-nanosecond and test clocks).
    "asserted": 0,
    "ruled": 0,
    "ratified": 1,
    "activated": 1,
    "revised": 2,
    "revision-proposed": 3,
    "overturn-proposed": 3,
    "overturned": 4,
}
_REF_ID_RE = re.compile(r"r-[0-9a-f]{12}")
_ISSUE_RE = re.compile(r"(?:issue:|#)(\d+)\b", re.IGNORECASE)
_EVIDENCE_KINDS = frozenset({
    "message", "transcript", "artifact", "issue", "measurement", "url",
})
_SPACE_RE = re.compile(r"\s+")
_MAX_TEXT = 2000
_MAX_ANCHORS = 24
_MAX_EVIDENCE = 24
# #693: a standing rule needing more is a document, not a ruling — capped at
# WRITE time so the render never has to truncate one (a clipped conditional
# rule is a different rule). Creates two validation regimes in one id space;
# a ruling can never be minted to collide with a long-subject refutation.
_MAX_RULING_TEXT = 280
# Best-effort growth containment for the two agent-writable proposal
# channels on a ruling (append is unlocked, so this is not an invariant;
# read-side boundedness comes from latest-wins in the fold).
_MAX_OPEN_PROPOSALS = 3

# Every field of a ledger row that can hold ITEM plaintext, flat then nested
# (#645). One declaration, two consumers: `forget_content_key` below decides
# which records a deletion reaches, and `privacy.audit_project` decides which
# fields it hashes when proving the deletion happened. Hand-maintaining those
# two lists separately is the exact shape #601 built the surface registry to
# stop — a field the auditor reports but forget cannot reach is a permanent
# exit 1, and a field forget reaches but the auditor ignores is a silent exit
# 0 over live plaintext.
#
# `author` is deliberately absent: it is a person's name, not item text, and
# matching a tombstone against it would let one forgotten value delete every
# record a given author ever wrote.
_PLAINTEXT_FIELDS = ("subject", "verdict", "scope", "revisit_when", "note")
_PLAINTEXT_LISTS = ("anchors", "evidence")


class RefutationError(ValueError):
    """A requested ledger transition is invalid or cannot be persisted."""


def _path(project_dir=None):
    slug = store.project_slug(project_dir)
    if not slug:
        return None
    return config.checkpoint_dir() / slug / "refutations.jsonl"


def _text(name: str, value, *, required: bool = True) -> str:
    out = _SPACE_RE.sub(" ", str(value or "")).strip()
    if required and not out:
        raise RefutationError(f"{name} is required")
    if len(out) > _MAX_TEXT:
        raise RefutationError(
            f"{name} is too long ({len(out)} > {_MAX_TEXT} characters)")
    return out


def canonical_anchor(value) -> str:
    """Canonical, display-safe anchor used by exact proactive matching."""
    anchor = _text("anchor", value).casefold()
    issue = _ISSUE_RE.fullmatch(anchor)
    if issue:
        return f"issue:{issue.group(1)}"
    # URLs to GitHub issues are a common authored spelling.  Collapsing them
    # to issue:N lets a later '#N' prompt hit without fuzzy semantics.
    url_issue = re.search(r"/issues/(\d+)(?:\b|$)", anchor)
    if url_issue:
        return f"issue:{url_issue.group(1)}"
    return anchor


def _anchors(values) -> list[str]:
    """Canonical anchor set, scrubbed BEFORE it is canonicalized (#647).

    `canonical_anchor` casefolds, and several of redact.py's pattern classes
    are uppercase-dependent (aws-key, google-key, jwt), so `append`'s later
    scrub of the nested array no longer matched what the transform had already
    lowered. The fix is the ORDERING, not either function: the scrub runs on
    the raw text, then canonicalization runs on the redacted result.

    Both sides of the guard comparison come through here, so an anchor a user
    can type still matches the redacted anchor that was stored.
    """
    out = []
    seen = set()
    for raw in values or []:
        clean, _ = redact.redact_text(str(raw or ""))
        anchor = canonical_anchor(clean)
        if anchor not in seen:
            seen.add(anchor)
            out.append(anchor)
    if len(out) > _MAX_ANCHORS:
        raise RefutationError(
            f"too many anchors ({len(out)} > {_MAX_ANCHORS})")
    return out


def _evidence(values, *, required: bool = True) -> list[str]:
    out = []
    seen = set()
    for raw in values or []:
        value = _text("evidence", raw)
        kind, separator, payload = value.partition(":")
        if (not separator or kind.casefold() not in _EVIDENCE_KINDS
                or not payload.strip()):
            raise RefutationError(
                f"invalid evidence source {value!r}; use a typed source such as "
                "message:<id>, transcript:<session>, artifact:<path>, "
                "issue:<number>, measurement:<receipt>, or url:<source>")
        if value not in seen:
            seen.add(value)
            out.append(value)
    if required and not out:
        raise RefutationError(
            "at least one --evidence source is required; name the measurement, "
            "artifact, issue, or transcript span cited for the verdict")
    if len(out) > _MAX_EVIDENCE:
        raise RefutationError(
            f"too many evidence sources ({len(out)} > {_MAX_EVIDENCE})")
    return out


def make_id(subject: str, scope: str) -> str:
    """Stable logical id from the redacted subject+scope identity."""
    raw = f"{_text('subject', subject).casefold()}\0{_text('scope', scope).casefold()}"
    return "r-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _identity_id(record: dict) -> str:
    """The id this record's CURRENT subject+scope would mint, or "".

    The stored `refutation_id` answers a different question. It is minted once,
    at assertion, and a `revise` may replace the subject without re-deriving it
    — deliberately, because the id is the ledger's join key and every appended
    row, anchor reference and `forget` target names it. So identity has two
    faces after a revision, and the duplicate check needs the one that reflects
    what the record SAYS now (#646).
    """
    try:
        return make_id(record.get("subject") or "", record.get("scope") or "")
    except RefutationError:
        return ""      # a row too damaged to name an identity claims none


def _identity_holder(ref_id: str, *, exclude: str = "",
                     project_dir=None) -> dict | None:
    """The record already occupying `ref_id`, by stored id or by what it now
    says. `exclude` is the record being revised — every record collides with
    itself under a current-subject check."""
    existing = get(ref_id, project_dir=project_dir)
    if existing is not None and existing.get("refutation_id") != exclude:
        return existing
    for record in records(project_dir=project_dir).values():
        if record.get("refutation_id") == exclude:
            continue
        if _identity_id(record) == ref_id:
            return record
    return None


def _stamp(event: str, refutation_id: str, channel: str,
           *, now_ns: int | None = None, event_id: str | None = None) -> dict:
    if event not in EVENTS:
        raise RefutationError(f"unknown refutation event: {event}")
    if not _REF_ID_RE.fullmatch(str(refutation_id or "")):
        raise RefutationError(f"invalid refutation id: {refutation_id!r}")
    if channel not in CHANNELS:
        raise RefutationError(
            f"channel must be one of: {', '.join(sorted(CHANNELS))}")
    # Derived, never accepted: there is no way to name one channel and claim
    # the authority of another.
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
        "refutation_id": refutation_id,
        "channel": channel,
        "authority": authority,
        "author": config.author(),
    }


def _scrub_list(values: list[str]) -> list[str]:
    out = []
    for value in values:
        clean, _ = redact.redact_text(value)
        out.append(clean)
    return out


def _is_torn(path) -> bool:
    """True when the last append died before writing its terminator.

    Appending onto an unterminated line fuses two rows into one unparseable
    line, and `events` drops malformed lines silently — so the new row would
    vanish while its command still reported success.  A torn write must cost
    exactly the torn row.
    """
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as handle:
            handle.seek(-1, 2)
            return handle.read(1) != b"\n"
    except OSError:
        return False


def append(row: dict, project_dir=None) -> bool:
    """Append one admitted lifecycle row.  Never mutates another ledger."""
    if config.is_disabled():
        return False
    path = _path(project_dir)
    if path is None:
        return False
    # Nested free-text arrays are scrubbed before the flat row crosses the
    # policy seam.  The admitted row object itself is the one written, keeping
    # the write-audit correlation exact.
    #
    # Only keys the caller actually set are scrubbed.  `revise` uses absence to
    # mean "unchanged" and `fold` reads key presence as "replace", so forging
    # an empty list here would silently clear the field on every revision that
    # did not name it — which is how an active guard loses its anchors.
    for key in ("anchors", "evidence"):
        if key in row:
            row[key] = _scrub_list(list(row[key] or []))
    admitted = policy.admit_row(
        row,
        redact_fields=(
            "subject", "verdict", "scope", "revisit_when", "note", "author"),
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            if _is_torn(path):
                handle.write("\n")
            handle.write(json.dumps(admitted, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def plaintext_values(row: dict) -> list[str]:
    """Every scalar plaintext value this row carries (#698).

    The forget TARGETING pool reads this (cli._cmd_forget) instead of
    hand-reading `subject` — the same one-declaration discipline
    `row_content_keys` below gives the deleter and the auditor. Scalars only:
    `anchors`/`evidence` are bounded typed tokens shared across records, so
    offering one as a by-value target would show a single record in the
    dry-run while the deleter removes every record carrying the token — the
    same reasoning that keeps `author` out of the declared set entirely."""
    out: list[str] = []
    for field in _PLAINTEXT_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            out.append(value)
    return out


def row_content_keys(row: dict) -> set[str]:
    """Canonical keys for every plaintext field this row carries (#645).

    The one reader of _PLAINTEXT_FIELDS/_PLAINTEXT_LISTS, so the deleter below
    and `privacy.audit_project` cannot drift apart about what counts as
    plaintext on this surface.
    """
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
    """Remove every record holding `content_key` in a plaintext field (#578).

    The ONE path that rewrites this ledger rather than appending to it, and it
    exists because `forget` promises the content leaves the audit trail (#321,
    restated in `cli._cmd_forget`).  Daimon sorts its stores by whether they
    hold PLAINTEXT, not by whether they are append-only: events.jsonl is never
    rewritten because it carries hashes, and #419 was filed as a defect the
    moment plaintext reached it.  This ledger carries plaintext by design, so
    it is in the checkpoint's category and a removal must reach the bytes.

    Matches on the SAME canonical key the checkpoint splice uses, never on
    substring containment: `forget` removes a value, and has never claimed to
    scrub a phrase out of records that were not targeted.

    Every plaintext field is matched, not the subject alone (#645).  The audit
    hashes them all, so a value surviving in a `verdict` or a `note` would be
    reported as residue on a surface no deletion could reach — a permanent
    exit 1.  Whole-VALUE equality after canonicalization keeps that honest: a
    record goes when a field IS the forgotten value, never when one mentions it.

    Every row of a matched record goes, not only the row that matched.  A
    revision rewrites the subject, so an earlier row can hold an older subject
    the folded record no longer renders; keeping it would leave forgotten text
    on disk in a row nothing displays.

    No kill-switch check: forget is the ratified deletion exemption (#421), so
    the write that makes the deletion real must run while daimon is disabled.

    Atomic or nothing.  A half-written rewrite would truncate history, which is
    strictly worse than the value surviving, so the replacement is staged in the
    same directory and swapped with os.replace.  Returns the ids removed, or []
    when nothing matched, the ledger is absent, or the rewrite failed.
    """
    path = _path(project_dir)
    if path is None or not path.exists():
        return []
    # #693: the doomed set derives from RAW LINES, never from `events()` —
    # that reader drops rows whose event name this install does not know, and
    # a deleter inheriting the filter would leave exactly those rows on disk
    # while the audit (which reads raw lines) reports them as residue: the
    # permanent exit 1 this module forbids. Version-proof for any future
    # event name; the rewrite below already walks raw lines for this reason.
    doomed = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(row, dict):
            continue
        if content_key in row_content_keys(row):
            ref_id = str(row.get("refutation_id") or "")
            if _REF_ID_RE.fullmatch(ref_id):
                doomed.add(ref_id)
    if not doomed:
        return []
    # Rewrite RAW LINES, never `events()` output. That reader is deliberately
    # tolerant — it drops malformed lines and rows whose `event` it does not
    # recognise, and it stamps a `_line` key onto what it returns. Round-tripping
    # through it would silently delete every row a future daimon added and write
    # `_line` into the ledger. Scars 0025 and 0042 are both this shape: a
    # forgiving read feeding a write. `lines` is the single read above — the
    # doomed set and the rewrite see the same bytes.
    kept: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except (ValueError, TypeError):
            # Already invisible to every read path, and `_is_torn` establishes
            # that a torn row is expendable. Keeping it would leave forgotten
            # bytes on disk for no reachable benefit.
            continue
        if (isinstance(row, dict)
                and str(row.get("refutation_id") or "") in doomed):
            continue
        # Anything else is written back BYTE-IDENTICAL, including rows this
        # version cannot interpret.
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
                or not _REF_ID_RE.fullmatch(str(row.get("refutation_id") or ""))):
            continue
        copy = dict(row)
        copy["_line"] = index
        rows.append(copy)
    return rows


def fold(rows: list[dict]) -> dict[str, dict]:
    """Fold lifecycle facts into current records, deterministic under reorder."""
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
        ref_id = row["refutation_id"]
        event = row["event"]
        current = out.get(ref_id)
        if event in ("asserted", "ruled"):
            if current is not None:
                continue  # duplicate logical assertion, first writer wins
            state = (
                "active" if row.get("ratified") is True
                and CHANNEL_AUTHORITY.get(_channel_of(row)) == "human" else "candidate")
            out[ref_id] = {
                "refutation_id": ref_id,
                # #693: polarity is DERIVED from the founding event name at
                # fold time, never read from a caller-supplied field.
                "polarity": "ruling" if event == "ruled" else "refutation",
                "state": state,
                "subject": str(row.get("subject") or ""),
                "verdict": str(row.get("verdict") or ""),
                "scope": str(row.get("scope") or ""),
                "anchors": list(row.get("anchors") or []),
                "revisit_when": str(row.get("revisit_when") or ""),
                "evidence": list(row.get("evidence") or []),
                "asserted_by": row.get("authority"),
                "asserted_author": row.get("author"),
                # Who authored the CURRENT text — distinct from asserted_by
                # so human-ratified agent prose renders as exactly that.
                # DERIVED from the channel, never read from the row's own
                # authority claim: this is a rendered authority label.
                "text_authored_by": CHANNEL_AUTHORITY.get(_channel_of(row)),
                "activation": (CHANNEL_LABEL.get(_channel_of(row))
                               if state == "active" else None),
                "activation_channel": (row.get("channel")
                                       if state == "active" else None),
                "activation_author": (
                    row.get("author") if state == "active" else None),
                "activated_at": row.get("ts") if state == "active" else None,
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "revision": 1,
                "history_count": 1,
            }
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert here
        is_ruling = current.get("polarity") == "ruling"
        # #693: fold-enforced, not CLI convention — no agent path changes
        # what a ruling renders. An agent-authority `revised` row on an
        # active ruling is fully inert, and a retired ruling cannot be
        # resurrected by revise; both stay visible in the raw audit.
        if is_ruling and event == "revised" and (
                (current["state"] == "active"
                 and CHANNEL_AUTHORITY.get(_channel_of(row)) != "human")
                or current["state"] == "overturned"):
            continue
        # #693: a content-bound ratify whose displayed text no longer matches
        # is fully inert — refused BEFORE the bump, consistent with the other
        # pre-bump gates, so a rejected activation moves nothing rendered.
        if (event == "ratified"
                and str(row.get("verdict_key") or "")
                and str(row.get("verdict_key"))
                != normalize.content_key(current.get("verdict") or "")):
            continue
        current["history_count"] += 1
        # #693: an agent proposal must not move a ruling's rendered age or
        # its list/search order. Ruling polarity only — changing the shipped
        # refutation ordering is its own decision.
        if not (is_ruling and event in ("revision-proposed",
                                        "overturn-proposed")):
            current["updated_at"] = row.get("ts") or current["updated_at"]
        if event == "ratified":
            # Content binding (#693) is checked pre-bump above: a ratify row
            # carrying a verdict_key activates only the text it displayed; a
            # row with NO key is unbound and activates normally (every
            # pre-existing ledger row is absent-key).
            if current["state"] != "overturned" and CHANNEL_AUTHORITY.get(_channel_of(row)) == "human":
                current["state"] = "active"
                current["activation"] = CHANNEL_LABEL.get(_channel_of(row))
                current["activation_channel"] = row.get("channel")
                current["activation_author"] = row.get("author")
                current["activated_at"] = row.get("ts")
        elif event == "activated":
            # #693: mechanical activation is a refutation concept (#581);
            # a mechanical row on a ruling stays in the raw audit, inert.
            if (current["state"] != "overturned"
                    and current.get("polarity") != "ruling"
                    and CHANNEL_AUTHORITY.get(_channel_of(row)) == "mechanical"):
                current["state"] = "active"
                current["activation"] = "mechanically-activated"
                current["activation_channel"] = "mechanical"
                current["activation_author"] = row.get("author")
                current["activated_at"] = row.get("ts")
        elif event == "revised":
            for key in ("subject", "verdict", "scope", "revisit_when"):
                if key in row:
                    current[key] = str(row.get(key) or "")
            if "anchors" in row:
                current["anchors"] = list(row.get("anchors") or [])
            if "evidence" in row:
                # Replacement, not accrual.  A folded record states what is
                # believed NOW, so `evidence` names the citations backing the
                # CURRENT verdict; the founding citation is not lost, it is
                # in the append-only stream that `events()` returns.
                # Accrual shipped first and was wrong twice over: reviving an
                # overturned record carried forward the very citation whose
                # invalidity justified the overturn, and merging across
                # revisions walked straight through the per-row _MAX_EVIDENCE
                # cap (74 sources against a limit of 24).
                current["evidence"] = list(row.get("evidence") or [])
            # #693: re-stamped ONLY when the row carries a text key — the
            # replace-by-key-presence contract above means a human revising
            # only scope must not relabel agent-authored text as human.
            if "verdict" in row or "subject" in row:
                current["text_authored_by"] = CHANNEL_AUTHORITY.get(
                    _channel_of(row))
            was_active = current["state"] == "active"
            current["state"] = (
                "active" if row.get("ratified") is True
                and CHANNEL_AUTHORITY.get(_channel_of(row)) == "human" else "candidate")
            current["activation"] = (
                CHANNEL_LABEL.get(_channel_of(row))
                if current["state"] == "active" else None)
            current["activation_channel"] = (
                row.get("channel") if current["state"] == "active" else None)
            current["activation_author"] = (
                row.get("author") if current["state"] == "active" else None)
            if current["state"] == "active" and not was_active:
                current["activated_at"] = row.get("ts")
            elif current["state"] != "active":
                current["activated_at"] = None
            current["revision"] += 1
            current.pop("overturn_proposed", None)
            current.pop("revision_proposed", None)
        elif event == "revision-proposed":
            # #693: latest-wins; the active text and its render are untouched.
            if current["state"] == "active":
                current["revision_proposed"] = {
                    "by": row.get("authority"),
                    "evidence": list(row.get("evidence") or []),
                    "note": str(row.get("note") or ""),
                    "subject": str(row.get("subject") or ""),
                    "verdict": str(row.get("verdict") or ""),
                }
        elif event == "overturn-proposed":
            if current["state"] == "active":
                current["overturn_proposed"] = {
                    "by": row.get("authority"),
                    "evidence": list(row.get("evidence") or []),
                    "note": str(row.get("note") or ""),
                }
        elif event == "overturned":
            if CHANNEL_AUTHORITY.get(_channel_of(row)) == "human":
                current["state"] = "overturned"
                current["activation"] = None
                current["activation_channel"] = None
                current["activation_author"] = None
                current["overturned_by"] = "human"
                current["overturned_author"] = row.get("author")
                current["overturn_evidence"] = list(row.get("evidence") or [])
                current["overturn_note"] = str(row.get("note") or "")
                current.pop("overturn_proposed", None)
    return out


def records(project_dir=None) -> dict[str, dict]:
    return fold(events(project_dir=project_dir))


def get(refutation_id: str, project_dir=None) -> dict | None:
    return records(project_dir=project_dir).get(refutation_id)


def assert_refutation(*, subject: str, verdict: str, scope: str,
                      evidence, channel: str, anchors=(), revisit_when: str = "",
                      ratified: bool = False, project_dir=None) -> str:
    subject = _text("subject", subject)
    verdict = _text("verdict", verdict)
    scope = _text("scope", scope)
    revisit_when = _text("revisit_when", revisit_when, required=False)
    evidence = _evidence(evidence)
    anchors = _anchors(anchors)
    # Identity derives from the bytes that can actually persist.  Computing
    # the id before redaction would leave a stable hash of secret-bearing raw
    # text and make a later revision/search over the redacted record disagree.
    subject, _ = redact.redact_text(subject)
    verdict, _ = redact.redact_text(verdict)
    scope, _ = redact.redact_text(scope)
    if ratified and CHANNEL_AUTHORITY.get(channel) != "human":
        raise RefutationError(
            "activating with --ratify requires a human channel; this call "
            f"arrived through {channel!r}")
    ref_id = make_id(subject, scope)
    holder = _identity_holder(ref_id, project_dir=project_dir)
    if holder is not None:
        held = holder.get("refutation_id") or ref_id
        # #693: polarity-aware — a collision against a ruling must not send
        # the user to a verb that will refuse the id.
        verb = ("ruling" if holder.get("polarity") == "ruling" else "refute")
        raise RefutationError(
            f"{held} already exists for this subject and scope; use "
            f"`daimon {verb} revise {held}`")
    row = _stamp("asserted", ref_id, channel)
    row.update({
        "subject": subject,
        "verdict": verdict,
        "scope": scope,
        "anchors": anchors,
        "revisit_when": revisit_when,
        "evidence": evidence,
    })
    if ratified:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise RefutationError("refutation not written (daimon disabled, project unknown, or ledger unwritable)")
    return ref_id


def _guard_ruling_cap(project_dir=None, exclude: str = "") -> None:
    """#693: one chokepoint for every path that activates a ruling.

    Enforced at activation, never at render, so the always-present briefing
    section can never silently truncate. Candidates are never counted."""
    cap = config.ruling_cap()
    active = sorted(
        r["refutation_id"] for r in records(project_dir=project_dir).values()
        if r.get("polarity") == "ruling" and r["state"] == "active"
        and r["refutation_id"] != exclude)
    if len(active) >= cap:
        raise RefutationError(
            f"ruling cap reached ({cap} active): {', '.join(active)} — "
            "retire one first, or raise DAIMON_RULING_CAP deliberately")


def _guard_open_proposals(refutation_id: str, event: str,
                          project_dir=None) -> None:
    """#693: best-effort growth containment for the agent-writable proposal
    channels (append is unlocked; read-side boundedness is latest-wins)."""
    def _order(row):
        try:
            return int(row.get("order") or 0)
        except (TypeError, ValueError):
            return 0

    rows = [r for r in events(project_dir=project_dir)
            if r.get("refutation_id") == refutation_id]
    # Only a HUMAN-authority verdict resets the bound — an agent-channel row
    # wearing a verdict event name is inert in the fold and must not launder
    # the counter either. A content-bound ratify whose displayed key no
    # longer matches the current verdict is inert in the fold too, and the
    # adversary controls when that happens (revise during the confirm
    # window), so it must not hand back a proposal slot. Best-effort mirror:
    # the record's verdict may have moved again since the row, but the
    # reachable lever is the fresh-mismatch case this catches.
    current_key = None
    record = get(refutation_id, project_dir=project_dir)
    if record is not None:
        current_key = normalize.content_key(record.get("verdict") or "")
    verdicts = [r for r in rows
                if CHANNEL_AUTHORITY.get(_channel_of(r)) == "human"
                and r.get("event") in ("ratified", "overturned", "revised")
                and not (r.get("event") == "ratified"
                         and str(r.get("verdict_key") or "")
                         and str(r.get("verdict_key")) != current_key)]
    since = max((_order(r) for r in verdicts), default=-1)
    pending = [r for r in rows
               if r.get("event") == event and _order(r) > since]
    if len(pending) >= _MAX_OPEN_PROPOSALS:
        raise RefutationError(
            f"{refutation_id} already has {len(pending)} open "
            f"{event} row(s) awaiting a human verdict")


def _guard_ruling_text(subject, verdict) -> None:
    # Measured on what would actually be STORED: whitespace collapsed and
    # redaction applied (a short secret can lengthen into its placeholder).
    for name, value in (("subject", subject), ("verdict", verdict)):
        if value is None:
            continue
        stored, _ = redact.redact_text(
            _SPACE_RE.sub(" ", str(value)).strip())
        if len(stored) > _MAX_RULING_TEXT:
            raise RefutationError(
                f"ruling {name} exceeds {_MAX_RULING_TEXT} chars — a "
                "standing rule that long is a document, not a ruling")


def assert_ruling(*, subject: str, verdict: str, scope: str,
                  evidence, channel: str, anchors=(), revisit_when: str = "",
                  ratified: bool = False, project_dir=None) -> str:
    """#693: found a positive-polarity record. Same row schema, same id
    space, same identity-collision refusal as a refutation — the polarity is
    the founding event name (`ruled`), derived at fold time."""
    _guard_ruling_text(subject, verdict)
    subject = _text("subject", subject)
    verdict = _text("verdict", verdict)
    scope = _text("scope", scope)
    revisit_when = _text("revisit_when", revisit_when, required=False)
    evidence = _evidence(evidence)
    anchors = _anchors(anchors)
    subject, _ = redact.redact_text(subject)
    verdict, _ = redact.redact_text(verdict)
    scope, _ = redact.redact_text(scope)
    if ratified and CHANNEL_AUTHORITY.get(channel) != "human":
        raise RefutationError(
            "activating with --ratify requires a human channel; this call "
            f"arrived through {channel!r}")
    if ratified:
        _guard_ruling_cap(project_dir=project_dir)
    ref_id = make_id(subject, scope)
    holder = _identity_holder(ref_id, project_dir=project_dir)
    if holder is not None:
        held = holder.get("refutation_id") or ref_id
        verb = ("ruling" if holder.get("polarity") == "ruling" else "refute")
        raise RefutationError(
            f"{held} already exists for this subject and scope; use "
            f"`daimon {verb} revise {held}`")
    row = _stamp("ruled", ref_id, channel)
    row.update({
        "subject": subject,
        "verdict": verdict,
        "scope": scope,
        "anchors": anchors,
        "revisit_when": revisit_when,
        "evidence": evidence,
    })
    if ratified:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise RefutationError(
            "ruling not written (daimon disabled, project unknown, or "
            "ledger unwritable)")
    return ref_id


def retire(ruling_id: str, *, channel: str, evidence=(), note: str = "",
           project_dir=None) -> str:
    """#693: end an active ruling. Human channels retire directly; an agent
    channel gets a proposal and the ruling stands. Evidence is OPTIONAL —
    a rule that simply stopped applying has no citation, and requiring one
    manufactures fake evidence."""
    current = get(ruling_id, project_dir=project_dir)
    if current is None:
        raise RefutationError(f"unknown ruling: {ruling_id}")
    if current.get("polarity") != "ruling":
        raise RefutationError(
            f"{ruling_id} is a refutation; use `daimon refute overturn`")
    if current["state"] == "overturned":
        raise RefutationError(f"{ruling_id} is already retired")
    event = ("overturned" if CHANNEL_AUTHORITY.get(channel) == "human"
             else "overturn-proposed")
    if event == "overturn-proposed":
        _guard_open_proposals(ruling_id, event, project_dir=project_dir)
    row = _stamp(event, ruling_id, channel)
    row["evidence"] = _evidence(evidence, required=False)
    row["note"] = _text("note", note, required=False)
    if not append(row, project_dir=project_dir):
        raise RefutationError("retirement not written")
    return event


def ratify(refutation_id: str, *, channel: str, note: str = "",
           verdict_key: str = "", project_dir=None) -> None:
    # Ratification is the transition that makes a record load-bearing, so it
    # is the one that must not be self-declarable.  The caller names the
    # channel it OBSERVED; authority is derived from that, so an agent cannot
    # reach this state by describing itself differently.
    if CHANNEL_AUTHORITY.get(channel) != "human":
        raise RefutationError(
            "ratification requires a human channel; this call arrived "
            f"through {channel!r}")
    current = get(refutation_id, project_dir=project_dir)
    if current is None:
        raise RefutationError(f"unknown refutation: {refutation_id}")
    if current["state"] == "overturned":
        raise RefutationError(
            "an overturned refutation cannot be ratified; revise it with new evidence first")
    if (current.get("polarity") == "ruling"
            and current["state"] != "active"):
        _guard_ruling_cap(project_dir=project_dir, exclude=refutation_id)
    row = _stamp("ratified", refutation_id, channel)
    row["note"] = _text("note", note, required=False)
    # #693: content-bound ratification — the CLI records the key of the
    # verdict it DISPLAYED, and the fold refuses to activate any other text.
    # A hash, never plaintext, and absent means unbound.
    if verdict_key:
        row["verdict_key"] = str(verdict_key)
    if not append(row, project_dir=project_dir):
        raise RefutationError("ratification not written")


def revise(refutation_id: str, *, channel: str, evidence,
           subject=None, verdict=None, scope=None, anchors=None,
           revisit_when=None, ratified: bool = False, project_dir=None) -> None:
    current = get(refutation_id, project_dir=project_dir)
    if current is None:
        raise RefutationError(f"unknown refutation: {refutation_id}")
    if ratified and CHANNEL_AUTHORITY.get(channel) != "human":
        raise RefutationError(
            "activating a revision requires a human channel; this call "
            f"arrived through {channel!r}")
    event = "revised"
    if current.get("polarity") == "ruling":
        # #693: the lifecycle is polarity-asymmetric. A retired ruling is
        # not resurrectable by revise; an agent revising an ACTIVE ruling
        # writes a proposal that leaves the render untouched; a human
        # revising an active ruling keeps it active — demotion is never a
        # side effect of editing (only ratify and retire change render
        # membership). The ceremony boundary is WHAT RENDERS: human edits
        # to scope/anchors/revisit_when on an active ruling apply without a
        # confirm because they do not move the rendered text; verdict and
        # subject edits go through the CLI's display-and-confirm.
        _guard_ruling_text(subject, verdict)
        if current["state"] == "overturned":
            raise RefutationError(
                f"{refutation_id} is retired; propose a new ruling instead "
                "of reviving it")
        if current["state"] == "active":
            if CHANNEL_AUTHORITY.get(channel) != "human":
                event = "revision-proposed"
                _guard_open_proposals(refutation_id, event,
                                      project_dir=project_dir)
            else:
                ratified = True
        elif ratified:
            # Module-enforced, same reasoning as overturn(): in-process
            # writers reach this function directly, and activating a
            # candidate ruling here would be the revise --ratify side door
            # one layer down — no ceremony, no content binding. The only
            # activation paths are ratify() and found-with-ratify.
            raise RefutationError(
                f"a candidate ruling activates only through ratify(); "
                f"revise {refutation_id} first, then ratify it")
    row = _stamp(event, refutation_id, channel)
    row["evidence"] = _evidence(evidence)
    if subject is not None:
        row["subject"] = _text("subject", subject)
    if verdict is not None:
        row["verdict"] = _text("verdict", verdict)
    if scope is not None:
        row["scope"] = _text("scope", scope)
    if anchors is not None:
        row["anchors"] = _anchors(anchors)
    if revisit_when is not None:
        row["revisit_when"] = _text(
            "revisit_when", revisit_when, required=False)
    if not any(key in row for key in (
            "subject", "verdict", "scope", "anchors", "revisit_when")):
        raise RefutationError(
            "revision changes nothing; provide a new subject, verdict, scope, "
            "anchor set, or revisit condition")
    # #646 from the revise side: a revision that moves this record onto another
    # record's subject+scope is the same defect as asserting a duplicate, and
    # the render would drop one of the two ratified verdicts either way.
    # Redacted first, because that is what will be stored and therefore what a
    # later assertion's identity is computed from.
    if "subject" in row or "scope" in row:
        new_subject, _ = redact.redact_text(
            row.get("subject", current.get("subject") or ""))
        new_scope, _ = redact.redact_text(
            row.get("scope", current.get("scope") or ""))
        holder = _identity_holder(make_id(new_subject, new_scope),
                                  exclude=refutation_id,
                                  project_dir=project_dir)
        if holder is not None:
            held = holder.get("refutation_id")
            raise RefutationError(
                f"{held} already exists for that subject and scope; a "
                "revision cannot take over another record's identity")
    if ratified:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise RefutationError("revision not written")


def overturn(refutation_id: str, *, channel: str, evidence, note: str = "",
             project_dir=None) -> str:
    current = get(refutation_id, project_dir=project_dir)
    if current is None:
        raise RefutationError(f"unknown refutation: {refutation_id}")
    if current.get("polarity") == "ruling":
        # #693: module-enforced, not CLI convention — in-process writers
        # (ui/signed) reach this function directly, and retire() carries the
        # ruling guards (polarity, proposal bound, optional evidence).
        raise RefutationError(
            f"{refutation_id} is a ruling; use retire()")
    if current["state"] == "overturned":
        raise RefutationError(f"{refutation_id} is already overturned")
    event = ("overturned" if CHANNEL_AUTHORITY.get(channel) == "human"
             else "overturn-proposed")
    row = _stamp(event, refutation_id, channel)
    row["evidence"] = _evidence(evidence)
    row["note"] = _text("note", note, required=False)
    if not append(row, project_dir=project_dir):
        raise RefutationError("overturn event not written")
    return event


def listing(*, states=None, polarity=None, project_dir=None) -> list[dict]:
    """Every record in `refute list` order: active first, then most recently
    updated, ties broken by id. This sort is the CLI's presentation contract;
    it lives here so the viewer's lane and the CLI cannot drift apart.

    `polarity` (#693): None returns everything; "refutation"/"ruling" scopes
    the lane — `refute list` and `ruling list` never mix polarities."""
    wanted = set(states or STATES)
    unknown = wanted - STATES
    if unknown:
        raise RefutationError(f"unknown state: {', '.join(sorted(unknown))}")
    rows = [row for row in records(project_dir=project_dir).values()
            if row["state"] in wanted
            and (polarity is None or row.get("polarity") == polarity)]
    rows.sort(key=lambda row: (row.get("state") != "active",
                               row.get("updated_at") or "",
                               row["refutation_id"]))
    return rows


def search(query: str, *, project_dir=None, states=None) -> list[dict]:
    query = _text("query", query)
    wanted = set(states or STATES)
    unknown = wanted - STATES
    if unknown:
        raise RefutationError(f"unknown state: {', '.join(sorted(unknown))}")
    q = query.casefold()
    q_terms = set(re.findall(r"[\w.-]{2,}", q))
    issue_anchors = {f"issue:{n}" for n in _ISSUE_RE.findall(q)}
    scored = []
    for record in records(project_dir=project_dir).values():
        if record["state"] not in wanted:
            continue
        anchors = set(record.get("anchors") or [])
        haystack = " ".join((
            record.get("subject") or "", record.get("verdict") or "",
            record.get("scope") or "", " ".join(anchors),
        )).casefold()
        terms = set(re.findall(r"[\w.-]{2,}", haystack))
        anchor_hit = bool(issue_anchors & anchors)
        phrase_hit = q in haystack
        overlap = len(q_terms & terms)
        if not (anchor_hit or phrase_hit or overlap):
            continue
        scored.append((anchor_hit, phrase_hit, overlap,
                       record.get("updated_at") or "", record))
    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3]),
                reverse=True)
    return [row[-1] for row in scored]


def guard(query: str, *, anchors=(), project_dir=None) -> list[dict]:
    """High-precision active matches only: stable anchor or subject phrase.

    Refutation polarity only (#693): the shipped guidance reads a guard hit
    as "this approach lost", and a standing ruling is the opposite claim."""
    query = _text("query", query)
    q = query.casefold()
    query_anchors = set(_anchors(anchors))
    query_anchors.update(f"issue:{n}" for n in _ISSUE_RE.findall(q))
    matches = []
    for record in records(project_dir=project_dir).values():
        if record["state"] != "active":
            continue
        if record.get("polarity") == "ruling":
            continue
        record_anchors = set(record.get("anchors") or [])
        subject = _SPACE_RE.sub(" ", record.get("subject") or "").strip().casefold()
        anchor_hits = sorted(query_anchors & record_anchors)
        subject_hit = bool(subject and len(subject) >= 8 and subject in q)
        if not anchor_hits and not subject_hit:
            continue
        copy = dict(record)
        copy["guard_match"] = {
            "rail": "anchor" if anchor_hits else "subject",
            "anchors": anchor_hits,
        }
        matches.append(copy)
    return sorted(matches, key=lambda row: (
        0 if row["guard_match"]["rail"] == "anchor" else 1,
        row["refutation_id"],
    ))
