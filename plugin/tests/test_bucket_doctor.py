"""`bucket doctor` (slice 3b): the read-only survey of what a store's buckets
say about themselves.

It stats the recorded roots, so it is a human surface and never on a hook
path. Names print only to a TTY (the CLI's concern, tested in
test_bucket_cli.py); this module returns the full survey. It never writes, and
it makes no automatic decision: every finding is evidence for a person.
"""

import json
import os
from pathlib import Path

from daimon_briefing import bucket_tools, buckets, config, store
from tests.test_bucket_merge import _dir, _snapshot
from tests.test_bucket_migration import _write
from tests.test_bucket_stamp_tool import _bucket_for, _git, _repo


def _stamped(name: str, root: str, *, ledger: bool = True) -> Path:
    d = _dir(name)
    d.mkdir(parents=True, exist_ok=True)
    (d / "root").write_text(root + "\n", encoding="utf-8")
    if ledger:
        (d / "events.jsonl").write_text(
            json.dumps({"event_id": "e", "ts": "2026-11-01T00:00:00Z"}) + "\n",
            encoding="utf-8")
    return d


def test_doctor_writes_nothing(tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-live", str(live))
    _stamped("-gone", str(tmp_path / "gone"))
    _dir("-bare").mkdir(parents=True)
    before = _snapshot()
    bucket_tools.doctor()
    assert _snapshot() == before


def test_a_healthy_bucket_has_no_findings(tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-live", str(live))
    survey = bucket_tools.doctor()
    assert survey["buckets"] == 1
    assert all(not found for found in survey["findings"].values())


def test_a_dangling_root_is_reported(tmp_path, tmp_checkpoint_dir):
    _stamped("-gone", str(tmp_path / "gone"))
    assert bucket_tools.doctor()["findings"]["dangling"] == ["-gone"]


def test_an_unstamped_bucket_and_an_empty_root_are_reported(
        tmp_path, tmp_checkpoint_dir):
    _dir("-bare").mkdir(parents=True)
    (_dir("-bare") / "events.jsonl").write_text("{}\n", encoding="utf-8")
    d = _dir("-blank")
    d.mkdir()
    (d / "root").write_text("", encoding="utf-8")
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert bucket_tools.doctor()["findings"]["unstamped"] == ["-bare",
                                                              "-blank"]


def test_two_buckets_with_one_root_are_reported_together(
        tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-a", str(live))
    _stamped("-b", str(live))
    _stamped("-c", str(tmp_path / "other"))
    assert bucket_tools.doctor()["findings"]["shared_root"] == [["-a", "-b"]]


def test_an_empty_bucket_holds_nothing_but_markers(tmp_path,
                                                  tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-empty", str(live), ledger=False)
    (_dir("-empty") / ".ledger-census").write_text("{}", encoding="utf-8")
    (_dir("-empty") / ".pointer.lock").write_text("", encoding="utf-8")
    _stamped("-full", str(live) + "x")
    assert bucket_tools.doctor()["findings"]["empty"] == ["-empty"]


def test_a_ghost_is_an_empty_bucket_whose_project_is_gone(
        tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-empty-live", str(live), ledger=False)
    _stamped("-empty-gone", str(tmp_path / "gone"), ledger=False)
    _stamped("-full-gone", str(tmp_path / "gone2"))
    findings = bucket_tools.doctor()["findings"]
    assert findings["ghost"] == ["-empty-gone"]
    assert "-full-gone" in findings["dangling"]


def test_a_worktree_is_recognised_by_its_gitfile(tmp_path, tmp_checkpoint_dir):
    main = _repo(tmp_path / "main")
    wt = tmp_path / "elsewhere" / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "side")
    _stamped("-wt", os.path.realpath(wt))
    _stamped("-main", os.path.realpath(main))
    assert bucket_tools.doctor()["findings"]["worktree"] == ["-wt"]


def test_a_worktree_is_recognised_by_its_claude_worktrees_path_when_gone(
        tmp_path, tmp_checkpoint_dir):
    gone = tmp_path / "repo" / ".claude" / "worktrees" / "agent-1"
    _stamped("-agent", str(gone))
    findings = bucket_tools.doctor()["findings"]
    assert findings["worktree"] == ["-agent"]
    assert findings["dangling"] == ["-agent"]


def test_a_bucket_left_by_an_incomplete_merge_is_a_leftover(
        tmp_path, tmp_checkpoint_dir):
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    main.mkdir()
    wt.mkdir()
    _write(str(main), "b1", "2026-11-01T00:00:00Z")
    _write(str(wt), "g1", "2026-11-10T00:00:00Z")
    g = store.project_bucket(str(wt))
    (_dir(g) / "notes.txt").write_text("x", encoding="utf-8")
    import shutil
    shutil.rmtree(wt)
    buckets.merge(g, store.project_bucket(str(main)), apply=True)
    assert bucket_tools.doctor()["findings"]["leftover"] == [g]


def test_identity_that_disagrees_with_root_is_reported_unless_it_is_history(
        tmp_path, tmp_checkpoint_dir):
    one = tmp_path / "one"
    one.mkdir()
    d = _stamped("-moved", str(one))
    bucket_tools.write_identity(d, "/older/place", {
        "origin": None, "root_commit": None, "folder": "place"})
    assert bucket_tools.doctor()["findings"]["identity_mismatch"] == ["-moved"]
    bucket_tools.write_identity(d, "/older/place", {
        "origin": None, "root_commit": None, "folder": "place"},
        history=[str(one)])
    assert bucket_tools.doctor()["findings"]["identity_mismatch"] == []


def test_a_corrupt_identity_is_reported(tmp_path, tmp_checkpoint_dir):
    one = tmp_path / "one"
    one.mkdir()
    d = _stamped("-bad", str(one))
    (d / store._IDENTITY_NAME).write_text("{torn", encoding="utf-8")
    assert bucket_tools.doctor()["findings"]["identity_unreadable"] == ["-bad"]


def test_a_stray_directory_is_not_surveyed_as_a_bucket(tmp_path,
                                                      tmp_checkpoint_dir):
    (config.checkpoint_dir() / "repo.x").mkdir(parents=True)
    (config.checkpoint_dir() / ".birth").mkdir()
    assert bucket_tools.doctor()["buckets"] == 0


# ---------------------------------------------------------------------------
# --path: evidence, not proof
# ---------------------------------------------------------------------------


def test_path_lists_dangling_buckets_whose_anchors_match(tmp_path,
                                                        tmp_checkpoint_dir):
    here = _repo(tmp_path / "new-machine" / "proj",
                 "https://example.com/o/r.git")
    old = _stamped("-old-machine-proj", "/Users/a/code/proj")
    bucket_tools.write_identity(old, "/Users/a/code/proj", {
        "origin": "https://example.com/o/r.git",
        "root_commit": _git(here, "rev-list", "--max-parents=0", "HEAD"),
        "folder": "proj"})
    other = _stamped("-unrelated", "/Users/a/code/other")
    bucket_tools.write_identity(other, "/Users/a/code/other", {
        "origin": "https://example.com/o/other.git",
        "root_commit": "0" * 40, "folder": "other"})
    survey = bucket_tools.doctor(path=str(here))
    assert [(m["bucket"], m["matched"]) for m in survey["candidates"]] == [
        ("-old-machine-proj", ["origin", "root_commit"])]
    assert survey["candidates_note"] == "evidence, not proof"


def test_path_never_lists_a_bucket_whose_root_still_exists(tmp_path,
                                                          tmp_checkpoint_dir):
    here = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    clone = tmp_path / "other-clone"
    clone.mkdir()
    d = _stamped("-live-clone", str(clone))
    bucket_tools.write_identity(d, str(clone), {
        "origin": "https://example.com/o/r.git", "root_commit": None,
        "folder": "other-clone"})
    assert bucket_tools.doctor(path=str(here))["candidates"] == []


def test_without_a_path_there_are_no_candidates(tmp_path, tmp_checkpoint_dir):
    assert bucket_tools.doctor()["candidates"] == []


# ---------------------------------------------------------------------------
# suggestions: merge for a shared root, withheld for worktree-looking roots
# ---------------------------------------------------------------------------


def test_a_shared_root_suggests_a_merge_into_the_bucket_with_the_newest_session(
        tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    a = store.project_bucket(str(live))
    _write(str(live), "s-old", "2026-11-01T00:00:00Z")
    b = _stamped("-twin", str(live), ledger=False)
    (b / "latest.json").write_text(json.dumps({
        "session_id": "s-new", "created": "2026-11-09T00:00:00Z"}),
        encoding="utf-8")
    survey = bucket_tools.doctor()
    assert survey["suggestions"] == [
        {"from": a, "into": "-twin"}]


def test_no_merge_is_suggested_for_a_worktree_looking_root(
        tmp_path, tmp_checkpoint_dir):
    gone = tmp_path / "repo" / ".claude" / "worktrees" / "agent-1"
    _stamped("-agent-a", str(gone))
    _stamped("-agent-b", str(gone))
    survey = bucket_tools.doctor()
    assert survey["findings"]["shared_root"] == [["-agent-a", "-agent-b"]]
    assert survey["suggestions"] == []
    assert survey["suggestions_withheld"] == "worktree"


def test_bucket_for_helper_names_the_seed_slug(tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "p")
    assert _bucket_for(repo, stamped=True) == store.location_slug(
        os.path.realpath(repo))
