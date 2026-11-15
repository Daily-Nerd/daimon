"""#1348: where a severity shows, what it costs, and the decide queue order.

One renderer (`requests.severity_line`) serves every surface; a normal request
adds no byte anywhere."""

import pytest

from daimon_briefing import briefing, cli, pending, requests, store
from daimon_briefing.surfaces import Writer

WHY = "it blocks the release note we owe the team"
REASON = "the release waits on this"
RDIR = "/p/sev-display-recipient"


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RDIR)
    return "/p/sev-display-sender"


def _seed_bucket(project_dir, session="S-bucket"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _open(project, ask="publish the schema", **kwargs):
    return requests.open_request(
        to=_seed_bucket(RDIR), ask=ask, why=WHY, channel="cli-agent",
        project_dir=project, **kwargs)


# ---- the briefing panels ----------------------------------------------------


def test_the_decision_panel_shows_it_under_the_from_line(project):
    q_id = _open(project, severity="high", severity_reason=REASON)
    lines = briefing.request_panel_lines(RDIR)
    at = next(i for i, ln in enumerate(lines) if q_id in ln)
    assert lines[at + 1].startswith("  From:")
    assert lines[at + 2] == f"  High: {REASON}"


def test_the_owed_panel_shows_it_under_the_from_line(project):
    q_id = _open(project, severity="critical", severity_reason=REASON)
    requests.accept(q_id, channel="cli-tty", project_dir=RDIR)
    lines = briefing.owed_panel_lines(RDIR)
    at = next(i for i, ln in enumerate(lines) if q_id in ln)
    assert lines[at + 2] == f"  Critical: {REASON}"


def test_a_normal_request_adds_no_line_to_either_panel(project):
    q_id = _open(project)
    decision = briefing.request_panel_lines(RDIR)
    assert [ln.split()[0] for ln in decision] == ["Requests", "→", "From:"]
    requests.accept(q_id, channel="cli-tty", project_dir=RDIR)
    owed = briefing.owed_panel_lines(RDIR)
    assert not any(level.capitalize() + ":" in ln for ln in owed + decision
                   for level in requests.SEVERITIES)


def test_the_panel_is_byte_identical_when_every_request_is_normal(project):
    ids = [_open(project, ask=f"ask {i}") for i in range(2)]
    want = [briefing._REQUEST_PANEL_HEADER]
    for i in (1, 0):
        want += [f"→ {ids[i]}  ask {i}", "  From: sev-display-sender"]
    assert briefing.request_panel_lines(RDIR) == want


def test_a_critical_ask_takes_a_panel_slot_from_a_newer_normal_one(project):
    first = _open(project, ask="first", severity="critical",
                  severity_reason=REASON)
    others = [_open(project, ask=f"later {i}") for i in range(3)]
    text = "\n".join(briefing.request_panel_lines(RDIR))
    assert first in text
    assert others[0] not in text  # the oldest normal one moves to the count
    assert "(+1 more waiting" in text


def test_the_worst_case_cost_per_row_is_bounded(project):
    for level in ("critical", "high", "low"):
        _open(project, ask=f"ask {level}", severity=level,
              severity_reason="r" * requests.SEVERITY_REASON_CHARS)
    lines = briefing.request_panel_lines(RDIR)
    severity = [ln for ln in lines if ln.lstrip().startswith(
        tuple(level.capitalize() + ":" for level in requests.SEVERITIES))]
    assert len(severity) == 3
    for line in severity:
        assert len((line + "\n").encode()) <= 93


def test_the_senders_verdict_panel_does_not_show_it(project):
    q_id = _open(project, severity="high", severity_reason=REASON)
    requests.accept(q_id, channel="cli-tty", project_dir=RDIR)
    lines = briefing.verdict_panel_lines(project)
    assert lines and not any("High" in ln for ln in lines)


# ---- decide -----------------------------------------------------------------


def test_the_decide_queue_orders_blocking_then_severity_then_oldest(project):
    old_normal = _open(project, ask="old normal")
    low = _open(project, ask="a low one", severity="low",
                severity_reason=REASON)
    high = _open(project, ask="a high one", severity="high",
                 severity_reason=REASON)
    blocking_normal = _open(project, ask="stalled", blocking=True)
    crit = _open(project, ask="a critical one", severity="critical",
                 severity_reason=REASON)
    rows = pending.queue(project_dir=RDIR)["rows"]
    assert [r["id"] for r in rows if r["kind"] == "request"] == [
        blocking_normal, crit, high, old_normal, low]


def test_every_queue_row_carries_a_severity_and_other_lanes_are_normal(
        project):
    _open(project, severity="high", severity_reason=REASON)
    rows = pending.queue(project_dir=RDIR)["rows"]
    (request_row,) = [r for r in rows if r["kind"] == "request"]
    assert (request_row["severity"], request_row["severity_reason"]) == (
        "high", REASON)
    row = pending._row(kind="ruling", record_id="r-1", slug="s", headline="h",
                       waiting_since="", commands=[])
    assert (row["severity"], row["severity_reason"]) == ("normal", "")


def test_the_decide_card_shows_it_and_a_normal_card_does_not(project, capsys):
    raised = _open(project, ask="raised ask", severity="high",
                   severity_reason=REASON)
    plain = _open(project, ask="plain ask")
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out.splitlines()
    at = next(i for i, ln in enumerate(out) if raised in ln)
    assert f"  High: {REASON}" in out[at:at + 4]
    at = next(i for i, ln in enumerate(out) if plain in ln)
    assert not any("High:" in ln for ln in out[at:at + 4])
