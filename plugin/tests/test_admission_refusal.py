"""Where a refused admission is raised, printed, logged and healed (#1132 PR
10b, D10.4): before the model is called, as an ordinary failed serialize.
"""

import json
import re

import pytest

from daimon_briefing import capture, cli, hooks, jsonl, ledger, serializer, store
from daimon_briefing.surfaces import Writer
from tests.conftest import make_messages

@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here; expose the path.
    return tmp_path / ".daimon" / "logs"


PROJECT = "/work/admission-proj"
GARBAGE = b"<<<<<<< conflict\n"
LINE = re.compile(
    r"^error: admission refused: events\.jsonl is unreadable; "
    r"run: daimon ledger repair events"
    r"(?: \(session: (?P<session>[^)]+)\))?"
    r" \(transcript: (?P<path>.+?)\) after (?P<secs>\d+)s$")


def _break(project=PROJECT, body=GARBAGE):
    path = store._events_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _transcript(tmp_path, name="S-adm.jsonl", n=24):
    path = tmp_path / name
    rows = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append(json.dumps({"type": role, "role": role,
                                "content": f"line {i} from {role}"}))
    path.write_text("\n".join(rows) + "\n")
    return path


class _Spy:
    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        raise AssertionError("the model must not be called")


# ---- capture.run: the preflight --------------------------------------------

def test_capture_refuses_before_any_model_call(tmp_checkpoint_dir):
    _break()
    spy = _Spy()
    with pytest.raises(jsonl.Refused) as caught:
        capture.run("S-1", make_messages(24), project=PROJECT, chat=spy,
                    deadline=None)
    assert spy.calls == 0
    assert caught.value.admission is True
    assert not list(tmp_checkpoint_dir.rglob("S-1.json"))


def test_a_too_short_session_is_a_skip_never_a_refusal(tmp_checkpoint_dir):
    _break()
    spy = _Spy()
    with pytest.raises(serializer.TooShortError):
        capture.run("S-1", make_messages(2), project=PROJECT, chat=spy,
                    deadline=None)
    assert spy.calls == 0


def test_a_proven_ledger_goes_on_to_the_model(tmp_checkpoint_dir):
    calls = []

    def chat(messages, **kw):
        calls.append(1)
        raise serializer.SerializeError("stop here")
    with pytest.raises(serializer.SerializeError):
        capture.run("S-1", make_messages(24), project=PROJECT, chat=chat,
                    deadline=None)
    assert calls


def test_the_too_short_message_is_one_text_for_both_gates():
    with pytest.raises(serializer.TooShortError) as a:
        serializer.require_enough_messages(make_messages(2))
    with pytest.raises(serializer.TooShortError) as b:
        serializer.serialize_strict("S", make_messages(2), chat=_Spy())
    assert str(a.value) == str(b.value)


# ---- _run_serialize: the ordinary error line --------------------------------

def test_run_serialize_prints_and_logs_the_exact_refusal_line(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch, capsys):
    _break()
    spy = _Spy()
    monkeypatch.setattr(cli, "_chat", spy)
    tp = _transcript(tmp_path)
    rc = cli._run_serialize(tp, PROJECT)
    assert rc == 1
    assert spy.calls == 0
    err = capsys.readouterr().err.strip()
    m = LINE.match(err)
    assert m and m["session"] is None and m["path"] == str(tp)
    logged = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()[-1]
    assert logged == err
    assert err.startswith(jsonl.ADMISSION_PREFIX)


def test_run_serialize_with_a_session_prints_the_group_before_the_transcript(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch, capsys):
    _break()
    monkeypatch.setattr(cli, "_chat", _Spy())
    tp = _transcript(tmp_path, "wire.jsonl")
    assert cli._run_serialize(tp, PROJECT, session="kimi-real") == 1
    err = capsys.readouterr().err.strip()
    m = LINE.match(err)
    assert m["session"] == "kimi-real" and m["path"] == str(tp)
    # And the fold keys it by the real session, not by `wire`.
    sessions = ledger._session_ledger(err, 0)
    assert list(sessions) == ["kimi-real"]


