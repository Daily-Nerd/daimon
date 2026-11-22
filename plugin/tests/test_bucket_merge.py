"""`bucket merge`: the one-time fold of a split bucket into its main (slice 3b).

The repair for a split that already exists: a worktree bucket, a ghost bucket,
a fresh bucket born before the user rebound. After it G no longer exists, the
alias row `G -> B` is minted, and nothing is synchronised, so no second source
of truth remains.

The pointer rule (owner decision D16): G's pointers fill B's free slots and
never evict. Then ONE rotation step promotes G's newest payload to B's latest
when it is newer than B's latest, exactly what one more live session in B would
do. What fits no slot is dropped and counted, never silently: the flat signed
`<session>.json` files still hold the bytes.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from daimon_briefing import buckets, config, store
from tests.test_bucket_migration import _marker, _row, _write


@pytest.fixture
def pair(tmp_path, tmp_checkpoint_dir):
    """(main project dir, worktree project dir, main bucket, worktree bucket),
    both directories real and both buckets stamped by real writes."""
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    main.mkdir()
    wt.mkdir()
    return (str(main), str(wt), store.project_bucket(str(main)),
            store.project_bucket(str(wt)))


def _dir(slug: str) -> Path:
    return config.checkpoint_dir() / slug


def _pointers(slug: str) -> dict:
    d = _dir(slug)
    return {p.name: _marker(p) for p in sorted(d.glob("*.json"))
            if store._POINTER_RE.match(p.name)}


def _snapshot() -> dict:
    root = config.checkpoint_dir()
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None,
                                       p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def _fill(main, wt):
    """B holds b1..b3 (full chain at the default history of 3), G holds g1,g2
    both NEWER than every pointer in B: the worktree-newer-than-main case."""
    for marker, created in (("b1", "2026-11-01T00:00:00Z"),
                            ("b2", "2026-11-02T00:00:00Z"),
                            ("b3", "2026-11-03T00:00:00Z")):
        _write(main, marker, created)
    for marker, created in (("g1", "2026-11-10T00:00:00Z"),
                            ("g2", "2026-11-11T00:00:00Z")):
        _write(wt, marker, created)


# ---------------------------------------------------------------------------
# the acceptance case: a worktree session newer than main becomes main's latest
# ---------------------------------------------------------------------------


def test_a_worktree_session_newer_than_main_becomes_main_latest(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    assert _pointers(b) == {"latest.json": "b3", "prev-1.json": "b2",
                            "prev-2.json": "b1"}
    shutil.rmtree(wt)  # the worktree is gone: nothing re-births G
    record = buckets.merge(g, b, apply=True)
    assert _pointers(b) == {"latest.json": "g2", "prev-1.json": "b3",
                            "prev-2.json": "b2"}
    assert record["promoted"] == "g2"
    assert record["pointers_evicted"] == ["b1"]
    assert record["pointers_dropped"] == ["g1"]
    assert not _dir(g).exists()
    assert record["complete"] is True


def test_the_promoted_copy_is_restamped_to_the_bucket_it_lives_in(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    buckets.merge(g, b, apply=True)
    latest = json.loads((_dir(b) / "latest.json").read_text(encoding="utf-8"))
    assert latest["project_slug"] == b


def test_a_free_slot_keeps_every_pointer_of_both_buckets(pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    _write(wt, "g2", "2026-11-11T00:00:00Z")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    assert sorted(_pointers(b).values()) == ["b1", "g1", "g2"]
    assert _pointers(b)["latest.json"] == "g2"
    assert record["pointers_evicted"] == []
    assert record["pointers_dropped"] == []


def test_an_older_worktree_never_displaces_a_live_pointer(pair):
    main, wt, b, g = pair
    for marker, created in (("b1", "2026-11-05T00:00:00Z"),
                            ("b2", "2026-11-06T00:00:00Z"),
                            ("b3", "2026-11-07T00:00:00Z")):
        _write(main, marker, created)
    _write(wt, "g1", "2026-11-01T00:00:00Z")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    assert _pointers(b) == {"latest.json": "b3", "prev-1.json": "b2",
                            "prev-2.json": "b1"}
    assert record["promoted"] is None
    assert record["pointers_evicted"] == []
    assert record["pointers_dropped"] == ["g1"]
    assert not _dir(g).exists(), "the dropped pointer's bytes live in the flat file"
    assert (config.checkpoint_dir() / "g1.json").is_file()


def test_a_bucket_with_no_latest_takes_the_newest_worktree_pointer(pair):
    main, wt, b, g = pair
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    _write(wt, "g2", "2026-11-11T00:00:00Z")
    shutil.rmtree(wt)
    _dir(b).mkdir(parents=True, exist_ok=True)
    buckets.merge(g, b, apply=True)
    assert _pointers(b)["latest.json"] == "g2"


# ---------------------------------------------------------------------------
# the receipt, the alias, idempotence
# ---------------------------------------------------------------------------


def test_the_alias_row_is_minted_even_when_every_pointer_was_dropped(pair):
    main, wt, b, g = pair
    for marker, created in (("b1", "2026-11-05T00:00:00Z"),
                            ("b2", "2026-11-06T00:00:00Z"),
                            ("b3", "2026-11-07T00:00:00Z")):
        _write(main, marker, created)
    _write(wt, "g1", "2026-11-01T00:00:00Z")
    shutil.rmtree(wt)
    buckets.merge(g, b, apply=True)
    assert buckets.alias_map() == {g: b}
    assert buckets.aliases_for(b) == frozenset({g})


def test_a_pointer_only_bucket_mints_an_alias(pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    assert [p.name for p in _dir(g).iterdir()
            if p.suffix == ".jsonl"] == [], "fixture: no ledger in G"
    buckets.merge(g, b, apply=True)
    assert buckets.alias_map() == {g: b}


def test_a_second_run_is_a_no_op_and_writes_no_second_receipt(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    buckets.merge(g, b, apply=True)
    rows = buckets.migrations_path().read_text(encoding="utf-8")
    again = buckets.merge(g, b, apply=True)
    assert again["mode"] == "absent"
    assert buckets.migrations_path().read_text(encoding="utf-8") == rows


def test_the_target_root_is_never_rewritten(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    before = (_dir(b) / "root").read_bytes()
    buckets.merge(g, b, apply=True)
    assert (_dir(b) / "root").read_bytes() == before


# ---------------------------------------------------------------------------
# ledgers
# ---------------------------------------------------------------------------


def test_ledgers_are_unioned_with_byte_equal_dedup(pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    (_dir(b) / "requests.jsonl").write_text(_row("shared") + _row("only-b"),
                                            encoding="utf-8")
    (_dir(g) / "requests.jsonl").write_text(_row("shared") + _row("only-g"),
                                            encoding="utf-8")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    lines = (_dir(b) / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert sorted(json.loads(x)["event_id"] for x in lines) == [
        "only-b", "only-g", "shared"]
    assert record["ledgers"]["requests.jsonl"] == 1


# ---------------------------------------------------------------------------
# signed bytes, dry run
# ---------------------------------------------------------------------------


def test_signed_flat_files_and_receipts_are_byte_identical_after_a_merge(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    flat = {p.name: p.read_bytes()
            for p in config.checkpoint_dir().glob("*")
            if p.is_file() and p.suffix in (".json", ".receipt")
            and not store._POINTER_RE.match(p.name)}
    assert flat, "fixture: the writes left flat files"
    buckets.merge(g, b, apply=True)
    assert {name: (config.checkpoint_dir() / name).read_bytes()
            for name in flat} == flat


def test_a_dry_run_leaves_the_store_byte_identical_and_predicts_the_run(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    before = _snapshot()
    planned = buckets.merge(g, b, apply=False)
    assert _snapshot() == before
    applied = buckets.merge(g, b, apply=True)
    for key in ("promoted", "pointers_evicted", "pointers_dropped", "ledgers",
                "leftovers", "complete"):
        assert planned[key] == applied[key], key


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------


def test_a_live_root_is_refused_without_force(pair):
    main, wt, b, g = pair
    _fill(main, wt)  # the worktree directory still exists on disk
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="still exists"):
        buckets.merge(g, b, apply=True)
    assert _snapshot() == before


def test_force_merges_a_live_root_bucket(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    buckets.merge(g, b, apply=True, force=True)
    assert not _dir(g).exists()
    assert buckets.alias_map() == {g: b}


def test_a_dry_run_also_reports_the_live_root_refusal(pair):
    main, wt, b, g = pair
    _fill(main, wt)
    with pytest.raises(buckets.MigrationError, match="still exists"):
        buckets.merge(g, b, apply=False)


def test_merging_a_bucket_into_itself_is_refused(pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    with pytest.raises(buckets.MigrationError, match="itself"):
        buckets.merge(b, b, apply=True)


@pytest.mark.parametrize("bad", ["../x", "a/b", "", ".birth", "has space"])
def test_a_name_that_is_not_a_bucket_is_refused(pair, bad):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    with pytest.raises(buckets.MigrationError, match="not a bucket name"):
        buckets.merge(bad, b, apply=True)
    with pytest.raises(buckets.MigrationError, match="not a bucket name"):
        buckets.merge(b, bad, apply=True)


def test_a_missing_target_is_refused_and_nothing_moves(pair):
    main, wt, b, g = pair
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    with pytest.raises(buckets.MigrationError, match="no bucket"):
        buckets.merge(g, "-nowhere", apply=True)
    assert _dir(g).is_dir()


# ---------------------------------------------------------------------------
# leftovers: a bucket that cannot be removed keeps its root
# ---------------------------------------------------------------------------


def test_a_bucket_kept_alive_by_a_leftover_keeps_its_root_and_mints_no_alias(
        pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    (_dir(g) / "notes.txt").write_text("a person's file", encoding="utf-8")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    assert record["complete"] is False
    assert record["leftovers"] == ["notes.txt"]
    assert (_dir(g) / "root").read_text(encoding="utf-8").strip() == \
        str(Path(wt).resolve()), "never an unstamped orphan"
    assert g not in buckets.alias_map()


def test_an_unreadable_target_pointer_blocks_promotion_and_keeps_the_source(
        pair):
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    (_dir(b) / "prev-1.json").write_text("{torn", encoding="utf-8")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    assert record["promoted"] is None
    assert record["complete"] is False
    assert record["target_unreadable"] == ["prev-1.json"]
    assert (_dir(g) / "latest.json").is_file(), "nothing was dropped"


# ---------------------------------------------------------------------------
# migrate keeps its own pointer rule
# ---------------------------------------------------------------------------


def test_the_migrate_path_still_never_evicts_and_never_promotes(
        tmp_path, tmp_checkpoint_dir):
    from tests.test_bucket_migration import _populate_legacy, _stage_legacy_pointers  # noqa: F401
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    for marker, created in (("t1", "2026-11-01T00:00:00Z"),
                            ("t2", "2026-11-02T00:00:00Z"),
                            ("t3", "2026-11-03T00:00:00Z")):
        _write(str(real), marker, created)
    legacy = _dir(buckets.legacy_slug(str(link)))
    legacy.mkdir()
    _stage_legacy_pointers(str(link), legacy,
                           [("l1", "2026-11-10T00:00:00Z")])
    record = buckets.migrate(str(link))
    assert _pointers(store.project_bucket(str(real))) == {
        "latest.json": "t3", "prev-1.json": "t2", "prev-2.json": "t1"}
    assert record["stranded_pointers"] == ["l1"]
    assert record["promoted"] is None
    assert os.path.isdir(legacy)
