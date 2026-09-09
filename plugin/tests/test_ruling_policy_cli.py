"""#961 slice 4: the `--request-policy`/`--no-request-policy` flags on
`daimon ruling propose|revise`, the ratify ceremony's policy line, and the
`Policy:` line rendered on `ruling show`/`list`. Mirrors
`tests/test_ruling_check_cli.py`'s own shape for `check`."""
import json

import pytest

from daimon_briefing import cli, refutations


PROJECT = "/p/ruling-policies"


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _propose(extra=()):
    argv = ["ruling", "propose", "--subject", "requests from p-sender",
            "--verdict", "agent may accept work asks from p-sender",
            "--scope", "cross-project requests", "--evidence", "issue:961",
            "--by", "agent", "--project", PROJECT, "--json"]
    argv += list(extra)
    return cli.main(argv)


_POLICY_FLAGS = ["--request-policy", "sender=p-sender",
                 "--request-policy", "kind=work",
                 "--request-policy", "verb=accept",
                 "--request-policy", "by=agent"]


def test_propose_with_request_policy_flags_records_a_candidate_grant(
        tmp_checkpoint_dir, capsys):
    assert _propose(_POLICY_FLAGS) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["request_policy"]["sender"] == "p-sender"
    assert record["request_policy"]["kind"] == "work"
    assert record["state"] == "candidate"


def test_propose_with_a_malformed_policy_value_is_refused(
        tmp_checkpoint_dir, capsys):
    rc = cli.main(["ruling", "propose", "--subject", "x", "--verdict", "y",
                   "--scope", "z", "--evidence", "issue:961", "--by", "agent",
                   "--project", PROJECT,
                   "--request-policy", "sender=p-sender",
                   "--request-policy", "kind=everything",
                   "--request-policy", "verb=accept",
                   "--request-policy", "by=agent"])
    assert rc == 1
    assert "kind" in capsys.readouterr().out


def test_propose_with_a_keyvalue_shaped_wrong_flag_is_refused(
        tmp_checkpoint_dir, capsys):
    rc = cli.main(["ruling", "propose", "--subject", "x", "--verdict", "y",
                   "--scope", "z", "--evidence", "issue:961", "--by", "agent",
                   "--project", PROJECT,
                   "--request-policy", "sender-no-equals-sign"])
    assert rc == 1
    assert "KEY=VALUE" in capsys.readouterr().out


def test_propose_with_a_repeated_policy_key_is_refused(
        tmp_checkpoint_dir, capsys):
    """#961 slice 4 review round 2 (M5a): a repeated KEY used to win
    silently (plain dict assignment, last one in wins) — the same shape
    every other malformed `--request-policy` input already refuses at the
    parse boundary, now including this one."""
    rc = cli.main(["ruling", "propose", "--subject", "x", "--verdict", "y",
                   "--scope", "z", "--evidence", "issue:961", "--by", "agent",
                   "--project", PROJECT,
                   "--request-policy", "sender=p-sender",
                   "--request-policy", "sender=p-other",
                   "--request-policy", "kind=work",
                   "--request-policy", "verb=accept",
                   "--request-policy", "by=agent"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "sender" in out
    assert "more than once" in out


def test_revise_shows_the_new_policys_pinned_sha_before_confirming(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    """#961 slice 4 review round 2 (M5b): the pre-write ceremony used to
    print the raw, unhashed `--request-policy` KEY=VALUE dict — a human
    confirming `New policy: {'sender': ...}` had no way to check it against
    what actually lands, unlike every other pin this codebase discloses
    before a write. It now shows the SAME formatted line, with the sha the
    write will pin, that `ruling ratify` already shows for a stored one."""
    # The pre-confirm ceremony only fires on an ACTIVE ruling's revise —
    # propose+ratify first, the same setup `test_revise_no_request_policy_
    # clears_an_active_grant` above uses.
    assert _propose(_POLICY_FLAGS) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT]) == 0
    capsys.readouterr()
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:961",
                   "--project", PROJECT,
                   "--request-policy", "sender=p-other",
                   "--request-policy", "kind=work",
                   "--request-policy", "verb=accept",
                   "--request-policy", "by=agent"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "New policy: sender=p-other kind=work verb=accept by=agent" in out
    assert "sha " in out
    assert "{'sender'" not in out  # never the raw, unhashed dict repr
    record = refutations.get(ruling_id, project_dir=PROJECT)
    expected_sha = record["request_policy"]["sha256"]
    assert expected_sha[:12] in out


def test_ratify_shows_and_pins_the_policy(tmp_checkpoint_dir, _tty,
                                          monkeypatch, capsys):
    assert _propose(_POLICY_FLAGS) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Policy: sender=p-sender" in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"


def test_ratify_is_loud_when_the_policy_changed_mid_confirmation(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    assert _propose(_POLICY_FLAGS) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]

    def _swap_policy(prompt=""):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:961"],
            request_policy={"sender": "p-other", "kind": "work",
                            "verb": "accept", "by": "agent"},
            project_dir=PROJECT)
        return "y"

    monkeypatch.setattr("builtins.input", _swap_policy)
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    assert "changed during confirmation" in capsys.readouterr().out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_revise_with_request_policy_and_no_request_policy_together_is_refused(
        tmp_checkpoint_dir, capsys):
    assert _propose(_POLICY_FLAGS) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:961",
                   "--by", "agent", "--project", PROJECT,
                   "--no-request-policy"] + _POLICY_FLAGS)
    assert rc == 1
    assert "mutually exclusive" in capsys.readouterr().out


def test_revise_no_request_policy_clears_an_active_grant(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    rc = cli.main(["ruling", "propose", "--subject", "requests from p-sender",
                   "--verdict", "agent may accept work asks from p-sender",
                   "--scope", "cross-project requests",
                   "--evidence", "issue:961", "--ratify",
                   "--project", PROJECT, "--json"] + _POLICY_FLAGS)
    assert rc == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "revise", ruling_id, "--evidence", "issue:961",
                   "--project", PROJECT, "--no-request-policy"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLEARED" in out
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["request_policy"] is None
    assert record["state"] == "active"


def test_ruling_show_and_list_render_the_policy_line(tmp_checkpoint_dir,
                                                      capsys):
    assert _propose(_POLICY_FLAGS) == 0
    ruling_id = json.loads(capsys.readouterr().out)["refutation_id"]
    rc = cli.main(["ruling", "show", ruling_id, "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Policy: sender=p-sender kind=work verb=accept by=agent" in out
    assert "not in force (candidate)" in out
