"""Slice 4b: the failure edges of the resolver, the claim and the verbs.

Each test names a failure the happy-path files do not reach and pins what the
code does there: never raise from resolution, defer a store it cannot write,
and leave the store as it was when a rebind meets a lock, a race or an
identity record it cannot read.
"""

import errno
import os
from pathlib import Path

import pytest

from daimon_briefing import bucket_tools, cli, identity, jsonl, store
from daimon_briefing.cli import projects as cli_projects

from tests.test_identity_resolver import _bucket


@pytest.fixture
def where(tmp_path):
    p = tmp_path / "work" / "proj"
    p.mkdir(parents=True)
    return str(p.resolve())


# ---- the scan ---------------------------------------------------------------------------


def test_a_scan_with_no_store_root_is_empty(monkeypatch):
    monkeypatch.setattr(identity, "_store_root", lambda: None)
    assert identity._scan() == {}


def test_a_configuration_fault_resolves_to_the_unbound_seed(
        where, monkeypatch):
    """A location is still named when configuration cannot say where the store
    is: the seed, unbound. The caller's own join raises the fault."""
    monkeypatch.setattr(identity, "_store_root", lambda: None)
    assert identity.resolve(where) == identity.Resolution(
        store.location_slug(where), False)


def test_a_directory_entry_that_cannot_be_inspected_is_skipped(
        where, tmp_checkpoint_dir, monkeypatch):
    _bucket(tmp_checkpoint_dir, "-real", where)

    class Broken:
        name = "-broken"
        path = "/nowhere"

        def is_dir(self):
            raise OSError(errno.EIO, "io error")

    class Listing:
        def __init__(self, entries):
            self.entries = entries

        def __iter__(self):
            return iter(self.entries)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    real_scandir = os.scandir
    monkeypatch.setattr(
        identity.os, "scandir",
        lambda p: (Listing([Broken(), *real_scandir(p)])))
    assert identity._scan() == {os.path.normcase(where): ["-real"]}


# ---- the claim ---------------------------------------------------------------------------


def test_a_blank_location_claims_nothing(tmp_checkpoint_dir):
    assert identity.claim("   ") is None
    assert not tmp_checkpoint_dir.exists()


def test_a_slug_claim_in_an_unwritable_store_defers(tmp_checkpoint_dir):
    tmp_checkpoint_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_checkpoint_dir.write_text("a file where the store should be")
    ref = identity.claim("-routed-by-slug")
    assert ref is not None and ref.bound is False


def test_the_stage_is_removed_when_its_root_cannot_be_written(
        tmp_checkpoint_dir, monkeypatch):
    real_open = os.open

    def failing(path, *a, **k):
        if str(path).endswith("/root"):
            raise OSError(errno.ENOSPC, "no space")
        return real_open(path, *a, **k)

    monkeypatch.setattr(identity.os, "open", failing)
    with pytest.raises(OSError):
        identity._stage(tmp_checkpoint_dir, "/some/location")
    assert list((tmp_checkpoint_dir / ".birth").iterdir()) == []


