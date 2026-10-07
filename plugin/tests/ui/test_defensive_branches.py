"""Defensive branches the happy-path suites never reach: torn files, hostile
params, and engine failure. Each test names the guarantee — a broken input
degrades to an error shape or a skip, never a traceback."""
import json

from daimon_briefing import api, config, view
from daimon_ui import reader
from tests.ui.scope import scoped


# ---- receipt sidecar edge shapes ----

def _root_with_receipt(tmp_path, sidecar_body):
    d = tmp_path / "checkpoints"
    d.mkdir()
    (d / "S1.json").write_text(json.dumps({"session_id": "S1", "receipts": True}))
    if sidecar_body is not None:
        (d / "S1.receipt").write_text(sidecar_body)
    return d


def _state(d):
    with config.checkpoint_dir_override(d):
        return api.receipt_state({"receipts": True, "session_id": "S1"})


def test_receipt_with_non_string_hash_reads_missing(tmp_path):
    d = _root_with_receipt(tmp_path, json.dumps({"receipt": {"outputs_hash": 7}}))
    assert _state(d)["state"] == "missing"


def test_receipt_root_unreadable_reads_missing(tmp_path):
    d = _root_with_receipt(
        tmp_path, json.dumps({"receipt": {"outputs_hash": "uABC"}}))
    (d / "S1.json").unlink()
    (d / "S1.json").mkdir()  # a directory: read_bytes raises OSError
    assert _state(d)["state"] == "missing"


def test_a_session_id_that_is_not_text_reads_missing(tmp_path):
    with config.checkpoint_dir_override(tmp_path):
        got = api.receipt_state({"receipts": True, "session_id": 7})
    assert got == {"state": "missing", "detail": None}


# ---- normalization and event-log edge shapes ----

def test_norm_item_rejects_non_dict_non_string():
    assert reader._norm_item(42) is None


# ---- session-file loss between scan and read ----

def test_biography_skips_a_session_that_vanishes_before_it_is_opened(
        tmp_path, monkeypatch):
    d = tmp_path / "checkpoints"
    (d / "-p").mkdir(parents=True)
    item = {"id": "o-a1b2c3d4e5f6", "text": "t", "trust": "inferred"}
    for sid, created in (("OLD", "2026-08-01T00:00:00Z"),
                         ("NEW", "2026-08-02T00:00:00Z")):
        (d / f"{sid}.json").write_text(json.dumps({
            "session_id": sid, "created": created, "project_slug": "-p",
            "working_context": {"open_questions": [item],
                                "recent_decisions": []},
            "epistemic_snapshot": {}}))
    real = view.open_sessions

    def without_old(project, sids, *, live):
        got = real(project, sids, live=live)
        got.pop("OLD")
        return got

    monkeypatch.setattr(view, "open_sessions", without_old)
    got = scoped(d).item_biography("-p", "o-a1b2c3d4e5f6")
    assert got["ok"] is True
    assert got["item"]["text"] == "t"
    assert [c["session_id"] for c in got["trust_anatomy"]["chain"]] == ["NEW"]

