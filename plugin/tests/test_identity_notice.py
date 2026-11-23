"""Slice 4b: the unbound notice.

Strictness means a project that moved looks, to daimon, like a project it has
never seen. The notice is how that shows: one fixed line, fired by exactly one
condition (this location is unbound, no bucket records it), carrying nothing
that depends on the store or on the repository, off under tenant scope and
SEALED, and asking the USER to run the survey, never telling the agent to run
a command that prints other projects.
"""

import json

import pytest

from daimon_briefing import cli, hostgit, identity, store

from tests.test_hostgit_sealed import SEALED

TEXT = ("No memory is bound to this folder yet. If this project moved from "
        "another folder or machine, ask the user to run "
        "`daimon bucket doctor --path .` themselves.")


@pytest.fixture
def folder(tmp_path):
    p = tmp_path / "work" / "zz-secret-dir"
    p.mkdir(parents=True)
    return str(p.resolve())


def _brief(capsys, folder):
    cli.main(["brief", "--project", folder])
    return capsys.readouterr().out


def test_the_notice_text_is_fixed_and_has_no_repo_or_store_content():
    assert identity.UNBOUND_NOTICE == TEXT
    assert "—" not in TEXT
    assert "ask the user" in TEXT


def test_an_unbound_folder_gets_the_notice_in_its_brief(capsys, folder):
    assert TEXT in _brief(capsys, folder)


def test_a_bound_folder_does_not(capsys, folder, tmp_checkpoint_dir):
    identity.claim(folder)
    assert TEXT not in _brief(capsys, folder)


def test_a_legacy_unstamped_bucket_counts_as_bound(
        capsys, folder, tmp_checkpoint_dir):
    (tmp_checkpoint_dir / store.location_slug(folder)).mkdir(parents=True)
    assert TEXT not in _brief(capsys, folder)


def test_the_notice_names_nothing_the_store_holds(
        capsys, folder, tmp_checkpoint_dir):
    """The same line whatever else is in the store: no bucket name, no project
    name, no count."""
    empty = _brief(capsys, folder)
    for name in ("-secret-client-acme", "-another-project"):
        (tmp_checkpoint_dir / name).mkdir(parents=True)
        (tmp_checkpoint_dir / name / "root").write_text(
            "/gone/" + name + "\n", encoding="utf-8")
    crowded = _brief(capsys, folder)
    assert crowded == empty
    assert "secret-client" not in crowded


def test_the_notice_never_names_the_folder_or_a_repo(capsys, folder):
    out = _brief(capsys, folder)
    line = next(ln for ln in out.splitlines() if "No memory is bound" in ln)
    assert folder not in line and "zz-secret-dir" not in line


def test_tenant_scope_has_no_notice(capsys, folder, monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert TEXT not in _brief(capsys, folder)


def test_sealed_has_no_notice(capsys, tmp_path, tmp_checkpoint_dir):
    home = tmp_path / "home"
    (home / "r").mkdir(parents=True)
    hostgit.configure(posture=SEALED, ceiling=home)
    try:
        assert TEXT not in _brief(capsys, str((home / "r").resolve()))
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


def test_a_slug_routed_brief_has_no_notice(capsys, tmp_checkpoint_dir):
    (tmp_checkpoint_dir / "-some-bucket").mkdir(parents=True)
    cli.main(["brief", "--slug=-some-bucket"])
    assert TEXT not in capsys.readouterr().out


def test_the_notice_is_the_only_trigger_not_dangling_buckets(
        capsys, folder, tmp_checkpoint_dir):
    """A store full of dangling and unstamped buckets does not make a BOUND
    folder show it, and does not need to for an unbound one."""
    (tmp_checkpoint_dir / "-dangling").mkdir(parents=True)
    (tmp_checkpoint_dir / "-dangling" / "root").write_text(
        "/gone/forever\n", encoding="utf-8")
    (tmp_checkpoint_dir / "-unstamped").mkdir()
    identity.claim(folder)
    assert TEXT not in _brief(capsys, folder)


def test_status_says_it_too(capsys, folder, tmp_checkpoint_dir):
    cli.main(["status", "--project", folder, "--json"])
    health = json.loads(capsys.readouterr().out)
    assert any(TEXT in w for w in health["health"]["warnings"])


def test_status_is_quiet_for_a_bound_folder(capsys, folder, tmp_checkpoint_dir):
    identity.claim(folder)
    cli.main(["status", "--project", folder, "--json"])
    health = json.loads(capsys.readouterr().out)
    assert not any("No memory is bound" in w
                   for w in health["health"]["warnings"])


def test_rendering_the_notice_creates_nothing(
        capsys, folder, tmp_checkpoint_dir):
    """Asking is a read: an unbound brief makes no bucket."""
    _brief(capsys, folder)
    assert not any(p.name != ".birth" for p in tmp_checkpoint_dir.iterdir()
                   ) if tmp_checkpoint_dir.exists() else True


def test_unbound_is_exactly_the_resolvers_answer(folder, tmp_checkpoint_dir):
    assert identity.is_unbound(folder) is True
    identity.claim(folder)
    assert identity.is_unbound(folder) is False
    assert identity.is_unbound("-a-slug") is False
    assert identity.is_unbound(None) is False
