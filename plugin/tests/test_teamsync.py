"""Team sidecar sync (#113): init/sync/status against REAL git and LOCAL bare
remotes under tmp (fast, deterministic — the same two-clones-one-bare shape the
sync spike used). No test ever talks to a network remote.

The autouse conftest fixture already points DAIMON_TEAM_DIR under tmp, so no
test can touch the developer's real ~/.daimon/team.
"""

import json
import os
import subprocess
import time

import pytest

from daimon_briefing import cli, config, store, teamsync


def _git(cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True, timeout=30,
    )


@pytest.fixture(autouse=True)
def _git_isolation(monkeypatch):
    """Keep the host's git config (signing, hooks, odd defaults) out of every
    repo these tests create — commits must be deterministic everywhere."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def bare_remote(tmp_path):
    """A LOCAL bare repo standing in for the private team sidecar remote."""
    bare = tmp_path / "origin" / "team-mem.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True, capture_output=True, timeout=30,
    )
    return bare


def _seed_remote(bare, tmp_path) -> None:
    """Give the bare remote a root commit (a non-empty remote to clone)."""
    seed = tmp_path / "seed-clone"
    subprocess.run(["git", "clone", str(bare), str(seed)],
                   check=True, capture_output=True, timeout=30)
    (seed / "README.md").write_text("seeded\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@x", "commit", "-m", "seed")
    _git(seed, "push", "origin", "HEAD")


def _bare_files(bare) -> set:
    """Paths present in the remote's HEAD tree."""
    proc = subprocess.run(
        ["git", "-C", str(bare), "ls-tree", "-r", "--name-only", "HEAD"],
        capture_output=True, text=True, timeout=30,
    )
    return set(proc.stdout.split())


# ---- remote_slug ----


def test_remote_slug_munges_scheme_and_git_suffix():
    assert teamsync.remote_slug("https://github.com/org/team-mem.git") == \
        "github-com-org-team-mem"


def test_remote_slug_handles_scp_style():
    assert teamsync.remote_slug("git@github.com:org/x.git") == "git-github-com-org-x"


def test_remote_slug_rejects_empty():
    with pytest.raises(teamsync.TeamError):
        teamsync.remote_slug("   ")


# ---- daimon team init ----


def test_init_clones_seeded_remote(bare_remote, tmp_path, monkeypatch):
    _seed_remote(bare_remote, tmp_path)
    dest = teamsync.init(str(bare_remote))
    assert dest == config.team_dir() / teamsync.remote_slug(str(bare_remote))
    assert (dest / ".git").exists()
    assert (dest / "README.md").read_text(encoding="utf-8") == "seeded\n"


def test_init_empty_remote_seeds_root_commit_and_pushes(bare_remote):
    dest = teamsync.init(str(bare_remote))
    # unborn-branch handling: a README stub root commit exists locally AND on
    # the remote, so every later sync has a branch to ls-remote against.
    assert (dest / "README.md").exists()
    assert "README.md" in _bare_files(bare_remote)


def test_init_existing_dir_errors_with_hint(bare_remote):
    slug = teamsync.remote_slug(str(bare_remote))
    dest = config.team_dir() / slug
    dest.mkdir(parents=True)
    with pytest.raises(teamsync.TeamError, match="already"):
        teamsync.init(str(bare_remote))


def test_init_bad_clone_target_errors(tmp_path):
    with pytest.raises(teamsync.TeamError, match="clone failed"):
        teamsync.init(str(tmp_path / "no-such-remote.git"))


# ---- daimon team sync: commit own files, push, graceful no-ops ----


def _write_team_file(sidecar, author_slug, name, payload=None):
    d = sidecar / "authors" / author_slug
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        json.dumps(payload or {"session_id": name, "author": author_slug}),
        encoding="utf-8",
    )


