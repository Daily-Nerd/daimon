"""The in-process baton writer a host calls: agent tier, capped, loud."""
import pytest

from daimon_briefing import api, authority, cli, handoff, jsonl, store

PROJECT = "/p/handoff-write"
AGENT_LABEL = "HANDOFF (written by an agent, unverified, "


def _tty(monkeypatch, value):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: value, raising=False)


def _baton_rows():
    path = store._events_path(store._resolved(PROJECT))
    return [r for r in jsonl.read(path).rows if r.get("kind") == "handoff"]


def _brief(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


def test_the_limit_is_readable_and_is_the_cli_limit():
    assert api.HANDOFF_MAX_CHARS == handoff.MAX_CHARS == cli._HANDOFF_MAX_CHARS


@pytest.mark.parametrize("attended", [True, False])
def test_write_handoff_is_agent_tier_whatever_the_terminal(
        tmp_checkpoint_dir, monkeypatch, capsys, attended):
    _tty(monkeypatch, attended)
    api.write_handoff(PROJECT, "  pick up the migration  ")
    [row] = _baton_rows()
    assert authority.of_row("events", row) == "agent"
    assert row["note"] == "pick up the migration"
    assert AGENT_LABEL in _brief(capsys, monkeypatch)


def test_write_handoff_accepts_exactly_the_limit(tmp_checkpoint_dir):
    api.write_handoff(PROJECT, "x" * api.HANDOFF_MAX_CHARS)
    assert len(store.active_handoff(PROJECT)["note"]) == api.HANDOFF_MAX_CHARS


def test_write_handoff_refuses_over_the_limit_and_writes_nothing(
        tmp_checkpoint_dir):
    with pytest.raises(api.HandoffTooLong) as caught:
        api.write_handoff(PROJECT, "x" * (api.HANDOFF_MAX_CHARS + 1))
    assert caught.value.limit == api.HANDOFF_MAX_CHARS
    assert caught.value.length == api.HANDOFF_MAX_CHARS + 1
    assert str(api.HANDOFF_MAX_CHARS) in str(caught.value)
    assert _baton_rows() == []


@pytest.mark.parametrize("text", ["", "   \n "])
def test_write_handoff_refuses_an_empty_baton(tmp_checkpoint_dir, text):
    with pytest.raises(ValueError):
        api.write_handoff(PROJECT, text)
    assert _baton_rows() == []


def test_write_handoff_says_when_nothing_was_recorded(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "append_event", lambda *a, **k: False)
    with pytest.raises(api.HandoffNotRecorded):
        api.write_handoff(PROJECT, "baton")
