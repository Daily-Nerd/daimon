"""Serialize liveness for hosts (#1179 PR 2).

One canonical session key (`ledger._session_key`) at every boundary, a
heartbeat index and lease, a bounded log tail, and `api.serializes`: one row
per session joining heartbeats, the serialize.log fold and the checkpoint
directory. The state table these tests walk is section 3.1 of the host API
contract design."""

import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

import pytest

from daimon_briefing import ledger, serialize_liveness as liveness

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


# ---- D3: the heartbeat lease ------------------------------------------------


def test_lease_stamps_the_slug_and_clears_on_exit(log_dir):
    with ledger.heartbeat_lease("S-lease", "proj-a"):
        stamp = log_dir / "heartbeats" / "S-lease"
        assert stamp.read_text() == "proj-a"
    assert not stamp.exists()


def test_lease_clears_when_the_body_raises(log_dir):
    with pytest.raises(RuntimeError):
        with ledger.heartbeat_lease("S-boom", "proj-a"):
            raise RuntimeError("boom")
    assert not (log_dir / "heartbeats" / "S-boom").exists()


def test_lease_without_a_slug_leaves_empty_content(log_dir):
    with ledger.heartbeat_lease("S-noroot", None):
        assert (log_dir / "heartbeats" / "S-noroot").read_text() == ""


# ---- D5: a bounded read of the log tail -------------------------------------


def _log(log_dir, text):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text(text, encoding="utf-8")


def test_read_log_tail_of_a_missing_log_is_empty_and_whole(log_dir):
    assert liveness.read_log_tail() == ("", False)


def test_read_log_tail_returns_a_small_log_whole(log_dir):
    _log(log_dir, "one\ntwo\n")
    assert liveness.read_log_tail() == ("one\ntwo\n", False)


def test_read_log_tail_starts_at_the_next_line_and_says_it_cut(log_dir):
    _log(log_dir, "aaaa\nbbbb\ncccc\ndddd\n")
    text, cut = liveness.read_log_tail(12)
    assert cut is True
    assert text == "cccc\ndddd\n"


def test_read_log_tail_keeps_a_line_that_starts_exactly_at_the_window_edge(
        log_dir):
    _log(log_dir, "aaaa\nbbbb\ncccc\n")
    text, cut = liveness.read_log_tail(10)
    assert cut is True
    assert text == "bbbb\ncccc\n"


def test_read_log_tail_seeks_instead_of_reading_the_whole_file(
        log_dir, monkeypatch):
    _log(log_dir, "x" * 40 + "\n" + "tail line\n")
    monkeypatch.setattr(Path, "read_text",
                        lambda *a, **k: pytest.fail("read the whole log"))
    monkeypatch.setattr(Path, "read_bytes",
                        lambda *a, **k: pytest.fail("read the whole log"))
    text, cut = liveness.read_log_tail(16)
    assert (text, cut) == ("tail line\n", True)


def test_read_log_tail_is_tolerant_of_a_cut_inside_a_multibyte_char(log_dir):
    _log(log_dir, "café\nnext line\n")
    text, cut = liveness.read_log_tail(12)
    assert cut is True
    assert text == "next line\n"


def test_read_log_tail_of_one_giant_line_is_empty_not_a_fragment(log_dir):
    _log(log_dir, "y" * 100 + "\n")
    assert liveness.read_log_tail(20) == ("", True)


def test_read_log_tail_raises_oserror_when_the_log_is_not_a_file(log_dir):
    (log_dir / "serialize.log").mkdir(parents=True)
    with pytest.raises(OSError):
        liveness.read_log_tail()


# ---- 3.1: the state table, one case per row ---------------------------------

NOW = 1_800_000_000.0


