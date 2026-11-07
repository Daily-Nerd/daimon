"""Receiver consent for info asks.

Answering an `info` ask exposes what the RECEIVING project knows, so an agent
on the receiving side may accept or answer one only under a `verb=accept`
ruling a person of that project ratified (or after a person accepts it).
One rule for `info` and `work`, enforced by the same predicate at the write
boundary and in the fold, on every agent channel.
"""

import pytest

from daimon_briefing import (authority, clock, config, pending, refutations, requests,
                             store)
from daimon_briefing.surfaces import Writer

ASK = "which retry policy does the exporter use"
WHY = "the sender is wiring a client against it"

AGENT_CHANNELS = sorted(
    c for c, a in authority.table("requests").items() if a == "agent")


@pytest.fixture
def recipient(tmp_checkpoint_dir):
    return "/p/consent-recipient"


def _seed_bucket(project_dir, session="S-bucket"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _info_ask(recipient, sender_dir="/p/consent-sender"):
    sender = _seed_bucket(sender_dir)
    q_id = requests.open_request(
        to=store.project_slug(recipient), ask=ASK, why=WHY,
        channel="cli-tty", kind="info", project_dir=sender)
    return q_id, sender


def _cover(recipient, sender_slug, *, kind="info"):
    ruling_id = refutations.assert_ruling(
        subject=f"{kind} asks from {sender_slug}",
        verdict=f"agent may accept {kind} asks from {sender_slug}",
        scope="cross-project requests", evidence=["issue:1"],
        channel="cli-tty", ratified=True,
        request_policy={"sender": sender_slug, "kind": kind,
                        "verb": "accept", "by": "agent"},
        project_dir=recipient)
    return ruling_id


def _record(recipient, q_id):
    return requests.recipient_join(project_dir=recipient)[q_id]


# (a) no receiver policy: refused, still open, the refusal names what to do


@pytest.mark.parametrize("channel", AGENT_CHANNELS)
def test_agent_accept_of_an_info_ask_without_a_receiver_ruling_is_refused(
        recipient, channel):
    q_id, sender = _info_ask(recipient)
    with pytest.raises(requests.RequestError) as exc:
        requests.accept(q_id, channel=channel, project_dir=recipient)
    text = str(exc.value)
    assert sender in text
    assert "verb=accept" in text and "kind=info" in text
    assert f"daimon request accept {q_id}" in text
    assert _record(recipient, q_id)["state"] == "open"


# (b) + (c): the ruling the vocabulary already validates now does something


def test_agent_accept_of_an_info_ask_under_a_receiver_ruling_lands(recipient):
    q_id, sender = _info_ask(recipient)
    ruling_id = _cover(recipient, sender)
    requests.accept(q_id, channel="cli-agent", project_dir=recipient)
    record = _record(recipient, q_id)
    assert record["state"] == "accepted"
    assert record["accepted_by"] == "agent"
    assert record["accepted_under"] == ruling_id


def test_a_receiver_ruling_names_one_exact_sender(recipient):
    q_id, _sender = _info_ask(recipient)
    other = _seed_bucket("/p/consent-other")
    _cover(recipient, other)
    with pytest.raises(requests.RequestError):
        requests.accept(q_id, channel="cli-agent", project_dir=recipient)
    assert _record(recipient, q_id)["state"] == "open"


def test_a_work_ruling_does_not_cover_an_info_ask(recipient):
    q_id, sender = _info_ask(recipient)
    _cover(recipient, sender, kind="work")
    with pytest.raises(requests.RequestError):
        requests.accept(q_id, channel="cli-agent", project_dir=recipient)


def test_a_human_accept_of_an_info_ask_needs_no_ruling(recipient):
    q_id, _sender = _info_ask(recipient)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient)
    record = _record(recipient, q_id)
    assert record["state"] == "accepted"
    assert record["accepted_by"] == "human"


