from daimon_ui import reader

def test_history_filters_and_sorts(flat_history):
    d, slug = flat_history
    got = reader.project_history(d, slug)
    assert [s["session_id"] for s in got["sessions"]] == ["rollout-2026-08-06-cccc", "bbbb-2222", "aaaa-1111"]
    assert got["sessions"][0]["active_topic"] == "day three"
    assert got["unreadable"] == 1

def test_history_empty_for_unknown_slug(flat_history):
    d, _ = flat_history
    assert reader.project_history(d, "-nope")["sessions"] == []

def test_history_counts_non_utf8_file_as_unreadable(flat_history):
    d, slug = flat_history
    (d / "bin.json").write_bytes(b"\xff\xfe{")
    got = reader.project_history(d, slug)
    assert got["unreadable"] == 2  # pre-existing torn.json + this non-UTF-8 file
