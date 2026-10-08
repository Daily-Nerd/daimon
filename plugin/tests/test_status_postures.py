"""`status` names the cure and the path (#1132 PR 10a, D10.6).

Every ledger line that is not "ok" ends with what to do (by state) and where
the file is. The human's own `status` may name paths; the notes every other
surface prints may not.
"""


from daimon_briefing import cli, config, jsonl, store, trust
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import Writer

PROJECT = "/p/status-postures"


def _ledger(name):
    return config.checkpoint_dir() / store.project_slug(PROJECT) / name


def _seed():
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "a decision", "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT, writer=Writer.HUMAN)


def _status(capsys):
    cli.main(["status", "--project", PROJECT])
    return capsys.readouterr().out


def _seam(monkeypatch, name, result):
    real = jsonl.read
    target = _ledger(name)
    monkeypatch.setattr(
        jsonl, "read",
        lambda p, *a, **k: result if p == target else real(p, *a, **k))


def test_a_garbage_ledger_line_ends_with_the_repair_verb_and_the_path(
        tmp_checkpoint_dir, capsys):
    _seed()
    with open(_ledger("amendments.jsonl"), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    out = _status(capsys)
    assert (f"⚠ ledger amendments.jsonl: unreadable (1 garbage); "
            f"run: daimon ledger repair amendments "
            f"({_ledger('amendments.jsonl')})") in out


def test_a_torn_ledger_gets_the_same_cure(tmp_checkpoint_dir, capsys):
    _seed()
    with open(_ledger("relations.jsonl"), "ab") as handle:
        handle.write(b'{"torn')
    out = _status(capsys)
    assert "⚠ ledger relations.jsonl: degraded (1 torn); " in out
    assert "run: daimon ledger repair relations" in out


def test_the_trust_ledger_names_the_trust_verb(tmp_checkpoint_dir, capsys):
    _seed()
    with open(_ledger("trust.jsonl"), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    assert "run: daimon trust repair" in _status(capsys)


def test_an_os_error_says_check_permissions_not_run_status(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed()
    _seam(monkeypatch, "requests.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    out = _status(capsys)
    line = next(ln for ln in out.splitlines()
                if ln.startswith("⚠ ledger requests.jsonl"))
    assert "check permissions (EIO)" in line
    assert "run: daimon status" not in line
    assert str(_ledger("requests.jsonl")) in line


def test_a_transient_ledger_says_retry(tmp_checkpoint_dir, capsys,
                                       monkeypatch):
    _seed()
    _seam(monkeypatch, "events.jsonl",
          jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    line = next(ln for ln in _status(capsys).splitlines()
                if ln.startswith("⚠ ledger events.jsonl"))
    assert "; retry (" in line


def test_a_healthy_store_keeps_the_one_compact_line(tmp_checkpoint_dir,
                                                   capsys):
    _seed()
    out = _status(capsys)
    assert "ledgers: " in out
    assert "⚠ ledger" not in out


def test_the_payload_carries_the_cure_for_each_ledger_not_read_as_is(
        tmp_checkpoint_dir):
    _seed()
    with open(_ledger("amendments.jsonl"), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    payload, _rc = cli.status_payload(PROJECT)
    repair = payload["ledgers"]["repair"]
    assert set(repair) == {"amendments.jsonl"}
    assert repair["amendments.jsonl"]["hint"] == (
        "run: daimon ledger repair amendments")
    assert repair["amendments.jsonl"]["path"] == str(
        _ledger("amendments.jsonl"))


def test_status_still_never_moves_the_exit_code(tmp_checkpoint_dir, capsys):
    _seed()
    with open(_ledger("events.jsonl"), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    assert cli.main(["status", "--project", PROJECT]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--project", PROJECT, "--json"]) == 0


def test_a_proposed_trust_record_is_unaffected(tmp_checkpoint_dir):
    # the census reads `trust` only for health; propose still works on a
    # healthy ledger (guards the new `repair` key against the marker path)
    _seed()
    assert trust.propose(text="a fabricated claim nobody verified", kind="decision", reason="r",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)
    payload, _rc = cli.status_payload(PROJECT)
    assert payload["ledgers"]["repair"] == {}
    assert payload["ledgers"]["forget_incomplete"] == []
