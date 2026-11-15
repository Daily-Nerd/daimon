"""#1348: severity decides which rows win the capped attention slots, never how
many there are, and the listings and the filter read the same table.

Within one level the order is exactly what it was: a ledger with no
severities sorts as it always did."""

import ast
import inspect
import textwrap

import pytest

from daimon_briefing import requests, store
from daimon_briefing.surfaces import Writer

WHY = "it blocks the release note we owe the team"
REASON = "the release waits on this"
RDIR = "/p/sev-order-recipient"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return "/p/sev-order-sender"


def _seed_bucket(project_dir, session="S-bucket"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _open_all(project, severities):
    """One ask per entry, opened in order (so the last is the newest), each
    with the given severity. Returns the ids in that order."""
    slug = _seed_bucket(RDIR)
    ids = []
    for i, level in enumerate(severities):
        extra = ({} if level == "normal"
                 else {"severity": level, "severity_reason": REASON})
        ids.append(requests.open_request(
            to=slug, ask=f"ask number {i}", why=WHY, channel="cli-agent",
            project_dir=project, **extra))
    return ids


def _ids(feed):
    return [r["request_id"] for r in feed["rows"]]


# ---- the capped recipient feeds ---------------------------------------------

DECISION_FEEDS = {
    "decision_renderable": lambda: requests.decision_renderable(RDIR),
    "inbox_renderable": lambda: requests.inbox_renderable(RDIR),
    "deliverable": lambda: requests.deliverable("S-1", RDIR),
}


@pytest.mark.parametrize("feed", sorted(DECISION_FEEDS))
def test_an_all_normal_ledger_keeps_newest_first(project, feed):
    ids = _open_all(project, ["normal"] * 5)
    got = DECISION_FEEDS[feed]()
    assert _ids(got) == list(reversed(ids))[:requests.RENDER_CAP]
    assert got["overflow"] == 2


@pytest.mark.parametrize("feed", sorted(DECISION_FEEDS))
def test_a_critical_ask_beats_three_newer_normal_ones_for_a_slot(
        project, feed):
    ids = _open_all(project, ["critical", "normal", "normal", "normal"])
    got = DECISION_FEEDS[feed]()
    assert _ids(got) == [ids[0], ids[3], ids[2]]
    assert got["overflow"] == 1


@pytest.mark.parametrize("feed", sorted(DECISION_FEEDS))
def test_severity_orders_the_slots_and_recency_breaks_ties(project, feed):
    ids = _open_all(project, ["low", "high", "normal", "high", "critical",
                              "low"])
    got = DECISION_FEEDS[feed]()
    assert _ids(got) == [ids[4], ids[3], ids[1]]
    assert got["overflow"] == 3


def test_the_cap_stays_three_whatever_the_severities(project):
    ids = _open_all(project, ["critical"] * 5)
    got = requests.decision_renderable(RDIR)
    assert _ids(got) == list(reversed(ids))[:requests.RENDER_CAP]
    assert got["overflow"] == 2


@pytest.fixture
def accepted(project):
    ids = _open_all(project, ["normal", "low", "critical", "normal"])
    for q_id in ids:
        requests.accept(q_id, channel="cli-tty", project_dir=RDIR)
    return ids


@pytest.mark.parametrize("feed", ["owed_renderable", "owed_deliverable"])
def test_the_owed_feeds_sort_by_severity_then_recency(accepted, feed):
    got = (requests.owed_renderable(RDIR) if feed == "owed_renderable"
           else requests.owed_deliverable("S-1", RDIR))
    assert _ids(got) == [accepted[2], accepted[3], accepted[0]]
    assert got["overflow"] == 1


def _calls(name):
    """The names called, and the methods called, in a function's CODE (the
    parse drops comments, which an explaining comment would otherwise
    satisfy)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(getattr(requests, name))))
    plain, methods = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                plain.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                methods.add(node.func.attr)
    return plain, methods


def test_every_recipient_attention_composer_sorts_through_one_helper():
    for name in ("inbox_renderable", "decision_renderable", "deliverable",
                 "owed_renderable", "owed_deliverable"):
        plain, methods = _calls(name)
        assert "_attention_sorted" in plain, name
        assert "sort" not in methods, name


def test_the_sender_side_feed_is_not_reordered_by_severity():
    plain, _ = _calls("renderable")
    assert "_attention_sorted" not in plain


# ---- the listings -----------------------------------------------------------


def test_the_listings_put_severity_between_undecided_and_recency(project):
    ids = _open_all(project, ["normal", "low", "critical", "high", "normal"])
    requests.reject(ids[3], channel="cli-tty", note="no", project_dir=RDIR)
    # undecided first (as today), then most severe, then today's recency
    # order, which is oldest first in a listing
    want = [ids[2], ids[0], ids[4], ids[1], ids[3]]
    assert [r["request_id"] for r in requests.inbox(RDIR).rows] == want
    assert [r["request_id"] for r in requests.listed(project).rows] == want
    assert [r["request_id"] for r in requests.listing(project)] == want
    assert [r["request_id"] for r in requests.inbox_listing(RDIR)] == want


def test_an_all_normal_listing_keeps_todays_order(project):
    ids = _open_all(project, ["normal"] * 3)
    assert [r["request_id"] for r in requests.inbox(RDIR).rows] == ids
    assert [r["request_id"] for r in requests.listed(project).rows] == ids


# ---- the filter -------------------------------------------------------------


@pytest.mark.parametrize("floor,kept", [
    ("critical", {0}), ("high", {0, 1}), ("normal", {0, 1, 2}),
    ("low", {0, 1, 2, 3}), (None, {0, 1, 2, 3})])
def test_min_severity_keeps_that_level_and_anything_more_severe(
        project, floor, kept):
    ids = _open_all(project, ["critical", "high", "normal", "low"])
    want = {ids[i] for i in kept}
    for rows in (requests.inbox(RDIR, min_severity=floor).rows,
                 requests.listed(project, min_severity=floor).rows,
                 requests.inbox_listing(RDIR, min_severity=floor),
                 requests.listing(project, min_severity=floor)):
        assert {r["request_id"] for r in rows} == want


def test_an_unknown_min_severity_is_refused_naming_the_table(project):
    _open_all(project, ["normal"])
    for call in (lambda: requests.inbox(RDIR, min_severity="urgent"),
                 lambda: requests.listed(project, min_severity="urgent")):
        with pytest.raises(requests.RequestError) as err:
            call()
        for level in requests.SEVERITIES:
            assert level in str(err.value)
