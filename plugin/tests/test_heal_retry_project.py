"""A retried session keeps its project (#1213).

The project of a failed session used to live on one line only, the original
spawn line, and `_session_ledger` recovered it by a 200-line scan. In a busy
log the spawn scrolled out while the retry marker and the error line stayed
in, and heal routed the healed checkpoint to the global dir. Now every line
that opens an attempt carries the routing facts, the fold reads them over the
long window, and heal names a session whose project it cannot recover instead
of routing it to the global dir.
"""

import time

import pytest

from daimon_briefing import cli, heal, ledger, serialize_liveness
from daimon_briefing.cli import serialize as cli_serialize

SID = "044bfde5-4bba-4e63-924d-de621d07b5f1"
PROJECT = "/work/BrandMeV2"
FILLER = 230  # more than the 200-line tail the classifier folds


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR",
                       str(tmp_path / ".claude" / "projects"))


def _stamp(now, seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - seconds_ago))


def _transcript(tmp_path, name=None):
    t = tmp_path / (name or f"{SID}.jsonl")
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text("{}\n")
    return t


def _spawn(now, t, project=PROJECT, ago=5000):
    return (f"{_stamp(now, ago)} session-end: spawned serialize for {SID} "
            f"(reason: exit, project: {project}) (transcript: {t})")


def _filler(now, n=FILLER):
    return [f"{_stamp(now, 4000)} INFO daimon_briefing.serializer: filler {i}"
            for i in range(n)]


def _old_retry(now):
    """The marker as written before #1213: no project, no transcript."""
    return (f"{_stamp(now, 300)} session-start: retry serialize for {SID} "
            f"(prior: error: boom)")


def _error(t):
    return f"error: boom (transcript: {t}) after 9s"


def _busy_log(tmp_path, now):
    """The incident: spawn, 230 unrelated lines, a retry marker, the error."""
    t = _transcript(tmp_path)
    lines = [_spawn(now, t), *_filler(now), _old_retry(now), _error(t)]
    return "\n".join(lines), t


# ---- the fold ---------------------------------------------------------------

def test_project_survives_when_the_spawn_is_inside_the_tail(tmp_path):
    now = time.time()
    t = _transcript(tmp_path)
    text = "\n".join([_spawn(now, t), *_filler(now, 10), _old_retry(now), _error(t)])
    assert ledger._session_ledger(text, now)[SID]["project"] == PROJECT


def test_project_survives_when_the_spawn_scrolls_out_of_the_tail(tmp_path):
    now = time.time()
    text, _ = _busy_log(tmp_path, now)
    assert ledger._session_ledger(text, now)[SID]["project"] == PROJECT


def test_the_tail_still_decides_the_terminal_state(tmp_path):
    """A success inside the tail wins over an error the long window holds, and
    a session that only the long window knows is not folded at all."""
    now = time.time()
    t = _transcript(tmp_path)
    other = "other-session"
    text = "\n".join([
        _spawn(now, t),
        f"{_stamp(now, 4900)} session-end: spawned serialize for {other} "
        f"(reason: exit, project: /work/other) (transcript: /gone.jsonl)",
        *_filler(now),
        f"wrote checkpoint: /cp/{SID}.json (took 3s)",
    ])
    fold = ledger._session_ledger(text, now)
    assert fold[SID]["result_kind"] == "success"
    assert other not in fold


def test_a_hung_entry_reads_its_transcript_from_the_long_window(tmp_path):
    now = time.time()
    t = _transcript(tmp_path)
    text = "\n".join([_spawn(now, t), *_filler(now), _old_retry(now)])
    e = ledger._session_ledger(text, now)[SID]
    assert (e["project"], e["transcript"], e["result_kind"]) == (PROJECT, str(t), None)


def test_heal_plans_the_project_of_a_session_whose_spawn_left_the_tail(tmp_path):
    now = time.time()
    text, t = _busy_log(tmp_path, now)
    plan = ledger._heal_plan(text, now, force=True)
    assert plan["target"] is not None, plan
    assert plan["target"]["project"] == PROJECT
    assert plan["target"]["transcript"] == str(t)


def test_per_project_heal_keeps_a_session_whose_spawn_left_the_tail(tmp_path):
    now = time.time()
    text, _ = _busy_log(tmp_path, now)
    slug = ledger._slug_of(PROJECT)
    plan = heal.plan_all(text, now, force=True, slug=slug)
    assert [t.session_id for t in plan.targets] == [SID]
    assert plan.targets[0].project_slug == slug


# ---- the writer -------------------------------------------------------------

