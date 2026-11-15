"""#1346: every request event belongs to one role, the fold applies it only
from that role's bucket, and the sender's own retraction is `withdraw`.

One table (`requests.EVENT_ROLE`) owns who may write what. The fold and the
write path both read it, so a row from the wrong role is inert at read time and
a verb run from the wrong side is refused before a row exists."""

import json
import time

import pytest

from daimon_briefing import config, requests, store
from daimon_briefing.surfaces import Writer

from ._fold import fold_rows

ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"
RECIPIENT = "-p-recipient"
REASON = "the follow-up write failed, so this ask no longer stands"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return "/p/sender"


def _open(project, *, to, ask=ASK, why=WHY, channel="cli-agent"):
    return requests.open_request(to=to, ask=ask, why=why, channel=channel,
                                 project_dir=project)


def _rows(project):
    path = (config.checkpoint_dir() / store.project_slug(project)
            / "requests.jsonl")
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _seed_bucket(project_dir, session="S-bucket"):
    """A real checkpoint bucket, so the join can discover it."""
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _sent(project, rdir="/p/role-recipient", **kwargs):
    """An ask `project` sent to a real bucket; returns (id, recipient dir)."""
    slug = _seed_bucket(rdir)
    return _open(project, to=slug, **kwargs), rdir


def _forge(event, q_id, project_dir, *, channel="cli-tty", **extra):
    """A raw row appended off the write path (the verbs refuse it): what a
    hand-edit, an old build or a hostile writer leaves in a bucket."""
    row = requests._stamp(event, q_id, channel, now_ns=time.time_ns())
    row.update(extra)
    assert requests.append(row, project_dir=project_dir, writer=Writer.HUMAN)
    return row["event_id"]


def _effect(record, event_id):
    return record["row_effects"][event_id]["reason"]


# ---- the table -------------------------------------------------------------


def test_every_event_has_a_role():
    assert set(requests.EVENT_ROLE) == set(requests.EVENTS)
    assert set(requests.EVENT_ROLE.values()) <= {
        requests.ROLE_SENDER, requests.ROLE_RECIPIENT, requests.ROLE_EITHER}


def test_the_role_table_is_the_approved_split():
    by_role = {}
    for event, role in requests.EVENT_ROLE.items():
        by_role.setdefault(role, set()).add(event)
    assert by_role[requests.ROLE_SENDER] == {"opened", "revised", "withdrawn"}
    assert by_role[requests.ROLE_RECIPIENT] == {
        "accepted", "rejected", "needs_info", "done", "suppressed"}
    assert "replied" in by_role[requests.ROLE_EITHER]


# ---- the fold: a wrong-role row is inert, and says why ----------------------


@pytest.mark.parametrize("event,extra", [
    ("accepted", {}), ("rejected", {"note": "no"}),
    ("needs_info", {"note": "which?"}), ("done", {"evidence": "shipped"}),
])
def test_a_verdict_in_the_senders_bucket_is_inert_in_every_view(
        project, event, extra):
    q_id, rdir = _sent(project)
    event_id = _forge(event, q_id, project, **extra)
    sender_view = requests.sent_record(q_id, project_dir=project)
    recipient_view = requests.recipient_join(project_dir=rdir)[q_id]
    local_view = requests.get(q_id, project_dir=project)
    for record in (sender_view, recipient_view, local_view):
        assert record["state"] == "open"
        assert record["verdict_by"] is None
        assert _effect(record, event_id) == "not_from_recipient"
        assert record["row_effects"][event_id]["effect"] == "ignored"


def test_a_suppression_in_the_senders_bucket_is_inert(project):
    q_id, rdir = _sent(project)
    event_id = _forge("suppressed", q_id, project)
    record = requests.recipient_join(project_dir=rdir)[q_id]
    assert record["suppressed"] is False
    assert _effect(record, event_id) == "not_from_recipient"


