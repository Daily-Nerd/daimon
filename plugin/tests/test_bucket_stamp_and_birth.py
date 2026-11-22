"""Slice 3b registries: the root stamp, the identity record, and `.birth`.

Three file shapes join the checkpoint dir in this slice. Each one is declared
where the walkers look for it, so none is mistaken for a checkpoint, a stray
directory or a bucket:

* `root`: an EMPTY file counts as unstamped (a reader that catches the
  exclusive create between open and write, or a crash between them, must not
  leave the bucket unstamped forever).
* `identity`: evidence anchors, named WITHOUT a `.json` suffix because six
  hand-maintained walkers read every `*.json` in a bucket as a plaintext
  checkpoint.
* `.birth/`: the staging directory a future birth claim renames from. Typed
  INTERNAL in `scope`, reaped by age, never a bucket, never a stray.
"""

import json
import os
import time
from pathlib import Path

from daimon_briefing import config, privacy, scope, store, surfaces
from daimon_briefing.cli import stats as cli_stats


def _bucket(tmp_checkpoint_dir, slug="-b") -> Path:
    d = tmp_checkpoint_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# root: exclusive create, empty means unstamped
# ---------------------------------------------------------------------------


def test_an_empty_root_is_unstamped_and_the_next_write_stamps_it(
        tmp_path, tmp_checkpoint_dir):
    proj = tmp_path / "p"
    proj.mkdir()
    slug = store.project_slug(str(proj.resolve()))
    root = _bucket(tmp_checkpoint_dir, slug) / "root"
    root.write_text("", encoding="utf-8")
    assert store.bucket_root(slug) is None
    store.ensure_bucket(str(proj))
    assert root.read_text(encoding="utf-8").strip() == str(proj.resolve())


def test_a_stamped_root_is_never_overwritten_by_a_second_location(
        tmp_path, tmp_checkpoint_dir):
    proj = tmp_path / "p"
    proj.mkdir()
    slug = store.project_slug(str(proj.resolve()))
    root = _bucket(tmp_checkpoint_dir, slug) / "root"
    root.write_text("/somewhere/else\n", encoding="utf-8")
    store.ensure_bucket(str(proj))
    assert root.read_text(encoding="utf-8") == "/somewhere/else\n"


def test_stamp_root_creates_exclusively_and_reports_who_won(
        tmp_checkpoint_dir):
    d = _bucket(tmp_checkpoint_dir)
    assert store.stamp_root(d, "/a/one") is True
    assert store.stamp_root(d, "/a/two") is False
    assert (d / "root").read_text(encoding="utf-8").strip() == "/a/one"


def test_stamp_root_leaves_no_staging_file_behind(tmp_checkpoint_dir):
    d = _bucket(tmp_checkpoint_dir)
    (d / "root").write_text("", encoding="utf-8")
    assert store.stamp_root(d, "/a/one") is True
    assert sorted(p.name for p in d.iterdir()) == ["root"]


# ---------------------------------------------------------------------------
# .birth: typed internal, never a bucket, never a stray, reaped by age
# ---------------------------------------------------------------------------


def test_birth_is_a_registered_internal_directory():
    assert scope.BIRTH == ".birth"
    assert scope.BIRTH in scope.CURRENT_INTERNAL_DIRS
    assert scope.parse_bucket(scope.BIRTH) is None
    assert scope.classify_dir(scope.BIRTH) is scope.DirKind.INTERNAL


def _plant_birth(tmp_checkpoint_dir, name, age_seconds) -> Path:
    stage = tmp_checkpoint_dir / scope.BIRTH / name
    stage.mkdir(parents=True)
    (stage / "root").write_text("/x\n", encoding="utf-8")
    old = time.time() - age_seconds
    os.utime(stage / "root", (old, old))
    os.utime(stage, (old, old))
    return stage