def test_reply_on_an_info_ask_needs_the_consenting_accept_first(recipient):
    q_id, sender = _info_ask(recipient)
    with pytest.raises(requests.RequestError):
        requests.reply(q_id, "the answer", channel="cli-agent",
                       project_dir=recipient)
    _cover(recipient, sender)
    requests.accept(q_id, channel="cli-agent", project_dir=recipient)
    requests.reply(q_id, "the answer", channel="cli-agent",
                   project_dir=recipient)
    assert _record(recipient, q_id)["state"] == "accepted"


# (d) done cannot skip the consent


def test_agent_done_on_an_open_info_ask_is_only_a_pending_claim(recipient):
    q_id, _sender = _info_ask(recipient)
    requests.done(q_id, channel="cli-agent", evidence="the answer is 3",
                  project_dir=recipient)
    record = _record(recipient, q_id)
    assert record["state"] == "open"
    assert record["done_pending"] is True


def test_agent_done_after_a_consenting_accept_closes_the_info_ask(recipient):
    q_id, sender = _info_ask(recipient)
    _cover(recipient, sender)
    requests.accept(q_id, channel="cli-agent", project_dir=recipient)
    requests.done(q_id, channel="cli-agent", evidence="the answer is 3",
                  project_dir=recipient)
    record = _record(recipient, q_id)
    assert record["state"] == "done"
    assert record["done_pending"] is False


def test_human_done_on_an_open_info_ask_still_lands(recipient):
    q_id, _sender = _info_ask(recipient)
    requests.done(q_id, channel="cli-tty", evidence="answered by hand",
                  project_dir=recipient)
    assert _record(recipient, q_id)["state"] == "done"


# (f) a hand-appended agent row is inert in the fold


def test_a_forged_agent_accepted_row_on_an_info_ask_is_inert(recipient):
    q_id, _sender = _info_ask(recipient)
    row = requests._stamp("accepted", q_id, "cli-agent")
    assert requests.append(row, project_dir=recipient, writer=Writer.HUMAN)
    record = _record(recipient, q_id)
    assert record["state"] == "open"
    assert record["accepted_by"] is None


def test_a_forged_agent_accepted_row_naming_a_ruling_that_is_not_there_is_inert(
        recipient):
    q_id, _sender = _info_ask(recipient)
    row = requests._stamp("accepted", q_id, "cli-agent")
    row["under_ruling"] = "r-000000000000"
    row["policy_sha256"] = "0" * 64
    assert requests.append(row, project_dir=recipient, writer=Writer.HUMAN)
    assert _record(recipient, q_id)["state"] == "open"


# (e) the order anchor: a verdict written before the rule keeps its state


def _legacy_scenario(recipient, event):
    sender = _seed_bucket("/p/consent-legacy-sender")
    before = requests.RECEIVER_CONSENT_FROM_NS - 1_000_000
    q_id = "q-0123456789ab"
    opened = requests._stamp("opened", q_id, "cli-tty", now_ns=before - 10)
    opened.update({"to": store.project_slug(recipient), "to_human": False,
                   "blocking": False, "kind": "info", "ask": ASK, "why": WHY,
                   "from_label": "sender"})
    assert requests.append(opened, project_dir=sender, writer=Writer.HUMAN)
    row = requests._stamp(event, q_id, "cli-agent", now_ns=before)
    if event == "done":
        row["evidence"] = "answered before the rule"
    assert requests.append(row, project_dir=recipient, writer=Writer.HUMAN)
    return q_id


def test_an_agent_accepted_row_older_than_the_rule_keeps_landing(recipient):
    q_id = _legacy_scenario(recipient, "accepted")
    record = _record(recipient, q_id)
    assert record["state"] == "accepted"
    assert record["accepted_by"] == "agent"


def test_an_agent_done_row_older_than_the_rule_keeps_landing(recipient):
    q_id = _legacy_scenario(recipient, "done")
    record = _record(recipient, q_id)
    assert record["state"] == "done"
    assert record["done_pending"] is False


