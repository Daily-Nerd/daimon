"""`api.heal_plan` and `api.heal_all` (#1179 PR 4, design 3.2, D8, D11, D23).

`heal_plan` is the pure read over the same plan core the CLI loop uses, so its
codes are the ones `status` classifies. `heal_all` is the one launcher: it plans,
takes a look at the machine-wide lock, then starts `daimon heal --all` detached
with the env the child needs to see the caller's store. The launch itself goes
through `heal.launch`, which most tests replace with a spy; two tests run the
real thing (`launch` against a tiny child, and a full `wait=True` against a real
`python -m daimon_briefing.cli heal --all`)."""

import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest

from daimon_briefing import api, cli, config, heal, heal_host, ledger

NOW = 1_800_000_000.0


def _stamp(seconds_ago, now=None):
    t = (now if now is not None else time.time()) - seconds_ago
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_path():
    p = config.log_dir() / "serialize.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(*lines):
    with _log_path().open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def _project(tmp_path, name="proj"):
    p = tmp_path / name
    p.mkdir(exist_ok=True)
    return str(p)


def _fail(tmp_path, sid, seconds_ago, project, *, now=None, exists=True):
    """A spawn line plus an error result line; the transcript is on disk."""
    t = tmp_path / f"{sid}.jsonl"
    if exists:
        t.write_text("{}\n")
    _append(f"{_stamp(seconds_ago + 5, now)} session-end: spawned serialize for "
            f"{sid} (reason: exit, project: {project}) (transcript: {t})",
            f"error: boom (transcript: {t}) after 3s")
    return t


class Launches:
    """A `heal.launch` spy: records (argv, env, wait), returns `rc`."""

    def __init__(self, rc=None, exc=None):
        self.calls = []
        self.rc = rc
        self.exc = exc

    def __call__(self, argv, env, *, wait=False):
        self.calls.append((list(argv), dict(env), wait))
        if self.exc is not None:
            raise self.exc
        return self.rc


@pytest.fixture
def spy(monkeypatch):
    s = Launches()
    monkeypatch.setattr(heal, "launch", s)
    return s


# ---- the export surface -----------------------------------------------------


def test_the_heal_names_are_exported_from_api():
    for name in ("heal_plan", "heal_all", "HealPlan", "HealReport",
                 "HealTarget", "HealSkip"):
        assert name in api.__all__, name
        assert hasattr(api, name), name
    assert api.HealPlan is heal.HealPlan
    assert api.HealTarget is heal.HealTarget
    assert api.HealSkip is heal.HealSkip
    assert api.HealReport is heal.HealReport


def test_the_api_docstring_carries_the_heal_contract():
    doc = api.__doc__
    assert "heal_all" in doc and "presumed_dead_before" in doc
    assert "laptop" in doc            # D11: never pass it where children survive
    assert "unknown" in doc           # D2 guidance
    assert "launcher" in doc          # D23: the named exception


# ---- heal_plan --------------------------------------------------------------


def test_heal_plan_codes_match_what_status_classifies(tmp_path):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-ok", 100, proj, now=NOW)
    ex = _fail(tmp_path, "S-ex", 500, proj, now=NOW)
    _append(f"{_stamp(400, NOW)} session-start: retry serialize for S-ex "
            f"(prior: error)", f"error: boom (transcript: {ex}) after 2s")
    _fail(tmp_path, "S-gone", 300, proj, now=NOW, exists=False)
    text = _log_path().read_text(encoding="utf-8")

    plan = api.heal_plan(proj, now=NOW)

    status_view = {f["sid"]: f["class"]
                   for f in ledger._compute_outstanding(text, NOW)}
    assert status_view == {"S-ok": "healable", "S-ex": "retry-exhausted",
                           "S-gone": "unrecoverable"}
    assert [t.session_id for t in plan.targets] == ["S-ok"]
    assert {s.session_id: s.reason for s in plan.skipped} == {
        sid: cls for sid, cls in status_view.items() if cls != "healable"}
    assert isinstance(plan, api.HealPlan)


