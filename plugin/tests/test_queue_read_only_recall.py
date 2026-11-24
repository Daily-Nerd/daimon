"""#1407: a queue read never rebuilds the recall index.

The defect: `_amendment_rows` looked the loop text up once per verified
amendment row through a read that refreshes the index (a machine-wide
rebuild when the fingerprint mismatches, plus an mtime touch on every
read), so a display-only `pending.queue` paid the whole index build per row.
The fix: ONE batched `view.lookup_many` per queue read, the canonical
exact-id read (pointer window plus `index_locate`, `mode=ro`, never
refreshed; scar 0136). `view.lookup_many` and `index_locate` own the
lookup's own semantics (newest row, scope, closed snapshot, mode=ro); these
tests pin what the QUEUE does with it.
"""

import json
import os

import pytest

from daimon_briefing import (amendments, config, pending, recall, store, trust,
                             view)
from daimon_briefing.surfaces import Writer

ITEM = "o-1234567890ab"
TEXT = "ship the fix"
PLACEHOLDER = "(loop text unavailable)"


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


@pytest.fixture
def no_refresh(monkeypatch):
    """Every way a read could rebuild or refresh the index raises, the style
    tests/test_why_cost.py uses. Yields the real `recall.rebuild` so a test
    can still build an index to be stale against."""
    real_rebuild = recall.rebuild

    def boom(*_args, **_kwargs):
        raise AssertionError("a queue read must never refresh the index")

    monkeypatch.setattr(recall, "rebuild", boom)
    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    monkeypatch.setattr(recall, "_rebuild_forced", boom)
    return real_rebuild


def _cp(item_id=ITEM, text=TEXT):
    return {
        "session_id": f"S-{item_id}",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"id": item_id, "text": text, "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _verified_amendment(project, item_id=ITEM):
    a_id = amendments.propose(item_id=item_id, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    return a_id


def _amendment_rows(project):
    return [r for r in pending.queue(project_dir=project)["rows"]
            if r["kind"] == "amendment"]


# ---- the regression: a display read never pays for the index ---------------


def test_queue_read_with_stale_index_never_rebuilds(project, no_refresh):
    _verified_amendment(project)
    store.write_checkpoint("S-1", _cp(), project_dir=project,
                           writer=Writer.HUMAN)
    assert no_refresh() > 0     # the REAL rebuild, taken before the guards
    # A new checkpoint lands without a rebuild: the fingerprint now
    # mismatches, exactly the state in which the old per-row read rebuilt.
    store.write_checkpoint("S-2", _cp(item_id="o-222222222222",
                                      text="second session"),
                           project_dir=project, writer=Writer.HUMAN)
    db = config.recall_db()
    before = db.read_bytes()
    before_ns = os.stat(db).st_mtime_ns
    rows = _amendment_rows(project)
    assert os.stat(db).st_mtime_ns == before_ns   # no touch
    assert db.read_bytes() == before              # no write
    assert [r["amend"]["loop_text"] for r in rows] == [TEXT]


def test_queue_read_with_missing_index_never_builds_one(
        project, no_refresh):
    _verified_amendment(project)
    store.write_checkpoint("S-1", _cp(), project_dir=project,
                           writer=Writer.HUMAN)
    rows = _amendment_rows(project)
    assert not config.recall_db().exists()
    # The pointer window still holds the checkpoint, so the text is served
    # without any index.
    assert [r["amend"]["loop_text"] for r in rows] == [TEXT]


def test_queue_read_with_no_text_anywhere_shows_the_placeholder(
        project, no_refresh):
    _verified_amendment(project)          # no checkpoint holds the item
    rows = _amendment_rows(project)
    assert [r["amend"]["loop_text"] for r in rows] == [PLACEHOLDER]
    assert not config.recall_db().exists()


# ---- pending: one batched lookup per queue read ----------------------------


def test_amendment_rows_use_one_batched_lookup(project, monkeypatch,
                                               no_refresh):
    ids = [f"o-{i:012d}" for i in range(3)]
    for item_id in ids:
        _verified_amendment(project, item_id=item_id)
    calls = []
    real = view.lookup_many

    def spy(proj, item_ids, **kwargs):
        calls.append(list(item_ids))
        return real(proj, item_ids, **kwargs)

    monkeypatch.setattr(pending.view, "lookup_many", spy)
    rows = _amendment_rows(project)
    assert len(rows) == 3
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted(ids)
    assert all(r["amend"]["loop_text"] == PLACEHOLDER for r in rows)


def test_amendment_row_withheld_loop_text_is_the_placeholder(
        project, no_refresh):
    secret = "the quokkasentinel claim was fabricated by the model"
    store.write_checkpoint("S-1", {
        "session_id": "S-1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{"text": secret, "trust": "inferred"}],
        },
        "epistemic_snapshot": {},
    }, project_dir=project, writer=Writer.HUMAN)
    body = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                  admit=store.Admit.ANY)
    item_id = body["working_context"]["recent_decisions"][0]["id"]
    _verified_amendment(project, item_id=item_id)
    trust.propose(text=secret, kind="decision", reason="fabricated finding",
                  evidence=["issue:1"], channel="cli-tty",
                  project_dir=project)
    rows = _amendment_rows(project)
    assert [r["amend"]["loop_text"] for r in rows] == [PLACEHOLDER]
    assert secret not in json.dumps(rows)


def test_queue_read_with_a_corrupt_index_keeps_the_rows(project, no_refresh):
    # view.lookup_many is fail-open on a broken index (a note, not a raise),
    # so the queue keeps every row with the placeholder and never repairs it.
    for i in range(2):
        _verified_amendment(project, item_id=f"o-{i:012d}")
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("not a sqlite file", encoding="utf-8")
    rows = _amendment_rows(project)
    assert len(rows) == 2
    assert all(r["amend"]["loop_text"] == PLACEHOLDER for r in rows)
    assert db.read_text(encoding="utf-8") == "not a sqlite file"


def test_amendment_row_loop_text_contract_after_batching(project):
    # The display contract of the amendment lane survives the batching:
    # real text still reaches the row, capped and one-lined.
    long_text = "wrap the flaky adapter  " + "on every host " * 30
    _verified_amendment(project)
    store.write_checkpoint("S-1", _cp(ITEM, long_text),
                           project_dir=project, writer=Writer.HUMAN)
    row = _amendment_rows(project)[0]
    text = row["amend"]["loop_text"]
    assert "\n" not in text
    assert text.startswith("wrap the flaky adapter on every host")
    assert len(text) <= pending._LOOP_TEXT_CAP