def test_the_retry_marker_carries_project_and_transcript(tmp_path):
    now = time.time()
    t = _transcript(tmp_path)
    ledger._append_retry_log(SID, "error: boom", PROJECT, str(t))
    text = (tmp_path / ".daimon" / "logs" / "serialize.log").read_text()
    e = ledger._session_ledger(text, now)[SID]
    assert (e["project"], e["transcript"]) == (PROJECT, str(t))
    assert e["retried"] is True and e["spawned"] is True
    assert f"session-start: retry serialize for {SID} " in text


def test_a_marker_for_an_unknown_project_reads_back_as_none(tmp_path):
    now = time.time()
    t = _transcript(tmp_path)
    ledger._append_retry_log(SID, "error: boom", None, str(t))
    text = (tmp_path / ".daimon" / "logs" / "serialize.log").read_text()
    e = ledger._session_ledger(text, now)[SID]
    assert (e["project"], e["transcript"]) == (None, str(t))


def test_a_marker_alone_routes_a_session_after_the_spawn_is_gone(tmp_path):
    """The marker is enough on its own: no spawn line anywhere in the log."""
    now = time.time()
    t = _transcript(tmp_path)
    ledger._append_retry_log(SID, "error: boom", PROJECT, str(t))
    marker = (tmp_path / ".daimon" / "logs" / "serialize.log").read_text().strip()
    text = "\n".join([*_filler(now), marker, _error(t)])
    e = ledger._session_ledger(text, now)[SID]
    assert e["project"] == PROJECT


def test_heal_target_writes_the_marker_with_project_and_transcript(tmp_path, monkeypatch):
    t = _transcript(tmp_path)
    seen = {}
    monkeypatch.setattr(cli, "_append_retry_log",
                        lambda *a: seen.update(args=a))
    monkeypatch.setattr(cli, "_run_serialize", lambda *a, **k: 0)
    cli_serialize._heal_target(SID, str(t), PROJECT, f"error: boom (transcript: {t}) after 9s")
    assert seen["args"] == (SID, "error: boom", PROJECT, str(t))


# ---- the named skip ---------------------------------------------------------

def _unknown_project_log(now, t):
    """A hook that could not tell its cwd logs `project: ?`."""
    return "\n".join([_spawn(now, t, project="?"), _error(t)])


def test_heal_skips_a_claude_transcript_whose_project_is_unknown(tmp_path):
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    plan = ledger._heal_plan(_unknown_project_log(now, t), now)
    assert plan["target"] is None
    assert [s["sid"] for s in plan["skipped"]] == [SID]
    assert plan["skipped"][0]["reason"] == ledger._HEAL_SKIP_REASON["project-unknown"]
    assert "project" in plan["skipped"][0]["reason"]


def test_the_named_skip_has_its_own_code_in_the_plan(tmp_path):
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    plan = heal.plan_all(_unknown_project_log(now, t), now)
    assert plan.targets == ()
    assert [(s.session_id, s.reason) for s in plan.skipped] == [(SID, "project-unknown")]


def test_force_does_not_route_an_unknown_project_to_the_global_dir(tmp_path):
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    plan = heal.plan_all(_unknown_project_log(now, t), now, force=True)
    assert plan.targets == ()


def test_a_hung_claude_session_with_an_unknown_project_is_skipped_too(tmp_path):
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    plan = heal.plan_all(_spawn(now, t, project="?"), now)
    assert plan.targets == ()
    assert [s.reason for s in plan.skipped] == ["project-unknown"]


def test_a_transcript_outside_a_project_dir_still_heals_to_the_global_dir(tmp_path):
    """No project is a legitimate answer for a host whose transcripts are not
    filed under a project (`_run_serialize`: None => global)."""
    now = time.time()
    t = _transcript(tmp_path, f"elsewhere/{SID}.jsonl")
    plan = heal.plan_all(_unknown_project_log(now, t), now)
    assert [(x.session_id, x.project) for x in plan.targets] == [(SID, None)]


def test_status_names_the_skip_and_does_not_send_the_reader_to_heal(tmp_path):
    from daimon_briefing import render
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    out = ledger._compute_outstanding(_unknown_project_log(now, t), now)
    assert out[0]["class"] == "project-unknown"
    text = "\n".join(render._outstanding_lines(out))
    assert SID in text and "project" in text
    assert "run `daimon heal`" not in text


def test_the_liveness_row_reports_the_named_reason(tmp_path):
    now = time.time()
    t = _transcript(tmp_path, f".claude/projects/-work-BrandMeV2/{SID}.jsonl")
    got = serialize_liveness.serialize_rows_core(
        _unknown_project_log(now, t), now, sessions=[SID], heartbeats={})
    assert [(r.state, r.reason) for r in got.rows] == [("failed", "project-unknown")]
