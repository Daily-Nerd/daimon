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
_EVENT_RANK = {
    # Same-order ambiguity fails toward less authority: ratification before a
    # revision leaves the revision candidate; overturn remains last.
    "asserted": 0,
    "ratified": 1,
    "activated": 1,
    "revised": 2,
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
    out = []
    seen = set()
    for raw in values or []:
        anchor = canonical_anchor(raw)
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


def forget_content_key(content_key: str, *, project_dir=None) -> list[str]:
    """Remove every record whose subject folds to `content_key` (#578).

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
    doomed = {
        str(row.get("refutation_id") or "")
        for row in events(project_dir=project_dir)
        if "subject" in row
        and normalize.content_key(str(row.get("subject") or "")) == content_key
    }
    doomed.discard("")
    if not doomed:
        return []
    # Rewrite RAW LINES, never `events()` output. That reader is deliberately
    # tolerant — it drops malformed lines and rows whose `event` it does not
    # recognise, and it stamps a `_line` key onto what it returns. Round-tripping
    # through it would silently delete every row a future daimon added and write
    # `_line` into the ledger. Scars 0025 and 0042 are both this shape: a
    # forgiving read feeding a write.
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
        if event == "asserted":
            if current is not None:
                continue  # duplicate logical assertion, first writer wins
            state = (
                "active" if row.get("ratified") is True
                and CHANNEL_AUTHORITY.get(row.get("channel")) == "human" else "candidate")
            out[ref_id] = {
                "refutation_id": ref_id,
                "state": state,
                "subject": str(row.get("subject") or ""),
                "verdict": str(row.get("verdict") or ""),
                "scope": str(row.get("scope") or ""),
                "anchors": list(row.get("anchors") or []),
                "revisit_when": str(row.get("revisit_when") or ""),
                "evidence": list(row.get("evidence") or []),
                "asserted_by": row.get("authority"),
                "asserted_author": row.get("author"),
                "activation": (CHANNEL_LABEL.get(row.get("channel"))
                               if state == "active" else None),
                "activation_channel": (row.get("channel")
                                       if state == "active" else None),
                "activation_author": (
                    row.get("author") if state == "active" else None),
                "created_at": row.get("ts"),
                "updated_at": row.get("ts"),
                "revision": 1,
                "history_count": 1,
            }
            continue
        if current is None:
            continue  # orphan lifecycle event: visible in raw audit, inert here
        current["history_count"] += 1
        current["updated_at"] = row.get("ts") or current["updated_at"]
        if event == "ratified":
            if current["state"] != "overturned" and CHANNEL_AUTHORITY.get(row.get("channel")) == "human":
                current["state"] = "active"
                current["activation"] = CHANNEL_LABEL.get(row.get("channel"))
                current["activation_channel"] = row.get("channel")
                current["activation_author"] = row.get("author")
        elif event == "activated":
            if current["state"] != "overturned" and CHANNEL_AUTHORITY.get(row.get("channel")) == "mechanical":
                current["state"] = "active"
                current["activation"] = "mechanically-activated"
                current["activation_channel"] = "mechanical"
                current["activation_author"] = row.get("author")
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
            current["state"] = (
                "active" if row.get("ratified") is True
                and CHANNEL_AUTHORITY.get(row.get("channel")) == "human" else "candidate")
            current["activation"] = (
                CHANNEL_LABEL.get(row.get("channel"))
                if current["state"] == "active" else None)
            current["activation_channel"] = (
                row.get("channel") if current["state"] == "active" else None)
            current["activation_author"] = (
                row.get("author") if current["state"] == "active" else None)
            current["revision"] += 1
            current.pop("overturn_proposed", None)
        elif event == "overturn-proposed":
            if current["state"] == "active":
                current["overturn_proposed"] = {
                    "by": row.get("authority"),
                    "evidence": list(row.get("evidence") or []),
                    "note": str(row.get("note") or ""),
                }
        elif event == "overturned":
            if CHANNEL_AUTHORITY.get(row.get("channel")) == "human":
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
    if get(ref_id, project_dir=project_dir) is not None:
        raise RefutationError(
            f"{ref_id} already exists; use `daimon refute revise {ref_id}`")
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


def ratify(refutation_id: str, *, channel: str, note: str = "",
           project_dir=None) -> None:
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
    row = _stamp("ratified", refutation_id, channel)
    row["note"] = _text("note", note, required=False)
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
    row = _stamp("revised", refutation_id, channel)
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
    if ratified:
        row["ratified"] = True
    if not append(row, project_dir=project_dir):
        raise RefutationError("revision not written")


def overturn(refutation_id: str, *, channel: str, evidence, note: str = "",
             project_dir=None) -> str:
    current = get(refutation_id, project_dir=project_dir)
    if current is None:
        raise RefutationError(f"unknown refutation: {refutation_id}")
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
    """High-precision active matches only: stable anchor or subject phrase."""
    query = _text("query", query)
    q = query.casefold()
    query_anchors = set(_anchors(anchors))
    query_anchors.update(f"issue:{n}" for n in _ISSUE_RE.findall(q))
    matches = []
    for record in records(project_dir=project_dir).values():
        if record["state"] != "active":
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
