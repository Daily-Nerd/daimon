"""Slice 5 of project identity: a session in a LINKED git worktree uses its
main repository's bucket (#1384, D3).

The authority is the main repository's OWN worktree list, never the `.git`
file a checkout carries: that file only chooses which repository git is asked
about, and the answer must list this exact real path. Real `git init` and
`git worktree add` in tmp_path throughout.
"""

import os
import shutil
import subprocess

import pytest

from daimon_briefing import (bucket_tools, buckets, cli, config, hostgit,
                             identity, store)
from daimon_briefing.store import Writer

from tests.test_bucket_stamp_tool import _git, _repo
from tests.test_hostgit_sealed import SEALED


@pytest.fixture(autouse=True)
def _fresh_root_memo():
    config.resolve_project_root.cache_clear()
    yield
    config.resolve_project_root.cache_clear()


@pytest.fixture
def pair(tmp_path):
    """(main, linked worktree), both real paths."""
    main = _repo(tmp_path / "main")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    return str(main.resolve()), str(wt.resolve())


def _write(project, session="S1", created="2030-01-01T00:00:00Z"):
    cp = {"session_id": session, "created": created, "working_context": {
        "active_topic": {"text": "t", "trust": "inferred"}}}
    return store.write_checkpoint(session, cp, project_dir=project,
                                  writer=Writer.HUMAN)


def _no_fork(monkeypatch):
    calls = []
    real = subprocess.run

    def spy(*a, **k):
        calls.append(a[0] if a else k.get("args"))
        return real(*a, **k)

    monkeypatch.setattr(hostgit.subprocess, "run", spy)
    return calls


# ---- hostgit.linked_main: the exact checks ----------------------------------------


def test_linked_main_names_the_main_of_a_linked_worktree(pair):
    main, wt = pair
    assert hostgit.linked_main(wt) == main


def test_linked_main_is_none_for_the_main_repo_and_forks_nothing(
        pair, monkeypatch):
    main, _ = pair
    calls = _no_fork(monkeypatch)
    assert hostgit.linked_main(main) is None
    assert calls == []


def test_linked_main_is_none_for_a_plain_folder_and_a_missing_one(tmp_path):
    (tmp_path / "plain").mkdir()
    assert hostgit.linked_main(str(tmp_path / "plain")) is None
    assert hostgit.linked_main(str(tmp_path / "missing")) is None


def test_a_git_symlink_never_counts(pair, tmp_path):
    main, wt = pair
    link = tmp_path / "linked"
    link.mkdir()
    (link / ".git").symlink_to(os.path.join(wt, ".git"))
    assert hostgit.linked_main(str(link)) is None


def test_a_spoofed_gitfile_naming_a_victims_admin_dir_is_not_a_worktree(
        tmp_path, pair):
    victim, victim_wt = pair
    admin = os.path.join(victim, ".git", "worktrees", os.path.basename(victim_wt))
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / ".git").write_text(f"gitdir: {admin}\n")
    assert hostgit.linked_main(str(evil.resolve())) is None


def test_a_locked_worktree_reason_cannot_forge_a_list_entry(tmp_path, pair):
    """`-z` keeps one field per attribute: a newline inside a lock reason
    stays inside it and never starts a record for a path of the attacker's
    choosing."""
    main, wt = pair
    evil = tmp_path / "evil"
    evil.mkdir()
    _git(main, "worktree", "lock", "--reason",
         f"x\nworktree {evil.resolve()}\nHEAD abc", wt)
    (evil / ".git").write_text(
        f"gitdir: {main}/.git/worktrees/{os.path.basename(wt)}\n")
    assert hostgit.linked_main(str(evil.resolve())) is None
    assert hostgit.linked_main(wt) == main


def test_a_newline_in_the_path_stays_one_record(tmp_path):
    main = _repo(tmp_path / "main")
    odd = tmp_path / "a\nb"
    _git(main, "worktree", "add", "-q", "-b", "odd", str(odd))
    assert hostgit.linked_main(str(odd.resolve())) == str(main.resolve())