@pytest.mark.parametrize("event,extra", [
    ("revised", {"ask": "a different ask entirely"}),
    ("withdrawn", {"note": REASON}),
])
def test_a_sender_act_in_the_recipients_bucket_is_inert_in_every_view(
        project, event, extra):
    q_id, rdir = _sent(project)
    event_id = _forge(event, q_id, rdir, **extra)
    for record in (requests.sent_record(q_id, project_dir=project),
                   requests.recipient_join(project_dir=rdir)[q_id]):
        assert record["state"] == "open"
        assert record["ask"] == ASK
        assert record["revision"] == 0
        assert _effect(record, event_id) == "not_from_sender"


def test_the_recipients_own_verdict_still_lands(project):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    assert requests.sent_record(q_id, project_dir=project)["state"] == "accepted"
    assert requests.recipient_join(project_dir=rdir)[q_id]["state"] == "accepted"


def test_a_self_addressed_ask_holds_both_roles(project):
    own = store.project_slug(project)
    q_id = _open(project, to=own)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    assert requests.get(q_id, project_dir=project)["state"] == "accepted"
    requests.done(q_id, channel="cli-tty", evidence="shipped",
                  project_dir=project)
    assert requests.get(q_id, project_dir=project)["state"] == "done"
    other = _open(project, to=own, ask="a second self ask")
    requests.revise(other, channel="cli-tty", why="sharper",
                    project_dir=project)
    requests.withdraw(other, reason=REASON, channel="cli-tty",
                      project_dir=project)
    assert requests.get(other, project_dir=project)["state"] == "withdrawn"


def test_the_fold_reads_origin_for_hand_built_rows():
    q_id, base = "q-0000000000b1", 5 * 10**18
    opened = requests._stamp("opened", q_id, "cli-agent", now_ns=base)
    opened.update(to=RECIPIENT, ask=ASK, why=WHY, _origin_slug="-p-sender")
    mine = requests._stamp("accepted", q_id, "cli-tty", now_ns=base + 1)
    mine["_origin_slug"] = RECIPIENT
    sender = requests._stamp("rejected", q_id, "cli-tty", now_ns=base + 2)
    sender["_origin_slug"] = "-p-sender"
    record = fold_rows([opened, mine, sender])[q_id]
    assert record["state"] == "accepted"
    assert _effect(record, sender["event_id"]) == "not_from_recipient"


# ---- the write path reads the same table ------------------------------------


@pytest.mark.parametrize("verb,kwargs", [
    ("accept", {}), ("reject", {"note": "no"}),
    ("needs_info", {"note": "which?"}), ("suppress", {}),
    ("done", {"evidence": "shipped"}),
])
def test_a_sender_cannot_decide_its_own_ask(project, verb, kwargs):
    q_id, _ = _sent(project)
    before = _rows(project)
    with pytest.raises(requests.RequestError, match="withdraw"):
        getattr(requests, verb)(q_id, channel="cli-tty",
                                project_dir=project, **kwargs)
    assert _rows(project) == before


def test_a_recipient_cannot_revise_or_withdraw_what_it_was_sent(project):
    q_id, rdir = _sent(project)
    with pytest.raises(requests.RequestError):
        requests.revise(q_id, channel="cli-tty", why="mine now",
                        project_dir=rdir)
    with pytest.raises(requests.RequestError, match="sender"):
        requests.withdraw(q_id, reason=REASON, channel="cli-agent",
                          project_dir=rdir)


# ---- withdraw ---------------------------------------------------------------


def _assert_withdrawn(record, *, by="agent"):
    assert record["state"] == "withdrawn"
    assert record["withdrawn_by"] == by
    assert record["withdrawn_at"]
    assert record["note"] == REASON


def _withdraw(project, q_id, channel="cli-agent"):
    return requests.withdraw(q_id, reason=REASON, channel=channel,
                             project_dir=project)


def test_an_agent_sender_withdraws_an_open_ask(project):
    q_id, rdir = _sent(project)
    event_id = _withdraw(project, q_id)
    for record in (requests.sent_record(q_id, project_dir=project),
                   requests.recipient_join(project_dir=rdir)[q_id]):
        _assert_withdrawn(record)
        assert _effect(record, event_id) == "withdrawn"
        # A retraction is not a verdict: nothing says the recipient decided.
        assert record["verdict_by"] is None
        assert record["verdict_label"] is None
        assert record["verdict_at"] is None


