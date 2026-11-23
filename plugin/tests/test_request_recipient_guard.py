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


# ---- review round: who may answer, and what a degraded read may claim -----


def _damage_ledger(project):
    """The sender's requests ledger made unprovable: garbage ahead of the
    rows, the shape the join skips rather than reads around."""
    from daimon_briefing import config
    path = config.checkpoint_dir() / store.project_slug(project) / "requests.jsonl"
    path.write_bytes(b"\xff\xfe not json\n" + path.read_bytes())


@pytest.mark.parametrize("channel,where", [("cli-agent", RECIPIENT),
                                           ("cli-tty", STRANGER)])
def test_an_unreadable_sender_ledger_is_named_not_called_unknown(
        offered, channel, where):
    _damage_ledger(SENDER)
    verb = (requests.done if channel == "cli-agent" else requests.accept)
    kwargs = {"evidence": "x"} if verb is requests.done else {}
    with pytest.raises(requests.RequestError,
                       match="could not be read") as err:
        verb(offered, channel=channel, project_dir=where, **kwargs)
    assert "unknown request" not in str(err.value)
    assert "daimon status" in str(err.value)
    assert _ledger_rows(where) == []


def test_a_person_decides_an_ask_addressed_elsewhere_from_any_directory(
        offered):
    event_id = requests.accept(offered, channel="cli-tty",
                               project_dir=STRANGER)
    rows = [r for r in _ledger_rows(STRANGER) if r["event"] == "accepted"]
    assert [r["event_id"] for r in rows] == [event_id]
    record = requests.recipient_join(project_dir=RECIPIENT)[offered]
    assert record["state"] == "accepted"


def test_a_tenant_scoped_person_cannot_probe_another_tenants_request(
        offered, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    with pytest.raises(requests.RequestError, match="unknown request"):
        requests.accept(offered, channel="cli-tty", project_dir=STRANGER)
    assert _ledger_rows(STRANGER) == []
    requests.accept(offered, channel="cli-tty", project_dir=RECIPIENT)
    assert [r["event"] for r in _ledger_rows(RECIPIENT)] == ["accepted"]


@pytest.mark.parametrize("channel", ["bogus", "mechanical"])
@pytest.mark.parametrize("verb", ["accept", "done"])
def test_an_unknown_or_machine_channel_never_passes_as_a_person(
        offered, channel, verb):
    kwargs = {"evidence": "x"} if verb == "done" else {}
    with pytest.raises(requests.RequestError):
        getattr(requests, verb)(offered, channel=channel,
                                project_dir=STRANGER, **kwargs)
    assert _ledger_rows(STRANGER) == []


def test_an_ask_addressed_to_a_legacy_slug_of_this_project_is_answerable(
        tmp_checkpoint_dir, monkeypatch):
    old_slug = "-p-recipient-before-the-move"
    mine = store.project_slug(RECIPIENT)
    real = requests.buckets.aliases_for
    monkeypatch.setattr(
        requests.buckets, "aliases_for",
        lambda slug: frozenset({old_slug}) if slug == mine else real(slug))
    legacy = requests.open_request(
        to=old_slug, ask="check the retrieval bar", why="it blocks",
        channel="cli-agent", project_dir=SENDER)
    event_id = requests.done(legacy, channel="cli-agent", evidence="x",
                             project_dir=RECIPIENT)
    assert event_id
    assert requests.recipient_join(project_dir=RECIPIENT)[legacy][
        "done_pending"] is True


def test_the_existence_scan_ignores_a_ledger_the_join_refuses_to_trust(
        offered):
    assert requests._was_ever_sent(offered, STRANGER) is True
    _damage_ledger(SENDER)
    assert requests._was_ever_sent(offered, STRANGER) is False


def test_a_person_with_a_ledger_of_their_own_still_decides_elsewhere(offered):
    _offer(to_project=SENDER, sender=STRANGER)
    assert requests.accept(offered, channel="cli-tty", project_dir=STRANGER)
