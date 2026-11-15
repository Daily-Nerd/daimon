"""#1348: the CLI writes a severity (`request open --severity`, `request
severity`), filters by it (`--min-severity`), and every card and inject line
renders it through the one `requests.severity_line`."""

import json

import pytest

from daimon_briefing import cli, requests, store
from daimon_briefing.surfaces import Writer

WHY = "it blocks the release note we owe the team"
REASON = "the release waits on this"
RDIR = "/p/sev-cli-recipient"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return "/p/sev-cli-sender"


def _seed_bucket(project_dir, session="S-bucket"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _open(project, ask="publish the schema", **kwargs):
    slug = _seed_bucket(RDIR)
    return requests.open_request(to=slug, ask=ask, why=WHY,
                                 channel="cli-agent", project_dir=project,
                                 **kwargs)


def _run(argv, capsys):
    capsys.readouterr()
    rc = cli.main(argv)
    return rc, capsys.readouterr().out


# ---- open -------------------------------------------------------------------


def test_open_takes_a_severity_and_its_reason(project, capsys):
    slug = _seed_bucket(RDIR)
    rc, out = _run(["request", "open", f"--to={slug}", "--ask", "do it",
                    "--why", WHY, "--severity", "high",
                    "--severity-reason", REASON, "--by", "agent",
                    "--project", project], capsys)
    assert rc == 0
    assert f"  High: {REASON}" in out
    (record,) = requests.listing(project)
    assert (record["severity"], record["severity_reason"]) == ("high", REASON)


def test_open_without_a_severity_prints_no_severity_line(project, capsys):
    slug = _seed_bucket(RDIR)
    rc, out = _run(["request", "open", f"--to={slug}", "--ask", "do it",
                    "--why", WHY, "--by", "agent", "--project", project],
                   capsys)
    assert rc == 0
    for level in requests.SEVERITIES:
        assert f"{level.capitalize()}:" not in out


def test_open_refuses_a_raised_level_without_a_reason(project, capsys):
    slug = _seed_bucket(RDIR)
    rc, out = _run(["request", "open", f"--to={slug}", "--ask", "do it",
                    "--why", WHY, "--severity", "critical", "--by", "agent",
                    "--project", project], capsys)
    assert rc == 1
    assert "request not recorded" in out and "reason" in out
    assert requests.listing(project) == []


def test_open_refuses_a_level_outside_the_table(project):
    slug = _seed_bucket(RDIR)
    with pytest.raises(SystemExit):
        cli.main(["request", "open", f"--to={slug}", "--ask", "x",
                  "--why", WHY, "--severity", "urgent", "--by", "agent",
                  "--project", project])


# ---- the verb ---------------------------------------------------------------


def test_the_verb_changes_it_and_says_what_the_fold_did(project, capsys):
    q_id = _open(project)
    rc, out = _run(["request", "severity", q_id, "critical", "--reason",
                    REASON, "--by", "agent", "--project", project], capsys)
    assert rc == 0
    assert f"  Critical: {REASON}" in out
    assert f"{q_id}: severity recorded" in out
    assert requests.sent_record(q_id, project_dir=project)["severity"] == (
        "critical")


def test_the_verb_clears_with_normal(project, capsys):
    q_id = _open(project, severity="high", severity_reason=REASON)
    rc, out = _run(["request", "severity", q_id, "normal", "--by", "agent",
                    "--project", project], capsys)
    assert rc == 0
    assert "High:" not in out
    assert requests.sent_record(q_id, project_dir=project)["severity"] == (
        "normal")


def test_the_verb_refuses_a_raised_level_without_a_reason(project, capsys):
    q_id = _open(project)
    rc, out = _run(["request", "severity", q_id, "high", "--by", "agent",
                    "--project", project], capsys)
    assert rc == 1
    assert "request severity refused" in out


def test_the_verb_refuses_a_change_that_changes_nothing(project, capsys):
    q_id = _open(project, severity="high", severity_reason=REASON)
    rc, out = _run(["request", "severity", q_id, "high", "--reason", REASON,
                    "--by", "agent", "--project", project], capsys)
    assert rc == 1
    assert "changes nothing" in out


def test_the_verb_is_refused_from_the_recipient_side(project, capsys):
    q_id = _open(project)
    rc, out = _run(["request", "severity", q_id, "high", "--reason", REASON,
                    "--by", "agent", "--project", RDIR], capsys)
    assert rc == 1
    assert "only its sender" in out


def test_the_verb_takes_only_levels_from_the_table(project):
    q_id = _open(project)
    with pytest.raises(SystemExit):
        cli.main(["request", "severity", q_id, "urgent", "--reason", REASON,
                  "--by", "agent", "--project", project])


# ---- the filter -------------------------------------------------------------


@pytest.mark.parametrize("verb,where", [("list", "sender"),
                                        ("inbox", "recipient")])
def test_min_severity_filters_both_listings(project, capsys, verb, where):
    for level in ("critical", "high", "normal", "low"):
        extra = ({} if level == "normal"
                 else {"severity": level, "severity_reason": REASON})
        _open(project, ask=f"ask at {level}", **extra)
    here = project if where == "sender" else RDIR
    rc, out = _run(["request", verb, "--min-severity", "high", "--json",
                    "--project", here], capsys)
    assert rc == 0
    assert [r["severity"] for r in json.loads(out)] == ["critical", "high"]


@pytest.mark.parametrize("verb", ["list", "inbox"])
def test_the_json_carries_an_explicit_severity_on_every_record(
        project, capsys, verb):
    _open(project)
    here = project if verb == "list" else RDIR
    rc, out = _run(["request", verb, "--json", "--project", here], capsys)
    (row,) = json.loads(out)
    assert (row["severity"], row["severity_reason"]) == ("normal", "")


def test_min_severity_takes_only_levels_from_the_table(project):
    with pytest.raises(SystemExit):
        cli.main(["request", "list", "--min-severity", "urgent",
                  "--project", project])


# ---- the cards and the inject lines -----------------------------------------


@pytest.mark.parametrize("verb", ["list", "inbox"])
def test_a_card_shows_the_severity_after_why_and_normal_shows_nothing(
        project, capsys, verb):
    _open(project, ask="raised", severity="high", severity_reason=REASON)
    _open(project, ask="plain")
    here = project if verb == "list" else RDIR
    rc, out = _run(["request", verb, "--project", here], capsys)
    assert rc == 0
    lines = out.splitlines()
    at = lines.index(f"  High: {REASON}")
    assert lines[at - 1] == f"  Why: {WHY}"
    assert out.count("High:") == 1


def test_the_undecided_inject_lines_show_it(project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    _open(project, severity="critical", severity_reason=REASON)
    rc, out = _run(["request-inject", "--session", "S-1", "--project", RDIR],
                   capsys)
    assert rc == 0
    assert f"  Critical: {REASON}" in out


def test_the_owed_inject_lines_show_it(project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    q_id = _open(project, severity="high", severity_reason=REASON)
    requests.accept(q_id, channel="cli-tty", project_dir=RDIR)
    rc, out = _run(["request-inject", "--session", "S-1", "--project", RDIR],
                   capsys)
    assert rc == 0
    assert "daimon owed:" in out and f"  High: {REASON}" in out


def test_the_verdict_inject_lines_do_not_show_it(project):
    from daimon_briefing.cli import request as cli_request
    record = {"request_id": "q-000000000001", "state": "accepted",
              "to": "x", "ask": "a", "severity": "high",
              "severity_reason": REASON}
    assert not any("High" in line
                   for line in cli_request._verdict_inject_lines(record))


def test_the_verb_still_reports_when_the_record_cannot_be_reread(
        project, capsys, monkeypatch):
    q_id = _open(project)
    real = requests.sent_record
    calls = []

    def once(request_id, project_dir=None):
        calls.append(request_id)
        return real(request_id, project_dir=project_dir) if len(calls) == 1 \
            else None

    monkeypatch.setattr(requests, "sent_record", once)
    rc, out = _run(["request", "severity", q_id, "high", "--reason", REASON,
                    "--by", "agent", "--project", project], capsys)
    assert rc == 0
    assert f"{q_id}: severity recorded in this project's ledger" in out
