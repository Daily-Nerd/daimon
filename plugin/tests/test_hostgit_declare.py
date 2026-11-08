"""hostgit.declare: a host states a project's identity (#1308, slice 4).

A declaration supplies DATA (slug, origin URL) and pins which directory a path
under the root resolves to. It never bypasses the shape check or the config
gate. Sealed posture only. Tests use tmp dirs only.
"""


import pytest

from daimon_briefing import api, config, hostgit, store
from tests.test_hostgit_config_gate import Spy, needs_git, spy  # noqa: F401
from tests.test_hostgit_sealed import SEALED, SealedRecorder, fake_repo

URL = "https://github.com/acme/widget.git"


@pytest.fixture
def home(tmp_path):
    ceiling = tmp_path / "home"
    ceiling.mkdir()
    hostgit.configure(posture=SEALED, ceiling=ceiling)
    config.resolve_project_root.cache_clear()
    return ceiling


@pytest.fixture
def rec(monkeypatch):
    recorder = SealedRecorder()
    monkeypatch.setattr(hostgit, "_spawn", recorder)
    return recorder


# ---- the registry ---------------------------------------------------------------


def test_declare_stores_a_declaration_keyed_by_the_real_path(home):
    repo = fake_repo(home / "r")
    link = home / "link"
    link.symlink_to(repo, target_is_directory=True)
    got = hostgit.declare(link, slug="acme-widget", origin_url=URL)
    assert got == hostgit.Declaration(root=str(repo.resolve()),
                                      slug="acme-widget", origin_url=URL)
    assert hostgit.declared(repo) == got
    assert hostgit.declared(link) == got
    assert hostgit.declared(home / "other") is None


def test_the_same_values_are_a_no_op_and_different_values_raise(home):
    repo = fake_repo(home / "r")
    first = hostgit.declare(repo, slug="a", origin_url=URL)
    assert hostgit.declare(repo, slug="a", origin_url=URL) == first
    for other in ({"slug": "b", "origin_url": URL},
                  {"slug": "a", "origin_url": URL + "x"},
                  {"slug": "a", "origin_url": None}):
        with pytest.raises(ValueError, match="already declared"):
            hostgit.declare(repo, **other)
    assert hostgit.declared(repo) == first


@pytest.mark.parametrize("slug", ["", "a/b", "a b", "../x", "a.b", None, 3])
def test_a_slug_that_is_not_a_bucket_name_is_refused(home, slug):
    with pytest.raises(ValueError, match="slug"):
        hostgit.declare(home / "r", slug=slug)


def test_an_origin_url_that_is_not_a_string_is_refused(home):
    with pytest.raises(ValueError, match="origin_url"):
        hostgit.declare(home / "r", slug="a", origin_url=3)  # type: ignore[arg-type]


def test_declare_under_ambient_raises_and_stores_nothing(tmp_path):
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    with pytest.raises(RuntimeError, match="SEALED"):
        hostgit.declare(tmp_path, slug="a")
    hostgit.configure(posture=SEALED, ceiling=tmp_path.parent)
    assert hostgit.declared(tmp_path) is None  # nothing was stored


def test_reset_clears_declarations(home):
    repo = fake_repo(home / "r")
    hostgit.declare(repo, slug="a")
    hostgit._reset()
    hostgit.configure(posture=SEALED, ceiling=home)
    assert hostgit.declared(repo) is None


def test_ambient_ignores_a_declaration_made_earlier(home):
    repo = fake_repo(home / "r")
    hostgit.declare(repo, slug="decl", origin_url=URL)
    assert hostgit.declared_slug(repo) == "decl"
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    assert hostgit.declared_slug(repo) is None
    assert store.project_slug(str(repo)) != "decl"


# ---- origin_url and project ------------------------------------------------------------


def test_a_declared_origin_url_is_answered_without_a_spawn(home, rec):
    repo = fake_repo(home / "r", url="https://example.com/real.git")
    hostgit.declare(repo, slug="a", origin_url=URL)
    assert isinstance(hostgit.project(repo), hostgit.Checkout)  # gate memoized
    rec.listings.clear()
    assert hostgit.origin_url(repo) == URL
    assert hostgit.origin_url(repo / "sub") is None  # a dir that does not exist
    assert rec.calls == [] and rec.listings == []


def test_a_declaration_without_a_url_leaves_origin_to_the_config(home, rec):
    repo = fake_repo(home / "r")
    hostgit.declare(repo, slug="a")
    rec.answer("https://example.com/real.git\n")
    assert hostgit.origin_url(repo) == "https://example.com/real.git"
    assert len(rec.calls) == 1


