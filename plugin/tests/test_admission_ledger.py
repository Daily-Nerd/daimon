"""A refused admission is an ordinary failed serialize (#1132 PR 10b, D10.4).

The refusal line parses in both log folds, heal is the only thing that drains
it, it is held back while the ledger is unproven, and it is never lost to the
200-line tail of `serialize.log`.
"""

import importlib.util
import re
import sys
import time
from pathlib import Path


from daimon_briefing import jsonl, ledger, store

NOW = time.time()
STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 120))
GARBAGE = b"<<<<<<< conflict\n"
HINT = "run: daimon ledger repair events"


def _hook_lib():
    path = Path(__file__).resolve().parents[2] / "hook" / "_daimon_hook_lib.py"
    spec = importlib.util.spec_from_file_location("_daimon_hook_lib_t", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _spawn(sid, transcript, project="/work/proj", host="session-end"):
    return (f"{STAMP} {host}: spawned serialize for {sid} "
            f"(reason: x, project: {project}) (transcript: {transcript})")


def _refusal(transcript, *, session=None, state="unreadable", hint=HINT,
             secs=0):
    group = f" (session: {session})" if session else ""
    return (f"error: admission refused: events.jsonl is {state}; {hint}"
            f"{group} (transcript: {transcript}) after {secs}s")


# ---- the exact line through both folds --------------------------------------

def test_the_admission_prefix_is_mirrored_in_the_hook_library():
    assert _hook_lib()._ADMISSION_PREFIX == jsonl.ADMISSION_PREFIX


def test_a_claude_refusal_is_keyed_by_the_transcript_stem():
    text = "\n".join([_spawn("s-1", "/t/s-1.jsonl"),
                      _refusal("/t/s-1.jsonl")])
    entry = ledger._session_ledger(text, NOW)["s-1"]
    assert entry["result_kind"] == "error"
    assert entry["transcript"] == "/t/s-1.jsonl"
    assert entry["result_line"].startswith(jsonl.ADMISSION_PREFIX)
    assert entry["spawned"] is True


def test_a_kimi_refusal_lands_on_the_real_session_not_on_wire():
    text = "\n".join([
        _spawn("kimi-real", "/k/kimi-real/wire.jsonl", host="kimi-stop"),
        _refusal("/k/kimi-real/wire.jsonl", session="kimi-real")])
    sessions = ledger._session_ledger(text, NOW)
    assert "wire" not in sessions
    assert sessions["kimi-real"]["result_kind"] == "error"
    assert sessions["kimi-real"]["transcript"] == "/k/kimi-real/wire.jsonl"


def test_a_codex_refusal_has_no_session_group_and_keys_by_the_rollout_stem():
    stem = "rollout-2026-10-08T10-11-12-abc-123"
    text = "\n".join([_spawn("abc-123", f"/c/{stem}.jsonl",
                             host="codex-stop"),
                      _refusal(f"/c/{stem}.jsonl")])
    sessions = ledger._session_ledger(text, NOW)
    assert list(sessions) == ["abc-123"]
    assert sessions["abc-123"]["result_kind"] == "error"


def test_the_session_group_before_the_transcript_group_leaves_the_path_whole():
    line = _refusal("/t/with space/s-1.jsonl", session="s-1")
    assert ledger._HEAL_TRANSCRIPT_RE.search(line).group(1) == (
        "/t/with space/s-1.jsonl")
    assert ledger._RESULT_ERR_RE.match(line)


def test_the_hook_library_fold_records_both_the_stem_and_the_group_key(
        tmp_path, monkeypatch):
    lib = _hook_lib()
    log = tmp_path / "serialize.log"
    log.write_text("\n".join([
        _spawn("kimi-real", "/k/kimi-real/wire.jsonl", host="kimi-stop"),
        _refusal("/k/kimi-real/wire.jsonl", session="kimi-real")]) + "\n")
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path)
    assert lib._failed_session_stems() == {"wire", "kimi-real"}


def test_the_hook_library_still_reads_an_ordinary_error_line(tmp_path,
                                                            monkeypatch):
    lib = _hook_lib()
    (tmp_path / "serialize.log").write_text(
        "error: boom (transcript: /t/s-9.jsonl) after 3s\n")
    monkeypatch.setattr(lib, "LOG_DIR", tmp_path)
    assert lib._failed_session_stems() == {"s-9"}


