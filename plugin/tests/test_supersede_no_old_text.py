"""A supersede candidate names ids, never the old wording (#1132 PR 11b, H3).
`events.jsonl` is append-only, so a value copied into `item_text` would have to
be scrubbed by a later forget; the reader of an id-shaped ref never uses it
(`Snapshot.fuzzy_events` skips id refs). The forget gate stays: it is free and
it also covers the sibling-id shape (#418)."""

import json

from daimon_briefing import capture, normalize, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/supersede-no-old-text"
OLD = "adopt sqlite for the recall index cache"
OTHER = "route telemetry through the vector gateway"


def _rows():
    path = store.ledger_file(PROJECT, "events.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_a_candidate_row_has_no_item_text_at_all(tmp_checkpoint_dir):
    n = capture._emit_supersede_candidates(
        [("r-old0001", "r-new0001", OLD)], {}, PROJECT)
    assert n == 1
    (row,) = _rows()
    assert row["item_ref"] == "r-old0001"
    assert row["status"] == "supersede-candidate:r-new0001"
    assert row["source"] == "serializer"
    assert "item_text" not in row
    assert OLD.encode() not in store.ledger_file(
        PROJECT, "events.jsonl").read_bytes()


def test_the_forget_gate_still_skips_a_forgotten_value(tmp_checkpoint_dir):
    store.append_event("r-gone0001",
                       f"forgotten:{normalize.content_key(OLD)}",
                       project_dir=PROJECT, tombstone=True, writer=Writer.HUMAN)
    pairs = [("r-old0001", "r-new0001", OLD),
             ("r-old0002", "r-new0002", OTHER)]
    n = capture._emit_supersede_candidates(
        pairs, store.resolutions(project_dir=PROJECT), PROJECT)
    assert n == 1
    refs = {r["item_ref"] for r in _rows()}
    assert "r-old0001" not in refs and "r-old0002" in refs


def test_the_arity_and_the_injected_forgotten_set_are_unchanged(tmp_checkpoint_dir):
    key = normalize.content_key(OLD)
    n = capture._emit_supersede_candidates(
        [("r-old0001", "r-new0001", OLD)], {}, PROJECT,
        forgotten=frozenset({key}))
    assert n == 0