def test_a_path_under_a_declared_root_resolves_to_it_not_to_a_nested_repo(home):
    outer = fake_repo(home / "outer")
    nested = fake_repo(outer / "pkg" / "inner")
    sub = outer / "pkg" / "deep"
    sub.mkdir(parents=True)
    # undeclared: the nearest repository wins, as today
    assert hostgit.project(nested / ".").root == str(nested.resolve())
    hostgit.declare(outer, slug="a")
    got = hostgit.project(sub)
    assert got == hostgit.Checkout(root=str(outer.resolve()),
                                   gitdir=str(outer.resolve() / ".git"))
    assert hostgit.project(nested).root == str(outer.resolve())
    # a path outside every declared root behaves as before
    other = fake_repo(home / "other")
    assert hostgit.project(other).root == str(other.resolve())


def test_the_longest_declared_root_covering_a_path_wins(home):
    outer = fake_repo(home / "outer")
    inner = fake_repo(outer / "inner")
    hostgit.declare(outer, slug="o")
    hostgit.declare(inner, slug="i")
    deep = inner / "x"
    deep.mkdir()
    assert hostgit.project(deep).root == str(inner.resolve())


@needs_git
def test_a_declaration_does_not_bypass_the_config_gate(home, spy):  # noqa: F811
    repo = fake_repo(home / "r")
    cfg = repo / ".git" / "config"
    cfg.write_text(cfg.read_text(encoding="utf-8")
                   + '[filter "x"]\n\tclean = /bin/evil\n', encoding="utf-8")
    hostgit.declare(repo, slug="a", origin_url=URL)
    assert hostgit.project(repo) == hostgit.Refused("config-key:filter.x.clean")
    assert hostgit.origin_url(repo) is None  # the URL is data, not a pass


def test_a_declared_root_with_a_dot_git_file_is_refused_for_writes(home, rec):
    victim = fake_repo(home / "victim")
    root = home / "p"
    root.mkdir()
    (root / ".git").write_text(f"gitdir: {victim}/.git\n", encoding="utf-8")
    hostgit.declare(root, slug="a", origin_url=URL)
    assert hostgit.project(root) == hostgit.Refused("gitfile")
    assert hostgit.origin_url(root) is None
    with pytest.raises(config.ProjectWriteRefused):
        config.resolve_project_dir_for_write(str(root))


def test_a_declared_root_with_no_git_entry_is_refused(home, rec):
    root = home / "bare"
    root.mkdir()
    hostgit.declare(root, slug="a")
    assert hostgit.project(root) == hostgit.Refused("no-git-below-ceiling")


# ---- the slug seam ---------------------------------------------------------------------------


def test_the_declared_slug_names_the_bucket_for_a_declared_root(home, rec):
    repo = fake_repo(home / "r")
    sub = repo / "sub"
    sub.mkdir()
    undeclared = store.project_bucket(str(repo))
    assert undeclared == store.project_slug(str(repo.resolve()))
    hostgit.declare(repo, slug="acme-widget", origin_url=URL)
    config.resolve_project_root.cache_clear()
    assert store.project_slug(str(repo.resolve())) == "acme-widget"
    assert store.project_bucket(str(repo)) == "acme-widget"
    assert store.project_bucket(str(sub)) == "acme-widget"  # resolves to the root
    assert store.project_slug("acme-widget") == "acme-widget"  # idempotent


def test_a_write_for_a_declared_root_lands_in_the_declared_bucket(home, rec):
    repo = fake_repo(home / "r")
    hostgit.declare(repo, slug="acme-widget")
    config.resolve_project_root.cache_clear()
    path = config.resolve_project_dir_for_write(str(repo))
    store.record_bucket_root(path)  # the call every bucket-creating write makes
    buckets = {p.name for p in config.checkpoint_dir().iterdir() if p.is_dir()}
    assert "acme-widget" in buckets
    assert not any(name.endswith("-r") for name in buckets)  # no path-derived one
    root_file = config.checkpoint_dir() / "acme-widget" / "root"
    assert root_file.read_text(encoding="utf-8").strip() == str(repo.resolve())


def test_an_undeclared_root_keeps_todays_slug(home, rec):
    declared = fake_repo(home / "d")
    plain = fake_repo(home / "p")
    hostgit.declare(declared, slug="decl")
    config.resolve_project_root.cache_clear()
    assert store.project_slug(str(plain.resolve())) == "".join(
        c if c.isalnum() or c in "_-" else "-" for c in str(plain.resolve()))
    # a relative or non-path value is never mistaken for a declared root
    assert store.project_slug("decl") == "decl"
    assert store.project_slug("d") == "d"


# ---- api ---------------------------------------------------------------------------------------


def test_api_declare_project_is_the_sealed_only_declare(home):
    assert api.declare_project is hostgit.declare
    assert "declare_project" in api.__all__
    assert "SEALED" in hostgit.declare.__doc__
    repo = fake_repo(home / "r")
    api.declare_project(repo, slug="s", origin_url=URL)
    assert hostgit.declared(repo).slug == "s"


def test_api_declare_project_under_ambient_raises(tmp_path):
    hostgit.configure(posture=hostgit.Posture.AMBIENT)
    with pytest.raises(RuntimeError):
        api.declare_project(tmp_path, slug="s")
