"""#1363: the lifecycle fold reads lifecycle rows only.

`events.jsonl` carries rows of several kinds. Only the lifecycle kinds (a row
with no kind, `resolution`, and the forget pair `tombstone` and `lift`) say
whether an item is live. Every other kind, known or not yet invented, is inert
to the fold: it can sit on any ref, including a real item id, and the item
stays visible.

The first block pins what the fold does with lifecycle rows and passes on both
sides of the change. The second block is the defect.
"""
import json
import random
import sqlite3

import pytest

from daimon_briefing import config, recall, store, view
from daimon_briefing.surfaces import Writer

_P = "/repo/lifecycle-kinds"
_ITEM = "o-1a2b3c4d5e"


def _row(ref, status, ts, kind="resolution", **extra):
    row = {"ts": ts, "item_ref": ref, "status": status, "source": "cli", **extra}
    if kind is not None:
        row["kind"] = kind
    return row


def _plant(*rows, project=_P):
    path = config.checkpoint_dir() / store.project_slug(project) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        for row in rows:
            handle.write((json.dumps(row) + "\n").encode("utf-8"))
    return path


def _resolved(rows, ref=_ITEM):
    return store.is_resolved(store.fold_resolutions(rows).get(ref))


# ---- pins: lifecycle rows fold as before -------------------------------------

_STATUS_VERDICTS = [
    ("resolving-candidate:claim", False),
    ("resolved-agent-verified", True),
    ("resolved", True),
    ("supersede-candidate:o-other", False),
    ("reopened", False),
    ("reopen", False),
    ("REOPENED:why", False),
    ("merged", True),
    ("closed", True),
    ("superseded-by:o-other", True),
    ("banana", True),
]


@pytest.mark.parametrize("kind", [None, "resolution"])
@pytest.mark.parametrize("status,resolved", _STATUS_VERDICTS)
def test_lifecycle_status_verdicts(status, resolved, kind):
    rows = [_row(_ITEM, status, "2026-10-01T00:00:00Z", kind=kind)]
    assert _resolved(rows) is resolved


def test_reopen_revives_a_resolved_item():
    rows = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z"),
            _row(_ITEM, "reopened", "2026-10-02T00:00:00Z")]
    assert not _resolved(rows)


def test_a_resolution_after_a_reopen_closes_it_again():
    rows = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z"),
            _row(_ITEM, "reopened", "2026-10-02T00:00:00Z"),
            _row(_ITEM, "closed", "2026-10-03T00:00:00Z")]
    assert _resolved(rows)


def test_a_candidate_leaves_the_item_live():
    rows = [_row(_ITEM, "supersede-candidate:o-other", "2026-10-01T00:00:00Z")]
    assert not _resolved(rows)


def test_ref_less_rows_are_ignored():
    rows = [_row("", "resolved", "2026-10-01T00:00:00Z"),
            _row("", "active", "2026-10-01T00:00:00Z", kind="handoff")]
    assert store.fold_resolutions(rows) == {}


def test_corroboration_rows_on_a_namespaced_ref_do_not_touch_the_item():
    rows = [_row(_ITEM, "supersede-candidate:o-x", "2026-10-01T00:00:00Z"),
            _row(store.corroboration_ref(_ITEM), "corroborated-by:S-2",
                 "2026-10-02T00:00:00Z", kind="corroboration")]
    folded = store.fold_resolutions(rows)
    assert folded[_ITEM]["status"] == "supersede-candidate:o-x"
    assert not _resolved(rows)


def test_forget_resolves_and_unforget_revives_and_standing_agrees():
    key = "ab12cd34ef56"
    keyref = store.new_key_ref()
    forget = [_row(_ITEM, f"forgotten:{key}", "2026-10-01T00:00:00Z",
                   kind="tombstone"),
              _row(keyref, f"forgotten:{key}", "2026-10-01T00:00:00Z",
                   kind="tombstone")]
    assert _resolved(forget)
    assert key in store.standing_tombstones(forget)
    lifted = forget + [
        _row(_ITEM, store.LIFT_STATUS, "2026-10-02T00:00:00Z", kind="lift"),
        _row(keyref, store.LIFT_STATUS, "2026-10-02T00:00:00Z", kind="lift")]
    assert not _resolved(lifted)
    assert store.standing_tombstones(lifted) == {}


def test_the_forget_writer_rows_fold_as_a_tombstone(tmp_checkpoint_dir):
    key = "ab12cd34ef56"
    assert store.append_event(
        _ITEM, f"forgotten:{key}", kind="tombstone", tombstone=True,
        project_dir=_P, source="cli", writer=Writer.HUMAN)
    folded = store.resolutions(project_dir=_P)
    assert store.is_resolved(folded[_ITEM])
    assert store.tombstone_refs(folded) == {_ITEM}


