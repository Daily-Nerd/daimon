"""Reading a file that is not a session transcript ends in a final skip (#1362)."""
import json
import uuid
from pathlib import Path

import pytest

from daimon_briefing import cli, ledger, serializer, transcript
from tests.conftest import FIXTURES

PROJECT = "/p/not-a-transcript"
TOOL_ROWS = [{"ts": "2026-08-16T02:23:06Z", "session": "s", "file": "/m.md"}] * 3
UUID_NAME = f"{uuid.uuid4()}.jsonl"


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


# ---- the decision at the serializer door -------------------------------------


@pytest.fixture
def claude_root(tmp_path, monkeypatch):
    root = tmp_path / "claude" / "projects"
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(root))
    return root


def _boom_chat(*_a, **_k):
    raise AssertionError("the model must not be called")


def _log(tmp_path):
    p = tmp_path / ".daimon" / "logs" / "serialize.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_serializing_a_tool_log_is_a_final_skip_not_a_failure(
        tmp_path, claude_root, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    odd = _write(claude_root / "-p-slug" / "memory-index-reads.jsonl",
                 TOOL_ROWS)
    assert cli._run_serialize(odd, PROJECT) == 0
    logged = _log(tmp_path)
    assert "skipped serialize for memory-index-reads: not-a-transcript" in logged
    assert "error:" not in logged and "too short" not in logged


def test_a_drifted_real_transcript_is_still_a_failure(
        tmp_path, claude_root, monkeypatch):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    real = _write(claude_root / "-p-slug" / UUID_NAME, [{"foo": 1}] * 3)
    assert cli._run_serialize(real, PROJECT) == 1
    assert "error: unreadable-transcript" in _log(tmp_path)


def test_the_unrecognized_rows_fixture_outside_a_host_root_stays_a_failure(
        tmp_path, claude_root, monkeypatch):
    monkeypatch.setattr(cli, "_chat", _boom_chat)
    assert cli._run_serialize(FIXTURES / "generic_unrecognized_rows.jsonl",
                              PROJECT) == 1


def test_the_gate_raises_not_a_transcript_only_when_told_so():
    account = transcript.RowAccount(3, 0, {}, {"no-role": 3})
    with pytest.raises(serializer.NotATranscript):
        serializer.require_readable([], account, foreign=True)
    with pytest.raises(serializer.UnreadableTranscript):
        serializer.require_readable([], account)
    assert issubclass(serializer.NotATranscript, serializer.TooShortError)


def test_a_not_a_transcript_skip_is_never_outstanding_and_clears_an_old_error(
        tmp_path):
    stamp = "2026-11-20T00:00:00Z"
    # The file exists: an old error whose transcript is gone is lost, and the
    # test is about an error that is still outstanding.
    transcript = tmp_path / "memory-index-reads.jsonl"
    transcript.write_text("{}\n")
    path = str(transcript)
    old = [
        f"{stamp} session-start: spawned serialize for memory-index-reads "
        f"(reason: catch-up-orphan, project: /p) (transcript: {path})",
        f"error: unreadable-transcript: 57 rows, none read as conversation "
        f"(dropped no-role=57) (transcript: {path}) after 0s",
    ]
    new = [
        f"{stamp} session-start: retry serialize for memory-index-reads "
        f"(reason: heal-retry, project: /p) (transcript: {path})",
        "skipped serialize for memory-index-reads: not-a-transcript: no row "
        "carries a host marker",
    ]
    now = 1_800_000_000.0
    assert ledger._compute_outstanding("\n".join(old) + "\n", now)
    assert ledger._compute_outstanding("\n".join(old + new) + "\n", now) == []
    assert ledger._compute_outstanding("\n".join(new) + "\n", now) == []


