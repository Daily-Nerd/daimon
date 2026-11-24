"""Hardening of the slice 3b repair tools after the blind review.

Each test reproduces one reviewed case. The invariants:

* "same bucket" is decided by the filesystem, never by a name (B1);
* the durable record exists before the irreversible removal, and a bucket is
  complete only when it is really gone (M1, m1);
* nothing writes to the folded bucket while it is being folded, and the
  target's chain is planned and changed in ONE critical section (M2, M3);
* an identity record that could not be read is never overwritten (M4).
"""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from daimon_briefing import bucket_tools, buckets, jsonl, scope, store
from tests.test_bucket_merge import _dir, _snapshot, pair  # noqa: F401
from tests.test_bucket_migration import _row, _write
from tests.test_bucket_stamp_tool import _repo


def _plant(slug: str, root: str | None = "/gone/place") -> Path:
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text(_row("keep-me"), encoding="utf-8")
    (d / "latest.json").write_text(json.dumps({
        "session_id": "S-" + slug, "created": "2026-11-01T00:00:00Z",
        "project_slug": slug}), encoding="utf-8")
    if root is not None:
        (d / "root").write_text(root + "\n", encoding="utf-8")
    return d


def _case_insensitive(tmp_path) -> bool:
    probe = tmp_path / "CaseProbe"
    probe.mkdir()
    return (tmp_path / "caseprobe").exists()


# ---------------------------------------------------------------------------
# B1: one directory under two names is not a merge
# ---------------------------------------------------------------------------


def test_a_case_variant_name_of_the_same_directory_is_refused(
        tmp_path, tmp_checkpoint_dir):
    if not _case_insensitive(tmp_path):
        pytest.skip("case-sensitive filesystem: the two names are two dirs")
    d = _plant("-Users-Me-Repo")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="same directory"):
        buckets.merge("-Users-Me-Repo", "-users-me-repo", apply=True,
                      force=True)
    assert _snapshot() == before
    assert (d / "events.jsonl").is_file() and (d / "latest.json").is_file()


def test_the_samestat_check_refuses_any_aliased_pair(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    a = _plant("-a")
    _plant("-b")
    monkeypatch.setattr(buckets.os.path, "samestat", lambda x, y: True)
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="same directory"):
        buckets.merge("-a", "-b", apply=True, force=True)
    assert _snapshot() == before and (a / "events.jsonl").is_file()


def test_migrate_goes_through_the_same_refusal(tmp_checkpoint_dir):
    d = _plant("-same")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="same directory"):
        buckets.merge_buckets("-same", "-same", dry_run=False)
    assert _snapshot() == before and (d / "events.jsonl").is_file()


@pytest.mark.parametrize("which", ["source", "target"])
def test_a_symlinked_bucket_directory_is_refused(tmp_path, tmp_checkpoint_dir,
                                                which):
    real = _plant("-real")
    _plant("-other")
    alias = _dir("-alias")
    alias.symlink_to(real, target_is_directory=True)
    args = ("-alias", "-other") if which == "source" else ("-other", "-alias")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="symlink"):
        buckets.merge(*args, apply=True, force=True)
    assert _snapshot() == before and (real / "events.jsonl").is_file()


def test_a_symlinked_checkpoint_directory_is_still_fine(tmp_path, monkeypatch):
    """~/.daimon is often a symlink; only a symlinked BUCKET is refused."""
    real = tmp_path / "real-checkpoints"
    real.mkdir()
    link = tmp_path / "link-checkpoints"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(link))
    _plant("-g")
    _plant("-b")
    assert buckets.merge("-g", "-b", apply=True, force=True)["complete"] is True


# ---------------------------------------------------------------------------
# M1, m1: the record before the removal, complete only when really gone
# ---------------------------------------------------------------------------