def test_sync_commits_and_pushes_own_new_files(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 1
    assert r["pushed"] is True
    assert "authors/Ada/S1.json" in _bare_files(bare_remote)
    # mechanical commit message
    subject = _git(sidecar, "log", "-1", "--format=%s").stdout.strip()
    assert subject == "sync: Ada 1 checkpoint(s)"


def test_sync_never_touches_other_authors_paths(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    _write_team_file(sidecar, "Bob", "S9.json")  # a stray foreign file
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 1
    # Bob's file was neither committed nor pushed — still untracked.
    status = _git(sidecar, "status", "--porcelain").stdout
    assert "?? authors/Bob/" in status
    assert "authors/Bob/S9.json" not in _bare_files(bare_remote)


def test_sync_commit_excludes_prestaged_unrelated_paths(bare_remote, monkeypatch):
    # #144: a bare `git commit` sweeps EVERYTHING in the index. If anything
    # unrelated was pre-staged in the sidecar when a sync fires, it must NOT be
    # published to the team branch, must NOT inflate the reported count, and
    # must still be staged (exactly as the user left it) after the sync.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    (sidecar / "notes.txt").write_text("not for the team\n", encoding="utf-8")
    _git(sidecar, "add", "--", "notes.txt")
    _write_team_file(sidecar, "Ada", "S1.json")
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 1        # own-dir count, not index-wide
    committed = set(
        _git(sidecar, "diff", "--name-only", "HEAD~1", "HEAD").stdout.split()
    )
    assert committed == {"authors/Ada/S1.json"}
    assert "notes.txt" not in _bare_files(bare_remote)
    staged = _git(sidecar, "diff", "--cached", "--name-only").stdout.split()
    assert staged == ["notes.txt"]    # pre-staged entry survives untouched


def _write_nested_team_file(sidecar, logical, author_slug, name, payload=None):
    """Lay down a #200 nested-era file: projects/<logical…>/authors/<slug>/<name>."""
    d = sidecar.joinpath("projects", *logical.split("/"), "authors", author_slug)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        json.dumps(payload or {"session_id": name, "author": author_slug}),
        encoding="utf-8",
    )


def test_sync_stages_own_files_in_both_eras(bare_remote, monkeypatch):
    # #200: own files land flat (legacy era) AND nested at any depth under
    # projects/ — sync must commit both, and never another author's files.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    _write_nested_team_file(sidecar, "core/api", "Ada", "S2.json")
    _write_nested_team_file(sidecar, "core/cosmo/dusters/finance-1", "Ada", "S3.json")
    _write_nested_team_file(sidecar, "core/api", "Bob", "S9.json")
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 3
    published = _bare_files(bare_remote)
    assert "authors/Ada/S1.json" in published
    assert "projects/core/api/authors/Ada/S2.json" in published
    assert "projects/core/cosmo/dusters/finance-1/authors/Ada/S3.json" in published
    # Bob's nested file was neither committed nor pushed — still untracked.
    status = _git(sidecar, "status", "--porcelain").stdout
    assert "?? projects/core/api/authors/Bob/" in status
    assert "projects/core/api/authors/Bob/S9.json" not in published


def test_sync_nested_only_no_flat_dir(bare_remote, monkeypatch):
    # A sidecar born after #200 may have NO flat authors/ dir at all.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_nested_team_file(sidecar, "core/api", "Ada", "S1.json")
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 1
    assert "projects/core/api/authors/Ada/S1.json" in _bare_files(bare_remote)


def test_sync_failed_add_aborts_commit(bare_remote, monkeypatch):
    # #144: if staging the own dir fails, committing would publish whatever
    # stale state the index happens to hold — abort instead.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    real_git = teamsync._git

    def failing_add(cwd, *args, **kwargs):
        if args and args[0] == "add":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="add failed")
        return real_git(cwd, *args, **kwargs)

    monkeypatch.setattr(teamsync, "_git", failing_add)
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 0


