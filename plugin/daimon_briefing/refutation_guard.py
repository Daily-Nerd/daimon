"""`daimon refute guard` and `daimon refute search`: reader-facing matches
over the refutations ledger.

A match runs over the values a reader may see and never over the folded
ledger. A value the read withholds (forgotten, quarantined or closed) cannot
fire the guard (#1208) or decide a search hit or its rank (#1211): either
would be an oracle on a value the read must treat as absent. The ledger
(`refutations`) owns the rows and their fold; deciding which of them a reader
may match against is the view's job, and this module sits above it.
"""

from __future__ import annotations

import re

from . import refutations, view

_TERM_RE = re.compile(r"[\w.-]{2,}")
_TEXT_FIELDS = ("subject", "verdict", "scope")


def guard(query: str, *, anchors=(), project_dir=None) -> list[dict]:
    """High-precision active matches only: stable anchor or subject phrase.

    Refutation polarity only (#693): the shipped guidance reads a guard hit
    as "this approach lost", and a standing ruling is the opposite claim."""
    query = refutations._text("query", query)
    q = query.casefold()
    query_anchors = set(refutations._anchors(anchors))
    query_anchors.update(
        f"issue:{n}" for n in refutations._ISSUE_RE.findall(q))
    active = [record
              for record in refutations.records(project_dir=project_dir).values()
              if record["state"] == "active"
              and record.get("polarity") != "ruling"]
    matches = []
    for record in view.masked(project_dir, "refutations.jsonl", active):
        record_anchors = set(record.get("anchors") or [])
        subject = refutations._SPACE_RE.sub(
            " ", record.get("subject") or "").strip().casefold()
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


def _visible_text(raw: dict, shown: dict) -> tuple[str, set]:
    """The searchable text and anchors of a record. `shown` is `view.masked`'s
    copy of `raw`: a field it changed (blanked, dropped from the anchors or
    replaced by the withheld marker) is withheld and contributes nothing, so
    neither the value nor the marker text can match."""
    parts = [shown.get(name) or "" for name in _TEXT_FIELDS
             if shown.get(name) == raw.get(name)]
    raw_anchors = set(raw.get("anchors") or [])
    anchors = {a for a in shown.get("anchors") or [] if a in raw_anchors}
    return " ".join([*parts, *anchors]).casefold(), anchors


def search(query: str, *, project_dir=None, states=None) -> list[dict]:
    """Records whose visible subject, verdict, scope or anchors match `query`,
    best first, as a reader may see them (both polarities, see #693)."""
    query = refutations._text("query", query)
    wanted = set(states or refutations.STATES)
    unknown = wanted - refutations.STATES
    if unknown:
        raise refutations.RefutationError(
            f"unknown state: {', '.join(sorted(unknown))}")
    q = query.casefold()
    q_terms = set(_TERM_RE.findall(q))
    issue_anchors = {f"issue:{n}" for n in refutations._ISSUE_RE.findall(q)}
    raws = [record
            for record in refutations.records(project_dir=project_dir).values()
            if record["state"] in wanted]
    scored = []
    for raw, shown in zip(
            raws, view.masked(project_dir, "refutations.jsonl", raws)):
        haystack, anchors = _visible_text(raw, shown)
        anchor_hit = bool(issue_anchors & anchors)
        phrase_hit = q in haystack
        overlap = len(q_terms & set(_TERM_RE.findall(haystack)))
        if not (anchor_hit or phrase_hit or overlap):
            continue
        scored.append((anchor_hit, phrase_hit, overlap,
                       shown.get("updated_at") or "", shown))
    scored.sort(key=lambda row: (row[0], row[1], row[2], row[3]),
                reverse=True)
    return [row[-1] for row in scored]
