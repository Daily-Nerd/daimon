"""Check strip reader (#670 slice 3): project_grid widens the shared walk
into object × checkpoint lanes. Sightings come from _walk_transitions with
the trust the item carried AT that session; quote checks carry no session
attribution on disk, so they are bucketed by ts into the checkpoint interval
they precede and the payload keeps their exact ts — the viewer must never
claim a session wrote them."""
import json

from daimon_ui import reader
from tests.ui.conftest import make_checkpoint


def _write_session(d, slug, sid, created, questions):
    cp = make_checkpoint(created=created, session_id=sid, open_questions=questions)
    cp["project_slug"] = slug
    (d / f"{sid}.json").write_text(json.dumps(cp))


def _grid_project(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-grid-proj"
    bucket = d / slug
    bucket.mkdir(parents=True)
    (bucket / "latest.json").write_text(json.dumps(make_checkpoint()))
    _write_session(d, slug, "grid-s1", "2026-08-01T10:00:00Z", [
        {"text": "the build gates on the bundle", "id": "o-aaa111aaa111"},
        {"text": "flags are cached at boot", "id": "o-bbb222bbb222", "trust": "inferred"},
    ])
    _write_session(d, slug, "grid-s2", "2026-08-02T10:00:00Z", [
        {"text": "the build gates on the bundle", "id": "o-aaa111aaa111",
         "trust": "verbatim", "quote": "gates on the bundle"},
        {"text": "flags are cached at boot", "id": "o-bbb222bbb222", "trust": "inferred"},
    ])
    _write_session(d, slug, "grid-s3", "2026-08-03T10:00:00Z", [
        {"text": "the build gates on the bundle", "id": "o-aaa111aaa111",
         "trust": "verbatim", "quote": "gates on the bundle"},
    ])
    return d, slug


def test_grid_columns_are_the_session_window_oldest_first(tmp_path):
    d, slug = _grid_project(tmp_path)
    got = reader.project_grid(d, slug)
    assert got["ok"] is True
    assert [c["session_id"] for c in got["columns"]] == ["grid-s1", "grid-s2", "grid-s3"]
    assert [c["is_head"] for c in got["columns"]] == [False, False, True]


def test_grid_cells_carry_kind_and_trust_at_that_session(tmp_path):
    d, slug = _grid_project(tmp_path)
    got = reader.project_grid(d, slug)
    rows = {r["id"]: r for r in got["rows"]}
    a = rows["o-aaa111aaa111"]["cells"]
    assert a["grid-s1"]["kind"] == "first_seen"
    assert a["grid-s1"]["trust"] is None            # untagged when first written
    assert a["grid-s2"]["kind"] == "changed"
    assert a["grid-s2"]["trust"] == "verbatim"      # the class it held THEN


def test_grid_marks_the_departure_at_the_transition_column(tmp_path):
    """A vanished object marks the session whose absence recorded it — the
    same attribution the ledger makes — and the lane is dashed after it."""
    d, slug = _grid_project(tmp_path)
    got = reader.project_grid(d, slug)
    b = {r["id"]: r for r in got["rows"]}["o-bbb222bbb222"]
    assert b["cells"]["grid-s3"]["kind"] == "last_seen"
    assert b["gone_after"] == "grid-s3"


def test_grid_buckets_quote_checks_by_ts_without_naming_a_session(tmp_path):
    d, slug = _grid_project(tmp_path)
    (d / slug / "verification.jsonl").write_text(json.dumps({
        "ts": "2026-08-02T23:00:00Z", "check": "quote", "reason": "compacted",
        "item_ref": "o-aaa111aaa111"}) + "\n")
    got = reader.project_grid(d, slug)
    a = {r["id"]: r for r in got["rows"]}["o-aaa111aaa111"]
    assert len(a["checks"]) == 1
    check = a["checks"][0]
    # bucketed into the interval that closed at grid-s3; the ts stays exact
    assert check["column"] == "grid-s3"
    assert check["ts"] == "2026-08-02T23:00:00Z"
    assert "session" not in check


def test_grid_windows_columns_and_reports_what_lies_beyond(tmp_path):
    d, slug = _grid_project(tmp_path)
    for i in range(4, 12):
        _write_session(d, slug, f"grid-s{i}", f"2026-08-{i:02d}T10:00:00Z", [
            {"text": "the build gates on the bundle", "id": "o-aaa111aaa111",
             "trust": "verbatim", "quote": "gates on the bundle"},
        ])
    got = reader.project_grid(d, slug)
    assert len(got["columns"]) == reader.GRID_COLUMNS
    assert got["older_columns"] == 11 - reader.GRID_COLUMNS
    assert any("older checkpoint" in p for p in got["partial"])


def test_grid_unknown_project_is_empty_not_an_error(tmp_path):
    d = tmp_path / "checkpoints"
    d.mkdir()
    got = reader.project_grid(d, "-nothing-here")
    assert got["ok"] is True
    assert got["columns"] == []
    assert got["rows"] == []
