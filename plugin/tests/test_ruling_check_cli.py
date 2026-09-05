"""#943: the check flags on `daimon ruling propose|revise`, the ratify
ceremony's check line, and the render of proposed / armed."""
import hashlib
import json

import pytest

from daimon_briefing import cli, refutations
from daimon_briefing.cli import _ledger


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


def test_oversized_check_body_file_is_refused_without_reading_it(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    """#943: the cap is enforced on the file's SIZE, so an oversized script is
    refused by one stat instead of being pulled into memory to be measured."""
    big = tmp_path / "huge.sh"
    big.write_text("#!/bin/sh\n" + "e" * refutations._MAX_CHECK_BODY,
                   encoding="utf-8")
    real_open = open
    opened = []

    def _tracking_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _tracking_open)
    assert _propose(str(big)) == 1
    out = capsys.readouterr().out
    assert "ruling not recorded" in out
    assert str(refutations._MAX_CHECK_BODY) in out
    assert str(big) not in opened
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


def test_ratify_shows_the_check_and_arms_it(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Check: warn" in out
    assert "gh pr create" in out
    assert "will run before matching actions" in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check_lifecycle"] == "armed"


def test_ratify_is_loud_when_the_check_changed_mid_confirmation(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]

    def _swap_check(prompt=""):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check={"match": "gh pr create", "body": "exit 0\n"},
            project_dir=PROJECT)
        return "y"

    monkeypatch.setattr("builtins.input", _swap_check)
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    assert "changed during confirmation" in capsys.readouterr().out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["check_lifecycle"] == "proposed"


def test_ratify_is_loud_when_a_check_appeared_during_confirmation(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    """#943: the human confirmed a ruling that showed NO check, so the ratify
    row carries no pin. A check that arrives during the confirm window must
    not ride that unbound row into an armed state."""
    assert _propose() == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]

    def _add_check(prompt=""):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check={"match": "gh", "body": "exit 1\n"}, project_dir=PROJECT)
        return "y"

    monkeypatch.setattr("builtins.input", _add_check)
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    assert "changed during confirmation" in capsys.readouterr().out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["check_lifecycle"] == "proposed"


def test_ratify_without_a_check_has_no_check_line(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    assert _propose() == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT]) == 0
    assert "Check:" not in capsys.readouterr().out


def test_check_ceremony_lines_helper():
    """#943: one home for the check ceremony wording."""
    lines = _ledger._check_ceremony_lines(
        {"match": "gh", "intent": "warn", "body": "a\nb\n"},
        label="Check", verb="Ratifying")
    assert len(lines) == 2
    assert "Check: warn" in lines[0]
    assert "· 2 lines ·" in lines[0]
    expected_sha = hashlib.sha256(b"a\nb\n").hexdigest()[:12]
    assert expected_sha in lines[0]
    assert lines[1].endswith("Ratifying arms an executable.")


def test_show_renders_a_candidate_check_as_proposed_not_armed(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Check: proposed, not armed" in out
    assert "match /gh pr create/" in out


def test_show_renders_an_active_check_as_armed(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT]) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    assert "Check: armed" in capsys.readouterr().out


def test_list_marks_rows_that_carry_a_check(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "list", "--project", PROJECT]) == 0
    assert "[check: proposed]" in capsys.readouterr().out


def test_show_says_when_a_pending_proposal_carries_a_check(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, tmp_path, capsys):
    """#943: a proposal that would swap the executable is not the same ask as
    one that rewords the rule, and the human reading `show` decides which."""
    ruling_id = _armed_check(body_file, monkeypatch, capsys)
    new = tmp_path / "gate2.sh"
    new.write_text("exit 0\n", encoding="utf-8")
    assert cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                     "--by", "agent", "--project", PROJECT,
                     "--check-body-file", str(new),
                     "--check-match", "gh pr create"]) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Pending revision proposal" in out
    assert "(carries a check)" in out


def test_show_stays_silent_when_a_pending_proposal_carries_no_check(
        tmp_checkpoint_dir, body_file, _tty, monkeypatch, capsys):
    ruling_id = _armed_check(body_file, monkeypatch, capsys)
    assert cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                     "--by", "agent", "--project", PROJECT,
                     "--verdict", "no internal numbers in public posts"]) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Pending revision proposal" in out
    assert "(carries a check)" not in out


def test_show_without_a_check_has_no_check_line(tmp_checkpoint_dir, capsys):
    assert _propose() == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    assert "Check:" not in capsys.readouterr().out


def test_show_head_line_carries_no_check_suffix(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "[check:" not in out
    assert "Check: proposed, not armed" in out


def test_revise_check_flags_must_come_together(
        tmp_checkpoint_dir, body_file, capsys):
    assert _propose(body_file) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:943",
                   "--by", "agent", "--project", PROJECT,
                   "--check-match", "gh pr create"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ruling not revised" in out
    assert "--check-body-file" in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["revision"] == 1
    assert record["check"]["body"] == BODY
