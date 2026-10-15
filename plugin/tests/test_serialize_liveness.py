"""Serialize liveness for hosts (#1179 PR 2).

One canonical session key (`ledger._session_key`) at every boundary, a
heartbeat index and lease, a bounded log tail, and `api.serializes`: one row
per session joining heartbeats, the serialize.log fold and the checkpoint
directory. The state table these tests walk is section 3.1 of the host API
contract design."""

import os
import time
from datetime import datetime, timezone

import pytest

from daimon_briefing import ledger

CEILING = 1800
ROLLOUT = "rollout-2026-10-12T10-11-12-abc"


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"


@pytest.fixture
def cp_dir(tmp_path):
    return tmp_path / ".daimon" / "checkpoints"


def _stamp(seconds_ago, now=None):
    t = (now if now is not None else time.time()) - seconds_ago
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _spawn(sid, seconds_ago, *, host="codex-session-end", project="/p/A",
           transcript=None, retry=False, now=None):
    verb = "session-start: retry" if retry else f"{host}: spawned"
    line = (f"{_stamp(seconds_ago, now)} {verb} serialize for {sid} "
            f"(reason: exit, project: {project})")
    if transcript:
        line += f" (transcript: {transcript})"
    return line


def _beat(log_dir, name, *, age=0, content=""):
    d = log_dir / "heartbeats"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


# ---- D6: one canonical key at every boundary --------------------------------


def test_catch_up_spawn_under_rollout_stem_folds_with_its_result(cp_dir):
    text = "\n".join([
        _spawn(ROLLOUT, 600, host="session-start"),
        f"wrote checkpoint: {cp_dir}/{ROLLOUT}.json (took 9s)",
    ])
    folded = ledger._session_ledger(text, time.time())
    assert list(folded) == ["abc"]
    assert folded["abc"]["result_kind"] == "success"
    assert folded["abc"]["spawned"] is True


def test_bare_spawn_and_rollout_result_still_fold_into_one_entry(cp_dir):
    text = "\n".join([
        _spawn("abc", 600),
        f"wrote checkpoint: {cp_dir}/{ROLLOUT}.json (took 9s)",
    ])
    assert list(ledger._session_ledger(text, time.time())) == ["abc"]


def test_skip_line_under_rollout_stem_lands_on_the_bare_id():
    text = "\n".join([
        _spawn("abc", 600),
        f"skipped serialize for {ROLLOUT}: transcript unchanged since "
        "checkpoint (hash match)",
    ])
    folded = ledger._session_ledger(text, time.time())
    assert list(folded) == ["abc"]
    assert folded["abc"]["result_kind"] == "skipped"


def test_non_codex_ids_pass_through_the_spawn_key():
    folded = ledger._session_ledger(_spawn("S-1", 60, host="session-end"),
                                    time.time())
    assert list(folded) == ["S-1"]


def test_codex_rollout_stamp_keeps_a_bare_id_spawn_alive(log_dir, cp_dir):
    now = time.time()
    text = _spawn("abc", CEILING + 600, now=now,
                  transcript="/t/" + ROLLOUT + ".jsonl")
    # No stamp: the old-age spawn is hung (legacy rule, unchanged).
    assert len(ledger._compute_outstanding(text, now)) == 1
    _beat(log_dir, ROLLOUT, age=30)
    assert ledger._compute_outstanding(text, now) == []


def test_stale_rollout_stamp_does_not_keep_it_alive(log_dir, cp_dir):
    now = time.time()
    text = _spawn("abc", CEILING + 600, now=now,
                  transcript="/t/" + ROLLOUT + ".jsonl")
    _beat(log_dir, ROLLOUT, age=CEILING + 100)
    assert len(ledger._compute_outstanding(text, now)) == 1


def test_heartbeat_index_maps_file_names_through_the_canonical_key(log_dir):
    _beat(log_dir, ROLLOUT, age=40, content="proj-a")
    _beat(log_dir, "S-plain", age=5)
    index = ledger.heartbeat_index(time.time())
    assert set(index) == {"abc", "S-plain"}
    assert index["abc"].project_slug == "proj-a"
    assert 39 <= index["abc"].age < 60
    assert index["S-plain"].project_slug == ""


def test_heartbeat_index_keeps_the_freshest_stamp_per_key(log_dir):
    _beat(log_dir, "abc", age=900, content="old")
    _beat(log_dir, ROLLOUT, age=10, content="new")
    stamp = ledger.heartbeat_index(time.time())["abc"]
    assert stamp.project_slug == "new"
    assert stamp.age < 60


def test_heartbeat_index_of_a_missing_dir_is_empty(log_dir):
    assert ledger.heartbeat_index(time.time()) == {}


def test_heartbeat_index_skips_entries_it_cannot_read(log_dir):
    (log_dir / "heartbeats").mkdir(parents=True)
    (log_dir / "heartbeats" / "a-dir").mkdir()
    _beat(log_dir, "S-ok", age=1)
    assert set(ledger.heartbeat_index(time.time())) == {"S-ok"}


def test_heartbeat_index_raises_when_the_dir_is_not_a_dir(log_dir):
    log_dir.mkdir(parents=True)
    (log_dir / "heartbeats").write_text("not a dir")
    with pytest.raises(OSError):
        ledger.heartbeat_index(time.time())
