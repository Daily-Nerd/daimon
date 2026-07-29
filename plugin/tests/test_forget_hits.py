"""#404: hit accounting on the forget ledger — suppression as a published number.

When the value-keyed tombstone (#402) catches a re-assertion at capture, that
event is otherwise silent. This records it (count + last_hit_at + a short claim
snapshot) on the read/telemetry side — mirroring the verification-counts
pattern, never touching the append-only lifecycle stream — and surfaces it in
`daimon status` when non-zero.
"""

from daimon_briefing import cli, store

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
    # a short claim snapshot of what was suppressed is retained
    assert any(_S[:20] in snap for snap in stats["recent"])


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
