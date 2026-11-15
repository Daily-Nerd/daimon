"""#1339: a pending done claim's evidence stays with the recipient.

An agent's `done --evidence` on an ask nobody has accepted is only a pending
claim. For an `info` ask the accept is the recipient person's consent to share
what that project knows, so the answer must not reach the sender before it.
The fold's sender joins own that decision: the record they return carries no
evidence text for an unlanded claim, and says so with `done_evidence_withheld`.
"""

import json

import pytest

from daimon_briefing import cli, requests, store
from daimon_briefing.surfaces import Writer

SENDER = "/p/claim-sender"
RECIPIENT = "/p/claim-recipient"
ANSWER = "the exporter retries three times"
PENDING_LINE = "a completion is pending the recipient's accept"


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _seed(project_dir):
    store.write_checkpoint("S-" + project_dir[-4:], {
        "session_id": "S-" + project_dir[-4:],
        "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


@pytest.fixture
def claim(tmp_checkpoint_dir):
    _seed(SENDER)
    recipient = _seed(RECIPIENT)
    q_id = requests.open_request(
        to=recipient, ask="which retry policy", why="wiring a client",
        channel="cli-tty", kind="info", project_dir=SENDER)
    requests.done(q_id, channel="cli-agent", evidence=ANSWER,
                  project_dir=RECIPIENT)
    return q_id


def _out(capsys):
    return capsys.readouterr().out


def test_sender_list_hides_the_pending_evidence_and_says_it_is_pending(
        claim, capsys):
    assert cli.main(["request", "list", "--project", SENDER]) == 0
    out = _out(capsys)
    assert ANSWER not in out
    assert PENDING_LINE in out


def test_sender_json_list_carries_no_pending_evidence(claim, capsys):
    assert cli.main(["request", "list", "--json", "--project", SENDER]) == 0
    raw = _out(capsys)
    assert ANSWER not in raw
    assert json.loads(raw)[0]["done_pending"] is True


def test_every_sender_side_composer_withholds_it(claim):
    for record in (requests.sent_record(claim, project_dir=SENDER),
                   requests.sender_join(project_dir=SENDER)[claim],
                   requests.sent(project_dir=SENDER).by_id[claim],
                   next(r for r in requests.listing(project_dir=SENDER)
                        if r["request_id"] == claim)):
        assert record["done_pending"] is True
        assert record["done_evidence"] == ""
        assert record["done_evidence_withheld"] is True


def test_the_recipient_still_sees_the_text_to_decide_the_accept(
        claim, capsys):
    record = requests.recipient_join(project_dir=RECIPIENT)[claim]
    assert record["done_evidence"] == ANSWER
    assert not record.get("done_evidence_withheld")
    assert cli.main(["request", "inbox", "--project", RECIPIENT]) == 0
    assert ANSWER in _out(capsys)


def test_a_human_accept_lands_the_claim_and_the_sender_sees_the_text(
        claim, tty, capsys):
    requests.accept(claim, channel="cli-tty", project_dir=RECIPIENT)
    record = requests.sent_record(claim, project_dir=SENDER)
    assert record["state"] == "done"
    assert record["done_evidence"] == ANSWER
    assert not record.get("done_evidence_withheld")
    assert cli.main(["request", "list", "--project", SENDER]) == 0
    out = _out(capsys)
    assert ANSWER in out and PENDING_LINE not in out


def test_a_self_addressed_ask_keeps_its_text_the_sender_is_the_recipient(
        tmp_checkpoint_dir):
    own = _seed(SENDER)
    q_id = requests.open_request(
        to=own, ask="a", why="b", channel="cli-tty", project_dir=SENDER)
    requests.done(q_id, channel="cli-agent", evidence=ANSWER,
                  project_dir=SENDER)
    record = requests.sent_record(q_id, project_dir=SENDER)
    assert record["done_evidence"] == ANSWER


def test_a_discarded_claim_stays_withheld_from_the_sender(
        claim, capsys):
    requests.revise(claim, channel="cli-tty", ask="which timeout",
                    project_dir=SENDER)
    record = requests.sent_record(claim, project_dir=SENDER)
    assert record["superseded_claims"]
    assert all(c["evidence"] == "" for c in record["superseded_claims"])
    assert cli.main(["request", "list", "--project", SENDER]) == 0
    assert ANSWER not in _out(capsys)


def test_the_verdict_surfaces_say_pending_and_never_print_the_text(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing
    from daimon_briefing.cli import request as cli_request
    _seed(SENDER)
    recipient = _seed(RECIPIENT)
    q_id = requests.open_request(
        to=recipient, ask="which retry policy", why="wiring a client",
        channel="cli-tty", kind="info", project_dir=SENDER)
    requests.needs_info(q_id, channel="cli-tty", note="which client?",
                        project_dir=RECIPIENT)
    requests.done(q_id, channel="cli-agent", evidence=ANSWER,
                  project_dir=RECIPIENT)
    record = requests.sent_record(q_id, project_dir=SENDER)
    assert record["state"] == "needs-info" and record["done_pending"]
    inject = "\n".join(cli_request._verdict_inject_lines(record))
    panel = "\n".join(briefing.verdict_panel_lines(SENDER))
    for text in (inject, panel):
        assert ANSWER not in text
        assert requests.WITHHELD_CLAIM_LINE in text
