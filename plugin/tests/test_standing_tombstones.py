"""PR 12: the key tombstone fold stands apart from the item fold.

A forget writes a second row on a `forgotten-key:` ref. That ref is not an
item, so `fold_resolutions` (the item fold every reader shares) must never
return it, and `standing_tombstones` is the one parser that reads both kinds
of ref. Every hostile row here is planted with a plain byte writer, never
through the package writers, so the reader is judged against shapes the
writers do not make.
"""
import json

from daimon_briefing import (cli, config, normalize, store, view)
from daimon_briefing.cli import stats as cli_stats

_A = "/repo/standing-A"
_VALUE = "the staging password is correct horse battery staple"
_KEY = normalize.content_key(_VALUE)
_OTHER = "rotate the signing certificate every ninety days"
_OTHER_KEY = normalize.content_key(_OTHER)
_KEYREF = "forgotten-key:00aa11bb22cc33dd"
_KEYREF2 = "forgotten-key:44ee55ff66aa77bb"


def _row(ref, status, ts="2026-10-01T00:00:00Z", kind="tombstone", **extra):
    return {"ts": ts, "kind": kind, "item_ref": ref, "status": status,
            "source": "cli", **extra}


def _plant(*rows, project=_A):
    """Append rows with the independent byte writer."""
    path = config.checkpoint_dir() / store.project_slug(project) / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as handle:
        for row in rows:
            handle.write((json.dumps(row) + "\n").encode("utf-8"))
    return path


# ---- the predicate and the item fold ---------------------------------------

def test_is_key_ref_names_only_the_key_namespace():
    assert store.is_key_ref(_KEYREF)
    assert store.is_key_ref("forgotten-key:")
    assert not store.is_key_ref("i-1a2b3c")
    assert not store.is_key_ref("corroboration:i-1a2b3c")
    assert not store.is_key_ref("")
    assert not store.is_key_ref(None)


def test_the_item_fold_never_returns_a_key_ref():
    rows = [_row("i-1", f"forgotten:{_KEY}"), _row(_KEYREF, f"forgotten:{_KEY}")]
    assert set(store.fold_resolutions(rows)) == {"i-1"}


def test_no_item_reader_meets_a_key_ref(tmp_checkpoint_dir):
    _plant(_row("i-1", f"forgotten:{_KEY}"), _row(_KEYREF, f"forgotten:{_KEY}"),
           _row("i-2", "resolved", kind="resolution"))
    assert set(store.resolutions(project_dir=_A)) == {"i-1", "i-2"}
    assert store.tombstone_refs(store.resolutions(project_dir=_A)) == {"i-1"}
    assert store.tombstoned_item_ids(project_dir=_A) == {"i-1"}
    snap = view.snapshot(_A)
    assert snap.resolved_refs == {"i-1", "i-2"}
    assert view.forgotten_ids(snap.resolutions) == {"i-1"}


def test_the_stats_total_counts_items_not_key_refs(tmp_checkpoint_dir):
    _plant(_row("i-1", f"forgotten:{_KEY}"), _row(_KEYREF, f"forgotten:{_KEY}"))
    assert cli_stats._stats_events(_A)["resolved_refs"] == 1


def test_the_privacy_residue_set_holds_item_ids_only(tmp_checkpoint_dir):
    _plant(_row(_KEYREF, f"forgotten:{_KEY}"))
    assert store.tombstoned_item_ids(project_dir=_A) == set()
    assert store.forgotten_content_keys(_A) == {_KEY}


# ---- the one parser --------------------------------------------------------

def test_standing_tombstones_maps_a_key_to_its_ids_and_key_refs():
    rows = [_row("i-9", f"forgotten:{_KEY}"), _row("i-3", f"forgotten:{_KEY}"),
            _row(_KEYREF, f"forgotten:{_KEY}"),
            _row("i-5", f"forgotten:{_OTHER_KEY}")]
    got = store.standing_tombstones(rows)
    assert set(got) == {_KEY, _OTHER_KEY}
    assert got[_KEY].ids == ("i-3", "i-9")
    assert got[_KEY].key_refs == (_KEYREF,)
    assert got[_OTHER_KEY] == store.Standing(("i-5",), ())


