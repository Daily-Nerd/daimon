"""The events, verification and forget-hits readers read per line (#1132 PR 3b).

Every fixture is raw bytes written straight to the file, never through a
package appender: a reader tested against the writers it judges could only
ever agree with them. A row holding a raw U+2028 stays one row, and one
undecodable byte costs its own line, never the file.
"""

import json

from daimon_briefing import store

PROJECT = "/p/store-readers"
SEP = " "


def _dump(row):
    return json.dumps(row, ensure_ascii=False)


def _put(path, *chunks):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))


def _line(row):
    return _dump(row).encode("utf-8") + b"\n"


def _events():
    return store._events_path(PROJECT)


BAD = b"\xff\xfe not utf-8\n"


def _evt(ref, status, ts, **extra):
    return {"ts": ts, "kind": "resolution", "item_ref": ref,
            "status": status, "source": "cli", **extra}


# ---- fold_resolutions: the pure fold ------------------------------------

def test_fold_resolutions_latest_by_ts_wins_never_line_order():
    rows = [_evt("o-a", "reopen", "2026-01-02T00:00:00Z"),
            _evt("o-a", "resolved", "2026-01-01T00:00:00Z")]
    assert store.fold_resolutions(rows)["o-a"]["status"] == "reopen"


def test_fold_resolutions_skips_ref_less_and_non_dict_rows():
    rows = [{"ts": "2026-01-01T00:00:00Z", "kind": "handoff"}, [1], "x",
            _evt("o-a", "resolved", "2026-01-01T00:00:00Z")]
    assert list(store.fold_resolutions(rows)) == ["o-a"]


def test_fold_resolutions_equal_second_reopen_beats_resolved():
    ts = "2026-01-01T00:00:00Z"
    folded = store.fold_resolutions([_evt("o-a", "resolved", ts),
                                     _evt("o-a", "reopen", ts)])
    assert folded["o-a"]["status"] == "reopen"


def test_fold_resolutions_first_of_two_unstamped_rows_wins():
    rows = [{"item_ref": "o-a", "status": "first"},
            {"item_ref": "o-a", "status": "second"}]
    assert store.fold_resolutions(rows)["o-a"]["status"] == "first"


def test_fold_resolutions_unstamped_never_displaces_a_stamped_row():
    rows = [_evt("o-a", "resolved", "2026-01-01T00:00:00Z"),
            {"item_ref": "o-a", "status": "reopen"}]
    assert store.fold_resolutions(rows)["o-a"]["status"] == "resolved"


def test_fold_resolutions_does_not_dedupe_across_refs():
    rows = [_evt("o-a", "resolved", "2026-01-01T00:00:00Z"),
            _evt("o-b", "resolved", "2026-01-01T00:00:00Z")]
    assert sorted(store.fold_resolutions(rows)) == ["o-a", "o-b"]


# ---- resolutions ---------------------------------------------------------

def test_resolutions_read_the_good_rows_around_an_undecodable_line(
        tmp_checkpoint_dir):
    _put(_events(), _line(_evt("o-a", "resolved", "2026-01-01T00:00:00Z")),
         BAD, _line(_evt("o-b", "resolved", "2026-01-01T00:00:00Z")))
    assert sorted(store.resolutions(project_dir=PROJECT)) == ["o-a", "o-b"]


def test_resolutions_keep_a_row_holding_a_raw_line_separator(
        tmp_checkpoint_dir):
    _put(_events(), _line(_evt("o-a", "resolved", "2026-01-01T00:00:00Z",
                               note=f"before{SEP}after")))
    folded = store.resolutions(project_dir=PROJECT)
    assert folded["o-a"]["note"] == f"before{SEP}after"


# ---- item_events and corroborations --------------------------------------

def test_item_events_keep_a_row_with_a_raw_line_separator_and_skip_bad_bytes(
        tmp_checkpoint_dir):
    _put(_events(), _line(_evt("o-a", "resolved", "2026-01-01T00:00:00Z",
                               note=f"x{SEP}y")), BAD,
         _line(_evt("o-a", "reopen", "2026-01-02T00:00:00Z")))
    got = store.item_events("o-a", project_dir=PROJECT)
    assert [e["status"] for e in got] == ["resolved", "reopen"]
    assert got[0]["note"] == f"x{SEP}y"


