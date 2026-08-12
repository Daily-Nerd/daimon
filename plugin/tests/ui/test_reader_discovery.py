import json
from pathlib import Path
import pytest
from daimon_ui import reader

def test_resolve_data_dir_env_override(tmp_path):
    assert reader.resolve_data_dir({"DAIMON_CHECKPOINT_DIR": str(tmp_path)}) == tmp_path

def test_resolve_data_dir_default():
    assert reader.resolve_data_dir({}) == Path.home() / ".daimon" / "checkpoints"

def test_project_slug_munging():
    assert reader.project_slug("/Users/x/My Proj") == "-Users-x-My-Proj"

def test_list_recent_orders_and_filters(bucket):
    got = reader.list_recent(bucket)
    assert [g["ref"] for g in got] == ["latest", "prev-1", "prev-2"]
    assert got[0]["active_topic"] == "Scoping the inspector"
    assert got[0]["created"] == "2026-08-06T08:05:12Z"

def test_list_recent_empty_bucket(tmp_path):
    assert reader.list_recent(tmp_path / "nope") == []

def test_list_recent_torn_pointer(bucket):
    (bucket / "prev-1.json").write_text("{not json")
    got = reader.list_recent(bucket)
    refs = [g["ref"] for g in got]
    assert "prev-1" in refs                       # named, not hidden
    assert got[refs.index("prev-1")]["created"] is None


@pytest.fixture
def multi_buckets(tmp_path):
    root = tmp_path / "checkpoints"
    root.mkdir()

    a = root / "-proj-a"
    a.mkdir()
    (a / "latest.json").write_text(json.dumps({
        "created": "2026-08-06T10:00:00Z",
        "working_context": {
            "active_topic": {"text": "Proj A topic"},
            "open_questions": [{"text": "q1"}, {"text": "q2"}],
            "recent_decisions": [{"text": "d1"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [], "contradictions_flagged": []},
    }))

    b = root / "-proj-b"
    b.mkdir()
    (b / "latest.json").write_text(json.dumps({
        "created": "2026-08-05T10:00:00Z",
        "working_context": {"active_topic": {"text": "Proj B topic"}, "open_questions": [], "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [], "contradictions_flagged": []},
    }))

    torn = root / "-proj-torn"
    torn.mkdir()
    (torn / "latest.json").write_text("{not json")

    missing = root / "-proj-missing"      # no latest.json — must be skipped
    missing.mkdir()
    (missing / "prev-1.json").write_text("{}")

    (root / ".chunk-cache").mkdir()       # no latest.json — must be skipped

    return root


def test_list_buckets_sorts_by_created_desc_none_last(multi_buckets):
    got = reader.list_buckets(multi_buckets)
    assert [b["slug"] for b in got] == ["-proj-a", "-proj-b", "-proj-torn"]


def test_list_buckets_skips_dirs_without_latest_json(multi_buckets):
    got = reader.list_buckets(multi_buckets)
    slugs = {b["slug"] for b in got}
    assert "-proj-missing" not in slugs
    assert ".chunk-cache" not in slugs


def test_list_buckets_torn_listed_with_none_fields(multi_buckets):
    got = reader.list_buckets(multi_buckets)
    torn = next(b for b in got if b["slug"] == "-proj-torn")
    assert torn["created"] is None
    assert torn["active_topic"] is None
    assert torn["item_count"] is None


def test_list_buckets_item_count_correctness(multi_buckets):
    got = reader.list_buckets(multi_buckets)
    a = next(b for b in got if b["slug"] == "-proj-a")
    b = next(b for b in got if b["slug"] == "-proj-b")
    assert a["item_count"] == 3
    assert b["item_count"] == 0


def test_list_buckets_empty_data_dir(tmp_path):
    assert reader.list_buckets(tmp_path / "nope") == []
