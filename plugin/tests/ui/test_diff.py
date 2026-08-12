import json
from daimon_ui import reader
from tests.ui.conftest import make_checkpoint

def test_resolutions_fold_skips_broken_line_and_non_resolution_kind(flat_with_events):
    d, slug = flat_with_events
    got = reader.resolutions(d / slug)
    assert got == {"o-aaa111aaa111": {"ts": "2026-08-06T09:00:00Z", "note": "closed it"}}

def test_resolutions_last_event_wins(tmp_path):
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    (bucket / "events.jsonl").write_text('\n'.join([
        json.dumps({"ts": "1", "kind": "resolution", "item_ref": "o-xxx", "status": "resolved", "note": "first"}),
        json.dumps({"ts": "2", "kind": "resolution", "item_ref": "o-xxx", "status": "active"}),
        json.dumps({"ts": "3", "kind": "resolution", "item_ref": "o-yyy", "status": "active"}),
        json.dumps({"ts": "4", "kind": "resolution", "item_ref": "o-yyy", "status": "resolved", "note": "second"}),
    ]) + '\n')
    got = reader.resolutions(bucket)
    assert got == {"o-yyy": {"ts": "4", "note": "second"}}

def test_resolutions_missing_events_file_is_empty(flat_history):
    d, slug = flat_history
    assert reader.resolutions(d / slug) == {}

def test_resolutions_all_torn_lines_is_empty(tmp_path):
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    (bucket / "events.jsonl").write_text("{nope\n{also nope\n")
    assert reader.resolutions(bucket) == {}


def _write_diff_sessions(d, slug, sid_a, sid_b):
    cp_a = make_checkpoint(session_id=sid_a, open_questions=[
        {"text": "resolved question", "id": "o-aaa111aaa111", "trust": "verbatim"},
        {"text": "gone question", "id": "o-bbb222bbb222", "trust": "verbatim"},
        {"text": "trust change item", "id": "o-ccc333ccc333", "trust": "verbatim"},
        {"text": "carried item", "id": "o-ddd444ddd444", "trust": "inferred"},
        {"text": "no id item old"},
    ])
    cp_a["project_slug"] = slug
    (d / f"{sid_a}.json").write_text(json.dumps(cp_a))

    cp_b = make_checkpoint(session_id=sid_b, open_questions=[
        {"text": "trust change item", "id": "o-ccc333ccc333", "trust": "inferred"},
        {"text": "carried item", "id": "o-ddd444ddd444", "trust": "inferred"},
        {"text": "new born item", "id": "o-eee555eee555", "trust": "verbatim"},
        {"text": "no id item new"},
    ])
    cp_b["project_slug"] = slug
    (d / f"{sid_b}.json").write_text(json.dumps(cp_b))


def test_diff_checkpoints_categorizes_born_resolved_gone_carried_trust_changed(flat_with_events):
    d, slug = flat_with_events
    sid_a, sid_b = "diff-a-0001", "diff-b-0002"
    _write_diff_sessions(d, slug, sid_a, sid_b)

    got = reader.diff_checkpoints(d, slug, sid_a, sid_b)
    assert got["ok"] is True
    assert got["a"]["session_id"] == sid_a
    assert got["b"]["session_id"] == sid_b

    born_ids = [i["id"] for i in got["born"]]
    assert born_ids == ["o-eee555eee555"]
    assert got["born"][0]["section"] == "open_loops"

    assert len(got["resolved"]) == 1
    resolved = got["resolved"][0]
    assert resolved["item"]["id"] == "o-aaa111aaa111"
    assert resolved["note"] == "closed it"
    assert resolved["ts"] == "2026-08-06T09:00:00Z"

    gone_ids = [i["id"] for i in got["gone"]]
    assert gone_ids == ["o-bbb222bbb222"]

    assert len(got["carried"]) == 1
    carried = got["carried"][0]
    assert carried["item"]["id"] == "o-ddd444ddd444"
    assert "sessions_present" not in carried

    assert len(got["trust_changed"]) == 1
    tc = got["trust_changed"][0]
    assert tc["item"]["id"] == "o-ccc333ccc333"
    assert tc["from"] == "verbatim"
    assert tc["to"] == "inferred"

    assert any("without an id" in p for p in got["partial"])


def test_diff_checkpoints_bad_sid_a_is_error(flat_with_events):
    d, slug = flat_with_events
    sid_b = "diff-b-0002"
    _write_diff_sessions(d, slug, "diff-a-0001", sid_b)
    got = reader.diff_checkpoints(d, slug, "not-a-real-session", sid_b)
    assert got["ok"] is False
    assert set(got["error"]) == {"what", "why", "fix"}


def test_diff_checkpoints_bad_sid_b_rejects_path_traversal(flat_with_events):
    d, slug = flat_with_events
    sid_a = "diff-a-0001"
    _write_diff_sessions(d, slug, sid_a, "diff-b-0002")
    got = reader.diff_checkpoints(d, slug, sid_a, "../../../etc/passwd")
    assert got["ok"] is False
    assert set(got["error"]) == {"what", "why", "fix"}
