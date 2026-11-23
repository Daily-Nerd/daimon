"""A recipient verb is written only for a request addressed to this project.

The ledger boundary (`requests._answering`) is where `done`, `accept`,
`reject`, `needs-info` and `suppress` decide what they may write, so every
channel (CLI, MCP, an in-process caller) gets the same rule. A request this
project was offered but has not accepted is addressed to it: a `done` on it is
a held claim by design (#1333). An id that is not addressed to it is refused
and nothing is written.
"""

import pytest

from daimon_briefing import cli, requests, store

SENDER = "/p/sender"
RECIPIENT = "/p/recipient"
STRANGER = "/p/stranger"
GHOST = "q-0123456789ab"


def _ledger_rows(project):
    return requests.events(project_dir=project)


def _offer(to_project=RECIPIENT, sender=SENDER):
    return requests.open_request(
        to=store.project_slug(to_project), ask="check the retrieval bar",
        why="it blocks the release", channel="cli-agent", project_dir=sender)


@pytest.fixture
def offered(tmp_checkpoint_dir):
    return _offer()


def test_done_on_an_unknown_id_is_refused_and_writes_nothing(tmp_checkpoint_dir):
    with pytest.raises(requests.RequestError, match="unknown request"):
        requests.done(GHOST, channel="cli-agent", evidence="shipped it",
                      project_dir=RECIPIENT)
    assert _ledger_rows(RECIPIENT) == []


def test_done_on_a_request_addressed_to_someone_else_is_refused(offered):
    with pytest.raises(requests.RequestError, match="unknown request"):
        requests.done(offered, channel="cli-agent", evidence="shipped it",
                      project_dir=STRANGER)
    assert _ledger_rows(STRANGER) == []


def test_done_on_an_offered_unaccepted_request_is_a_held_claim(offered):
    event_id = requests.done(offered, channel="cli-agent",
                             evidence="shipped it", project_dir=RECIPIENT)
    record = requests.recipient_join(project_dir=RECIPIENT)[offered]
    assert record["state"] == "open"
    assert record["done_pending"] is True
    assert record["row_effects"][event_id]["effect"] == "held_as_claim"


@pytest.mark.parametrize("verb", ["accept", "reject", "needs_info",
                                  "suppress"])
def test_every_recipient_verdict_refuses_an_unknown_id(tmp_checkpoint_dir,
                                                       verb):
    with pytest.raises(requests.RequestError, match="unknown request"):
        getattr(requests, verb)(GHOST, channel="cli-tty",
                                project_dir=RECIPIENT)
    assert _ledger_rows(RECIPIENT) == []


def test_an_agent_accept_of_an_unknown_id_is_refused_as_unknown(
        tmp_checkpoint_dir):
    with pytest.raises(requests.RequestError, match="unknown request"):
        requests.accept(GHOST, channel="cli-agent", project_dir=RECIPIENT)
    assert _ledger_rows(RECIPIENT) == []


def test_a_verdict_on_an_addressed_foreign_ask_still_lands(offered):
    requests.accept(offered, channel="cli-tty", note="on it",
                    project_dir=RECIPIENT)
    record = requests.recipient_join(project_dir=RECIPIENT)[offered]
    assert record["state"] == "accepted"


def test_done_on_a_rejected_foreign_ask_is_refused(offered):
    requests.reject(offered, channel="cli-tty", project_dir=RECIPIENT)
    with pytest.raises(requests.RequestError, match="rejected"):
        requests.done(offered, channel="cli-agent", evidence="x",
                      project_dir=RECIPIENT)


def test_cli_done_on_an_unknown_id_exits_nonzero_and_writes_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main(["request", "done", GHOST, "--evidence", "x",
                   "--by", "agent", "--project", RECIPIENT])
    assert rc == 1
    assert "unknown request" in capsys.readouterr().out
    assert _ledger_rows(RECIPIENT) == []