def test_a_row_with_no_kind_key_is_a_lifecycle_row():
    rows = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z", kind=None)]
    assert _resolved(rows)


# ---- the defect ---------------------------------------------------------------


def test_a_verification_row_on_a_real_ref_does_not_resolve_the_item(
        tmp_checkpoint_dir):
    assert store.append_event(_ITEM, "quote-verification-failed",
                              kind="verification", project_dir=_P,
                              source="serializer", writer=Writer.EMITTER)
    folded = store.resolutions(project_dir=_P)
    assert _ITEM not in folded
    assert not store.is_resolved(folded.get(_ITEM))


def test_an_unknown_future_kind_on_a_real_ref_is_inert(tmp_checkpoint_dir):
    _plant(_row(_ITEM, "done", "2026-10-01T00:00:00Z", kind="future-kind",
                extra=1))
    assert _ITEM not in store.resolutions(project_dir=_P)


def test_a_non_lifecycle_row_after_a_resolution_leaves_it_resolved():
    rows = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z"),
            _row(_ITEM, "reopened", "2026-10-02T00:00:00Z", kind="note")]
    assert _resolved(rows)


def test_a_non_lifecycle_row_after_a_reopen_leaves_it_live():
    rows = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z"),
            _row(_ITEM, "reopened", "2026-10-02T00:00:00Z"),
            _row(_ITEM, "done", "2026-10-03T00:00:00Z", kind="measurement")]
    assert not _resolved(rows)


def test_non_lifecycle_rows_never_change_the_fold():
    """Property: whatever non-lifecycle rows are added, wherever they land in
    time, the fold of the lifecycle history is the same."""
    rng = random.Random(1363)
    history = [_row(_ITEM, "resolved", "2026-10-01T00:00:00Z"),
               _row(_ITEM, "reopened", "2026-10-02T00:00:00Z"),
               _row("o-other", "closed", "2026-10-02T00:00:00Z"),
               _row(_ITEM, "superseded-by:o-other", "2026-10-03T00:00:00Z")]
    expected = store.fold_resolutions(history)
    kinds = ["verification", "note", "decision", "measurement", "handoff",
             "corroboration", "future-kind", "x", ""]
    statuses = ["done", "reopened", "resolved", "active", "weird", ""]
    refs = [_ITEM, "o-other", "o-third", ""]
    for _ in range(200):
        noise = [_row(rng.choice(refs), rng.choice(statuses),
                      f"2026-10-0{rng.randint(1, 9)}T0{rng.randint(0, 9)}:00:00Z",
                      kind=rng.choice(kinds))
                 for _ in range(rng.randint(1, 8))]
        rows = history + noise
        rng.shuffle(rows)
        assert store.fold_resolutions(rows) == expected


def test_briefing_still_shows_the_item_after_a_non_lifecycle_row(
        tmp_checkpoint_dir):
    store.append_event(_ITEM, "quote-verification-failed", kind="verification",
                       project_dir=_P, source="serializer",
                       writer=Writer.EMITTER)
    snap = view.snapshot(_P)
    assert _ITEM not in snap.resolved_refs
    assert view.live({"id": _ITEM, "text": "ship the thing"}, snap)


def test_recall_does_not_mark_the_item_resolved_after_a_non_lifecycle_row(
        tmp_checkpoint_dir):
    stamp = "2026-06-20T00:00:00Z"
    store.write_checkpoint(
        "S-one",
        {"session_id": "S-one", "created": stamp,
         "working_context": {
             "active_topic": {"text": "topic", "trust": "inferred"},
             "open_questions": [{"text": "should the zephyr cache stay pinned",
                                 "trust": "inferred", "importance": 7,
                                 "first_seen": stamp, "id": _ITEM}],
             "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}},
        project_dir=_P, writer=Writer.HUMAN)
    store.append_event(_ITEM, "quote-verification-failed", kind="verification",
                       project_dir=_P, source="serializer",
                       writer=Writer.EMITTER)
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = conn.execute("SELECT superseded_by FROM items WHERE item_id=?",
                            (_ITEM,)).fetchall()
    finally:
        conn.close()
    assert rows, "the item must be indexed"
    assert all(r[0] is None for r in rows)


def test_a_non_lifecycle_row_does_not_demote_a_corroboration():
    rows = [_row(store.corroboration_ref(_ITEM), "corroborated-by:S-2",
                 "2026-10-01T00:00:00Z", kind="corroboration"),
            _row(_ITEM, "quote-verification-failed", "2026-10-02T00:00:00Z",
                 kind="verification")]
    got = store.fold_corroborations(rows)[_ITEM]
    assert got["origins"] == {"S-2"}
    assert got["latest_demotion_ts"] is None
