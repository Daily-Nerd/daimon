"""Fixes from the blind review of slice 4b.

M1: adopt, doctor and remap bind and print the folder a SESSION opens (one
shared derivation, `identity.session_root`), not whatever was typed. The minors
close the guards adopt had and remap lacked, the crash window in the rebind,
the concurrent-adopt race, and the edges of the resolver and the hook helper.
"""

import contextlib
import errno
import importlib.util

import pytest

from daimon_briefing import (bucket_tools, cli, config, hostgit, identity,
                             jsonl, store)
from daimon_briefing.cli import projects as cli_projects

from tests.test_bucket_merge import _snapshot
from tests.test_bucket_stamp_tool import _git, _repo
from tests.test_hostgit_sealed import SEALED
from tests.test_identity_resolver import _bucket
from tests.test_read_layers import PLUGIN


def _root(bucket):
    return (bucket / "root").read_text(encoding="utf-8").strip()


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)


@pytest.fixture(autouse=True)
def _fresh_root_memo():
    config.resolve_project_root.cache_clear()
    yield
    config.resolve_project_root.cache_clear()


# ---- M1: one derivation of "the folder a session opens" ----------------------------------


def test_session_root_is_the_git_toplevel_sessions_resolve_to(tmp_path):
    repo = _repo(tmp_path / "repo")
    (repo / "plugin").mkdir()
    assert identity.session_root(repo / "plugin") == str(repo.resolve())
    assert identity.session_root(repo) == str(repo.resolve())
    plain = tmp_path / "plain"
    plain.mkdir()
    assert identity.session_root(plain) == str(plain.resolve())


def test_adopt_refuses_a_subdirectory_and_names_the_folder_to_use(
        tmp_path, tmp_checkpoint_dir):
    repo = _repo(tmp_path / "repo")
    (repo / "plugin").mkdir()
    bucket = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused) as caught:
        bucket_tools.adopt("-old", str(repo / "plugin"), apply=True)
    assert str(repo.resolve()) in str(caught.value)
    assert _snapshot() == before
    bucket_tools.adopt("-old", str(repo), apply=True)
    assert _root(bucket) == str(repo.resolve())
    assert store.bucket_of(str(repo.resolve())) == "-old"


def test_doctor_path_looks_up_and_prints_the_session_folder(
        tmp_path, tmp_checkpoint_dir, tty, capsys):
    repo = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    (repo / "sub").mkdir()
    d = _bucket(tmp_checkpoint_dir, "-old-proj", "/Users/a/proj")
    bucket_tools.write_identity(d, "/Users/a/proj", {
        "origin": "https://example.com/o/r.git",
        "root_commit": _git(repo, "rev-list", "--max-parents=0", "HEAD"),
        "folder": "proj"})
    assert cli.main(["bucket", "doctor", "--path", str(repo / "sub")]) == 0
    out = capsys.readouterr().out
    assert "-old-proj" in out
    assert f"--path {repo.resolve()}" in out
    assert str(repo / "sub") not in out


def test_remap_skips_a_target_that_is_not_a_folder_a_session_opens(
        tmp_path, tmp_checkpoint_dir):
    new_repo = _repo(tmp_path / "bob" / "repo")
    (new_repo / "sub").mkdir()
    old = tmp_path / "alice"
    sub_bucket = _bucket(tmp_checkpoint_dir, "-sub", str(old / "repo" / "sub"))
    root_bucket = _bucket(tmp_checkpoint_dir, "-root", str(old / "repo"))
    plan = bucket_tools.remap(str(old), str(tmp_path / "bob"), apply=True)
    by = {a["bucket"]: a for a in plan["actions"]}
    assert by["-sub"]["action"] == "not-a-root"
    assert by["-sub"]["to"] == str(new_repo.resolve())
    assert by["-root"]["action"] == "rewrite"
    assert _root(sub_bucket) == str(old / "repo" / "sub")
    assert _root(root_bucket) == str(new_repo.resolve())


# ---- m1: remap guards a live source like adopt ------------------------------------------------