def test_sync_nothing_to_do_is_clean_noop(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    teamsync.sync_remote(sidecar)
    r = teamsync.sync_remote(sidecar)  # second run: nothing new anywhere
    assert r["committed"] == 0
    assert r["pushed"] is False
    assert r["warnings"] == []


def test_sync_offline_commits_locally_and_degrades(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    # Simulate going offline: the remote vanishes out from under the clone.
    offline = bare_remote.with_name("gone.git")
    bare_remote.rename(offline)
    r = teamsync.sync_remote(sidecar)
    assert r["committed"] == 1        # local commit still lands
    assert r["pushed"] is False
    assert any("offline" in n for n in r["notes"])
    assert r["warnings"] == []        # offline is a note, not an alarm


def test_cli_team_sync_no_remotes_rc0(capsys):
    assert cli.main(["team", "sync"]) == 0
    out = capsys.readouterr().out
    assert "nothing to sync" in out


# ---- two authors, one bare remote (the spike's clone topology) ----


def _author_sidecar(bare, tmp_path, monkeypatch, author, dirname):
    """Switch identity to `author` (own team dir + DAIMON_AUTHOR) and return
    their sidecar clone, initializing it on first call."""
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(tmp_path / dirname))
    monkeypatch.setenv("DAIMON_AUTHOR", author)
    sidecar = config.team_dir() / teamsync.remote_slug(str(bare))
    if not sidecar.exists():
        return teamsync.init(str(bare))
    return sidecar


def test_two_authors_converge_via_freshness_gate(bare_remote, tmp_path, monkeypatch):
    ada = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    bob = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")

    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    _write_team_file(ada, "Ada", "S1.json")
    assert teamsync.sync_remote(ada)["pushed"] is True

    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")
    _write_team_file(bob, "Bob", "S2.json")
    r = teamsync.sync_remote(bob)
    # Bob's tracking ref is stale -> gate mismatch -> fetch+merge -> push.
    assert r["fetched"] is True
    assert r["pushed"] is True
    assert r["warnings"] == []  # honest authors: no mismatch alarm
    files = _bare_files(bare_remote)
    assert {"authors/Ada/S1.json", "authors/Bob/S2.json"} <= files
    # Ada's checkpoint is now local to Bob's sidecar (visible to read_team).
    assert (bob / "authors" / "Ada" / "S1.json").exists()


def test_push_reject_fetch_merge_push_retry(bare_remote, tmp_path, monkeypatch):
    ada = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    bob = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")

    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    _write_team_file(ada, "Ada", "S1.json")
    assert teamsync.sync_remote(ada)["pushed"] is True

    # Simulate the gate-vs-push race: the gate reports "unchanged" (stale
    # tracking hash) so sync goes straight to push and gets REJECTED, forcing
    # the reject -> fetch+merge -> push retry path.
    real_rev = teamsync._rev
    monkeypatch.setattr(
        teamsync, "_ls_remote",
        lambda sidecar, branch: ("ok", real_rev(sidecar, f"refs/remotes/origin/{branch}")),
    )
    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")
    _write_team_file(bob, "Bob", "S2.json")
    r = teamsync.sync_remote(bob)
    assert r["fetched"] is True   # the reject forced an integrate
    assert r["pushed"] is True    # and the retry landed
    assert {"authors/Ada/S1.json", "authors/Bob/S2.json"} <= _bare_files(bare_remote)


def test_ls_remote_gate_skips_fetch_when_remote_unchanged(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    teamsync.sync_remote(sidecar)
    r = teamsync.sync_remote(sidecar)  # remote unchanged since our own push
    assert r["fetched"] is False
    # No object transfer EVER happened: fetch is what creates FETCH_HEAD.
    assert not (sidecar / ".git" / "FETCH_HEAD").exists()


# ---- guard rails ----


def test_remote_history_rewrite_warns_and_touches_nothing(bare_remote, tmp_path, monkeypatch):
    ada = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    _write_team_file(ada, "Ada", "S1.json")
    assert teamsync.sync_remote(ada)["pushed"] is True
    bob = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")

    # Someone rewrites shared history: drop Ada's checkpoint commit and
    # force-push a replacement (daimon itself never does this).
    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Ada", "team-a")
    _git(ada, "reset", "--hard", "HEAD~1")
    _write_team_file(ada, "Ada", "S1-rewritten.json")
    _git(ada, "add", "--", "authors/Ada")
    _git(ada, "-c", "user.name=Ada", "-c", "user.email=a@x", "commit", "-m", "rewrite")
    _git(ada, "push", "--force", "origin", "HEAD")
    rewritten_head = subprocess.run(
        ["git", "-C", str(bare_remote), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()

    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")
    before = _git(bob, "rev-parse", "HEAD").stdout.strip()
    r = teamsync.sync_remote(bob)
    assert any("REWRITTEN" in w for w in r["warnings"])
    assert r["pushed"] is False
    # No auto-repair in either direction: Bob's local copy is untouched AND
    # the remote was not "fixed" by a (force-)push from Bob.
    assert _git(bob, "rev-parse", "HEAD").stdout.strip() == before
    assert (bob / "authors" / "Ada" / "S1.json").exists()
    after = subprocess.run(
        ["git", "-C", str(bare_remote), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    assert after == rewritten_head


def test_author_vs_committer_mismatch_warns(bare_remote, tmp_path, monkeypatch):
    mallory = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Mallory", "team-m")
    bob = _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")

    # Mallory stamps someone ELSE's name into the checkpoint JSON but the git
    # commit (free provenance) says Mallory.
    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Mallory", "team-m")
    _write_team_file(mallory, "Mallory", "S1.json",
                     payload={"session_id": "S1", "author": "Ada"})
    assert teamsync.sync_remote(mallory)["pushed"] is True

    _author_sidecar(bare_remote, tmp_path, monkeypatch, "Bob", "team-b")
    r = teamsync.sync_remote(bob)
    assert any(
        "author mismatch" in w and "Ada" in w and "Mallory" in w
        for w in r["warnings"]
    )
    # Surfaced, not blocked: the file still arrived.
    assert (bob / "authors" / "Mallory" / "S1.json").exists()


# ---- dual-write routing (#111 -> #113): one real remote takes over 'local' ----


def _checkpoint(session_id):
    return {"session_id": session_id, "working_context": {"active_topic":
            {"text": "t", "trust": "inferred"}, "open_questions": [],
            "recent_decisions": []}}


def test_dual_write_routes_into_single_remote_sidecar(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    monkeypatch.setenv("DAIMON_TEAM", "1")
    sidecar = teamsync.init(str(bare_remote))
    store.write_checkpoint("S-r", _checkpoint("S-r"), project_dir="/repo/x")
    assert (sidecar / "authors" / "Ada" / "S-r.json").exists()
    assert not (config.team_dir() / "local").exists()


def test_dual_write_multiple_remotes_keeps_local(bare_remote, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    monkeypatch.setenv("DAIMON_TEAM", "1")
    teamsync.init(str(bare_remote))
    second = tmp_path / "origin" / "other.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(second)],
                   check=True, capture_output=True, timeout=30)
    teamsync.init(str(second))
    store.write_checkpoint("S-l", _checkpoint("S-l"), project_dir="/repo/x")
    # Ambiguous routing -> the documented 'local' fallback, not a guess.
    assert (config.team_dir() / "local" / "authors" / "Ada" / "S-l.json").exists()


# ---- CLI: team init/status + the ONE team line in `daimon status` ----


def test_cli_team_init_rc1_on_existing_dir(bare_remote, capsys):
    assert cli.main(["team", "init", str(bare_remote)]) == 0
    assert cli.main(["team", "init", str(bare_remote)]) == 1
    assert "already initialized" in capsys.readouterr().err


def test_cli_team_status_freshness_unpushed_authors(bare_remote, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    _git(sidecar, "add", "--", "authors/Ada")
    _git(sidecar, "-c", "user.name=Ada", "-c", "user.email=a@x",
         "commit", "-m", "unpushed")
    assert cli.main(["team", "status"]) == 0
    out = capsys.readouterr().out
    assert teamsync.remote_slug(str(bare_remote)) in out
    assert "fresh" in out              # ls-remote hash matches tracking ref
    assert "1 unpushed" in out
    assert "Ada" in out


def test_status_authors_seen_includes_nested(bare_remote, monkeypatch):
    # #200: authors who only ever wrote in the nested era must still count.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")
    _write_nested_team_file(sidecar, "core/api", "Bob", "S2.json")
    rows = teamsync.team_status()
    assert rows[0]["authors"] == ["Ada", "Bob"]


def test_team_status_warns_on_broken_config(bare_remote, monkeypatch, capsys):
    # #200: a broken daimon-team.toml fails open (config treated as absent),
    # but the parse error must surface in `daimon team status`.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    (sidecar / "daimon-team.toml").write_text("[projects.\nbroken", encoding="utf-8")
    rows = teamsync.team_status()
    assert rows[0]["config_warning"]
    assert cli.main(["team", "status"]) == 0
    assert "daimon-team.toml" in capsys.readouterr().out


def test_team_status_no_warning_on_valid_or_missing_config(bare_remote, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    rows = teamsync.team_status()
    assert rows[0]["config_warning"] is None  # missing file: no false alarm
    (sidecar / "daimon-team.toml").write_text(
        '[projects."core/x"]\nrepos = ["https://github.com/org/x"]\n',
        encoding="utf-8",
    )
    rows = teamsync.team_status()
    assert rows[0]["config_warning"] is None


def test_cli_team_status_no_remote(capsys):
    assert cli.main(["team", "status"]) == 0
    assert "no team remote configured" in capsys.readouterr().out


def test_daimon_status_gains_team_line_when_remote_exists(bare_remote, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    teamsync.init(str(bare_remote))
    cli.main(["status", "--project", str(tmp_path)])
    out = capsys.readouterr().out
    team_lines = [ln for ln in out.splitlines() if ln.startswith("team: ")]
    assert len(team_lines) == 1        # exactly ONE objective line
    assert "1 remote" in team_lines[0]
    assert "0 unpushed" in team_lines[0]


def test_daimon_status_silent_when_team_unused(tmp_path, capsys):
    cli.main(["status", "--project", str(tmp_path)])
    out = capsys.readouterr().out
    assert "team:" not in out          # no remote -> no line, no false alarms


def test_cli_team_sync_git_absent_rc0(monkeypatch, capsys):
    monkeypatch.setattr(teamsync.shutil, "which", lambda _name: None)
    assert cli.main(["team", "sync"]) == 0
    assert "git not found" in capsys.readouterr().out


# ---- review hardening: URL injection + wedged-sidecar recovery ----


@pytest.mark.parametrize("url", [
    "-owned", "--upload-pack=/tmp/evil", "ext::sh -c whoami", "fd::17",
])
def test_init_rejects_option_and_transport_injection(url, monkeypatch):
    # Option-shaped URLs reach `git clone` argv where git parses them as flags
    # (--upload-pack is the classic RCE shape); ext::/fd:: transports execute
    # arbitrary commands. init must reject them BEFORE any subprocess runs.
    def no_subprocess(*a, **k):
        raise AssertionError("subprocess must not run for a rejected URL")
    monkeypatch.setattr(teamsync.subprocess, "run", no_subprocess)
    with pytest.raises(teamsync.TeamError):
        teamsync.init(url)


def test_sync_recovers_from_leftover_merge_state(bare_remote, tmp_path):
    # A timeout mid-merge leaves MERGE_HEAD behind; every later sync used to
    # fail on it forever. sync_remote must recover (abort) or surface a manual
    # hint — never raise, never stay silently wedged.
    _seed_remote(bare_remote, tmp_path)
    dest = teamsync.init(str(bare_remote))
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"],
                          capture_output=True, text=True, timeout=30).stdout.strip()
    (dest / ".git" / "MERGE_HEAD").write_text(head + "\n", encoding="utf-8")
    (dest / ".git" / "MERGE_MSG").write_text("wedged\n", encoding="utf-8")
    report = teamsync.sync_remote(dest)
    assert not (dest / ".git" / "MERGE_HEAD").exists() or any(
        "merge" in w.lower() for w in report["warnings"]
    ), "wedged merge state neither recovered nor surfaced"


def test_sync_removes_stale_index_lock_keeps_fresh(bare_remote, tmp_path):
    _seed_remote(bare_remote, tmp_path)
    dest = teamsync.init(str(bare_remote))
    lock = dest / ".git" / "index.lock"

    # Stale lock (ancient mtime): a crashed git left it — remove and note.
    lock.write_text("", encoding="utf-8")
    os.utime(lock, (time.time() - 3600, time.time() - 3600))
    report = teamsync.sync_remote(dest)
    assert not lock.exists(), "stale index.lock not cleaned"

    # Fresh lock (live concurrent sync, scar 0002): must NOT be deleted.
    lock.write_text("", encoding="utf-8")
    report = teamsync.sync_remote(dest)
    assert lock.exists(), "live index.lock must not be stolen"
    assert isinstance(report, dict)  # degraded, never raised


# ---- #136: git timeouts degrade offline, non-interactive credentials ----


def test_git_timeout_returns_failed_completedprocess_not_raise(tmp_path, monkeypatch):
    # TimeoutExpired subclasses SubprocessError (NOT OSError), so it would slip
    # past every caller's OSError catch. _git must convert it into the same
    # rc != 0 shape the offline/TeamError paths already handle — never raise.
    def boom(argv, *a, **k):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=k.get("timeout", 1))
    monkeypatch.setattr(teamsync.subprocess, "run", boom)
    proc = teamsync._git(tmp_path, "ls-remote", "origin")
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode != 0
    assert "timed out" in (proc.stderr or "")


def test_git_calls_are_non_interactive(tmp_path, monkeypatch):
    # A private remote with no cached creds must fail fast, not block on git's
    # interactive credential prompt until the timeout fires.
    captured = {}
    real_run = subprocess.run

    def spy(argv, *a, **k):
        captured.update(k)
        captured["argv"] = argv
        return real_run(argv, *a, **k)

    monkeypatch.setattr(teamsync.subprocess, "run", spy)
    teamsync._git(tmp_path, "rev-parse", "HEAD")
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["capture_output"] is True  # existing kwargs preserved, not clobbered
    assert captured["text"] is True


def test_sync_remote_offline_on_timeout_never_raises(bare_remote, monkeypatch):
    # The "never raises / lands offline" contract must hold under a hung remote,
    # not just under a clean rc != 0 (which the offline test already covers).
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    sidecar = teamsync.init(str(bare_remote))
    _write_team_file(sidecar, "Ada", "S1.json")

    def timeout_run(argv, *a, **k):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=k.get("timeout", 1))

    monkeypatch.setattr(teamsync.subprocess, "run", timeout_run)
    r = teamsync.sync_remote(sidecar)  # must NOT raise
    assert isinstance(r, dict)
    assert any("offline" in n for n in r["notes"])
    assert r["pushed"] is False


def test_cli_team_sync_rc0_on_timeout(bare_remote, monkeypatch):
    # End-to-end: a hung remote degrades to rc 0, never a traceback + crash.
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    teamsync.init(str(bare_remote))

    def timeout_run(argv, *a, **k):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=k.get("timeout", 1))

    monkeypatch.setattr(teamsync.subprocess, "run", timeout_run)
    assert cli.main(["team", "sync"]) == 0


def test_init_clone_timeout_surfaces_teamerror(tmp_path, monkeypatch):
    # init's contract is a clean TeamError on failure — a clone timeout must
    # surface there, not as a raw TimeoutExpired traceback.
    real_run = subprocess.run

    def maybe_timeout(argv, *a, **k):
        if "clone" in argv:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=k.get("timeout", 1))
        return real_run(argv, *a, **k)

    monkeypatch.setattr(teamsync.subprocess, "run", maybe_timeout)
    with pytest.raises(teamsync.TeamError, match="clone failed"):
        teamsync.init(str(tmp_path / "some-remote.git"))


def test_merge_works_without_any_git_identity(bare_remote, tmp_path, monkeypatch):
    # CI runners have NO git identity and auto-detect fails ("(none)" hostname);
    # macOS auto-detects, which masked this locally. Reproduce CI exactly:
    # a global config that forbids auto-detection and carries no identity.
    # The integrate merge must fall back to a daimon-derived identity like
    # _commit does — otherwise every fetch+merge fails only on CI.
    strict = tmp_path / "strict-gitconfig"
    strict.write_text("[user]\n\tuseConfigOnly = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(strict))

    _seed_remote(bare_remote, tmp_path)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    ada = teamsync.init(str(bare_remote))
    # Someone else pushes → ada's tracking ref goes stale → her sync must
    # fetch + MERGE (a commit needing identity) and then push.
    seed = tmp_path / "seed-clone"
    (seed / "poke.txt").write_text("x\n", encoding="utf-8")
    _git(seed, "add", "poke.txt")
    _git(seed, "-c", "user.name=Seed", "-c", "user.email=seed@x", "commit", "-m", "poke")
    _git(seed, "push", "origin", "HEAD")

    (ada / "authors" / "ada").mkdir(parents=True, exist_ok=True)
    (ada / "authors" / "ada" / "S1.json").write_text(
        json.dumps({"session_id": "S1", "author": "ada"}), encoding="utf-8")
    report = teamsync.sync_remote(ada)
    assert report["pushed"] is True, report
