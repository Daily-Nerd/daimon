"""Slice 0 of project identity: pins, no behavior change.

Design: "project identity survives machine, username, folder and worktree
changes" (rev 3 plus its refutation). This file pins what the store does TODAY
so a later slice cannot change it silently, and records the target invariants
as strict xfails that flip to XPASS (and so fail the run) the moment a slice
delivers them, forcing the marker to be removed on purpose.

What is pinned:
  * the exact output of the pure slug transform (`store.project_slug`, #913)
    over a golden corpus, including the cases that look like bugs on purpose;
  * that the transform is a fixed point and does not touch the filesystem;
  * the resolver's behavior for slug-shaped and non-absolute input;
  * the AST census of modules that carry a copy of the slug regex;
  * a security pin: a `.git` pointer file naming ANOTHER repository's admin
    directory must never make the checkout resolve to that repository's bucket.

Already pinned elsewhere, not repeated here: slug passthrough in
test_store_resolution.py (`test_project_bucket_passes_a_slug_through`) and the
declared-slug idempotence in test_hostgit_declare.py.
"""

import ast
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import config, store
from daimon_briefing.surfaces import Writer

from .test_read_layers import PLUGIN

# ---- golden slug corpus --------------------------------------------------------------------
# (input, exact output). The rule: every char that is not `\w` or '-' becomes
# '-'. Nothing is collapsed, trimmed (beyond outer whitespace) or lowercased.
GOLDEN = [
    ("/Users/b/code/x", "-Users-b-code-x"),
    ("/Users/b/my_proj", "-Users-b-my_proj"),             # underscore survives
    ("/Users/b/caf\u00e9", "-Users-b-caf\u00e9"),         # accented letters survive
    ("/Users/b/\u65e5\u672c", "-Users-b-\u65e5\u672c"),   # non-Latin scripts survive
    ("/Users/b/my proj", "-Users-b-my-proj"),             # space folds
    ("/Users/b/a.b.c", "-Users-b-a-b-c"),                 # dots fold
    ("/Users/b/a--b", "-Users-b-a--b"),                   # doubled hyphen kept
    ("/Users/b//x", "-Users-b--x"),                       # doubled slash not collapsed
    ("/Users/b/code/x/", "-Users-b-code-x-"),             # trailing slash becomes '-'
    ("  /Users/b/x  ", "-Users-b-x"),                     # only outer whitespace stripped
    ("C:\\Users\\b\\code\\x", "C--Users-b-code-x"),       # Windows shapes, strings only
    ("C:/Users/b/code/x", "C--Users-b-code-x"),
    ("c:\\Users\\b\\code\\x", "c--Users-b-code-x"),       # drive-letter case kept
    ("\\\\srv\\share\\x", "--srv-share-x"),
    ("relative/dir", "relative-dir"),                     # no absolutizing here
    ("-Users-b-code-x", "-Users-b-code-x"),               # a slug maps to itself
]


@pytest.mark.parametrize("raw, expected", GOLDEN)
def test_project_slug_golden_corpus(raw, expected):
    assert store.project_slug(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "\n\t"])
def test_project_slug_is_none_for_an_unknown_project(raw):
    assert store.project_slug(raw) is None


