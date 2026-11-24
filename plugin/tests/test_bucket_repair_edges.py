"""Edges of the slice 3b repair tools: failures that must degrade the way the
design says, never raise out of a best-effort path and never lose a record."""

import json
import os
import shutil
import sys
import time

import pytest

from daimon_briefing import bucket_tools, buckets, cli, hostgit, scope, store
from daimon_briefing.cli import projects as cli_projects
from tests.test_bucket_merge import _dir, _fill, _pointers, pair  # noqa: F401
from tests.test_bucket_migration import _write
from tests.test_bucket_stamp_tool import _bucket_for, _repo


# ---------------------------------------------------------------------------
# store: stamp_root and the birth reaper never raise
# ---------------------------------------------------------------------------


def test_stamp_root_reports_false_when_the_existing_root_cannot_be_read(
        tmp_checkpoint_dir):
    d = _dir("-b")
    (d / "root").mkdir(parents=True)  # a directory where the file should be
    assert store.stamp_root(d, "/a/one") is False


def test_the_birth_reaper_survives_a_stage_it_cannot_remove(
        tmp_checkpoint_dir, monkeypatch):
    stage = tmp_checkpoint_dir / scope.BIRTH / "1-old"
    stage.mkdir(parents=True)
    old = time.time() - store._TMP_REAP_SECONDS - 600
    os.utime(stage, (old, old))

    def refuse(path, *a, **k):
        raise PermissionError(path)

    monkeypatch.setattr(store.shutil, "rmtree", refuse)
    store._gc_checkpoints(tmp_checkpoint_dir, keep=0)
    assert stage.is_dir()


def test_the_birth_reaper_is_silent_when_there_is_no_birth_directory(
        tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True)
    store._reap_stale_births(tmp_checkpoint_dir)


# ---------------------------------------------------------------------------
# hostgit: the two readers the survey uses
# ---------------------------------------------------------------------------


def test_root_commit_is_none_under_the_sealed_posture(tmp_path):
    repo = _repo(tmp_path / "r")
    assert hostgit.root_commit(str(repo))
    hostgit.configure(posture="sealed", ceiling=str(tmp_path))
    try:
        assert hostgit.root_commit(str(repo)) is None
    finally:
        hostgit._reset()


def test_root_commit_is_none_for_a_directory_with_no_commits(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert hostgit.root_commit(str(plain)) is None


def test_has_gitfile_distinguishes_a_pointer_file_from_a_directory(tmp_path):
    repo = _repo(tmp_path / "r")
    assert hostgit.has_gitfile(str(repo)) is False
    pointer = tmp_path / "wt"
    pointer.mkdir()
    (pointer / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert hostgit.has_gitfile(str(pointer)) is True
    assert hostgit.has_gitfile(str(tmp_path / "missing")) is False


# ---------------------------------------------------------------------------
# stamp: the scan is bounded and skips what is never a repository
# ---------------------------------------------------------------------------


def test_a_repository_deeper_than_the_scan_depth_is_not_found(
        tmp_path, tmp_checkpoint_dir):
    deep = tmp_path / "scan"
    for i in range(bucket_tools.SCAN_DEPTH + 1):
        deep = deep / f"d{i}"
    repo = _repo(deep)
    slug = _bucket_for(repo, stamped=False)
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")])
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


def test_a_repository_at_the_scan_depth_is_found(tmp_path,
                                                tmp_checkpoint_dir):
    shallow = tmp_path / "scan"
    for i in range(bucket_tools.SCAN_DEPTH):
        shallow = shallow / f"d{i}"
    repo = _repo(shallow)
    slug = _bucket_for(repo, stamped=False)
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")])
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "root")]


@pytest.mark.parametrize("name", ["node_modules", ".hidden"])
def test_a_scan_skips_directories_that_are_never_repositories(
        tmp_path, tmp_checkpoint_dir, name):
    repo = _repo(tmp_path / "scan" / name / "proj")
    slug = _bucket_for(repo, stamped=False)
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")])
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


