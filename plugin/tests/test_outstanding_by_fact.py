"""A failed session leaves "outstanding" by a fact, never by its position in
`serialize.log`: a later success, a final skip, a covering checkpoint, or its
transcript no longer existing (class `lost`, derived on every read).

Both readers of the log, the ledger and the hook library, take the part of it
that is read from `serialize_log_window`, bounded in bytes from a line start.
"""

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

from daimon_briefing import cli, config, heal, ledger, serialize_log_window

NOW = time.time()
STAMP = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 300))
HOOK_DIR = Path(__file__).resolve().parents[2] / "hook"
USER_ROW = json.dumps({"type": "user", "message": {"role": "user",
                                                   "content": "hi"}}) + "\n"


def _spawn(sid, transcript, project="/work/proj", verb="spawned"):
    host = "session-start" if verb == "retry" else "session-end"
    return (f"{STAMP} {host}: {verb} serialize for {sid} "
            f"(reason: x, project: {project}) (transcript: {transcript})")


def _error(transcript, secs=3):
    return f"error: boom (transcript: {transcript}) after {secs}s"


def _noise(n, start=0):
    return [f"{STAMP} session-end: spawned serialize for n-{i} "
            f"(reason: x, project: /other) (transcript: /nope/n-{i}.jsonl)"
            for i in range(start, start + n)]


def _text(*parts):
    return "\n".join(parts) + "\n"


def _transcript(tmp_path, sid, *, exists=True):
    path = tmp_path / f"{sid}.jsonl"
    if exists:
        path.write_text(USER_ROW)
    return str(path)


def _classes(text, **kw):
    return {f["sid"]: f["class"]
            for f in ledger._compute_outstanding(text, NOW, **kw)}


def _hook_lib():
    sys.path.insert(0, str(HOOK_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "_daimon_hook_lib_by_fact", HOOK_DIR / "_daimon_hook_lib.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOK_DIR))


# ---- a failure does not leave by scrolling out of the log -------------------

def test_a_failure_far_beyond_200_lines_is_still_outstanding(tmp_path):
    t = _transcript(tmp_path, "s-old")
    text = _text(_spawn("s-old", t), _error(t), *_noise(600))
    assert _classes(text) == {"s-old": "healable"}


def test_a_later_success_still_clears_a_failure_beyond_200_lines(tmp_path):
    t = _transcript(tmp_path, "s-old")
    ok = f"wrote checkpoint: {tmp_path}/s-old.json (took 3s)"
    text = _text(_spawn("s-old", t), _error(t), *_noise(300), ok)
    assert _classes(text) == {}


def test_a_final_skip_still_clears_a_failure_beyond_200_lines(tmp_path):
    t = _transcript(tmp_path, "s-old")
    skip = "skipped serialize for s-old: too short"
    text = _text(_spawn("s-old", t), _error(t), *_noise(300), skip)
    assert _classes(text) == {}


def test_the_part_of_the_log_that_is_read_is_bounded_in_bytes(tmp_path):
    t = _transcript(tmp_path, "s-old")
    big = "x" * 200
    filler = (big + "\n") * (serialize_log_window.WINDOW_BYTES // 201 + 10)
    text = _text(_spawn("s-old", t), _error(t)) + filler
    assert _classes(text) == {}


# ---- lost: the transcript is gone, nothing is left to capture ---------------

def test_a_failure_whose_transcript_is_gone_is_lost_not_outstanding(tmp_path):
    t = _transcript(tmp_path, "s-gone", exists=False)
    text = _text(_spawn("s-gone", t), _error(t))
    assert _classes(text) == {}
    outstanding, lost = ledger._scan_failures(text, NOW)
    assert outstanding == []
    assert [(f["sid"], f["class"]) for f in lost] == [("s-gone", "lost")]


def test_a_retried_failure_whose_transcript_is_gone_is_lost(tmp_path):
    t = _transcript(tmp_path, "s-gone", exists=False)
    text = _text(_spawn("s-gone", t), _error(t),
                 _spawn("s-gone", t, verb="retry"), _error(t))
    _out, lost = ledger._scan_failures(text, NOW)
    assert [f["sid"] for f in lost] == ["s-gone"]


def test_a_hung_spawn_whose_transcript_is_gone_is_lost(tmp_path):
    t = _transcript(tmp_path, "s-gone", exists=False)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 86400))
    text = _text(f"{old} session-end: spawned serialize for s-gone "
                 f"(reason: x, project: /work/proj) (transcript: {t})")
    out, lost = ledger._scan_failures(text, NOW)
    assert out == []
    assert [f["sid"] for f in lost] == ["s-gone"]


def test_a_hung_spawn_with_no_transcript_path_keeps_its_class(tmp_path):
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 86400))
    text = _text(f"{old} session-end: spawned serialize for s-x")
    out, lost = ledger._scan_failures(text, NOW)
    assert [(f["sid"], f["class"]) for f in out] == [("s-x", "hung")]
    assert lost == []