def test_the_receipt_exists_before_the_bucket_is_removed(pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    seen = {}
    real = buckets._retire

    def spy(legacy_dir):
        seen["rows"] = buckets.alias_map()
        return real(legacy_dir)

    monkeypatch.setattr(buckets, "_retire", spy)
    buckets.merge(g, b, apply=True)
    assert seen["rows"] == {g: b}


def test_a_failed_receipt_leaves_the_bucket_so_a_rerun_mints_the_alias(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    real = buckets._append_record

    def fail(record):
        raise buckets.MigrationError("disk full")

    monkeypatch.setattr(buckets, "_append_record", fail)
    with pytest.raises(buckets.MigrationError, match="disk full"):
        buckets.merge(g, b, apply=True)
    assert _dir(g).is_dir(), "no receipt, so nothing irreversible happened"
    assert (_dir(g) / "root").is_file()
    monkeypatch.setattr(buckets, "_append_record", real)
    record = buckets.merge(g, b, apply=True)
    assert record["complete"] is True
    assert buckets.alias_map() == {g: b}
    assert not _dir(g).exists()


def test_a_bucket_that_could_not_be_removed_is_not_complete_and_no_alias(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    held = (_dir(g) / "root").read_text(encoding="utf-8")
    real = type(_dir(g)).rmdir

    def refuse(self, *a):
        # removed from its tombstone, named after the bucket
        if self.name == g or self.name.startswith(
                store.RETIRED_PREFIX + g):
            raise PermissionError("busy")
        return real(self, *a)

    monkeypatch.setattr(type(_dir(g)), "rmdir", refuse)
    record = buckets.merge(g, b, apply=True)
    assert record["complete"] is False
    assert buckets.alias_map() == {}
    assert (_dir(g) / "root").read_text(encoding="utf-8") == held
    monkeypatch.setattr(type(_dir(g)), "rmdir", real)
    assert buckets.merge(g, b, apply=True)["complete"] is True
    assert buckets.alias_map() == {g: b}


# ---------------------------------------------------------------------------
# M2: no writer reaches the folded bucket mid-merge; unstamped needs --force
# ---------------------------------------------------------------------------


def test_an_unstamped_bucket_needs_force_because_nothing_proves_it_is_dead(
        tmp_checkpoint_dir):
    _plant("-g", root=None)
    _plant("-b")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="no recorded root"):
        buckets.merge("-g", "-b", apply=True)
    assert _snapshot() == before
    assert buckets.merge("-g", "-b", apply=True, force=True)["complete"] is True


def test_a_writer_to_the_folded_bucket_is_held_off_during_the_merge(
        tmp_checkpoint_dir, monkeypatch):
    g = _plant("-g")
    _plant("-b")
    done = threading.Event()
    state = {}

    def writer():
        with jsonl.dir_lock(g):  # what every ledger append takes
            pass
        done.set()

    real = buckets._append_lines

    def hook(path, lines, **kw):
        if "thread" not in state:  # the first append only
            t = threading.Thread(target=writer)
            t.start()
            state["blocked"] = not done.wait(0.3)
            state["thread"] = t
        return real(path, lines, **kw)

    monkeypatch.setattr(buckets, "_append_lines", hook)
    (g / "requests.jsonl").write_text(_row("x"), encoding="utf-8")
    buckets.merge("-g", "-b", apply=True, force=True)
    state["thread"].join(5)
    assert state["blocked"] is True


# ---------------------------------------------------------------------------
# M3: plan and apply of the target chain are one critical section
# ---------------------------------------------------------------------------


def test_a_live_writer_cannot_rotate_the_target_between_plan_and_apply(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    done = threading.Event()
    state = {"fired": False}
    real = store._file_recency

    def rotate_b():
        store._advance_pointers(
            _dir(b), json.dumps({"session_id": "S-live", "created":
                                 "2026-11-20T00:00:00Z"}),
            store._created_epoch("2026-11-20T00:00:00Z"), True, 3)
        done.set()

    def spy(path):
        if (not state["fired"]
                and sys._getframe(1).f_code.co_name == "_merge_pointers"):
            state["fired"] = True
            threading.Thread(target=rotate_b).start()
            state["blocked"] = not done.wait(0.3)
        return real(path)

    monkeypatch.setattr(store, "_file_recency", spy)
    buckets.merge(g, b, apply=True)
    assert state["fired"] and state["blocked"] is True
    done.wait(5)


# ---------------------------------------------------------------------------
# M4: stamp never overwrites an identity record it could not read
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["{torn", "[1]", '{"version": 99, "history": ["/x"]}'])
def test_stamp_leaves_an_unreadable_identity_record_alone(
        tmp_path, tmp_checkpoint_dir, body):
    d = _plant("-b", root=str(tmp_path))
    (d / store._IDENTITY_NAME).write_text(body, encoding="utf-8")
    before = (d / store._IDENTITY_NAME).read_bytes()
    result = bucket_tools.stamp(apply=True)
    assert [a["action"] for a in result["actions"]] == ["unreadable"]
    assert (d / store._IDENTITY_NAME).read_bytes() == before
    assert bucket_tools.doctor()["findings"]["identity_unreadable"] == ["-b"]


def test_stamp_exits_1_for_an_unreadable_record(tmp_path, tmp_checkpoint_dir):
    from daimon_briefing import cli
    d = _plant("-b", root=str(tmp_path))
    (d / store._IDENTITY_NAME).write_text("{torn", encoding="utf-8")
    assert cli.main(["bucket", "stamp"]) == 1


# ---------------------------------------------------------------------------
# m2: an empty root is claimed by exactly one writer
# ---------------------------------------------------------------------------


def test_only_one_of_many_racing_writers_wins_an_empty_root(
        tmp_checkpoint_dir):
    for trial in range(15):
        d = _dir(f"-race-{trial}")
        d.mkdir(parents=True)
        (d / "root").write_text("", encoding="utf-8")
        barrier = threading.Barrier(8)
        wins = []

        def go(i):
            barrier.wait()
            if store.stamp_root(d, f"/loc/{i}"):
                wins.append(i)

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len(wins) == 1, (trial, wins)
        assert (d / "root").read_text(encoding="utf-8").strip() == \
            f"/loc/{wins[0]}"


# ---------------------------------------------------------------------------
# m3, m6
# ---------------------------------------------------------------------------


def test_a_file_named_head_does_not_blind_the_root_commit(tmp_path):
    from daimon_briefing import hostgit
    repo = _repo(tmp_path / "r")
    (repo / "HEAD").write_text("not a ref", encoding="utf-8")
    assert hostgit.root_commit(str(repo))


def test_a_stage_with_fresh_content_is_not_reaped(tmp_checkpoint_dir):
    stage = tmp_checkpoint_dir / scope.BIRTH / "1-busy"
    stage.mkdir(parents=True)
    (stage / "root").write_text("/x\n", encoding="utf-8")
    old = time.time() - store._TMP_REAP_SECONDS - 600
    os.utime(stage, (old, old))
    store._gc_checkpoints(tmp_checkpoint_dir, keep=0)
    assert stage.is_dir(), "a file written inside the stage a moment ago"
    os.utime(stage / "root", (old, old))
    store._gc_checkpoints(tmp_checkpoint_dir, keep=0)
    assert not stage.exists()
