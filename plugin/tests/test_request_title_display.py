"""#1131: every place a request's ask is shown in a title position leads with
the sender's title when there is one, and renders exactly as after #1127 when
there is not. `requests.headline` is the one owner of that choice."""

import pytest

from daimon_briefing import briefing, cli, pending, requests, store
from daimon_briefing.cli import request as cli_request

LONG_ASK = (
    "Alignment check from the sender: please publish the updated checkpoint "
    "schema to the shared docs site and confirm that the migration notes for "
    "the bucket split are linked from the changelog entry for the release "
    "that introduced the new request lifecycle states. ") * 3
TITLE = "Publish the checkpoint schema"


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


def _open(project, ask=LONG_ASK, **kw):
    return requests.open_request(
        to=store.project_slug(project), ask=ask.strip(), why="needed",
        channel="cli-agent", project_dir=project, **kw)


def test_headline_prefers_the_title():
    assert requests.headline({"ask": LONG_ASK, "title": TITLE}) == TITLE


def test_headline_falls_back_to_the_bounded_ask():
    assert requests.headline({"ask": LONG_ASK}) == requests.short_ask(LONG_ASK)
    assert requests.headline({"ask": LONG_ASK, "title": ""}) == (
        requests.short_ask(LONG_ASK))


def test_headline_bounds_a_title_edited_on_disk():
    out = requests.headline({"ask": "a", "title": "w " * 300})
    assert len(out) <= requests.ASK_CHARS


def test_pending_row_leads_with_the_title_and_keeps_the_full_ask(project):
    _open(project, title=TITLE)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["headline"] == TITLE
    assert row["detail"] == "Ask: " + " ".join(LONG_ASK.split())


def test_pending_row_with_a_short_ask_and_a_title_still_carries_the_ask(
        project):
    _open(project, ask="do the thing", title=TITLE)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["headline"] == TITLE
    assert row["detail"] == "Ask: do the thing"


def test_pending_row_without_a_title_is_as_after_1127(project):
    _open(project)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["headline"] == requests.short_ask(LONG_ASK)


def test_request_panel_leads_with_the_title(project):
    q_id = _open(project, title=TITLE)
    lines = briefing.request_panel_lines(project)
    line = next(ln for ln in lines if q_id in ln)
    assert TITLE in line
    assert requests.short_ask(LONG_ASK) not in line


def test_request_panel_without_a_title_is_unchanged(project):
    q_id = _open(project)
    line = next(ln for ln in briefing.request_panel_lines(project)
                if q_id in ln)
    assert requests.short_ask(LONG_ASK) in line


def test_owed_panel_leads_with_the_title(project):
    q_id = _open(project, title=TITLE)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    line = next(ln for ln in briefing.owed_panel_lines(project)
                if q_id in ln)
    assert TITLE in line


def test_inject_lines_lead_with_the_title_then_the_bounded_ask(project):
    q_id = _open(project, title=TITLE)
    record = requests.get(q_id, project_dir=project)
    lines = cli_request._inject_lines(record)
    assert lines[1] == f"  Title: {TITLE}"
    assert lines[2].startswith("  Ask: ")
    assert "daimon request inbox" in lines[2]


def test_verdict_and_owed_inject_lines_lead_with_the_title(project):
    q_id = _open(project, title=TITLE)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    for fn in (cli_request._verdict_inject_lines,
               cli_request._owed_inject_lines):
        assert f"  Title: {TITLE}" in fn(record)


def test_inject_lines_without_a_title_have_no_title_line(project):
    q_id = _open(project)
    record = requests.get(q_id, project_dir=project)
    assert not any("Title:" in ln for ln in cli_request._inject_lines(record))


def test_list_and_inbox_cards_lead_with_the_title(project):
    q_id = _open(project, title=TITLE)
    record = requests.get(q_id, project_dir=project)
    for fn in (cli_request._request_lines, cli_request._inbox_lines):
        lines = fn(record)
        assert lines[0].endswith(f"{q_id}  {TITLE}")
        assert lines[1] == "  Ask: " + " ".join(LONG_ASK.split())


def test_list_and_inbox_cards_without_a_title_are_unchanged(project):
    q_id = _open(project)
    record = requests.get(q_id, project_dir=project)
    for fn in (cli_request._request_lines, cli_request._inbox_lines):
        lines = fn(record)
        assert lines[0].endswith(f"{q_id}  " + " ".join(LONG_ASK.split()))
        assert not any(ln.startswith("  Ask:") for ln in lines)


def test_decide_card_shows_title_header_and_full_ask(project, capsys):
    q_id = _open(project, title=TITLE)
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    header = next(ln for ln in out.splitlines() if q_id in ln)
    assert TITLE in header
    assert " ".join(LONG_ASK.split()) in " ".join(out.split())