def _core(text="", *, sessions=None, since=None, slug="proj-a", beats=None,
          cut=False, log_unreadable=False, hb_unreadable=False,
          admission=None, transcripts=(), checkpoint=None):
    """serialize_rows_core with every reader injected."""
    return liveness.serialize_rows_core(
        text, NOW, slug=slug, sessions=sessions, since=since,
        heartbeats=beats or {}, cut=cut, log_unreadable=log_unreadable,
        heartbeats_unreadable=hb_unreadable, ceiling=CEILING,
        transcript_exists=lambda p: p in transcripts,
        admission_state=admission or (lambda project: None),
        checkpoint_of=checkpoint or (lambda key: (False, None)))


def _row(result, sid="abc"):
    assert [r.session_id for r in result.rows] == [sid]
    return result.rows[0]


def _hb(age, slug=""):
    return ledger.Heartbeat(age, slug)


def _sp(sid, ago, **kw):
    return _spawn(sid, ago, now=NOW, **kw)


def test_row1_fresh_stamp_on_a_retried_session_is_repairing():
    text = _sp("abc", 300, retry=True)
    r = _row(_core(text, sessions=["abc"], beats={"abc": _hb(20)}))
    assert (r.state, r.reason, r.retried, r.heartbeat_age) == (
        "repairing", "heartbeat", True, 20)


def test_row2_fresh_stamp_is_running_even_over_an_older_result():
    text = _sp("abc", 900) + "\nerror: boom (transcript: /t/abc.jsonl) after 3s"
    r = _row(_core(text, sessions=["abc"], beats={"abc": _hb(5)}))
    assert (r.state, r.reason) == ("running", "heartbeat")


def test_row2_a_stamp_with_no_log_line_is_running():
    r = _row(_core("", sessions=["abc"], beats={"abc": _hb(5, "proj-a")}))
    assert (r.state, r.reason, r.project_slug, r.spawn_age) == (
        "running", "heartbeat", "proj-a", None)


def test_row3_success_result_is_succeeded():
    text = _sp("abc", 600) + "\nwrote checkpoint: /cp/abc.json (took 9s)"
    r = _row(_core(text, sessions=["abc"]))
    assert (r.state, r.reason, r.spawn_age) == ("succeeded", "result", 600)


def test_row4_skip_result_is_skipped():
    text = _sp("abc", 600) + "\nskipped serialize for abc: transcript too short"
    r = _row(_core(text, sessions=["abc"]))
    assert (r.state, r.reason) == ("skipped", "result")


def _err(sid="abc", path="/t/abc.jsonl"):
    return f"error: boom (session: {sid}) (transcript: {path}) after 3s"


def test_row5_a_refused_admission_still_held_is_held():
    text = "\n".join([
        _sp("abc", 600, transcript="/t/abc.jsonl"),
        "error: admission refused: events.jsonl is unreadable; fix it "
        "(session: abc) (transcript: /t/abc.jsonl) after 3s"])
    result = _core(text, sessions=["abc"], transcripts=("/t/abc.jsonl",),
                   admission=lambda project: ("unreadable", "repair the ledger"))
    r = _row(result)
    assert (r.state, r.reason) == ("held", "admission-refused")


def test_row5_a_refusal_whose_ledger_reads_again_is_an_ordinary_failure():
    text = "\n".join([
        _sp("abc", 600, transcript="/t/abc.jsonl"),
        "error: admission refused: events.jsonl is unreadable; fix it "
        "(session: abc) (transcript: /t/abc.jsonl) after 3s"])
    r = _row(_core(text, sessions=["abc"], transcripts=("/t/abc.jsonl",)))
    assert (r.state, r.reason) == ("failed", "healable")


@pytest.mark.parametrize("retry,exists,reason", [
    (False, True, "healable"),
    (True, True, "retry-exhausted"),
    (False, False, "unrecoverable"),
])
def test_row6_an_error_result_is_failed_with_the_classifier_class(
        retry, exists, reason):
    lines = [_sp("abc", 900, transcript="/t/abc.jsonl")]
    if retry:
        lines.append(_sp("abc", 800, retry=True, transcript="/t/abc.jsonl"))
    lines.append(_err())
    r = _row(_core("\n".join(lines), sessions=["abc"],
                   transcripts=("/t/abc.jsonl",) if exists else ()))
    assert (r.state, r.reason) == ("failed", reason)