def test_a_pre_flight_error_without_a_transcript_is_still_dropped():
    assert ledger._session_ledger("error: no api key configured", NOW) == {}


# ---- the tail ---------------------------------------------------------------

def _noise(n):
    return [f"{STAMP} session-end: spawned serialize for n-{i} "
            f"(reason: x, project: /other) (transcript: /t/n-{i}.jsonl)"
            for i in range(n)]


def test_the_default_tail_is_the_last_200_lines():
    text = "\n".join([_spawn("old", "/t/old.jsonl"),
                      _refusal("/t/old.jsonl"), *_noise(250)])
    assert "old" not in ledger._session_ledger(text, NOW)


def test_tail_none_reaches_a_refusal_beyond_200_lines():
    text = "\n".join([_spawn("old", "/t/old.jsonl"),
                      _refusal("/t/old.jsonl"), *_noise(250)])
    assert ledger._session_ledger(text, NOW, tail=None)["old"][
        "result_kind"] == "error"


# ---- the classifier ---------------------------------------------------------

def _classify(text, *, state, transcript_exists=True, force=False, now=NOW):
    return ledger._outstanding_failures(
        ledger._session_ledger(text, now), now,
        has_checkpoint=lambda sid: False, ceiling=1800,
        transcript_exists=lambda p: transcript_exists, force=force,
        admission_state=lambda project: state)


def _line_pair(retried=False, transcript="/t/s-1.jsonl"):
    out = [_spawn("s-1", transcript)]
    if retried:
        out.append(f"{STAMP} session-start: retry serialize for s-1 "
                   f"(reason: heal, project: /work/proj) "
                   f"(transcript: {transcript})")
    out.append(_refusal(transcript))
    return "\n".join(out)


def _real_transcript(tmp_path):
    path = tmp_path / "s-1.jsonl"
    path.write_text("{}\n")
    return str(path)


def test_a_refusal_is_its_own_class_while_the_ledger_is_unproven():
    [f] = _classify(_line_pair(), state=("unreadable", HINT))
    assert f["class"] == "admission-refused"
    assert (f["state"], f["hint"]) == ("unreadable", HINT)
    assert f["sid"] == "s-1"


def test_once_the_ledger_is_proven_a_refusal_is_healable():
    [f] = _classify(_line_pair(), state=None)
    assert f["class"] == "healable"


def test_the_one_retry_gate_is_ignored_for_this_class_only():
    [f] = _classify(_line_pair(retried=True), state=None)
    assert f["class"] == "healable"
    ordinary = _line_pair(retried=True).replace(
        f"admission refused: events.jsonl is unreadable; {HINT}", "boom")
    [g] = _classify(ordinary, state=None)
    assert g["class"] == "retry-exhausted"


def test_a_refusal_with_its_transcript_gone_is_unrecoverable():
    [f] = _classify(_line_pair(), state=None, transcript_exists=False)
    assert f["class"] == "unrecoverable"


def test_a_refusal_with_no_spawn_line_is_unrecoverable():
    text = _refusal("/t/s-1.jsonl")
    [f] = _classify(text, state=None)
    assert f["class"] == "unrecoverable"


def test_the_default_seam_keeps_todays_behaviour_for_every_other_error():
    text = "\n".join([_spawn("s-2", "/t/s-2.jsonl"),
                      "error: boom (transcript: /t/s-2.jsonl) after 3s"])
    [f] = ledger._outstanding_failures(
        ledger._session_ledger(text, NOW), NOW, lambda sid: False, 1800,
        lambda p: True)
    assert f["class"] == "healable"


# ---- heal -------------------------------------------------------------------

def _seam(monkeypatch, state):
    monkeypatch.setattr(ledger, "admission_state", lambda project: state)


def test_heal_holds_back_an_unproven_refusal_and_names_the_hint(monkeypatch):
    _seam(monkeypatch, ("unreadable", HINT))
    plan = ledger._heal_plan(_line_pair(), NOW)
    assert plan["target"] is None
    [skipped] = plan["skipped"]
    assert skipped["sid"] == "s-1"
    assert "events.jsonl is unreadable" in skipped["reason"]
    assert HINT in skipped["reason"]


