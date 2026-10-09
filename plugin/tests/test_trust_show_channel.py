"""`trust show` and `trust list` read the cited evidence by channel: raw for a
person at a terminal, a count (text) or nothing (JSON) for anyone else. The
quarantine's reason is a copy of the value and is masked on every channel
(#1132 PR 11c)."""

import json

from daimon_briefing import normalize, store, trust
from tests import _masking as m

EVIDENCE = "artifact:SECRET-E the cited proof of this test"


def _record(**kw):
    values = dict(text=m.QUARANTINED, kind="decision", reason=m.QUARANTINED,
                  evidence=["issue:1109", EVIDENCE], channel="cli-tty",
                  project_dir=m.PROJECT)
    values.update(kw)
    return trust.propose(**values)


def test_a_tty_reads_the_evidence_raw_and_the_reason_masked(
        tmp_checkpoint_dir, capsys, monkeypatch):
    tid = _record()
    m.human(monkeypatch, tty=True)
    rc, out, _ = m.run(capsys, "trust", "show", tid)
    assert rc == 0
    assert f"Evidence: {EVIDENCE}" in out and "Evidence: issue:1109" in out
    assert "SECRET-Q" not in out                       # the reason is masked
    assert f"[withheld: quarantine {tid}]" in out


def test_an_agent_reads_a_count_and_never_the_evidence(
        tmp_checkpoint_dir, capsys, monkeypatch):
    tid = _record()
    m.human(monkeypatch, tty=False)
    rc, out, _ = m.run(capsys, "trust", "show", tid)
    assert rc == 0 and "Evidence: [2 withheld]" in out
    assert "SECRET" not in out and "issue:1109" not in out


def test_json_carries_the_evidence_to_a_tty_and_omits_it_otherwise(
        tmp_checkpoint_dir, capsys, monkeypatch):
    tid = _record()
    m.human(monkeypatch, tty=True)
    rc, out, _ = m.run(capsys, "trust", "show", tid, "--json")
    assert rc == 0 and json.loads(out)["evidence"] == ["issue:1109", EVIDENCE]
    assert json.loads(out)["reason"] == f"[withheld: quarantine {tid}]"
    m.human(monkeypatch, tty=False)
    rc, out, _ = m.run(capsys, "trust", "show", tid, "--json")
    assert rc == 0 and "evidence" not in json.loads(out)
    assert "SECRET" not in out and "issue:1109" not in out


def test_list_has_the_same_gate_on_its_json(tmp_checkpoint_dir, capsys,
                                            monkeypatch):
    tid = _record()
    m.human(monkeypatch, tty=False)
    rc, out, _ = m.run(capsys, "trust", "list", "--json")
    rows = json.loads(out)
    assert rc == 0 and "evidence" not in rows[0] and "SECRET" not in out
    assert rows[0]["quarantine_id"] == tid
    m.human(monkeypatch, tty=True)
    rc, out, _ = m.run(capsys, "trust", "list", "--json")
    assert json.loads(out)[0]["evidence"] == ["issue:1109", EVIDENCE]


def test_a_forgotten_value_has_no_key_to_show(tmp_checkpoint_dir, capsys,
                                              monkeypatch):
    tid = _record()
    key = normalize.content_key(m.QUARANTINED)
    m.forget(m.QUARANTINED)
    for tty in (True, False):
        m.human(monkeypatch, tty=tty)
        rc, out, _ = m.run(capsys, "trust", "show", tid)
        assert rc == 0 and key not in out and "Value key" not in out
        rc, out, _ = m.run(capsys, "trust", "show", tid, "--json")
        assert key not in out and "value_key" not in json.loads(out)
        rc, out, _ = m.run(capsys, "trust", "list", "--json")
        assert key not in out


def test_a_deleter_marker_never_reaches_the_output(tmp_checkpoint_dir,
                                                   capsys, monkeypatch):
    """The real deleter (`daimon forget`) redacts a quarantine's reason and
    evidence in place with a marker carrying the forgotten key."""
    from daimon_briefing import cli
    from daimon_briefing.surfaces import Writer
    canary = "ZQXCANARY the staging password rotates on fridays"
    proof = "artifact:ZQXCANARY/rotation-notes.md"
    store.write_checkpoint(
        "S1", {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
               "working_context": {"recent_decisions": [
                   {"text": canary, "trust": "inferred"},
                   {"text": proof, "trust": "inferred"}]}},
        project_dir=m.PROJECT, writer=Writer.HUMAN)
    tid = _record(reason=canary, evidence=["issue:1", proof])
    assert cli.main(["forget", canary, "--project", m.PROJECT]) == 0
    assert cli.main(["forget", proof, "--project", m.PROJECT]) == 0
    keys = (normalize.content_key(canary), normalize.content_key(proof))
    for tty in (True, False):
        m.human(monkeypatch, tty=tty)
        for argv in (("show", tid), ("show", tid, "--json"), ("list",),
                     ("list", "--json")):
            rc, out, _ = m.run(capsys, "trust", *argv)
            assert rc == 0, (tty, argv)
            assert not any(k in out for k in keys), (tty, argv)
            assert "[forgotten:" not in out, (tty, argv)
    m.human(monkeypatch, tty=True)
    rc, out, _ = m.run(capsys, "trust", "show", tid)
    assert out.count("(value forgotten)") == 2


def test_propose_echoes_a_masked_line(tmp_checkpoint_dir, capsys,
                                      monkeypatch):
    m.quarantine(m.QUARANTINED)
    m.human(monkeypatch, tty=True)
    rc, out, _ = m.run(capsys, "trust", "propose", "--text",
                       "another claim that is long enough to quarantine",
                       "--kind", "decision", "--reason", m.QUARANTINED,
                       "--evidence", "issue:1")
    assert rc == 0 and "SECRET" not in out and "withheld" in out


def test_a_judge_that_fails_shows_nothing(tmp_checkpoint_dir, capsys,
                                          monkeypatch):
    from daimon_briefing import view
    tid = _record()

    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(view, "judge", boom)
    for argv in (("show", tid), ("list",)):
        rc, out, err = m.run(capsys, "trust", *argv)
        assert rc == 2 and out == "" and err.count("\n") == 1, argv
