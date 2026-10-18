"""`daimon refute guard`: does this text revisit an approach that lost?

A reader-facing match, so it runs over the rows a reader may see and never
over the folded ledger. A value the read withholds (forgotten, quarantined or
closed) cannot fire the guard: firing would be an oracle on a value the read
must treat as absent (#1208). The ledger (`refutations`) owns the rows and
their fold; deciding which of them a reader may match against is the view's
job, and this module sits above it.
"""

from __future__ import annotations

from . import refutations, view


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