def test_a_human_sender_withdraws_and_it_is_recorded_as_human(project):
    q_id, _ = _sent(project)
    _withdraw(project, q_id, channel="cli-tty")
    _assert_withdrawn(requests.sent_record(q_id, project_dir=project),
                      by="human")


def test_withdraw_takes_a_needs_info_ask(project):
    q_id, rdir = _sent(project)
    requests.needs_info(q_id, channel="cli-tty", note="which?",
                        project_dir=rdir)
    _withdraw(project, q_id)
    _assert_withdrawn(requests.sent_record(q_id, project_dir=project))


def test_withdraw_takes_an_accepted_ask_and_remembers_it_was_accepted(project):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _withdraw(project, q_id)
    record = requests.recipient_join(project_dir=rdir)[q_id]
    _assert_withdrawn(record)
    assert record["withdrawn_from"] == "accepted"
    assert record["verdict_by"] == "human"  # still the recipient's own accept


def test_withdraw_keeps_a_pending_completion_claim_on_the_record(project):
    q_id, rdir = _sent(project)
    requests.done(q_id, channel="cli-agent", evidence="done in abc123",
                  project_dir=rdir)
    assert requests.recipient_join(project_dir=rdir)[q_id]["done_pending"]
    event_id = _withdraw(project, q_id)
    record = requests.recipient_join(project_dir=rdir)[q_id]
    _assert_withdrawn(record)
    assert record["done_pending"] is False
    assert [c["evidence"] for c in record["superseded_claims"]] == [
        "done in abc123"]
    assert record["row_effects"][event_id]["effect"] == "discarded_claim"


@pytest.mark.parametrize("settle", ["done", "rejected", "withdrawn"])
def test_withdraw_on_a_settled_ask_changes_nothing_and_says_why(project, settle):
    q_id, rdir = _sent(project)
    if settle == "done":
        requests.done(q_id, channel="cli-tty", evidence="shipped",
                      project_dir=rdir)
    elif settle == "rejected":
        requests.reject(q_id, channel="cli-tty", note="no", project_dir=rdir)
    else:
        _withdraw(project, q_id)
    before = _rows(project)
    with pytest.raises(requests.RequestError, match=settle):
        _withdraw(project, q_id)
    assert _rows(project) == before
    # The same row forged past the verb is inert in the fold, with a reason.
    event_id = _forge("withdrawn", q_id, project, note=REASON)
    record = requests.sent_record(q_id, project_dir=project)
    assert record["state"] == settle
    assert record["row_effects"][event_id]["effect"] == "ignored"
    assert _effect(record, event_id) == (
        "already_withdrawn" if settle == "withdrawn" else "not_withdrawable")


@pytest.mark.parametrize("event,extra", [
    ("accepted", {}), ("rejected", {"note": "no"}),
    ("needs_info", {"note": "?"}), ("done", {"evidence": "x"}),
])
def test_a_recipient_row_after_a_withdraw_is_ignored(project, event, extra):
    q_id, rdir = _sent(project)
    _withdraw(project, q_id)
    event_id = _forge(event, q_id, rdir, **extra)
    record = requests.recipient_join(project_dir=rdir)[q_id]
    assert record["state"] == "withdrawn"
    assert _effect(record, event_id) == "record_withdrawn"


def test_a_withdrawn_ask_is_not_revised_or_replied_to(project):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _withdraw(project, q_id)
    with pytest.raises(requests.RequestError, match="withdrawn"):
        requests.revise(q_id, channel="cli-tty", why="more",
                        project_dir=project)
    with pytest.raises(requests.RequestError, match="withdrawn"):
        requests.reply(q_id, "progress", channel="cli-agent",
                       project_dir=rdir)