def test_fold_item_events_orders_by_ts_then_row_position():
    rows = [_evt("o-a", "second", "2026-01-01T00:00:00Z"),
            _evt("o-b", "other", "2026-01-01T00:00:00Z"),
            _evt("o-a", "first", "2026-01-01T00:00:00Z"),
            {"item_ref": "o-a", "status": "unstamped"}]
    got = store.fold_item_events(rows, "o-a")
    assert [e["status"] for e in got] == ["unstamped", "second", "first"]


def _corroboration(item, observer, ts, **extra):
    return {"ts": ts, "kind": "corroboration",
            "item_ref": store.corroboration_ref(item),
            "status": f"corroborated-by:{observer}", "source": "serializer",
            **extra}


def test_corroborations_read_around_bad_bytes_and_a_raw_line_separator(
        tmp_checkpoint_dir):
    _put(_events(),
         _line(_corroboration("o-a", "S-1", "2026-01-01T00:00:00Z",
                              note=f"x{SEP}y")), BAD,
         _line(_corroboration("o-a", "S-2", "2026-01-02T00:00:00Z")))
    got = store.corroborations(project_dir=PROJECT)
    assert got["o-a"]["origins"] == {"S-1", "S-2"}


def test_fold_corroborations_demotion_cancels_earlier_witnesses():
    rows = [_corroboration("o-a", "S-1", "2026-01-01T00:00:00Z"),
            _evt("o-a", "resolved", "2026-01-02T00:00:00Z")]
    got = store.fold_corroborations(rows)
    assert got["o-a"]["origins"] == set()
    assert got["o-a"]["recorded"] == {"S-1"}


# ---- active_handoff -------------------------------------------------------

def _handoff(note, ts="2026-01-01T00:00:00Z"):
    return {"ts": ts, "kind": "handoff", "item_ref": "", "status": "set",
            "source": "cli", "note": note}


def test_active_handoff_survives_undecodable_bytes(tmp_checkpoint_dir):
    _put(_events(), BAD, _line(_handoff("pick up the migration")))
    got = store.active_handoff(PROJECT)
    assert got is not None and got["note"] == "pick up the migration"


def test_active_handoff_keeps_a_note_with_a_raw_line_separator(
        tmp_checkpoint_dir):
    _put(_events(), _line(_handoff(f"first{SEP}second")))
    got = store.active_handoff(PROJECT)
    assert got is not None and got["note"] == f"first{SEP}second"


# ---- verification_rows and forget_hit_stats -------------------------------

def test_verification_rows_read_around_bad_bytes_and_a_line_separator(
        tmp_checkpoint_dir):
    path = store._ledger_path(PROJECT)
    _put(path, _line({"ts": "t1", "check": "quote", "item_ref": "o-a",
                      "reason": f"x{SEP}y"}), BAD,
         b"not json at all\n",
         _line({"ts": "t2", "check": "quote", "item_ref": "o-b",
                "reason": "z"}))
    rows = store.verification_rows(PROJECT)
    assert [r["item_ref"] for r in rows] == ["o-a", "o-b"]
    assert rows[0]["reason"] == f"x{SEP}y"


def test_verification_rows_by_bucket_read_around_bad_bytes(
        tmp_checkpoint_dir):
    path = store._ledger_path(PROJECT)
    _put(path, BAD, _line({"ts": "t", "check": "c", "item_ref": "o-a",
                           "reason": "r"}))
    assert len(store.verification_rows(bucket=path.parent)) == 1


def test_forget_hit_stats_count_around_bad_bytes_and_a_line_separator(
        tmp_checkpoint_dir):
    path = store._forget_hits_path(PROJECT)
    _put(path, _line({"ts": "2026-01-01T00:00:00Z", "key": "k1"}), BAD,
         _line({"ts": "2026-01-02T00:00:00Z", "key": "k2",
                "reason": f"ruling-echo{SEP}"}),
         _line({"ts": "2026-01-03T00:00:00Z", "key": "k3"}))
    got = store.forget_hit_stats(PROJECT)
    assert got["count"] == 3
    assert got["last_hit_at"] == "2026-01-03T00:00:00Z"
