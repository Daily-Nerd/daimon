"""#1236 slice 1: the import door and its ledger.

`daimon import` (slice 2) will call `cli._run_serialize(..., door="import")`
once per transcript. This file pins what that door does and does not do:

- the success line ends with ` [import]`, beside the ` [fallback backend]`
  marker when a rescue produced the checkpoint, and every reader of the
  success line still counts it;
- a door the code does not know raises before any model call.
"""

import json

import pytest

from daimon_briefing import capture, cli, ledger, llm
from tests.conftest import FIXTURES, make_messages

PROJECT = "/p/import-door"


def _valid_json(session_id):
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [{"text": "q", "trust": "inferred"}],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


@pytest.fixture
def log_dir(tmp_path):
    # conftest's autouse isolation already points DAIMON_LOG_DIR here.
    return tmp_path / ".daimon" / "logs"


def _log_text(log_dir):
    return (log_dir / "serialize.log").read_text(encoding="utf-8")


def _success_lines(text):
    return [ln for ln in text.splitlines() if ln.startswith("wrote checkpoint: ")]


# ---- the success line marker ------------------------------------------------


def test_import_success_line_ends_with_the_marker_and_still_counts(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, capsys, monkeypatch
):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-imp")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT, door="import")
    assert rc == 0
    printed = [
        ln
        for ln in capsys.readouterr().out.splitlines()
        if ln.startswith("wrote checkpoint: ")
    ]
    logged = _success_lines(_log_text(log_dir))
    assert printed == logged, "printed and logged lines must stay byte-identical"
    assert logged[0].endswith(" (took 0s) [import]") or logged[0].endswith(
        "s) [import]"
    )
    # Every reader of the success line still reads it as one success.
    led = cli._session_ledger(_log_text(log_dir), now=0.0)
    assert led["sample_transcript"]["result_kind"] == "success"
    parsed = ledger._parse_serialize_log(log_dir / "serialize.log", now=0.0)
    assert parsed["result"]["outcome"] == "success"
    stats = ledger._stats_capture()
    assert stats["success"] == 1
    assert stats["fallback_serializes"] == 0
    assert ledger._is_result_line(logged[0])


def test_import_marker_coexists_with_the_fallback_marker(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, monkeypatch
):
    def chat_with_fallback(*a, **k):
        llm._fallback_used = True  # simulate llm.chat falling back mid-serialize
        return fake_chat_factory(_valid_json("S-fb"))(*a, **k)

    monkeypatch.setattr(cli, "_chat", chat_with_fallback)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT, door="import")
    assert rc == 0
    line = _success_lines(_log_text(log_dir))[0]
    assert line.endswith(" [fallback backend] [import]")
    led = cli._session_ledger(_log_text(log_dir), now=0.0)
    assert led["sample_transcript"]["result_kind"] == "success"
    stats = ledger._stats_capture()
    assert stats["success"] == 1
    assert stats["fallback_serializes"] == 1


def test_live_doors_write_no_marker(
    tmp_checkpoint_dir, log_dir, fake_chat_factory, monkeypatch
):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-live")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    assert cli._run_serialize(FIXTURES / "sample_transcript.md", PROJECT) == 0
    assert cli.main(["serialize", str(FIXTURES / "sample_transcript.md")]) == 0
    for line in _success_lines(_log_text(log_dir)):
        assert "[import]" not in line


# ---- the door is a closed set -----------------------------------------------


def test_capture_run_refuses_an_unknown_door(monkeypatch):
    calls = []
    monkeypatch.setattr(
        capture.serializer,
        "serialize_strict",
        lambda *a, **k: calls.append("model") or {},
    )
    with pytest.raises(ValueError, match="unknown capture door"):
        capture.run(
            "S-x",
            make_messages(20),
            project=PROJECT,
            chat=None,
            deadline=0.0,
            door="backfill",
        )
    assert calls == [], "an unknown door must refuse before any model call"


# ---- the import marker line --------------------------------------------------
#
# `daimon import` opens every attempt with `ledger.write_import_marker`, a
# timestamped spawn-style line the fold reads like a hook's spawn line. What
# sets it apart is its reason: heal never touches an attempt opened by an
# import, whatever happened to it, because resume belongs to `daimon import`.

