"""#1132 2c-1: a field `trust.redact_content_key` redacted reads as
"(value forgotten)" on every human surface, never as the raw marker."""
import json

from daimon_briefing import cli, normalize, pending, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/forget-trust-ledger"
CANARY = "zqxtrustcanary2c1 the staging db password rotates on fridays"
EVIDENCE_CANARY = "artifact:zqxtrustcanary2c1/rotation-notes.md"
QUARANTINED = "a fabricated claim that the deploy key rotates hourly"
MARKER = f"[forgotten:{normalize.content_key(CANARY)}]"


def _checkpoint_with(*texts):
    items = [{"text": t, "trust": "inferred"} for t in texts]
    store.write_checkpoint(
        "S1", {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
               "working_context": {"recent_decisions": items}},
        project_dir=PROJECT, writer=Writer.HUMAN)


def _quarantine(reason, evidence):
    return trust.propose(
        text=QUARANTINED, kind="decision", reason=reason, evidence=evidence,
        channel="cli-tty", project_dir=PROJECT)


def _forget(value):
    assert cli.main(["forget", value, "--project", PROJECT]) == 0


def test_trust_show_prints_value_forgotten_not_the_marker(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # a person at a terminal reads the evidence; anyone else gets a count
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    _checkpoint_with(CANARY, EVIDENCE_CANARY)
    tid = _quarantine(CANARY, ["issue:1109", EVIDENCE_CANARY])
    _forget(CANARY)
    _forget(EVIDENCE_CANARY)
    capsys.readouterr()
    assert cli.main(["trust", "show", tid, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "(value forgotten)" in out
    assert "[forgotten:" not in out
    assert "Evidence: issue:1109" in out
    assert out.count("(value forgotten)") == 2


def test_trust_list_and_pending_never_print_the_marker(
        tmp_checkpoint_dir, capsys):
    tid = trust.propose(
        text=QUARANTINED, kind="decision", reason=MARKER,
        evidence=["issue:1109"], channel="cli-agent", project_dir=PROJECT)
    assert cli.main(["trust", "list", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert tid in out
    assert "(value forgotten)" in out and "[forgotten:" not in out
    rows = pending._trust_rows(PROJECT, store.project_slug(PROJECT))
    flat = json.dumps([r for r, _ in rows], default=str)
    assert "(value forgotten)" in flat and "[forgotten:" not in flat
