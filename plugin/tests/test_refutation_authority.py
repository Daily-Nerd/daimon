"""Authority is a property of the write path, never a caller's claim.

`--by human` let any caller assert its own authority. An agent that typed it
produced a row indistinguishable from one a person typed, and the ledger kept
no bit to tell them apart afterwards. That is the echo-defense hole (#512) and
the self-assigned-identity hole (scar 0032) one layer up: an actor witnessing
its own claim.

The fix is the pattern `resolve` already uses in this same CLI — the agent value
is the NARROWER one and the human path is the ABSENCE of the flag — plus an
observed `channel` recorded on every lifecycle row. The CLI can only observe two
channels. `ui` and `signed` are reachable only by an in-process writer, which is
what stops a future UI from being a wrapper around the very flag we deleted.
"""
import json

import pytest

from daimon_briefing import refutations
from daimon_briefing.cli import build_parser
from daimon_briefing import cli


PROJECT = "/p/refute-authority"

MUTATING = ("add", "ratify", "revise", "overturn")


def _tty(monkeypatch, value):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: value, raising=False)


def _add(monkeypatch, *, by_agent=True, tty=False, ratify=False):
    _tty(monkeypatch, tty)
    argv = ["refute", "add", "--subject", "rebuild the merge cache",
            "--verdict", "measured slower than streaming",
            "--scope", "serialize path",
            "--evidence", "measurement:566/623",
            "--project", PROJECT]
    if by_agent:
        argv += ["--by", "agent"]
    if ratify:
        argv += ["--ratify"]
    return cli.main(argv)


def _rows(tmp_checkpoint_dir):
    path = (tmp_checkpoint_dir / refutations.store.project_slug(PROJECT)
            / "refutations.jsonl")
    if not path.exists():
        # A refused write leaves no ledger at all, which is stronger than an
        # empty one: nothing was created to be cleaned up.
        return []
    return [json.loads(line) for line in
            path.read_text().splitlines() if line.strip()]


def _only_id(tmp_checkpoint_dir):
    return _rows(tmp_checkpoint_dir)[0]["refutation_id"]


def _by_action(sub):
    """The `--by` action on one refute subparser, or None if it has none."""
    refute = next(
        a for a in build_parser()._subparsers._group_actions[0].choices["refute"]
        ._subparsers._group_actions[0].choices.items() if a[0] == sub)[1]
    for action in refute._actions:
        if "--by" in action.option_strings:
            return action
    return None


@pytest.mark.parametrize("sub", MUTATING)
def test_by_human_is_not_an_accepted_choice(sub):
    # Asserted against the parser's own choices, not against SystemExit: every
    # one of these commands also exits on missing required arguments, so a
    # `pytest.raises(SystemExit)` here would pass whether or not the flag was
    # ever removed. That is the shape of the round-2 defect this whole review
    # exists to stop repeating.
    action = _by_action(sub)
    assert action is not None, f"{sub} lost its --by flag entirely"
    assert "human" not in (action.choices or ()), (
        f"refute {sub} still lets a caller declare its own humanity")
    assert list(action.choices) == ["agent"], (
        "agent must be the narrower declaration, mirroring `resolve`")
    assert not action.required, (
        "the human path is the ABSENCE of --by, so it cannot be required")


def test_agent_channel_is_recorded_and_cannot_activate(
        tmp_checkpoint_dir, monkeypatch):
    assert _add(monkeypatch, by_agent=True) == 0
    row = _rows(tmp_checkpoint_dir)[0]

    assert row["channel"] == "cli-agent"
    assert row["authority"] == "agent"
    record = refutations.get(row["refutation_id"], project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_non_interactive_caller_cannot_claim_the_human_channel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # Omitting --by is the human path. A caller with no terminal has not
    # demonstrated one, so the claim is refused rather than recorded on trust.
    rc = _add(monkeypatch, by_agent=False, tty=False)
    assert rc == 1
    assert "no interactive terminal" in capsys.readouterr().out.lower()
    assert _rows(tmp_checkpoint_dir) == []


def test_interactive_caller_ratifies_through_the_tty_channel(
        tmp_checkpoint_dir, monkeypatch):
    assert _add(monkeypatch, by_agent=True) == 0
    ref_id = _only_id(tmp_checkpoint_dir)

    _tty(monkeypatch, True)
    assert cli.main(["refute", "ratify", ref_id, "--project", PROJECT]) == 0

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activation_channel"] == "cli-tty"
    assert _rows(tmp_checkpoint_dir)[-1]["channel"] == "cli-tty"


def test_every_lifecycle_row_records_a_channel(
        tmp_checkpoint_dir, monkeypatch):
    assert _add(monkeypatch, by_agent=True) == 0
    ref_id = _only_id(tmp_checkpoint_dir)
    _tty(monkeypatch, True)
    cli.main(["refute", "ratify", ref_id, "--project", PROJECT])
    cli.main(["refute", "revise", ref_id, "--evidence", "measurement:re-run",
              "--verdict", "still slower on the rerun", "--project", PROJECT])
    cli.main(["refute", "overturn", ref_id, "--evidence", "measurement:x",
              "--project", PROJECT])

    rows = _rows(tmp_checkpoint_dir)
    assert len(rows) >= 4
    for row in rows:
        assert row.get("channel") in refutations.CHANNELS, row["event"]


def test_cli_cannot_mint_the_ui_or_signed_channels(
        tmp_checkpoint_dir, monkeypatch):
    # A UI must be a writer, not a wrapper. If the CLI could emit `ui`, an
    # agent shelling out could emit it too, which is the deleted flag wearing
    # a different name.
    _tty(monkeypatch, True)
    for channel in ("ui", "signed"):
        with pytest.raises(SystemExit):
            cli.main(["refute", "add", "--subject", "s", "--verdict", "v",
                      "--scope", "sc", "--evidence", "issue:1",
                      "--channel", channel, "--project", PROJECT])


def test_authority_is_derived_from_channel_not_supplied(tmp_checkpoint_dir):
    # The in-process contract: a writer names the channel it observed, and the
    # authority follows from it. There is no way to name one and claim the
    # other.
    assert refutations.CHANNEL_AUTHORITY["cli-agent"] == "agent"
    for human_channel in ("cli-tty", "ui", "signed"):
        assert refutations.CHANNEL_AUTHORITY[human_channel] == "human"

    with pytest.raises(refutations.RefutationError, match="channel"):
        refutations.assert_refutation(
            subject="s", verdict="v", scope="sc", evidence=["issue:1"],
            channel="not-a-channel", project_dir=PROJECT)
