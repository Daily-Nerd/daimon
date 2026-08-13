import json
from pathlib import Path

import lineage_lab
from daimon_briefing import policy, store


PROJECT = "/repo/identity-fixture"


def _cp(sid, created, questions=(), decisions=(), beliefs=()):
    cp = {
        "session_id": sid,
        "created": created,
        "project_slug": store.project_slug(PROJECT),
        "working_context": {
            "open_questions": [{"text": text} for text in questions],
            "recent_decisions": [{"text": text} for text in decisions],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": text} for text in beliefs],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }
    policy.stamp_item_ids(cp)
    return cp


def _write(root: Path, cp: dict):
    (root / f"{cp['session_id']}.json").write_text(
        json.dumps(cp, indent=2), encoding="utf-8")


def test_shadow_run_is_deterministic_and_source_bytes_do_not_change(tmp_path):
    source = tmp_path / "checkpoints"
    source.mkdir()
    first = _cp(
        "S1", "2026-01-01T00:00:00Z",
        questions=["Should the lineage ledger remain append only?"],
        decisions=["Keep checkpoint representations immutable"],
        beliefs=["False identity merges are worse than missed links"],
    )
    second = _cp(
        "S2", "2026-01-02T00:00:00Z",
        decisions=[
            "The lineage ledger should remain append only",
            "Checkpoint representations remain immutable",
        ],
        beliefs=["Invented continuity is worse than a missed relationship"],
    )
    _write(source, first)
    _write(source, second)
    before = {path.name: path.read_bytes() for path in source.iterdir()}

    one = lineage_lab.run(source, PROJECT, tmp_path / "out-one")
    two = lineage_lab.run(source, PROJECT, tmp_path / "out-two")

    assert one == two
    assert one["source_byte_integrity"] == "verified"
    assert one["consumer_effects"] == {
        "checkpoint_writes": 0,
        "events_writes": 0,
        "recall_writes": 0,
        "viewer_changes": 0,
    }
    assert before == {path.name: path.read_bytes() for path in source.iterdir()}
    assert (tmp_path / "out-one" / "review.html").exists()
    assert (tmp_path / "out-one" / "candidates.jsonl").exists()


def test_changed_shared_id_is_reviewed_not_confirmed():
    first = _cp("S1", "2026-01-01T00:00:00Z",
                decisions=["Keep the record immutable"])
    second = _cp("S2", "2026-01-02T00:00:00Z",
                 decisions=["The record remains immutable"])
    old = first["working_context"]["recent_decisions"][0]
    second["working_context"]["recent_decisions"][0]["id"] = old["id"]

    candidates, metrics = lineage_lab.compare_transition(first, second)

    assert metrics["shared_id_text_changes"] == 1
    assert len(candidates) == 1
    assert candidates[0]["state"] == "candidate"
    assert candidates[0]["matched_by"] == ["legacy-shared-id"]


def test_ambiguous_overlap_is_never_collapsed_to_one_guess():
    first = _cp(
        "S1", "2026-01-01T00:00:00Z",
        decisions=[
            "Persist immutable checkpoint records",
            "Review lineage relation candidates",
        ],
    )
    second = _cp(
        "S2", "2026-01-02T00:00:00Z",
        decisions=[
            "Persist immutable checkpoint records while reviewing lineage relation candidates"
        ],
    )

    candidates, _ = lineage_lab.compare_transition(first, second)

    revisions = [row for row in candidates if row["relation"] == "revision-of"]
    assert len(revisions) == 2
    assert all(row["ambiguous"] for row in revisions)


def test_explicit_supersession_is_typed_not_identity():
    first = _cp("S1", "2026-01-01T00:00:00Z",
                decisions=["Use mutable memory records"])
    second = _cp("S2", "2026-01-02T00:00:00Z",
                 decisions=["Use immutable relation records"])
    old = first["working_context"]["recent_decisions"][0]
    new = second["working_context"]["recent_decisions"][0]
    new["links"] = [{"type": "supersedes", "target": old["id"]}]

    candidates, _ = lineage_lab.compare_transition(first, second)

    supersedes = [row for row in candidates if row["relation"] == "supersedes"]
    assert len(supersedes) == 1
    assert supersedes[0]["state"] == "candidate"
    assert "typed-supersedes" in supersedes[0]["matched_by"]
