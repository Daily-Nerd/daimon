"""Corners of the post-review fixes: the never-overwrite identity write, a lost
identity race, the observed line of a merge receipt, and a staging file that
cannot be unlinked."""

import os

from daimon_briefing import bucket_tools, store
from daimon_briefing.cli import projects as cli_projects
from tests.test_bucket_merge import _dir
from tests.test_bucket_repair_edges import _record

ANCHORS = {"origin": None, "root_commit": None, "folder": "p"}


def test_write_identity_without_replace_never_touches_an_existing_record(
        tmp_checkpoint_dir):
    d = _dir("-b")
    d.mkdir(parents=True)
    (d / store._IDENTITY_NAME).write_text("{torn", encoding="utf-8")
    assert bucket_tools.write_identity(d, "/a", ANCHORS, replace=False) is False
    assert (d / store._IDENTITY_NAME).read_text(encoding="utf-8") == "{torn"
    assert [p.name for p in d.iterdir()] == [store._IDENTITY_NAME]


def test_write_identity_without_replace_creates_when_absent(tmp_checkpoint_dir):
    d = _dir("-b")
    d.mkdir(parents=True)
    assert bucket_tools.write_identity(d, "/a", ANCHORS, replace=False) is True
    assert bucket_tools.read_identity(d)["root"] == "/a"


def test_a_lost_identity_race_is_reported_not_overwritten(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    d = _dir("-b")
    d.mkdir(parents=True)
    (d / "root").write_text(str(tmp_path) + "\n", encoding="utf-8")
    monkeypatch.setattr(bucket_tools, "write_identity",
                        lambda *a, **k: False)
    result = bucket_tools.stamp(apply=True)
    assert [a["action"] for a in result["actions"]] == ["raced"]


def test_the_merge_render_says_when_the_bucket_could_not_be_removed():
    lines = cli_projects._bucket_merge_lines(
        _record(observed="the bucket directory could not be removed"),
        dry_run=False)
    assert any("could not be removed" in line and "run again" in line
               for line in lines)


def test_stamp_root_survives_a_staging_file_it_cannot_unlink(
        tmp_checkpoint_dir, monkeypatch):
    d = _dir("-b")
    d.mkdir(parents=True)
    real = os.unlink

    def refuse(path, *a, **k):
        if ".tmp" in str(path):
            raise PermissionError(path)
        return real(path, *a, **k)

    monkeypatch.setattr(store.os, "unlink", refuse)
    assert store.stamp_root(d, "/a/one") is True
    assert (d / "root").read_text(encoding="utf-8").strip() == "/a/one"