def test_a_submodule_is_not_a_linked_worktree(tmp_path):
    inner = _repo(tmp_path / "inner")
    outer = _repo(tmp_path / "outer")
    subprocess.run(
        ["git", "-C", str(outer), "-c", "protocol.file.allow=always",
         "-c", "user.name=t", "-c", "user.email=t@x", "submodule", "add",
         "-q", str(inner), "sub"], check=True, capture_output=True)
    sub = outer / "sub"
    assert hostgit.has_gitfile(str(sub))
    assert hostgit.linked_main(str(sub.resolve())) is None


def test_a_worktree_of_a_bare_repo_is_not_bound(tmp_path):
    source = _repo(tmp_path / "src")
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(source), str(bare)],
                   check=True, capture_output=True)
    wt = tmp_path / "bwt"
    _git(bare, "worktree", "add", "-q", str(wt), "HEAD")
    assert hostgit.linked_main(str(wt.resolve())) is None


def test_git_missing_or_failing_means_none(pair, monkeypatch):
    _, wt = pair
    monkeypatch.setattr(hostgit, "_read", lambda *a, **k: None)
    hostgit.linked_main.cache_clear()
    assert hostgit.linked_main(wt) is None


def test_an_unusable_listing_means_none(pair, monkeypatch):
    """Empty output, a bare main with no linked entries, a main that is gone."""
    main, wt = pair
    for out in ("", "worktree /nowhere\0\0", f"worktree {main}\0bare\0\0"):
        hostgit.linked_main.cache_clear()
        monkeypatch.setattr(hostgit, "_read", lambda *a, _o=out, **k: _o)
        assert hostgit.linked_main(wt) is None
    hostgit.linked_main.cache_clear()
    monkeypatch.setattr(hostgit, "_read", lambda *a, **k: (
        f"worktree {main}\0HEAD x\0\0worktree {wt}\0HEAD y\0\0worktree {wt}"
        f"\0\0"))
    assert hostgit.linked_main(wt) == main
    # the same list but the first entry is the path itself: not linked
    hostgit.linked_main.cache_clear()
    monkeypatch.setattr(hostgit, "_read", lambda *a, **k: (
        f"worktree {wt}\0\0worktree {wt}\0\0"))
    assert hostgit.linked_main(wt) is None


def test_sealed_never_asks(pair, monkeypatch):
    _, wt = pair
    calls = _no_fork(monkeypatch)
    hostgit.configure(posture=SEALED, ceiling=os.path.dirname(wt))
    try:
        assert hostgit.linked_main(wt) is None
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)
    assert calls == []


def test_the_answer_is_memoized_and_cleared_by_the_root_memo_clear(
        pair, monkeypatch):
    _, wt = pair
    calls = _no_fork(monkeypatch)
    hostgit.linked_main(wt)
    hostgit.linked_main(wt)
    assert len(calls) == 1
    config.resolve_project_root.cache_clear()
    hostgit.linked_main(wt)
    assert len(calls) == 2


def test_a_stale_list_entry_is_a_documented_residual(tmp_path, pair):
    """Whoever can place a directory at a path the main repository still
    lists inherits the binding: the worktree was moved, its old entry stays
    until `git worktree prune`. Documented, not mitigated."""
    main, wt = pair
    moved = tmp_path / "moved"
    shutil.move(wt, moved)
    squat = tmp_path / "wt"
    squat.mkdir()
    shutil.copy(moved / ".git", squat / ".git")
    assert hostgit.linked_main(str(squat.resolve())) == main


# ---- the resolver and the claim ---------------------------------------------------


