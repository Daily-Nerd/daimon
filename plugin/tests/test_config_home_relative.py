"""`config.home_relative` (#1093): the `~`-prefixed display form of an
absolute path at or below `Path.home()`, used to name the owning layer in an
inherited ruling's render suffix (`[from ~/work]`). Added alongside
`layer_scopes` (#1092) since both read `Path.home()` the same way; this one
never raises and never walks a filesystem beyond `Path.home()` and its own
argument.
"""

from daimon_briefing import config


def test_home_relative_renders_the_tilde_form(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_home))

    assert config.home_relative(str(work)) == "~/work"


def test_home_relative_of_home_itself_is_bare_tilde(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))

    assert config.home_relative(str(tmp_home)) == "~"


def test_home_relative_outside_home_is_unchanged(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    tmp_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_home))
    outside = tmp_path / "elsewhere" / "proj"
    outside.mkdir(parents=True)

    assert config.home_relative(str(outside)) == str(outside)


def test_home_relative_resolves_symlinks_on_both_sides(tmp_path, monkeypatch):
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    work.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_home))
    link = tmp_home / "work-link"
    link.symlink_to(work)

    assert config.home_relative(str(link)) == "~/work"


def test_home_relative_never_raises_when_home_lookup_fails(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(config.Path, "home", staticmethod(_boom))
    assert config.home_relative(str(tmp_path)) == str(tmp_path)


def test_home_relative_of_falsy_input_is_empty_string(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.home_relative(None) == ""
    assert config.home_relative("") == ""
