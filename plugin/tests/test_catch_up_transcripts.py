"""The catch-up sweep spawns session transcripts, never a tool's log (#1362)."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

HOOK_DIR = Path(__file__).parents[2] / "hook"
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))
import _daimon_hook_lib as lib  # noqa: E402

TOOL_ROW = json.dumps({"ts": "t", "session": "s", "file": "/m.md"}) + "\n"
USER_ROW = json.dumps({"type": "user", "message": {"role": "user",
                                                   "content": "hi"}}) + "\n"


def _age(path, seconds):
    past = time.time() - seconds
    os.utime(path, (past, past))


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "logs"))
    folder = tmp_path / "proj"
    folder.mkdir()
    current = folder / f"{uuid.uuid4()}.jsonl"
    current.write_text(USER_ROW)
    calls = []
    monkeypatch.setattr(lib, "spawn_serialize",
                        lambda cli, path, env: calls.append(Path(path).name))
    return folder, current, calls


def _sweep(current, host="claude-code"):
    lib.sweep_orphans("daimon", "/p/A", current.stem, str(current), host=host)


def test_a_tool_log_beside_a_real_orphan_is_never_spawned_the_orphan_is(
        tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    real = folder / f"{uuid.uuid4()}.jsonl"
    real.write_text(USER_ROW)
    log = folder / "memory-index-reads.jsonl"
    log.write_text(TOOL_ROW * 5)
    _age(real, 7200)
    _age(log, 60)  # the tool log is the NEWEST candidate
    _sweep(current)
    assert calls == [real.name]


def test_only_a_tool_log_means_no_spawn(tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    log = folder / "memory-index-reads.jsonl"
    log.write_text(TOOL_ROW * 5)
    _age(log, 60)
    _sweep(current)
    assert calls == []


def test_an_oddly_named_file_whose_rows_carry_host_markers_is_still_swept(
        tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    odd = folder / "renamed-by-hand.jsonl"
    odd.write_text(TOOL_ROW + USER_ROW)
    _age(odd, 60)
    _sweep(current)
    assert calls == [odd.name]


def test_codex_names_and_markers_follow_the_same_definition(
        tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    real = folder / f"rollout-2026-11-01T10-20-30-{uuid.uuid4()}.jsonl"
    real.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n")
    log = folder / "tool.jsonl"
    log.write_text(TOOL_ROW)
    _age(real, 7200)
    _age(log, 60)
    _sweep(current, host="codex")
    assert calls == [real.name]


def test_without_a_host_the_sweep_behaves_as_before(tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    log = folder / "memory-index-reads.jsonl"
    log.write_text(TOOL_ROW)
    _age(log, 60)
    lib.sweep_orphans("daimon", "/p/A", current.stem, str(current))
    assert calls == [log.name]


def test_a_missing_shared_module_fails_open_to_todays_behaviour(
        tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    log = folder / "memory-index-reads.jsonl"
    log.write_text(TOOL_ROW)
    _age(log, 60)
    monkeypatch.setattr(lib, "_session_transcript", lambda: None)
    _sweep(current)
    assert calls == [log.name]


# ---- a failure is known by its line, however many lines follow ----------------


def test_a_tool_log_whose_error_is_far_back_in_the_log_is_still_not_spawned(
        tmp_path, monkeypatch):
    folder, current, calls = _setup(tmp_path, monkeypatch)
    log = folder / "memory-index-reads.jsonl"
    log.write_text(TOOL_ROW * 5)
    _age(log, 60)
    logs = tmp_path / "logs"
    logs.mkdir()
    filler = [f"note for S-other-{i}: filler" for i in range(500)]
    (logs / "serialize.log").write_text(
        f"error: unreadable-transcript: 5 rows (transcript: {log}) after 0s\n"
        + "\n".join(filler) + "\n", encoding="utf-8")
    assert log.stem in lib._failed_session_stems()  # still known
    _sweep(current)
    assert calls == []  # not a candidate either way
