"""`daimon decide` — the human's queue (#766), slice 2.

`loops` is what the agent still owes; `decide` is what you still owe. The
command is a pure reader over `pending.queue`: it prints ids, the record's own
text, and the one command that closes each entry. It writes nothing, so running
it can never change what the agent's panels show.

Slice 2 is this project only. Foreign projects arrive as counts in slice 3 and
as text only behind an explicit flag in slice 4, per scar 0055.
"""

import pytest

from daimon_briefing import (
    amendments,
    cli,
    pending,
    refutations,
    render,
    requests,
    store,
)


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


def test_decide_card_shows_the_info_marker_for_an_info_request(
        project, capsys):
    requests.open_request(
        to=store.project_slug(project), ask="an info-only ask",
        why="the recipient can read this from its own artifacts",
        channel="cli-tty", kind="info", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "[info]" in out


def test_decide_card_shows_no_marker_for_a_work_request(project, capsys):
    requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "[info]" not in out


def test_decide_card_lane_tag_is_never_overloaded_by_the_approval_kind(
        project, capsys):
    """THE LANDMINE: `pending._row`'s `kind` key is the queue LANE, never
    the approval requirement — a request-lane row that happens to be `info`
    must still say `[request` in the tag, not `[info`."""
    requests.open_request(
        to=store.project_slug(project), ask="an info-only ask",
        why="why", channel="cli-tty", kind="info", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "[request" in out


def test_decide_card_a_ruling_row_never_shows_an_info_marker(project, capsys):
    """A non-request lane never carries an approval kind at all, so it must
    never render the marker even incidentally."""
    _ruling(project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "[info]" not in out


def test_decide_cards_lane_guard_is_load_bearing_not_dead_code():
    """No REAL composer ever produces a non-request row with `approval`
    set: `pending._request_rows` is the only caller that passes anything but
    the default `None`. That makes the `row.get("kind") == "request"` half
    of `_decide_cards`'s guard unreachable through the shipped pipeline, so a
    test that only ever drives real rows through `pending.queue` can pass
    whether or not that half of the guard exists. This test hands
    `_decide_cards` a synthetic row directly, shaped the way a future bug
    elsewhere in `pending.py` could actually produce one (a non-request lane
    that leaks an `approval` value it was never supposed to carry), and
    checks the guard catches it anyway."""
    from daimon_briefing.cli import lifecycle
    row = {
        "kind": "ruling", "id": "r-0123456789ab", "slug": "-p-x",
        "headline": "never bump to 1.0 without a human call",
        "context": "", "waiting_since": "", "blocking": False,
        "commands": [("ratify", "daimon ruling ratify r-0123456789ab")],
        "approval": "info",
    }

    card = lifecycle._decide_cards([row])[0]

    assert "[info]" not in card[0]


def test_decide_card_never_reads_the_approval_off_a_raw_row(
        project, capsys):
    """THE FOLD RULE THROUGH THE SURFACE: a raw `opened` row appended on a
    non-human channel with `kind="info"` folds to `work`, and the decide
    card must show exactly what the fold says. Appended directly because
    `open_request` refuses this combination at the write boundary."""
    row = requests._stamp("opened", "q-0123456789ab", "cli-agent")
    row.update({"to": store.project_slug(project), "ask": "an ask",
               "why": "why", "kind": "info"})
    assert requests.append(row, project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    # Positive anchor: empty output would also satisfy "no marker".
    assert "q-0123456789ab" in out
    assert "[info]" not in out


def test_decide_card_with_the_info_marker_still_gets_ledger_header_spans(
        project):
    """The marker is inserted between the headline and the blocking suffix,
    so the two-space-after-id contract `render._ledger_header_spans` relies
    on must still hold. Built through the REAL pipeline (`pending.queue` ->
    `lifecycle._decide_cards`), not a hand-typed header string: a hand-typed
    string proves the regex can parse a shape like this, never that
    `_decide_cards` actually produces that shape."""
    from daimon_briefing.cli import lifecycle
    q_id = requests.open_request(
        to=store.project_slug(project), ask="an info-only ask",
        why="why", channel="cli-tty", kind="info", project_dir=project)
    rows = pending.queue(project_dir=project)["rows"]
    card = lifecycle._decide_cards(rows)[0]

    spans = render._ledger_header_spans(card[0])

    assert spans is not None
    assert spans[1] == q_id
    assert "[info]" in card[0]


# ---- #978: a pending completion claim must not clear the queue ------------


def test_decide_still_lists_a_work_request_after_an_agent_done_claim(
        project, capsys):
    """THE test for #978: an agent's completion claim on a work ask nobody
    accepted must not clear the human's decision queue — before this fix
    `daimon decide` dropped the request the moment the agent's `done` row
    landed, and the person addressed never saw it again."""
    q_id = requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)
    requests.done(q_id, channel="cli-agent",
                 evidence="bumped in commit abc123", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert q_id in out
    assert "completion claimed by the agent" in out


def test_decide_card_never_shows_the_claim_line_for_a_plain_open_request(
        project, capsys):
    requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "completion claimed" not in out


def test_decide_cards_claim_marker_guard_is_load_bearing_not_dead_code():
    """Mirrors `test_decide_cards_lane_guard_is_load_bearing_not_dead_code`
    for the `claimed` field: no REAL composer ever sets `claimed` on a
    non-request lane, so this hands `_decide_cards` a synthetic row shaped
    the way a future bug elsewhere in `pending.py` could actually produce
    one, and checks the guard catches it anyway."""
    from daimon_briefing.cli import lifecycle
    row = {
        "kind": "ruling", "id": "r-0123456789ab", "slug": "-p-x",
        "headline": "never bump to 1.0 without a human call",
        "context": "", "waiting_since": "", "blocking": False,
        "commands": [("ratify", "daimon ruling ratify r-0123456789ab")],
        "approval": None, "claimed": True,
    }

    card = lifecycle._decide_cards([row])[0]

    assert "completion claimed" not in "\n".join(card)


def test_decide_card_with_the_claim_line_still_gets_ledger_header_spans(
        project):
    """Built through the REAL pipeline (`pending.queue` ->
    `lifecycle._decide_cards`), not a hand-typed header string — mirrors
    `test_decide_card_with_the_info_marker_still_gets_ledger_header_spans`.
    The claim line is a separate card line, not part of the header, so this
    also pins that the header itself is untouched."""
    from daimon_briefing.cli import lifecycle
    q_id = requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)
    requests.done(q_id, channel="cli-agent",
                 evidence="bumped in commit abc123", project_dir=project)
    rows = pending.queue(project_dir=project)["rows"]
    card = lifecycle._decide_cards(rows)[0]

    spans = render._ledger_header_spans(card[0])

    assert spans is not None
    assert spans[1] == q_id


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


# --- foreign counts (slice 3) -------------------------------------------------

def test_decide_footer_shows_foreign_counts_never_text(project, capsys):
    """The plaintext guarantee, pinned structurally: scar 0055 forbids a
    foreign bucket's own prose from crossing into THIS project's stdout
    (and therefore its checkpoint). Seed a request whose ask/why/evidence
    each carry a distinctive sentinel and prove it never lands in output —
    only the owning slug and an integer count may.
    """
    sentinel = "UNIQUE-PLAINTEXT-SENTINEL-990177"
    b = store.project_slug("/p/B")
    requests.open_request(
        to=b, ask=f"do the thing {sentinel}", why=f"because {sentinel}",
        evidence=f"proof {sentinel}", channel="cli-agent",
        project_dir="/p/A")
    # A local ruling too, so this exercises the POPULATED path (footer after
    # the cards and the suppressed line), not just the empty-queue branch.
    _ruling(project)

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert sentinel not in out
    assert b in out
    assert "1 more waiting in other projects" in out


def test_empty_local_queue_still_reports_foreign_counts(project, capsys):
    """The sibling of the ordinary populated case: nothing waits HERE, but
    the empty-queue path must not silently drop the fact that something
    waits elsewhere.
    """
    requests.open_request(
        to=store.project_slug("/p/B"), ask="please review", why="ready",
        channel="cli-agent", project_dir="/p/A")

    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert "nothing waiting on you in this project" in out
    assert store.project_slug("/p/B") in out


def test_no_foreign_activity_means_no_footer(project, capsys):
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out

    assert "more waiting in other projects" not in out


def test_foreign_counts_returns_integers_only(project):
    requests.open_request(
        to=store.project_slug("/p/B"), ask="please review", why="ready",
        channel="cli-agent", project_dir="/p/A")

    result = pending.foreign_counts(project_dir=project)

    assert result
    assert all(isinstance(v, int) for v in result.values())


# ---- slice 4: text behind an explicit flag, composed per bucket -----------
#
# Scar 0055 governs the default: a foreign bucket's own prose never crosses
# into this project's stdout unasked. `--all-projects` is the person asking,
# the same user-invoked crossing `recall --all-projects` already is. Each
# foreign bucket is composed by ITS OWN `pending.queue` (its own inbox join,
# its own staleness anchor, its own suppression), never one global fold, and
# every printed command carries `--slug=<slug>` so it runs from here.


def _sentinel_ruling_in_b():
    # A ruling id hashes subject and scope, so B's subject differs from the
    # local `_ruling` helper's on purpose: two buckets, two ids.
    return refutations.assert_ruling(
        subject="release of B", verdict="UNIQUE-FOREIGN-RULING-SENTINEL-4471",
        scope="repo", evidence=["issue:766"], channel="cli-agent",
        project_dir="/p/B")


def test_all_projects_shows_foreign_text_with_a_routed_command(project,
                                                               capsys):
    rid = _sentinel_ruling_in_b()
    b = store.project_slug("/p/B")

    assert cli.main(["decide", "--all-projects"]) == 0
    out = capsys.readouterr().out

    assert "UNIQUE-FOREIGN-RULING-SENTINEL-4471" in out
    assert f"daimon ruling ratify {rid} --slug={b}" in out
    assert f"waiting on you in {b}" in out
    # the counts footer is redundant once the text itself is on screen
    assert "more waiting in other projects" not in out


def test_without_the_flag_the_default_stays_counts_only(project, capsys):
    _sentinel_ruling_in_b()
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "UNIQUE-FOREIGN-RULING-SENTINEL-4471" not in out
    assert "1 more waiting in other projects" in out


def test_all_projects_composes_each_foreign_inbox_by_its_own_join(project,
                                                                  capsys):
    """An ask from C to B is B's mail. Under one global fold it would be an
    orphan with no owner; composed per bucket it lands under B, routed."""
    b = store.project_slug("/p/B")
    q = requests.open_request(to=b, ask="review the thing for B", why="w",
                              channel="cli-agent", project_dir="/p/C")

    assert cli.main(["decide", "--all-projects"]) == 0
    out = capsys.readouterr().out

    assert "review the thing for B" in out
    assert f"daimon request accept {q} --slug={b}" in out


def test_all_projects_local_commands_carry_no_slug(project, capsys):
    r_local = _ruling(project)
    _sentinel_ruling_in_b()

    assert cli.main(["decide", "--all-projects"]) == 0
    out = capsys.readouterr().out

    assert f"daimon ruling ratify {r_local}\n" in out
    assert f"daimon ruling ratify {r_local} --slug" not in out


def test_all_projects_with_nothing_anywhere_says_so(project, capsys):
    assert cli.main(["decide", "--all-projects"]) == 0
    assert "nothing waiting on you in any project" in capsys.readouterr().out


def test_all_projects_is_refused_on_a_tenant_scoped_home(project, capsys,
                                                         monkeypatch):
    _sentinel_ruling_in_b()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert cli.main(["decide", "--all-projects"]) == 2
    captured = capsys.readouterr()
    assert "tenant-scoped" in captured.err
    assert "UNIQUE-FOREIGN-RULING-SENTINEL-4471" not in captured.out


def test_all_projects_writes_nothing(project, capsys, tmp_checkpoint_dir):
    _sentinel_ruling_in_b()
    requests.open_request(to=store.project_slug("/p/B"), ask="a", why="w",
                          channel="cli-agent", project_dir="/p/C")
    root = tmp_checkpoint_dir
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}

    assert cli.main(["decide", "--all-projects"]) == 0
    capsys.readouterr()

    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_all_projects_counts_a_foreign_suppression_never_lists_it(
        project, capsys, monkeypatch):
    """The owner's own "not now" in another bucket stays theirs: counted
    under that bucket's heading, the record itself never printed."""
    b = store.project_slug("/p/B")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    requests.open_request(to=b, ask="UNIQUE-SUPPRESSED-ASK-7731", why="w",
                          channel="cli-agent", project_dir="/p/C")
    q = next(iter(requests.recipient_join(project_dir="/p/B")))
    requests.suppress(q, channel="cli-tty", project_dir="/p/B")

    assert cli.main(["decide", "--all-projects"]) == 0
    out = capsys.readouterr().out

    assert f"waiting on you in {b}" in out
    assert "1 suppressed there" in out
    assert "UNIQUE-SUPPRESSED-ASK-7731" not in out
