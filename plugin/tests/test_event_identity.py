"""One stable event identifier, written and read back (the `event_id` every
ledger row already carries).

A host that writes a claim and reads it back must tell its own write from an
earlier claim on the same item, which a second-resolution timestamp cannot
do. The write verbs print the id of the row they wrote (text and `--json`),
and the read side exposes the id of the latest row: `why --json`
(`lifecycle_event`) for a lifecycle event, and the request records the
listing and inbox verbs print.
"""

import json

import pytest

from daimon_briefing import cli, requests, store, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/event-identity"
SENDER = "/p/event-identity-sender"
ITEM = "o-aaaaaaaaaaaa"


def _checkpoint():
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-09-01T10:00:00Z",
        "working_context": {"open_questions": [
            {"text": "which retry budget?", "id": ITEM,
             "trust": "inferred"}]},
    }, project_dir=PROJECT, writer=Writer.HUMAN)


def _run(argv, capsys, monkeypatch, tty=True):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty)
    rc = cli.main(argv)
    return rc, capsys.readouterr()


def test_write_event_returns_the_id_of_the_row_it_wrote(tmp_checkpoint_dir):
    event_id = store.write_event(ITEM, "resolved", source="cli-tty",
                                 project_dir=PROJECT, writer=Writer.HUMAN)
    rows = view.events(PROJECT)
    assert event_id and rows[-1].event_id == event_id


def test_two_writes_in_one_second_get_two_ids(tmp_checkpoint_dir):
    first = store.write_event(ITEM, "resolved", source="agent",
                              project_dir=PROJECT, writer=Writer.HUMAN)
    second = store.write_event(ITEM, "resolved", source="agent",
                               project_dir=PROJECT, writer=Writer.HUMAN)
    assert first != second


