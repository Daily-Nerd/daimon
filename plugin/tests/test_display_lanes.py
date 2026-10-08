"""#1129 PR A: the decide queue's lane registry and per-lane text bounds.

Every lane that shows a foreign record's text as a headline bounds that text
in its own lane (never by cutting a composed string), and the full text
reaches the card either as the headline or as a labeled detail line.
"""

import pytest

from daimon_briefing import (
    amendments,
    cli,
    display,
    pending,
    refutations,
    requests,
    store,
    trust,
)
from daimon_briefing.cli import lifecycle
from daimon_briefing.surfaces import Writer

ITEM = "o-1234567890ab"
LONG_ASK = ("Please review the release plan and tell me what is missing.\n"
            + "The rollout touches every host and every adapter. " * 12)
LONG_REASON = "the agent claims a fact nothing in the repo supports " * 8
LONG_LOOP = "ship the fix for the flaky adapter on every host " * 4


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


def _seed_every_kind(bucket, *, sender="/p/X"):
    """One owed row of every lane kind in `bucket`, with long foreign text."""
    requests.open_request(
        to=store.project_slug(bucket), ask=LONG_ASK, why="needed",
        channel="cli-agent", project_dir=sender)
    refutations.assert_ruling(
        subject="release", verdict="never bump without a human call",
        scope="repo", evidence=["issue:766"], channel="cli-agent",
        project_dir=bucket)
    refutations.assert_refutation(
        subject="idf recall", verdict="does not improve recall",
        scope="repo", evidence=["measurement:n=50"], channel="cli-agent",
        project_dir=bucket)
    trust.propose(
        text="a fabricated runbook step", kind="decision",
        reason=LONG_REASON, evidence=["issue:1109"], channel="cli-agent",
        project_dir=bucket)
    store.write_checkpoint("S-1", {
        "session_id": "S-1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"id": ITEM, "text": LONG_LOOP, "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }, project_dir=bucket, writer=Writer.HUMAN)
    a_id = amendments.propose(
        item_id=ITEM, change="progressed", evidence="the PR merged",
        channel="cli-agent", project_dir=bucket)
    amendments.verify(a_id, role="assistant", project_dir=bucket)


def _all_rows(project):
    rows = list(pending.queue(project_dir=project)["rows"])
    for _slug, result in pending.foreign_queues(project_dir=project):
        rows += result["rows"]
    return rows


# --- the registry ---------------------------------------------------------

def test_headline_chars_is_pinned_to_the_ask_bound():
    assert pending.HEADLINE_CHARS == requests.ASK_CHARS == 160


def test_kind_rank_is_derived_from_the_lane_registry():
    registry_kinds = {kind for _source, ranks in pending._LANES
                      for kind in ranks}
    assert set(pending._KIND_RANK) == registry_kinds
    assert pending._KIND_RANK["request"] < pending._KIND_RANK["amendment"]
    assert pending._KIND_RANK["amendment"] < pending._KIND_RANK["trust"]


def test_fixture_kinds_equal_registry_kinds(project):
    _seed_every_kind(project)
    seen = {row["kind"] for row in pending.queue(project_dir=project)["rows"]}
    assert seen == set(pending._KIND_RANK)


def test_every_emitted_row_has_a_registered_kind_local_and_foreign(project):
    _seed_every_kind(project)
    _seed_every_kind("/p/B", sender="/p/Y")
    foreign = pending.foreign_queues(project_dir=project)
    assert foreign
    rows = _all_rows(project)
    assert {row["kind"] for row in rows} == set(pending._KIND_RANK)
    for row in rows:
        assert row["kind"] in pending._KIND_RANK


def test_every_headline_is_one_line_and_bounded(project):
    _seed_every_kind(project)
    _seed_every_kind("/p/B", sender="/p/Y")
    for row in _all_rows(project):
        # Ruling and refutation headlines are out of PR A's scope: they ship
        # with PR D of #1129. Every other lane is census-checked now.
        if row["kind"] in ("ruling", "refutation"):
            continue
        assert "\n" not in row["headline"], row["kind"]
        assert len(row["headline"]) <= pending.HEADLINE_CHARS, row["kind"]


# --- the amendment lane ----------------------------------------------------