def test_an_unreadable_scan_directory_finds_nothing_and_does_not_raise(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    scan = tmp_path / "scan"
    scan.mkdir()
    slug = _bucket_for(tmp_path / "p", stamped=False)

    real = os.scandir

    def refuse(path):
        if str(path) == str(scan):
            raise PermissionError(path)
        return real(path)

    monkeypatch.setattr(bucket_tools.os, "scandir", refuse)
    result = bucket_tools.stamp(scan=[str(scan)])
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


# ---------------------------------------------------------------------------
# merge: the corners of the pointer rule
# ---------------------------------------------------------------------------


def test_an_unreadable_newest_pointer_is_never_promoted(pair):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    (_dir(g) / "latest.json").write_text("{torn", encoding="utf-8")
    shutil.rmtree(wt)
    record = buckets.merge(g, b, apply=True)
    assert record["promoted"] is None
    assert _pointers(b) == {"latest.json": "b1"}
    assert record["complete"] is False
    assert (_dir(g) / "latest.json").is_file(), "unreadable stays and is named"
    assert record["stranded_pointers"] == ["latest.json"]


def test_a_refused_advance_promotes_nothing_and_loses_nothing(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    monkeypatch.setattr(store, "_advance_pointers_held",
                        lambda *a, **k: False)
    record = buckets.merge(g, b, apply=True)
    assert record["promoted"] is None
    assert sorted(_pointers(b).values()) == ["b1", "g1"], \
        "the slot logic still keeps it"


def test_a_root_put_back_when_the_directory_cannot_be_removed(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    held = (_dir(g) / "root").read_text(encoding="utf-8")
    shutil.rmtree(wt)

    real = type(_dir(g)).rmdir

    def refuse(self, *a):
        # removed from its tombstone, named after the bucket
        if self.name == g or self.name.startswith(
                store.RETIRED_PREFIX + g):
            raise OSError("busy")
        return real(self, *a)

    monkeypatch.setattr(type(_dir(g)), "rmdir", refuse)
    record = buckets.merge(g, b, apply=True)
    assert record["complete"] is False, "it is not gone, so it is not complete"
    assert (_dir(g) / "root").read_text(encoding="utf-8") == held, \
        "a bucket that survives is never left unstamped"


def test_a_bucket_that_cannot_be_moved_aside_stays_whole_at_its_path(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    held = (_dir(g) / "root").read_text(encoding="utf-8")
    shutil.rmtree(wt)

    def refuse(bucket):
        raise OSError("read-only parent")

    monkeypatch.setattr(store, "move_aside", refuse)
    record = buckets.merge(g, b, apply=True)
    assert record["complete"] is False, "it is not gone, so it is not complete"
    assert (_dir(g) / "root").read_text(encoding="utf-8") == held, \
        "a bucket that was never moved keeps its root"
    assert not (_dir(g).parent / scope.BIRTH).exists() or not any(
        p.name.startswith(store.RETIRED_PREFIX)
        for p in (_dir(g).parent / scope.BIRTH).iterdir()), \
        "nothing was moved, so no tombstone exists"


def test_a_merge_finds_leftovers_a_writer_added_during_the_run(
        pair, monkeypatch):  # noqa: F811
    main, wt, b, g = pair
    _write(main, "b1", "2026-11-01T00:00:00Z")
    _write(wt, "g1", "2026-11-10T00:00:00Z")
    shutil.rmtree(wt)
    real = buckets._retire

    def racing(legacy_dir):
        (legacy_dir / "late.txt").write_text("x", encoding="utf-8")
        return real(legacy_dir)

    monkeypatch.setattr(buckets, "_retire", racing)
    record = buckets.merge(g, b, apply=True)
    assert record["leftovers"] == ["late.txt"]
    assert record["complete"] is False
    assert (_dir(g) / "root").is_file()


# ---------------------------------------------------------------------------
# the CLI: renderers, the terminal check, the json shapes
# ---------------------------------------------------------------------------


def test_the_terminal_check_reads_stdout(monkeypatch):
    class Fake:
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdout", Fake())
    assert cli_projects._stdout_is_tty() is True


def _record(**over):
    base = {"mode": "merge", "from_slug": "-g", "to_slug": "-b",
            "ledgers": {"requests.jsonl": 2}, "leftovers": [],
            "unreadable": [], "target_unreadable": [], "promoted": None,
            "pointers_evicted": [], "pointers_dropped": []}
    base.update(over)
    return base


def test_the_merge_render_for_an_absent_source():
    lines = cli_projects._bucket_merge_lines(_record(mode="absent"),
                                             dry_run=False)
    assert lines == ["nothing to merge: -g is not there (already folded, or "
                     "never existed)"]


def test_the_merge_render_names_every_condition():
    lines = cli_projects._bucket_merge_lines(_record(
        target_unreadable=["prev-1.json"], unreadable=["events.jsonl"],
        leftovers=["events.jsonl", "latest.json", "notes.txt"]),
        dry_run=False)
    text = "\n".join(lines)
    assert "requests.jsonl: appended 2 line(s)" in text
    assert "prev-1.json in -b could not be read" in text
    assert "events.jsonl could not be read, so it was not moved" in text
    assert "notes.txt could not be read as anything this verb moves" in text
    assert text.count("events.jsonl") == 1, "named once, with its remedy"
    assert "latest.json" not in text, "pointers are reported by session"


def test_the_merge_render_of_a_plan_says_so():
    lines = cli_projects._bucket_merge_lines(_record(), dry_run=True)
    assert lines[0] == "would fold -g into -b"
    assert "requests.jsonl: would append 2 line(s)" in "\n".join(lines)
    assert lines[-1].startswith("plan only")


def test_stamp_json_without_a_terminal_carries_counts_only(
        tmp_path, tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)
    repo = _repo(tmp_path / "proj")
    slug = _bucket_for(repo, stamped=True)
    assert cli.main(["bucket", "stamp", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"applied": False, "counts": {"identity": 1}}
    assert slug not in json.dumps(out)


def test_stamp_json_on_a_terminal_lists_the_actions_and_the_exit_code_follows(
        tmp_path, tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    one = _repo(tmp_path / "scan" / "my" / "app")
    _repo(tmp_path / "scan" / "my-app")
    _bucket_for(one, stamped=False)
    rc = cli.main(["bucket", "stamp", "--scan", str(tmp_path / "scan"),
                   "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["actions"][0]["action"] == "ambiguous"


def test_stamp_on_an_empty_store_says_so(tmp_checkpoint_dir, capsys,
                                         monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    tmp_checkpoint_dir.mkdir(parents=True)
    assert cli.main(["bucket", "stamp"]) == 0
    assert "no buckets" in capsys.readouterr().out


def test_stamp_listing_skips_the_quiet_actions_and_names_the_rest(
        tmp_path, tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    repo = _repo(tmp_path / "proj")
    kept = _bucket_for(repo, stamped=True)
    bucket_tools.write_identity(_dir(kept), os.path.realpath(repo),
                                bucket_tools.anchors_for(str(repo)))
    bare = _dir("-unmatched-bare")
    bare.mkdir()
    (bare / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert cli.main(["bucket", "stamp"]) == 0
    out = capsys.readouterr().out
    assert "kept: 1" in out and "unmatched: 1" in out
    assert "  kept" not in out and "  unmatched" not in out


# ---------------------------------------------------------------------------
# doctor: a bucket that cannot be listed is not called empty
# ---------------------------------------------------------------------------


def test_a_bucket_that_cannot_be_listed_is_not_empty(tmp_path):
    assert bucket_tools._is_empty(tmp_path / "missing") is False


def test_path_skips_a_dangling_bucket_that_has_no_identity_record(
        tmp_path, tmp_checkpoint_dir):
    here = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    d = _dir("-no-identity")
    d.mkdir(parents=True)
    (d / "root").write_text("/Users/a/proj\n", encoding="utf-8")
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert bucket_tools.doctor(path=str(here))["candidates"] == []
