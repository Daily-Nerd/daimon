"""The write exits of a ledger judge it first (#1132 PR 10b, D10.3).

Fixtures are plain bytes on disk and the `jsonl._read_bytes` seam, never a
package appender: a posture tested through the writers it guards could only
agree with them.
"""

import errno
import fcntl

import pytest

from daimon_briefing import config, jsonl
from daimon_briefing.jsonl import Health, Refused
from daimon_briefing.surfaces import WritePosture as W
from daimon_briefing.surfaces import Writer

NAME = "events.jsonl"
GOOD = b'{"a": 1}\n'


def _ledger(tmp_path, body=GOOD, name=NAME):
    path = tmp_path / "checkpoints" / "slug" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is not None:
        path.write_bytes(body)
    return path


def _eio(monkeypatch, code):
    def boom(_path):
        raise OSError(code, "injected")
    monkeypatch.setattr(jsonl, "_read_bytes", boom)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)


# ---- Refused and the admission prefix --------------------------------------

def test_refused_is_an_oserror_so_every_appender_already_catches_it():
    assert issubclass(Refused, OSError)


def test_the_admission_prefix_is_the_one_place_the_class_is_detected():
    assert jsonl.ADMISSION_PREFIX == "error: admission refused: "


def test_an_admission_refusal_body_names_the_ledger_state_and_hint():
    exc = Refused(NAME, "transient", "EAGAIN", "retry", admission=True)
    assert str(exc) == "admission refused: events.jsonl is transient; retry"
    assert (jsonl.ADMISSION_PREFIX + str(exc)).startswith(
        jsonl.ADMISSION_PREFIX)
    assert exc.admission is True


def test_a_human_refusal_body_carries_the_detail():
    exc = Refused(NAME, "unreadable", "garbage",
                  "run: daimon ledger repair events")
    assert str(exc) == ("events.jsonl is unreadable (garbage); "
                        "run: daimon ledger repair events")
    assert exc.admission is False
    assert (exc.name, exc.state, exc.detail) == (NAME, "unreadable", "garbage")


def test_a_refusal_with_no_detail_has_no_empty_parentheses():
    assert str(Refused(NAME, "unreadable", "", "h")) == (
        "events.jsonl is unreadable; h")


# ---- posture(): judge the ledger once, by writer ---------------------------

@pytest.mark.parametrize("writer", [Writer.HUMAN, Writer.ADMISSION,
                                    Writer.EMITTER, Writer.CURE])
def test_a_missing_ledger_proceeds_for_every_writer(tmp_path, writer):
    p = jsonl.posture(_ledger(tmp_path, None), NAME, writer)
    assert (p.health, p.write) == (Health.ABSENT, W.PROCEED)


@pytest.mark.parametrize("writer", [Writer.HUMAN, Writer.ADMISSION,
                                    Writer.EMITTER, Writer.CURE])
def test_a_degraded_ledger_is_proven_so_every_writer_proceeds(tmp_path, writer):
    path = _ledger(tmp_path, GOOD + b'{"torn": ')
    p = jsonl.posture(path, NAME, writer)
    assert (p.health, p.write) == (Health.DEGRADED, W.PROCEED)
    assert p.detail == "torn"


@pytest.mark.parametrize("writer,expected", [
    (Writer.HUMAN, W.REFUSE), (Writer.ADMISSION, W.REFUSE),
    (Writer.EMITTER, W.SKIP), (Writer.CURE, W.PROCEED)])
def test_a_garbage_line_makes_the_ledger_unproven(tmp_path, writer, expected):
    path = _ledger(tmp_path, GOOD + b"<<<<<<< conflict\n")
    p = jsonl.posture(path, NAME, writer)
    assert (p.health, p.detail, p.write) == (Health.UNREADABLE, "garbage",
                                             expected)


def test_an_undecodable_line_is_counted_and_unproven(tmp_path):
    path = _ledger(tmp_path, GOOD + b'{"a": "\xff"}\n')
    p = jsonl.posture(path, NAME, Writer.HUMAN)
    assert (p.health, p.undecodable, p.write) == (Health.UNREADABLE, 1,
                                                 W.REFUSE)


def test_an_os_error_is_unproven_and_keeps_the_errno_name(tmp_path,
                                                         monkeypatch):
    path = _ledger(tmp_path)
    _eio(monkeypatch, errno.EACCES)
    p = jsonl.posture(path, NAME, Writer.ADMISSION)
    assert (p.health, p.detail, p.write) == (Health.UNREADABLE, "EACCES",
                                             W.REFUSE)


def test_a_transient_failure_is_unproven_not_a_pass(tmp_path, monkeypatch):
    path = _ledger(tmp_path)
    _eio(monkeypatch, errno.EAGAIN)
    p = jsonl.posture(path, NAME, Writer.HUMAN)
    assert (p.health, p.write) == (Health.TRANSIENT, W.REFUSE)
    assert jsonl.posture(path, NAME, Writer.EMITTER).write is W.SKIP