def test_row6_a_covering_checkpoint_does_not_turn_a_logged_error_green():
    text = _sp("abc", 900, transcript="/t/abc.jsonl") + "\n" + _err()
    r = _row(_core(text, sessions=["abc"], transcripts=("/t/abc.jsonl",),
                   checkpoint=lambda key: (True, NOW)))
    assert r.state == "failed"


def test_row7_a_young_spawn_with_no_result_is_starting():
    r = _row(_core(_sp("abc", 60), sessions=["abc"]))
    assert (r.state, r.reason, r.spawn_age) == ("starting", "spawned", 60)


def test_row7_a_young_retry_with_no_stamp_is_repairing():
    r = _row(_core(_sp("abc", 60, retry=True), sessions=["abc"]))
    assert (r.state, r.reason, r.retried) == ("repairing", "spawned", True)


@pytest.mark.parametrize("exists,reason", [(True, "healable"), (False, "hung")])
def test_row8_a_spawn_past_the_ceiling_with_no_result_is_hung(exists, reason):
    text = _sp("abc", CEILING + 60, transcript="/t/abc.jsonl")
    r = _row(_core(text, sessions=["abc"],
                   transcripts=("/t/abc.jsonl",) if exists else ()))
    assert (r.state, r.reason) == ("hung", reason)


def test_row8_a_stale_stamp_does_not_keep_it_alive():
    text = _sp("abc", CEILING + 600)
    r = _row(_core(text, sessions=["abc"],
                   beats={"abc": _hb(CEILING + 100)}))
    assert r.state == "hung"
    assert r.heartbeat_age == CEILING + 100


def test_row9_a_checkpoint_at_or_after_since_is_succeeded():
    r = _row(_core("", sessions=["abc"], since=NOW - 100,
                   checkpoint=lambda key: (True, NOW - 100)))
    assert (r.state, r.reason) == ("succeeded", "checkpoint")


def test_row10_a_prior_checkpoint_and_no_log_line_is_unknown_without_since():
    r = _row(_core("", sessions=["abc"],
                   checkpoint=lambda key: (True, NOW - 5000)))
    assert (r.state, r.reason) == ("unknown", "checkpoint-without-run")


def test_row10_a_checkpoint_older_than_since_is_unknown():
    r = _row(_core("", sessions=["abc"], since=NOW - 100,
                   checkpoint=lambda key: (True, NOW - 5000)))
    assert (r.state, r.reason) == ("unknown", "checkpoint-without-run")


def test_row10_a_legacy_checkpoint_without_created_is_unknown():
    r = _row(_core("", sessions=["abc"], since=NOW - 100,
                   checkpoint=lambda key: (True, None)))
    assert (r.state, r.reason) == ("unknown", "checkpoint-without-run")


def test_row11_nothing_known_is_unknown_not_found():
    r = _row(_core("", sessions=["abc"]))
    assert (r.state, r.reason, r.heartbeat_age, r.spawn_age, r.retried,
            r.project_slug) == ("unknown", "not-found", None, None, False, None)


def test_row11_outside_the_window_when_the_log_was_cut():
    result = _core("", sessions=["abc"], cut=True)
    assert (_row(result).state, _row(result).reason) == (
        "unknown", "outside-window")
    assert len(result.notes) == 1
    assert "window" in result.notes[0]


def test_row11_an_unreadable_log_is_a_note_and_a_reason():
    result = _core("", sessions=["abc"], log_unreadable=True)
    assert _row(result).reason == "log-unreadable"
    assert len(result.notes) == 1


