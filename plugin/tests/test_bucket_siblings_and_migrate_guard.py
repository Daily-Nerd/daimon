"""Slice 4b: `sibling_buckets` decides children by recorded root, and `migrate`
asks the resolver instead of the two slug rules.

Before the root was authoritative, every `<S>-*` bucket looked like a phantom
child of `<S>`. A twin born under `<S>-6hex` and a bucket whose permanent name
has nothing to do with its location both broke that rule.
"""

import hashlib
import os

import pytest

from daimon_briefing import buckets, identity, store

from tests.test_bucket_merge import _dir, _snapshot
from tests.test_bucket_migration import _row


def _bucket(root_dir, name, root=None, *, latest=True):
    d = root_dir / name
    d.mkdir(parents=True, exist_ok=True)
    if root is not None:
        (d / "root").write_text(f"{root}\n", encoding="utf-8")
    if latest:
        (d / "latest.json").write_text('{"session_id": "S-1"}',
                                       encoding="utf-8")
    return d


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "repo"
    (p / "plugin").mkdir(parents=True)
    return str(p.resolve())


# ---- sibling_buckets ---------------------------------------------------------------


def test_a_suffixed_twin_is_not_a_phantom_child(proj, tmp_checkpoint_dir):
    seed = store.location_slug(proj)
    _bucket(tmp_checkpoint_dir, seed, proj)
    _bucket(tmp_checkpoint_dir, f"{seed}-abc123", "/somewhere/else/entirely")
    assert store.sibling_buckets(proj) == []


def test_a_bucket_whose_root_lies_below_is_a_child_whatever_its_name(
        proj, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, store.location_slug(proj), proj)
    _bucket(tmp_checkpoint_dir, "-a-permanent-name", proj + "/plugin")
    got = store.sibling_buckets(proj)
    assert [s["slug"] for s in got] == ["-a-permanent-name"]


def test_a_worktree_bucket_below_the_project_still_shows(
        proj, tmp_checkpoint_dir):
    seed = store.location_slug(proj)
    _bucket(tmp_checkpoint_dir, seed, proj)
    wt = proj + "/.claude/worktrees/agent-1"
    _bucket(tmp_checkpoint_dir, store.location_slug(wt), wt)
    assert [s["slug"] for s in store.sibling_buckets(proj)] == [
        store.location_slug(wt)]


def test_children_are_separator_bounded(proj, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, store.location_slug(proj), proj)
    _bucket(tmp_checkpoint_dir, "-lookalike", proj + "-two/plugin")
    assert store.sibling_buckets(proj) == []


def test_a_bucket_with_no_root_keeps_the_prefix_rule(proj, tmp_checkpoint_dir):
    seed = store.location_slug(proj)
    _bucket(tmp_checkpoint_dir, seed, proj)
    _bucket(tmp_checkpoint_dir, f"{seed}-plugin")
    _bucket(tmp_checkpoint_dir, "-unrelated")
    assert [s["slug"] for s in store.sibling_buckets(proj)] == [
        f"{seed}-plugin"]


def test_the_project_is_not_its_own_child(proj, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-permanent", proj)
    assert store.sibling_buckets(proj) == []


def test_a_rebound_bucket_is_measured_by_its_permanent_name(
        proj, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, "-permanent", proj)
    _bucket(tmp_checkpoint_dir, "-permanent-plugin")  # no root: prefix of it
    assert [s["slug"] for s in store.sibling_buckets(proj)] == [
        "-permanent-plugin"]


def test_a_bucket_without_a_pointer_is_not_listed(proj, tmp_checkpoint_dir):
    _bucket(tmp_checkpoint_dir, store.location_slug(proj), proj)
    _bucket(tmp_checkpoint_dir, "-below", proj + "/plugin", latest=False)
    assert store.sibling_buckets(proj) == []


# ---- migrate asks the resolver ------------------------------------------------------


@pytest.fixture
def linked(tmp_path, tmp_checkpoint_dir):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    legacy = _dir(buckets.legacy_slug(str(link)))
    legacy.mkdir(parents=True)
    (legacy / "requests.jsonl").write_text(_row("x"), encoding="utf-8")
    return str(link), str(real.resolve())


def test_migrate_refuses_when_the_target_name_belongs_to_another_location(
        linked, tmp_checkpoint_dir):
    """A lossy twin holds the target's name: the resolver would birth this
    location elsewhere, so pouring the legacy bucket into that name would give
    this project's history to the other one."""
    link, real = linked
    target = buckets.target_slug(link)
    twin = _dir(target)
    twin.mkdir()
    (twin / "root").write_text("/another/location\n", encoding="utf-8")
    before = _snapshot()
    with pytest.raises(buckets.MigrationError, match="bucket merge"):
        buckets.migrate(link)
    assert _snapshot() == before


def test_migrate_into_a_free_target_still_renames(linked):
    link, _real = linked
    assert buckets.migrate(link)["mode"] == "rename"


def test_migrate_allows_a_legacy_bucket_the_resolver_binds_to_this_folder(
        linked, tmp_checkpoint_dir):
    """A legacy bucket whose root already names the folder IS the folder's
    bucket; migrating it into the rule's name is exactly what the verb is for."""
    link, real = linked
    legacy = _dir(buckets.legacy_slug(link))
    (legacy / "root").write_text(real + "\n", encoding="utf-8")
    assert store.bucket_of(real) == legacy.name
    record = buckets.migrate(link)
    assert record["mode"] == "rename"
    assert store.bucket_of(real) == buckets.target_slug(link)


def test_migrate_dry_run_makes_no_bucket(linked):
    link, _real = linked
    before = _snapshot()
    buckets.migrate(link, dry_run=True)
    assert _snapshot() == before


def test_the_merge_path_stamps_the_target_with_the_resolved_folder(
        linked, tmp_checkpoint_dir, monkeypatch):
    """The cross-device fallback creates the target by name: it must not go
    through the resolver, which could answer another bucket for this folder."""
    link, real = linked
    real_rename = os.rename

    def exdev(src, dst, *a, **k):
        if ".birth" in str(src) or ".birth" in str(dst):
            return real_rename(src, dst, *a, **k)
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(buckets.os, "rename", exdev)
    buckets.migrate(link)
    target = tmp_checkpoint_dir / buckets.target_slug(link)
    assert (target / "root").read_text(encoding="utf-8").strip() == real
    assert identity.resolve(real).slug == target.name


def test_the_hash_helper_is_what_the_twin_name_uses():
    assert identity._birth_names("/x/y", "s")[1] == "s-" + hashlib.sha256(
        os.path.normcase(os.path.realpath("/x/y")).encode()).hexdigest()[:6]
