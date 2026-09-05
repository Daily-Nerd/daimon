"""#929: a session with an OLDER checkpoint whose re-capture failed is lost
too. Lost means "no checkpoint at or after the transcript's last conversation
row", the sweep's rule, not "no checkpoint at all"."""

import json

from daimon_briefing import ledger, store


def _ledger_error(sid, transcript):
    return {sid: {"spawned": True, "spawn_ts": 0.0, "spawn_age": 10, "project": "/p",
                  "result_kind": "error", "result_line": "error: timed out",
                  "transcript": transcript, "retried": False}}


def test_fold_keeps_a_failure_whose_checkpoint_predates_the_transcript():
    out = ledger._outstanding_failures(
        _ledger_error("S1", "/t/S1.jsonl"), 100.0, lambda sid: True, 1800,
        lambda p: True, checkpoint_covers=lambda sid, t: False)
    assert [(f["sid"], f["class"]) for f in out] == [("S1", "healable")]


def test_fold_drops_a_failure_whose_checkpoint_covers_the_transcript():
    out = ledger._outstanding_failures(
        _ledger_error("S1", "/t/S1.jsonl"), 100.0, lambda sid: True, 1800,
        lambda p: True, checkpoint_covers=lambda sid, t: True)
    assert out == []


def test_fold_without_the_new_predicate_behaves_as_before():
    out = ledger._outstanding_failures(
        _ledger_error("S1", "/t/S1.jsonl"), 100.0, lambda sid: True, 1800,
        lambda p: True)
    assert out == []


def _write_transcript(path, last_stamp):
    rows = [{"type": "user", "uuid": "u1", "timestamp": "2026-09-01T00:00:00.000Z",
             "message": {"role": "user", "content": "hi"}},
            {"type": "assistant", "uuid": "a1", "timestamp": last_stamp,
             "message": {"role": "assistant", "content": "later"}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _write_checkpoint(sid, created, sample_checkpoint):
    cp = dict(sample_checkpoint)
    cp["session_id"] = sid
    cp["created"] = created
    store.write_checkpoint(sid, cp)


def test_checkpoint_covers_is_false_when_conversation_continues_after_it(
        tmp_checkpoint_dir, tmp_path, sample_checkpoint):
    t = tmp_path / "S1.jsonl"
    _write_transcript(t, "2026-09-01T06:00:00.000Z")
    _write_checkpoint("S1", "2026-09-01T01:00:00Z", sample_checkpoint)
    assert ledger.checkpoint_covers("S1", str(t)) is False


def test_checkpoint_covers_is_true_when_the_checkpoint_is_at_or_after_the_last_row(
        tmp_checkpoint_dir, tmp_path, sample_checkpoint):
    t = tmp_path / "S1.jsonl"
    _write_transcript(t, "2026-09-01T00:30:00.000Z")
    _write_checkpoint("S1", "2026-09-01T01:00:00Z", sample_checkpoint)
    assert ledger.checkpoint_covers("S1", str(t)) is True


def test_checkpoint_covers_is_true_when_it_cannot_judge(
        tmp_checkpoint_dir, tmp_path, sample_checkpoint):
    # Missing transcript, or a legacy checkpoint with no created stamp:
    # ambiguous is not lost (#54), so the fold stays quiet.
    _write_checkpoint("S1", "2026-09-01T01:00:00Z", sample_checkpoint)
    assert ledger.checkpoint_covers("S1", str(tmp_path / "gone.jsonl")) is True
    assert ledger.checkpoint_covers("S1", None) is True
    t = tmp_path / "S2.jsonl"
    _write_transcript(t, "2026-09-01T06:00:00.000Z")
    # The store stamps `created` on every write, so a legacy file without one
    # has to be written directly to reach that branch.
    from daimon_briefing import config
    legacy = dict(sample_checkpoint)
    legacy["session_id"] = "S2"
    legacy.pop("created", None)
    (config.checkpoint_dir() / "S2.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert store.read_checkpoint("S2") is not None
    assert ledger.checkpoint_covers("S2", str(t)) is True


def test_compute_outstanding_surfaces_the_stale_failed_session(
        tmp_checkpoint_dir, tmp_path, sample_checkpoint):
    t = tmp_path / "S1.jsonl"
    _write_transcript(t, "2026-09-01T06:00:00.000Z")
    _write_checkpoint("S1", "2026-09-01T01:00:00Z", sample_checkpoint)
    text = (f"2026-09-01T05:00:00Z session-start: spawned serialize for S1 "
            f"(reason: catch-up-orphan, project: /p) (transcript: {t})\n"
            f"error: LLM call failed on merge level 2, group 1 of 1: ChatError: "
            f"command backend timed out (transcript: {t}) after 900s\n")
    out = ledger._compute_outstanding(text, 1_800_000_000.0)
    assert [(f["sid"], f["kind"], f["class"]) for f in out] == [("S1", "error", "healable")]


def test_checkpoint_covers_is_true_for_a_checkpoint_the_bare_id_cannot_address(
        tmp_checkpoint_dir, tmp_path):
    # Codex-shaped: has_checkpoint found a rollout-named file the bare id
    # cannot read back (#634). Cannot judge, so covered.
    t = tmp_path / "S9.jsonl"
    _write_transcript(t, "2026-09-01T06:00:00.000Z")
    assert store.read_checkpoint("S9") is None
    assert ledger.checkpoint_covers("S9", str(t)) is True
