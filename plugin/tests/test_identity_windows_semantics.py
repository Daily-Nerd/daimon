"""#1384 slice 6: the root comparisons under Windows path rules (design D9).

Nothing here runs on Windows. The comparison functions reach the platform only
through `os.path.normcase` (and `os.sep` in one hostgit helper), so a test can
give them ntpath's rules by swapping that one function, and a stored Windows
root is plain text on any host. That proves the LOGIC (case-insensitive,
separator-folding, separator-bounded matching). It cannot prove what only a
Windows filesystem decides: `Path.resolve()` and 8.3 short names, junctions,
open-handle `PermissionError`, the directory-rename birth claim, 0600 on the
signing seed, 260-character limits. Windows CI is not green, so those stay
design intent.

POSIX behavior is pinned in the same file: the swap is per test, and one test
asserts the shipped comparisons are still case-sensitive here.
"""

import ntpath
import os

import pytest

from daimon_briefing import bucket_tools, hostgit, identity, store


@pytest.fixture
def ntcase(monkeypatch):
    """`os.path.normcase` as Windows defines it: lower case, `/` to `\\`."""
    monkeypatch.setattr(os.path, "normcase", ntpath.normcase)


# ---- root_eq ---------------------------------------------------------------------------


@pytest.mark.parametrize("a, b", [
    ("C:\\Users\\b\\code\\x", "c:\\users\\B\\CODE\\x"),
    ("C:\\Users\\b\\code\\x", "C:/Users/b/code/x"),
    ("\\\\srv\\share\\x", "//SRV/share/X"),
])
def test_root_eq_folds_case_and_separators_under_windows_rules(ntcase, a, b):
    assert store.root_eq(a, b)


@pytest.mark.parametrize("a, b", [
    ("C:\\Users\\a", "C:\\Users\\ab"),
    ("C:\\Users\\b\\x", "D:\\Users\\b\\x"),
    ("C:\\Users\\b\\x", "C:\\Users\\b\\x\\y"),
])
def test_root_eq_still_tells_different_folders_apart(ntcase, a, b):
    assert not store.root_eq(a, b)


def test_on_posix_two_casings_stay_two_folders():
    """No behavior change off Windows: `normcase` is the identity there."""
    assert not store.root_eq("/Users/B/x", "/Users/b/x")
    assert store.root_eq("/Users/b/x", "/Users/b/x")


# ---- the separator-bounded match `remap` and the importer share --------------------------


@pytest.mark.parametrize("root, base, tail", [
    ("C:\\Users\\a\\code\\x", "C:\\Users\\a", "\\code\\x"),
    ("c:\\users\\A\\code\\x", "C:\\Users\\a", "\\code\\x"),
    ("C:\\Users\\a", "c:\\users\\a", ""),
    ("\\\\srv\\share\\x", "\\\\SRV\\share", "\\x"),
])
def test_below_matches_a_folder_and_its_children_ignoring_case(
        ntcase, root, base, tail):
    assert bucket_tools._below(root, base) == tail


@pytest.mark.parametrize("root, base", [
    ("C:\\Users\\abc\\x", "C:\\Users\\a"),
    ("C:\\Users\\a-two", "C:\\Users\\a"),
    ("D:\\Users\\a\\x", "C:\\Users\\a"),
    ("\\\\srv\\share2\\x", "\\\\srv\\share"),
])
def test_below_never_matches_a_longer_name_or_another_drive(ntcase, root, base):
    assert bucket_tools._below(root, base) is None


@pytest.mark.parametrize("raw", ["C:\\", "C:", "c:/", "\\\\", "\\\\srv", "/", ""])
def test_the_roots_too_broad_to_remap_are_refused_in_either_shape(raw):
    with pytest.raises(bucket_tools.RebindRefused):
        bucket_tools._normalized_root(raw, what="--from")


def test_a_trailing_separator_is_stripped_from_a_windows_root():
    assert bucket_tools._normalized_root(
        "C:\\Users\\a\\", what="--from") == "C:\\Users\\a"


def test_a_windows_root_maps_onto_a_posix_one_and_back():
    assert bucket_tools._mapped(
        "C:\\Users\\a\\code\\x", "C:\\Users\\a", "/home/b") == (
        "/home/b/code/x", None)
    assert bucket_tools._mapped(
        "/Users/a/code/x", "/Users/a", "C:\\Users\\b") == (
        "C:\\Users\\b\\code\\x", None)