def test_a_writer_the_ledger_never_declared_is_a_bug_not_a_pass(tmp_path):
    with pytest.raises(LookupError):
        jsonl.posture(_ledger(tmp_path, name="verification.jsonl"),
                      "verification.jsonl", Writer.HUMAN)


def test_the_quarantine_sidecar_and_team_tombstones_resolve_by_name(tmp_path):
    side = _ledger(tmp_path, b"junk\n", name="events.quarantined-lines")
    assert jsonl.posture(side, "events.quarantined-lines",
                         Writer.HUMAN).write is W.REFUSE
    tomb = _ledger(tmp_path, b"junk\n", name="tombstones.jsonl")
    assert jsonl.posture(tomb, "tombstones.jsonl",
                         Writer.HUMAN).write is W.PROCEED


# ---- the append exits ------------------------------------------------------

def test_the_posture_keyword_is_required(tmp_path):
    with pytest.raises(TypeError):
        jsonl.append_lines(_ledger(tmp_path), ['{"a": 2}'])
    with pytest.raises(TypeError):
        jsonl.append(_ledger(tmp_path), {"a": 2})


def test_proceed_appends_and_still_heals_a_torn_tail(tmp_path):
    path = _ledger(tmp_path, GOOD + b'{"torn": ')
    assert jsonl.append_lines(path, ['{"a": 2}'], posture=W.PROCEED) == 1
    assert path.read_bytes() == GOOD + b'{"torn": \n{"a": 2}\n'


def test_a_refused_append_writes_nothing_and_raises_refused(tmp_path):
    path = _ledger(tmp_path, GOOD + b"junk\n")
    before = path.read_bytes()
    posture = jsonl.posture(path, NAME, Writer.HUMAN)
    with pytest.raises(Refused) as caught:
        jsonl.append_lines(path, ['{"a": 2}'], posture=posture)
    assert path.read_bytes() == before
    assert caught.value.name == NAME
    assert caught.value.state == "unreadable"
    assert caught.value.hint == "run: daimon ledger repair events"
    assert caught.value.admission is False


def test_an_admission_refusal_says_so(tmp_path):
    path = _ledger(tmp_path, GOOD + b"junk\n")
    posture = jsonl.posture(path, NAME, Writer.ADMISSION)
    with pytest.raises(Refused) as caught:
        jsonl.append(path, {"a": 2}, posture=posture)
    assert caught.value.admission is True


def test_a_skipped_append_returns_zero_and_records_usage(tmp_path):
    path = _ledger(tmp_path, GOOD + b"junk\n")
    before = path.read_bytes()
    posture = jsonl.posture(path, NAME, Writer.EMITTER)
    assert jsonl.append_lines(path, ['{"a": 2}'], posture=posture) == 0
    assert path.read_bytes() == before
    usage = (config.log_dir() / "usage.log").read_text()
    assert usage.strip().endswith("events.jsonl:skipped-unreadable")


def test_a_bare_posture_value_carries_no_state_but_still_refuses(tmp_path):
    path = _ledger(tmp_path)
    with pytest.raises(Refused):
        jsonl.append_lines(path, ['{"a": 2}'], posture=W.REFUSE)
    assert path.read_bytes() == GOOD
    assert jsonl.append_lines(path, ['{"a": 2}'], posture=W.SKIP) == 0


def test_nothing_to_write_is_neither_a_refusal_nor_a_read(tmp_path):
    path = _ledger(tmp_path, b"junk\n")

    def never():
        raise AssertionError("an empty append must not judge the ledger")
    assert jsonl.append_lines(path, [], posture=never) == 0


def test_a_lazy_posture_is_judged_under_the_ledger_lock(tmp_path):
    path = _ledger(tmp_path)
    seen = []

    def judge():
        # The sidecar lock is held by the appender: a second flock on it
        # cannot be taken while the posture is being resolved.
        fh = open(path.parent / jsonl.LOCK_NAME, "a+")
        try:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.append("free")
            except OSError:
                seen.append("held")
        finally:
            fh.close()
        return W.PROCEED

    assert jsonl.append_lines(path, ['{"a": 2}'], posture=judge) == 1
    assert seen == ["held"]


def test_lazy_posture_resolves_the_registry_row_at_append_time(tmp_path):
    path = _ledger(tmp_path, GOOD + b"junk\n")
    with pytest.raises(Refused):
        jsonl.append(path, {"a": 2},
                     posture=jsonl.lazy_posture(path, Writer.HUMAN))
    assert jsonl.append(path, {"a": 2},
                        posture=jsonl.lazy_posture(path, Writer.CURE)) == 1
