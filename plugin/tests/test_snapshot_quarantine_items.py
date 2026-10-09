"""`Snapshot.quarantine_items` (#1132 PR 11a): the active quarantines whose
record names an item id, so a lookup can say `quarantine` for an id whose
checkpoint copy is no longer in the retained window. Built from the trust
ledger the snapshot already reads; records are made by the real writer."""

from daimon_briefing import normalize, store, trust, view

PROJECT = "/p/qitems"
SLUG = store.project_slug(PROJECT)
TEXT = "the migration owner was never actually decided anywhere"
ITEM_ID = "o-0123456789ab"


def _propose(text=TEXT, kind="question", item_id=ITEM_ID):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         item_id=item_id, project_dir=PROJECT)


def test_the_empty_snapshot_has_no_quarantine_items():
    assert dict(view.Snapshot.empty().quarantine_items) == {}


def test_an_active_record_with_an_item_id_is_indexed(tmp_checkpoint_dir):
    qid = _propose()
    key = normalize.content_key(TEXT)
    for snap in (view.snapshot(PROJECT), view.judge(SLUG).snap):
        assert dict(snap.quarantine_items) == {ITEM_ID: ("question", qid, key)}


def test_a_record_without_an_item_id_is_not_indexed(tmp_checkpoint_dir):
    _propose(item_id="")
    snap = view.snapshot(PROJECT)
    assert dict(snap.quarantine_items) == {}
    assert snap.quarantined          # the value is still quarantined by key


def test_a_released_record_leaves_the_index(tmp_checkpoint_dir):
    qid = _propose()
    assert view.judge(SLUG).snap.quarantine_items
    trust.release(qid, channel="cli-tty", project_dir=PROJECT)
    assert dict(view.snapshot(PROJECT).quarantine_items) == {}
    assert dict(view.judge(SLUG).snap.quarantine_items) == {}


def test_an_unratified_candidate_is_not_indexed(tmp_checkpoint_dir):
    trust.propose(text=TEXT, kind="question", reason="r", evidence=["issue:1"],
                  channel="cli-agent", item_id=ITEM_ID, project_dir=PROJECT)
    assert dict(view.snapshot(PROJECT).quarantine_items) == {}


def test_the_judge_memo_key_is_unchanged_and_still_sees_a_new_record(
        tmp_checkpoint_dir):
    first = view.judge(SLUG)
    assert view.judge(SLUG) is first                   # memoized on stats
    _propose()
    second = view.judge(SLUG)
    assert second is not first
    assert ITEM_ID in second.snap.quarantine_items


def test_a_closed_snapshot_keeps_the_field_empty_and_closed(
        tmp_checkpoint_dir):
    from daimon_briefing import config
    _propose()
    (config.checkpoint_dir() / SLUG / "trust.jsonl").write_bytes(
        b"this is not json\n")
    snap = view.judge(SLUG).snap
    assert snap.closed and dict(snap.quarantine_items) == {}
