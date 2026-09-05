"""#943: the check flags on `daimon ruling propose|revise`, the ratify
ceremony's check line, and the render of proposed / armed."""
import json

import pytest

from daimon_briefing import cli, refutations


PROJECT = "/p/ruling-checks"
BODY = "#!/bin/sh\nrg -q -- '\\u2014' \"$DAIMON_CHECK_SUBJECT\" && exit 1\nexit 0\n"


@pytest.fixture
def body_file(tmp_path):
    path = tmp_path / "gate.sh"
    path.write_text(BODY, encoding="utf-8")
    return str(path)


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _propose(body_file=None, extra=()):
    argv = ["ruling", "propose", "--subject", "public posts",
            "--verdict", "no em-dashes in public posts",
            "--scope", "publishing", "--evidence", "issue:943",
            "--by", "agent", "--project", PROJECT, "--json"]
    if body_file:
        argv += ["--check-body-file", body_file, "--check-match", "gh pr create"]
    argv += list(extra)
    return cli.main(argv)


def test_propose_with_check_flags_records_a_proposed_check(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["check"]["body"] == BODY
    assert record["check"]["match"] == "gh pr create"
    assert record["check"]["intent"] == "warn"
    assert record["check_lifecycle"] == "proposed"


def test_propose_check_intent_flag_is_stored(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file, extra=["--check-intent", "enforce"]) == 0
    assert json.loads(capsys.readouterr().out)["check"]["intent"] == "enforce"


def test_propose_check_flags_must_come_together(tmp_checkpoint_dir, capsys):
    rc = cli.main(["ruling", "propose", "--subject", "public posts",
                   "--verdict", "no em-dashes in public posts",
                   "--scope", "publishing", "--evidence", "issue:943",
                   "--by", "agent", "--project", PROJECT,
                   "--check-match", "gh pr create"])
    assert rc == 1
    assert "--check-body-file" in capsys.readouterr().out


def test_propose_unreadable_check_file_refuses(tmp_checkpoint_dir, capsys):
    assert _propose("/nonexistent/gate.sh") == 1
    out = capsys.readouterr().out
    assert "ruling not recorded" in out
    assert "check body file" in out
    assert refutations.listing(polarity="ruling", project_dir=PROJECT) == []


def test_propose_without_check_flags_records_no_check(
        tmp_checkpoint_dir, capsys):
    assert _propose() == 0
    record = json.loads(capsys.readouterr().out)
    assert "check" not in record


def test_revise_with_check_flags_proposes_a_new_body(
        tmp_checkpoint_dir, body_file, tmp_path, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    new = tmp_path / "gate2.sh"
    new.write_text("exit 0\n", encoding="utf-8")
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                   "--by", "agent", "--project", PROJECT, "--json",
                   "--check-body-file", str(new), "--check-match", "gh pr create"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["check"]["body"] == "exit 0\n"


def _armed_check(body_file, monkeypatch, capsys):
    """Propose then ratify a ruling carrying a check, leaving it armed."""
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 0
    capsys.readouterr()
    return ruling_id


def test_human_check_revise_on_active_ruling_shows_ceremony_and_arms(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, tmp_path, capsys):
    ruling_id = _armed_check(body_file, monkeypatch, capsys)
    new = tmp_path / "gate2.sh"
    new.write_text("exit 0\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                   "--project", PROJECT,
                   "--check-body-file", str(new), "--check-match", "gh pr create"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "New check:" in out
    assert "arms an executable" in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["body"] == "exit 0\n"
    assert record["check_lifecycle"] == "armed"


def test_human_check_revise_declined_leaves_the_armed_check(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, tmp_path, capsys):
    ruling_id = _armed_check(body_file, monkeypatch, capsys)
    new = tmp_path / "gate2.sh"
    new.write_text("exit 0\n", encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                   "--project", PROJECT,
                   "--check-body-file", str(new), "--check-match", "gh pr create"])
    assert rc == 1
    assert "not revised" in capsys.readouterr().out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["body"] == BODY


def test_agent_check_revise_on_active_ruling_has_no_ceremony(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, tmp_path, capsys):
    ruling_id = _armed_check(body_file, monkeypatch, capsys)
    new = tmp_path / "gate2.sh"
    new.write_text("exit 0\n", encoding="utf-8")

    def _no_prompt(prompt=""):
        raise AssertionError("ceremony must not prompt")

    monkeypatch.setattr("builtins.input", _no_prompt)
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                   "--by", "agent", "--project", PROJECT,
                   "--check-body-file", str(new), "--check-match", "gh pr create"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "New check:" not in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["body"] == BODY
