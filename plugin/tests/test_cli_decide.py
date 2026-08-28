"""`daimon decide` — the human's queue (#766), slice 2.

`loops` is what the agent still owes; `decide` is what you still owe. The
command is a pure reader over `pending.queue`: it prints ids, the record's own
text, and the one command that closes each entry. It writes nothing, so running
it can never change what the agent's panels show.

Slice 2 is this project only. Foreign projects arrive as counts in slice 3 and
as text only behind an explicit flag in slice 4, per scar 0055.
"""

import pytest

from daimon_briefing import amendments, cli, refutations, render, requests, store


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


def _ruling(project, verdict="never bump to 1.0 without a human call"):
    return refutations.assert_ruling(
        subject="release", verdict=verdict, scope="repo",
        evidence=["issue:766"], channel="cli-agent", project_dir=project)


def test_empty_queue_says_so_and_exits_zero(project, capsys):
    assert cli.main(["decide"]) == 0
    assert "nothing waiting on you" in capsys.readouterr().out.lower()


def test_a_candidate_ruling_shows_its_text_and_its_command(project, capsys):
    r_id = _ruling(project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert r_id in out
    assert "never bump to 1.0 without a human call" in out
    assert f"daimon ruling ratify {r_id}" in out


def test_a_decided_request_is_absent(project, capsys):
    q_id = requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)

    assert cli.main(["decide"]) == 0
    assert q_id not in capsys.readouterr().out


def test_suppressed_asks_are_counted_not_listed(project, capsys):
    q_id = requests.open_request(
        to=store.project_slug(project), ask="a muted ask",
        why="why", channel="cli-agent", project_dir=project)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    # Suppression is the owner's own "not now": it takes away placement,
    # never visibility of the fact that something was set aside.
    assert q_id not in out
    assert "1 suppressed" in out


def test_decide_writes_nothing(project, capsys):
    requests.open_request(
        to=store.project_slug(project), ask="an ask",
        why="why", channel="cli-agent", project_dir=project)
    before = requests.events(project_dir=project)

    assert cli.main(["decide"]) == 0
    capsys.readouterr()

    assert requests.events(project_dir=project) == before


def test_an_amendment_id_gets_the_ledger_header_treatment(project):
    """#766: amendment ids are `a-`, and the ledger header span regex was
    widened for `q-` requests (#694) but never for `a-`. An amendment card
    would fall back to whole-line styling and bury the id a human copies —
    silently, because the plain path stays byte-identical either way.
    """
    spans = render._ledger_header_spans(
        "[? candidate] a-0f1e2d3c4b5a  the quote the agent proposed")

    assert spans is not None
    assert spans[1] == "a-0f1e2d3c4b5a"


def test_a_verified_amendment_reaches_the_queue(project, capsys):
    a_id = amendments.propose(
        item_id="o-1234567890ab", change="progressed",
        evidence="the PR merged this morning", channel="cli-agent",
        project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert a_id in out
    assert f"daimon amend ratify {a_id}" in out


def test_the_queue_names_itself(project, capsys):
    """The header is pinned the way the brief panels are (see
    test_cli_brief_verdicts). Rename it and this fails deliberately: the
    string is what a reader learns to scan for, and it is the sibling of
    "Decisions on requests you sent:" already registered for the brief.
    """
    from daimon_briefing.cli import lifecycle

    assert lifecycle._DECIDE_HEADER == "Decisions waiting on you:"

    _ruling(project)
    assert cli.main(["decide"]) == 0
    assert lifecycle._DECIDE_HEADER in capsys.readouterr().out


def test_an_amendment_names_the_item_it_amends(project, capsys):
    """The quote alone cannot be decided on. An amendment is a claim ABOUT a
    checkpoint item, so the target id has to travel with it or the reader has
    to go find out what the sentence refers to."""
    a_id = amendments.propose(
        item_id="o-1234567890ab", change="progressed",
        evidence="the PR merged this morning", channel="cli-agent",
        project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert "o-1234567890ab" in out
    assert "progressed" in out


# --- age formatting -----------------------------------------------------------

def test_age_reads_in_days_once_there_is_a_day_to_show():
    """A backlog is read in days. False precision on something that has waited
    three weeks helps nobody, so hours only appear below one day."""
    import datetime

    from daimon_briefing.cli import lifecycle

    now = datetime.datetime.now(datetime.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"

    assert lifecycle._decide_age(
        (now - datetime.timedelta(hours=3)).strftime(fmt)) == "3h"
    assert lifecycle._decide_age(
        (now - datetime.timedelta(days=21)).strftime(fmt)) == "21d"


def test_an_unreadable_timestamp_costs_the_age_and_nothing_else():
    """A row whose stamp cannot be parsed still has a decision attached to it.
    Dropping the row would hide work; dropping the age loses nothing that
    matters."""
    from daimon_briefing.cli import lifecycle

    assert lifecycle._decide_age("not a timestamp") == ""
    assert lifecycle._decide_age("") == ""
    assert lifecycle._decide_age(None) == ""


def test_a_populated_queue_still_admits_what_it_is_not_showing(project, capsys):
    """The sibling of the empty-queue case, and the ordinary one: items ARE
    listed and something is also set aside. The footer is what stops the queue
    claiming a completeness it does not have, so it has to survive a refactor
    that only ever looks at the populated path.
    """
    _ruling(project)
    muted = requests.open_request(
        to=store.project_slug(project), ask="a muted ask",
        why="why", channel="cli-agent", project_dir=project)
    requests.suppress(muted, channel="cli-tty", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert "never bump to 1.0 without a human call" in out   # listed
    assert muted not in out                                   # not listed
    assert "1 suppressed" in out                              # admitted