def test_birth_is_not_enumerated_as_a_bucket_or_a_stray(tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-real")
    _plant_birth(tmp_checkpoint_dir, "1-abc", 0)
    found = store.bucket_dirs()
    assert [p.name for p in found.buckets] == ["-real"]
    assert found.strays == ()


def test_the_privacy_walkers_skip_birth(tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-real")
    _plant_birth(tmp_checkpoint_dir, "1-abc", 0)
    audited = [r.get("project") or r.get("slug") for r in privacy.audit_all()]
    assert scope.BIRTH not in audited
    _known, unknown = privacy._checkpoint_candidates()
    assert all(scope.BIRTH not in str(p) for p, _ in unknown)


def test_stats_does_not_count_birth_as_a_project_bucket(tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-real")
    _plant_birth(tmp_checkpoint_dir, "1-abc", 0)
    assert cli_stats._stats_store()["project_buckets"] == 1


def test_forget_all_projects_does_not_walk_birth(tmp_checkpoint_dir,
                                                 monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    _bucket(tmp_checkpoint_dir, "-real")
    _plant_birth(tmp_checkpoint_dir, "1-abc", 0)
    monkeypatch.setattr(store, "foreign_forgotten_content_keys",
                        lambda: {"k"})
    walked: list = []

    def spy(key, project_dir=None):
        walked.append(project_dir)
        return []

    monkeypatch.setattr(store, "scrub_content_key", spy)
    store.apply_foreign_tombstones(all_projects=True)
    assert walked == ["-real"]


def test_gc_reaps_a_stale_birth_directory_and_keeps_a_fresh_one(
        tmp_checkpoint_dir):
    stale = _plant_birth(tmp_checkpoint_dir, "1-old",
                         store._TMP_REAP_SECONDS + 600)
    fresh = _plant_birth(tmp_checkpoint_dir, "2-new", 5)
    store._gc_checkpoints(tmp_checkpoint_dir, keep=0)
    assert not stale.exists()
    assert fresh.is_dir() and (fresh / "root").is_file()
    assert (tmp_checkpoint_dir / scope.BIRTH).is_dir(), \
        "only the stage directories go, never the registered parent"


def test_gc_never_reaps_a_file_named_like_a_stage(tmp_checkpoint_dir):
    parent = tmp_checkpoint_dir / scope.BIRTH
    parent.mkdir(parents=True)
    keep = parent / "stray-file"
    keep.write_text("x", encoding="utf-8")
    old = time.time() - store._TMP_REAP_SECONDS - 600
    os.utime(keep, (old, old))
    store._gc_checkpoints(tmp_checkpoint_dir, keep=0)
    assert keep.exists(), "the reaper removes stage DIRECTORIES only"


# ---------------------------------------------------------------------------
# identity: declared, not a checkpoint, removable from a merged-away bucket
# ---------------------------------------------------------------------------


def test_the_identity_record_has_no_json_suffix():
    assert not store._IDENTITY_NAME.endswith(".json"), (
        "every *.json in a bucket is read as a plaintext checkpoint by "
        "_plaintext_surfaces, recall and the privacy audit")


def test_identity_is_a_declared_audit_exempt_surface():
    assert store._IDENTITY_NAME in surfaces.exempt_names()
    assert store._BUCKET_ROOT_NAME in surfaces.exempt_names()


def test_a_bucket_holding_identity_is_not_an_unknown_file_to_the_audit(
        tmp_checkpoint_dir):
    d = _bucket(tmp_checkpoint_dir)
    (d / store._IDENTITY_NAME).write_text(json.dumps({"version": 1}),
                                          encoding="utf-8")
    _known, unknown = privacy._checkpoint_candidates()
    assert [p for p, _ in unknown if p.name == store._IDENTITY_NAME] == []


def test_identity_is_not_a_plaintext_surface(tmp_checkpoint_dir):
    d = _bucket(tmp_checkpoint_dir)
    (d / store._IDENTITY_NAME).write_text("{}", encoding="utf-8")
    assert store._plaintext_surfaces(config.checkpoint_dir()) == []
