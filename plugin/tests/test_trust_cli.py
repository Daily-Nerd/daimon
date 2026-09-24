"""`daimon trust` CLI wiring (#1109 Slice 1).

The library already enforces the human-only gate on confirm/dismiss/release
(tests/test_trust.py); these tests pin the CLI boundary that reaches it —
`--by agent` is exposed on every verb, refused for confirm/dismiss/release
by the library, never hidden from the parser.
"""

from daimon_briefing import cli, trust


def test_cli_propose_agent_lands_candidate(tmp_checkpoint_dir):
    rc = cli.main([
        "trust", "propose",
        "--text", "the deploy runbook step was fabricated by the agent",
        "--kind", "decision", "--reason", "no matching PR anywhere",
        "--evidence", "issue:1109", "--by", "agent", "--project", "/p/A",
    ])
    assert rc == 0
    records = trust.records(project_dir="/p/A")
    assert len(records) == 1
    assert next(iter(records.values()))["state"] == "candidate"


def test_cli_propose_human_path_requires_tty(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main([
        "trust", "propose",
        "--text", "the deploy runbook step was fabricated by the agent",
        "--kind", "decision", "--reason", "no matching PR anywhere",
        "--evidence", "issue:1109", "--project", "/p/A",
    ])
    assert rc == 1
    assert not trust.records(project_dir="/p/A")


def test_cli_propose_human_tty_lands_active(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    rc = cli.main([
        "trust", "propose",
        "--text", "the deploy runbook step was fabricated by the agent",
        "--kind", "decision", "--reason", "no matching PR anywhere",
        "--evidence", "issue:1109", "--project", "/p/A",
    ])
    assert rc == 0
    assert next(iter(
        trust.records(project_dir="/p/A").values()))["state"] == "active"


def _propose(project="/p/A"):
    return trust.propose(
        text="the deploy runbook step was fabricated by the agent",
        kind="decision", reason="no matching PR anywhere",
        evidence=["issue:1109"], item_id="o-1234567890ab",
        channel="cli-agent", project_dir=project)


def test_cli_confirm_by_agent_is_refused(tmp_checkpoint_dir):
    tid = _propose()
    rc = cli.main(["trust", "confirm", tid, "--by", "agent",
                   "--project", "/p/A"])
    assert rc == 1
    assert trust.get(tid, project_dir="/p/A")["state"] == "candidate"


def test_cli_confirm_requires_tty(tmp_checkpoint_dir, monkeypatch):
    tid = _propose()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main(["trust", "confirm", tid, "--project", "/p/A"])
    assert rc == 1
    assert trust.get(tid, project_dir="/p/A")["state"] == "candidate"


def test_cli_confirm_from_a_tty_activates(tmp_checkpoint_dir, monkeypatch):
    tid = _propose()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    rc = cli.main(["trust", "confirm", tid, "--project", "/p/A"])
    assert rc == 0
    assert trust.get(tid, project_dir="/p/A")["state"] == "active"


def test_cli_dismiss_by_agent_is_refused(tmp_checkpoint_dir):
    tid = _propose()
    rc = cli.main(["trust", "dismiss", tid, "--by", "agent",
                   "--project", "/p/A"])
    assert rc == 1
    assert trust.get(tid, project_dir="/p/A")["state"] == "candidate"


def test_cli_release_by_agent_is_refused(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    tid = trust.propose(
        text="the deploy runbook step was fabricated by the agent",
        kind="decision", reason="no matching PR anywhere",
        evidence=["issue:1109"], channel="cli-tty", project_dir="/p/A")
    rc = cli.main(["trust", "release", tid, "--by", "agent",
                   "--project", "/p/A"])
    assert rc == 1
    assert trust.get(tid, project_dir="/p/A")["state"] == "active"


def test_cli_list_json(tmp_checkpoint_dir, capsys):
    tid = _propose()
    rc = cli.main(["trust", "list", "--project", "/p/A", "--json"])
    assert rc == 0
    import json
    rows = json.loads(capsys.readouterr().out)
    assert [r["quarantine_id"] for r in rows] == [tid]


def test_cli_show_unknown_id(tmp_checkpoint_dir):
    rc = cli.main(["trust", "show", "tr-000000000000", "--project", "/p/A"])
    assert rc == 1


def test_cli_show_json(tmp_checkpoint_dir, capsys):
    tid = _propose()
    rc = cli.main(["trust", "show", tid, "--project", "/p/A", "--json"])
    assert rc == 0
    import json
    row = json.loads(capsys.readouterr().out)
    assert row["quarantine_id"] == tid
    assert "value_key" in row


def test_cli_show_plain_text(tmp_checkpoint_dir, capsys):
    tid = _propose()
    rc = cli.main(["trust", "show", tid, "--project", "/p/A"])
    assert rc == 0
    out = capsys.readouterr().out
    assert tid in out
    assert "Value key:" in out
    assert "Item:" in out
    assert "Evidence: issue:1109" in out


def test_cli_list_plain_text_empty(tmp_checkpoint_dir, capsys):
    rc = cli.main(["trust", "list", "--project", "/p/A"])
    assert rc == 0
    assert "no quarantines recorded" in capsys.readouterr().out


def test_cli_list_plain_text(tmp_checkpoint_dir, capsys):
    tid = _propose()
    rc = cli.main(["trust", "list", "--project", "/p/A"])
    assert rc == 0
    assert tid in capsys.readouterr().out
