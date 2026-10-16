"""The heal plan core and the machine-wide heal lock (#1179 PR 3).

`heal.plan_all` is the one notion of "healable" shared by `status`, the single
heal and `heal --all` (design D7, D11, D12). These tests walk its scope rules,
the attempted set and the `presumed_dead_before` assertion with real
serialize.log text, so the classifier under them is the shipping one."""

import os
import time
from datetime import datetime, timezone

import pytest

from daimon_briefing import heal, ledger

NOW = 1_800_000_000.0


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"


def _stamp(seconds_ago):
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _failed(tmp_path, sid, seconds_ago, project="/p/A"):
    """A spawn line plus an error result line; the transcript exists on disk."""
    t = tmp_path / f"{sid}.jsonl"
    t.write_text("{}\n")
    return [
        f"{_stamp(seconds_ago + 5)} session-end: spawned serialize for {sid} "
        f"(reason: exit, project: {project}) (transcript: {t})",
        f"error: boom (transcript: {t}) after 3s",
    ]


def _spawned(tmp_path, sid, seconds_ago, project="/p/A", retry=False):
    """A spawn line with no result; `retry` writes the heal marker shape."""
    t = tmp_path / f"{sid}.jsonl"
    t.write_text("{}\n")
    if retry:
        return [f"{_stamp(seconds_ago)} session-start: retry serialize for "
                f"{sid} (prior: error)"]
    return [f"{_stamp(seconds_ago)} session-end: spawned serialize for {sid} "
            f"(reason: exit, project: {project}) (transcript: {t})"]


def _beat(log_dir, name, *, age, content=""):
    d = log_dir / "heartbeats"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    os.utime(p, (NOW - age, NOW - age))


def _text(*groups):
    return "\n".join(line for g in groups for line in g) + "\n"


def _sids(plan):
    return [t.session_id for t in plan.targets]


def _slug(path):
    return ledger._slug_of(path)


# ---- the plan lists every healable, newest first ----------------------------


def test_plan_lists_every_healable_newest_first(tmp_path):
    text = _text(_failed(tmp_path, "S-old", 3000), _failed(tmp_path, "S-new", 100),
                 _failed(tmp_path, "S-mid", 900))
    plan = heal.plan_all(text, NOW)
    assert _sids(plan) == ["S-new", "S-mid", "S-old"]
    first = plan.targets[0]
    assert first.kind == "error" and first.retried is False
    assert first.transcript.endswith("S-new.jsonl")
    assert first.project_slug == _slug("/p/A")


def test_plan_puts_the_unhealable_in_skipped_with_a_code(tmp_path):
    exhausted = _failed(tmp_path, "S-ex", 500) + _spawned(
        tmp_path, "S-ex", 400, retry=True) + ["error: boom (transcript: "
        f"{tmp_path}/S-ex.jsonl) after 2s"]
    text = _text(_failed(tmp_path, "S-ok", 100), exhausted)
    plan = heal.plan_all(text, NOW)
    assert _sids(plan) == ["S-ok"]
    assert [(s.session_id, s.reason) for s in plan.skipped] == [
        ("S-ex", "retry-exhausted")]
    assert "retry already attempted" in plan.skipped[0].detail


# ---- scope: project, session, everywhere ------------------------------------


def test_scope_by_project_slug(tmp_path):
    text = _text(_failed(tmp_path, "S-a", 100, project="/p/A"),
                 _failed(tmp_path, "S-b", 200, project="/p/B"))
    assert _sids(heal.plan_all(text, NOW, slug=_slug("/p/A"))) == ["S-a"]
    assert _sids(heal.plan_all(text, NOW, slug=_slug("/p/B"))) == ["S-b"]
    assert _sids(heal.plan_all(text, NOW)) == ["S-a", "S-b"]  # everywhere


def test_scope_by_sessions_uses_the_canonical_key(tmp_path):
    text = _text(_failed(tmp_path, "abc", 100), _failed(tmp_path, "S-b", 200))
    rollout = "rollout-2026-10-12T10-11-12-abc"
    assert _sids(heal.plan_all(text, NOW, sessions=["S-b"])) == ["S-b"]
    assert _sids(heal.plan_all(text, NOW, sessions=[rollout])) == ["abc"]


def test_scope_project_and_sessions_intersect(tmp_path):
    text = _text(_failed(tmp_path, "S-a", 100, project="/p/A"),
                 _failed(tmp_path, "S-b", 200, project="/p/B"))
    plan = heal.plan_all(text, NOW, slug=_slug("/p/A"), sessions=["S-b"])
    assert plan.targets == ()


def test_out_of_scope_failures_are_neither_targets_nor_skips(tmp_path):
    text = _text(_failed(tmp_path, "S-a", 100, project="/p/A"),
                 _failed(tmp_path, "S-b", 200, project="/p/B"))
    plan = heal.plan_all(text, NOW, slug=_slug("/p/A"))
    assert [s.session_id for s in plan.skipped] == []


# ---- the attempted set -------------------------------------------------------