# ---- the resolver finds a bucket by its recorded Windows root ----------------------------


def test_a_location_in_another_case_finds_the_bucket_that_recorded_the_root(
        ntcase, tmp_checkpoint_dir):
    """Today two casings are two buckets; the shared index compares normcase,
    so the second casing is found instead of birthing a twin."""
    bucket = tmp_checkpoint_dir / "-win-proj"
    bucket.mkdir(parents=True)
    (bucket / "root").write_text("C:\\Users\\A\\Code\\proj\n", encoding="utf-8")
    answer = identity.resolve("c:/users/a/code/proj")
    assert (answer.slug, answer.bound) == ("-win-proj", True)


def test_a_sibling_folder_does_not_find_it(ntcase, tmp_checkpoint_dir):
    bucket = tmp_checkpoint_dir / "-win-proj"
    bucket.mkdir(parents=True)
    (bucket / "root").write_text("C:\\Users\\A\\Code\\proj\n", encoding="utf-8")
    assert identity.resolve("C:\\Users\\A\\Code\\proj2").slug != "-win-proj"


# ---- the importer's question: was this folder once the project's? ------------------------


def test_a_transcript_from_the_old_windows_home_is_found_in_the_history(
        ntcase, tmp_path, tmp_checkpoint_dir):
    project = tmp_path / "proj"
    project.mkdir()
    real = os.path.realpath(project)
    bucket = tmp_checkpoint_dir / store.location_slug(real)
    bucket.mkdir(parents=True)
    (bucket / "root").write_text(f"{real}\n", encoding="utf-8")
    bucket_tools.write_identity(
        bucket, real, {}, history=["C:\\Users\\a\\code\\proj"])
    for cwd in ("C:\\Users\\a\\code\\proj", "c:\\USERS\\a\\Code\\proj\\plugin",
                "C:/Users/a/code/proj"):
        assert bucket_tools.was_located_under_former_root(real, cwd), cwd
    for cwd in ("C:\\Users\\a\\code\\proj-two", "D:\\Users\\a\\code\\proj"):
        assert not bucket_tools.was_located_under_former_root(real, cwd), cwd


# ---- hostgit's own containment test (design: UNVERIFIED on Windows) ----------------------


def test_hostgit_below_is_separator_bounded_and_case_blind_on_windows(
        monkeypatch, ntcase):
    """`_below` compares with `os.sep` after `normcase`, like every other
    root comparison (#1401). With a backslash separator it bounds at a
    separator, and a different CASING of the same folder, which a
    case-insensitive volume treats as the same folder, is below it."""
    monkeypatch.setattr(os, "sep", "\\")
    assert hostgit._below("C:\\r\\sub", "C:\\r")
    assert not hostgit._below("C:\\rr", "C:\\r")
    assert hostgit._below("c:\\r\\sub", "C:\\r")
    assert hostgit._below("C:\\R", "c:\\r")
    assert hostgit._below("C:/r/sub", "C:\\r")


def test_hostgit_declarations_are_found_in_any_casing_on_windows(
        tmp_path, ntcase):
    """`_declared` is keyed like every other root comparison (#1401): a host
    that declared `C:\\Repo` and a caller that names `c:\\repo` mean one
    folder, so the declaration is found, redeclaring it in the other casing
    is the same declaration, and it still covers a path below it."""
    hostgit.configure(posture=hostgit.Posture.SEALED, ceiling=tmp_path)
    try:
        hostgit.declare(tmp_path / "Repo", slug="acme-widget")
        assert hostgit.declared(tmp_path / "REPO").slug == "acme-widget"
        assert hostgit.declared_slug(str(tmp_path / "repo")) == "acme-widget"
        again = hostgit.declare(tmp_path / "repo", slug="acme-widget")
        assert again.slug == "acme-widget"
        with pytest.raises(ValueError):
            hostgit.declare(tmp_path / "rEPO", slug="other")
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


def test_hostgit_declarations_stay_case_sensitive_on_posix(tmp_path):
    hostgit.configure(posture=hostgit.Posture.SEALED, ceiling=tmp_path)
    try:
        hostgit.declare(tmp_path / "Repo", slug="acme-widget")
        assert hostgit.declared(tmp_path / "repo") is None
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


def test_hostgit_below_stays_case_sensitive_on_posix(monkeypatch):
    monkeypatch.setattr(os.path, "normcase", os.fspath)
    assert not hostgit._below("/Users/a/sub", "/users/a")
