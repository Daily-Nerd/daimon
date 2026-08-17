"""#694 PR 3: the sender-side verdict panel — verdicts on requests THIS
project sent, surfaced in its own briefing. Mirrors test_request_briefing.py
(PR 2's incoming panel) and test_ruling_briefing.py's shape: fail-open,
always present, never budget-dropped. The CLI-only gate (worldcheck_project)
is exercised in test_cli_brief_requests.py, not here.
"""

from daimon_briefing import briefing, requests, store

SENDER = "/p/verdict-brief-sender"
RECIPIENT = "/p/verdict-brief-recipient"


def _ask(ask="publish the schema", **kw):
    return requests.open_request(
        to=store.project_slug(RECIPIENT), ask=ask, why="the client needs it",
        channel="cli-agent", project_dir=SENDER, **kw)


def test_panel_lists_verdicts_on_requests_this_project_sent(
        tmp_checkpoint_dir):
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    assert any("publish the schema" in ln for ln in lines)
    assert any("accepted" in ln for ln in lines)


def test_panel_names_the_recipient(tmp_checkpoint_dir):
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    assert any(store.project_slug(RECIPIENT) in ln for ln in lines)


def test_panel_shows_a_verdict_note(tmp_checkpoint_dir):
    q_id = _ask()
    requests.reject(q_id, channel="cli-tty", note="wrong project",
                    project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    assert any("Note: wrong project" in ln for ln in lines)


def test_panel_shows_done_evidence(tmp_checkpoint_dir):
    q_id = _ask()
    requests.done(q_id, channel="cli-tty", evidence="shipped in v2.1",
                 project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    assert any("Done: shipped in v2.1" in ln for ln in lines)


def test_panel_excludes_still_open_requests(tmp_checkpoint_dir):
    _ask()  # no verdict yet
    assert briefing.verdict_panel_lines(SENDER) == []


def test_panel_never_silently_truncates_over_cap(tmp_checkpoint_dir):
    for n in range(requests.RENDER_CAP + 2):
        q_id = _ask(ask=f"ask number {n} about the release")
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    body = "\n".join(lines)
    assert "+2 more decided" in body
    assert "daimon request list" in body


def test_panel_empty_when_no_project_dir(tmp_checkpoint_dir):
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    assert briefing.verdict_panel_lines(None) == []


def test_panel_empty_with_nothing_decided(tmp_checkpoint_dir):
    assert briefing.verdict_panel_lines(SENDER) == []


def test_panel_fails_open_on_a_broken_composer(tmp_checkpoint_dir, monkeypatch):
    def boom(project_dir=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(requests, "verdict_renderable", boom)
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    assert briefing.verdict_panel_lines(SENDER) == []


def test_panel_and_incoming_panel_coexist(tmp_checkpoint_dir):
    # #694 PR 2's incoming panel and PR 3's verdict panel are independent
    # sections — both must render together without interfering.
    sent_id = _ask(ask="the sent ask")
    requests.accept(sent_id, channel="cli-tty", project_dir=RECIPIENT)
    incoming_id = requests.open_request(
        to=store.project_slug(SENDER), ask="the incoming ask",
        why="because", channel="cli-agent", project_dir=RECIPIENT)
    out = briefing.render(None, project_dir=SENDER, worldcheck_project=SENDER)
    assert "the sent ask" in out
    assert "the incoming ask" in out
    assert incoming_id  # keeps the linter from flagging an unused id


def test_panel_survives_budget_pressure(tmp_checkpoint_dir, sample_checkpoint):
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    import os
    os.environ["DAIMON_BRIEF_MAX_TOKENS"] = "40"
    try:
        out = briefing.render(sample_checkpoint, project_dir=SENDER,
                              worldcheck_project=SENDER)
    finally:
        del os.environ["DAIMON_BRIEF_MAX_TOKENS"]
    assert "publish the schema" in out


def test_render_with_no_checkpoint_still_shows_the_panel(tmp_checkpoint_dir):
    q_id = _ask()
    requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    out = briefing.render(None, project_dir=SENDER, worldcheck_project=SENDER)
    assert out is not None
    assert "publish the schema" in out


def test_full_cap_worst_case_stays_within_budget_share(tmp_checkpoint_dir):
    for n in range(requests.RENDER_CAP):
        q_id = _ask(ask=f"n{n} " + "x" * (requests._MAX_TEXT - 4))
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    lines = briefing.verdict_panel_lines(SENDER)
    section = "\n".join(lines)
    assert briefing.estimate_tokens(section) <= 300
    assert briefing.estimate_tokens(section) >= 100
