"""The edges of the write-posture code: each branch a caller can reach that
the larger suites do not (#1132 PR 10b)."""

import pytest

from daimon_briefing import config, jsonl, ledger, store, surfaces, trust
from daimon_briefing.cli import lifecycle
from daimon_briefing.jsonl import Health
from daimon_briefing.ledger_repair import Unreached
from daimon_briefing.surfaces import Writer


# ---- forget's unreached lines ------------------------------------------------

def test_a_sidecar_with_unreadable_lines_asks_for_a_manual_review():
    line = lifecycle._unreached_line(
        Unreached("trust.quarantined-lines", Health.DEGRADED, 2))
    assert line == ("trust.quarantined-lines holds 2 line(s) forget cannot "
                    "read (degraded); review that file by hand")
    assert lifecycle._unreached_line(
        Unreached("trust.quarantined-lines", Health.UNREADABLE, 0)) == (
        "trust.quarantined-lines holds a line(s) forget cannot read "
        "(unreadable); review that file by hand")


def test_a_ledger_that_could_not_be_rewritten_says_the_value_may_remain():
    for state in (Health.OK, Health.DEGRADED):
        assert lifecycle._unreached_line(
            Unreached("amendments.jsonl", state, 0)) == (
            "amendments.jsonl could not be rewritten; the value may still "
            "be in it")


# ---- jsonl --------------------------------------------------------------------

def _garbage(tmp_path):
    path = tmp_path / "trust.jsonl"
    path.write_bytes(b"<<<<<<< conflict\n")
    return path


def test_require_writable_raises_the_callers_typed_error_in_library_mode(
        tmp_path):
    path = _garbage(tmp_path)
    with pytest.raises(ValueError, match="trust.jsonl is unreadable"):
        jsonl.require_writable(path, Writer.HUMAN, error=ValueError)


def test_require_writable_raises_refused_when_the_run_surfaces_refusals(
        tmp_path):
    path = _garbage(tmp_path)
    with jsonl.surface_refusals():
        with pytest.raises(jsonl.Refused):
            jsonl.require_writable(path, Writer.HUMAN, error=ValueError)
    with pytest.raises(jsonl.Refused):
        jsonl.require_writable(path, Writer.HUMAN)


def test_require_writable_passes_a_proven_ledger(tmp_path):
    (tmp_path / "trust.jsonl").write_bytes(b'{"a": 1}\n')
    jsonl.require_writable(tmp_path / "trust.jsonl", Writer.HUMAN)
    (tmp_path / "gone").mkdir()
    jsonl.require_writable(tmp_path / "gone" / "trust.jsonl", Writer.HUMAN)


def test_a_skipped_row_leaves_no_usage_line_under_the_kill_switch(
        tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    jsonl._note_skip("events.jsonl", "unreadable")
    assert not (config.log_dir() / "usage.log").exists()


def test_a_skipped_row_survives_an_unwritable_usage_log(tmp_path, monkeypatch):
    blocked = tmp_path / "logs-is-a-file"
    blocked.write_text("x")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(blocked))
    jsonl._note_skip("events.jsonl", "unreadable")  # must not raise


# ---- ledger -------------------------------------------------------------------

def test_a_project_that_names_no_bucket_has_no_slug():
    for project in (None, "", "?", "  "):
        assert ledger._slug_of(project) == ""


def test_admission_waiting_is_zero_for_no_slug_and_for_no_refusals(
        tmp_checkpoint_dir):
    assert ledger.admission_waiting("") == 0
    assert ledger.admission_waiting("some-slug", text="no refusals here\n") == 0


# ---- store --------------------------------------------------------------------

def test_republish_is_a_no_op_without_a_team(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    out = store.republish_tombstones("/p/no-team")
    assert out == [] and out.failed == ()


def test_admission_state_of_a_project_with_no_ledger_path_is_none(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "_events_path", lambda project_dir: None)
    assert store.admission_state("/p/x") is None


# ---- surfaces -----------------------------------------------------------------

def test_an_undeclared_ledger_name_has_no_write_column():
    with pytest.raises(LookupError):
        surfaces.write_row("nothing.jsonl")


# ---- trust --------------------------------------------------------------------

def test_a_redacted_quarantine_no_longer_reads_as_unredacted(tmp_checkpoint_dir):
    key = "a" * 64
    marker = store._FORGOTTEN_FIELD_MARKER.format(key)
    assert trust._unredacted({"value_key": key, "reason": "still here"}, key)
    assert not trust._unredacted({"value_key": key, "reason": marker,
                                  "evidence": [marker]}, key)
    assert not trust._unredacted({"value_key": "b" * 64, "reason": "other"},
                                 key)