def test_a_failure_with_a_transcript_on_disk_and_no_spawn_is_unrecoverable(
        tmp_path):
    t = _transcript(tmp_path, "s-nospawn")
    assert _classes(_text(_error(t))) == {"s-nospawn": "unrecoverable"}


def test_lost_is_derived_the_session_is_outstanding_when_the_file_returns(
        tmp_path):
    t = _transcript(tmp_path, "s-back", exists=False)
    text = _text(_spawn("s-back", t), _error(t))
    assert _classes(text) == {}
    Path(t).write_text(USER_ROW)
    assert _classes(text) == {"s-back": "healable"}


def test_a_lost_session_is_never_healed(tmp_path):
    t = _transcript(tmp_path, "s-gone", exists=False)
    plan = heal.plan_all(_text(_spawn("s-gone", t), _error(t)), NOW)
    assert plan.targets == () and plan.skipped == () and plan.order == ()


def test_a_checkpoint_clears_a_session_before_it_can_be_lost(
        tmp_path, tmp_checkpoint_dir):
    t = _transcript(tmp_path, "s-cp", exists=False)
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "s-cp.json").write_text(
        json.dumps({"session_id": "s-cp"}))
    out, lost = ledger._scan_failures(_text(_spawn("s-cp", t), _error(t)), NOW)
    assert out == [] and lost == []


@pytest.fixture
def proj(tmp_path):
    d = tmp_path / "work" / "app"
    d.mkdir(parents=True)
    return d


def _write_log(text):
    config.log_dir().mkdir(parents=True, exist_ok=True)
    (config.log_dir() / "serialize.log").write_text(text, encoding="utf-8")


def test_status_counts_lost_apart_and_health_ignores_it(proj, tmp_path):
    t = _transcript(tmp_path, "s-gone", exists=False)
    _write_log(_text(_spawn("s-gone", t, project=str(proj)), _error(t)))
    payload, _rc = cli.status_payload(str(proj))
    assert payload["outstanding"] == []
    assert payload["lost"] == 1
    assert not any("failed to serialize" in w
                   for w in payload["health"]["warnings"])


def test_status_prints_the_lost_line(proj, tmp_path, capsys):
    t = _transcript(tmp_path, "s-gone", exists=False)
    _write_log(_text(_spawn("s-gone", t, project=str(proj)), _error(t)))
    cli.main(["status", "--project", str(proj)])
    out = capsys.readouterr().out
    assert "lost: 1 (never captured, transcript gone)" in out
    assert "failed to serialize" not in out


def test_status_says_nothing_about_lost_when_there_is_none(proj, capsys):
    _write_log(_text(*_noise(3)))
    cli.main(["status", "--project", str(proj)])
    assert "lost:" not in capsys.readouterr().out


def test_status_says_when_older_lines_are_not_read(proj, capsys, monkeypatch):
    monkeypatch.setattr(serialize_log_window, "WINDOW_BYTES", 2000)
    _write_log(_text(*_noise(200)))
    payload, _rc = cli.status_payload(str(proj))
    assert payload["serialize_log_cut"] is True
    cli.main(["status", "--project", str(proj)])
    assert "older lines are not read" in capsys.readouterr().out


def test_status_is_silent_about_the_window_when_the_log_fits(proj, capsys):
    _write_log(_text(*_noise(5)))
    payload, _rc = cli.status_payload(str(proj))
    assert payload["serialize_log_cut"] is False
    cli.main(["status", "--project", str(proj)])
    assert "not read" not in capsys.readouterr().out


# ---- the effects the issue lists ---------------------------------------------

@pytest.mark.parametrize("src", [HOOK_DIR / "_daimon_hook_lib.py",
                                 Path(ledger.__file__).parent / "_hooks"
                                 / "_daimon_hook_lib.py"],
                         ids=["hook", "mirror"])
def test_the_sweep_knows_a_failure_older_than_200_lines(
        tmp_path, monkeypatch, src):
    sys.path.insert(0, str(src.parent))
    try:
        spec = importlib.util.spec_from_file_location("_lib_6a", src)
        lib = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = lib
        spec.loader.exec_module(lib)
    finally:
        sys.path.remove(str(src.parent))
    folder = tmp_path / "proj"
    folder.mkdir()
    current = folder / "current.jsonl"
    current.write_text(USER_ROW)
    failed = folder / "failed-one.jsonl"
    failed.write_text(USER_ROW)
    past = time.time() - 7200
    import os
    os.utime(failed, (past, past))
    _write_log(_text(_spawn("failed-one", str(failed)), _error(str(failed)),
                     *_noise(500)))
    calls = []
    monkeypatch.setattr(lib, "spawn_serialize",
                        lambda cli_, path, env: calls.append(Path(path).name))
    lib.sweep_orphans("daimon", "/p/A", current.stem, str(current))
    assert calls == []