def test_a_withdrawn_ask_leaves_every_lane(project):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    open_id = _open(project, to=store.project_slug(rdir), ask="a second ask")
    assert [r["request_id"] for r in
            requests.decision_renderable(rdir)["rows"]] == [open_id]
    assert [r["request_id"] for r in
            requests.owed_renderable(rdir)["rows"]] == [q_id]
    _withdraw(project, q_id)
    _withdraw(project, open_id)
    assert requests.decision_renderable(rdir)["rows"] == []
    assert requests.owed_renderable(rdir)["rows"] == []
    assert requests.verdict_renderable(project)["rows"] == []
    for record in requests.recipient_join(project_dir=rdir).values():
        assert record["state"] == "withdrawn"
        assert requests.is_overdue(record, now_ns=10**19) is False
    assert requests.status_counts(project_dir=project)["open_sent"] == 0


# ---- ties ------------------------------------------------------------------


def _same_instant(events):
    """Hand-built rows at ONE `order`: founder in the sender's bucket, each
    later row in the bucket its role names."""
    q_id, base = "q-0000000000c1", 5 * 10**18
    opened = requests._stamp("opened", q_id, "cli-agent", now_ns=base)
    opened.update(to=RECIPIENT, ask=ASK, why=WHY, _origin_slug="-p-sender")
    rows = [opened]
    for i, (event, origin, channel, extra) in enumerate(events):
        row = requests._stamp(event, q_id, channel, now_ns=base + 1,
                              event_id=f"{i:032x}")
        row["_origin_slug"] = origin
        row.update(extra)
        rows.append(row)
    return fold_rows(list(reversed(rows)))[q_id]


def test_withdraw_ranks_after_accepted_done_and_rejected():
    rank = requests._EVENT_RANK
    assert rank["withdrawn"] > rank["rejected"] > rank["accepted"]
    assert rank["withdrawn"] > rank["done"]
    assert rank["replied"] > rank["withdrawn"]


def test_a_same_instant_accept_and_withdraw_ends_withdrawn():
    record = _same_instant([
        ("accepted", RECIPIENT, "cli-tty", {}),
        ("withdrawn", "-p-sender", "cli-agent", {"note": REASON})])
    assert record["state"] == "withdrawn"


@pytest.mark.parametrize("event,extra,final", [
    ("done", {"evidence": "shipped"}, "done"),
    ("rejected", {"note": "no"}, "rejected"),
])
def test_a_same_instant_finish_beats_the_withdraw(event, extra, final):
    record = _same_instant([
        (event, RECIPIENT, "cli-tty", extra),
        ("withdrawn", "-p-sender", "cli-agent", {"note": REASON})])
    assert record["state"] == final


# ---- the recipient is told once, through live delivery ----------------------


def _inject(project_dir, session="S-alpha"):
    from daimon_briefing import cli
    return cli.main(["request-inject", "--project", project_dir,
                     "--session", session])


