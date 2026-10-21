"""`daimon heal --all`: the serial runner, its lock and its exit codes (#1179 PR 3).

The loop lives in cli/serialize.py and reaches the plan, the retry marker and
the serialize through the cli namespace (`_cli._heal_plan_all`,
`_cli._append_retry_log`, `_cli._run_serialize`), so each step is fakeable the
way the single heal's are. The fakes here write the SAME ledger lines the real
steps do, so the loop's re-plan reads a log that moved. Design D7 and D12."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from daimon_briefing import cli, config, heal, ledger


def _args(**kw):
    base = dict(dry_run=False, force=False, all=True, project=None, session=None,
                presumed_dead_before=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _stamp(seconds_ago):
    return datetime.fromtimestamp(time.time() - seconds_ago, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _log_path():
    p = config.log_dir() / "serialize.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append(*lines):
    with _log_path().open("a", encoding="utf-8") as f:
        for ln in lines:
            f.write(ln + "\n")


def _read():
    return _log_path().read_text(encoding="utf-8")


def _fail(tmp_path, sid, seconds_ago, project="/p/A"):
    t = tmp_path / f"{sid}.jsonl"
    t.write_text("{}\n")
    _append(f"{_stamp(seconds_ago + 5)} session-end: spawned serialize for {sid} "
            f"(reason: exit, project: {project}) (transcript: {t})",
            f"error: boom (transcript: {t}) after 3s")
    return t


def _ok_line(sid):
    return f"wrote checkpoint: {config.checkpoint_dir()}/{sid}.json (took 1s)"


def _err_line(path):
    return f"error: still broken (transcript: {path}) after 2s"


class Steps:
    """Fake marker and serialize steps that move the real log, and record."""

    def __init__(self):
        self.calls = []
        self.rc = 0
        self.outcome = "ok"      # "ok" | "error"
        self.on_run = None       # hook run before the result line is written

    def retry(self, sid, prior, project, transcript):
        self.calls.append(("marker", sid))
        ledger._append_retry_log(sid, prior, project, transcript)

    def run(self, path, project, escalate=False, session=None):
        sid = path.stem
        self.calls.append(("run", sid))
        if self.on_run:
            self.on_run(sid)
        if self.outcome == "ok":
            _append(_ok_line(sid))
        else:
            _append(_err_line(path))
        return self.rc


@pytest.fixture
def steps(monkeypatch):
    s = Steps()
    monkeypatch.setattr(cli, "_append_retry_log", s.retry)
    monkeypatch.setattr(cli, "_run_serialize", s.run)
    return s


# ---- the loop ----------------------------------------------------------------


def test_heal_all_heals_every_target_newest_first(tmp_path, steps, capsys):
    _fail(tmp_path, "S-old", 3000)
    _fail(tmp_path, "S-new", 100)
    _fail(tmp_path, "S-mid", 900)
    assert cli._cmd_heal(_args()) == 0
    assert [c for c in steps.calls if c[0] == "run"] == [
        ("run", "S-new"), ("run", "S-mid"), ("run", "S-old")]
    out = capsys.readouterr().out
    assert "S-new" in out and "S-mid" in out and "S-old" in out
    assert "3 attempted, 0 failed" in out


def test_the_retry_marker_is_written_just_before_each_run(tmp_path, steps):
    # A marker is a spawn line that resets the result: written up front for a
    # queued target it would turn it into `repairing`, then `hung` (D7).
    _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 900)
    seen = {}
    steps.on_run = lambda sid: seen.setdefault(
        sid, "retry serialize for S-2" in _read())
    cli._cmd_heal(_args())
    assert steps.calls == [("marker", "S-1"), ("run", "S-1"),
                           ("marker", "S-2"), ("run", "S-2")]
    assert seen["S-1"] is False  # the queued target had no marker yet


def test_the_loop_replans_and_skips_a_target_healed_meanwhile(tmp_path, steps):
    _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 900)
    steps.on_run = lambda sid: _append(_ok_line("S-2")) if sid == "S-1" else None
    assert cli._cmd_heal(_args()) == 0
    assert [c for c in steps.calls if c[0] == "run"] == [("run", "S-1")]


def test_a_forced_target_runs_once_per_run(tmp_path, steps):
    # force ignores the retry marker, so a session that keeps failing would be
    # healable again after every run without the attempted set.
    _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 900)
    steps.outcome = "error"
    steps.rc = 1
    assert cli._cmd_heal(_args(force=True)) == 1
    runs = [c[1] for c in steps.calls if c[0] == "run"]
    assert sorted(runs) == ["S-1", "S-2"]


def test_a_vanished_transcript_is_noted_and_the_loop_moves_on(
        tmp_path, steps, capsys, monkeypatch):
    t1 = _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 900)
    real = heal.plan_all
    first = {"done": False}

    def plan_then_lose_the_transcript(*a, **k):
        plan = real(*a, **k)
        if not first["done"]:
            first["done"] = True
            t1.unlink()  # gone between the plan and the run
        return plan

    monkeypatch.setattr(cli, "_heal_plan_all", plan_then_lose_the_transcript)
    assert cli._cmd_heal(_args()) == 0
    assert [c[1] for c in steps.calls if c[0] == "run"] == ["S-2"]
    assert "transcript for S-1 vanished" in capsys.readouterr().out


def test_exit_one_when_an_attempted_target_ends_in_error(tmp_path, steps):
    _fail(tmp_path, "S-1", 100)
    steps.rc = 1
    steps.outcome = "error"
    assert cli._cmd_heal(_args()) == 1


def test_exit_zero_when_nothing_is_to_heal(steps, capsys):
    assert cli._cmd_heal(_args()) == 0
    assert "nothing to heal" in capsys.readouterr().out
    assert steps.calls == []


def test_exit_zero_when_every_attempted_target_skips(tmp_path, steps):
    _fail(tmp_path, "S-1", 100)
    assert cli._cmd_heal(_args()) == 0


# ---- scope ----------------------------------------------------------------


def test_project_flag_heals_only_that_project(tmp_path, steps):
    a = tmp_path / "projA"
    b = tmp_path / "projB"
    a.mkdir()
    b.mkdir()
    _fail(tmp_path, "S-a", 100, project=str(a))
    _fail(tmp_path, "S-b", 200, project=str(b))
    assert cli._cmd_heal(_args(project=str(a))) == 0
    assert [c[1] for c in steps.calls if c[0] == "run"] == ["S-a"]


def test_session_flag_heals_only_the_named_sessions(tmp_path, steps):
    _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 200)
    _fail(tmp_path, "S-3", 300)
    assert cli._cmd_heal(_args(session=["S-3", "S-1"])) == 0
    assert [c[1] for c in steps.calls if c[0] == "run"] == ["S-1", "S-3"]


def test_a_project_flag_alone_runs_the_loop(tmp_path, steps):
    a = tmp_path / "projA"
    a.mkdir()
    _fail(tmp_path, "S-1", 100, project=str(a))
    _fail(tmp_path, "S-2", 200, project=str(a))
    assert cli._cmd_heal(_args(all=False, project=str(a))) == 0
    assert [c[1] for c in steps.calls if c[0] == "run"] == ["S-1", "S-2"]


def test_presumed_dead_before_heals_a_killed_young_spawn(tmp_path, steps):
    a = tmp_path / "projA"
    a.mkdir()
    t = tmp_path / "S-1.jsonl"
    t.write_text("{}\n")
    _append(f"{_stamp(300)} session-end: spawned serialize for S-1 "
            f"(reason: exit, project: {a}) (transcript: {t})")
    assert cli._cmd_heal(_args(project=str(a))) == 0
    assert steps.calls == []  # young and unproven: alive until the ceiling
    assert cli._cmd_heal(_args(project=str(a),
                               presumed_dead_before=time.time() - 100)) == 0
    assert [c[1] for c in steps.calls if c[0] == "run"] == ["S-1"]


def test_presumed_dead_before_without_a_project_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["heal", "--all", "--presumed-dead-before", "100"])
    assert exc.value.code == 2
    assert "--presumed-dead-before requires --project" in capsys.readouterr().err


def test_heal_flags_reach_the_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "_cmd_heal", lambda a: seen.update(vars(a)) or 0)
    cli.main(["heal", "--all", "--project", "/p", "--session", "a", "--session",
              "b", "--presumed-dead-before", "12.5", "--force"])
    assert seen["all"] is True and seen["project"] == "/p"
    assert seen["session"] == ["a", "b"] and seen["presumed_dead_before"] == 12.5
    assert seen["force"] is True
    seen.clear()
    cli.main(["heal"])
    assert seen["all"] is False and seen["session"] is None


# ---- dry run ---------------------------------------------------------------


def test_dry_run_all_lists_every_target_and_runs_nothing(tmp_path, steps, capsys):
    _fail(tmp_path, "S-1", 100)
    _fail(tmp_path, "S-2", 900)
    assert cli._cmd_heal(_args(dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "would heal S-1" in out and "would heal S-2" in out
    assert steps.calls == []


# ---- the lock ---------------------------------------------------------------


def test_heal_all_while_the_lock_is_held_exits_zero(tmp_path, steps, capsys):
    _fail(tmp_path, "S-1", 100)
    with heal.try_lock() as got:
        assert got is True
        assert cli._cmd_heal(_args()) == 0
    assert "another heal is running" in capsys.readouterr().out
    assert steps.calls == []


def test_the_single_heal_takes_the_lock_too(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_heal_plan", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("planned while another heal held the lock")))
    with heal.try_lock():
        assert cli._cmd_heal(SimpleNamespace(dry_run=False, force=False)) == 0
    assert "another heal is running" in capsys.readouterr().out


def test_the_lock_is_free_after_a_heal(tmp_path, steps):
    _fail(tmp_path, "S-1", 100)
    cli._cmd_heal(_args())
    with heal.try_lock() as got:
        assert got is True


def test_the_single_heal_still_calls_the_three_argument_plan(monkeypatch):
    seen = {}

    def fake(text, now, force=False):
        seen["force"] = force
        return {"target": None, "skipped": [], "note": "nothing to heal"}

    monkeypatch.setattr(cli, "_heal_plan", fake)
    assert cli._cmd_heal(SimpleNamespace(dry_run=False, force=True)) == 0
    assert seen == {"force": True}


def test_an_unresolvable_project_never_widens_the_scope(monkeypatch, steps, capsys):
    # A falsy slug would read as "everywhere" to the plan; the runner refuses.
    monkeypatch.setattr(ledger, "_slug_of", lambda project: "")
    assert cli._cmd_heal(_args(project="/nowhere")) == 1
    assert "cannot resolve a project" in capsys.readouterr().err
    assert steps.calls == []


def test_presumed_dead_before_without_a_project_fails_without_a_parser(capsys):
    # A caller that builds its own args (no argparse parser attached) still
    # gets the same refusal and the same code.
    assert cli._cmd_heal(_args(presumed_dead_before=5.0)) == 2
    assert "requires --project" in capsys.readouterr().err