def test_run_serialize_a_too_short_session_logs_the_skip_not_a_refusal(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch, capsys):
    _break()
    monkeypatch.setattr(cli, "_chat", _Spy())
    tp = _transcript(tmp_path, "short.jsonl", n=1)
    assert cli._run_serialize(tp, PROJECT) == 0
    assert "skipped serialize for short" in capsys.readouterr().out
    assert "admission refused" not in (tmp_log_dir / "serialize.log").read_text()


def test_the_exact_line_round_trips_through_the_ledger_and_the_classifier(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch, capsys):
    _break()
    monkeypatch.setattr(cli, "_chat", _Spy())
    tp = _transcript(tmp_path)
    cli._run_serialize(tp, PROJECT)
    err = capsys.readouterr().err.strip()
    now = 1_900_000_000.0
    spawn = ("2030-03-17T17:46:30Z session-end: spawned serialize for S-adm "
             f"(reason: x, project: {PROJECT}) (transcript: {tp})")
    sessions = ledger._session_ledger(spawn + "\n" + err, now)
    assert sessions["S-adm"]["result_kind"] == "error"
    assert sessions["S-adm"]["transcript"] == str(tp)
    out = ledger._outstanding_failures(
        sessions, now, lambda sid: False, 1800, lambda p: True,
        admission_state=ledger.admission_state)
    assert [f["class"] for f in out] == ["admission-refused"]


# ---- heal -------------------------------------------------------------------

def test_heal_passes_session_only_when_the_id_is_not_the_stem(
        tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_append_retry_log", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "_run_serialize",
        lambda path, proj, escalate=False, session=None:
        seen.append(session) or 0)

    def plan(sid, name):
        tp = tmp_path / name
        tp.write_text("{}")
        return {"target": {"sid": sid, "transcript": str(tp), "project": "/p",
                           "age_str": "3m", "line": "error: x"},
                "skipped": [], "note": ""}

    class A:
        dry_run = False
        force = False
    for sid, name in [("S-claude", "S-claude.jsonl"),
                      ("kimi-real", "wire.jsonl"),
                      ("abc-123", "rollout-2026-10-08T10-11-12-abc-123.jsonl")]:
        monkeypatch.setattr(cli, "_heal_plan",
                            lambda text, now, force=False, p=plan(sid, name): p)
        assert cli._cmd_heal(A()) == 0
    assert seen == [None, "kimi-real", None]


def test_heal_drains_a_refusal_only_once_the_ledger_is_proven(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch, capsys):
    path = _break()
    tp = _transcript(tmp_path)
    monkeypatch.setattr(cli, "_chat", _Spy())
    cli._run_serialize(tp, PROJECT)
    capsys.readouterr()
    stamp = "2030-03-17T17:46:30Z"
    log = tmp_log_dir / "serialize.log"
    body = log.read_text()
    log.write_text(f"{stamp} session-end: spawned serialize for S-adm "
                   f"(reason: x, project: {PROJECT}) (transcript: {tp})\n"
                   + body)
    monkeypatch.setattr(ledger.time, "time", lambda: 1_900_000_000.0)

    retried = []
    serialized = []
    monkeypatch.setattr(cli, "_append_retry_log",
                        lambda sid, prior: retried.append(sid))
    monkeypatch.setattr(cli, "_run_serialize",
                        lambda p, proj, escalate=False, session=None:
                        serialized.append((str(p), proj, session)) or 0)
    # Unproven: heal holds it back, says why, writes no retry marker.
    assert cli.main(["heal"]) == 0
    out = capsys.readouterr().out
    assert "events.jsonl is unreadable" in out
    assert retried == [] and serialized == []
    # Proven again: the same heal takes it, with no session keyword (the id
    # is the stem).
    path.write_bytes(b"")
    assert cli.main(["heal"]) == 0
    assert retried == ["S-adm"]
    assert serialized == [(str(tp), PROJECT, None)]


# ---- Hermes: on_session_end -------------------------------------------------

def _hermes(monkeypatch, tmp_path, session="H-1", transcript=True):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setattr("daimon_briefing.transcript.from_session",
                        lambda sid: make_messages(24))
    spy = _Spy()
    monkeypatch.setattr(hooks, "_chat", spy)
    tp = None
    if transcript:
        tp = tmp_path / "host-artifact.jsonl"
        tp.write_text("{}\n")
    hooks.on_session_end(session_id=session, completed=True,
                         interrupted=False, model="m", platform="hermes",
                         transcript_path=str(tp) if tp else None)
    return spy, tp


