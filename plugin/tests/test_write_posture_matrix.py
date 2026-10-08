"""Every ledger x every health state x every writer class (#1132 PR 10b,
D10.9, write half).

The ledger is damaged with plain bytes where bytes can express the state
(torn tail, garbage line, undecodable byte) and with the `jsonl._read_bytes`
seam where they cannot (a transient failure, an OS error). The expected
postures are the human copy of R2.3 in `test_write_posture_registry`, applied
by hand here, never read off the registry. Each cell is asserted twice: the
posture `jsonl.posture` resolves, and what the real writer of that ledger
does with it (written, refused, or skipped).
"""

import errno
import json

import pytest

from daimon_briefing import (amendments, config, jsonl, refutations,
                             relations, requests, store, trust)
from daimon_briefing.surfaces import WritePosture as W
from daimon_briefing.surfaces import Writer
from tests.test_write_posture_registry import EXPECTED

PROJECT = "/p/write-matrix"
GOOD = json.dumps({"item_ref": "i-1", "status": "resolved",
                   "ts": "2026-10-01T00:00:00Z"}).encode() + b"\n"

# state -> (how to damage the ledger, the column index it lands in or None for
# PROCEED-always)
STATES = {
    "absent": (None, None),
    "ok": ("ok", None),
    "degraded-torn": ("torn", 0),
    "transient": ("transient", 1),
    "garbage": ("garbage", 2),
    "undecodable": ("undecodable", 2),
    "os-error": ("oserror", 2),
}


def _path(name):
    d = config.checkpoint_dir() / store.project_slug(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    return d / name


def _damage(monkeypatch, path, how):
    if how is None:
        return
    if how == "ok":
        path.write_bytes(GOOD)
    elif how == "torn":
        path.write_bytes(GOOD + b'{"torn": ')
    elif how == "garbage":
        path.write_bytes(GOOD + b"<<<<<<< conflict\n")
    elif how == "undecodable":
        path.write_bytes(GOOD + b'{"a": "\xff"}\n')
    elif how in ("transient", "oserror"):
        path.write_bytes(GOOD)
        code = errno.EAGAIN if how == "transient" else errno.EACCES
        real = jsonl._read_bytes

        def fake(p):
            if p == path:
                raise OSError(code, "injected")
            return real(p)
        monkeypatch.setattr(jsonl, "_read_bytes", fake)
        monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)


def _expected(name, writer, index):
    if writer is Writer.CURE or index is None:
        return W.PROCEED
    return EXPECTED[name][writer][index]


# One real writer per ledger: (callable(writer) -> bool "row landed").
ROW = {"event": "proposed", "item_id": "o-aaaaaaaaaaaa"}


def _writers():
    return {
        "events.jsonl": lambda w: store.append_event(
            "o-1", "resolved", project_dir=PROJECT, writer=w),
        "trust.jsonl": lambda w: trust.append(
            dict(ROW), project_dir=PROJECT, writer=w),
        "refutations.jsonl": lambda w: refutations.append(
            dict(ROW), project_dir=PROJECT, writer=w),
        "amendments.jsonl": lambda w: amendments.append(
            dict(ROW), project_dir=PROJECT, writer=w),
        "requests.jsonl": lambda w: requests.append(
            dict(ROW), project_dir=PROJECT, writer=w),
        "relations.jsonl": lambda w: relations._append(
            dict(ROW), project_dir=PROJECT, writer=w),
        # The two counters take no writer: EMITTER is their only class.
        "verification.jsonl": lambda w: store.append_verification(
            "o-1", "quote", "missed", project_dir=PROJECT),
        "forget-hits.jsonl": lambda w: store.record_forget_hits(
            [{"text": "a value"}], project_dir=PROJECT),
    }


CELLS = [(name, state, writer)
         for name in sorted(EXPECTED)
         for state in STATES
         for writer in list(EXPECTED[name]) + [Writer.CURE]]


@pytest.mark.parametrize("name,state,writer", CELLS)
def test_the_posture_of_every_cell(tmp_checkpoint_dir, monkeypatch,
                                   name, state, writer):
    how, index = STATES[state]
    path = _path(name)
    _damage(monkeypatch, path, how)
    got = jsonl.posture(path, name, writer)
    assert got.write is _expected(name, writer, index), (name, state, writer)


@pytest.mark.parametrize("name,state,writer", [
    c for c in CELLS if c[0] in _writers() and c[2] is not Writer.CURE])
def test_the_real_writer_does_what_its_posture_says(
        tmp_checkpoint_dir, monkeypatch, name, state, writer):
    how, index = STATES[state]
    path = _path(name)
    _damage(monkeypatch, path, how)
    before = path.read_bytes() if path.exists() else b""
    posture = _expected(name, writer, index)
    write = _writers()[name]
    if name in ("verification.jsonl", "forget-hits.jsonl"):
        # Counters: the only class is EMITTER, so judge them as one.
        assert writer is Writer.EMITTER
    with jsonl.surface_refusals():
        if posture is W.REFUSE:
            with pytest.raises(jsonl.Refused):
                write(writer)
            landed = False
        else:
            landed = bool(write(writer))
    after = path.read_bytes() if path.exists() else b""
    if posture is W.PROCEED:
        assert landed and len(after) > len(before), (name, state, writer)
    else:
        assert not landed and after == before, (name, state, writer)


def test_the_matrix_is_not_vacuous():
    assert len(CELLS) >= 150
    # Every ledger the registry declares for writes appears.
    assert {c[0] for c in CELLS} == set(EXPECTED)
    # And every state and every declared class.
    assert {c[1] for c in CELLS} == set(STATES)
    assert {c[2] for c in CELLS} == set(Writer)
