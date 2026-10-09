"""The `why` payload contract (#1132 PR 11a): every kind of answer carries the
same keys, a withheld answer carries no value anywhere in either rendering,
and the names a consumer imports do not move. Stores come from the real
writers; the only hand-shaped bytes are events-ledger rows."""

import inspect
import json

import pytest

from daimon_briefing import api, display, inspector, normalize, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/why-contract"
SLUG = store.project_slug(PROJECT)
ITEM = "r-aaaaaaaaaaaa"
SECRET = "SENTINEL-contract the withheld value must never print"
KEY = normalize.content_key(SECRET)
TOP = ["schema_version", "item", "preceding_tool_context", "axes",
       "corroboration", "ranking", "receipt", "source", "lifecycle_event"]
ITEM_KEYS = ["item_id", "kind", "text", "trust", "quote", "author",
             "project_slug", "session_id", "origin_session", "occurrences"]
AXES = ["capture", "provenance", "locator", "bytes", "current_support",
        "verifier_comparison", "lifecycle"]


def _write(text=SECRET, item_id=ITEM):
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-09-01T10:00:00Z",
        "author": "alice",
        "working_context": {
            "active_topic": {"text": "contract", "trust": "inferred"},
            "recent_decisions": [{"text": text, "id": item_id,
                                  "trust": "verbatim", "quote": text}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT, writer=Writer.HUMAN)


def _tombstone(item_ref, text=SECRET):
    store.append_event(item_ref, "forgotten:" + normalize.content_key(text),
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)


def _quarantine():
    _write()
    return trust.propose(text=SECRET, kind="decision", reason="fabricated",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _closed():
    _write()
    (store.config.checkpoint_dir() / SLUG / "trust.jsonl").write_bytes(
        b"this is not json\n")


def _forgotten_with_copy():
    _write()
    _tombstone("o-elsewhere")           # same value, another ref: copy stays


def _tombstone_only():
    _write("another decision that stays visible", "r-bbbbbbbbbbbb")
    _tombstone(ITEM)


def _events_only():
    _write("another decision that stays visible", "r-bbbbbbbbbbbb")
    store.append_event(ITEM, "resolved", source="cli-tty",
                       project_dir=PROJECT, writer=Writer.HUMAN)


WITHHELD = {"quarantine": _quarantine, "closed": _closed,
            "forgotten-with-copy": _forgotten_with_copy,
            "tombstone-only": _tombstone_only}


def test_the_names_a_consumer_imports_do_not_move():
    sig = inspect.signature(inspector.inspect_item)
    assert list(sig.parameters) == [
        "project_dir", "item_id", "include_source", "resolver", "now"]
    assert [p.kind for p in sig.parameters.values()] == [
        inspect.Parameter.POSITIONAL_OR_KEYWORD] * 2 + [
        inspect.Parameter.KEYWORD_ONLY] * 3
    assert sig.parameters["include_source"].default is False
    assert callable(inspector.human_lines)
    assert api.why is inspector.inspect_item
    assert api.why_lines is inspector.human_lines
    assert {"why", "why_lines"} <= set(api.__all__)


def test_an_id_nothing_names_answers_none(tmp_checkpoint_dir):
    _write("another decision that stays visible")
    assert inspector.inspect_item(PROJECT, "r-ffffffffffff") is None
    assert inspector.inspect_item(PROJECT, "r-ffffffffffff",
                                  include_source=True) is None


@pytest.mark.parametrize("name", [*WITHHELD, "events-only"])
def test_every_answer_carries_every_key(tmp_checkpoint_dir, name):
    ({**WITHHELD, "events-only": _events_only}[name])()
    for include_source in (False, True):
        got = inspector.inspect_item(PROJECT, ITEM,
                                     include_source=include_source)
        assert list(got) == TOP + (["source_excerpt"] if include_source
                                   else [])
        assert list(got["item"]) == ITEM_KEYS
        assert list(got["axes"]) == AXES
        assert got["item"]["item_id"] == ITEM
        assert got["item"]["project_slug"] == SLUG
        assert set(got["corroboration"]) == {"count", "references"}
        json.dumps(got)                    # JSON-safe, nothing exotic


@pytest.mark.parametrize("name", list(WITHHELD))
def test_a_withheld_answer_names_the_marker_and_nothing_else(
        tmp_checkpoint_dir, name):
    WITHHELD[name]()
    got = inspector.inspect_item(PROJECT, ITEM, include_source=True)
    reason = {"quarantine": "quarantine", "closed": "closed"}.get(
        name, "forgotten")
    marker = got["item"]["text"]
    assert marker["state"] == "withheld" and marker["reason"] == reason
    assert got["item"]["quote"] == marker
    for key in ("trust", "author", "session_id", "origin_session"):
        assert got["item"][key] is None
    assert got["item"]["occurrences"] == 0
    assert got["preceding_tool_context"] is None
    assert got["ranking"] is None and got["receipt"] is None
    assert got["source"] is None
    assert got["axes"]["current_support"] == "withheld"
    assert got["corroboration"] == {"count": 0, "references": []}
    assert got["source_excerpt"]["state"] == "withheld"
    assert set(got["source_excerpt"]) == {"state", "reason"}
    blob = json.dumps(got) + "\n".join(inspector.human_lines(got))
    assert SECRET not in blob and KEY not in blob and "SENTINEL" not in blob


def test_the_withheld_item_text_is_the_displays_json(tmp_checkpoint_dir):
    _quarantine()
    got = inspector.inspect_item(PROJECT, ITEM)
    w = view.lookup(PROJECT, ITEM)
    assert isinstance(w, view.Withheld)
    assert got["item"]["text"] == display.withheld_json(w)
    assert got["item"]["text"]["quarantine_id"] == w.quarantine_id


@pytest.mark.parametrize("name", [*WITHHELD, "events-only"])
def test_the_human_rendering_never_raises_and_is_short(tmp_checkpoint_dir,
                                                       name):
    ({**WITHHELD, "events-only": _events_only}[name])()
    got = inspector.inspect_item(PROJECT, ITEM, include_source=True)
    lines = inspector.human_lines(got)
    assert lines[0].startswith("Now:") and lines[1].startswith("Item:")
    assert f"[{ITEM}]" in lines[1]
    assert any(line.startswith("Lifecycle:") for line in lines)
    assert not any(line.startswith(("Capture:", "Provenance:", "Ranking:",
                                    "Corroboration:", "Verifier:"))
                   for line in lines)


def test_the_item_line_carries_each_marker(tmp_checkpoint_dir):
    qid = _quarantine()
    lines = inspector.human_lines(inspector.inspect_item(PROJECT, ITEM))
    assert f"[withheld: quarantine {qid}]" in lines[1]
    assert f"daimon trust show {qid}" in lines[1]


def test_a_closed_snapshot_prints_the_cure(tmp_checkpoint_dir):
    _closed()
    lines = inspector.human_lines(inspector.inspect_item(PROJECT, ITEM))
    assert "[withheld: trust ledger unreadable]" in lines[1]
    assert any("daimon status" in line for line in lines)


def test_a_forgotten_answer_says_forgotten_and_never_its_key(tmp_checkpoint_dir):
    _tombstone_only()
    got = inspector.inspect_item(PROJECT, ITEM)
    assert got["lifecycle_event"]["status"] == "forgotten"
    assert got["axes"]["lifecycle"] == "forgotten"
    assert "[withheld: forgotten]" in inspector.human_lines(got)[1]
    assert KEY not in json.dumps(got)


def test_an_events_only_answer_has_no_item_and_a_lifecycle(tmp_checkpoint_dir):
    _events_only()
    got = inspector.inspect_item(PROJECT, ITEM)
    assert got["item"]["kind"] == "unknown" and got["item"]["text"] is None
    assert got["axes"]["lifecycle"] == "resolved"
    assert got["lifecycle_event"]["status"] == "resolved"
    assert got["ranking"] is None
    assert "not retained" in inspector.human_lines(got)[0]
    assert "(content unavailable)" in inspector.human_lines(got)[1]


def test_a_short_rendering_prints_the_source_line_it_is_given():
    lines = inspector.human_lines({
        "item": {"item_id": ITEM, "kind": "decision", "text": None},
        "axes": {"capture": "unknown", "lifecycle": "resolved"},
        "ranking": None,
        "source": {"host": "claude-code", "session_id": "S-9"}})
    assert "Source: claude-code session S-9" in lines
