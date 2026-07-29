"""#404: hit accounting on the forget ledger — suppression as a published number.

When the value-keyed tombstone (#402) catches a re-assertion at capture, that
event is otherwise silent. This records it (count + last_hit_at) on the
read/telemetry side — mirroring the verification-counts pattern, never touching
the append-only lifecycle stream — and surfaces it in `daimon status` when
non-zero.

The ledger stores ONLY the canonical hash key + timestamp, never the text: the
value forget removed must not be re-persisted (redact_text does not catch the
free-text PII users run `forget` on). The published value is the COUNT.
"""

import json

import pytest

from daimon_briefing import cli, normalize, render, store

_A = "/repo/forget-hits-A"
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"


def _cp(sid, created, decisions):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {
            "recent_decisions": [{"text": d, "trust": "inferred"} for d in decisions]
        },
    }


def _forget_S(monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored = store.read_latest(project_dir=_A, fallback=False)
    x_id = next(d["id"] for d in stored["working_context"]["recent_decisions"]
                if d["text"] == _S)
    assert cli.main(["forget", x_id]) == 0


def test_no_hits_before_any_suppression(tmp_checkpoint_dir, monkeypatch):
    _forget_S(monkeypatch)
    stats = store.forget_hit_stats(project_dir=_A)
    assert stats["count"] == 0
    assert stats["last_hit_at"] is None


def test_capture_suppression_records_a_hit(tmp_checkpoint_dir, monkeypatch):
    _forget_S(monkeypatch)
    # a later session re-extracts S (suppressed) + T (kept)
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stats = store.forget_hit_stats(project_dir=_A)
    assert stats["count"] == 1
    assert stats["last_hit_at"]  # a timestamp was stamped
    # the recorded row is the canonical key, never the value's content
    row = json.loads(store._forget_hits_path(_A).read_text().splitlines()[0])
    assert row["key"] == normalize.content_key(_S)
    assert set(row) == {"ts", "key"}


def test_ledger_holds_no_forgotten_text(tmp_checkpoint_dir, monkeypatch):
    """The forget-hits ledger must never re-persist the value forget removed —
    not even a prefix. Only the hash key + timestamp may reach disk."""
    _forget_S(monkeypatch)
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    raw = store._forget_hits_path(_A).read_text(encoding="utf-8")
    assert _S not in raw                    # forgotten value absent
    for token in _S.split():                # and no word of it, either
        assert token not in raw


def test_hits_accumulate_across_sessions(tmp_checkpoint_dir, monkeypatch):
    _forget_S(monkeypatch)
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S]),
                           project_dir=_A)
    store.write_checkpoint("S3", _cp("S3", "2026-07-04T00:00:00Z", [_S]),
                           project_dir=_A)
    assert store.forget_hit_stats(project_dir=_A)["count"] == 2


def test_status_surfaces_forget_hits_when_nonzero(tmp_checkpoint_dir, capsys, monkeypatch):
    _forget_S(monkeypatch)
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "forget ledger" in out
    assert "suppressed 1 re-assertion" in out


def test_status_silent_when_no_hits(tmp_checkpoint_dir, capsys, monkeypatch):
    _forget_S(monkeypatch)  # forgot something, but nothing has re-asserted yet
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "forget ledger" not in capsys.readouterr().out


def test_status_rich_path_surfaces_forget_hits(tmp_checkpoint_dir, capsys, monkeypatch):
    pytest.importorskip("rich")
    _forget_S(monkeypatch)
    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    assert "forget ledger" in capsys.readouterr().out


# ---- fail-open / edge branches (mirror the verification-ledger posture) ----


def test_record_forget_hits_noop_on_empty_and_kill_switch(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    assert store.record_forget_hits([], project_dir=_A) is False        # nothing to record
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert store.record_forget_hits([{"text": _S}], project_dir=_A) is False  # kill switch


def test_record_forget_hits_skips_non_dict_items(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    assert store.record_forget_hits([{"text": _S}, "not-a-dict", 7],
                                    project_dir=_A) is True
    assert store.forget_hit_stats(project_dir=_A)["count"] == 1


def test_record_forget_hits_survives_oserror(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    p = store._forget_hits_path(_A)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.mkdir()  # a directory where the file should be -> open('a') raises OSError
    assert store.record_forget_hits([{"text": _S}], project_dir=_A) is False


def test_forget_hit_stats_skips_corrupt_rows(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    p = store._forget_hits_path(_A)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('not json\n123\n{"ts":"2026-07-03T00:00:00Z","key":"k"}\n',
                 encoding="utf-8")
    stats = store.forget_hit_stats(project_dir=_A)
    assert stats["count"] == 1  # only the valid dict row counts
    assert stats["last_hit_at"] == "2026-07-03T00:00:00Z"


def test_forget_hit_stats_survives_read_error(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    p = store._forget_hits_path(_A)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.mkdir()  # a directory -> read_text raises OSError -> fail open
    assert store.forget_hit_stats(project_dir=_A) == {"count": 0, "last_hit_at": None}


def test_unknown_project_is_a_quiet_noop(tmp_checkpoint_dir):
    # an unknown project has no bucket -> no ledger path; every entry point
    # fails open (same posture as verification_counts / the events fold).
    assert store._forget_hits_path("") is None
    assert store.record_forget_hits([{"text": _S}], project_dir="") is False
    assert store.forget_hit_stats(project_dir="") == {"count": 0, "last_hit_at": None}


def test_forgotten_key_lifted_by_reopen(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S]),
                           project_dir=_A)
    stored = store.read_latest(project_dir=_A, fallback=False)
    x_id = stored["working_context"]["recent_decisions"][0]["id"]
    assert cli.main(["forget", x_id]) == 0
    key = normalize.content_key(_S)
    assert key in store.forgotten_content_keys(_A)
    # a later reopen is the latest event -> the tombstone (and its key) lifts
    store.append_event(x_id, "reopen", project_dir=_A)
    assert key not in store.forgotten_content_keys(_A)