def test_attempted_sessions_are_not_planned_again(tmp_path):
    text = _text(_failed(tmp_path, "S-1", 100), _failed(tmp_path, "S-2", 200))
    plan = heal.plan_all(text, NOW, attempted=frozenset({"S-1"}))
    assert _sids(plan) == ["S-2"]


def test_a_forced_target_is_planned_once_per_run(tmp_path):
    # force ignores the retry marker, so without the attempted set a failing
    # session would be healable again after every run, forever.
    failed = _failed(tmp_path, "S-1", 100)
    retried = _spawned(tmp_path, "S-1", 50, retry=True) + [
        f"error: boom (transcript: {tmp_path}/S-1.jsonl) after 2s"]
    text = _text(failed, retried)
    assert _sids(heal.plan_all(text, NOW)) == []
    assert _sids(heal.plan_all(text, NOW, force=True)) == ["S-1"]
    assert _sids(heal.plan_all(text, NOW, force=True,
                               attempted=frozenset({"S-1"}))) == []


# ---- D11: presumed_dead_before relaxes BOTH gates ---------------------------

EPOCH = NOW - 600  # the host's process was killed 10 min ago


def test_a_young_spawn_with_a_pre_epoch_stamp_becomes_healable(tmp_path, log_dir):
    slug = _slug("/p/A")
    text = _text(_spawned(tmp_path, "S-1", 300))
    _beat(log_dir, "S-1", age=700, content=slug)
    assert _sids(heal.plan_all(text, NOW, slug=slug)) == []  # young: alive
    plan = heal.plan_all(text, NOW, slug=slug, presumed_dead_before=NOW - 100)
    assert _sids(plan) == ["S-1"]
    assert plan.targets[0].kind == "hung"


def test_a_stamp_touched_after_the_epoch_still_proves_life(tmp_path, log_dir):
    slug = _slug("/p/A")
    text = _text(_spawned(tmp_path, "S-1", 1000))
    _beat(log_dir, "S-1", age=30, content=slug)
    plan = heal.plan_all(text, NOW, slug=slug, presumed_dead_before=NOW - 100)
    assert _sids(plan) == []
    assert plan.skipped == ()


def test_a_young_retry_with_no_stamp_stops_reading_repairing(tmp_path):
    slug = _slug("/p/A")
    text = _text(_failed(tmp_path, "S-1", 900),
                 _spawned(tmp_path, "S-1", 300, retry=True))
    alive = heal.plan_all(text, NOW, slug=slug)
    assert [(s.session_id, s.reason) for s in alive.skipped] == [
        ("S-1", "repairing")]
    dead = heal.plan_all(text, NOW, slug=slug, presumed_dead_before=NOW - 100)
    assert [s.reason for s in dead.skipped] != ["repairing"]
    assert [(s.session_id, s.reason) for s in dead.skipped] == [("S-1", "hung")]


def test_a_session_of_another_project_is_never_presumed_dead(tmp_path, log_dir):
    text = _text(_spawned(tmp_path, "S-b", 300, project="/p/B"))
    _beat(log_dir, "S-b", age=700, content=_slug("/p/B"))
    plan = heal.plan_all(text, NOW, slug=_slug("/p/A"),
                         presumed_dead_before=NOW - 100)
    assert plan.targets == () and plan.skipped == ()
    # and with B's own scope it is, which proves the gate is the scope
    own = heal.plan_all(text, NOW, slug=_slug("/p/B"),
                        presumed_dead_before=NOW - 100)
    assert _sids(own) == ["S-b"]


def test_an_empty_stamp_is_attributed_through_the_spawn_cwd(tmp_path, log_dir):
    text = _text(_spawned(tmp_path, "S-1", 300, project="/p/A"))
    _beat(log_dir, "S-1", age=700, content="")
    mine = heal.plan_all(text, NOW, slug=_slug("/p/A"),
                         presumed_dead_before=NOW - 100)
    assert _sids(mine) == ["S-1"]
    other = heal.plan_all(text, NOW, slug=_slug("/p/B"),
                          presumed_dead_before=NOW - 100)
    assert other.targets == ()


def test_a_stamp_naming_another_project_is_not_attributed_by_the_cwd(
        tmp_path, log_dir):
    text = _text(_spawned(tmp_path, "S-1", 300, project="/p/A"))
    _beat(log_dir, "S-1", age=700, content=_slug("/p/B"))
    plan = heal.plan_all(text, NOW, slug=_slug("/p/A"),
                         presumed_dead_before=NOW - 100)
    assert plan.targets == ()


def test_presumed_dead_without_a_project_scope_is_ignored(tmp_path):
    text = _text(_spawned(tmp_path, "S-1", 300))
    plan = heal.plan_all(text, NOW, presumed_dead_before=NOW - 100)
    assert plan.targets == ()


def test_the_epoch_must_be_after_the_spawn(tmp_path):
    slug = _slug("/p/A")
    text = _text(_spawned(tmp_path, "S-1", 300))
    plan = heal.plan_all(text, NOW, slug=slug, presumed_dead_before=NOW - 400)
    assert plan.targets == ()  # spawned after the host was killed: not ours


