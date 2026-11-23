"""`bucket stamp` and the `identity` record (slice 3b).

Run on the OLD machine before a move so the data carries its own evidence:
stamped buckets gain an identity record, and an unstamped bucket gains a root
only from an exact match, a repository under an explicit `--scan` directory
whose seed slug IS the bucket name. Two matches refuse. A dry run is a plan.
Signed flat files are never touched.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import bucket_tools, config, store
from tests.test_bucket_merge import _dir, _snapshot
from tests.test_bucket_migration import _write


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@x",
         *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(path: Path, origin: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    if origin:
        _git(path, "remote", "add", "origin", origin)
    (path / "f").write_text("x", encoding="utf-8")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "first")
    return path


def _bucket_for(path: Path, *, stamped: bool) -> str:
    """A bucket named by the seed rule for `path`; stamped or left bare."""
    slug = store.location_slug(os.path.realpath(path))
    d = _dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / "events.jsonl").write_text(
        json.dumps({"event_id": "e", "ts": "2026-11-01T00:00:00Z"}) + "\n",
        encoding="utf-8")
    if stamped:
        (d / "root").write_text(os.path.realpath(path) + "\n",
                                encoding="utf-8")
    return slug


# ---------------------------------------------------------------------------
# the identity record
# ---------------------------------------------------------------------------


def test_origin_credentials_are_stripped_before_anything_is_stored():
    assert bucket_tools.public_origin(
        "https://user:tok3n@github.com/o/r.git") == "https://github.com/o/r.git"
    assert bucket_tools.public_origin(
        "ssh://git@github.com/o/r.git") == "ssh://github.com/o/r.git"
    assert bucket_tools.public_origin(
        "git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert bucket_tools.public_origin(None) is None
    assert bucket_tools.public_origin("  ") is None


def test_anchors_of_a_repository_are_origin_root_commit_and_folder(tmp_path):
    repo = _repo(tmp_path / "proj", "https://u:p@example.com/o/r.git")
    anchors = bucket_tools.anchors_for(str(repo))
    assert anchors == {"origin": "https://example.com/o/r.git",
                       "root_commit": _git(repo, "rev-list",
                                           "--max-parents=0", "HEAD"),
                       "folder": "proj"}


def test_anchors_of_a_plain_directory_are_the_folder_alone(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert bucket_tools.anchors_for(str(plain)) == {
        "origin": None, "root_commit": None, "folder": "plain"}


def test_anchors_of_a_missing_directory_are_the_folder_alone(tmp_path):
    assert bucket_tools.anchors_for(str(tmp_path / "gone")) == {
        "origin": None, "root_commit": None, "folder": "gone"}


def test_the_identity_record_round_trips_and_is_written_atomically(
        tmp_checkpoint_dir):
    d = _dir("-b")
    d.mkdir(parents=True)
    bucket_tools.write_identity(d, "/a/b", {"origin": None,
                                            "root_commit": None,
                                            "folder": "b"})
    record = bucket_tools.read_identity(d)
    assert record["version"] == 1
    assert record["root"] == "/a/b"
    assert record["anchors"]["folder"] == "b"
    assert record["history"] == []
    assert sorted(p.name for p in d.iterdir()) == [store._IDENTITY_NAME]


def test_a_corrupt_identity_record_reads_as_none(tmp_checkpoint_dir):
    d = _dir("-b")
    d.mkdir(parents=True)
    (d / store._IDENTITY_NAME).write_text("{torn", encoding="utf-8")
    assert bucket_tools.read_identity(d) is None


def test_a_record_of_another_shape_reads_as_none(tmp_checkpoint_dir):
    d = _dir("-b")
    d.mkdir(parents=True)
    (d / store._IDENTITY_NAME).write_text("[1, 2]", encoding="utf-8")
    assert bucket_tools.read_identity(d) is None
    (d / store._IDENTITY_NAME).write_text('{"version": 99}', encoding="utf-8")
    assert bucket_tools.read_identity(d) is None


# ---------------------------------------------------------------------------
# stamp: stamped buckets gain identity
# ---------------------------------------------------------------------------


def test_a_dry_run_is_a_plan_and_writes_nothing(tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    slug = _bucket_for(repo, stamped=True)
    before = _snapshot()
    result = bucket_tools.stamp(apply=False)
    assert _snapshot() == before
    assert result["applied"] is False
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "identity")]


def test_apply_writes_identity_for_a_stamped_bucket(tmp_path,
                                                   tmp_checkpoint_dir):
    repo = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    slug = _bucket_for(repo, stamped=True)
    bucket_tools.stamp(apply=True)
    record = bucket_tools.read_identity(_dir(slug))
    assert record["root"] == os.path.realpath(repo)
    assert record["anchors"]["origin"] == "https://example.com/o/r.git"


def test_a_second_apply_changes_nothing(tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "proj")
    _bucket_for(repo, stamped=True)
    bucket_tools.stamp(apply=True)
    before = _snapshot()
    again = bucket_tools.stamp(apply=True)
    assert _snapshot() == before
    assert [a["action"] for a in again["actions"]] == ["kept"]


def test_a_dangling_root_still_gets_identity_from_what_is_known(
        tmp_path, tmp_checkpoint_dir):
    d = _dir("-gone-project")
    d.mkdir(parents=True)
    (d / "root").write_text(str(tmp_path / "gone" / "project") + "\n",
                            encoding="utf-8")
    bucket_tools.stamp(apply=True)
    record = bucket_tools.read_identity(d)
    assert record["anchors"] == {"origin": None, "root_commit": None,
                                 "folder": "project"}


def test_stamp_never_rewrites_a_recorded_root(tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=True)
    (_dir(slug) / "root").write_text("/recorded/elsewhere\n", encoding="utf-8")
    bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=True)
    assert (_dir(slug) / "root").read_text(
        encoding="utf-8") == "/recorded/elsewhere\n"


def test_signed_flat_files_and_pointers_are_byte_identical_after_apply(
        tmp_path, tmp_checkpoint_dir):
    proj = tmp_path / "proj"
    proj.mkdir()
    _write(str(proj), "s1", "2026-11-01T00:00:00Z")
    held = {p: p.read_bytes() for p in config.checkpoint_dir().rglob("*")
            if p.is_file() and p.suffix in (".json", ".receipt")}
    assert held
    bucket_tools.stamp(apply=True)
    assert {p: p.read_bytes() for p in held} == held


# ---------------------------------------------------------------------------
# stamp: unstamped buckets need an exact match under --scan
# ---------------------------------------------------------------------------


def test_an_unstamped_bucket_is_stamped_from_an_exact_scan_match(
        tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=False)
    plan = bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=False)
    assert [(a["bucket"], a["action"]) for a in plan["actions"]] == [
        (slug, "root")]
    assert not (_dir(slug) / "root").exists(), "a dry run writes nothing"
    bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=True)
    assert (_dir(slug) / "root").read_text(encoding="utf-8").strip() == \
        os.path.realpath(repo)
    assert bucket_tools.read_identity(_dir(slug))["root"] == \
        os.path.realpath(repo)


def test_an_empty_root_is_stamped_like_an_unstamped_bucket(
        tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=False)
    (_dir(slug) / "root").write_text("", encoding="utf-8")
    bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=True)
    assert (_dir(slug) / "root").read_text(encoding="utf-8").strip() == \
        os.path.realpath(repo)


def test_without_scan_an_unstamped_bucket_is_reported_and_left_alone(
        tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=False)
    before = _snapshot()
    result = bucket_tools.stamp(apply=True)
    assert _snapshot() == before
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


def test_two_matching_repositories_refuse_and_write_nothing(
        tmp_path, tmp_checkpoint_dir):
    """`/scan/my/app` and `/scan/my-app` share a seed slug: lossy twins."""
    one = _repo(tmp_path / "scan" / "my" / "app")
    _repo(tmp_path / "scan" / "my-app")
    slug = _bucket_for(one, stamped=False)
    assert slug == store.location_slug(os.path.realpath(
        tmp_path / "scan" / "my-app"))
    before = _snapshot()
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=True)
    assert _snapshot() == before
    action = result["actions"][0]
    assert action["action"] == "ambiguous"
    assert len(action["candidates"]) == 2


def test_a_scan_does_not_descend_into_a_repository(tmp_path,
                                                  tmp_checkpoint_dir):
    outer = _repo(tmp_path / "scan" / "outer")
    inner = outer / "vendor" / "inner"
    _repo(inner)
    slug = _bucket_for(inner, stamped=False)
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=False)
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


def test_a_symlinked_directory_under_scan_is_not_followed(
        tmp_path, tmp_checkpoint_dir):
    real = _repo(tmp_path / "elsewhere" / "proj")
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "link").symlink_to(real, target_is_directory=True)
    slug = _bucket_for(scan / "link", stamped=False)
    result = bucket_tools.stamp(scan=[str(scan)], apply=False)
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "unmatched")]


def test_the_scan_directory_itself_may_be_the_repository(
        tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "proj")
    slug = _bucket_for(repo, stamped=False)
    result = bucket_tools.stamp(scan=[str(repo)], apply=False)
    assert [(a["bucket"], a["action"]) for a in result["actions"]] == [
        (slug, "root")]


def test_a_scan_directory_that_does_not_exist_is_refused(tmp_path,
                                                        tmp_checkpoint_dir):
    with pytest.raises(bucket_tools.StampRefused, match="not a directory"):
        bucket_tools.stamp(scan=[str(tmp_path / "nope")], apply=False)


def test_a_race_for_the_root_is_reported_not_overwritten(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=False)
    real = store.stamp_root

    def lose(bucket, location):
        (Path(bucket) / "root").write_text("/winner\n", encoding="utf-8")
        return real(bucket, location)

    monkeypatch.setattr(store, "stamp_root", lose)
    result = bucket_tools.stamp(scan=[str(tmp_path / "scan")], apply=True)
    assert result["actions"][0]["action"] == "raced"
    assert (_dir(slug) / "root").read_text(encoding="utf-8") == "/winner\n"