def test_heal_plan_scopes_to_the_project_by_default(tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    _fail(tmp_path, "S-a", 100, a, now=NOW)
    _fail(tmp_path, "S-b", 200, b, now=NOW)
    assert [t.session_id for t in api.heal_plan(a, now=NOW).targets] == ["S-a"]
    assert [t.session_id for t in api.heal_plan(b, now=NOW).targets] == ["S-b"]


def test_heal_plan_everywhere_is_machine_wide(tmp_path):
    a, b = _project(tmp_path, "a"), _project(tmp_path, "b")
    _fail(tmp_path, "S-a", 100, a, now=NOW)
    _fail(tmp_path, "S-b", 200, b, now=NOW)
    plan = api.heal_plan(a, everywhere=True, now=NOW)
    assert [t.session_id for t in plan.targets] == ["S-a", "S-b"]
    assert plan.notes == ()


def test_heal_plan_sessions_narrow_the_scope(tmp_path):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj, now=NOW)
    _fail(tmp_path, "S-2", 200, proj, now=NOW)
    plan = api.heal_plan(proj, sessions=["S-2"], now=NOW)
    assert [t.session_id for t in plan.targets] == ["S-2"]
    assert api.heal_plan(proj, sessions=[], now=NOW).targets == ()


def test_heal_plan_naming_nothing_plans_nothing_instead_of_the_whole_machine(
        tmp_path, spy):
    # An empty slug must not widen the scope: `resolve_project_dir(None)` is
    # None, as for `serializes`, and plan_all treats no slug as machine-wide.
    _fail(tmp_path, "S-a", 100, _project(tmp_path, "a"), now=NOW)
    plan = api.heal_plan(now=NOW)
    assert plan.targets == () and plan.skipped == ()
    assert len(plan.notes) == 1 and "no project" in plan.notes[0]
    assert api.heal_all().status == "nothing-to-heal"
    assert spy.calls == []


def test_heal_plan_sessions_alone_name_their_own_scope(tmp_path, spy):
    _fail(tmp_path, "S-a", 100, _project(tmp_path, "a"), now=NOW)
    _fail(tmp_path, "S-b", 200, _project(tmp_path, "b"), now=NOW)
    plan = api.heal_plan(sessions=["S-b"], now=NOW)
    assert [t.session_id for t in plan.targets] == ["S-b"]
    assert plan.notes == ()
    report = api.heal_all(sessions=["S-b"], presumed_dead_before=NOW)
    assert report.status == "started"
    argv = spy.calls[0][0]
    assert "--project" not in argv and "--presumed-dead-before" not in argv
    assert argv[argv.index("--session") + 1] == "S-b"
    assert any("presumed_dead_before" in n for n in report.notes)


def test_heal_plan_force_replans_a_retry_exhausted_session(tmp_path):
    proj = _project(tmp_path)
    t = _fail(tmp_path, "S-ex", 500, proj, now=NOW)
    _append(f"{_stamp(400, NOW)} session-start: retry serialize for S-ex "
            f"(prior: error)", f"error: boom (transcript: {t}) after 2s")
    assert api.heal_plan(proj, now=NOW).targets == ()
    forced = api.heal_plan(proj, force=True, now=NOW)
    assert [x.session_id for x in forced.targets] == ["S-ex"]


def test_heal_plan_presumed_dead_before_frees_a_young_killed_spawn(tmp_path):
    proj = _project(tmp_path)
    t = tmp_path / "S-kill.jsonl"
    t.write_text("{}\n")
    _append(f"{_stamp(300, NOW)} session-end: spawned serialize for S-kill "
            f"(reason: exit, project: {proj}) (transcript: {t})")
    assert api.heal_plan(proj, now=NOW).targets == ()
    plan = api.heal_plan(proj, presumed_dead_before=NOW - 60, now=NOW)
    assert [x.session_id for x in plan.targets] == ["S-kill"]


def test_heal_plan_everywhere_ignores_presumed_dead_before_with_a_note(tmp_path):
    proj = _project(tmp_path)
    t = tmp_path / "S-kill.jsonl"
    t.write_text("{}\n")
    _append(f"{_stamp(300, NOW)} session-end: spawned serialize for S-kill "
            f"(reason: exit, project: {proj}) (transcript: {t})")
    plan = api.heal_plan(proj, everywhere=True, presumed_dead_before=NOW - 60,
                         now=NOW)
    assert plan.targets == ()                    # the epoch was ignored
    assert len(plan.notes) == 1
    assert "presumed_dead_before" in plan.notes[0]
    assert api.heal_plan(proj, everywhere=True, now=NOW).notes == ()


def test_heal_plan_is_a_pure_read(tmp_path):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-ok", 100, proj, now=NOW)
    hb = config.log_dir() / "heartbeats"
    hb.mkdir(parents=True)
    old = hb / "S-ancient"
    old.write_text("", encoding="utf-8")
    os.utime(old, (NOW - 30 * 86400, NOW - 30 * 86400))   # past the 7-day reap

    def tree():
        return sorted((str(p.relative_to(config.log_dir())), p.stat().st_mtime_ns,
                       p.stat().st_size)
                      for p in config.log_dir().rglob("*"))

    before = tree()
    api.heal_plan(proj, now=NOW)
    assert tree() == before
    assert not (config.log_dir() / heal.LOCK_NAME).exists()