def test_heal_still_sees_already_retried_when_the_retry_line_is_old(tmp_path):
    t = _transcript(tmp_path, "s-r")
    text = _text(_spawn("s-r", t), _error(t),
                 _spawn("s-r", t, verb="retry"), _error(t),
                 *_noise(400),
                 _spawn("s-r", t), _error(t))
    assert _classes(text) == {"s-r": "retry-exhausted"}
    plan = ledger._heal_plan(text, NOW)
    assert plan["target"] is None


# ---- one listing of the checkpoint directory per scan ------------------------

def test_a_scan_lists_the_checkpoint_directory_a_bounded_number_of_times(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    lines = []
    for i in range(40):
        t = _transcript(tmp_path, f"s-{i}")
        lines += [_spawn(f"s-{i}", t), _error(t)]
    listings = []
    real = Path.glob

    def counting(self, pattern):
        if str(self) == str(tmp_checkpoint_dir):
            listings.append(pattern)
        return real(self, pattern)
    monkeypatch.setattr(Path, "glob", counting)
    out = ledger._compute_outstanding(_text(*lines), NOW)
    assert len(out) == 40
    assert len(listings) <= 2


# ---- the window itself -------------------------------------------------------

def _log_of(tmp_path, text):
    path = tmp_path / "serialize.log"
    path.write_bytes(text.encode("utf-8"))
    return path


def test_the_window_is_the_whole_log_when_it_fits(tmp_path):
    path = _log_of(tmp_path, "a\nb\n")
    assert serialize_log_window.read(path, 100) == ("a\nb\n", False)
    assert serialize_log_window.trim("a\nb\n", 100) == ("a\nb\n", False)


def test_a_missing_log_is_no_activity(tmp_path):
    assert serialize_log_window.read(tmp_path / "nope.log") == ("", False)


def test_the_window_starts_on_a_line_start_when_the_cut_lands_mid_line(
        tmp_path):
    text = "aaaa\nbbbb\ncccc\n"
    path = _log_of(tmp_path, text)
    assert serialize_log_window.read(path, 8) == ("cccc\n", True)
    assert serialize_log_window.trim(text, 8) == ("cccc\n", True)


def test_a_cut_that_lands_exactly_on_a_line_start_keeps_that_line(tmp_path):
    text = "aaaa\nbbbb\ncccc\n"
    path = _log_of(tmp_path, text)
    assert serialize_log_window.read(path, 10) == ("bbbb\ncccc\n", True)
    assert serialize_log_window.trim(text, 10) == ("bbbb\ncccc\n", True)


def test_a_window_inside_one_long_line_reads_nothing(tmp_path):
    text = "x" * 50 + "\n"
    assert serialize_log_window.read(_log_of(tmp_path, text), 10) == ("", True)
    assert serialize_log_window.trim(text, 10) == ("", True)


def test_a_cut_inside_a_multibyte_character_drops_the_partial_line(tmp_path):
    path = _log_of(tmp_path, "café\nok\n")
    assert serialize_log_window.read(path, 5) == ("ok\n", True)


def test_the_hook_library_fails_open_when_the_window_module_is_missing(
        monkeypatch):
    lib = _hook_lib()
    _write_log(_text(_spawn("s-x", "/t/s-x.jsonl"), _error("/t/s-x.jsonl")))
    assert lib._failed_session_stems() == {"s-x"}
    monkeypatch.setattr(lib, "_SERIALIZE_LOG_WINDOW", [None])
    assert lib._failed_session_stems() == set()


def test_the_rich_status_prints_the_lost_line(proj, tmp_path, capsys,
                                              monkeypatch):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    t = _transcript(tmp_path, "s-gone", exists=False)
    _write_log(_text(_spawn("s-gone", t, project=str(proj)), _error(t)))
    cli.main(["status", "--project", str(proj)])
    assert "lost: 1" in capsys.readouterr().out


def test_an_unreadable_log_reads_as_no_activity_for_status_and_heal(
        proj, capsys):
    from daimon_briefing.cli import serialize as cli_serialize
    (config.log_dir() / "serialize.log").mkdir(parents=True)
    payload, _rc = cli.status_payload(str(proj))
    assert payload["outstanding"] == [] and payload["lost"] == 0
    assert cli_serialize._read_serialize_window() == ""


def test_the_status_window_lines_name_lost_and_the_cut():
    from daimon_briefing import render
    assert render._ledger_window_lines(
        {"lost": 2, "serialize_log_cut": True}) == [
        "lost: 2 (never captured, transcript gone)",
        "serialize.log is longer than the window that is read; "
        "older lines are not read"]
    assert render._ledger_window_lines({}) == []
