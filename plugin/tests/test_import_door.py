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


# ---- carry never runs backward -----------------------------------------------
#
# An import of an old transcript lands behind the stream's live latest. The
# pointer guard (#123) already keeps it off `latest`; the same rule now keeps
# carry from folding that newer latest into it, so an older checkpoint never
# carries items born after it and never corroborates from them. The rule is
# owned by store and read by both, so it holds for every door, not just the
# import: a heal of an old session and the orphan sweep get the same answer.

from daimon_briefing import store, transcript  # noqa: E402
from daimon_briefing.surfaces import Writer  # noqa: E402

CARRY_PROJECT = "/p/carry-order"


def _cp(sid, created, text="an open loop"):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {
            "open_questions": [{"text": text, "trust": "inferred", "importance": 7}]
        },
        "epistemic_snapshot": {},
    }


def _spy_emitters(monkeypatch):
    calls = []
    monkeypatch.setattr(
        capture,
        "_emit_supersede_candidates",
        lambda *a, **k: calls.append("supersede") or 0,
    )
    monkeypatch.setattr(
        capture,
        "_emit_corroborations",
        lambda *a, **k: calls.append("corroborate") or 0,
    )
    return calls


def _carried(cp):
    return [q for q in cp["working_context"]["open_questions"] if q.get("carried_from")]


def test_an_older_capture_folds_nothing_backward_and_emits_nothing(
    tmp_checkpoint_dir, monkeypatch
):
    store.write_checkpoint(
        "S-live",
        _cp("S-live", "2026-10-20T10:00:00Z"),
        project_dir=CARRY_PROJECT,
        writer=Writer.HUMAN,
    )
    calls = _spy_emitters(monkeypatch)
    older = capture.carry_forward(
        _cp("S-old", "2026-10-01T10:00:00Z", "an older loop"), CARRY_PROJECT
    )
    assert _carried(older) == []
    assert calls == []


def test_a_newer_capture_still_carries(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint(
        "S-live",
        _cp("S-live", "2026-10-20T10:00:00Z"),
        project_dir=CARRY_PROJECT,
        writer=Writer.HUMAN,
    )
    calls = _spy_emitters(monkeypatch)
    newer = capture.carry_forward(
        _cp("S-next", "2026-10-21T10:00:00Z", "a newer loop"), CARRY_PROJECT
    )
    assert [q["carried_from"] for q in _carried(newer)] == ["S-live"]
    assert calls == ["supersede", "corroborate"]


def test_the_carry_rule_is_the_pointer_guard_rule(tmp_checkpoint_dir):
    """One predicate, two readers: the answer carry gets for a prev body is
    the answer the pointer guard gives for the pointer holding that body."""
    pdir = tmp_checkpoint_dir / store.project_slug(CARRY_PROJECT)
    cases = [
        (_cp("P", "2026-10-20T10:00:00Z"), _cp("N", "2026-10-01T10:00:00Z"), True),
        (_cp("P", "2026-10-20T10:00:00Z"), _cp("N", "2026-10-21T10:00:00Z"), False),
        (_cp("P", "2026-10-20T10:00:00Z"), _cp("N", "2026-10-20T10:00:00Z"), False),
        ({**_cp("P", ""), "created": None}, _cp("N", "2026-10-01T10:00:00Z"), False),
        (_cp("P", "2026-10-20T10:00:00Z"), {**_cp("N", ""), "created": None}, False),
    ]
    for prev, incoming, expected in cases:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "latest.json").write_text(json.dumps(prev), encoding="utf-8")
        epoch = store._created_epoch(incoming.get("created"))
        assert store._pointer_regresses(pdir, epoch) is expected, (prev, incoming)
        assert store.regresses_latest(prev, incoming) is expected, (prev, incoming)
    assert store.regresses_latest(None, _cp("N", "2026-10-01T10:00:00Z")) is False


_OLD_STAMPS = ["2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z"]


def _old_transcript(tmp_path, sid="S-older"):
    rows = []
    for i, ts in enumerate(_OLD_STAMPS):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append(
            {
                "type": role,
                "message": {"role": role, "content": f"turn {i}"},
                "timestamp": ts,
            }
        )
    p = tmp_path / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (1782000000, 1782000000))
    return p


@pytest.mark.parametrize("door", [None, "import"])
def test_a_capture_behind_the_live_latest_keeps_latest_and_carries_nothing(
    tmp_path, tmp_checkpoint_dir, log_dir, fake_chat_factory, monkeypatch, door
):
    """End to end through `_run_serialize`, for the import door and a live
    door alike: the per-session file lands, `latest` stays with the newer
    session, and the older checkpoint holds none of the newer one's items."""
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    store.write_checkpoint(
        "S-live",
        _cp("S-live", "2026-10-20T10:00:00Z", "live loop"),
        project_dir=CARRY_PROJECT,
        writer=Writer.HUMAN,
    )
    tpath = _old_transcript(tmp_path)
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-older")))
    assert cli._run_serialize(tpath, CARRY_PROJECT, door=door) == 0
    written = store.read_checkpoint("S-older")
    assert written is not None
    assert written["created"] == _OLD_STAMPS[-1]
    assert transcript.last_timestamp(tpath) == _OLD_STAMPS[-1]
    assert _carried(written) == []
    assert (
        store.read_latest_body(
            project_dir=CARRY_PROJECT, route=store.Route.OWN, admit=store.Admit.ANY
        )["session_id"]
        == "S-live"
    )
    events = tmp_checkpoint_dir / store.project_slug(CARRY_PROJECT) / "events.jsonl"
    assert not events.exists() or "corroborat" not in events.read_text(encoding="utf-8")


