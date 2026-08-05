---
id: 42
type: landmine
title: "A ledger writer must normalise only keys the caller set — in an append-only stream the ABSENCE of a key is data, and a fold that reads presence as intent will silently clear the field"
severity: high
confidence: 0.9
created: 2026-08-05
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/refutations.py
  - path: plugin/daimon_briefing/policy.py
evidence:
  - pr: 575
  - note: 2026-08-05 review of #575: `refute revise` without --anchor cleared every anchor, so guard() stopped matching while `refute show` still rendered [active . human-ratified]. Reproduced independently by two reviewers in isolated DAIMON_CHECKPOINT_DIR dirs; the repo's own suite drove the failing sequence and passed.
expires:
  condition: "no ledger fold distinguishes replace-from-unchanged by key presence (e.g. every partial update carries an explicit clear flag)"
  review_after: 2027-02-05
status: active
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

`policy.admit_row` is anchored because it is the shared admission seam —
adding list-field normalisation there would inject this into events.jsonl and
verification.jsonl at once. It does not have the defect today; it is where it
would be made next.

Corrected 2026-08-05 after review: this scar first also claimed forget-hits.jsonl
crosses `admit_row`, and anchored `store.py` as the owner of those writers. Both
were wrong. `store.record_forget_hits` (store.py:1085) writes its rows with a
bare `json.dumps` and never reaches the seam, so that stream is out of scope;
and the injection risk lives at the seam rather than at its callers, so a
high-severity anchor on a 1,445-line file with 31 commits in 90 days bought
false fires and no coverage. Anchor the seam, not everything downstream of it.

Sibling of scar 0025 (fold ignores `kind`, so any event resolves the item).
Same family: an append-only writer and its fold disagreeing, failing silent.
Both shipped green. Patch coverage cannot catch this class — every line of the
fold executes, on the wrong data. When you touch a fold, assert on the FOLDED
STATE, never on an exit code.