def test_a_file_exists_error_from_the_rename_means_taken(
        tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()

    def taken(src, dst, *a, **k):
        raise FileExistsError(errno.EEXIST, "exists")

    monkeypatch.setattr(identity.os, "rename", taken)
    assert identity._publish(stage, tmp_path / "bucket") is False
    assert not stage.exists()


def test_a_name_that_exists_is_taken_without_a_rename(tmp_path, monkeypatch):
    """rename onto an EMPTY directory would replace it: an existing entry is
    never renamed over."""
    stage = tmp_path / "stage"
    stage.mkdir()
    target = tmp_path / "bucket"
    target.mkdir()

    def never(*a, **k):
        raise AssertionError("renamed over an existing name")

    monkeypatch.setattr(identity.os, "rename", never)
    assert identity._publish(stage, target) is False
    assert target.is_dir() and not stage.exists()


def test_losing_the_legacy_stamp_to_another_location_births_a_twin(
        where, tmp_checkpoint_dir, monkeypatch):
    """Two locations race to stamp one unstamped bucket: the loser re-resolves,
    finds the seed taken and is born under a suffixed name."""
    seed = store.location_slug(where)
    legacy = _bucket(tmp_checkpoint_dir, seed)
    real = store.stamp_root

    def lose(bucket, location):
        (Path(bucket) / "root").write_text("/the/winner\n", encoding="utf-8")
        return real(bucket, location)

    monkeypatch.setattr(store, "stamp_root", lose)
    ref = identity.claim(where)
    assert ref.slug != seed and ref.slug.startswith(seed + "-")
    assert (legacy / "root").read_text(encoding="utf-8").strip() == "/the/winner"


def test_a_claim_that_never_settles_fails_loudly(where, tmp_checkpoint_dir,
                                                 monkeypatch):
    monkeypatch.setattr(identity, "_claim_once", lambda text, res: None)
    with pytest.raises(identity.BucketClaimFailed, match="settle"):
        identity.claim(where)


# ---- the notice never costs the briefing ----------------------------------------------------


def test_a_resolver_failure_never_costs_the_briefing(
        where, tmp_checkpoint_dir, monkeypatch, capsys):
    def boom(project):
        raise RuntimeError("the resolver broke")

    monkeypatch.setattr(identity, "is_unbound", boom)
    assert cli.main(["brief", "--project", where]) == 0
    out = capsys.readouterr().out
    assert "No memory is bound" not in out


# ---- adopt and remap at their edges ---------------------------------------------------------


def test_adopting_into_an_unstamped_bucket_fails_closed_on_a_lock(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    import contextlib

    bucket = _bucket(tmp_checkpoint_dir, "-unstamped")

    @contextlib.contextmanager
    def never(_d):
        yield False

    monkeypatch.setattr(jsonl, "dir_lock", never)
    assert bucket_tools._stamp_unstamped(bucket, str(tmp_path)) == "locked"
    assert not (bucket / "root").exists()


def test_adopting_into_a_bucket_that_was_stamped_meanwhile_is_raced(
        tmp_path, tmp_checkpoint_dir):
    bucket = _bucket(tmp_checkpoint_dir, "-stamped", "/someone/else")
    assert bucket_tools._stamp_unstamped(bucket, str(tmp_path)) == "raced"
    assert identity.read_root(bucket) == "/someone/else"


def test_adopt_refuses_when_the_bucket_was_rebound_while_it_ran(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    bucket = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    monkeypatch.setattr(bucket_tools, "_write_rebind",
                        lambda b, old, to: "raced")
    with pytest.raises(bucket_tools.RebindRefused, match="rebound by someone"):
        bucket_tools.adopt("-old", str(new), apply=True)
    assert identity.read_root(bucket) == "/gone/old"


def test_remap_skips_a_bucket_whose_identity_record_cannot_be_read(
        tmp_path, tmp_checkpoint_dir):
    new = tmp_path / "bob"
    (new / "p").mkdir(parents=True)
    bucket = _bucket(tmp_checkpoint_dir, "-torn", str(tmp_path / "alice" / "p"))
    (bucket / "identity").write_text("{torn", encoding="utf-8")
    plan = bucket_tools.remap(str(tmp_path / "alice"), str(new), apply=True)
    assert [a["action"] for a in plan["actions"]] == ["unreadable"]
    assert identity.read_root(bucket) == str(tmp_path / "alice" / "p")


# ---- the adopt render ---------------------------------------------------------------------------


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)


def test_adopt_of_an_adopted_bucket_says_there_is_nothing_to_do(
        tmp_path, tmp_checkpoint_dir, tty, capsys):
    new = tmp_path / "new"
    new.mkdir()
    _bucket(tmp_checkpoint_dir, "-old", str(new.resolve()))
    rc = cli.main(["bucket", "adopt", "--bucket=-old", f"--path={new}"])
    assert rc == 0
    assert "nothing to do: -old already records" in capsys.readouterr().out


def test_adopt_shows_matching_and_differing_anchors_on_a_terminal(
        tmp_path, tmp_checkpoint_dir, tty, capsys, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    bucket = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    bucket_tools.write_identity(
        bucket, "/gone/old", {"folder": "new", "origin": "git@h:o/other.git"})
    real = bucket_tools.anchors_for
    monkeypatch.setattr(
        bucket_tools, "anchors_for",
        lambda path: dict(real(path), origin="git@h:o/this.git"))
    cli.main(["bucket", "adopt", "--bucket=-old", f"--path={new}"])
    out = capsys.readouterr().out
    assert "same folder as that folder" in out
    assert "different origin" in out
    assert store.bucket_of(str(new.resolve())) != "-old"  # a preview only
