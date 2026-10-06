"""#1132 2c-2: `daimon ledger repair` and the single-key scrub it shares with
forget.

Ledgers here are built from raw bytes, never through the package writers, so
repair is judged against shapes the writers cannot make.
"""
import json

from daimon_briefing import jsonl

ROW_A = json.dumps({"id": "a", "note": "first"}, ensure_ascii=False)
ROW_B = json.dumps({"id": "b", "note": "second"}, ensure_ascii=False)
SPLIT = json.dumps({"id": "s", "note": "line one line two"},
                   ensure_ascii=False)
TORN = '{"id": "t", "note": "cut off'


def _text(*lines):
    return "".join(line + "\n" for line in lines)


def test_partition_separates_rows_from_torn_and_garbage():
    head, tail = SPLIT.split(" ")
    text = _text(ROW_A, head, tail, TORN, "not json at all", "[1, 2]",
                 "\udcff\udcfe raw", ROW_B)
    result = jsonl.partition(text)
    assert result.rows == [ROW_A, SPLIT, ROW_B]
    assert result.split == 1
    assert result.torn == [TORN]
    assert result.garbage == ["not json at all", "[1, 2]", "\udcff\udcfe raw"]


def test_partition_agrees_with_the_health_read(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_text(ROW_A, TORN, "nope").encode())
    kept = jsonl.partition(path.read_text(errors="surrogateescape")).rows
    assert kept == [ROW_A]
    path.write_bytes(_text(*kept).encode())
    assert jsonl.read(path).health is jsonl.Health.OK


# ---- the shared single-key scrub -------------------------------------------

import pytest  # noqa: E402

from daimon_briefing import (amendments, cli, ledger_repair, refutations,  # noqa: E402
                             relations, requests, store, trust)

_DELETERS = (
    (store, "scrub_event_fields"), (refutations, "forget_content_key"),
    (relations, "forget_item_id"), (amendments, "forget_content_key"),
    (amendments, "forget_item_id"), (requests, "forget_content_key"),
    (trust, "redact_content_key"),
    (ledger_repair, "forget_quarantined_lines"))
_ORDER = ["store.scrub_event_fields", "refutations.forget_content_key",
          "relations.forget_item_id", "amendments.forget_content_key",
          "amendments.forget_item_id", "requests.forget_content_key",
          "trust.redact_content_key", "ledger_repair.forget_quarantined_lines"]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for module, name in _DELETERS:
        label = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"

        def spy(*args, _label=label, **kwargs):
            seen.append((_label, args, kwargs))
            if _label.endswith("forget_quarantined_lines"):
                return ledger_repair.Purged(0, 0)
            return 0 if _label == "store.scrub_event_fields" else []
        monkeypatch.setattr(module, name, spy)
    return seen


def test_scrub_forgotten_key_runs_every_ledger_deleter_in_order(calls):
    ledger_repair.scrub_forgotten_key(
        "k" * 16, item_id="i-1", sibling_ids={"i-3", "i-2"},
        text="the value", project_dir="/p/x")
    assert [c[0] for c in calls] == [
        "store.scrub_event_fields", "refutations.forget_content_key",
        "relations.forget_item_id", "amendments.forget_content_key",
        "amendments.forget_item_id", "amendments.forget_item_id",
        "amendments.forget_item_id", "requests.forget_content_key",
        "trust.redact_content_key", "ledger_repair.forget_quarantined_lines"]
    by_label = {}
    for label, args, kwargs in calls:
        by_label.setdefault(label, []).append((args, kwargs))
    assert by_label["relations.forget_item_id"] == [
        (("i-1",), {"project_dir": "/p/x"})]
    assert [a[0] for a, _ in by_label["amendments.forget_item_id"]] == [
        "i-1", "i-2", "i-3"]
    assert by_label["ledger_repair.forget_quarantined_lines"] == [
        (("k" * 16,), {"text": "the value", "project_dir": "/p/x"})]


def test_forget_reaches_every_ledger_deleter_through_the_shared_entry(
        calls, tmp_checkpoint_dir):
    project = "/p/forget-order"
    store.write_checkpoint(
        "S1", {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
               "working_context": {"recent_decisions": [
                   {"text": "forget me please", "trust": "inferred"}]}},
        project_dir=project)
    assert cli.main(["forget", "forget me please", "--project", project]) == 0
    labels = [c[0] for c in calls]
    first_seen = [label for i, label in enumerate(labels)
                  if label not in labels[:i]]
    assert first_seen == _ORDER
    quarantine_call = [c for c in calls
                       if c[0] == "ledger_repair.forget_quarantined_lines"][0]
    assert quarantine_call[2]["text"] == "forget me please"
