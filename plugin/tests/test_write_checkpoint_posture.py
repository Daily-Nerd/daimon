"""`store.write_checkpoint` is the enforcing point of an admission (#1132 PR
10b, D10.4): it reads `events.jsonl` ONCE, for the forgotten keys and for the
ledger's health, and refuses an unproven ledger before it writes a byte.

Ledgers here are plain bytes and the `jsonl._read_bytes` seam, never a
package appender.
"""

import copy
import errno
import json

import pytest

from daimon_briefing import jsonl, normalize, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/wc-posture"


def _checkpoint(sid="s-1", text="keep this decision"):
    return {
        "session_id": sid,
        "working_context": {
            "active_topic": "t", "open_questions": [],
            "recent_decisions": [{"text": text, "trust": "stated"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _events(tmp_checkpoint_dir, body):
    path = store._events_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _tombstone(text):
    key = normalize.content_key(text)
    return (json.dumps({"ts": "2026-10-01T00:00:00Z", "item_ref": "o-1",
                        "status": f"forgotten:{key}", "kind": "tombstone"})
            + "\n").encode()


def _written(tmp_checkpoint_dir):
    return sorted(p.name for p in tmp_checkpoint_dir.rglob("*.json"))


def _eio_on_events(monkeypatch, code):
    real = jsonl._read_bytes

    def fake(path):
        if path.name == "events.jsonl":
            raise OSError(code, "injected")
        return real(path)
    monkeypatch.setattr(jsonl, "_read_bytes", fake)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)


def test_the_writer_keyword_is_required():
    with pytest.raises(TypeError):
        store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT)


@pytest.mark.parametrize("body", [b"<<<<<<< conflict\n", b'{"a": "\xff"}\n'],
                         ids=["garbage", "undecodable"])
def test_an_admission_is_refused_when_events_is_unreadable(
        tmp_checkpoint_dir, body):
    _events(tmp_checkpoint_dir, body)
    with pytest.raises(jsonl.Refused) as caught:
        store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT,
                               admit=True, writer=Writer.ADMISSION)
    assert caught.value.admission is True
    assert caught.value.name == "events.jsonl"
    assert _written(tmp_checkpoint_dir) == []


def test_an_admission_is_refused_when_events_is_transient(
        tmp_checkpoint_dir, monkeypatch):
    _events(tmp_checkpoint_dir, b"")
    _eio_on_events(monkeypatch, errno.EAGAIN)
    with pytest.raises(jsonl.Refused) as caught:
        store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT,
                               writer=Writer.ADMISSION)
    assert caught.value.state == "transient"
    assert _written(tmp_checkpoint_dir) == []


def test_a_human_write_is_refused_with_the_plain_body(
        tmp_checkpoint_dir, monkeypatch):
    _events(tmp_checkpoint_dir, b"")
    _eio_on_events(monkeypatch, errno.EACCES)
    with pytest.raises(jsonl.Refused) as caught:
        store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT,
                               writer=Writer.HUMAN)
    assert caught.value.admission is False
    assert "EACCES" in str(caught.value)
    assert _written(tmp_checkpoint_dir) == []


def test_a_cure_writes_whatever_the_ledger_says(tmp_checkpoint_dir):
    _events(tmp_checkpoint_dir, b"<<<<<<< conflict\n")
    out = store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT,
                                 writer=Writer.CURE)
    assert out is not None and out.exists()


def test_a_degraded_ledger_is_proven_and_its_good_lines_still_gate(
        tmp_checkpoint_dir):
    # A torn tail is DEGRADED: writes PROCEED and the tombstone still drops
    # the forgotten value.
    _events(tmp_checkpoint_dir,
            _tombstone("forget me") + b'{"torn": ')
    cp = _checkpoint(text="forget me")
    cp["working_context"]["recent_decisions"].append(
        {"text": "keep me", "trust": "stated"})
    out = store.write_checkpoint("s-1", cp, project_dir=PROJECT, admit=True,
                                 writer=Writer.ADMISSION)
    saved = json.loads(out.read_text())
    texts = [d["text"] for d in saved["working_context"]["recent_decisions"]]
    assert texts == ["keep me"]


def test_a_healthy_and_a_missing_ledger_both_proceed(tmp_checkpoint_dir):
    out = store.write_checkpoint("s-1", copy.deepcopy(_checkpoint()),
                                 project_dir=PROJECT,
                                 writer=Writer.ADMISSION)
    assert out is not None
    _events(tmp_checkpoint_dir, _tombstone("something else"))
    out = store.write_checkpoint("s-2", copy.deepcopy(_checkpoint("s-2")),
                                 project_dir=PROJECT,
                                 writer=Writer.ADMISSION)
    assert out is not None


def test_events_is_read_once_for_the_keys_and_the_health(
        tmp_checkpoint_dir, monkeypatch):
    _events(tmp_checkpoint_dir, _tombstone("x"))
    # The first write of a bucket also stamps the once-per-upgrade census
    # marker; the gate this pins is the one every LATER write pays.
    store.write_checkpoint("s-0", _checkpoint("s-0"), project_dir=PROJECT,
                           writer=Writer.ADMISSION)
    real = jsonl.read
    seen = []

    def spy(path, **kw):
        if path.name == "events.jsonl":
            seen.append(path)
        return real(path, **kw)
    monkeypatch.setattr(jsonl, "read", spy)
    store.write_checkpoint("s-1", _checkpoint(), project_dir=PROJECT,
                           writer=Writer.ADMISSION)
    assert len(seen) == 1


def test_a_preflight_refuses_before_any_work(tmp_checkpoint_dir):
    _events(tmp_checkpoint_dir, b"junk\n")
    with pytest.raises(jsonl.Refused) as caught:
        store.admission_preflight(PROJECT)
    assert caught.value.admission is True
    store.admission_preflight("/p/never-written")  # absent: proceeds