def _homes(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    (old / "p").mkdir(parents=True)
    (new / "p").mkdir(parents=True)
    return old, new


def test_remap_skips_a_bucket_whose_old_root_still_exists(
        tmp_path, tmp_checkpoint_dir):
    old, new = _homes(tmp_path)
    b = _bucket(tmp_checkpoint_dir, "-p", str(old.resolve() / "p"))
    plan = bucket_tools.remap(str(old), str(new), apply=True)
    assert [a["action"] for a in plan["actions"]] == ["live-source"]
    assert _root(b) == str(old.resolve() / "p")


def test_remap_force_moves_a_live_source(tmp_path, tmp_checkpoint_dir):
    old, new = _homes(tmp_path)
    b = _bucket(tmp_checkpoint_dir, "-p", str(old.resolve() / "p"))
    plan = bucket_tools.remap(str(old), str(new), apply=True, force=True)
    assert [a["action"] for a in plan["actions"]] == ["rewrite"]
    assert _root(b) == str(new.resolve() / "p")


def test_remap_cli_exits_1_for_a_live_source_and_force_flag_exists(
        tmp_path, tmp_checkpoint_dir, tty, capsys):
    old, new = _homes(tmp_path)
    _bucket(tmp_checkpoint_dir, "-p", str(old.resolve() / "p"))
    argv = ["bucket", "remap", f"--from={old}", f"--to={new}", "--apply"]
    assert cli.main(argv) == 1
    assert "live-source" in capsys.readouterr().out
    assert cli.main(argv + ["--force"]) == 0


# ---- m2: --from through a symlink ----------------------------------------------------------------


def test_remap_from_through_a_symlink_matches_the_real_roots(
        tmp_path, tmp_checkpoint_dir):
    old, new = _homes(tmp_path)
    link = tmp_path / "shortcut"
    link.symlink_to(old, target_is_directory=True)
    b = _bucket(tmp_checkpoint_dir, "-p", str(old.resolve() / "p"))
    plan = bucket_tools.remap(str(link), str(new), apply=True, force=True)
    assert [a["action"] for a in plan["actions"]] == ["rewrite"]
    assert _root(b) == str(new.resolve() / "p")


# ---- m3: a crash between the root and the identity record heals on rerun ----------------------------


def test_a_rerun_after_a_crash_between_root_and_identity_heals_the_record(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    new = tmp_path / "new"
    (new / "p").mkdir(parents=True)
    old = tmp_path / "gone"
    b = _bucket(tmp_checkpoint_dir, "-p", str(old / "p"))
    real = bucket_tools.write_identity
    calls = []

    def crash_once(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(errno.EIO, "crash")
        return real(*a, **k)

    monkeypatch.setattr(bucket_tools, "write_identity", crash_once)
    with pytest.raises(OSError):
        bucket_tools.remap(str(old), str(new), apply=True)
    # the first attempt left the store in a state a rerun still recognizes
    plan = bucket_tools.remap(str(old), str(new), apply=True)
    assert [a["action"] for a in plan["actions"]] == ["rewrite"]
    record = bucket_tools.read_identity(b)
    assert store.root_eq(record["root"], _root(b))
    assert record["history"] == [str(old / "p")]
    assert bucket_tools.doctor()["findings"]["identity_mismatch"] == []


# ---- m4: adopt will not orphan the folder's own legacy history ---------------------------------------


def test_adopt_refuses_to_orphan_an_unstamped_legacy_bucket_with_history(
        tmp_path, tmp_checkpoint_dir):
    new = tmp_path / "new"
    new.mkdir()
    real = str(new.resolve())
    legacy = _bucket(tmp_checkpoint_dir, store.location_slug(real))
    (legacy / "events.jsonl").write_text('{"event_id": "e1"}\n',
                                         encoding="utf-8")
    b = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    before = _snapshot()
    with pytest.raises(bucket_tools.RebindRefused,
                       match="bucket merge --from=" + legacy.name):
        bucket_tools.adopt("-old", str(new), apply=True)
    assert _snapshot() == before
    bucket_tools.adopt("-old", str(new), apply=True, force=True)
    assert _root(b) == real


def test_an_empty_unstamped_seed_bucket_does_not_block_adopt(
        tmp_path, tmp_checkpoint_dir):
    new = tmp_path / "new"
    new.mkdir()
    _bucket(tmp_checkpoint_dir, store.location_slug(str(new.resolve())))
    b = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    bucket_tools.adopt("-old", str(new), apply=True)
    assert _root(b) == str(new.resolve())


# ---- m5: two adopts onto one folder: the collision is re-checked under a store lock ----------------------


def test_a_competing_adopt_that_lands_first_wins_and_the_other_refuses(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    real = str(new.resolve())
    first = _bucket(tmp_checkpoint_dir, "-first", "/gone/first")
    second = _bucket(tmp_checkpoint_dir, "-second", "/gone/second")
    real_lock = bucket_tools._store_lock

    @contextlib.contextmanager
    def other_adopt_lands_before_we_hold_the_lock():
        (first / "root").write_text(real + "\n", encoding="utf-8")
        with real_lock():
            yield

    monkeypatch.setattr(bucket_tools, "_store_lock",
                        other_adopt_lands_before_we_hold_the_lock)
    with pytest.raises(bucket_tools.RebindRefused, match="-first"):
        bucket_tools.adopt("-second", str(new), apply=True)
    assert _root(second) == "/gone/second"


def test_adopt_refuses_when_the_store_lock_is_not_held(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    b = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")

    @contextlib.contextmanager
    def never(_d):
        yield False

    monkeypatch.setattr(jsonl, "dir_lock", never)
    with pytest.raises(bucket_tools.RebindRefused, match="lock"):
        bucket_tools.adopt("-old", str(new), apply=True)
    assert _root(b) == "/gone/old"


def test_remap_rechecks_collisions_against_roots_written_since_the_plan(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    old, new = _homes(tmp_path)
    mine = _bucket(tmp_checkpoint_dir, "-mine", str(old / "p"))
    rival = _bucket(tmp_checkpoint_dir, "-rival", "/gone/rival")
    real_lock = bucket_tools._store_lock

    @contextlib.contextmanager
    def rival_lands():
        (rival / "root").write_text(str(new.resolve() / "p") + "\n",
                                    encoding="utf-8")
        with real_lock():
            yield

    monkeypatch.setattr(bucket_tools, "_store_lock", rival_lands)
    plan = bucket_tools.remap(str(old), str(new), apply=True, force=True)
    assert [a["action"] for a in plan["actions"]] == ["collision"]
    assert _root(mine) == str(old / "p")


# ---- m7: SEALED refuses adopt and remap like tenant scope ---------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["bucket", "adopt", "--bucket=-old", "--path=.", "--apply"],
    ["bucket", "remap", "--from=/a", "--to=/b", "--apply"],
])
def test_adopt_and_remap_are_refused_under_sealed(
        argv, tmp_path, tmp_checkpoint_dir, capsys):
    home = tmp_path / "home"
    home.mkdir()
    _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    hostgit.configure(posture=SEALED, ceiling=home)
    try:
        before = _snapshot()
        assert cli.main(argv) == 2
        assert _snapshot() == before
        assert "SEALED" in capsys.readouterr().err
    finally:
        hostgit.configure(posture=hostgit.Posture.AMBIENT)


# ---- m6: an MCP tool call is one operation --------------------------------------------------------------------


def test_an_mcp_tool_call_runs_inside_one_identity_operation(monkeypatch):
    from daimon_briefing import mcp_server, mcp_tools

    seen = []

    class Result:
        text = "ok"
        notes = ()

    monkeypatch.setitem(
        mcp_tools.HANDLERS, "daimon_probe",
        lambda arguments: seen.append(identity._HOLDER.get()) or Result())
    out = mcp_server._handle_tools_call({"name": "daimon_probe"})
    assert out["isError"] is False
    assert seen and seen[0] is not None
    assert identity._HOLDER.get() is None


# ---- m8: the notice is information, not a failure ------------------------------------------------------------------


def test_the_unbound_notice_does_not_flip_status_to_not_ok():
    proj = {"exists": True, "session_id": "P", "age_seconds": 100, "age": "1m"}
    glob = {"exists": True, "session_id": "P", "same_session_as_project": True}
    h = cli._status_health(proj, glob, [], [], now=1000.0, unbound=True)
    assert h["ok"] is True and h["verdict"].startswith("✓")
    assert h["warnings"] == [identity.UNBOUND_NOTICE]


def test_a_real_warning_still_leads_the_verdict_beside_the_notice():
    proj = {"exists": False}
    h = cli._status_health(proj, {"exists": True}, [], [], now=1000.0,
                           unbound=True)
    assert h["ok"] is False
    assert "no checkpoint" in h["verdict"]
    assert h["warnings"][-1] == identity.UNBOUND_NOTICE


# ---- m9: the hook helper drops the pointer wherever it is ------------------------------------------------------------


@pytest.fixture(scope="module")
def lib():
    path = PLUGIN.parent / "hook" / "_daimon_hook_lib.py"
    spec = importlib.util.spec_from_file_location("_hook_lib_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POINTER = ("No checkpoint yet — nothing to brief. Checkpoints are written "
           "automatically at session end; to backfill one manually, run "
           "`daimon serialize <transcript>`.")


def test_a_pointer_followed_by_team_output_is_still_dropped(lib):
    text = f"NOTE\n\n{POINTER}\n\nTeammates:\n- a teammate item"
    assert lib.injectable(text) == "NOTE\n\nTeammates:\n- a teammate item"


def test_a_line_that_only_starts_alike_is_kept(lib):
    text = "- one\nNo checkpoint yet in this note\n- two"
    assert lib.injectable(text) == text


# ---- m10: resolve never raises ---------------------------------------------------------------------------------------


def test_resolve_survives_an_embedded_nul(tmp_checkpoint_dir):
    got = identity.resolve("/a/b\0c")
    assert got.bound is False and got.slug
    assert identity.is_unbound("/a/b\0c") in (True, False)


@pytest.mark.parametrize("payload", [b"\xff\xfe\x00garbage",
                                     b'{"from_slug": {"a": 1}, "to_slug": "y"}\n'])
def test_resolve_survives_a_corrupt_receipts_file(
        payload, tmp_checkpoint_dir):
    tmp_checkpoint_dir.mkdir(parents=True)
    (tmp_checkpoint_dir / "migrations.jsonl").write_bytes(payload)
    assert identity.resolve("/a/b").slug == "-a-b"


def test_resolve_survives_a_receipts_path_that_is_a_directory(
        tmp_checkpoint_dir):
    (tmp_checkpoint_dir / "migrations.jsonl").mkdir(parents=True)
    assert identity.resolve("/a/b").slug == "-a-b"



# ---- M2 (temporary): no notice in a linked worktree until slice 5 -------------------------------


@pytest.fixture
def folder(tmp_path):
    p = tmp_path / "plain"
    p.mkdir()
    return str(p.resolve())


def test_a_linked_worktree_gets_no_notice_and_a_plain_folder_still_does(
        tmp_path, folder, tmp_checkpoint_dir):
    main = _repo(tmp_path / "main")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "side", str(wt))
    assert identity.is_unbound(str(wt.resolve())) is False
    assert identity.is_unbound(folder) is True


def test_the_suppression_changes_nothing_but_the_notice(
        tmp_path, tmp_checkpoint_dir):
    """Resolution and claim of a worktree are untouched: it still resolves
    unbound and is born its own bucket (slice 5 binds it to the main repo)."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    where = str(wt.resolve())
    assert identity.resolve(where).bound is False
    assert identity.is_unbound(where) is False
    ref = identity.claim(where)
    assert ref.slug == store.location_slug(where)
    assert (tmp_checkpoint_dir / ref.slug / "root").is_file()


def test_the_brief_of_a_linked_worktree_carries_no_notice(
        tmp_path, tmp_checkpoint_dir, capsys):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    cli.main(["brief", "--project", str(wt)])
    assert "No memory is bound" not in capsys.readouterr().out


def test_a_git_directory_is_not_a_worktree(tmp_path, tmp_checkpoint_dir):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert identity.is_unbound(str(repo.resolve())) is True


def test_a_git_symlink_is_not_a_worktree(tmp_path, tmp_checkpoint_dir):
    """Repository-controlled content picks nothing: only a regular `.git` file
    (lstat, never followed) counts, and nothing in it is read."""
    target = tmp_path / "somefile"
    target.write_text("gitdir: x\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").symlink_to(target)
    assert identity.is_unbound(str(repo.resolve())) is True


def test_the_check_forks_no_git_and_reads_no_file(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    import subprocess

    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere\n")

    def boom(*a, **k):
        raise AssertionError("forked")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    assert identity.is_unbound(str(wt.resolve())) is False


def test_a_bad_posture_setting_refuses_the_rebind_verbs_as_json(
        tmp_checkpoint_dir, monkeypatch, capsys):
    import json

    monkeypatch.setenv("DAIMON_GIT_POSTURE", "nonsense")
    assert cli.main(["bucket", "remap", "--from=/a", "--to=/b", "--json"]) == 2
    assert "SEALED" in json.loads(capsys.readouterr().out)["refused"]


def test_adopt_into_a_bucket_locked_by_another_process_is_refused(
        tmp_path, tmp_checkpoint_dir, monkeypatch):
    new = tmp_path / "new"
    new.mkdir()
    b = _bucket(tmp_checkpoint_dir, "-old", "/gone/old")
    monkeypatch.setattr(bucket_tools, "_write_rebind",
                        lambda bucket, old, to: "locked")
    with pytest.raises(bucket_tools.RebindRefused, match="could not lock"):
        bucket_tools.adopt("-old", str(new), apply=True)
    assert _root(b) == "/gone/old"
