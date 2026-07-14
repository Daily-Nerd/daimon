"""Logical team-project resolution (#200): architect-authored daimon-team.toml
mapping, DAIMON_TEAM_PROJECT override, origin-derived fallback.

Git-dependent tests build REAL local repos under tmp (the test_teamsync
pattern) — no test ever talks to a network remote. The autouse conftest
fixture already points DAIMON_TEAM_DIR under tmp.
"""

import json
import os
import subprocess

import pytest

from daimon_briefing import config, store, teamproject


@pytest.fixture(autouse=True)
def _git_isolation(monkeypatch):
    """Keep the host's git config out of every repo these tests create."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


def _repo(tmp_path, name, origin=None):
    """A real local git repo, optionally with an `origin` remote configured."""
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(d)],
                   check=True, capture_output=True, timeout=30)
    if origin:
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin", origin],
                       check=True, capture_output=True, timeout=30)
    return d


def _write_config(text, remote="local"):
    """Author a daimon-team.toml under <team_dir>/<remote>/ (the local read path)."""
    root = config.team_dir() / remote
    root.mkdir(parents=True, exist_ok=True)
    path = root / teamproject.CONFIG_NAME
    path.write_text(text, encoding="utf-8")
    return path


# ---- origin-URL normalization: ssh/https/scp forms must compare equal ----


def test_normalize_scp_and_https_forms_are_equal():
    assert (teamproject.normalize_repo_url("git@github.com:org/finance-svc.git")
            == teamproject.normalize_repo_url("https://github.com/org/finance-svc"))


def test_normalize_strips_git_suffix_and_trailing_slashes():
    assert (teamproject.normalize_repo_url("https://github.com/org/x.git/")
            == "github.com/org/x")


def test_normalize_lowercases():
    assert teamproject.normalize_repo_url("HTTPS://GitHub.COM/Org/Repo") == \
        "github.com/org/repo"


def test_normalize_strips_credentials():
    assert teamproject.normalize_repo_url("https://user:pass@github.com/org/x") == \
        "github.com/org/x"
    assert teamproject.normalize_repo_url("ssh://git@gitlab.com/grp/sub/x.git") == \
        "gitlab.com/grp/sub/x"


def test_normalize_empty_is_none():
    assert teamproject.normalize_repo_url("") is None
    assert teamproject.normalize_repo_url(None) is None


# ---- tier 1: DAIMON_TEAM_PROJECT — explicit local intent, no git needed ----


def test_env_project_resolves_without_git(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/api-gateway")
    assert teamproject.resolve(None) == ("core", "api-gateway")


def test_env_segments_munged_and_empties_dropped(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core//api gateway/")
    assert teamproject.resolve(None) == ("core", "api-gateway")


@pytest.mark.parametrize("hostile", [
    "../../etc",
    "..%2F..%2Fetc",
    "a\\..\\b",
    "/abs/path",
    "C:\\windows\\system32",
    "core/../../etc/passwd",
    "././.",
    "//",
    "  ",
])
def test_hostile_env_inputs_never_escape(monkeypatch, tmp_path, hostile):
    # Munged segments can never contain separators or `..` — a resolved path
    # joined under a base dir must stay inside that base dir.
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", hostile)
    segs = teamproject.resolve(None)
    if segs is None:
        return  # all-empty inputs resolve to nothing — flat era, safe
    base = tmp_path / "base"
    for seg in segs:
        assert "/" not in seg
        assert "\\" not in seg
        assert seg not in ("", ".", "..")
    joined = base.joinpath(*segs)
    assert joined.resolve().is_relative_to(base.resolve())


def test_env_all_empty_falls_through_to_none(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "///")
    assert teamproject.resolve(None) is None


# ---- tier 2: architect config mapping (normalized-origin match) ----


def test_config_mapping_matches_normalized_origin(tmp_path):
    repo = _repo(tmp_path, "finance-svc", "git@github.com:Org/Finance-Svc.git")
    _write_config(
        '[projects."core/cosmo/dusters/finance-1"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    assert teamproject.resolve(repo) == ("core", "cosmo", "dusters", "finance-1")


def test_env_beats_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "finance-svc", "git@github.com:org/finance-svc.git")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "overridden/here")
    assert teamproject.resolve(repo) == ("overridden", "here")


def test_config_project_key_segments_are_munged(tmp_path):
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    _write_config(
        '[projects."core/../etc/api gateway"]\n'
        'repos = ["https://github.com/org/x"]\n'
    )
    segs = teamproject.resolve(repo)
    assert segs == ("core", "--", "etc", "api-gateway")
    for seg in segs:
        assert "/" not in seg and seg != ".."


def test_multiple_repos_map_to_one_project(tmp_path):
    # Several repos → ONE logical project: the squad-level shared pool.
    svc = _repo(tmp_path, "svc", "git@github.com:org/finance-svc.git")
    web = _repo(tmp_path, "web", "https://github.com/org/finance-web")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = [\n'
        '  "git@github.com:org/finance-svc.git",\n'
        '  "https://github.com/org/finance-web",\n'
        ']\n'
    )
    assert teamproject.resolve(svc) == ("core", "finance")
    assert teamproject.resolve(web) == ("core", "finance")


# ---- tier 3: origin-derived fallback (zero-config portable identity) ----


def test_derived_fallback_strips_host_and_munges(tmp_path):
    repo = _repo(tmp_path, "x", "git@gitlab.com:platform/devops/infra.git")
    assert teamproject.resolve(repo) == ("platform", "devops", "infra")


def test_derived_fallback_when_config_does_not_match(tmp_path):
    repo = _repo(tmp_path, "x", "https://github.com/org/unmapped")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/other-repo"]\n'
    )
    assert teamproject.resolve(repo) == ("org", "unmapped")


# ---- tier 4: no origin at all → None → legacy flat era ----


def test_no_remote_returns_none(tmp_path):
    repo = _repo(tmp_path, "x")  # git repo, no origin remote
    assert teamproject.resolve(repo) is None


def test_non_repo_dir_returns_none(tmp_path):
    d = tmp_path / "not-a-repo"
    d.mkdir()
    assert teamproject.resolve(d) is None


def test_none_project_dir_returns_none():
    assert teamproject.resolve(None) is None


# ---- broken/missing config: fail open, surface the parse error ----


def test_broken_toml_fails_open_to_derived(tmp_path):
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    _write_config("[projects.\nthis is not toml")
    assert teamproject.resolve(repo) == ("org", "x")


def test_config_error_surfaces_parse_failure():
    _write_config("not = valid = toml [")
    err = teamproject.config_error(config.team_dir() / "local")
    assert err is not None
    assert teamproject.CONFIG_NAME in err


def test_config_error_none_when_missing():
    root = config.team_dir() / "local"
    root.mkdir(parents=True, exist_ok=True)
    assert teamproject.config_error(root) is None


def test_config_error_none_when_valid():
    _write_config('[projects."core/x"]\nrepos = ["https://github.com/org/x"]\n')
    assert teamproject.config_error(config.team_dir() / "local") is None


def test_unreadable_config_treated_absent(tmp_path):
    # A daimon-team.toml that cannot be READ (here: it is a directory) is the
    # same fail-open shape as broken TOML: config absent + error surfaced.
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    root = config.team_dir() / "local"
    (root / teamproject.CONFIG_NAME).mkdir(parents=True)
    assert teamproject.resolve(repo) == ("org", "x")
    assert teamproject.config_error(root) is not None


def test_config_with_wrong_shapes_is_tolerated(tmp_path):
    # Valid TOML, junk shapes: no crash, junk entries ignored, good ones work.
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    _write_config(
        'projects = 7\n'  # not even a table
    )
    assert teamproject.resolve(repo) == ("org", "x")
    assert teamproject.config_error(config.team_dir() / "local") is None


# ---- remap resilience: reads cover every candidate path, writes only the
# ---- winner — mapping (or env-overriding) a repo AFTER it synced must never
# ---- orphan the history already sitting under the earlier path ----


def _ago(days):
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() - days * 86400))


def _mini_cp(sid, days_ago):
    return {"session_id": sid, "created": _ago(days_ago)}


def test_read_candidates_winner_first_then_derived(tmp_path):
    repo = _repo(tmp_path, "x", "git@github.com:org/finance-svc.git")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    assert teamproject.read_candidates(repo) == [
        ("core", "finance"),        # tier 2 wins…
        ("org", "finance-svc"),     # …but the tier-3 path stays readable
    ]
    assert teamproject.resolve(repo) == ("core", "finance")  # writes: winner only


def test_read_candidates_env_includes_lower_tiers(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "x", "git@github.com:org/finance-svc.git")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/special")
    assert teamproject.read_candidates(repo) == [
        ("squad", "special"),
        ("core", "finance"),
        ("org", "finance-svc"),
    ]
    assert teamproject.resolve(repo) == ("squad", "special")


def test_read_candidates_dedupe_when_tiers_agree(tmp_path, monkeypatch):
    # env naming exactly the derived path must not produce a duplicate scan.
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "org/x")
    assert teamproject.read_candidates(repo) == [("org", "x")]


def test_read_candidates_empty_when_nothing_resolves(tmp_path):
    d = tmp_path / "not-a-repo"
    d.mkdir()
    assert teamproject.read_candidates(d) == []


def test_remap_keeps_prior_history_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    repo = _repo(tmp_path, "svc", "git@github.com:org/finance-svc.git")
    # Day one, unmapped: ada syncs under the tier-3 origin-derived path.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-early", _mini_cp("S-early", 30), project_dir=repo)
    derived = (config.team_dir() / "local" / "projects" / "org" / "finance-svc"
               / "authors" / "ada" / "S-early.json")
    assert derived.exists()
    # The architect maps the repo AFTER that history exists. A real remap is
    # seen by a fresh process; the per-process cache is cleared to model that.
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    teamproject._cache.clear()
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("S-late", _mini_cp("S-late", 1), project_dir=repo)
    mapped = (config.team_dir() / "local" / "projects" / "core" / "finance"
              / "authors" / "grace" / "S-late.json")
    assert mapped.exists()  # new writes land at the mapped path only
    # Reads fan across BOTH paths: pre-map history is not orphaned.
    team = store.read_team(project_dir=repo)
    by_author = {a: cp for a, cp in team}
    assert set(by_author) == {"ada", "grace"}
    assert by_author["ada"]["session_id"] == "S-early"


def test_env_override_remap_keeps_prior_history_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    repo = _repo(tmp_path, "svc", "git@github.com:org/finance-svc.git")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-early", _mini_cp("S-early", 30), project_dir=repo)
    # This machine later imposes an explicit override (tier 1).
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "squad/special")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("S-late", _mini_cp("S-late", 1), project_dir=repo)
    assert (config.team_dir() / "local" / "projects" / "squad" / "special"
            / "authors" / "grace" / "S-late.json").exists()
    team = store.read_team(project_dir=repo)
    by_author = {a: cp for a, cp in team}
    assert set(by_author) == {"ada", "grace"}  # config-era history still read


def test_same_author_across_candidates_newest_wins_once(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    repo = _repo(tmp_path, "svc", "git@github.com:org/finance-svc.git")
    store.write_checkpoint("S-early", _mini_cp("S-early", 30), project_dir=repo)
    _write_config(
        '[projects."core/finance"]\n'
        'repos = ["https://github.com/org/finance-svc"]\n'
    )
    teamproject._cache.clear()
    store.write_checkpoint("S-late", _mini_cp("S-late", 1), project_dir=repo)
    team = store.read_team(project_dir=repo)
    assert [a for a, _ in team] == ["ada"]  # one entry, no duplicates
    assert team[0][1]["session_id"] == "S-late"  # newest across candidates


# ---- caching: one git probe per process per project dir ----


def test_resolution_cached_per_project_dir(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "x", "https://github.com/org/x")
    calls = []
    real = teamproject._origin_url

    def counting(project_dir):
        calls.append(project_dir)
        return real(project_dir)

    monkeypatch.setattr(teamproject, "_origin_url", counting)
    assert teamproject.resolve(repo) == ("org", "x")
    assert teamproject.resolve(repo) == ("org", "x")
    assert len(calls) == 1


# ---- end to end: two mapped repos share one team read pool ----


def test_mapped_repos_share_one_team_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    svc = _repo(tmp_path, "svc", "git@github.com:org/finance-svc.git")
    web = _repo(tmp_path, "web", "https://github.com/org/finance-web")
    _write_config(
        '[projects."core/finance"]\n'
        'repos = [\n'
        '  "git@github.com:org/finance-svc.git",\n'
        '  "https://github.com/org/finance-web",\n'
        ']\n'
    )
    cp = {"session_id": "S-svc",
          "working_context": {"recent_decisions": [
              {"text": "Adopt the shared pool", "trust": "inferred"}]}}
    store.write_checkpoint("S-svc", cp, project_dir=svc)
    nested = (config.team_dir() / "local" / "projects" / "core" / "finance"
              / "authors" / "ada" / "S-svc.json")
    assert nested.exists()
    blob = json.loads(nested.read_text(encoding="utf-8"))
    assert blob["team_project"] == "core/finance"
    # A session in the OTHER mapped repo reads the same pool by construction.
    team = store.read_team(project_dir=web)
    assert [a for a, _ in team] == ["ada"]
    assert team[0][1]["session_id"] == "S-svc"


# ---- #202: the defensive seams codecov flagged unexercised on #201 ----


def test_tomli_import_fallback():
    # py3.10 has no stdlib tomllib; the module must fall back to the tomli
    # runtime dep (#202). Simulated on every version: block tomllib, stub
    # tomli, reload — manual restore so the module is ALWAYS reloaded clean
    # even when the assertion fails.
    import builtins
    import importlib
    import sys
    import types

    real_import = builtins.__import__
    stub = types.ModuleType("tomli")
    stub.loads = lambda s: {}

    def blocking(name, *args, **kwargs):
        if name == "tomllib":
            raise ModuleNotFoundError("No module named 'tomllib'")
        if name == "tomli":
            return stub
        return real_import(name, *args, **kwargs)

    saved_tomllib = sys.modules.pop("tomllib", None)
    saved_tomli = sys.modules.pop("tomli", None)
    builtins.__import__ = blocking
    try:
        importlib.reload(teamproject)
        assert teamproject.tomllib is stub
    finally:
        builtins.__import__ = real_import
        if saved_tomllib is not None:
            sys.modules["tomllib"] = saved_tomllib
        if saved_tomli is not None:
            sys.modules["tomli"] = saved_tomli
        importlib.reload(teamproject)


def test_logical_segments_empty_inputs():
    assert teamproject.logical_segments(None) == ()
    assert teamproject.logical_segments("") == ()


def test_config_entry_with_non_list_repos_is_skipped(tmp_path):
    # A malformed entry (repos = string, not list) is dropped, so resolution
    # falls through to the tier-3 origin-derived path.
    _write_config(
        '[projects."core/x"]\n'
        'repos = "git@github.com:org/a.git"\n'
    )
    repo = _repo(tmp_path, "a", "git@github.com:org/a.git")
    assert teamproject.read_candidates(repo)[0] == ("org", "a")


def test_git_probe_failure_resolves_to_flat_era(tmp_path, monkeypatch):
    # The origin probe raising (timeout, git missing) must degrade to tier 4
    # (legacy flat era), never propagate into a write.
    repo = _repo(tmp_path, "a", "git@github.com:org/a.git")

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=5)

    monkeypatch.setattr(teamproject.subprocess, "run", boom)
    assert teamproject.read_candidates(repo) == []


def test_read_candidates_swallows_resolver_bugs(tmp_path, monkeypatch):
    # Belt-and-braces: an unexpected resolver exception yields [] (flat era),
    # never a broken serialize.
    def buggy(project_dir):
        raise RuntimeError("resolver bug")

    monkeypatch.setattr(teamproject, "_candidates", buggy)
    assert teamproject.read_candidates(tmp_path) == []


# ---- #279: per-remote scope — default-closed membership for synced remotes ----
#
# DAIMON_TEAM=1 is machine-global; without a membership gate the single
# sidecar clone receives EVERY project on the machine — a privacy leak the
# tidy tier-3 origin-derived layout actively masks. A synced remote accepts a
# project's checkpoints only when the sidecar's daimon-team.toml grants it
# ([scope] repos, or an architect [projects.*] mapping) or the machine states
# explicit intent via DAIMON_TEAM_PROJECT. Everything else falls back to the
# Phase-1 local mirror: withheld from the clone, never lost.


def _remote_clone(name="team-mem"):
    """A dir store._team_write_slug treats as a real synced remote (.git present)."""
    d = config.team_dir() / name
    (d / ".git").mkdir(parents=True, exist_ok=True)
    return d


def test_in_scope_when_repo_listed_in_scope_table(tmp_path):
    repo = _repo(tmp_path, "alpha", "git@github.com:Org/Alpha.git")
    sidecar = _remote_clone()
    _write_config('[scope]\nrepos = ["https://github.com/org/alpha"]\n',
                  remote="team-mem")
    assert teamproject.in_scope(repo, sidecar) is True


def test_in_scope_when_repo_mapped_in_projects_table(tmp_path):
    # Grandfathering: an architect-mapped repo IS a member — existing mapped
    # sidecars keep syncing with zero new configuration.
    repo = _repo(tmp_path, "beta", "https://github.com/org/beta")
    sidecar = _remote_clone()
    _write_config('[projects."core/beta"]\nrepos = ["https://github.com/org/beta"]\n',
                  remote="team-mem")
    assert teamproject.in_scope(repo, sidecar) is True


def test_in_scope_env_project_is_explicit_intent(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "gamma", "https://github.com/org/gamma")
    sidecar = _remote_clone()  # no config at all
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/gamma")
    assert teamproject.in_scope(repo, sidecar) is True


def test_out_of_scope_when_repo_unlisted(tmp_path):
    repo = _repo(tmp_path, "intruder", "https://github.com/me/personal-notes")
    sidecar = _remote_clone()
    _write_config('[scope]\nrepos = ["https://github.com/org/alpha"]\n',
                  remote="team-mem")
    assert teamproject.in_scope(repo, sidecar) is False


def test_default_closed_when_no_config(tmp_path):
    repo = _repo(tmp_path, "alpha", "https://github.com/org/alpha")
    assert teamproject.in_scope(repo, _remote_clone()) is False


def test_default_closed_when_config_broken(tmp_path):
    # Path resolution fails OPEN on broken TOML (#200); membership fails
    # CLOSED — a paste error must not open the remote to the whole machine.
    repo = _repo(tmp_path, "alpha", "https://github.com/org/alpha")
    sidecar = _remote_clone()
    _write_config("[scope\nbroken", remote="team-mem")
    assert teamproject.in_scope(repo, sidecar) is False


def test_default_closed_when_project_has_no_origin(tmp_path):
    repo = _repo(tmp_path, "no-origin")  # git repo, no origin remote
    sidecar = _remote_clone()
    _write_config('[scope]\nrepos = ["https://github.com/org/alpha"]\n',
                  remote="team-mem")
    assert teamproject.in_scope(repo, sidecar) is False


def test_scope_entries_union_normalized_sorted(tmp_path):
    sidecar = _remote_clone()
    _write_config(
        '[scope]\nrepos = ["git@github.com:Org/Zeta.git"]\n'
        '[projects."core/beta"]\nrepos = ["https://github.com/org/beta"]\n',
        remote="team-mem",
    )
    assert teamproject.scope_entries(sidecar) == \
        ["github.com/org/beta", "github.com/org/zeta"]


def test_scope_entries_empty_when_no_config():
    assert teamproject.scope_entries(_remote_clone()) == []


# ---- #279 acceptance: an out-of-scope project never dual-writes into a remote ----


def test_dual_write_scoped_remote_receives_only_member_project(
        tmp_path, sample_checkpoint, monkeypatch):
    # THE leak test: two local projects, one scoped remote — only the member
    # project's checkpoints reach the sidecar clone; the foreign project's
    # fall back to the local mirror and are never staged for a push.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    member = _repo(tmp_path, "alpha", "https://github.com/org/alpha")
    foreign = _repo(tmp_path, "personal", "https://github.com/me/personal-notes")
    sidecar = _remote_clone()
    _write_config('[scope]\nrepos = ["https://github.com/org/alpha"]\n',
                  remote="team-mem")
    store.write_checkpoint("S-in", {**sample_checkpoint, "session_id": "S-in"},
                           project_dir=member)
    store.write_checkpoint("S-out", {**sample_checkpoint, "session_id": "S-out"},
                           project_dir=foreign)
    clone_files = {p.name for p in sidecar.rglob("*.json")}
    assert "S-in.json" in clone_files
    assert "S-out.json" not in clone_files  # nothing foreign enters the clone
    local_files = {p.name for p in (config.team_dir() / "local").rglob("*.json")}
    assert "S-out.json" in local_files      # withheld, not lost


def test_dual_write_unscoped_remote_default_closed(
        tmp_path, sample_checkpoint, monkeypatch):
    # A sidecar with NO daimon-team.toml grants nothing: writes fall back to
    # the local mirror (default-closed; migration is one [scope] line).
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    repo = _repo(tmp_path, "alpha", "https://github.com/org/alpha")
    sidecar = _remote_clone()
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=repo)
    assert not any(sidecar.rglob("*.json"))
    assert any((config.team_dir() / "local").rglob("*.json"))


def test_dual_write_env_project_reaches_remote(
        tmp_path, sample_checkpoint, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/alpha")
    repo = _repo(tmp_path, "alpha", "https://github.com/org/alpha")
    sidecar = _remote_clone()  # no config: env intent alone grants membership
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=repo)
    assert any(sidecar.rglob("*.json"))
