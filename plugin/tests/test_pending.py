"""The `decide` queue composer (#766), slice 1: this project's buckets only.

`pending.queue` answers one question: what is waiting on a HUMAN here. A record
qualifies only when some verb's write path refuses a non-human channel, so the
test is structural rather than editorial. Slice 1 is deliberately single-bucket
— foreign counts land in slice 3 and foreign text only behind `--all-projects`
in slice 4, because printing another bucket's text into this project's
checkpoint is what scar 0055 forbids.

The composer writes nothing. In particular it never stamps `surfaced`: that
anchor drives `is_stale`, so a stamping reader would make the person reading
their own queue the mechanism that ages their asks out of the agent's panel.
"""

import pytest

from daimon_briefing import amendments, pending, refutations, requests, store


@pytest.fixture
def project(tmp_checkpoint_dir):
    # conftest's autouse fixture already isolates DAIMON_CHECKPOINT_DIR.
    return "/p/A"


def _kinds(result):
    return [row["kind"] for row in result["rows"]]


def _ids(result):
    return [row["id"] for row in result["rows"]]


def _commands(row):
    return [command for _label, command in row["commands"]]


# --- rulings and refutations -------------------------------------------------

def test_candidate_ruling_waits_on_a_human(project):
    r_id = refutations.assert_ruling(
        subject="release", verdict="never bump to 1.0 without a human call",
        scope="repo", evidence=["issue:766"], channel="cli-agent",
        project_dir=project)

    result = pending.queue(project_dir=project)

    assert _ids(result) == [r_id]
    row = result["rows"][0]
    assert row["kind"] == "ruling"
    # The rule text is the header a human reads, never the subject.
    assert row["headline"] == "never bump to 1.0 without a human call"


def test_an_active_ruling_owes_nothing(project):
    refutations.assert_ruling(
        subject="release", verdict="never bump to 1.0 without a human call",
        scope="repo", evidence=["issue:766"], channel="cli-tty",
        ratified=True, project_dir=project)

    assert pending.queue(project_dir=project)["rows"] == []


def test_candidate_refutation_waits_and_carries_its_own_verb(project):
    r_id = refutations.assert_refutation(
        subject="idf recall", verdict="does not improve recall",
        scope="repo", evidence=["measurement:n=50"], channel="cli-agent",
        project_dir=project)

    row = pending.queue(project_dir=project)["rows"][0]

    assert row["kind"] == "refutation"
    assert row["id"] == r_id
    # A refutation is not retired, and a ruling is not overturned.
    assert any("refute ratify" in c for c in _commands(row))
    assert not any("ruling ratify" in c for c in _commands(row))


# --- amendments --------------------------------------------------------------

def test_an_amendment_candidate_is_not_yet_owed(project):
    # amendments.py:64 — candidates render nowhere, because an unverified
    # annotation would let an agent assert state with no transcription check.
    amendments.propose(
        item_id="o-1234567890ab", change="progressed",
        evidence="the PR merged this morning", channel="cli-agent",
        project_dir=project)

    assert pending.queue(project_dir=project)["rows"] == []


def test_a_quote_verified_amendment_is_owed(project):
    a_id = amendments.propose(
        item_id="o-1234567890ab", change="progressed",
        evidence="the PR merged this morning", channel="cli-agent",
        project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    row = pending.queue(project_dir=project)["rows"][0]

    assert row["kind"] == "amendment"
    assert row["id"] == a_id
    assert any("amend ratify" in c for c in _commands(row))
    assert any("amend reject" in c for c in _commands(row))


# --- requests ----------------------------------------------------------------

def test_an_addressed_undecided_request_is_owed(project):
    q_id = requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)

    row = pending.queue(project_dir=project)["rows"][0]

    assert row["kind"] == "request"
    assert row["id"] == q_id
    assert any("request accept" in c for c in _commands(row))


def test_a_decided_request_is_not_owed(project):
    q_id = requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)

    assert pending.queue(project_dir=project)["rows"] == []


# --- ordering and posture ----------------------------------------------------

def test_oldest_waits_first(project):
    """`decide` is a backlog, not the panels' capped attention feed: the
    oldest undecided item is the one rotting, so this deliberately inverts
    `inbox_renderable`'s newest-first."""
    first = refutations.assert_ruling(
        subject="a", verdict="the older rule", scope="repo",
        evidence=["issue:1"], channel="cli-agent", project_dir=project)
    second = refutations.assert_ruling(
        subject="b", verdict="the newer rule", scope="repo",
        evidence=["issue:2"], channel="cli-agent", project_dir=project)

    assert _ids(pending.queue(project_dir=project)) == [first, second]


def test_the_composer_writes_nothing(project):
    """No `surfaced` stamp, no ledger row, nothing. `is_stale` counts
    serialized pointers against that anchor, so a stamping reader would
    decay the asks of the person reading them."""
    requests.open_request(
        to=store.project_slug(project), ask="bump before the docs change",
        why="the tag is referenced", channel="cli-agent", project_dir=project)
    before = requests.events(project_dir=project)

    pending.queue(project_dir=project)

    assert requests.events(project_dir=project) == before


def test_every_row_carries_its_project(project):
    """Slice 3 adds other projects' counts; the field has to exist from the
    start so a row can never be read as belonging to wherever it was run."""
    refutations.assert_ruling(
        subject="release", verdict="a rule", scope="repo",
        evidence=["issue:766"], channel="cli-agent", project_dir=project)

    row = pending.queue(project_dir=project)["rows"][0]

    assert row["slug"] == store.project_slug(project)


# --- degradation --------------------------------------------------------------

def test_one_unreadable_ledger_degrades_its_own_lane_only(project, monkeypatch):
    """Fail-open per source, not per queue.

    A human-facing backlog that silently renders nothing is worse than one that
    renders less: an empty queue reads as "you owe nothing", which is the one
    false claim this surface must never make. So a source that blows up costs
    its own lane and nothing else.
    """
    refutations.assert_ruling(
        subject="release", verdict="a standing rule", scope="repo",
        evidence=["issue:766"], channel="cli-agent", project_dir=project)
    requests.open_request(
        to=store.project_slug(project), ask="an ask", why="why",
        channel="cli-agent", project_dir=project)

    def boom(*_args, **_kwargs):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(pending.refutations, "records", boom)

    result = pending.queue(project_dir=project)

    assert _kinds(result) == ["request"]


def test_an_unreadable_request_ledger_does_not_empty_the_queue(project,
                                                               monkeypatch):
    refutations.assert_ruling(
        subject="release", verdict="a standing rule", scope="repo",
        evidence=["issue:766"], channel="cli-agent", project_dir=project)

    def boom(*_args, **_kwargs):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(pending.requests, "records", boom)

    result = pending.queue(project_dir=project)

    assert _kinds(result) == ["ruling"]
    # The suppressed count is unknown rather than zero when its source failed.
    assert result["excluded"]["suppressed"] == 0