def _amend(**over):
    base = {"loop_id": ITEM, "loop_text": "x" * 120, "state_from": "progressed",
            "state_to": "progressed", "role": "assistant",
            "found": amendments.found_label("assistant")}
    base.update(over)
    return base


def test_amendment_headline_keeps_the_full_suffix_and_the_bound():
    amend = _amend(state_from="blocked", state_to="progressed")
    headline = pending._amendment_headline(amend)
    suffix = ": blocked to progressed (found: agent's own words ⚠)"
    assert len(headline) <= pending.HEADLINE_CHARS
    assert headline.endswith(suffix)
    assert headline.startswith("x" * 50)


def test_amendment_headline_bounds_a_very_long_loop_id_fallback():
    amend = _amend(loop_text="(loop text unavailable)", loop_id="o-" + "a" * 400)
    headline = pending._amendment_headline(amend)
    assert len(headline) <= pending.HEADLINE_CHARS
    assert headline.endswith("(found: agent's own words ⚠)")
    assert ": progressed to progressed" in headline


def test_amendment_headline_short_loop_is_unchanged():
    amend = _amend(loop_text="ship it", role="")
    assert pending._amendment_headline(amend) == (
        "ship it: progressed to progressed")


def test_amendment_row_keeps_loop_text_capped_for_the_card(project):
    _seed_every_kind(project)
    row = next(r for r in pending.queue(project_dir=project)["rows"]
               if r["kind"] == "amendment")
    assert len(row["amend"]["loop_text"]) <= pending._LOOP_TEXT_CAP
    assert row["amend"]["loop_text"].endswith("…")
    card = "\n".join(lifecycle._amendment_card(row))
    assert row["amend"]["loop_text"] in card


# --- labeled detail --------------------------------------------------------

def test_detail_helper_is_none_when_nothing_was_shortened():
    assert pending._detail(("Ask", "a  b", "a b")) is None


def test_detail_helper_labels_only_shortened_fields():
    out = pending._detail(("Ask", "one\ntwo  three", "one…"),
                          ("Reason", "same", "same"))
    assert out == "Ask: one two three"


def test_request_row_detail_is_a_labeled_string(project):
    _seed_every_kind(project)
    row = next(r for r in pending.queue(project_dir=project)["rows"]
               if r["kind"] == "request")
    assert isinstance(row["detail"], str)
    assert row["detail"] == "Ask: " + display.one_line(LONG_ASK)


def test_trust_row_headline_is_shortened_and_reason_is_detail(project):
    _seed_every_kind(project)
    row = next(r for r in pending.queue(project_dir=project)["rows"]
               if r["kind"] == "trust")
    assert row["headline"] == display.shorten(
        LONG_REASON, pending.HEADLINE_CHARS)
    assert row["detail"] == "Reason: " + display.one_line(LONG_REASON)


def test_short_trust_reason_has_no_detail(project):
    trust.propose(
        text="a fabricated runbook step", kind="decision",
        reason="no matching PR", evidence=["issue:1109"],
        channel="cli-agent", project_dir=project)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["headline"] == "no matching PR"
    assert row["detail"] is None


def test_multiple_labeled_lines_render_one_per_line_with_hanging_indent():
    row = {"kind": "request", "id": "q-abc", "headline": "h",
           "detail": "Ask: " + "word " * 40 + "\nReason: short",
           "commands": []}
    card = lifecycle._generic_card(row)
    assert card[0].startswith("[request] q-abc  h")
    body = card[1:]
    assert body[0].startswith("  Ask: ")
    assert all(line.startswith(" " * 7) for line in body[1:-1])
    assert body[-1] == "  Reason: short"
    assert all(len(line) <= 76 for line in body)


@pytest.mark.parametrize("kind,full", [
    ("request", LONG_ASK), ("trust", LONG_REASON)])
def test_every_foreign_field_reaches_the_card_whole(project, kind, full):
    _seed_every_kind(project)
    row = next(r for r in pending.queue(project_dir=project)["rows"]
               if r["kind"] == kind)
    text = " ".join("\n".join(lifecycle._generic_card(row)).split())
    assert " ".join(full.split()) in text


def test_decide_prints_the_labeled_ask_line(project, capsys):
    _seed_every_kind(project)
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert "  Ask: Please review the release plan" in out
    assert "  Reason: the agent claims a fact" in out