def test_an_unreadable_heartbeat_dir_does_not_break_the_plan(
        tmp_path, log_dir, monkeypatch):
    slug = _slug("/p/A")
    text = _text(_spawned(tmp_path, "S-1", 300))
    monkeypatch.setattr(ledger, "heartbeat_index",
                        lambda now=None: (_ for _ in ()).throw(OSError("denied")))
    plan = heal.plan_all(text, NOW, slug=slug, presumed_dead_before=NOW - 100)
    assert _sids(plan) == ["S-1"]  # nothing proves life


# ---- the item shape plan_all requires ---------------------------------------


def _entry(**kw):
    base = {"spawned": True, "spawn_ts": NOW - 4000, "spawn_age": 4000,
            "project": "/p/A", "result_kind": None, "result_line": None,
            "transcript": "/t/x.jsonl", "retried": False}
    base.update(kw)
    return base


def _real_items():
    """Items from the real producer: an error, a hung and a repairing one."""
    ledger_ = {
        "E": _entry(result_kind="error", result_line="error: x (transcript: /t/x.jsonl) after 1s"),
        "H": _entry(),
        "R": _entry(retried=True, spawn_age=60),
    }
    out = ledger._outstanding_failures(
        ledger_, NOW, lambda sid: False, 1800, lambda p: True)
    return {f["sid"]: f for f in out}


def test_every_healable_item_the_classifier_emits_carries_the_required_keys():
    items = _real_items()
    assert {"E", "H"} <= set(items)
    for sid in ("E", "H"):
        assert items[sid]["class"] == "healable"
        assert {"kind", "age", "retried"} <= set(items[sid])


def test_the_test_helper_builds_the_shape_the_classifier_emits():
    from tests._heal_items import item
    real = _real_items()
    assert set(item("E")) == set(real["E"])
    assert set(item("H", kind="hung")) == set(real["H"])


def test_plan_all_requires_kind_age_and_retried_on_a_healable_item(monkeypatch):
    from tests._heal_items import item
    for key in ("kind", "age", "retried"):
        broken = item("S-1")
        del broken[key]
        monkeypatch.setattr(ledger, "_compute_outstanding",
                            lambda text, now, force=False, b=broken: [b])
        with pytest.raises(KeyError, match=key):
            heal.plan_all("x", NOW)


def test_plan_all_does_not_read_those_keys_off_an_item_it_skips(monkeypatch):
    from tests._heal_items import item
    skipped = item("S-1", "retry-exhausted")
    for key in ("kind", "age", "retried"):
        del skipped[key]
    monkeypatch.setattr(ledger, "_compute_outstanding",
                        lambda text, now, force=False: [skipped])
    assert [s.session_id for s in heal.plan_all("x", NOW).skipped] == ["S-1"]


# ---- the legacy dict over the same plan -------------------------------------


def test_the_legacy_plan_dict_and_plan_all_agree(tmp_path):
    text = _text(_failed(tmp_path, "S-1", 100), _failed(tmp_path, "S-2", 500))
    legacy = ledger._heal_plan(text, NOW)
    plan = heal.plan_all(text, NOW)
    assert legacy["target"]["sid"] == plan.targets[0].session_id
    assert [s["sid"] for s in legacy["skipped"]] == ["S-2"]
    assert "re-run" in legacy["skipped"][0]["reason"]


# ---- the machine-wide lock ---------------------------------------------------


def test_the_lock_is_exclusive_and_released_on_exit(log_dir):
    with heal.try_lock() as first:
        assert first is True
        with heal.try_lock() as second:
            assert second is False
    with heal.try_lock() as again:
        assert again is True


def test_the_lock_is_released_when_the_body_raises(log_dir):
    with pytest.raises(RuntimeError):
        with heal.try_lock():
            raise RuntimeError("boom")
    with heal.try_lock() as again:
        assert again is True


def test_the_lock_fails_open_without_fcntl(log_dir, monkeypatch):
    monkeypatch.setattr(heal, "_fcntl", None)
    with heal.try_lock() as a, heal.try_lock() as b:
        assert a is True and b is True


def test_the_lock_fails_open_when_the_dir_is_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(blocker / "logs"))
    with heal.try_lock() as got:
        assert got is True


def test_a_failing_unlock_never_breaks_the_caller(log_dir, monkeypatch):
    class Stub:
        LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 8

        @staticmethod
        def flock(fd, op):
            if op == Stub.LOCK_UN:
                raise OSError("unlock failed")

    monkeypatch.setattr(heal, "_fcntl", Stub)
    with heal.try_lock() as got:
        assert got is True


def test_the_lock_lives_at_logs_dot_heal_lock(log_dir):
    with heal.try_lock():
        assert (log_dir / ".heal.lock").exists()


def test_time_is_never_read_by_the_plan(tmp_path, monkeypatch):
    # plan_all takes `now`; a reader that called the wall clock would make a
    # host's two calls with the same inputs disagree.
    text = _text(_failed(tmp_path, "S-1", 100))
    monkeypatch.setattr(time, "time", lambda: (_ for _ in ()).throw(
        AssertionError("wall clock read")))
    assert _sids(heal.plan_all(text, NOW)) == ["S-1"]