def test_the_recipient_of_an_accepted_ask_is_told_once(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _withdraw(project, q_id)
    capsys.readouterr()
    assert _inject(rdir) == 0
    out = capsys.readouterr().out
    assert q_id in out
    assert f"withdrawn by the sender: {REASON}" in out
    assert _inject(rdir) == 0  # stamped for this session: silent
    assert capsys.readouterr().out == ""
    assert _inject(rdir, "S-beta") == 0  # a session that never heard of it
    assert f"withdrawn by the sender: {REASON}" in capsys.readouterr().out


def test_an_ask_that_was_never_accepted_withdraws_quietly(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    q_id, rdir = _sent(project)
    _withdraw(project, q_id)
    capsys.readouterr()
    assert _inject(rdir) == 0
    assert capsys.readouterr().out == ""
    assert requests.withdrawn_deliverable("S-alpha", rdir)["rows"] == []


def test_the_withdrawn_notice_expires_and_needs_a_session(project, monkeypatch):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _withdraw(project, q_id)
    assert requests.withdrawn_deliverable("", rdir)["rows"] == []
    assert len(requests.withdrawn_deliverable("S-1", rdir)["rows"]) == 1
    monkeypatch.setattr(store, "sessions_since_count",
                        lambda *a, **k: requests.VERDICT_PANEL_SESSIONS)
    assert requests.withdrawn_deliverable("S-1", rdir)["rows"] == []
    with pytest.raises(requests.RequestError):
        requests.stamp_withdrawn_delivered(q_id, "", rdir)


# ---- the CLI ----------------------------------------------------------------


def test_cli_withdraw_records_it_and_says_what_the_fold_did(project, capsys):
    from daimon_briefing import cli
    q_id, _ = _sent(project)
    capsys.readouterr()
    rc = cli.main(["request", "withdraw", q_id, "--reason", REASON,
                   "--by", "agent", "--project", project])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[↩ withdrawn" in out
    assert f"Withdrawn by the sender: {REASON}" in out
    assert f"{q_id}: withdrawn; final for this request" in out
    assert requests.sent_record(q_id, project_dir=project)["state"] == (
        "withdrawn")


def test_cli_withdraw_needs_a_reason(project, capsys):
    from daimon_briefing import cli
    q_id, _ = _sent(project)
    with pytest.raises(SystemExit):
        cli.main(["request", "withdraw", q_id, "--by", "agent",
                  "--project", project])


def test_cli_withdraw_of_a_finished_ask_says_why_and_writes_nothing(
        project, capsys):
    from daimon_briefing import cli
    q_id, rdir = _sent(project)
    requests.done(q_id, channel="cli-tty", evidence="shipped",
                  project_dir=rdir)
    before = _rows(project)
    capsys.readouterr()
    rc = cli.main(["request", "withdraw", q_id, "--reason", REASON,
                   "--by", "agent", "--project", project])
    assert rc == 1
    assert "is done" in capsys.readouterr().out
    assert _rows(project) == before


def test_cli_reject_by_the_sender_is_refused_and_points_at_withdraw(
        project, capsys, monkeypatch):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    q_id, _ = _sent(project)
    before = _rows(project)
    capsys.readouterr()
    rc = cli.main(["request", "reject", q_id, "--note", "nope",
                   "--project", project])
    assert rc == 1
    assert "daimon request withdraw" in capsys.readouterr().out
    assert _rows(project) == before


def test_a_recipient_cli_withdraw_is_refused(project, capsys):
    from daimon_briefing import cli
    q_id, rdir = _sent(project)
    capsys.readouterr()
    rc = cli.main(["request", "withdraw", q_id, "--reason", REASON,
                   "--by", "agent", "--project", rdir])
    assert rc == 1
    assert "only its sender" in capsys.readouterr().out


def test_more_withdrawals_than_the_cap_are_counted_not_dropped(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    rdir = "/p/role-recipient"
    for n in range(requests.RENDER_CAP + 1):
        q_id, _ = _sent(project, rdir, ask=f"{ASK} number {n}")
        requests.accept(q_id, channel="cli-tty", project_dir=rdir)
        _withdraw(project, q_id)
    capsys.readouterr()
    assert _inject(rdir) == 0
    assert "(+1 more withdrawn, not shown here)" in capsys.readouterr().out


def test_withdraw_of_an_unknown_id_is_refused_and_writes_nothing(project):
    with pytest.raises(requests.RequestError, match="unknown request"):
        requests.withdraw("q-000000000000", reason=REASON,
                          channel="cli-agent", project_dir=project)


def test_a_withdrawal_that_cannot_be_appended_is_an_error(project, monkeypatch):
    q_id, _ = _sent(project)
    monkeypatch.setattr(requests, "append", lambda *a, **k: False)
    with pytest.raises(requests.RequestError, match="not written"):
        _withdraw(project, q_id)


def test_cli_withdraw_still_reports_when_the_record_cannot_be_reread(
        project, capsys, monkeypatch):
    from daimon_briefing import cli
    q_id, _ = _sent(project)
    real = requests.sent_record
    seen = []

    def vanishing(request_id, project_dir=None):
        seen.append(request_id)
        # The verb's own read finds it; the card's read after the write races.
        return real(request_id, project_dir=project_dir) if len(seen) == 1 \
            else None

    monkeypatch.setattr(requests, "sent_record", vanishing)
    rc = cli.main(["request", "withdraw", q_id, "--reason", REASON,
                   "--by", "agent", "--project", project])
    assert rc == 0
    assert f"{q_id}: withdrawal recorded" in capsys.readouterr().out
