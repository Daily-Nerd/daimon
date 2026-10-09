"""`why --source` refusal (#1132 PR 11a, H2): a transcript window is raw text,
so the refusal is set-based. It names a reason from four and never a count.
Cost, stated: once anything was forgotten anywhere on the machine, `why
--source` is refused everywhere, for good (tombstones never expire)."""

import json

import pytest

from daimon_briefing import config, inspector, normalize, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/src-refusal"
OTHER = "/p/src-refusal-other"
SLUG = store.project_slug(PROJECT)
ITEM = "r-aaaaaaaaaaaa"
TEXT = "the retry budget stays at six attempts per request"


@pytest.fixture
def written(tmp_checkpoint_dir):
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-09-01T10:00:00Z",
        "author": "alice",
        "working_context": {
            "active_topic": {"text": "refusal", "trust": "inferred"},
            "recent_decisions": [{"text": TEXT, "id": ITEM,
                                  "trust": "inferred", "quote": TEXT}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT, writer=Writer.HUMAN)


def _excerpt(**kw):
    return inspector.inspect_item(PROJECT, ITEM, include_source=True,
                                  **kw)["source_excerpt"]


def _forget_elsewhere(project=OTHER, text="a value another project forgot"):
    store.append_event("o-elsewhere", "forgotten:" + normalize.content_key(text),
                       kind="tombstone", tombstone=True, project_dir=project,
                       writer=Writer.HUMAN)


def test_with_no_set_the_window_is_offered(written):
    assert _excerpt()["kind"] == "stored-quote"


def test_a_tombstone_in_another_project_refuses_the_window(written):
    _forget_elsewhere()
    assert _excerpt() == {"state": "withheld", "reason": "forgotten-set"}


def test_a_tombstone_in_this_project_refuses_the_window(written):
    _forget_elsewhere(PROJECT)
    assert _excerpt() == {"state": "withheld", "reason": "forgotten-set"}


def test_a_quarantine_in_this_project_refuses_the_window(written):
    trust.propose(text="an unrelated claim that is quarantined here",
                  kind="belief", reason="fabricated", evidence=["issue:1"],
                  channel="cli-tty", project_dir=PROJECT)
    assert _excerpt() == {"state": "withheld", "reason": "quarantine-set"}


def test_a_quarantine_in_another_project_does_not(written):
    trust.propose(text="an unrelated claim that is quarantined there",
                  kind="belief", reason="fabricated", evidence=["issue:1"],
                  channel="cli-tty", project_dir=OTHER)
    assert _excerpt()["kind"] == "stored-quote"


def test_an_unreadable_trust_ledger_refuses_the_window(written):
    (config.checkpoint_dir() / SLUG / "trust.jsonl").write_bytes(b"not json\n")
    assert _excerpt() == {"state": "withheld", "reason": "closed"}


def test_closed_outranks_forgotten_outranks_quarantine(written):
    _forget_elsewhere()
    trust.propose(text="an unrelated claim that is quarantined here",
                  kind="belief", reason="fabricated", evidence=["issue:1"],
                  channel="cli-tty", project_dir=PROJECT)
    assert _excerpt()["reason"] == "forgotten-set"
    (config.checkpoint_dir() / SLUG / "trust.jsonl").write_bytes(b"not json\n")
    assert _excerpt()["reason"] == "closed"


def test_the_item_itself_is_the_last_reason():
    assert inspector._source_refusal(view.Snapshot.empty(),
                                     item_withheld=True) == "withheld-item"
    assert inspector._source_refusal(view.Snapshot.empty(),
                                     item_withheld=False) is None
    assert inspector._source_refusal(None, item_withheld=False) is None


def test_the_refusal_publishes_no_count_whatever_the_tombstones(written):
    for n in range(3):
        _forget_elsewhere(text=f"a value another project forgot number {n}")
    got = _excerpt()
    assert set(got) == {"state", "reason"}
    assert not any(ch.isdigit() for ch in json.dumps(got))


def test_a_withheld_item_refuses_with_the_set_that_withheld_it(written):
    _forget_elsewhere(PROJECT, TEXT)               # this very value, forgotten
    got = inspector.inspect_item(PROJECT, ITEM, include_source=True)
    assert got["item"]["text"]["reason"] == "forgotten"
    assert got["source_excerpt"] == {"state": "withheld",
                                     "reason": "forgotten-set"}


def test_every_reason_has_words_in_the_human_rendering(written):
    for reason in inspector._SOURCE_REFUSALS:
        lines = inspector.human_lines({
            "item": {"item_id": ITEM, "kind": "decision",
                     "text": {"state": "withheld", "reason": "forgotten"}},
            "axes": {"capture": "unknown", "lifecycle": "active"},
            "ranking": None,
            "source_excerpt": {"state": "withheld", "reason": reason}})
        assert lines[-1].startswith("Source excerpt: withheld, ")
        assert reason not in lines[-1]


def test_an_unknown_reason_from_a_newer_producer_still_renders(written):
    lines = inspector.human_lines({
        "item": {"item_id": ITEM, "kind": "decision", "text": None},
        "axes": {"capture": "unknown", "lifecycle": "active"},
        "ranking": None,
        "source_excerpt": {"state": "withheld", "reason": "something-new"}})
    assert lines[-1] == "Source excerpt: withheld, this item is withheld"
    assert Writer.HUMAN
