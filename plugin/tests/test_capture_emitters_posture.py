"""Capture judges its events ledger ONCE before it emits (#1132 PR 10b).

An EMITTER write to an unproven events ledger is skipped, so there is nothing
to emit: `carry_forward` asks once and skips both emitters, instead of every
row paying for its own judgement (and, on a transient ledger, for its own
retries).
"""

import errno

from daimon_briefing import capture, jsonl, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/capture-emitters"


def _cp(sid, text):
    return {"session_id": sid, "created": "2026-09-01T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": text, "trust": "inferred"}]},
            "epistemic_snapshot": {}}


def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(capture, "_emit_supersede_candidates",
                        lambda *a, **k: calls.append("supersede") or 0)
    monkeypatch.setattr(capture, "_emit_corroborations",
                        lambda *a, **k: calls.append("corroborate") or 0)
    return calls


def _seed():
    store.write_checkpoint("S-prev", _cp("S-prev", "an earlier decision"),
                           project_dir=PROJECT, writer=Writer.HUMAN)


def test_a_healthy_ledger_emits(tmp_checkpoint_dir, monkeypatch):
    _seed()
    calls = _spy(monkeypatch)
    capture.carry_forward(_cp("S-new", "a new decision"), PROJECT)
    assert calls == ["supersede", "corroborate"]


def test_an_unproven_ledger_emits_nothing_and_asks_once(
        tmp_checkpoint_dir, monkeypatch):
    _seed()
    store._events_path(PROJECT).write_bytes(b"<<<<<<< conflict\n")
    calls = _spy(monkeypatch)
    capture.carry_forward(_cp("S-new", "a new decision"), PROJECT)
    assert calls == []


def test_a_transient_ledger_is_judged_once_not_per_row(
        tmp_checkpoint_dir, monkeypatch):
    _seed()
    path = store._events_path(PROJECT)
    path.write_bytes(b"")
    real = jsonl._read_bytes

    def flaky(p):
        if p == path:
            raise OSError(errno.EAGAIN, "busy")
        return real(p)
    monkeypatch.setattr(jsonl, "_read_bytes", flaky)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)
    judged = []
    real_posture = jsonl.posture
    monkeypatch.setattr(jsonl, "posture",
                        lambda *a, **k: judged.append(a[1]) or
                        real_posture(*a, **k))
    calls = _spy(monkeypatch)
    capture.carry_forward(_cp("S-new", "a new decision"), PROJECT)
    assert calls == []
    assert judged.count("events.jsonl") == 1


def test_emitters_open_is_true_for_an_unknown_project(tmp_checkpoint_dir):
    assert store.emitters_open(None) is True
