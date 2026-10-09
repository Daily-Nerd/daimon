"""`daimon resolve` binds through `view.match` and never through a raw read
(#1132 PR 11b, D11.5). Stores are written by the real writers; the verb is
driven through `cli.main`."""

import json

import pytest

from daimon_briefing import cli, normalize, schema, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/resolve-withheld"
Q1 = "SENTINEL-q1 release pipeline awaiting manual approval step"
Q2 = "serializer chunk retry budget unclear"
D1 = "SENTINEL-d1 adopt the strangler pattern for the gateway"
KEEP = "an unrelated decision that stays visible"


@pytest.fixture
def tmp_log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"


@pytest.fixture(autouse=True)
def _human(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


def _write(extra_questions=()):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "open_questions": [{"text": Q1, "trust": "inferred"},
                                 {"text": Q2, "trust": "inferred"},
                                 *({"text": t, "trust": "inferred"}
                                   for t in extra_questions)],
              "recent_decisions": [{"text": D1, "trust": "inferred"},
                                   {"text": KEEP, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)


def _id(text):
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    for _fld, item in schema.iter_items(cp):
        if item.get("text") == text:
            return item["id"]
    raise AssertionError(text)


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    key = normalize.content_key(text)
    store.append_event(f"i-gone-{key[:6]}", f"forgotten:{key}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)


def _close_trust():
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")


def _events_bytes():
    path = store.ledger_file(PROJECT, "events.jsonl")
    return path.read_bytes() if path is not None and path.exists() else b""


def _run(capsys, *argv):
    rc = cli.main(["resolve", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


# ---- a quarantined exact id --------------------------------------------------


def test_an_agent_is_refused_a_quarantined_exact_id(tmp_checkpoint_dir, capsys):
    _write()
    qid = _quarantine(Q1)
    rc, out, err = _run(capsys, _id(Q1), "--by", "agent", "--evidence", "it shipped")
    assert rc == 2
    assert f"is withheld (quarantine {qid}); a person must judge a value an " \
        "agent cannot see" in err
    assert "SENTINEL-q1" not in out + err
    assert _events_bytes() == b""


def test_a_person_binds_a_quarantined_exact_id_with_no_echo(
        tmp_checkpoint_dir, capsys):
    _write()
    qid = _quarantine(Q1)
    item_id = _id(Q1)
    rc, out, err = _run(capsys, item_id, "--note", "done")
    assert rc == 0, err
    assert out.strip() == (
        f"resolved {item_id} [question] [withheld: quarantine {qid}] [resolved]")
    assert "SENTINEL-q1" not in out + err
    row = json.loads(_events_bytes().splitlines()[-1])
    assert row["item_ref"] == item_id and row["status"] == "resolved"
    assert "item_text" not in row and b"SENTINEL-q1" not in _events_bytes()
    assert store.is_resolved(store.resolutions(project_dir=PROJECT)[item_id])


def test_a_dry_run_on_a_quarantined_exact_id_prints_the_marker(
        tmp_checkpoint_dir, capsys):
    _write()
    qid = _quarantine(Q1)
    item_id = _id(Q1)
    rc, out, _err = _run(capsys, item_id, "--dry-run")
    assert rc == 0
    assert out.strip() == (
        f"would resolve {item_id} [question] [withheld: quarantine {qid}] "
        "[resolved]")
    assert _events_bytes() == b""


# ---- closed trust ---------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    [], ["--by", "agent", "--evidence", "it shipped"]],
    ids=["human", "agent"])
def test_a_closed_exact_id_is_refused_on_every_channel(
        tmp_checkpoint_dir, capsys, argv):
    _write()
    item_id = _id(Q2)
    _close_trust()
    rc, out, err = _run(capsys, item_id, *argv)
    assert rc == 2
    assert f"error: {item_id} is withheld (trust ledger unreadable); " \
        "daimon trust repair" in err
    assert Q2 not in out + err
    assert _events_bytes() == b""


def test_a_closed_miss_prints_no_count_and_the_cure(tmp_checkpoint_dir, capsys):
    _write()
    _close_trust()
    rc, out, err = _run(capsys, "serializer chunk retry budget")
    assert rc == 1
    assert "withheld item(s)" not in out + err
    assert "daimon trust repair" in out + err


# ---- forgotten --------------------------------------------------------------------


def test_a_forgotten_id_is_not_a_write_target(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(D1)
    _forget(D1)
    before = _events_bytes()
    rc, out, err = _run(capsys, item_id)
    assert rc == 1 and "no item matches" in out
    assert "forgotten" not in out + err and D1 not in out + err
    assert _events_bytes() == before


# ---- never-guess and the deleted fallback ------------------------------------------


def test_a_visible_hit_beside_a_withheld_one_is_refused(tmp_checkpoint_dir, capsys):
    twin = "SENTINEL-q1 release pipeline awaiting manual approval plan"
    _write(extra_questions=[twin])
    _quarantine(Q1)
    rc, out, _err = _run(capsys, "release pipeline awaiting manual approval")
    assert rc == 1
    assert "1 withheld item(s) also match; use the exact id" in out
    assert _id(twin) in out
    assert Q1 not in out
    assert _events_bytes() == b""


def test_a_miss_prints_a_pointer_and_never_every_item(tmp_checkpoint_dir, capsys):
    _write()
    _quarantine(Q1)
    rc, out, _err = _run(capsys, "zzz qqq www")
    assert rc == 1
    assert out.splitlines()[0] == "no item matches 'zzz qqq www'"
    assert "daimon resolve <id>" in out
    for text in (Q1, Q2, D1, KEEP):
        assert text not in out


def test_the_refusals_keep_their_usage_tags(tmp_checkpoint_dir, tmp_log_dir, capsys):
    _write()
    _run(capsys, "zzz qqq www")
    _quarantine(Q1)
    _run(capsys, _id(Q1), "--by", "agent", "--evidence", "x")
    tags = [line.split()[1] for line in
            (tmp_log_dir / "usage.log").read_text().splitlines()]
    assert tags == ["resolve:no-match", "resolve:withheld"]


# ---- the event carries no value ----------------------------------------------------


def test_a_visible_bind_prints_the_text_and_writes_none(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q2)
    rc, out, _err = _run(capsys, item_id)
    assert rc == 0
    assert out.strip() == f"resolved {item_id}: {Q2} [resolved]"
    row = json.loads(_events_bytes().splitlines()[-1])
    assert "item_text" not in row and Q2.encode() not in _events_bytes()


def test_the_agent_claim_writes_no_text_either(tmp_checkpoint_dir, capsys):
    _write()
    item_id = _id(Q2)
    rc, out, _err = _run(capsys, item_id, "--by", "agent", "--evidence", "x")
    assert rc == 0 and f"claim recorded {item_id}: {Q2}" in out
    assert Q2.encode() not in _events_bytes()


def test_no_checkpoint_at_all_keeps_its_own_message(tmp_checkpoint_dir, capsys):
    rc, out, _err = _run(capsys, "anything at all")
    assert rc == 1
    assert out.strip() == ("no checkpoint for this project yet, nothing to "
                           "resolve")
    _write()
    rc, out, _err = _run(capsys, "zzz qqq www")
    assert rc == 1 and out.splitlines()[0] == "no item matches 'zzz qqq www'"
