"""Import CALIBRATION-set human labels as lab-import proposals (#678).

Ratified boundaries (2026-08-13, decisions.jsonl):
- Calibration partition ONLY; the held-out partition stays out of the
  product ledger until the gate evaluation is spent.
- Every imported row is a `lab-import` PROPOSAL (agent authority).  Human
  judgments influence which type is proposed — thought keeps the matcher's
  relation, arc becomes same-arc — but no imported row confirms anything.
- `legacy-shared-id` is not a persistable rail (derived at read time); rows
  whose evidence is legacy-only are skipped and counted.

Idempotent: candidate ids already present in the ledger are skipped, so a
re-run appends nothing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from daimon_briefing import relations

HERE = Path(__file__).parent
OUT = HERE / "out-current"

KIND_TO_FIELD = {
    "question": "open_questions",
    "decision": "recent_decisions",
    "belief": "strong_beliefs",
    "uncertainty": "uncertainties",
    "contradiction": "contradictions_flagged",
}


def _endpoint(side: dict) -> dict:
    return {
        "session_id": side["session_id"],
        "field": KIND_TO_FIELD[side["kind"]],
        "item_id": side["item_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="write proposals; default is a dry run")
    args = parser.parse_args()

    partitions = json.loads(
        (OUT / "partitions-frozen-2026-08-12.json").read_text())
    calibration = partitions["calibration"]          # {candidate_id: label}
    rows = {r["candidate_id"]: r for r in (
        json.loads(line) for line in
        (OUT / "candidates.jsonl").read_text().splitlines())}
    existing = set(relations.records(project_dir=args.project))

    planned, skipped_legacy, skipped_present, errors = [], 0, 0, []
    for cid, label in sorted(calibration.items()):
        row = rows[cid]
        rails = [r for r in row["matched_by"] if r in relations.RAILS]
        if not rails:
            skipped_legacy += 1
            continue
        type_ = "same-arc" if label == "arc" else row["relation"]
        frm = _endpoint(row["current"])
        to = _endpoint(row["previous"])
        try:
            rel_id = relations.make_id(
                type_,
                relations._validate_endpoint("from", frm),
                relations._validate_endpoint("to", to))
        except relations.RelationError as err:
            errors.append((cid, str(err)))
            continue
        if rel_id in existing:
            skipped_present += 1
            continue
        planned.append((cid, label, type_, frm, to, rails))

    print(f"calibration labels: {len(calibration)}")
    print(f"planned proposals:  {len(planned)}")
    print(f"skipped legacy-only rails: {skipped_legacy}")
    print(f"skipped already-present:   {skipped_present}")
    for cid, err in errors:
        print(f"REFUSED {cid}: {err}")
    if not args.apply:
        print("dry run — nothing written (pass --apply)")
        return 0

    written = 0
    for cid, label, type_, frm, to, rails in planned:
        relations.propose(
            type_=type_,
            from_endpoint=frm,
            to_endpoint=to,
            matched_by=rails,
            matcher_version="lab-2026-08-12",
            channel="lab-import",
            project_dir=args.project,
        )
        written += 1
    folded = relations.records(project_dir=args.project)
    states: dict[str, int] = {}
    for record in folded.values():
        states[record["state"]] = states.get(record["state"], 0) + 1
    print(f"written: {written}; ledger records now {len(folded)} {states}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