def test_a_failed_write_returns_no_id(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert store.write_event(ITEM, "resolved", source="cli-tty",
                             project_dir=PROJECT, writer=Writer.HUMAN) == ""


def test_resolve_prints_the_event_id_and_why_json_exposes_it(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _checkpoint()
    rc, out = _run(["resolve", ITEM, "--project", PROJECT], capsys,
                   monkeypatch)
    assert rc == 0
    printed = [ln.split("event: ", 1)[1] for ln in out.out.splitlines()
               if "event: " in ln]
    assert len(printed) == 1
    rc, out = _run(["why", ITEM, "--json", "--project", PROJECT], capsys,
                   monkeypatch)
    assert rc == 0
    assert json.loads(out.out)["lifecycle_event"]["event_id"] == printed[0]


def test_resolve_json_names_the_event(tmp_checkpoint_dir, monkeypatch,
                                      capsys):
    _checkpoint()
    rc, out = _run(["resolve", ITEM, "--json", "--project", PROJECT], capsys,
                   monkeypatch)
    payload = json.loads(out.out)
    assert rc == 0
    assert payload["item_id"] == ITEM and payload["status"] == "resolved"
    rc, out = _run(["why", ITEM, "--json", "--project", PROJECT], capsys,
                   monkeypatch)
    assert (json.loads(out.out)["lifecycle_event"]["event_id"]
            == payload["event_id"])


def test_an_agent_claim_is_told_apart_from_an_earlier_one(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _checkpoint()
    ids = []
    for _ in range(2):
        rc, out = _run(["resolve", ITEM, "--json", "--by", "agent",
                        "--evidence", "x", "--project", PROJECT], capsys,
                       monkeypatch, tty=False)
        if rc == 0:
            ids.append(json.loads(out.out)["event_id"])
    assert len(set(ids)) == len(ids) >= 1
    rc, out = _run(["why", ITEM, "--json", "--project", PROJECT], capsys,
                   monkeypatch)
    # `lifecycle_event` is the row the fold judges, which is not always the
    # last one written: the id is what tells a host which of its writes won.
    assert json.loads(out.out)["lifecycle_event"]["event_id"] in ids


def test_a_legacy_row_without_an_id_reads_as_null(tmp_checkpoint_dir):
    _checkpoint()
    path = store._events_path(PROJECT)
    path.write_text(json.dumps({
        "ts": "2026-09-01T10:00:00Z", "kind": "resolution",
        "item_ref": ITEM, "status": "resolved", "source": "cli-tty"}) + "\n",
        encoding="utf-8")
    from daimon_briefing import inspector
    got = inspector.inspect_item(PROJECT, ITEM)
    assert got["lifecycle_event"]["event_id"] is None


# ---- requests ------------------------------------------------------------


@pytest.fixture
def offered(tmp_checkpoint_dir):
    return requests.open_request(
        to=store.project_slug(PROJECT), ask="check the retry budget",
        why="it blocks the release", channel="cli-agent", project_dir=SENDER)


def test_a_record_names_the_id_of_the_row_that_last_moved_it(offered):
    accepted = requests.accept(offered, channel="cli-tty",
                               project_dir=PROJECT)
    record = requests.recipient_join(project_dir=PROJECT)[offered]
    assert record["updated_event_id"] == accepted
    replied = requests.reply(offered, "halfway", channel="cli-agent",
                             project_dir=PROJECT)
    record = requests.recipient_join(project_dir=PROJECT)[offered]
    assert record["updated_event_id"] == replied
    done = requests.done(offered, channel="cli-agent", evidence="shipped",
                         project_dir=PROJECT)
    record = requests.recipient_join(project_dir=PROJECT)[offered]
    assert record["updated_event_id"] == done


def test_the_opening_row_is_the_first_event(offered):
    record = requests.get(offered, project_dir=SENDER)
    opened = [r for r in requests.events(project_dir=SENDER)
              if r["event"] == "opened"][0]
    assert record["updated_event_id"] == opened["event_id"]


def test_request_done_prints_the_event_id_in_text_and_json(
        offered, monkeypatch, capsys):
    rc, out = _run(["request", "done", offered, "--evidence", "shipped",
                    "--by", "agent", "--project", PROJECT], capsys,
                   monkeypatch, tty=False)
    assert rc == 0
    rows = [r for r in requests.events(project_dir=PROJECT)
            if r["event"] == "done"]
    assert f"event: {rows[-1]['event_id']}" in out.out
    rc, out = _run(["request", "done", offered, "--evidence", "again",
                    "--by", "agent", "--json", "--project", PROJECT],
                   capsys, monkeypatch, tty=False)
    payload = json.loads(out.out)
    rows = [r for r in requests.events(project_dir=PROJECT)
            if r["event"] == "done"]
    assert rc == 0
    assert payload == {"request_id": offered, "event": "done",
                       "event_id": rows[-1]["event_id"]}


def test_request_reply_prints_the_event_id_in_text_and_json(
        offered, monkeypatch, capsys):
    requests.accept(offered, channel="cli-tty", project_dir=PROJECT)
    rc, out = _run(["request", "reply", offered, "--note", "halfway",
                    "--by", "agent", "--project", PROJECT], capsys,
                   monkeypatch, tty=False)
    assert rc == 0
    rows = [r for r in requests.events(project_dir=PROJECT)
            if r["event"] == "replied"]
    assert f"event: {rows[-1]['event_id']}" in out.out
    rc, out = _run(["request", "reply", offered, "--note", "more",
                    "--by", "agent", "--json", "--project", PROJECT],
                   capsys, monkeypatch, tty=False)
    rows = [r for r in requests.events(project_dir=PROJECT)
            if r["event"] == "replied"]
    assert json.loads(out.out) == {
        "request_id": offered, "event": "replied",
        "event_id": rows[-1]["event_id"]}


def test_inbox_json_rows_carry_the_latest_event_id(offered, monkeypatch,
                                                   capsys):
    accepted = requests.accept(offered, channel="cli-tty",
                               project_dir=PROJECT)
    rc, out = _run(["request", "inbox", "--json", "--project", PROJECT],
                   capsys, monkeypatch)
    rows = json.loads(out.out)
    assert rc == 0
    assert rows[0]["updated_event_id"] == accepted


def test_a_request_record_with_no_event_id_reads_null(tmp_checkpoint_dir):
    q_id = requests.open_request(
        to=store.project_slug(PROJECT), ask="check the retry budget",
        why="it blocks the release", channel="cli-agent", project_dir=SENDER)
    rows = requests.events(project_dir=SENDER)
    for row in rows:
        row.pop("event_id")
    folded = requests.fold(rows, requests.fold_inputs(
        rows, perspective=requests.Perspective.SENDER, project_dir=SENDER))
    assert folded[q_id]["updated_event_id"] is None


def test_attention_rows_print_an_id_but_do_not_move_the_record(offered):
    before = requests.recipient_join(project_dir=PROJECT)[offered]
    suppressed = requests.suppress(offered, channel="cli-tty",
                                   project_dir=PROJECT)
    after = requests.recipient_join(project_dir=PROJECT)[offered]
    assert after["updated_event_id"] == before["updated_event_id"]
    assert after["row_effects"][suppressed]["reason"] == "suppressed"
    level = requests.set_severity(offered, "high", reason="blocks the release",
                                  channel="cli-agent", project_dir=SENDER)
    sent = requests.sender_join(project_dir=SENDER)[offered]
    assert sent["updated_event_id"] != level
    assert sent["row_effects"][level]["reason"] == "severity_set"
