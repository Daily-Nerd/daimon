"""Slice 4b: `bucket adopt` and `bucket remap`, the explicit rebind verbs.

A bucket's name never changes; its `root` file is the one binding to a
location, and these two verbs are the only way it is rewritten. Both preview by
default and write only with `apply`; both refuse on any doubt and leave the
store as it was; neither touches a signed flat checkpoint, a receipt or a
ledger row.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from daimon_briefing import bucket_tools, identity, jsonl, store
from daimon_briefing.surfaces import Writer

from tests.test_bucket_merge import _snapshot


def _make(root_dir, name, root=None):
    d = root_dir / name
    d.mkdir(parents=True, exist_ok=True)
    if root is not None:
        (d / "root").write_text(f"{root}\n", encoding="utf-8")
    return d


def _root(bucket):
    return (bucket / "root").read_text(encoding="utf-8").strip()


def _history(bucket):
    return bucket_tools.read_identity(bucket)["history"]


@pytest.fixture
def home(tmp_path):
    """Two project folders on 'this machine': the old tree is gone, the new
    exists."""
    new = tmp_path / "home" / "bob" / "code"
    (new / "proj").mkdir(parents=True)
    (new / "other").mkdir()
    return {"old": str(tmp_path / "home" / "alice" / "code"),
            "new": str(new.resolve()), "tmp": tmp_path}


# ---- adopt -------------------------------------------------------------------------


def test_adopt_previews_by_default_and_writes_nothing(
        home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    before = _snapshot()
    plan = bucket_tools.adopt("-old-proj", home["new"] + "/proj")
    assert plan["applied"] is False and plan["action"] == "rewrite"
    assert plan["to"] == home["new"] + "/proj"
    assert _snapshot() == before
    assert _root(b) == home["old"] + "/proj"


def test_adopt_apply_rebinds_the_root_and_keeps_the_old_one_in_history(
        home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    plan = bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    assert plan["applied"] is True
    assert _root(b) == home["new"] + "/proj"
    assert _history(b) == [home["old"] + "/proj"]
    assert bucket_tools.read_identity(b)["root"] == home["new"] + "/proj"
    assert store.bucket_of(home["new"] + "/proj") == "-old-proj"


def test_adopt_appends_to_an_existing_history(home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    bucket_tools.write_identity(b, home["old"] + "/proj", {"folder": "proj"},
                                history=["/even/older"])
    bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    record = bucket_tools.read_identity(b)
    assert record["history"] == ["/even/older", home["old"] + "/proj"]
    assert record["anchors"]["folder"] == "proj" or record["anchors"]


def test_adopt_stores_the_real_path_of_a_symlinked_folder(
        home, tmp_checkpoint_dir):
    link = home["tmp"] / "shortcut"
    link.symlink_to(home["new"] + "/proj", target_is_directory=True)
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    bucket_tools.adopt("-old-proj", str(link), apply=True)
    assert _root(b) == home["new"] + "/proj"


def test_adopt_a_second_time_is_a_no_op(home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    before = _snapshot()
    again = bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    assert again["action"] == "kept"
    assert _snapshot() == before
    assert len(_history(b)) == 1


def test_adopt_refuses_a_folder_that_does_not_exist(home, tmp_checkpoint_dir):
    _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused, match="not a directory"):
        bucket_tools.adopt("-old-proj", home["new"] + "/nope", apply=True)
    assert _snapshot() == before


def test_adopt_refuses_a_bucket_that_is_not_there(home, tmp_checkpoint_dir):
    with pytest.raises(bucket_tools.RebindRefused, match="no bucket"):
        bucket_tools.adopt("-nope", home["new"] + "/proj")
    with pytest.raises(bucket_tools.RebindRefused, match="not a bucket name"):
        bucket_tools.adopt("../x", home["new"] + "/proj")


def test_adopt_refuses_when_another_bucket_already_holds_the_folder(
        home, tmp_checkpoint_dir):
    """The day-one fresh bucket: fold it with `merge` first, or the folder
    would answer for two buckets."""
    _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    _make(tmp_checkpoint_dir, "-fresh-today", home["new"] + "/proj")
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused,
                       match="-fresh-today.*bucket merge"):
        bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    assert _snapshot() == before


def test_adopt_refuses_a_bucket_whose_root_is_a_live_folder(
        home, tmp_checkpoint_dir):
    """A clone (D2): the recorded folder still exists, so the bucket belongs to
    it; rebinding would steal it. `force` is the person saying they looked."""
    _make(tmp_checkpoint_dir, "-live-clone", home["new"] + "/other")
    with pytest.raises(bucket_tools.RebindRefused, match="still exists"):
        bucket_tools.adopt("-live-clone", home["new"] + "/proj", apply=True)
    bucket_tools.adopt("-live-clone", home["new"] + "/proj", apply=True,
                       force=True)
    assert _root(tmp_checkpoint_dir / "-live-clone") == home["new"] + "/proj"


def test_adopt_will_not_replace_an_identity_record_it_cannot_read(
        home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    (b / "identity").write_text("{torn", encoding="utf-8")
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused, match="identity"):
        bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    assert _snapshot() == before


def test_adopt_refuses_when_the_bucket_cannot_be_locked(
        home, tmp_checkpoint_dir, monkeypatch):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    import contextlib

    @contextlib.contextmanager
    def never(_d):
        yield False

    monkeypatch.setattr(jsonl, "dir_lock", never)
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused, match="lock"):
        bucket_tools.adopt("-old-proj", home["new"] + "/proj", apply=True)
    assert _snapshot() == before
    assert _root(b) == home["old"] + "/proj"


def test_adopt_an_unstamped_bucket_stamps_it(home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-never-stamped")
    bucket_tools.adopt("-never-stamped", home["new"] + "/proj", apply=True)
    assert _root(b) == home["new"] + "/proj"
    assert _history(b) == []


def test_adopt_evidence_compares_the_anchors(home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-old-proj", home["old"] + "/proj")
    bucket_tools.write_identity(
        b, home["old"] + "/proj", {"origin": None, "root_commit": None,
                                   "folder": "proj"})
    plan = bucket_tools.adopt("-old-proj", home["new"] + "/proj")
    assert plan["evidence"]["matched"] == ["folder"]


def test_signed_flat_files_are_byte_identical_after_an_adopt(
        home, tmp_checkpoint_dir, sample_checkpoint):
    old = Path(home["old"] + "/proj")
    old.mkdir(parents=True)
    store.write_checkpoint("S1", dict(sample_checkpoint,
                                      created="2030-01-01T00:00:00Z"),
                           project_dir=str(old), writer=Writer.HUMAN)
    flat = {p.name: p.read_bytes() for p in tmp_checkpoint_dir.iterdir()
            if p.is_file() and p.name != "latest.json"}
    assert "S1.json" in flat
    bucket = tmp_checkpoint_dir / store.bucket_of(str(old.resolve()))
    old.rename(home["tmp"] / "gone")
    bucket_tools.adopt(bucket.name, home["new"] + "/proj", apply=True)
    assert {p.name: p.read_bytes() for p in tmp_checkpoint_dir.iterdir()
            if p.is_file() and p.name != "latest.json"} == flat


# ---- remap -------------------------------------------------------------------------


@pytest.fixture
def two_buckets(home, tmp_checkpoint_dir):
    a = _make(tmp_checkpoint_dir, "-alice-proj", home["old"] + "/proj")
    o = _make(tmp_checkpoint_dir, "-alice-other", home["old"] + "/other")
    return a, o


def test_remap_previews_by_default_and_writes_nothing(
        home, two_buckets):
    before = _snapshot()
    plan = bucket_tools.remap(home["old"], home["new"])
    assert plan["applied"] is False
    assert {a["bucket"]: a["action"] for a in plan["actions"]} == {
        "-alice-proj": "rewrite", "-alice-other": "rewrite"}
    assert _snapshot() == before


def test_remap_apply_rewrites_every_bucket_under_the_old_root(
        home, two_buckets):
    a, o = two_buckets
    bucket_tools.remap(home["old"], home["new"], apply=True)
    assert _root(a) == home["new"] + "/proj"
    assert _root(o) == home["new"] + "/other"
    assert _history(a) == [home["old"] + "/proj"]


def test_remap_a_second_run_changes_nothing(home, two_buckets):
    bucket_tools.remap(home["old"], home["new"], apply=True)
    before = _snapshot()
    again = bucket_tools.remap(home["old"], home["new"], apply=True)
    assert [x for x in again["actions"] if x["action"] == "rewrite"] == []
    assert _snapshot() == before


def test_remap_is_separator_bounded(home, tmp_checkpoint_dir):
    """`/Users/a` never matches `/Users/abc`."""
    (home["tmp"] / "abc").mkdir()
    (home["tmp"] / "a").mkdir()
    near = _make(tmp_checkpoint_dir, "-near", str(home["tmp"] / "abc"))
    exact = _make(tmp_checkpoint_dir, "-exact", str(home["tmp"] / "a"))
    bucket_tools.remap(str(home["tmp"] / "a"), home["new"], apply=True,
                       force=True)  # both folders still exist here
    assert _root(near) == str(home["tmp"] / "abc")
    assert _root(exact) == home["new"]


def test_remap_skips_a_bucket_whose_target_is_not_there_yet(
        home, tmp_checkpoint_dir):
    """On a fresh machine most projects are not cloned yet: skipped, listed,
    not an error."""
    here = _make(tmp_checkpoint_dir, "-here", home["old"] + "/proj")
    gone = _make(tmp_checkpoint_dir, "-not-cloned", home["old"] + "/never")
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    actions = {a["bucket"]: a["action"] for a in plan["actions"]}
    assert actions == {"-here": "rewrite", "-not-cloned": "missing"}
    assert _root(here) == home["new"] + "/proj"
    assert _root(gone) == home["old"] + "/never"


def test_remap_skips_a_bucket_whose_target_already_belongs_to_another(
        home, tmp_checkpoint_dir):
    a = _make(tmp_checkpoint_dir, "-alice-proj", home["old"] + "/proj")
    _make(tmp_checkpoint_dir, "-fresh-today", home["new"] + "/proj")
    ok = _make(tmp_checkpoint_dir, "-alice-other", home["old"] + "/other")
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    by = {x["bucket"]: x for x in plan["actions"]}
    assert by["-alice-proj"]["action"] == "collision"
    assert by["-alice-proj"]["other"] == "-fresh-today"
    assert _root(a) == home["old"] + "/proj"
    assert _root(ok) == home["new"] + "/other"


def test_remap_two_buckets_that_would_land_on_one_folder_move_only_one(
        home, tmp_checkpoint_dir):
    """Two old roots whose mapped folders are one real folder (a symlink): the
    second would make the folder answer for two buckets, so it is a collision
    against the first, listed for `merge`."""
    (Path(home["new"]) / "via").symlink_to(home["new"] + "/proj",
                                           target_is_directory=True)
    one = _make(tmp_checkpoint_dir, "-one", home["old"] + "/proj")
    two = _make(tmp_checkpoint_dir, "-two", home["old"] + "/via")
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    by = {a["bucket"]: a for a in plan["actions"]}
    assert by["-one"]["action"] == "rewrite"
    assert by["-two"]["action"] == "collision"
    assert by["-two"]["other"] == "-one"
    assert _root(one) == home["new"] + "/proj"
    assert _root(two) == home["old"] + "/via"


@pytest.mark.parametrize("bad", ["", "/", "//", "C:\\", "C:/", "C:", "\\\\",
                                 "   "])
def test_remap_refuses_a_root_that_matches_everything(
        bad, home, two_buckets):
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused):
        bucket_tools.remap(bad, home["new"], apply=True)
    assert _snapshot() == before


def test_remap_normalizes_a_trailing_separator(home, two_buckets):
    a, _o = two_buckets
    bucket_tools.remap(home["old"] + "/", home["new"] + "/", apply=True)
    assert _root(a) == home["new"] + "/proj"


def test_remap_refuses_a_relative_or_nested_target(home, two_buckets):
    with pytest.raises(bucket_tools.RebindRefused, match="absolute"):
        bucket_tools.remap(home["old"], "relative/dir")
    with pytest.raises(bucket_tools.RebindRefused, match="inside"):
        bucket_tools.remap(home["old"], home["old"] + "/nested")
    with pytest.raises(bucket_tools.RebindRefused, match="inside"):
        bucket_tools.remap(home["old"], home["old"])


def test_remap_rewrites_a_windows_root_to_this_machines_separators(
        home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-win-proj", "C:\\Users\\a\\code\\proj")
    bucket_tools.remap("C:\\Users\\a\\code", home["new"], apply=True)
    assert _root(b) == home["new"] + "/proj"


def test_remap_refuses_a_tail_holding_the_targets_foreign_separator(
        home, tmp_checkpoint_dir):
    """A POSIX folder name with a backslash in it, mapped onto a Windows root,
    would silently become two path components."""
    b = _make(tmp_checkpoint_dir, "-odd", "/Users/a/we\\ird")
    plan = bucket_tools.remap("/Users/a", "C:\\Users\\b", apply=True)
    assert plan["actions"][0]["action"] == "separator"
    assert _root(b) == "/Users/a/we\\ird"


def test_remap_cannot_reach_an_unstamped_bucket(home, tmp_checkpoint_dir):
    b = _make(tmp_checkpoint_dir, "-unstamped")
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    assert plan["actions"] == []
    assert not (b / "root").exists()


def test_remap_skips_a_bucket_it_cannot_lock_and_says_so(
        home, two_buckets, monkeypatch):
    import contextlib
    real = jsonl.dir_lock

    @contextlib.contextmanager
    def picky(d):
        if Path(d).name == "-alice-proj":
            yield False
        else:
            with real(d) as held:
                yield held

    monkeypatch.setattr(jsonl, "dir_lock", picky)
    a, o = two_buckets
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    by = {x["bucket"]: x["action"] for x in plan["actions"]}
    assert by == {"-alice-proj": "locked", "-alice-other": "rewrite"}
    assert _root(a) == home["old"] + "/proj"
    assert _root(o) == home["new"] + "/other"


def test_remap_skips_a_bucket_whose_root_changed_after_the_plan(
        home, two_buckets, monkeypatch):
    """Re-read under the lock: a bucket rebound by someone else in between is
    left alone."""
    a, _o = two_buckets
    real = bucket_tools._write_rebind

    def sneaky(bucket, old, new):
        if bucket.name == "-alice-proj":
            (bucket / "root").write_text("/someone/else\n", encoding="utf-8")
        return real(bucket, old, new)

    monkeypatch.setattr(bucket_tools, "_write_rebind", sneaky)
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    by = {x["bucket"]: x["action"] for x in plan["actions"]}
    assert by["-alice-proj"] == "raced"
    assert _root(a) == "/someone/else"


def test_signed_flat_files_are_byte_identical_after_a_remap(
        home, tmp_checkpoint_dir, sample_checkpoint):
    old = Path(home["old"] + "/proj")
    old.mkdir(parents=True)
    store.write_checkpoint("S1", dict(sample_checkpoint,
                                      created="2030-01-01T00:00:00Z"),
                           project_dir=str(old), writer=Writer.HUMAN)
    flat = {p.name: p.read_bytes() for p in tmp_checkpoint_dir.iterdir()
            if p.is_file()}
    old.rename(home["tmp"] / "gone")
    bucket_tools.remap(home["old"], home["new"], apply=True)
    assert {p.name: p.read_bytes() for p in tmp_checkpoint_dir.iterdir()
            if p.is_file()} == flat


# ---- the rebind makes the new location find the old history ------------------------------


def test_after_a_remap_the_new_location_finds_the_old_bucket_and_the_old_seed_does_not(
        home, two_buckets, tmp_checkpoint_dir):
    a, _o = two_buckets
    bucket_tools.remap(home["old"], home["new"], apply=True)
    with identity.operation():
        assert store.bucket_of(home["new"] + "/proj") == "-alice-proj"
    claimed = identity.claim(home["new"] + "/proj")
    assert claimed.slug == "-alice-proj"
    assert os.path.isdir(a)


def test_a_remap_between_two_operations_is_seen_by_the_second(
        home, two_buckets):
    with identity.operation():
        assert store.bucket_of(home["new"] + "/proj") == store.location_slug(
            home["new"] + "/proj")
    bucket_tools.remap(home["old"], home["new"], apply=True)
    with identity.operation():
        assert store.bucket_of(home["new"] + "/proj") == "-alice-proj"


def test_a_ref_claimed_before_a_rebind_fails_loudly_after_it(
        home, tmp_checkpoint_dir):
    """The writer keeps the ref it claimed; the directory no longer agrees."""
    proj = Path(home["new"]) / "proj"
    ref = identity.claim(str(proj))
    bucket = tmp_checkpoint_dir / ref.slug
    other = Path(home["new"]) / "other"
    bucket_tools.adopt(ref.slug, str(other), apply=True, force=True)
    with pytest.raises(identity.BucketMismatch):
        store.ensure_bucket(ref)
    assert _root(bucket) == str(other)


def test_the_digest_helper_matches_the_claim(home, tmp_checkpoint_dir):
    """Sanity for the tests above: the 6-hex birth suffix is sha256 of the
    normalized real path."""
    where = home["new"] + "/proj"
    want = hashlib.sha256(os.path.normcase(
        os.path.realpath(where)).encode()).hexdigest()[:6]
    assert identity._birth_names(where, "s")[1] == f"s-{want}"


def test_json_receipt_is_serializable(home, two_buckets):
    plan = bucket_tools.remap(home["old"], home["new"], apply=True)
    assert json.loads(json.dumps(plan))["applied"] is True
