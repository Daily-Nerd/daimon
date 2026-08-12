import json
from daimon_ui import reader
from tests.ui.conftest import make_checkpoint


def _write_sessions(d, slug):
    for sid, created, topic in [
        ("aaaa-1111", "2026-08-04T10:00:00Z", "day one"),
        ("bbbb-2222", "2026-08-05T10:00:00Z", "day two"),
    ]:
        cp = make_checkpoint(created=created, topic=topic, session_id=sid)
        cp["project_slug"] = slug
        (d / f"{sid}.json").write_text(json.dumps(cp))


def test_activity_merges_and_sorts_newest_first(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    _write_sessions(d, slug)
    (d / slug / "events.jsonl").write_text("\n".join([
        json.dumps({"ts": "2026-08-05T12:00:00Z", "kind": "resolution",
                    "item_ref": "o-abc123abc123", "status": "resolved",
                    "source": "cli", "note": "closed it", "item_text": "the question"}),
        json.dumps({"ts": "2026-08-06T09:00:00Z", "kind": "handoff",
                    "item_ref": "", "status": "open", "source": "cli",
                    "note": "Do X next. Beware Y."}),
    ]) + "\n")
    (d / slug / "verification.jsonl").write_text(json.dumps(
        {"ts": "2026-08-04T11:00:00Z", "check": "quote",
         "item_ref": "o-abc123abc123", "reason": "quote-not-in-transcript"}) + "\n")

    got = reader.project_activity(d, slug)
    assert got["ok"] is True
    assert [r["kind"] for r in got["rows"]] == [
        "handoff", "resolution", "session", "quote_check", "session"]
    assert got["rows"][0]["detail"] == "Do X next. Beware Y."
    assert got["rows"][0]["item_ref"] is None          # "" normalizes to None
    assert got["rows"][1]["extra"]["status"] == "resolved"
    assert got["rows"][3]["detail"] == "quote: quote-not-in-transcript"


def test_activity_session_rows_carry_topic(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    _write_sessions(d, slug)
    got = reader.project_activity(d, slug)
    sess = [r for r in got["rows"] if r["kind"] == "session"]
    assert [s["session_id"] for s in sess] == ["bbbb-2222", "aaaa-1111"]
    assert sess[0]["detail"] == "day two"
    assert sess[0]["extra"] == {"topic": "day two"}


def test_activity_resolution_detail_falls_back_to_item_text(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / slug / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-08-05T12:00:00Z", "kind": "resolution",
         "item_ref": "r-3031f5303030", "status": "resolved", "source": "cli",
         "item_text": "the described item"}) + "\n")
    got = reader.project_activity(d, slug)
    assert got["rows"][0]["detail"] == "the described item"


def test_activity_corroboration_detail_falls_back_to_status(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / slug / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-08-07T01:41:07Z", "kind": "corroboration",
         "item_ref": "corroboration:o-e00b696fa67a",
         "status": "corroborated-by:fddace65", "source": "serializer"}) + "\n")
    got = reader.project_activity(d, slug)
    row = got["rows"][0]
    assert row["detail"] == "corroborated-by:fddace65"
    assert row["item_ref"] == "corroboration:o-e00b696fa67a"   # kept verbatim


def test_activity_unknown_kind_passes_through(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / slug / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-08-07T02:00:00Z", "kind": "wormhole",
         "item_ref": "o-abc123abc123", "status": "weird", "source": "cli",
         "note": "novel event"}) + "\n")
    got = reader.project_activity(d, slug)
    assert got["rows"][0]["kind"] == "wormhole"
    assert got["rows"][0]["detail"] == "novel event"


def test_activity_torn_lines_skipped_tsless_rows_last(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / slug / "events.jsonl").write_text("\n".join([
        "{broken",
        json.dumps({"kind": "note", "item_ref": "", "status": "open",
                    "source": "cli", "note": "no timestamp"}),
        json.dumps({"ts": "2026-08-07T03:00:00Z", "kind": "note", "item_ref": "",
                    "status": "open", "source": "cli", "note": "stamped"}),
    ]) + "\n")
    got = reader.project_activity(d, slug)
    assert [r["detail"] for r in got["rows"]] == ["stamped", "no timestamp"]
    assert got["rows"][1]["ts"] is None


def test_activity_unreadable_session_lands_in_partial(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    _write_sessions(d, slug)
    (d / "torn.json").write_text("{nope")
    got = reader.project_activity(d, slug)
    assert got["partial"] and "1" in got["partial"][0]


def test_activity_empty_bucket_is_honestly_empty(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    got = reader.project_activity(d, slug)
    assert got["ok"] is True
    assert got["rows"] == []
