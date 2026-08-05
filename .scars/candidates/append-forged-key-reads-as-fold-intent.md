---
id: 0
type: landmine
title: "A ledger writer must normalise only keys the caller set — in an append-only stream the ABSENCE of a key is data, and a fold that reads presence as intent will silently clear the field"
severity: high
confidence: 0.9
created: 2026-08-05
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/refutations.py
  - path: plugin/daimon_briefing/policy.py
  - path: plugin/daimon_briefing/store.py
evidence:
  - pr: 575
  - note: "2026-08-05 review of #575: `refute revise` without --anchor cleared every anchor, so guard() stopped matching while `refute show` still rendered [active . human-ratified]. Reproduced independently by two reviewers in isolated DAIMON_CHECKPOINT_DIR dirs; the repo's own suite drove the failing sequence and passed."
expires:
  condition: "no ledger fold distinguishes replace-from-unchanged by key presence (e.g. every partial update carries an explicit clear flag)"
  review_after: 2027-02-05
status: candidate
---

`refutations.append` normalised every row with an unconditional
`row["anchors"] = _scrub_list(list(row.get("anchors") or []))`, forging the key
as `[]` when the caller never set it. `fold` decides replacement by key PRESENCE
(`if "anchors" in row`) and `revise` uses `is not None` to mean "unchanged", so
the writer destroyed the sentinel before the fold could read it. Any revision
that did not pass `--anchor` cleared every anchor: the guard stopped firing
while the record still displayed as active and human-ratified.

The rule: normalise only keys the caller actually set (`if key in row:`), and
never let a writer and a fold disagree about what absence means. If the fold
reads presence as intent, the writer must not manufacture presence.

`policy.admit_row` is anchored because it is the shared admission seam for
every stream — adding list-field normalisation there would inject this into
events.jsonl, verification.jsonl and forget-hits.jsonl at once. `store.py` is
anchored because it owns those writers. Neither has the defect today; they are
where it would be made next.

Sibling of scar 0025 (fold ignores `kind`, so any event resolves the item).
Same family: an append-only writer and its fold disagreeing, failing silent.
Both shipped green. Patch coverage cannot catch this class — every line of the
fold executes, on the wrong data. When you touch a fold, assert on the FOLDED
STATE, never on an exit code.
