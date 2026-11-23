"""A verb that deletes fails CLOSED (round two of the slice 3b review).

`jsonl.dir_lock` fails OPEN after about a second, right for a hook that must
never block and wrong for `bucket merge`/`migrate`: a destructive run that
carries on without the lock loses data to whoever it was excluding. The two
bucket locks are taken in one global order, a lock that was not acquired is a
refusal raised before anything is written, and the legacy ledger is re-read
immediately before it is removed.
"""

import errno
import json
import os
import threading
import time

import pytest

from daimon_briefing import buckets, config, jsonl, scope, store
from tests.test_bucket_merge import _dir, _snapshot
from tests.test_bucket_migration import _row


def _bucket(slug, events, *, root="/gone/place"):
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text("".join(_row(e) for e in events),
                                    encoding="utf-8")
    if root:
        (d / "root").write_text(root + "\n", encoding="utf-8")
    return d


def _events(slug):
    path = _dir(slug) / "events.jsonl"
    if not path.is_file():
        return set()
    return {json.loads(x)["event_id"]
            for x in path.read_text(encoding="utf-8").splitlines() if x}


@pytest.fixture
def fast_locks(monkeypatch):
    """A lock that is not granted gives up in ~60ms, not a second."""
    monkeypatch.setattr(jsonl, "_LOCK_TRIES", 3)
    monkeypatch.setattr(jsonl, "_LOCK_INTERVAL", 0.02)


# ---------------------------------------------------------------------------
# an ungranted lock is a refusal, before anything is written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("busy", ["-g", "-b"])
def test_a_merge_that_cannot_lock_either_bucket_changes_nothing(
        tmp_checkpoint_dir, fast_locks, busy):
    _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    before = _snapshot()
    with jsonl.dir_lock(_dir(busy)) as held:
        assert held
        with pytest.raises(buckets.MigrationError, match="could not lock"):
            buckets.merge("-g", "-b", apply=True, force=True)
    # the lock sidecar itself is created by the attempt to take the lock
    after = {k: v[0] for k, v in _snapshot().items()
             if not k.endswith(".pointer.lock")}
    assert after == {k: v[0] for k, v in before.items()
                     if not k.endswith(".pointer.lock")}
    assert _events("-g") == {"g1"} and _events("-b") == {"b1"}


def test_a_dry_run_needs_no_lock(tmp_checkpoint_dir, fast_locks):
    _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    with jsonl.dir_lock(_dir("-g")):
        assert buckets.merge("-g", "-b", apply=False, force=True)["mode"] \
            == "merge"


# ---------------------------------------------------------------------------
# opposite merges (probe p2): one wins, one is refused, nothing is lost
# ---------------------------------------------------------------------------