def test_an_unparsable_spawn_stamp_is_unknown_not_a_guess():
    text = ("not-a-stamp session-end: spawned serialize for abc "
            "(reason: exit, project: /p/A)")
    r = _row(_core(text, sessions=["abc"]))
    assert r.state == "unknown"


def test_a_checkpoint_beats_an_unreadable_log():
    r = _row(_core("", sessions=["abc"], since=NOW - 100, log_unreadable=True,
                   checkpoint=lambda key: (True, NOW)))
    assert (r.state, r.reason) == ("succeeded", "checkpoint")


# ---- which sessions come back -----------------------------------------------


def test_every_requested_id_comes_back_in_the_order_given_duplicates_included():
    text = "\n".join([_sp("b", 60), _sp("a", 120)])
    result = _core(text, sessions=["b", "zzz", "a", "b"])
    assert [r.session_id for r in result.rows] == ["b", "zzz", "a", "b"]
    assert [r.state for r in result.rows] == [
        "starting", "unknown", "starting", "starting"]


def test_a_codex_session_asked_by_bare_id_or_rollout_stem_is_the_same_row():
    text = _sp("abc", 120)
    beats = {"abc": _hb(10)}
    for ask in ("abc", ROLLOUT):
        r = _row(_core(text, sessions=[ask], beats=beats))
        assert (r.session_id, r.state) == ("abc", "running")
    both = _core(text, sessions=[ROLLOUT, "abc"], beats=beats)
    assert [r.session_id for r in both.rows] == ["abc", "abc"]
    assert both.rows[0] == both.rows[1]


def test_requested_sessions_are_returned_regardless_of_project():
    text = _sp("abc", 60, project="/somewhere/else")
    r = _row(_core(text, sessions=["abc"], slug="proj-a"))
    assert r.state == "starting"


def test_no_sessions_lists_this_projects_live_and_outstanding_newest_first(
        tmp_path):
    proj_a = tmp_path / "proj-a"
    proj_b = tmp_path / "proj-b"
    proj_a.mkdir()
    proj_b.mkdir()
    from daimon_briefing import store
    slug_a = store.project_slug(str(proj_a))
    text = "\n".join([
        _sp("old", 900, project=str(proj_a)),
        _sp("new", 30, project=str(proj_a)),
        _sp("done", 500, project=str(proj_a)),
        "wrote checkpoint: /cp/done.json (took 9s)",
        _sp("theirs", 40, project=str(proj_b)),
    ])
    result = _core(text, slug=slug_a)
    assert [(r.session_id, r.state) for r in result.rows] == [
        ("new", "starting"), ("old", "starting")]


def test_no_sessions_attributes_a_stamp_by_its_content():
    result = _core("", slug="proj-a", beats={
        "mine": _hb(5, "proj-a"), "theirs": _hb(5, "proj-b"),
        "nobody": _hb(5, "")})
    assert [r.session_id for r in result.rows] == ["mine"]


def test_no_sessions_and_no_slug_lists_nothing():
    assert _core(_sp("abc", 30), slug="").rows == ()
    assert _core(_sp("abc", 30), slug=None).rows == ()


def test_the_project_of_a_row_falls_back_to_the_spawn_cwd(tmp_path):
    from daimon_briefing import store
    proj = tmp_path / "proj"
    proj.mkdir()
    r = _row(_core(_sp("abc", 30, project=str(proj)), sessions=["abc"]))
    assert r.project_slug == store.project_slug(str(proj))


def test_a_stamp_attributes_over_the_spawn_cwd():
    r = _row(_core(_sp("abc", 30, project="/x/y"), sessions=["abc"],
                   beats={"abc": _hb(1, "from-stamp")}))
    assert r.project_slug == "from-stamp"


# ---- notes ------------------------------------------------------------------


