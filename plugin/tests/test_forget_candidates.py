"""`daimon forget` candidate display and gates (#1132 PR 11b, D11.5, H6).

The pool builder and the deleters are the write layer's and are untouched; what
changed is what the verb PRINTS and who may bind what: a candidate row is
judged through `view.label` (checkpoint rows by `classify`, ledger rows by the
printed subject AND the matched value), a quarantined target binds on a person's
channel only, a closed one binds on every channel (the deletion promise
outranks the outage), a forgotten row is never named."""

import json

import pytest

from daimon_briefing import cli, normalize, refutations, schema, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-candidates"
Q1 = "SENTINEL-q1 migration owner rollback plan unknown"
VISIBLE = "migration owner rollback plan needs review"
OTHER = "serializer chunk retry budget unclear"
DEC = "SENTINEL-d1 adopt the strangler pattern for the gateway"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)


def _tty(monkeypatch, value):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: value, raising=False)


def _write():
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "open_questions": [{"text": Q1, "trust": "inferred"},
                                 {"text": VISIBLE, "trust": "inferred"},
                                 {"text": OTHER, "trust": "inferred"}],
              "recent_decisions": [{"text": DEC, "trust": "inferred"}]}}
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


def _close_trust():
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")


def _run(capsys, *argv):
    rc = cli.main(["forget", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def _held(text):
    for path in store.project_surfaces(PROJECT):
        if text in path.read_text(encoding="utf-8"):
            return True
    return False


# ---- a miss and an ambiguous query ----------------------------------------------


def test_a_miss_prints_a_pointer_and_never_the_pools(tmp_checkpoint_dir, capsys,
                                                     monkeypatch):
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    refutations.assert_refutation(
        subject="a standing refutation", verdict=Q1, scope="s",
        evidence=["measurement:replay"], channel="cli-tty",
        project_dir=PROJECT)
    rc, out, _err = _run(capsys, "zzz qqq www")
    assert rc == 1
    assert out.splitlines()[0] == "no item matches 'zzz qqq www'"
    assert "forget by exact id: daimon forget <id>" in out
    for text in (Q1, VISIBLE, OTHER, DEC, "a standing refutation"):
        assert text not in out


def test_a_visible_hit_beside_a_quarantined_one_is_refused_and_counted(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    rc, out, _err = _run(capsys, "migration owner rollback plan")
    assert rc == 1
    assert f"  {_id(VISIBLE)}  [open_questions] {VISIBLE}" in out
    assert "1 withheld item(s) also match; use the exact id" in out
    assert Q1 not in out and _id(Q1) not in out
    assert _held(VISIBLE) and _held(Q1)               # nothing was removed


def test_a_query_that_only_a_quarantined_item_matches_is_counted_not_named(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    rc, out, _err = _run(capsys, "SENTINEL-q1 unknown")
    assert rc == 1
    assert "no visible item matches 'SENTINEL-q1 unknown'" in out
    assert "1 withheld item(s) also match" in out
    assert Q1 not in out and _id(Q1) not in out


def test_a_ledger_row_is_judged_by_its_subject(tmp_checkpoint_dir, capsys,
                                                monkeypatch):
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    rid = refutations.assert_refutation(
        subject=Q1, verdict="measured and refuted", scope="s",
        evidence=["measurement:replay"], channel="cli-tty",
        project_dir=PROJECT)
    rc, out, _err = _run(capsys, "rollback plan unknown")
    assert rc == 1
    assert rid not in out and Q1 not in out
    assert "withheld item(s) also match" in out


def test_a_ledger_row_is_judged_by_the_value_the_query_matched(
        tmp_checkpoint_dir, capsys, monkeypatch):
    """The subject is harmless; the value the query named is a quarantined one."""
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    rid = refutations.assert_refutation(
        subject="a harmless subject", verdict=Q1, scope="s",
        evidence=["measurement:replay"], channel="cli-tty",
        project_dir=PROJECT)
    rc, out, _err = _run(capsys, "migration owner rollback plan unknown")
    assert rc == 1
    assert rid not in out and "a harmless subject" not in out and Q1 not in out
    assert "withheld item(s) also match" in out


def test_a_forgotten_residue_row_is_in_no_line_and_no_count(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _tty(monkeypatch, True)
    _write()
    key = normalize.content_key(DEC)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT, writer=Writer.HUMAN)
    rc, out, _err = _run(capsys, "strangler pattern gateway")
    assert rc == 1
    assert out.splitlines()[0] == "no item matches 'strangler pattern gateway'"
    assert "withheld" not in out and DEC not in out


# ---- an exact id ------------------------------------------------------------------


def test_a_quarantined_exact_id_is_refused_to_an_agent(tmp_checkpoint_dir, capsys,
                                                       monkeypatch):
    _tty(monkeypatch, False)
    _write()
    qid = _quarantine(Q1)
    rc, out, err = _run(capsys, _id(Q1), "--dry-run")
    assert rc == 2
    assert f"is withheld (quarantine {qid}); a person must judge a value an " \
        "agent cannot see" in err
    assert Q1 not in out + err
    rc, out, err = _run(capsys, _id(Q1))
    assert rc == 2 and _held(Q1)


def test_a_person_forgets_a_quarantined_exact_id_and_sees_no_text(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _tty(monkeypatch, True)
    _write()
    qid = _quarantine(Q1)
    item_id = _id(Q1)
    rc, out, _err = _run(capsys, item_id, "--dry-run")
    assert rc == 0
    assert out.splitlines()[0] == (
        f"would forget {item_id} [open_questions] [withheld: quarantine {qid}]")
    assert Q1 not in out
    rc, out, err = _run(capsys, item_id)
    assert rc == 0, err
    assert out.startswith(f"forgot {item_id} (content hash "
                          f"{normalize.content_key(Q1)})")
    assert Q1 not in out
    assert not _held(Q1)


def test_a_closed_exact_id_still_works_on_every_channel(tmp_checkpoint_dir,
                                                        capsys, monkeypatch):
    _tty(monkeypatch, False)
    _write()
    item_id = _id(VISIBLE)
    _close_trust()
    rc, out, _err = _run(capsys, item_id, "--dry-run")
    assert rc == 0
    assert out.splitlines()[0] == (
        f"would forget {item_id} [open_questions] "
        "[withheld: trust ledger unreadable]")
    assert VISIBLE not in out
    rc, out, err = _run(capsys, item_id)
    # exit 4: forget scrubbed what it could read and says the trust ledger it
    # could not read was not reached
    assert rc == 4, err
    assert out.startswith(f"forgot {item_id} (content hash ")
    assert not _held(VISIBLE)


def test_a_forgotten_exact_id_is_no_match_to_an_agent_and_a_target_to_a_person(
        tmp_checkpoint_dir, capsys, monkeypatch):
    """The id is tombstoned while the value is still on disk (a sibling-id
    shape): a person can finish the deletion, an agent learns nothing."""
    _write()
    item_id = _id(DEC)
    store.append_event(item_id, "forgotten:" + normalize.content_key("other"),
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)
    _tty(monkeypatch, False)
    rc, out, err = _run(capsys, item_id)
    assert rc == 1 and out.splitlines()[0] == f"no item matches {item_id!r}"
    assert "forgotten" not in out + err and DEC not in out + err
    assert _held(DEC)
    _tty(monkeypatch, True)
    rc, out, _err = _run(capsys, item_id, "--dry-run")
    assert rc == 0
    assert out.splitlines()[0] == (
        f"would forget {item_id} [recent_decisions] [withheld: forgotten]")
    assert DEC not in out


# ---- what did not change -----------------------------------------------------------


def test_a_visible_target_keeps_its_preview_and_its_receipt(tmp_checkpoint_dir,
                                                            capsys, monkeypatch):
    _tty(monkeypatch, True)
    _write()
    item_id = _id(OTHER)
    rc, out, _err = _run(capsys, item_id, "--dry-run")
    assert rc == 0 and out.splitlines()[0] == f"would forget {item_id}: {OTHER}"
    rc, out, _err = _run(capsys, item_id)
    assert rc == 0
    assert out.startswith(
        f"forgot {item_id} (content hash {normalize.content_key(OTHER)})")
    assert not _held(OTHER)
    row = json.loads(store.ledger_file(PROJECT, "events.jsonl")
                     .read_text().splitlines()[-1])
    assert row["status"] == f"forgotten:{normalize.content_key(OTHER)}"


def test_a_fuzzy_query_with_one_visible_hit_binds_it(tmp_checkpoint_dir, capsys,
                                                      monkeypatch):
    _tty(monkeypatch, True)
    _write()
    rc, out, _err = _run(capsys, "serializer chunk retry budget", "--dry-run")
    assert rc == 0 and f"would forget {_id(OTHER)}: {OTHER}" in out


def test_the_usage_tags_are_kept(tmp_checkpoint_dir, tmp_log_dir, capsys,
                                 monkeypatch):
    _tty(monkeypatch, True)
    _write()
    _quarantine(Q1)
    _run(capsys, "zzz qqq www")
    _run(capsys, "migration owner rollback plan")
    tags = [line.split()[1] for line in
            (tmp_log_dir / "usage.log").read_text().splitlines()]
    assert tags == ["forget:no-match", "forget:ambiguous"]


@pytest.fixture
def tmp_log_dir(tmp_path):
    return tmp_path / ".daimon" / "logs"