def test_heal_plan_an_unreadable_log_becomes_a_note_not_a_raise(tmp_path):
    proj = _project(tmp_path)
    (config.log_dir() / "serialize.log").mkdir(parents=True)   # a dir, not a file
    plan = api.heal_plan(proj, now=NOW)
    assert plan.targets == () and plan.skipped == ()
    assert len(plan.notes) == 1


def test_heal_plan_reads_a_bounded_window_and_says_so(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-ok", 100, proj, now=NOW)
    monkeypatch.setattr(heal_host, "_HEAL_WINDOW_BYTES", 120)    # smaller than the log
    plan = api.heal_plan(proj, now=NOW)
    assert any("window" in n for n in plan.notes)


# ---- heal_all: the statuses -------------------------------------------------


def test_heal_all_with_nothing_to_heal_launches_nothing(tmp_path, spy):
    report = api.heal_all(_project(tmp_path))
    assert report.status == "nothing-to-heal"
    assert report.targets == () and report.outcomes == ()
    assert spy.calls == []


def test_heal_all_started_launches_detached_and_returns_the_plan(tmp_path, spy):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    report = api.heal_all(proj)
    assert report.status == "started"
    assert [t.session_id for t in report.targets] == ["S-1"]
    assert report.outcomes == ()
    assert len(spy.calls) == 1 and spy.calls[0][2] is False


def test_heal_all_already_running_when_the_lock_is_held(tmp_path, spy):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    with heal.try_lock() as free:
        assert free
        report = api.heal_all(proj)
    assert report.status == "already-running"
    assert [t.session_id for t in report.targets] == ["S-1"]
    assert spy.calls == []


def test_heal_all_releases_its_look_at_the_lock_before_launching(
        tmp_path, monkeypatch):
    # The child takes the lock itself; a parent still holding it would make
    # every launch a no-op.
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    held_during_launch = []

    def launch(argv, env, *, wait=False):
        with heal.try_lock() as free:
            held_during_launch.append(not free)

    monkeypatch.setattr(heal, "launch", launch)
    assert api.heal_all(proj).status == "started"
    assert held_during_launch == [False]


def test_heal_all_launch_failed_when_the_spawn_raises(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    monkeypatch.setattr(heal, "launch", Launches(exc=OSError("no exec")))
    report = api.heal_all(proj)
    assert report.status == "launch-failed"
    assert [t.session_id for t in report.targets] == ["S-1"]
    assert report.outcomes == ()
    assert any("OSError" in n for n in report.notes)


def test_heal_all_wait_true_reports_completed_with_one_outcome_per_target(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    _fail(tmp_path, "S-2", 200, proj)
    s = Launches(rc=0)
    monkeypatch.setattr(heal, "launch", s)
    report = api.heal_all(proj, wait=True)
    assert s.calls[0][2] is True
    assert report.status == "completed"
    assert [o.session_id for o in report.outcomes] == ["S-1", "S-2"]
    assert all(isinstance(o, api.SerializeRow) for o in report.outcomes)


def test_heal_all_wait_true_notes_a_failing_child(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    monkeypatch.setattr(heal, "launch", Launches(rc=1))
    report = api.heal_all(proj, wait=True)
    assert report.status == "completed"
    assert any("exit" in n for n in report.notes)


def test_heal_all_everywhere_ignores_the_epoch_and_says_so(tmp_path, spy):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    report = api.heal_all(proj, everywhere=True, presumed_dead_before=NOW)
    assert report.status == "started"
    assert any("presumed_dead_before" in n for n in report.notes)
    assert "--presumed-dead-before" not in spy.calls[0][0]


# ---- heal_all: what the child is told ---------------------------------------


def test_the_child_argv_carries_project_sessions_force_and_the_epoch(tmp_path, spy):
    proj = _project(tmp_path)
    t = tmp_path / "S-kill.jsonl"
    t.write_text("{}\n")
    _append(f"{_stamp(300)} session-end: spawned serialize for S-kill "
            f"(reason: exit, project: {proj}) (transcript: {t})")
    _fail(tmp_path, "S-2", 100, proj)
    epoch = time.time() - 60
    report = api.heal_all(proj, sessions=["S-kill", "S-2"], force=True,
                          presumed_dead_before=epoch)
    assert report.status == "started"
    argv = spy.calls[0][0]
    assert argv[:5] == [sys.executable, "-m", "daimon_briefing.cli", "heal",
                        "--all"]
    assert argv[argv.index("--project") + 1] == str(config.resolve_project_dir(proj))
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--session"] == [
        "S-kill", "S-2"]
    assert "--force" in argv
    assert float(argv[argv.index("--presumed-dead-before") + 1]) == epoch


def test_the_child_argv_is_minimal_when_nothing_narrows_it(tmp_path, spy):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    api.heal_all(proj, everywhere=True)
    argv = spy.calls[0][0]
    assert argv == [sys.executable, "-m", "daimon_briefing.cli", "heal", "--all"]


def test_the_child_env_has_the_effective_dirs_and_keeps_every_other_name(
        tmp_path, spy, monkeypatch):
    proj = _project(tmp_path)
    _fail(tmp_path, "S-1", 100, proj)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/somewhere/else")
    monkeypatch.setenv("KEEP_ME", "yes")
    api.heal_all(proj)
    env = spy.calls[0][1]
    assert env["DAIMON_CHECKPOINT_DIR"] == str(config.checkpoint_dir())
    assert env["DAIMON_LOG_DIR"] == str(config.log_dir())
    assert env["DAIMON_LLM_BACKEND"] == "command"
    assert env["DAIMON_PROJECT_DIR"] == "/somewhere/else"
    assert env["KEEP_ME"] == "yes"


# ---- end to end -------------------------------------------------------------


def _transcript(tmp_path, sid):
    rows = [{"type": "user" if i % 2 == 0 else "assistant",
             "message": {"role": "user" if i % 2 == 0 else "assistant",
                         "content": f"turn {i}"},
             "timestamp": f"2026-07-01T10:0{i}:00Z"} for i in range(3)]
    p = tmp_path / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


_CP = json.dumps({
    "session_id": "S-e2e",
    "working_context": {"active_topic": {"text": "t", "trust": "inferred"},
                        "open_questions": [], "recent_decisions": []},
    "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
})


def test_wait_true_heals_through_the_cli_and_a_second_call_has_nothing_to_do(
        tmp_path, fake_chat_factory, monkeypatch):
    """The child's argv is run through the real `daimon heal --all` parser and
    the real serializer; only the process boundary and the LLM are replaced."""
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    proj = _project(tmp_path)
    t = _transcript(tmp_path, "S-e2e")
    _append(f"{_stamp(100)} session-end: spawned serialize for S-e2e "
            f"(reason: exit, project: {proj}) (transcript: {t})",
            f"error: boom (transcript: {t}) after 3s")
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_CP))
    ran = []

    def launch(argv, env, *, wait=False):
        assert argv[1:4] == ["-m", "daimon_briefing.cli", "heal"]
        ran.append(argv)
        return cli.main(argv[3:])

    monkeypatch.setattr(heal, "launch", launch)

    first = api.heal_all(proj, wait=True)
    assert first.status == "completed"
    assert [(o.session_id, o.state) for o in first.outcomes] == [
        ("S-e2e", "succeeded")]
    second = api.heal_all(proj, wait=True)
    assert second.status == "nothing-to-heal"
    assert len(ran) == 1


def test_a_real_child_process_sees_the_callers_store(tmp_path):
    """No fakes: `heal.launch` starts `python -m daimon_briefing.cli heal --all`
    with the parent's env. The suite configures no LLM, so the child ends the
    session at its preflight; it writes that line and its retry marker into the
    CALLER's serialize.log, which only happens if the child read the log dir it
    was handed."""
    proj = _project(tmp_path)
    t = tmp_path / "S-real.jsonl"
    t.write_text(json.dumps({"type": "user", "message": {
        "role": "user", "content": "hi"}, "timestamp": "2026-07-01T10:00:00Z"})
        + "\n", encoding="utf-8")
    _append(f"{_stamp(100)} session-end: spawned serialize for S-real "
            f"(reason: exit, project: {proj}) (transcript: {t})",
            f"error: boom (transcript: {t}) after 3s")
    report = api.heal_all(proj, wait=True)
    assert report.status == "completed"
    log = _log_path().read_text(encoding="utf-8")
    assert "retry serialize for S-real" in log
    assert "no LLM API key" in log       # the child's own preflight line
    assert [(o.state, o.retried) for o in report.outcomes] == [("failed", True)]
    assert api.heal_all(proj).status == "nothing-to-heal"