def test_a_reopened_id_leaves_the_key_standing_on_its_key_ref():
    rows = [_row("i-1", f"forgotten:{_KEY}", ts="2026-10-01T00:00:00Z"),
            _row(_KEYREF, f"forgotten:{_KEY}", ts="2026-10-01T00:00:00Z"),
            _row("i-1", "reopened", ts="2026-10-02T00:00:00Z",
                 kind="resolution")]
    got = store.standing_tombstones(rows)
    assert got[_KEY] == store.Standing((), (_KEYREF,))


def test_a_lifted_key_ref_is_no_longer_standing():
    rows = [_row("i-1", f"forgotten:{_KEY}"), _row(_KEYREF, f"forgotten:{_KEY}"),
            _row("i-1", store.LIFT_STATUS, ts="2026-10-02T00:00:00Z",
                 kind="lift"),
            _row(_KEYREF, store.LIFT_STATUS, ts="2026-10-02T00:00:00Z",
                 kind="lift")]
    assert store.standing_tombstones(rows) == {}


def test_a_status_with_leading_space_still_counts_as_standing():
    # over-suppress direction: the stripped parse is the one parse
    rows = [_row("i-1", f"  Forgotten: {_KEY}  ")]
    assert set(store.standing_tombstones(rows)) == {_KEY}


def test_a_free_form_status_that_only_starts_with_the_word_is_not_standing():
    rows = [_row("i-1", "forgotten about it", kind="resolution")]
    assert store.standing_tombstones(rows) == {}


def test_the_key_set_is_a_view_of_the_standing_map(tmp_checkpoint_dir):
    path = _plant(_row(_KEYREF, f"forgotten:{_KEY}"),
                  _row("i-2", f"forgotten:{_OTHER_KEY}"))
    assert store.forgotten_content_keys(_A) == {_KEY, _OTHER_KEY}
    assert store._tombstoned_keys(store.jsonl.read(path).rows) \
        == set(store.standing_tombstones(store.jsonl.read(path).rows))


# ---- ledger repair reads the standing map ----------------------------------

def test_repair_rescrubs_a_key_that_only_a_key_ref_holds(tmp_checkpoint_dir):
    bucket = config.checkpoint_dir() / store.project_slug(_A)
    bucket.mkdir(parents=True)
    _plant(_row(_KEYREF, f"forgotten:{_KEY}"))
    (bucket / "trust.jsonl").write_bytes((json.dumps(
        {"quarantine_id": "tr-aaa", "event": "proposed", "reason": _VALUE,
         "evidence": ["issue:1"]}) + "\n").encode())
    assert cli.main(["ledger", "repair", "trust", "--project", _A]) == 0
    text = (bucket / "trust.jsonl").read_text(encoding="utf-8")
    assert _VALUE not in text
    assert f"[forgotten:{_KEY}]" in text


# ---- the lift status -------------------------------------------------------

def test_the_lift_status_is_one_literal_behind_one_predicate():
    assert store.is_lift_status(store.LIFT_STATUS)
    assert store.is_lift_status("  Reopened:Unforget ")
    assert not store.is_lift_status("reopened")
    assert not store.is_lift_status(f"forgotten:{_KEY}")
    assert not store.is_lift_status(None)


def test_a_lift_is_live_and_never_a_resolution():
    lift = _row("i-1", store.LIFT_STATUS, kind="lift")
    assert not store.is_resolved(lift)
    assert view.lifecycle_word(lift) == "active"


def test_a_lift_wins_a_same_second_tie_in_either_row_order():
    forget = _row("i-1", f"forgotten:{_KEY}", ts="2026-10-03T00:00:00Z")
    for lift_status, kind in ((store.LIFT_STATUS, "lift"),
                              (store.LIFT_STATUS, "tombstone"),
                              ("reopened", "resolution"),
                              ("reopen", "resolution")):
        lift = _row("i-1", lift_status, ts="2026-10-03T00:00:00Z", kind=kind)
        for rows in ([forget, lift], [lift, forget]):
            assert store.fold_resolutions(rows)["i-1"]["status"] == lift_status
            assert store.standing_tombstones(rows) == {}


def test_the_lift_word_survives_a_status_redaction():
    kept = store._class_preserving_marker(store.LIFT_STATUS, "[m]")
    assert store.is_lift_status(kept)