def test_opposite_merges_end_with_one_refused_and_no_event_lost(
        tmp_checkpoint_dir):
    mine = {f"a{i}" for i in range(5)}
    theirs = {f"b{i}" for i in range(5)}
    _bucket("-a", sorted(mine))
    _bucket("-b", sorted(theirs))
    barrier = threading.Barrier(2)
    outcome = {}

    def run(src, dst):
        barrier.wait()
        try:
            buckets.merge(src, dst, apply=True, force=True)
            outcome[src] = "ok"
        except buckets.MigrationError as exc:
            outcome[src] = str(exc)

    threads = [threading.Thread(target=run, args=("-a", "-b")),
               threading.Thread(target=run, args=("-b", "-a"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    done = sorted(k for k, v in outcome.items() if v == "ok")
    assert len(done) == 1, outcome
    survivor = "-b" if done == ["-a"] else "-a"
    assert _events(survivor) == mine | theirs
    assert not _dir(done[0]).exists()


# ---------------------------------------------------------------------------
# a writer that gave up waiting (probe p3) loses nothing
# ---------------------------------------------------------------------------


def test_a_line_appended_to_the_folded_bucket_mid_merge_is_carried(
        tmp_checkpoint_dir, monkeypatch):
    g = _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    real = buckets._append_lines
    state = {"done": False}

    def hook(path, lines, **kw):
        out = real(path, lines, **kw)
        if not state["done"] and path.parent.name == "-b":
            state["done"] = True
            # what a hook does after its bounded wait fails open
            with open(g / "events.jsonl", "a", encoding="utf-8") as fh:
                fh.write(_row("late"))
        return out

    monkeypatch.setattr(buckets, "_append_lines", hook)
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert _events("-b") == {"b1", "g1", "late"}
    assert record["ledgers"]["events.jsonl"] == 2
    assert not g.exists()


def test_a_legacy_ledger_that_stops_being_readable_is_kept(
        tmp_checkpoint_dir, monkeypatch):
    g = _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    real = buckets._read_lines
    calls = []

    def flaky(path):
        if path == g / "events.jsonl":
            calls.append(1)
            if len(calls) == 2:
                return [], False
        return real(path)

    monkeypatch.setattr(buckets, "_read_lines", flaky)
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert record["unreadable"] == ["events.jsonl"]
    assert (g / "events.jsonl").is_file() and record["complete"] is False


# ---------------------------------------------------------------------------
# the lock outlives the removal, so no waiter recreates the bucket under it
# ---------------------------------------------------------------------------


def test_the_folded_bucket_stays_locked_until_it_is_removed(
        tmp_checkpoint_dir, fast_locks, monkeypatch):
    g = _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    real = buckets._retire
    seen = {}

    def spy(legacy_dir):
        with jsonl.dir_lock(legacy_dir) as held:
            seen["held_by_someone_else"] = not held
        out = real(legacy_dir)
        seen["gone"] = not legacy_dir.exists()
        return out

    monkeypatch.setattr(buckets, "_retire", spy)
    buckets.merge("-g", "-b", apply=True, force=True)
    assert seen == {"held_by_someone_else": True, "gone": True}
    assert not g.exists()


# ---------------------------------------------------------------------------
# no hard links: the exclusive create still stamps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("err", [errno.EPERM, errno.ENOTSUP])
def test_stamp_root_works_on_a_filesystem_without_hard_links(
        tmp_checkpoint_dir, monkeypatch, err):
    d = _dir("-b")
    d.mkdir(parents=True)

    def nolink(src, dst, **kw):
        raise OSError(err, "no hard links here")

    monkeypatch.setattr(store.os, "link", nolink)
    assert store.stamp_root(d, "/a/one") is True
    assert (d / "root").read_text(encoding="utf-8") == "/a/one\n"
    assert store.stamp_root(d, "/a/two") is False, "still first writer wins"
    assert (d / "root").read_text(encoding="utf-8") == "/a/one\n"
    assert [p.name for p in d.iterdir() if p.name.endswith(".tmp")] == []


def test_create_exclusive_without_links_never_overwrites(tmp_checkpoint_dir,
                                                         monkeypatch):
    d = _dir("-b")
    d.mkdir(parents=True)
    (d / "identity").write_text("kept", encoding="utf-8")
    monkeypatch.setattr(store.os, "link", lambda *a, **k: (_ for _ in ()).throw(
        OSError(errno.EPERM, "no")))
    assert store.create_exclusive(d / "identity", "new") is False
    assert (d / "identity").read_text(encoding="utf-8") == "kept"


def test_record_bucket_root_stamps_without_hard_links(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store.os, "link", lambda *a, **k: (_ for _ in ()).throw(
        OSError(errno.EPERM, "no")))
    proj = tmp_path / "p"
    proj.mkdir()
    store.ensure_bucket(str(proj))
    assert store.bucket_root(store.project_bucket(str(proj))) == \
        os.path.realpath(proj)


# ---------------------------------------------------------------------------
# a bucket that cannot be removed does not grow the receipts file
# ---------------------------------------------------------------------------


def test_a_persistent_removal_failure_writes_two_rows_in_total(
        tmp_checkpoint_dir, monkeypatch):
    _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    real = type(_dir("-g")).rmdir

    def refuse(self, *a):
        if self.name == "-g":
            raise PermissionError("busy")
        return real(self, *a)

    monkeypatch.setattr(type(_dir("-g")), "rmdir", refuse)
    sizes = []
    for _ in range(4):
        record = buckets.merge("-g", "-b", apply=True, force=True)
        assert record["complete"] is False
        sizes.append(len(buckets.records()))
    assert sizes == [2, 2, 2, 2]
    assert buckets.alias_map() == {}
    monkeypatch.setattr(type(_dir("-g")), "rmdir", real)
    assert buckets.merge("-g", "-b", apply=True, force=True)["complete"] is True
    assert buckets.alias_map() == {"-g": "-b"}
    assert len(buckets.records()) == 3


# ---------------------------------------------------------------------------
# crash leftovers do not keep a bucket alive
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["root.0123456789ab.tmp",
                                  "identity.0123456789ab.tmp",
                                  "root.4242.tmp"])
def test_a_staging_leftover_does_not_block_retiring_a_bucket(
        tmp_checkpoint_dir, name):
    g = _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    (g / name).write_text("x", encoding="utf-8")
    planned = buckets.merge("-g", "-b", apply=False, force=True)
    assert planned["leftovers"] == [] and planned["complete"] is True
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert record["complete"] is True and not g.exists()


def test_other_tmp_files_are_still_leftovers(tmp_checkpoint_dir):
    g = _bucket("-g", ["g1"])
    _bucket("-b", ["b1"])
    (g / "notes.0123456789ab.tmp").write_text("x", encoding="utf-8")
    record = buckets.merge("-g", "-b", apply=True, force=True)
    assert record["leftovers"] == ["notes.0123456789ab.tmp"]
    assert (g / "root").is_file()


def test_the_reaper_removes_an_old_staging_file(tmp_checkpoint_dir):
    d = _bucket("-g", ["g1"])
    old = d / "root.0123456789ab.tmp"
    old.write_text("x", encoding="utf-8")
    past = time.time() - store._TMP_REAP_SECONDS - 600
    os.utime(old, (past, past))
    store._gc_checkpoints(config.checkpoint_dir(), keep=0)
    assert not old.exists() and scope.BIRTH not in os.listdir(d)
