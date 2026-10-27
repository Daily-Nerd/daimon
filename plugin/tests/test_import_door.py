"""#1236 slice 1: the import door and its ledger.

`daimon import` (slice 2) will call `cli._run_serialize(..., door="import")`
once per transcript. This file pins what that door does and does not do:

- the success line ends with ` [import]`, beside the ` [fallback backend]`
  marker when a rescue produced the checkpoint, and every reader of the
  success line still counts it;
- a door the code does not know raises before any model call.
"""

import json

import pytest

from daimon_briefing import capture, cli, ledger, llm
from tests.conftest import FIXTURES, make_messages

PROJECT = "/p/import-door"


def _valid_json(session_id):
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [{"text": "q", "trust": "inferred"}],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


@pytest.fixture
def log_dir(tmp_path):
    # conftest's autouse isolation already points DAIMON_LOG_DIR here.
    return tmp_path / ".daimon" / "logs"


def _log_text(log_dir):
    return (log_dir / "serialize.log").read_text(encoding="utf-8")


def _success_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("wrote checkpoint: ")]


# ---- the success line marker ------------------------------------------------


def test_import_success_line_ends_with_the_marker_and_still_counts(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, capsys, monkeypatch
):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-imp")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT, door="import")
    assert rc == 0
    printed = [
        ln
        for ln in capsys.readouterr().out.splitlines()
        if ln.startswith("wrote checkpoint: ")
    ]
    logged = _success_lines(_log_text(log_dir))
    assert printed == logged, "printed and logged lines must stay byte-identical"
    assert logged[0].endswith(" (took 0s) [import]") or logged[0].endswith(
        "s) [import]"
    )
    # Every reader of the success line still reads it as one success.
    led = cli._session_ledger(_log_text(log_dir), now=0.0)
    assert led["sample_transcript"]["result_kind"] == "success"
    parsed = ledger._parse_serialize_log(log_dir / "serialize.log", now=0.0)
    assert parsed["result"]["outcome"] == "success"
    stats = ledger._stats_capture()
    assert stats["success"] == 1
    assert stats["fallback_serializes"] == 0
    assert ledger._is_result_line(logged[0])


def test_import_marker_coexists_with_the_fallback_marker(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, monkeypatch
):
    def chat_with_fallback(*a, **k):
        llm._fallback_used = True  # simulate llm.chat falling back mid-serialize
        return fake_chat_factory(_valid_json("S-fb"))(*a, **k)

    monkeypatch.setattr(cli, "_chat", chat_with_fallback)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT, door="import")
    assert rc == 0
    line = _success_lines(_log_text(log_dir))[0]
    assert line.endswith(" [fallback backend] [import]")
    led = cli._session_ledger(_log_text(log_dir), now=0.0)
    assert led["sample_transcript"]["result_kind"] == "success"
    stats = ledger._stats_capture()
    assert stats["success"] == 1
    assert stats["fallback_serializes"] == 1


def test_live_doors_write_no_marker(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, monkeypatch
):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-live")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    assert cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT) == 0
    assert cli.main(["serialize", str(FIXTURES / "sample_transcript.md")]) == 0
    for line in _success_lines(_log_text(log_dir)):
        assert "[import]" not in line


# ---- the door is a closed set -----------------------------------------------


def test_capture_run_refuses_an_unknown_door(monkeypatch):
    calls = []
    monkeypatch.setattr(
        capture.serializer,
        "serialize_strict",
        lambda *a, **k: calls.append("model") or {},
    )
    with pytest.raises(ValueError, match="unknown capture door"):
        capture.run(
            "S-x",
            make_messages(20),
            project=PROJECT,
            chat=None,
            deadline=0.0,
            door="backfill",
        )
    assert calls == [], "an unknown door must refuse before any model call"
