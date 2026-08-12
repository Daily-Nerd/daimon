"""Defensive branches the happy-path suites never reach: torn files, hostile
params, and engine failure. Each test names the guarantee — a broken input
degrades to an error shape or a skip, never a traceback."""
import json

from daimon_ui import reader


# ---- receipt sidecar edge shapes ----

def _root_with_receipt(tmp_path, sidecar_body):
    d = tmp_path / "checkpoints"
    d.mkdir()
    (d / "S1.json").write_text(json.dumps({"session_id": "S1", "receipts": True}))
    if sidecar_body is not None:
        (d / "S1.receipt").write_text(sidecar_body)
    return d


def test_receipt_with_non_string_hash_reads_missing(tmp_path):
    d = _root_with_receipt(tmp_path, json.dumps({"receipt": {"outputs_hash": 7}}))
    got = reader.receipt_state(d, {"receipts": True, "session_id": "S1"})
    assert got["state"] == "missing"


def test_receipt_root_unreadable_reads_missing(tmp_path):
    d = _root_with_receipt(
        tmp_path, json.dumps({"receipt": {"outputs_hash": "uABC"}}))
    (d / "S1.json").unlink()
    (d / "S1.json").mkdir()  # a directory: read_bytes raises OSError
    got = reader.receipt_state(d, {"receipts": True, "session_id": "S1"})
    assert got["state"] == "missing"


def test_receipts_enabled_skips_torn_pointer(tmp_path):
    b = tmp_path / "checkpoints" / "-proj"
    b.mkdir(parents=True)
    (b / "latest.json").write_text("{torn")
    assert reader.receipts_enabled(b) is False


# ---- normalization and event-log edge shapes ----

def test_norm_item_rejects_non_dict_non_string():
    assert reader._norm_item(42) is None


def test_resolutions_skips_torn_lines_and_refless_events(tmp_path):
    b = tmp_path / "b"
    b.mkdir()
    (b / "events.jsonl").write_text("\n".join([
        "",  # blank line: the fold must step over it
        "{torn",
        json.dumps({"kind": "resolution", "status": "resolved"}),  # no item_ref
        json.dumps({"kind": "resolution", "item_ref": "o-aaa111aaa111",
                     "status": "resolved", "ts": "t", "note": "n"}),
    ]) + "\n")
    got = reader.resolutions(b)
    assert list(got) == ["o-aaa111aaa111"]


def test_jsonl_rows_skips_torn_lines(tmp_path):
    p = tmp_path / "verification.jsonl"
    p.write_text('\n{broken\n' + json.dumps({"item_ref": "o-a1b2c3d4e5f6"}) + "\n")
    rows = reader._jsonl_rows(p)
    assert rows == [{"item_ref": "o-a1b2c3d4e5f6"}]


# ---- session-file loss between scan and read ----

def test_load_session_missing_and_torn(tmp_path):
    data, err = reader._load_session(tmp_path, "gone")
    assert data is None and err["what"].startswith("Session")
    (tmp_path / "torn.json").write_text("{nope")
    data, err = reader._load_session(tmp_path, "torn")
    assert data is None and "Couldn't read" in err["what"]


def test_diff_surfaces_load_error_for_either_side(tmp_path, monkeypatch):
    """A session can vanish between the history scan and the read (GC race).
    Both sides must surface the load error rather than diffing nothing."""
    valid = [{"session_id": "A", "created": "1"}, {"session_id": "B", "created": "2"}]
    monkeypatch.setattr(reader, "project_history",
                        lambda *a: {"sessions": valid, "unreadable": 0})
    err = {"what": "x", "why": "y", "fix": "z"}
    real_load = reader._load_session

    def only_b(data_dir, sid):
        if sid == "A":
            return None, err
        return {"session_id": sid}, None

    monkeypatch.setattr(reader, "_load_session", only_b)
    got = reader.diff_checkpoints(tmp_path, "-p", "A", "B")
    assert got == {"ok": False, "error": err}

    def only_a(data_dir, sid):
        if sid == "B":
            return None, err
        return {"session_id": sid}, None

    monkeypatch.setattr(reader, "_load_session", only_a)
    got = reader.diff_checkpoints(tmp_path, "-p", "A", "B")
    assert got == {"ok": False, "error": err}
    monkeypatch.setattr(reader, "_load_session", real_load)


def test_biography_skips_a_torn_session_and_still_answers(tmp_path, monkeypatch):
    item = {"id": "o-a1b2c3d4e5f6", "text": "t", "trust": "inferred"}
    sessions = [{"session_id": "OLD", "created": "1"},
                {"session_id": "NEW", "created": "2"}]
    monkeypatch.setattr(reader, "project_history",
                        lambda *a: {"sessions": list(reversed(sessions)), "unreadable": 0})

    def load(data_dir, sid):
        if sid == "OLD":
            return None, {"what": "torn", "why": "", "fix": ""}
        return {"session_id": sid,
                "working_context": {"open_questions": [item],
                                     "recent_decisions": []},
                "epistemic_snapshot": {}}, None

    monkeypatch.setattr(reader, "_load_session", load)
    got = reader.item_biography(tmp_path, "-p", "o-a1b2c3d4e5f6")
    assert got["ok"] is True
    assert got["item"]["text"] == "t"