import os  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

from daimon_briefing import heal, render, serialize_liveness  # noqa: E402

SID = "S-import-1"


def _marker(log_dir, sid, transcript, project=PROJECT):
    ledger.write_import_marker(sid, project, str(transcript))
    lines = _log_text(log_dir).splitlines()
    return lines[-1]


def _aged(line, seconds_ago, now):
    """The same marker line, re-stamped `seconds_ago` before `now`."""
    stamp = datetime.fromtimestamp(now - seconds_ago, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return stamp + line[len(stamp) :]


def _transcript(tmp_path, sid=SID):
    t = tmp_path / f"{sid}.jsonl"
    t.write_text("{}\n", encoding="utf-8")
    return t


def test_import_marker_round_trips_through_the_spawn_regex(tmp_path, log_dir):
    t = _transcript(tmp_path)
    line = _marker(log_dir, SID, t)
    assert line.split(" ", 1)[1] == (
        f"import: started serialize for {SID} "
        f"(reason: import, project: {PROJECT}) (transcript: {t})"
    )
    m = ledger._SPAWN_RE.match(line)
    assert m and m.group(2) == SID
    e = ledger._session_ledger(line + "\n", now=time.time())[SID]
    assert e["spawned"] is True
    assert e["reason"] == "import"
    assert e["project"] == PROJECT
    assert e["transcript"] == str(t)
    assert e["retried"] is False
    # The sibling readers: last-spawn status and the host census.
    parsed = ledger._parse_serialize_log(log_dir / "serialize.log", now=time.time())
    assert parsed["spawn"]["session_id"] == SID
    assert not ledger._STATS_HOST_RE.match(line), "an import is not a host"


def test_live_spawn_lines_carry_their_own_reason_and_windsurf_none():
    now = 0.0
    text = "\n".join(
        [
            "2026-07-03T23:00:00Z session-end: spawned serialize for A "
            "(reason: exit, project: /p/A) (transcript: /t/A.jsonl)",
            "2026-07-03T23:00:00Z session-start: retry serialize for B "
            "(reason: heal-retry, project: /p/A) (prior: error: x) (transcript: /t/B.jsonl)",
            "2026-07-03T23:00:00Z windsurf-cascade: spawned serialize for C "
            "(project: /p/A) (transcript: /t/C.md)",
        ]
    )
    led = ledger._session_ledger(text, now)
    assert led["A"]["reason"] == "exit"
    assert led["B"]["reason"] == "heal-retry"
    assert led["C"]["reason"] is None


def test_a_later_live_attempt_owns_its_own_reason(tmp_path, log_dir):
    """A session imported once and then resumed live: the new spawn line
    opens a new attempt with its own reason, so a live failure after an
    import is an ordinary healable failure."""
    now = time.time()
    t = _transcript(tmp_path)
    marker = _aged(_marker(log_dir, SID, t), 600, now)
    stamp = datetime.fromtimestamp(now - 60, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    text = (
        "\n".join(
            [
                marker,
                f"error: quota (transcript: {t}) after 2s",
                f"{stamp} session-end: spawned serialize for {SID} "
                f"(reason: exit, project: {PROJECT}) (transcript: {t})",
                f"error: boom (transcript: {t}) after 3s",
            ]
        )
        + "\n"
    )
    assert ledger._session_ledger(text, now)[SID]["reason"] == "exit"
    plan = heal.plan_all(text, now)
    assert [x.session_id for x in plan.targets] == [SID]


def test_the_reason_survives_the_fold_window_like_the_other_routing_facts(
    tmp_path, log_dir
):
    now = time.time()
    t = _transcript(tmp_path)
    marker = _aged(_marker(log_dir, SID, t), 900, now)
    filler = [f"note for S-other-{i}: compact-summary-only-context" for i in range(250)]
    text = (
        "\n".join([marker, *filler, f"error: quota (transcript: {t}) after 2s"]) + "\n"
    )
    e = ledger._session_ledger(text, now)[SID]
    assert e["reason"] == "import" and e["project"] == PROJECT
    # Outside the tail the spawn itself is not seen (#1213 keeps the facts,
    # not the attempt), so the class is the pre-existing `unrecoverable`;
    # either way there is no target.
    plan = heal.plan_all(text, now)
    assert plan.targets == ()
    assert [s.session_id for s in plan.skipped] == [SID]


# ---- heal never retries an import --------------------------------------------


def _failed_import(tmp_path, log_dir, now, seconds_ago=300):
    t = _transcript(tmp_path)
    marker = _aged(_marker(log_dir, SID, t), seconds_ago, now)
    return "\n".join([marker, f"error: LLM HTTP 429 (transcript: {t}) after 3s"]) + "\n"


def _failed_live(tmp_path, now, sid="S-live-1", seconds_ago=200):
    t = _transcript(tmp_path, sid)
    stamp = datetime.fromtimestamp(now - seconds_ago, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return (
        "\n".join(
            [
                f"{stamp} session-end: spawned serialize for {sid} "
                f"(reason: exit, project: {PROJECT}) (transcript: {t})",
                f"error: boom (transcript: {t}) after 3s",
            ]
        )
        + "\n"
    )


def test_a_failed_import_is_not_a_heal_target_even_with_force(tmp_path, log_dir):
    now = time.time()
    text = _failed_import(tmp_path, log_dir, now)
    for force in (False, True):
        plan = heal.plan_all(text, now, force=force)
        assert plan.targets == (), f"force={force}"
        assert [(s.session_id, s.reason) for s in plan.skipped] == [(SID, "import")]
        assert "daimon import" in plan.skipped[0].detail
        assert "heal" in plan.skipped[0].detail
    legacy = ledger._heal_plan(text, now)
    assert legacy["target"] is None
    assert legacy["skipped"][0]["reason"] == ledger._HEAL_SKIP_REASON["import"]


def test_a_live_failure_beside_an_import_stays_healable(tmp_path, log_dir):
    now = time.time()
    text = _failed_import(tmp_path, log_dir, now) + _failed_live(tmp_path, now)
    plan = heal.plan_all(text, now)
    assert [x.session_id for x in plan.targets] == ["S-live-1"]
    assert [(s.session_id, s.reason) for s in plan.skipped] == [(SID, "import")]


def test_a_hung_import_shows_in_status_and_is_not_healable(
    tmp_path, log_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_HUNG_AFTER", "100")
    now = time.time()
    t = _transcript(tmp_path)
    text = _aged(_marker(log_dir, SID, t), 500, now) + "\n"
    out = ledger._compute_outstanding(text, now)
    assert [(f["sid"], f["kind"], f["class"]) for f in out] == [(SID, "hung", "import")]
    plan = heal.plan_all(text, now)
    assert plan.targets == ()
    assert [(s.session_id, s.reason) for s in plan.skipped] == [(SID, "import")]
    assert heal.plan_all(text, now, force=True).targets == ()
    lines = "\n".join(render._outstanding_lines(out))
    assert SID in lines and "daimon import" in lines
    assert "run `daimon heal`" not in lines


def test_status_wording_for_a_failed_import_points_at_import_not_heal(
    tmp_path, log_dir
):
    now = time.time()
    out = ledger._compute_outstanding(_failed_import(tmp_path, log_dir, now), now)
    assert [(f["sid"], f["kind"], f["class"]) for f in out] == [
        (SID, "error", "import")
    ]
    lines = "\n".join(render._outstanding_lines(out))
    assert SID in lines and "daimon import" in lines
    assert "run `daimon heal`" not in lines
    assert "cause: LLM HTTP 429" in lines


def test_the_liveness_row_reports_the_import_class(tmp_path, log_dir):
    now = time.time()
    got = serialize_liveness.serialize_rows_core(
        _failed_import(tmp_path, log_dir, now), now, sessions=[SID], heartbeats={}
    )
    assert [(r.state, r.reason) for r in got.rows] == [("failed", "import")]


def test_import_marker_is_best_effort(tmp_path, monkeypatch):
    # An unwritable log dir loses the marker and nothing else, like the
    # retry marker.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(blocker))
    ledger.write_import_marker(SID, PROJECT, "/t/x.jsonl")
    assert os.path.isfile(blocker)