def test_the_anchor_is_a_fixed_instant_not_a_moving_one():
    assert isinstance(requests.RECEIVER_CONSENT_FROM_NS, int)
    assert requests.RECEIVER_CONSENT_FROM_NS < clock.now_ns()


# the unasked half: an uncovered info ask waits in the receiver's human queue


def test_an_uncovered_info_ask_waits_in_the_receivers_human_queue(recipient):
    q_id, _sender = _info_ask(recipient)
    assert [r["id"] for r in pending.queue(project_dir=recipient)["rows"]] \
        == [q_id]
    rows = requests.decision_renderable(project_dir=recipient)["rows"]
    assert [r["request_id"] for r in rows] == [q_id]


def test_foreign_counts_includes_an_uncovered_info_ask(recipient):
    _q_id, _sender = _info_ask(recipient)
    counts = pending.foreign_counts(project_dir="/p/consent-elsewhere")
    assert counts.get(store.project_slug(recipient), 0) == 1


# (g) tenant scope: a recipient outside the caller's scope is refused


def test_tenant_scope_refuses_a_recipient_outside_the_callers_scope(
        tmp_checkpoint_dir, monkeypatch):
    sender_dir = "/p/tenant-a"
    _seed_bucket(sender_dir)
    other = _seed_bucket("/p/tenant-b")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    with pytest.raises(requests.RequestError) as exc:
        requests.open_request(to=other, ask=ASK, why=WHY, channel="cli-tty",
                              project_dir=sender_dir)
    assert config.TENANT_SCOPE_REFUSAL in str(exc.value)
    assert requests.records(project_dir=sender_dir) == {}


def test_tenant_scope_allows_own_slug_and_host_declared_extra_scopes(
        tmp_checkpoint_dir, monkeypatch):
    sender_dir = "/p/tenant-a"
    own = _seed_bucket(sender_dir)
    shared = _seed_bucket("/p/tenant-shared")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("DAIMON_EXTRA_READ_SLUGS", shared)
    for n, to in enumerate((own, shared)):
        assert requests.open_request(to=to, ask=f"{ASK} {n}", why=WHY,
                                     channel="cli-tty",
                                     project_dir=sender_dir)


def test_without_tenant_scope_any_recipient_is_still_addressable(
        tmp_checkpoint_dir):
    sender_dir = "/p/tenant-a"
    _seed_bucket(sender_dir)
    other = _seed_bucket("/p/tenant-b")
    assert requests.open_request(to=other, ask=ASK, why=WHY,
                                 channel="cli-tty", project_dir=sender_dir)


@pytest.mark.parametrize("extra", [[], ["--anyway"]])
def test_cli_open_refuses_another_tenants_slug_and_suggests_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys, extra):
    from daimon_briefing import cli
    sender_dir = "/p/tenant-a"
    _seed_bucket(sender_dir)
    other = _seed_bucket("/p/tenant-b")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender_dir)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    rc = cli.main(["request", "open", f"--to={other}", "--ask", ASK,
                   "--why", WHY, "--project", sender_dir, *extra])
    out = capsys.readouterr().out
    assert rc == 1
    assert config.TENANT_SCOPE_REFUSAL in out
    assert "did you mean" not in out
    assert requests.records(project_dir=sender_dir) == {}


@pytest.mark.parametrize("order,expected", [
    (requests.RECEIVER_CONSENT_FROM_NS - 1, True),
    (requests.RECEIVER_CONSENT_FROM_NS, False),
    (requests.RECEIVER_CONSENT_FROM_NS + 1, False),
    (0, False), (None, False), ("not a number", False), (-5, False),
])
def test_only_a_positive_order_before_the_anchor_keeps_the_old_rule(
        order, expected):
    row = {} if order is None else {"order": order}
    assert requests._before_receiver_consent(row) is expected
