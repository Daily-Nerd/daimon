"""Per-line health of a ledger file (#1132 PR 2b).

Every fixture is written with plain `write_bytes`, never through a package
append function: a health reader tested against the writers it will judge
could only ever agree with them.
"""

import errno
import json
import stat
from types import SimpleNamespace

import pytest

from daimon_briefing import jsonl
from daimon_briefing.jsonl import Health


def _row(**kw):
    return json.dumps(kw or {"a": 1}, ensure_ascii=False).encode("utf-8")


def _no_sleep(_seconds):
    return None


def test_a_missing_file_is_absent(tmp_path):
    result = jsonl.read(tmp_path / "nope.jsonl")
    assert result.health is Health.ABSENT
    assert result.rows == []


def test_an_empty_file_is_ok(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b"")
    result = jsonl.read(path)
    assert (result.health, result.rows) == (Health.OK, [])


def test_every_line_a_row_is_ok(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b"\n" + _row(a=2) + b"\n")
    result = jsonl.read(path)
    assert result.health is Health.OK
    assert result.rows == [{"a": 1}, {"a": 2}]
    assert (result.torn, result.split, result.garbage) == (0, 0, 0)


def test_crlf_lines_are_rows_not_garbage(tmp_path):
    # "\n" is the only separator, so "\r" rides on the line end and is JSON
    # whitespace: a file passed through a CRLF-converting editor stays OK.
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b"\r\n" + _row(a=2) + b"\r\n")
    result = jsonl.read(path)
    assert result.health is Health.OK
    assert result.rows == [{"a": 1}, {"a": 2}]


def test_a_truncated_line_is_degraded_and_the_rest_still_reads(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b'\n{"a": "cut off\n' + _row(a=3) + b"\n")
    result = jsonl.read(path)
    assert result.health is Health.DEGRADED
    assert result.torn == 1
    assert result.rows == [{"a": 1}, {"a": 3}]


def test_an_unterminated_torn_tail_is_degraded(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b'\n{"a": 2, "b"')
    result = jsonl.read(path)
    assert (result.health, result.torn) == (Health.DEGRADED, 1)
    assert result.rows == [{"a": 1}]


def test_a_u2028_split_row_is_counted_split_and_rejoined(tmp_path):
    whole = json.dumps({"note": "before after"}, ensure_ascii=False)
    head, tail = whole.split(" ")
    path = tmp_path / "l.jsonl"
    path.write_bytes((head + "\n" + tail + "\n").encode("utf-8")
                     + _row(a=2) + b"\n")
    result = jsonl.read(path)
    assert result.health is Health.DEGRADED
    assert (result.split, result.torn, result.garbage) == (1, 0, 0)
    assert result.rows == [{"note": "before after"}, {"a": 2}]


def test_one_undecodable_byte_poisons_only_its_own_line(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b'\n{"a": "\xff\xfe"}\n' + _row(a=3) + b"\n")
    result = jsonl.read(path)
    assert result.health is Health.UNREADABLE
    assert result.garbage == 1
    assert result.rows == [{"a": 1}, {"a": 3}]


def test_a_sync_conflict_marker_is_garbage(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b"<<<<<<< HEAD\n" + _row(a=1) + b"\n=======\n"
                     + _row(a=2) + b"\n>>>>>>> other\n")
    result = jsonl.read(path)
    assert result.health is Health.UNREADABLE
    assert result.garbage == 3
    assert result.rows == [{"a": 1}, {"a": 2}]


def test_valid_json_that_is_not_an_object_is_garbage(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b"[1, 2]\n" + _row(a=1) + b"\n")
    result = jsonl.read(path)
    assert (result.health, result.garbage) == (Health.UNREADABLE, 1)


def test_garbage_outranks_torn(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(b'{"cut\nnot json at all\n')
    result = jsonl.read(path)
    assert result.health is Health.UNREADABLE
    assert (result.torn, result.garbage) == (1, 1)


def test_a_directory_at_the_ledger_path_is_unreadable(tmp_path):
    path = tmp_path / "l.jsonl"
    path.mkdir()
    result = jsonl.read(path)
    assert result.health is Health.UNREADABLE
    assert result.detail == "EISDIR"


def test_a_permanent_oserror_is_unreadable_without_retry(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row())
    calls = []

    def boom(_p):
        calls.append(1)
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(jsonl, "_read_bytes", boom)
    result = jsonl.read(path, sleep=_no_sleep)
    assert (result.health, result.detail) == (Health.UNREADABLE, "EACCES")
    assert len(calls) == 1


def test_a_transient_errno_that_clears_on_retry_reads_ok(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row(a=1) + b"\n")
    real = jsonl._read_bytes
    attempts = []

    def flaky(p):
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError(errno.EAGAIN, "try again")
        return real(p)

    monkeypatch.setattr(jsonl, "_read_bytes", flaky)
    slept = []
    result = jsonl.read(path, sleep=slept.append)
    assert result.health is Health.OK
    assert result.rows == [{"a": 1}]
    assert slept == [0.05, 0.05]


@pytest.mark.parametrize("code", [errno.EAGAIN, errno.EBUSY, errno.EINTR,
                                  errno.ETIMEDOUT])
def test_a_transient_errno_that_never_clears_is_transient(
        tmp_path, monkeypatch, code):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row())
    attempts = []

    def stuck(_p):
        attempts.append(1)
        raise OSError(code, "still failing")

    monkeypatch.setattr(jsonl, "_read_bytes", stuck)
    slept = []
    result = jsonl.read(path, sleep=slept.append)
    assert result.health is Health.TRANSIENT
    assert result.detail == errno.errorcode[code]
    assert result.rows == []
    assert len(attempts) == 4 and slept == [0.05] * 3


def test_a_windows_sharing_violation_is_transient(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row())

    def locked(_p):
        exc = PermissionError(errno.EACCES, "in use")
        exc.winerror = 32  # type: ignore[attr-defined]
        raise exc

    monkeypatch.setattr(jsonl, "_read_bytes", locked)
    result = jsonl.read(path, sleep=_no_sleep)
    assert (result.health, result.detail) == (Health.TRANSIENT, "sharing")


def test_a_cloud_placeholder_is_transient_and_never_read(tmp_path, monkeypatch):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_row())
    monkeypatch.setattr(jsonl, "_is_dataless", lambda _st: True)

    def must_not_read(_p):
        raise AssertionError("a placeholder read would trigger a download")

    monkeypatch.setattr(jsonl, "_read_bytes", must_not_read)
    result = jsonl.read(path, sleep=_no_sleep)
    assert (result.health, result.detail) == (Health.TRANSIENT, "dataless")


def test_dataless_flag_check_is_safe_without_st_flags():
    assert jsonl._is_dataless(SimpleNamespace()) is False
    assert jsonl._is_dataless(SimpleNamespace(st_flags=0)) is False
    flag = getattr(stat, "SF_DATALESS", 0x40000000)
    assert jsonl._is_dataless(SimpleNamespace(st_flags=flag)) is True


def test_health_values_are_json_ready():
    assert json.dumps({"h": Health.DEGRADED}) == '{"h": "degraded"}'
    assert {h.value for h in Health} == {
        "absent", "ok", "degraded", "transient", "unreadable"}
