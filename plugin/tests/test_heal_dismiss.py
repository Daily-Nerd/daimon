"""`daimon heal --dismiss <session-id>`: a person ends an outstanding entry that
no fact can end. It writes the ordinary final skip line, so the fold, status
and heal need no new state, and it refuses anything that is not outstanding.
"""

import time

import pytest

from daimon_briefing import api, cli, config, heal, ledger, mcp_tools
from daimon_briefing.cli import _observe

NOW = time.time()
OLD = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 86400))
RECENT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - 30))


@pytest.fixture
def human(monkeypatch):
    monkeypatch.setattr(_observe, "attended", lambda: True)


def _write(*lines):
    config.log_dir().mkdir(parents=True, exist_ok=True)
    (config.log_dir() / "serialize.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _log():
    return (config.log_dir() / "serialize.log").read_text(encoding="utf-8")


def _hung(sid="S-hung", stamp=OLD, transcript=None):
    group = f" (transcript: {transcript})" if transcript else ""
    return (f"{stamp} session-end: spawned serialize for {sid} "
            f"(reason: x, project: /p){group}")


def _outstanding():
    return {f["sid"]: f["class"]
            for f in ledger._compute_outstanding(_log(), time.time())}


def _dismiss(sid):
    return cli.main(["heal", "--dismiss", sid])


def test_dismissing_a_hung_entry_with_no_transcript_ends_it(human, capsys):
    _write(_hung())
    assert _outstanding() == {"S-hung": "hung"}
    assert _dismiss("S-hung") == 0
    assert _outstanding() == {}
    plan = heal.plan_all(_log(), time.time())
    assert plan.targets == () and plan.skipped == ()
    out = capsys.readouterr().out
    assert "will not retry" in out


def test_the_log_gains_exactly_one_line_in_the_existing_skip_format(human):
    _write(_hung())
    before = _log().splitlines()
    _dismiss("S-hung")
    after = _log().splitlines()
    assert after[:-1] == before and len(after) == len(before) + 1
    assert ledger._LEDGER_SKIP_RE.match(after[-1])
    assert after[-1] == ledger.skip_line("S-hung", ledger.DISMISS_REASON)
    entry = ledger._session_ledger(_log(), time.time())["S-hung"]
    assert entry["result_kind"] == "skipped"


def test_a_later_real_success_still_reads_as_success(human):
    _write(_hung())
    _dismiss("S-hung")
    with (config.log_dir() / "serialize.log").open("a") as f:
        f.write("wrote checkpoint: /cp/S-hung.json (took 3s)\n")
    entry = ledger._session_ledger(_log(), time.time())["S-hung"]
    assert entry["result_kind"] == "success"


def test_an_entry_whose_transcript_exists_names_the_command_that_captures_it(
        human, tmp_path, capsys):
    t = tmp_path / "S-hung.jsonl"
    t.write_text("{}\n")
    _write(_hung(transcript=t),
           f"error: boom (transcript: {t}) after 3s")
    assert _dismiss("S-hung") == 0
    out = capsys.readouterr().out
    assert f"daimon serialize {t}" in out
    assert "even if its transcript still exists" in out


@pytest.mark.parametrize("case", ["unknown", "lost", "in-flight", "dismissed",
                                  "success"])
def test_dismissal_refuses_what_is_not_outstanding(human, tmp_path, capsys,
                                                   case):
    gone = tmp_path / "gone.jsonl"
    if case == "unknown":
        _write(_hung("S-other"))
    elif case == "lost":
        _write(_hung("S-hung", transcript=gone))
    elif case == "in-flight":
        _write(_hung("S-hung", stamp=RECENT).replace(
            "spawned serialize", "retry serialize").replace(
            "session-end", "session-start"))
    elif case == "dismissed":
        _write(_hung())
        assert _dismiss("S-hung") == 0
    else:
        _write(_hung(), "wrote checkpoint: /cp/S-hung.json (took 3s)")
    capsys.readouterr()
    before = _log()
    assert _dismiss("S-hung") != 0
    assert _log() == before
    assert "S-hung" in capsys.readouterr().err


def test_dismissal_refuses_an_agent_caller(capsys):
    _write(_hung())
    before = _log()
    assert _dismiss("S-hung") != 0
    assert _log() == before
    assert "human" in capsys.readouterr().err


def test_dismissal_stands_alone(human, capsys):
    _write(_hung())
    assert cli.main(["heal", "--dismiss", "S-hung", "--all"]) != 0
    assert _outstanding() == {"S-hung": "hung"}


def test_a_codex_rollout_stem_names_its_session(human):
    stem = "rollout-2026-10-12T10-11-12-abc"
    _write(_hung("abc"))
    assert _dismiss(stem) == 0
    assert _outstanding() == {}


def test_the_in_process_heal_entry_points_do_not_offer_dismissal():
    names = [n for n in dir(api) if "dismiss" in n.lower()]
    names += [n for n in dir(mcp_tools) if "dismiss" in n.lower()]
    assert names == []


def test_the_entries_heal_cannot_fix_name_the_command(tmp_path):
    from daimon_briefing import render
    _write(_hung())
    out = ledger._compute_outstanding(_log(), time.time())
    assert "daimon heal --dismiss S-hung" in "\n".join(
        render._outstanding_lines(out))
    for cls in ("unrecoverable", "hung", "retry-exhausted"):
        assert "--dismiss" in ledger._HEAL_SKIP_REASON[cls]