def test_project_slug_does_not_realpath_a_symlinked_directory(tmp_path):
    """A pure transform: two names for one directory give two slugs. Resolution
    (symlinks collapsed) lives in `config.resolve_project_dir`, not here."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    assert store.project_slug(str(link)) != store.project_slug(str(real.resolve()))
    assert store.project_slug(str(link)).endswith("-link")
    # The resolver, by contrast, collapses the link.
    assert config.resolve_project_dir(str(link)) == str(real.resolve())


@pytest.mark.parametrize("raw", [raw for raw, _ in GOLDEN])
def test_project_slug_is_a_fixed_point_over_the_corpus(raw):
    once = store.project_slug(raw)
    assert store.project_slug(once) == once


@pytest.mark.parametrize("raw", [raw for raw, _ in GOLDEN if "\\" in raw or ":" in raw])
def test_windows_shaped_strings_slug_to_separator_free_names(raw):
    """A Windows-shaped string is plain text on a POSIX host: the result carries
    no separator of either kind and no ':'."""
    out = store.project_slug(raw)
    assert out is not None
    assert not any(c in out for c in "/\\:")


def test_project_bucket_returns_a_slug_shaped_input_unchanged(tmp_path, monkeypatch):
    """Slug-shaped input has no separator and names no directory under the cwd,
    so the resolver passes it through and the bucket is the slug itself. Covers
    shapes the neighboring pins do not: unicode, underscore, a Windows
    drive-prefixed slug (no separator, so not a path on POSIX)."""
    monkeypatch.chdir(tmp_path)
    for slug in ("-Users-b-my_proj", "-Users-b-caf\u00e9", "C--Users-b-code-x",
                 "a--b"):
        assert store.project_bucket(slug) == slug


def test_project_bucket_resolves_a_non_absolute_path_against_the_cwd(
        tmp_path, monkeypatch):
    """A relative value WITH a separator is a path: it is absolutized, so the
    bucket is the slug of the absolute directory, not of the literal string."""
    d = tmp_path / "sub" / "dir"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    got = store.project_bucket("sub/dir")
    assert got == store.project_slug(str(d.resolve()))
    assert got != store.project_slug("sub/dir")


# ---- git fixtures --------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@x", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True)


def _repo_with_commit(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "f").write_text("x")
    _git(path, "add", "f")
    _git(path, "commit", "-q", "-m", "init")
    return path.resolve()


@pytest.fixture(autouse=True)
def _fresh_root_memo():
    config.resolve_project_root.cache_clear()
    yield
    config.resolve_project_root.cache_clear()


# ---- target invariant 1: a new username dir, after an explicit rebind ----------------------


@pytest.mark.xfail(
    strict=True,
    reason="project identity design (rev 3, slice 0): the bucket is derived from "
           "the absolute path, which embeds the username. No rebind verb exists, "
           "so a project tree at a new HOME path resolves to a NEW bucket and the "
           "old history is not found. Passes once the explicit rebind lands.")
def test_same_store_new_home_username_finds_the_same_bucket_after_rebind(
        tmp_path, tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    old_home = tmp_path / "home" / "alice"
    new_home = tmp_path / "home" / "bob"
    old_tree = old_home / "code" / "proj"
    new_tree = new_home / "code" / "proj"
    old_tree.mkdir(parents=True)
    new_tree.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(old_home))
    store.write_checkpoint("S1", dict(sample_checkpoint, created="2030-01-01T00:00:00Z"),
                           project_dir=str(old_tree), writer=Writer.HUMAN)

    # The same checkpoint store, a different username directory, the tree moved.
    monkeypatch.setenv("HOME", str(new_home))
    config.resolve_project_root.cache_clear()
    # TODO(rebind): the explicit rebind step goes here once the verb exists.

    assert store.project_bucket(str(new_tree)) == store.project_bucket(str(old_tree))
    assert store.sessions_since_count("2020-01-01T00:00:00Z",
                                      project_dir=str(new_tree)) == 1


def test_a_new_home_username_resolves_to_a_different_bucket_today(tmp_path):
    """The positive half of the xfail above: its mechanism, stated so the reason
    it fails cannot drift."""
    a = tmp_path / "home" / "alice" / "code" / "proj"
    b = tmp_path / "home" / "bob" / "code" / "proj"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert store.project_bucket(str(a)) != store.project_bucket(str(b))


# ---- target invariant 2: a linked worktree shares its main repo's bucket -------------------


@pytest.fixture
def main_and_worktree(tmp_path):
    main = _repo_with_commit(tmp_path / "main")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    return main, wt.resolve()


@pytest.mark.xfail(
    strict=True,
    reason="project identity design (rev 3, slice 0, worktrees D3): a linked "
           "worktree resolves to its own toplevel, so it gets its OWN bucket "
           "instead of its main repository's. Passes once worktrees map to the "
           "main repository's identity.")
def test_a_linked_worktree_resolves_to_its_main_repos_bucket(main_and_worktree):
    main, wt = main_and_worktree
    assert store.project_bucket(str(wt)) == store.project_bucket(str(main))


def test_a_linked_worktree_has_its_own_bucket_today(main_and_worktree):
    main, wt = main_and_worktree
    assert store.project_bucket(str(wt)) == store.project_slug(str(wt))
    assert store.project_bucket(str(wt)) != store.project_bucket(str(main))


# ---- security pin: a pointer at ANOTHER repo's admin dir ------------------------------------


def test_a_gitfile_pointing_at_another_repos_admin_dir_does_not_adopt_its_bucket(
        tmp_path, main_and_worktree):
    """`.git` as a file is a pointer anyone can write. A directory whose pointer
    names another repository's worktree admin dir must NOT resolve to that
    repository's bucket (nor to its worktree's), in this slice or any later
    one: following such a pointer blindly would let a hostile checkout read and
    write a victim project's history."""
    victim, victim_wt = main_and_worktree
    admin = victim / ".git" / "worktrees" / victim_wt.name
    assert admin.is_dir()

    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / ".git").write_text(f"gitdir: {admin}\n")

    got = store.project_bucket(str(evil))
    assert got != store.project_bucket(str(victim))
    assert got != store.project_bucket(str(victim_wt))
    # Today git itself answers the directory the pointer sits in.
    assert got == store.project_slug(str(evil.resolve()))


# ---- census: modules that carry a copy of the slug regex ------------------------------------

_SLUG_REGEX = r"[^\w-]"
# (file relative to the repo root, enclosing function). Slice 1 deleted the
# teamproject and hook-lib copies and moved the store copy into `scope`: the
# char rule now has exactly one definition, `scope._SEGMENT_RE` (module level).
EXPECTED_SLUG_REGEX_SITES = {
    ("plugin/daimon_briefing/scope.py", "<module>"),
}


def _regex_sites(path: Path, rel: str) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn)
            if isinstance(child, ast.Constant) and child.value == _SLUG_REGEX:
                found.add((rel, fn or "<module>"))
            visit(child, name)

    visit(tree, "")
    return found


def test_the_slug_regex_lives_in_exactly_the_known_places():
    """A new copy of the slug transform is a new place where identity can
    drift. It fails here until it is listed on purpose (or, better, deleted in
    favor of one owner). Whether a CALLER slugs a location is not statically
    decidable, so this only lists definitions of the transform."""
    repo = PLUGIN.parent
    found: set[tuple[str, str]] = set()
    for root in (PLUGIN / "daimon_briefing", repo / "hook"):
        for p in sorted(root.rglob("*.py")):
            found |= _regex_sites(p, p.relative_to(repo).as_posix())
    assert found == EXPECTED_SLUG_REGEX_SITES