def test_hermes_with_a_transcript_path_leaves_a_healable_refusal(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch):
    _break()
    spy, tp = _hermes(monkeypatch, tmp_path)
    assert spy.calls == 0
    lines = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()
    m = LINE.match(lines[-1])
    assert m and m["session"] == "H-1" and m["path"] == str(tp)
    sessions = ledger._session_ledger("\n".join(lines), 1_900_000_000.0)
    assert sessions["H-1"]["transcript"] == str(tp)
    assert sessions["H-1"]["spawned"] is True


def test_hermes_without_a_transcript_path_is_counted_not_healable(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch):
    _break()
    _hermes(monkeypatch, tmp_path, transcript=False)
    lines = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()
    m = LINE.match(lines[-1])
    assert m and m["path"] == "H-1" and m["session"] == "H-1"
    sessions = ledger._session_ledger("\n".join(lines), 1_900_000_000.0)
    out = ledger._outstanding_failures(
        sessions, 1_900_000_000.0, lambda sid: False, 1800,
        lambda p: bool(p) and __import__("pathlib").Path(p).exists(),
        admission_state=lambda project: None)
    assert [f["class"] for f in out] == ["unrecoverable"]


def test_an_ordinary_hermes_failure_keeps_todays_line(
        tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setattr("daimon_briefing.transcript.from_session",
                        lambda sid: make_messages(24))

    def chat(messages, **kw):
        raise serializer.SerializeError("model said no")
    monkeypatch.setattr(hooks, "_chat", chat)
    hooks.on_session_end(session_id="H-2", completed=True, interrupted=False,
                         model="m", platform="hermes")
    last = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()[-1]
    assert re.match(r"^error: LLM call failed .* \(session: H-2\) "
                    r"\(transcript: H-2\) after \d+s$", last)


def test_ledger_failure_keeps_a_host_path_whose_stem_is_not_the_session(
        tmp_log_dir, tmp_path):
    tp = tmp_path / "other-name.jsonl"
    tp.write_text("{}")
    hooks._ledger_failure("H-3", RuntimeError("boom"), 1, str(tp))
    last = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()
    assert f"(transcript: {tp})" in last[-1]
    assert "(session: H-3)" in last[-1]


def test_ledger_failure_without_an_existing_path_writes_the_session_id(
        tmp_log_dir, tmp_path):
    hooks._ledger_failure("H-4", RuntimeError("boom"), 1,
                          str(tmp_path / "gone.jsonl"))
    last = (tmp_log_dir / "serialize.log").read_text().strip().splitlines()[-1]
    assert "(session: H-4) (transcript: H-4)" in last


# ---- the stdin write-checkpoint path ----------------------------------------

def test_stdin_write_checkpoint_is_refused_with_rc_2_and_no_log_line(
        tmp_checkpoint_dir, tmp_log_dir, monkeypatch, capsys):
    import io
    _break()
    body = {
        "session_id": "S-in",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [{"text": "q", "trust": "inferred"}],
            "recent_decisions": [{"text": "d", "trust": "inferred"}]},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    rc = cli.main(["write-checkpoint", "--project", PROJECT,
                   "--session", "S-in"])
    assert rc == 2
    err = capsys.readouterr().err.strip()
    assert err == ("error: admission refused: events.jsonl is unreadable; "
                   "run: daimon ledger repair events")
    assert not (tmp_log_dir / "serialize.log").exists()
    assert store.read_checkpoint("S-in") is None


def test_the_stdin_path_writes_for_the_admission_class(tmp_checkpoint_dir):
    # The refused write is the only difference: a healthy ledger admits.
    cp = {"session_id": "S-ok",
          "working_context": {"active_topic": "t", "open_questions": [],
                              "recent_decisions": []},
          "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []}}
    assert store.write_checkpoint("S-ok", cp, project_dir=PROJECT,
                                  admit=True, writer=Writer.ADMISSION)