def test_a_held_row_of_this_project_adds_the_admission_note(tmp_path):
    from daimon_briefing import store
    proj = tmp_path / "proj"
    proj.mkdir()
    slug = store.project_slug(str(proj))
    text = "\n".join([
        _sp("abc", 600, project=str(proj), transcript="/t/abc.jsonl"),
        "error: admission refused: events.jsonl is unreadable; fix it "
        "(session: abc) (transcript: /t/abc.jsonl) after 3s"])
    result = _core(text, slug=slug, transcripts=("/t/abc.jsonl",),
                   admission=lambda project: ("unreadable", "repair it"))
    assert [r.state for r in result.rows] == ["held"]
    assert len(result.notes) == 1
    assert "events.jsonl is unreadable" in result.notes[0]


def test_a_held_row_of_another_project_adds_no_note(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    text = "\n".join([
        _sp("abc", 600, project=str(proj), transcript="/t/abc.jsonl"),
        "error: admission refused: events.jsonl is unreadable; fix it "
        "(session: abc) (transcript: /t/abc.jsonl) after 3s"])
    result = _core(text, slug="someone-else", sessions=["abc"],
                   transcripts=("/t/abc.jsonl",),
                   admission=lambda project: ("unreadable", "repair it"))
    assert result.rows[0].state == "held"
    assert result.notes == ()


def test_unreadable_heartbeats_are_a_note():
    result = _core("", sessions=["abc"], hb_unreadable=True)
    assert len(result.notes) == 1
    assert "heartbeat" in result.notes[0]


# ---- serializes(): the live readers -----------------------------------------


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "work" / "proj"
    proj.mkdir(parents=True)
    return proj


def _slug(proj):
    from daimon_briefing import store
    return store.project_slug(str(proj))


def test_serializes_reads_the_log_the_stamps_and_the_clock_once(
        project, log_dir):
    now = time.time()
    _log(log_dir, _spawn("S-1", 60, host="session-end", project=str(project),
                         now=now) + "\n")
    _beat(log_dir, "S-1", age=5, content=_slug(project))
    got = liveness.serializes(str(project), now=now)
    assert [(r.session_id, r.state, r.reason) for r in got.rows] == [
        ("S-1", "running", "heartbeat")]
    assert got.notes == ()
    assert liveness.serialize_rows(str(project), now=now) == got.rows


def test_serializes_for_a_stamp_with_no_log_at_all(project, log_dir):
    _beat(log_dir, "S-2", age=3, content=_slug(project))
    got = liveness.serializes(sessions=["S-2", "S-3"], now=time.time())
    assert [(r.session_id, r.state) for r in got.rows] == [
        ("S-2", "running"), ("S-3", "unknown")]


def test_serializes_row9_reads_a_real_checkpoint_against_since(
        project, log_dir, cp_dir):
    now = time.time()
    cp_dir.mkdir(parents=True)
    (cp_dir / "S-9.json").write_text(json.dumps(
        {"session_id": "S-9", "created": _stamp(0, now)}))
    ok = liveness.serializes(sessions=["S-9"], since=now - 60, now=now)
    assert (ok.rows[0].state, ok.rows[0].reason) == ("succeeded", "checkpoint")
    late = liveness.serializes(sessions=["S-9"], since=now + 600, now=now)
    assert (late.rows[0].state, late.rows[0].reason) == (
        "unknown", "checkpoint-without-run")
    none = liveness.serializes(sessions=["S-9"], now=now)
    assert none.rows[0].reason == "checkpoint-without-run"


def test_serializes_row9_finds_a_codex_rollout_checkpoint_by_either_name(
        project, log_dir, cp_dir):
    now = time.time()
    cp_dir.mkdir(parents=True)
    (cp_dir / f"{ROLLOUT}.json").write_text(json.dumps(
        {"session_id": "abc", "created": _stamp(0, now)}))
    for ask in ("abc", ROLLOUT):
        got = liveness.serializes(sessions=[ask], since=now - 60, now=now)
        assert (got.rows[0].session_id, got.rows[0].state) == (
            "abc", "succeeded")


def test_serializes_codex_fixture_with_a_rollout_stamp_reads_running_both_ways(
        project, log_dir):
    now = time.time()
    _log(log_dir, _spawn("abc", CEILING + 600, project=str(project),
                         now=now) + "\n")
    _beat(log_dir, ROLLOUT, age=12, content=_slug(project))
    for ask in ("abc", ROLLOUT):
        got = liveness.serializes(sessions=[ask], now=now)
        assert [(r.session_id, r.state) for r in got.rows] == [
            ("abc", "running")]
    assert ledger._compute_outstanding(
        (log_dir / "serialize.log").read_text(), now) == []


def test_serializes_a_log_over_the_window_reads_by_seek_and_flags_old_ids(
        project, log_dir, monkeypatch):
    now = time.time()
    old = _spawn("S-old", 500, host="session-end", project=str(project),
                 now=now) + "\n"
    filler = ("x" * 99 + "\n") * 45_000        # ~4.5 MB of noise
    recent = _spawn("S-new", 30, host="session-end", project=str(project),
                    now=now) + "\n"
    _log(log_dir, old + filler + recent)
    assert (log_dir / "serialize.log").stat().st_size > 4 * 1024 * 1024
    real_read_text = Path.read_text

    def guarded(self, *a, **k):
        if self.name == "serialize.log":
            pytest.fail("read the whole log")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", guarded)
    got = liveness.serializes(sessions=["S-old", "S-new"], now=now)
    assert [(r.session_id, r.state, r.reason) for r in got.rows] == [
        ("S-old", "unknown", "outside-window"),
        ("S-new", "starting", "spawned")]
    assert len(got.notes) == 1


def test_serializes_an_unreadable_log_becomes_a_note(project, log_dir):
    (log_dir / "serialize.log").mkdir(parents=True)
    got = liveness.serializes(sessions=["S-1"], now=time.time())
    assert got.rows[0].reason == "log-unreadable"
    assert len(got.notes) == 1


def test_serializes_an_unreadable_heartbeat_dir_becomes_a_note(project, log_dir):
    log_dir.mkdir(parents=True)
    (log_dir / "heartbeats").write_text("not a dir")
    got = liveness.serializes(sessions=["S-1"], now=time.time())
    assert got.rows[0].state == "unknown"
    assert len(got.notes) == 1
    assert "heartbeat" in got.notes[0]


def test_serializes_a_read_leaves_the_heartbeat_dir_byte_identical(
        project, log_dir):
    _beat(log_dir, "S-1", age=7, content=_slug(project))
    _beat(log_dir, "S-ancient", age=9 * 86400, content="")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in (log_dir / "heartbeats").iterdir()}
    liveness.serializes(str(project), now=time.time())
    liveness.serializes(sessions=["S-1", "S-ancient"], now=time.time())
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in (log_dir / "heartbeats").iterdir()}
    assert after == before


def test_serializes_without_now_reads_the_clock_once(project, log_dir,
                                                     monkeypatch):
    calls = []
    real = time.time
    monkeypatch.setattr(liveness.time, "time", lambda: (calls.append(1), real())[1])
    liveness.serializes(sessions=["S-1"])
    assert len(calls) == 1


def test_serializes_with_no_project_and_no_sessions_is_empty(log_dir):
    # Like `requests.join`: project_dir=None names no project, so only an
    # explicit `sessions` list can be answered.
    _beat(log_dir, "S-1", age=1, content="some-slug")
    assert liveness.serializes(now=time.time()).rows == ()
    assert liveness.serializes(sessions=["S-1"], now=time.time()).rows[0].state \
        == "running"


def test_classifier_survives_an_unlistable_heartbeat_dir(log_dir):
    now = time.time()
    log_dir.mkdir(parents=True)
    (log_dir / "heartbeats").write_text("not a dir")
    text = _spawn("abc", CEILING + 600, now=now)
    assert len(ledger._compute_outstanding(text, now)) == 1