# ---- round 2: the reason belongs to the attempt, the birth stamp to the session


def _codex_spawn(now, sid, transcript, seconds_ago):
    """A Codex spawn line: `(project: ...)` and no reason group, like the Kimi
    and Windsurf adapters write."""
    stamp = datetime.fromtimestamp(now - seconds_ago, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return (
        f"{stamp} codex-stop: spawned serialize for {sid} "
        f"(project: {PROJECT}) (transcript: {transcript})"
    )


def test_a_reasonless_live_failure_after_a_failed_import_is_healable(tmp_path, log_dir):
    now = time.time()
    t = _transcript(tmp_path)
    text = (
        "\n".join(
            [
                _aged(_marker(log_dir, SID, t), 900, now),
                f"error: LLM HTTP 429 (transcript: {t}) after 3s",
                _codex_spawn(now, SID, t, 120),
                f"error: boom (transcript: {t}) after 3s",
            ]
        )
        + "\n"
    )
    assert ledger._session_ledger(text, now)[SID]["reason"] is None
    assert [x.session_id for x in heal.plan_all(text, now).targets] == [SID]


def test_a_reasonless_live_spawn_before_the_tail_still_owns_the_attempt(
    tmp_path, log_dir
):
    """Both spawns scrolled out of the tail, both errors inside it: the
    long-window pass replays the lines in order, so the later reasonless
    spawn overwrites the import's reason. The attempt is unrecoverable only
    because its spawn is outside the tail, the pre-existing #1213 reading,
    never because it reads as an import."""
    now = time.time()
    t = _transcript(tmp_path)
    filler = [f"note for S-other-{i}: compact-summary-only-context" for i in range(250)]
    text = (
        "\n".join(
            [
                _aged(_marker(log_dir, SID, t), 900, now),
                _codex_spawn(now, SID, t, 800),
                *filler,
                f"error: LLM HTTP 429 (transcript: {t}) after 3s",
                f"error: boom (transcript: {t}) after 3s",
            ]
        )
        + "\n"
    )
    e = ledger._session_ledger(text, now)[SID]
    assert e["reason"] is None and e["project"] == PROJECT
    out = ledger._compute_outstanding(text, now)
    assert [f["class"] for f in out] == ["unrecoverable"]
    assert [s.reason for s in heal.plan_all(text, now).skipped] == ["unrecoverable"]


def test_a_hung_reasonless_live_spawn_after_an_import_is_healable(
    tmp_path, log_dir, monkeypatch
):
    monkeypatch.setenv("DAIMON_HUNG_AFTER", "100")
    now = time.time()
    t = _transcript(tmp_path)
    text = (
        "\n".join(
            [
                _aged(_marker(log_dir, SID, t), 900, now),
                "wrote checkpoint: /c/{SID}.json (took 4s) [import]".replace(
                    "{SID}", SID
                ),
                _codex_spawn(now, SID, t, 500),
            ]
        )
        + "\n"
    )
    out = ledger._compute_outstanding(text, now)
    assert [(f["kind"], f["class"]) for f in out] == [("hung", "healable")]
    assert [x.session_id for x in heal.plan_all(text, now).targets] == [SID]


def test_a_held_import_admission_names_import_as_its_cure(tmp_path, log_dir):
    from daimon_briefing import jsonl

    now = time.time()
    t = _transcript(tmp_path)
    text = (
        "\n".join(
            [
                _aged(_marker(log_dir, SID, t), 300, now),
                f"{jsonl.ADMISSION_PREFIX}events.jsonl is unreadable; fix it "
                f"(session: {SID}) (transcript: {t}) after 0s",
            ]
        )
        + "\n"
    )
    held = ledger._outstanding_failures(
        ledger._session_ledger(text, now),
        now,
        lambda sid: False,
        1800,
        lambda p: True,
        admission_state=lambda p: ("unreadable", "fix it"),
    )
    assert [(f["class"], f["reason"]) for f in held] == [
        ("admission-refused", "import")
    ]
    lines = "\n".join(render._outstanding_lines(held))
    assert "then re-run `daimon import`" in lines
    assert "daimon heal" not in lines
    # A live refusal keeps heal as its cure.
    live = (
        "\n".join(
            [
                _codex_spawn(now, "S-live-2", t, 300),
                f"{jsonl.ADMISSION_PREFIX}events.jsonl is unreadable; fix it "
                f"(session: S-live-2) (transcript: {t}) after 0s",
            ]
        )
        + "\n"
    )
    held = ledger._outstanding_failures(
        ledger._session_ledger(live, now),
        now,
        lambda sid: False,
        1800,
        lambda p: True,
        admission_state=lambda p: ("unreadable", "fix it"),
    )
    assert "then `daimon heal`" in "\n".join(render._outstanding_lines(held))

