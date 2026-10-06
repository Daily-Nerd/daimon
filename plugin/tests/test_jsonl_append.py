"""The one append, replace and lock substrate for ledger files (#1132 PR 3a).

Fixtures are plain bytes; reads go back through `jsonl.read`, the judge of
what a ledger file is, never through the appenders under test.
"""

import fcntl
import json
import os
from pathlib import Path

from daimon_briefing import jsonl
from daimon_briefing.jsonl import Health


def _row(**kw):
    return json.dumps(kw or {"a": 1}, ensure_ascii=False)


def test_append_lines_writes_each_line_terminated_and_returns_the_count(
        tmp_path):
    path = tmp_path / "l.jsonl"
    assert jsonl.append_lines(path, ['{"a": 1}', '{"a": 2}']) == 2
    assert path.read_bytes() == b'{"a": 1}\n{"a": 2}\n'


def test_append_lines_adds_to_the_end_of_an_existing_file(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    jsonl.append_lines(path, ['{"a": 2}'])
    assert path.read_bytes() == b'{"a": 1}\n{"a": 2}\n'


def test_append_lines_into_an_empty_file_writes_no_leading_newline(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b"")
    jsonl.append_lines(path, ['{"a": 1}'])
    assert path.read_bytes() == b'{"a": 1}\n'


def test_append_lines_of_nothing_writes_nothing_and_returns_zero(tmp_path):
    path = tmp_path / "l.jsonl"
    assert jsonl.append_lines(path, []) == 0
    assert not path.exists()


def test_a_torn_tail_becomes_its_own_line_and_the_new_row_is_intact(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"a": 1}\n{"cut": "hal')
    jsonl.append_lines(path, ['{"a": 2}'])
    assert path.read_bytes() == b'{"a": 1}\n{"cut": "hal\n{"a": 2}\n'
    result = jsonl.read(path)
    assert result.rows == [{"a": 1}, {"a": 2}]
    assert (result.torn, result.split) == (1, 0)
    assert result.health is Health.DEGRADED


def test_a_terminated_tail_is_not_healed_with_a_blank_line(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    jsonl.append_lines(path, ['{"a": 2}'])
    assert b"\n\n" not in path.read_bytes()


def test_undecodable_bytes_in_a_line_are_written_back_byte_for_byte(tmp_path):
    path = tmp_path / "l.jsonl"
    line = b'{"a": "\xff\xfe"}'.decode("utf-8", errors="surrogateescape")
    jsonl.append_lines(path, [line])
    assert path.read_bytes() == b'{"a": "\xff\xfe"}\n'


def test_append_dumps_with_ensure_ascii_false(tmp_path):
    path = tmp_path / "l.jsonl"
    assert jsonl.append(path, {"note": "café   x"}) == 1
    assert path.read_bytes() == (
        json.dumps({"note": "café   x"}, ensure_ascii=False)
        + "\n").encode("utf-8")


def test_a_row_over_8_kib_is_written_in_one_call(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"cut"')          # heal prefix must ride the same call
    calls = []
    real_open = Path.open

    def counting_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if self == path and "a" in mode:
            real_write = handle.write

            def write(data):
                calls.append(len(data))
                return real_write(data)

            return _Proxy(handle, write)
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    big = _row(blob="x" * 20000)
    jsonl.append_lines(path, [big])
    assert len(calls) == 1
    assert calls[0] == len(b"\n" + big.encode() + b"\n")


class _Proxy:
    def __init__(self, handle, write):
        self._handle, self.write = handle, write

    def __enter__(self):
        self._handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def test_append_lines_takes_the_dir_lock_sidecar_beside_the_ledger(tmp_path):
    path = tmp_path / "l.jsonl"
    jsonl.append_lines(path, ['{"a": 1}'])
    assert (tmp_path / ".pointer.lock").exists()
    assert (tmp_path / ".pointer.lock").read_bytes() == b""


def test_lock_false_leaves_no_sidecar(tmp_path):
    path = tmp_path / "l.jsonl"
    jsonl.append_lines(path, ['{"a": 1}'], lock=False)
    assert not (tmp_path / ".pointer.lock").exists()
    assert path.read_bytes() == b'{"a": 1}\n'


def test_a_contended_lock_fails_open_after_the_retry_budget(
        tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    sleeps = []
    monkeypatch.setattr(jsonl, "_LOCK_TRIES", 4)
    monkeypatch.setattr(jsonl.time, "sleep", sleeps.append)
    holder = open(tmp_path / ".pointer.lock", "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        assert jsonl.append_lines(path, ['{"a": 1}']) == 1
    finally:
        holder.close()
    assert path.read_bytes() == b'{"a": 1}\n'
    assert len(sleeps) == 4


def test_ledger_lock_excludes_a_second_holder(tmp_path):
    path = tmp_path / "l.jsonl"
    with jsonl.ledger_lock(path) as held:
        assert held is True
        probe = open(tmp_path / ".pointer.lock", "a+")
        try:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
            except OSError:
                got = False
        finally:
            probe.close()
    assert got is False


def test_ledger_lock_in_a_missing_dir_yields_not_held(tmp_path):
    with jsonl.ledger_lock(tmp_path / "nope" / "l.jsonl") as held:
        assert held is False


def test_rewrite_stages_a_pid_named_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"a": 1}\n{"a": 2}\n')
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(jsonl.os, "replace", spy)
    jsonl.rewrite(path, lambda line, row: None if row["a"] == 1 else line)
    assert seen == [f"l.jsonl.{os.getpid()}.tmp"]
    assert path.read_bytes() == b'{"a": 2}\n'


def test_rewrite_takes_the_lock(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    jsonl.rewrite(path, lambda line, row: None)
    assert (tmp_path / ".pointer.lock").exists()


def test_replace_swaps_the_text_in_atomically_under_the_lock(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b"old\n")
    jsonl.replace(path, '{"a": 1}\n')
    assert path.read_bytes() == b'{"a": 1}\n'
    assert (tmp_path / ".pointer.lock").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_replace_round_trips_undecodable_bytes(tmp_path):
    path = tmp_path / "l.jsonl"
    text = b'{"a": "\xff"}\n'.decode("utf-8", errors="surrogateescape")
    jsonl.replace(path, text)
    assert path.read_bytes() == b'{"a": "\xff"}\n'


def test_replace_hands_the_text_to_a_caller_supplied_stager(tmp_path):
    path = tmp_path / "l.jsonl"
    got = []
    jsonl.replace(path, "x\n", write=lambda target, text: got.append(
        (target, text)))
    assert got == [(path, "x\n")]
    assert not path.exists()


def test_a_missing_ledger_is_not_torn(tmp_path):
    assert jsonl._unterminated(tmp_path / "never-written.jsonl") is False


def test_an_unopenable_path_is_not_torn(tmp_path):
    assert jsonl._unterminated(tmp_path) is False


def test_an_unstattable_ledger_is_not_reported_as_torn(tmp_path, monkeypatch):
    """Guessing True here would inject a blank line into a healthy ledger on
    every append."""
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"cut"')

    def boom(*args, **kwargs):
        raise OSError("no stat for you")

    monkeypatch.setattr(Path, "stat", boom)
    assert jsonl._unterminated(path) is False


def test_append_to_an_unwritable_path_propagates_the_oserror(tmp_path):
    import pytest
    with pytest.raises(OSError):
        jsonl.append_lines(tmp_path / "no-such-dir" / "l.jsonl", ["{}"])