def test_heal_takes_a_refusal_once_the_ledger_is_proven(monkeypatch, tmp_path):
    _seam(monkeypatch, None)
    transcript = _real_transcript(tmp_path)
    plan = ledger._heal_plan(_line_pair(transcript=transcript), NOW)
    assert plan["target"]["sid"] == "s-1"
    assert plan["target"]["transcript"] == transcript


def test_a_refusal_beyond_the_200_line_tail_is_still_healable(
        monkeypatch, tmp_path):
    _seam(monkeypatch, None)
    text = "\n".join([_line_pair(transcript=_real_transcript(tmp_path)),
                      *_noise(300)])
    outstanding = ledger._compute_outstanding(text, NOW)
    classes = {f["sid"]: f["class"] for f in outstanding}
    assert classes["s-1"] == "healable"


def test_a_refusal_beyond_the_tail_is_counted_while_unproven(monkeypatch):
    _seam(monkeypatch, ("transient", "retry"))
    text = "\n".join([_line_pair(), *_noise(300)])
    classes = {f["sid"]: f["class"]
               for f in ledger._compute_outstanding(text, NOW)}
    assert classes["s-1"] == "admission-refused"


def test_the_admission_pass_reads_only_the_last_four_megabytes(monkeypatch):
    _seam(monkeypatch, None)
    big = "x" * 200 + "\n"
    filler = big * (ledger._ADMISSION_WINDOW_BYTES // len(big) + 10)
    text = "\n".join([_line_pair(), filler])
    sids = {f["sid"] for f in ledger._compute_outstanding(text, NOW)}
    assert "s-1" not in sids


def test_a_later_success_clears_a_refusal_in_the_long_window(monkeypatch):
    _seam(monkeypatch, None)
    ok = "wrote checkpoint: /cp/s-1.json (took 3s)"
    text = "\n".join([_line_pair(), *_noise(250), ok])
    assert [f for f in ledger._compute_outstanding(text, NOW)
            if f["sid"] == "s-1"] == []


def test_compute_outstanding_keeps_its_signature():
    import inspect
    assert list(inspect.signature(ledger._compute_outstanding).parameters
                ) == ["text", "now", "force"]


# ---- the seam against a real store ------------------------------------------

def test_the_default_seam_judges_the_projects_own_events_ledger(
        tmp_checkpoint_dir):
    project = "/work/seam-proj"
    assert ledger.admission_state(project) is None
    path = store._events_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)
    state, hint = ledger.admission_state(project)
    assert state == "unreadable"
    assert hint == HINT
    assert ledger.admission_state("?") is None
    assert ledger.admission_state(None) is None


# ---- the briefing note ------------------------------------------------------

def _store_log(text):
    from daimon_briefing import config
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text(text + "\n")


def test_admission_notes_name_the_count_and_the_hint_for_the_own_bucket(
        tmp_checkpoint_dir):
    project = "/work/note-proj"
    path = store._events_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)
    lines = []
    for i in (1, 2):
        lines += [_spawn(f"s-{i}", f"/t/s-{i}.jsonl", project=project),
                  _refusal(f"/t/s-{i}.jsonl")]
    _store_log("\n".join(lines))
    slug = store.project_slug(project)
    [note] = ledger.admission_notes(slug)
    assert note == ("⚠ 2 session(s) of this project not serialized: "
                    f"events.jsonl is unreadable; {HINT}, then daimon heal")
    assert ledger.admission_notes("another-slug") == ()


def test_admission_notes_are_silent_once_the_ledger_is_proven(
        tmp_checkpoint_dir):
    project = "/work/note-proj-2"
    _store_log("\n".join([_spawn("s-1", "/t/s-1.jsonl", project=project),
                          _refusal("/t/s-1.jsonl")]))
    assert ledger.admission_notes(store.project_slug(project)) == ()


def test_admission_notes_name_no_path_no_id_no_slug(tmp_checkpoint_dir):
    project = "/work/note-proj-3"
    path = store._events_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)
    _store_log("\n".join([_spawn("sess-xyz", "/t/sess-xyz.jsonl",
                                 project=project),
                          _refusal("/t/sess-xyz.jsonl")]))
    [note] = ledger.admission_notes(store.project_slug(project))
    assert not re.search(r"sess-xyz|/t/|note-proj", note)