def test_a_worktree_and_its_main_resolve_to_one_bucket_before_any_write(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    assert identity.resolve(wt) == identity.resolve(main)
    assert store.project_bucket(wt) == store.project_bucket(main)


def test_a_worktree_writes_into_the_main_repos_bucket(pair, tmp_checkpoint_dir):
    main, wt = pair
    assert _write(wt)
    bucket = store.project_bucket(main)
    assert (tmp_checkpoint_dir / bucket / "root").read_text().strip() == main
    assert [p.name for p in tmp_checkpoint_dir.iterdir()
            if p.is_dir() and p.name.startswith("-")] == [bucket]
    assert store.bucket_of(wt) == bucket
    assert store.sessions_since_count("2020-01-01T00:00:00Z",
                                      project_dir=main) == 1


def test_the_ref_keeps_the_physical_worktree_as_its_location(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    ref = identity.claim(wt)
    assert str(ref) == wt
    assert ref.slug == store.project_bucket(main)
    assert ref.home == main
    identity.verify(ref)  # the bucket records main, which is the ref's home


def test_main_first_or_worktree_first_is_one_bucket(pair, tmp_checkpoint_dir):
    main, wt = pair
    first = identity.claim(main).slug
    assert identity.claim(wt).slug == first
    assert len([p for p in tmp_checkpoint_dir.iterdir()
                if p.name.startswith("-")]) == 1


def test_the_project_name_is_the_main_repos_not_the_worktrees(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    _write(wt)
    flat = [p for p in tmp_checkpoint_dir.glob("S1.json")]
    assert flat
    assert '"project_name": "main"' in flat[0].read_text()


def test_a_worktree_deleted_before_a_late_claim_keeps_its_own_bucket(
        pair, tmp_checkpoint_dir):
    """Documented residual: with the worktree gone nothing can say it was
    linked, so a late heal binds the path's own (ghost) bucket, a merge
    candidate for `bucket doctor`."""
    main, wt = pair
    shutil.rmtree(wt)
    config.resolve_project_root.cache_clear()
    assert identity.resolve(wt).slug == store.location_slug(wt)


def test_tenant_scope_never_forks_for_a_worktree(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, wt = pair
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    calls = _no_fork(monkeypatch)
    assert identity.resolve(wt).slug == store.location_slug(wt)
    assert calls == []


def test_sealed_never_forks_and_keeps_its_own_bucket(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, wt = pair
    calls = _no_fork(monkeypatch)
    hostgit.configure(posture=SEALED, ceiling=os.path.dirname(wt))
    try:
        assert identity.resolve(wt).slug == store.location_slug(wt)
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)
    assert calls == []


def test_a_normal_repo_resolves_without_a_git_fork(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, _ = pair
    calls = _no_fork(monkeypatch)
    identity.resolve(main)
    assert calls == []


def test_a_worktree_still_reaches_declare_under_sealed(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    hostgit.configure(posture=SEALED, ceiling=os.path.dirname(wt))
    try:
        hostgit.declare(wt, slug="acme-wt")
        assert identity.resolve(wt) == identity.Resolution("acme-wt", True)
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


def test_layer_scopes_of_an_outside_worktree_walk_from_the_main(
        tmp_path, monkeypatch, tmp_checkpoint_dir):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    main = _repo(home / "work" / "proj")
    wt = home / "elsewhere" / "wt"
    wt.parent.mkdir(parents=True)
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    layer = (home / "work").resolve()
    bucket = tmp_checkpoint_dir / store.location_slug(str(layer))
    bucket.mkdir(parents=True)
    (bucket / "root").write_text(f"{layer}\n")
    assert config.layer_scopes(str(main)) == [str(layer)]
    assert config.layer_scopes(str(wt)) == [str(layer)]


# ---- notices ----------------------------------------------------------------------


def test_a_worktree_bound_to_its_main_gets_no_unbound_notice(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    identity.claim(main)
    assert identity.is_unbound(wt) is False
    assert identity.has_split_bucket(wt) is False


def test_a_worktree_of_an_unbound_main_gets_the_unbound_notice(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    assert identity.is_unbound(main) is True
    assert identity.is_unbound(wt) is True


def test_a_worktree_with_its_own_earlier_bucket_gets_the_second_notice(
        pair, tmp_checkpoint_dir, capsys):
    main, wt = pair
    own = tmp_checkpoint_dir / store.location_slug(wt)
    own.mkdir(parents=True)
    (own / "root").write_text(f"{wt}\n")
    (own / "events.jsonl").write_text("{}\n")
    identity.claim(main)
    assert identity.has_split_bucket(wt) is True
    cli.main(["brief", "--project", wt])
    out = capsys.readouterr().out
    assert identity.SPLIT_NOTICE in out
    assert identity.UNBOUND_NOTICE not in out
    assert store.location_slug(wt) not in out
    assert "bucket doctor" in identity.SPLIT_NOTICE
    assert "—" not in identity.SPLIT_NOTICE


def test_the_second_notice_is_off_for_a_plain_folder_and_the_main(
        pair, tmp_checkpoint_dir, tmp_path):
    main, _ = pair
    identity.claim(main)
    assert identity.has_split_bucket(main) is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert identity.has_split_bucket(str(plain.resolve())) is False
    assert identity.has_split_bucket("") is False
    assert identity.has_split_bucket("relative") is False


def test_the_second_notice_is_off_under_tenant_scope(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, wt = pair
    own = tmp_checkpoint_dir / store.location_slug(wt)
    own.mkdir(parents=True)
    (own / "root").write_text(f"{wt}\n")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert identity.has_split_bucket(wt) is False


def test_status_carries_the_second_notice(
        pair, tmp_checkpoint_dir, capsys):
    main, wt = pair
    own = tmp_checkpoint_dir / store.location_slug(wt)
    own.mkdir(parents=True)
    (own / "root").write_text(f"{wt}\n")
    identity.claim(main)
    cli.main(["status", "--project", wt])
    assert identity.SPLIT_NOTICE in capsys.readouterr().out


# ---- doctor and merge -------------------------------------------------------------


def _fold_fixture(pair, tmp_checkpoint_dir):
    main, wt = pair
    own = tmp_checkpoint_dir / store.location_slug(wt)
    own.mkdir(parents=True)
    (own / "root").write_text(f"{wt}\n")
    (own / "events.jsonl").write_text("{}\n")
    b = identity.claim(main).slug
    return main, wt, b, own.name


def test_doctor_suggests_folding_a_live_worktree_bucket_into_its_main(
        pair, tmp_checkpoint_dir):
    main, wt, b, g = _fold_fixture(pair, tmp_checkpoint_dir)
    survey = bucket_tools.doctor()
    assert g in survey["findings"]["worktree"]
    assert survey["suggestions"] == [{"from": g, "into": b}]


def test_doctor_does_not_suggest_a_fold_for_a_look_alike_that_is_not_linked(
        tmp_path, tmp_checkpoint_dir):
    """A live folder under `.claude/worktrees` that no repository lists would
    be born again by its own session: still no suggestion."""
    live = tmp_path / "repo" / ".claude" / "worktrees" / "a1"
    live.mkdir(parents=True)
    for name in ("-a", "-b"):
        d = tmp_checkpoint_dir / name
        d.mkdir(parents=True)
        (d / "root").write_text(f"{live}\n")
        (d / "events.jsonl").write_text("{}\n")
    survey = bucket_tools.doctor()
    assert survey["suggestions"] == []
    assert survey["suggestions_withheld"] == "worktree"


def test_merge_folds_a_live_worktree_bucket_without_force(
        pair, tmp_checkpoint_dir):
    main, wt, b, g = _fold_fixture(pair, tmp_checkpoint_dir)
    buckets.merge(g, b, apply=True)
    assert not (tmp_checkpoint_dir / g).exists()
    # the worktree session now lands in the main's bucket and G is not reborn
    assert identity.claim(wt).slug == b
    assert not (tmp_checkpoint_dir / g).exists()


def test_merge_still_refuses_a_live_root_that_would_birth_the_bucket_again(
        tmp_path, tmp_checkpoint_dir):
    live = tmp_path / "live"
    live.mkdir()
    g = tmp_checkpoint_dir / "-g"
    g.mkdir(parents=True)
    (g / "root").write_text(f"{live}\n")
    b = tmp_checkpoint_dir / "-b"
    b.mkdir()
    with pytest.raises(buckets.MigrationError, match="still exists"):
        buckets.merge("-g", "-b", apply=True)


def test_merge_refuses_when_the_worktree_binds_to_another_bucket(
        pair, tmp_checkpoint_dir):
    main, wt, b, g = _fold_fixture(pair, tmp_checkpoint_dir)
    other = tmp_checkpoint_dir / "-other"
    other.mkdir()
    with pytest.raises(buckets.MigrationError, match="still exists"):
        buckets.merge(g, "-other", apply=True)


# ---- edges ------------------------------------------------------------------------


def test_a_nul_or_a_bad_posture_setting_is_not_a_worktree(pair, monkeypatch):
    _, wt = pair
    assert hostgit.linked_main("/a\0b") is None
    assert config.worktree_main("/a\0b") is None
    monkeypatch.setenv("DAIMON_GIT_POSTURE", "nonsense")
    assert hostgit.linked_main(wt) is None
    assert config.worktree_main(wt) is None


def test_worktree_binds_to_answers_only_for_a_live_linked_worktree(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, wt = pair
    b = identity.claim(main).slug
    assert identity.worktree_binds_to(wt, b) is True
    assert identity.worktree_binds_to(wt, "-other") is False
    assert identity.worktree_binds_to(main, b) is False
    for odd in ("", None, "relative", "/a\0b"):
        assert identity.worktree_binds_to(odd, b) is False
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert identity.worktree_binds_to(wt, b) is False


def test_the_split_notice_needs_a_readable_store(
        pair, tmp_checkpoint_dir, monkeypatch):
    main, wt = pair
    monkeypatch.setattr(identity, "_store_root", lambda: None)
    assert identity.has_split_bucket(wt) is False


def test_a_failing_split_check_never_costs_the_brief(
        pair, tmp_checkpoint_dir, monkeypatch, capsys):
    main, wt = pair

    def boom(project):
        raise RuntimeError("no")

    monkeypatch.setattr(identity, "has_split_bucket", boom)
    assert cli.main(["brief", "--project", wt]) == 0
    assert identity.SPLIT_NOTICE not in capsys.readouterr().out


def test_doctor_suggests_nothing_while_the_main_has_no_bucket(
        pair, tmp_checkpoint_dir):
    """The worktree's bucket cannot fold into a bucket that does not exist."""
    main, wt = pair
    own = tmp_checkpoint_dir / store.location_slug(wt)
    own.mkdir(parents=True)
    (own / "root").write_text(f"{wt}\n")
    survey = bucket_tools.doctor()
    assert own.name in survey["findings"]["worktree"]
    assert survey["suggestions"] == []


def test_doctor_folds_the_linked_worktree_and_withholds_the_look_alike(
        pair, tmp_path, tmp_checkpoint_dir):
    main, wt = pair
    b = identity.claim(main).slug
    fake = tmp_path / "repo" / ".claude" / "worktrees" / "x"
    fake.mkdir(parents=True)
    for name, root in (("-real", wt), ("-fake-a", str(fake)),
                       ("-fake-b", str(fake))):
        d = tmp_checkpoint_dir / name
        d.mkdir(parents=True)
        (d / "root").write_text(f"{root}\n")
        (d / "events.jsonl").write_text("{}\n")
    survey = bucket_tools.doctor()
    assert survey["suggestions"] == [{"from": "-real", "into": b}]
    assert survey["suggestions_withheld"] == "worktree"


def test_home_of_leaves_anything_but_an_absolute_folder_alone(
        pair, monkeypatch):
    main, wt = pair
    monkeypatch.chdir(wt)
    assert identity.home_of(".") == "."
    assert identity.home_of("some-slug") == "some-slug"
    assert identity.home_of(wt) == main
    assert identity.home_of(main) == main


def test_an_undecidable_tenant_setting_is_the_narrow_answer(pair, monkeypatch):
    _, wt = pair

    def boom():
        raise ValueError("unreadable env file")

    monkeypatch.setattr(config, "tenant_scoped", boom)
    assert config.worktree_main(wt) is None


# ---- review fixes: memo keyed on what the answer depends on ---------------------------


def test_a_path_re_added_from_another_repo_answers_the_new_main(tmp_path):
    a = _repo(tmp_path / "a")
    b = _repo(tmp_path / "b")
    wt = tmp_path / "wt"
    _git(a, "worktree", "add", "-q", "-b", "x", str(wt))
    real = str(wt.resolve())
    assert hostgit.linked_main(real) == str(a.resolve())
    _git(a, "worktree", "remove", "--force", str(wt))
    _git(b, "worktree", "add", "-q", "-b", "y", str(wt))
    assert hostgit.linked_main(real) == str(b.resolve())


def test_a_failed_first_ask_is_not_remembered(pair, monkeypatch):
    main, wt = pair
    real_read = hostgit._read
    monkeypatch.setattr(hostgit, "_read", lambda *a, **k: None)
    assert hostgit.linked_main(wt) is None
    monkeypatch.setattr(hostgit, "_read", real_read)
    assert hostgit.linked_main(wt) == main


def test_a_pruned_entry_is_noticed_without_a_cache_clear(tmp_path):
    main = _repo(tmp_path / "main")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "x", str(wt))
    real = str(wt.resolve())
    assert hostgit.linked_main(real) == str(main.resolve())
    shutil.rmtree(main / ".git" / "worktrees")
    assert hostgit.linked_main(real) is None


def test_the_memo_forks_once_for_an_unchanged_worktree(pair, monkeypatch):
    _, wt = pair
    calls = _no_fork(monkeypatch)
    for _ in range(5):
        hostgit.linked_main(wt)
    assert len(calls) == 1


def test_a_separate_git_dir_store_is_not_a_main(tmp_path):
    store_dir = tmp_path / "gstore"
    sep = tmp_path / "sep"
    sep.mkdir()
    subprocess.run(["git", "init", "-q", "--separate-git-dir", str(store_dir),
                    str(sep)], check=True, capture_output=True)
    (sep / "f").write_text("x")
    _git(sep, "add", "f")
    _git(sep, "commit", "-q", "-m", "i")
    wt = tmp_path / "sepwt"
    _git(sep, "worktree", "add", "-q", "-b", "x", str(wt))
    assert hostgit.linked_main(str(wt.resolve())) is None


def test_the_listing_is_compared_the_way_roots_are(pair, monkeypatch):
    main, wt = pair
    monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())
    shouted = wt.upper()
    hostgit.linked_main.cache_clear()
    monkeypatch.setattr(hostgit, "_read", lambda *a, **k: (
        f"worktree {main}\0\0worktree {shouted}\0\0"))
    assert hostgit.linked_main(wt) == main


def test_adopt_refuses_a_worktree_path_and_names_the_main(
        pair, tmp_checkpoint_dir):
    main, wt = pair
    own = tmp_checkpoint_dir / "-old"
    own.mkdir(parents=True)
    (own / "root").write_text("/gone/old\n")
    with pytest.raises(bucket_tools.RebindRefused) as err:
        bucket_tools.adopt("-old", wt, apply=True)
    assert main in str(err.value)
    assert (own / "root").read_text().strip() == "/gone/old"


def test_session_root_of_a_worktree_is_its_main(pair):
    main, wt = pair
    assert identity.session_root(wt) == main
    assert identity.session_root(main) == main
