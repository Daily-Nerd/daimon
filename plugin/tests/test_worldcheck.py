"""#365 slice 1 / #397 slice 2: deterministic external-state spot-check for
carried claims at briefing render.

Contract pinned here:
- claim extraction: conservative per class — slice 1 wants a repo-local "#N"
  ref PLUS a state word (never bare refs, never cross-repo refs, never
  conflicting words); slice 2's file-exists / branch-state /
  dependency-version classes hold the same bar, so a bare path mention, a
  branch name with no "branch" keyword, and a future-tense version bump are
  all non-claims.
- check: read-only probes under ONE aggregate budget and ONE probe cap shared
  by every class; confirmed items untouched, contradicted items stamped with
  a transient `_worldcheck` annotation, everything else skipped SILENTLY.
- budget fairness (#397): the cap is allocated in checkpoint order (no class
  privileged) and on-disk probes execute before the `gh` fan-out (a hanging
  network probe cannot starve them).
- rendering: the stamped item gains an ADDED flag line reusing the existing
  confirm/reject command surface (`daimon resolve` / `daimon reverify`) —
  existing pinned literals never change, and every class stamps the same
  {note, status} shape so the render path stays class-agnostic.
- CLI wiring: DAIMON_WORLDCHECK opt-in (default OFF, byte-identical off-path),
  cross-project (--slug) and global-fallback briefs never probe, and outcomes
  land in usage.log as worldcheck:<outcome> AND worldcheck:<class>:<outcome>.

All gh probes in this suite hit a fake `gh` script on disk — zero network.
Slice 2's probes touch only tmp_path trees — zero network, zero subprocess.
"""

import json
import subprocess
import time

import pytest

from daimon_briefing import briefing, cli, config, store, worldcheck


# ---- helpers ----------------------------------------------------------------


def _cp(texts, carried=True, with_ids=True):
    """Checkpoint whose open_questions carry the given texts."""
    items = []
    for i, text in enumerate(texts):
        item = {"text": text, "trust": "inferred"}
        if carried:
            item["carried_from"] = "S-prev"
        if with_ids:
            item["id"] = f"o-{i + 1:06x}"
        items.append(item)
    return {
        "session_id": "S-now",
        "working_context": {"open_questions": items},
        "epistemic_snapshot": {},
    }


@pytest.fixture
def proj(tmp_path):
    """A real directory to act as the project root — Popen(cwd=...) needs it
    to exist (a nonexistent cwd is a spawn failure -> silent skip)."""
    d = tmp_path / "projroot"
    d.mkdir()
    return str(d)


@pytest.fixture
def fake_gh(tmp_path):
    """Executable fake `gh` + its invocation log. `body` is the shell that
    produces stdout; every call appends its argv (and cwd) to the log."""

    def make(body="echo '{\"state\":\"OPEN\"}'"):
        script = tmp_path / "fake-gh"
        log = tmp_path / "gh-calls.log"
        script.write_text(
            "#!/bin/sh\n"
            f'echo "$PWD|$@" >> "{log}"\n'
            f"{body}\n"
        )
        script.chmod(0o755)
        return str(script), log

    return make


def _enable_probes(monkeypatch, gh_path):
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: gh_path)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: True)


def _calls(log):
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if line.strip()]


# ---- claim extraction -------------------------------------------------------


def test_claim_pr_awaiting_review():
    claim = worldcheck.claim_of("PR #60 awaiting review")
    assert claim is not None
    assert claim.num == "60"
    assert claim.kind == "pr"
    assert claim.expected == frozenset({"OPEN"})


def test_claim_bare_ref_without_state_word_is_none():
    # "#48 slice 1" makes no state claim — nothing to check.
    assert worldcheck.claim_of("#48 slice 1 landed the cache") is None


def test_claim_cross_repo_ref_is_none():
    # gemini-cli#14715 belongs to ANOTHER repo — `gh` here would answer for
    # the wrong project. Never probe.
    assert worldcheck.claim_of("gemini-cli#14715 still open upstream") is None


def test_claim_issue_closed():
    claim = worldcheck.claim_of("issue #171 closed by the fix")
    assert claim is not None
    assert claim.num == "171"
    assert claim.kind == "issue"
    assert "CLOSED" in claim.expected


def test_claim_bare_merged_ref_is_pr():
    claim = worldcheck.claim_of("#60 merged last night")
    assert claim is not None
    assert claim.kind == "pr"
    assert claim.expected == frozenset({"MERGED"})


def test_claim_bare_open_ref_is_issue():
    claim = worldcheck.claim_of("#12 still open on the tracker")
    assert claim is not None
    assert claim.kind == "issue"
    assert claim.expected == frozenset({"OPEN"})


def test_claim_conflicting_state_words_is_none():
    # Both open-ish and done-ish vocabulary: the claim direction is ambiguous
    # and a wrong contradiction flag is worse than no check.
    assert worldcheck.claim_of("PR #60 was open, now merged") is None


def test_claim_state_word_without_ref_is_none():
    assert worldcheck.claim_of("awaiting review from the team") is None


def test_claim_explicit_kind_wins_over_state_heuristic():
    claim = worldcheck.claim_of("issue #9 awaiting triage")
    assert claim is not None
    assert claim.kind == "issue"


# ---- check(): probes, budget, stamping --------------------------------------


def test_check_confirmed_leaves_item_untouched(monkeypatch, fake_gh, proj):
    gh, log = fake_gh("echo '{\"state\":\"OPEN\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "pr-state:confirmed": 1}
    item = cp["working_context"]["open_questions"][0]
    assert "_worldcheck" not in item
    assert len(_calls(log)) == 1


def test_check_contradicted_stamps_item(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("echo '{\"state\":\"MERGED\",\"mergedAt\":\"2026-07-20T00:00:00Z\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 1, "skipped": 0,
                     "pr-state:contradicted": 1}
    item = cp["working_context"]["open_questions"][0]
    assert item["_worldcheck"] == {"note": "#60 merged", "status": "merged"}


def test_check_gh_missing_skips_silently(monkeypatch):
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: True)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, "/p/A")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_check_no_github_remote_skips_without_probing(monkeypatch, fake_gh):
    gh, log = fake_gh()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: gh)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: False)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, "/p/A")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}
    assert _calls(log) == []


def test_check_probe_failure_skips(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("exit 1")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}


def test_check_bad_json_skips(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("echo 'not json at all'")
    _enable_probes(monkeypatch, gh)
    stats = worldcheck.check(_cp(["PR #60 awaiting review"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}


def test_check_unknown_state_vocabulary_skips(monkeypatch, fake_gh, proj):
    # Only OPEN/CLOSED/MERGED may reach the rendered flag (the note rides
    # into briefing text — bounded vocabulary, not a passthrough).
    gh, _log = fake_gh("echo '{\"state\":\"WEIRD\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_check_budget_kills_slow_probe(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("sleep 5\necho '{\"state\":\"OPEN\"}'")
    _enable_probes(monkeypatch, gh)
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", 0.2)
    cp = _cp(["PR #60 awaiting review"])
    start = time.monotonic()
    stats = worldcheck.check(cp, proj)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0  # never blocks anywhere near the hook budget
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "pr-state:skipped": 1}


def test_check_probe_cap(monkeypatch, fake_gh, proj):
    gh, log = fake_gh("echo '{\"state\":\"OPEN\"}'")
    _enable_probes(monkeypatch, gh)
    texts = [f"PR #{n} awaiting review" for n in range(101, 108)]  # 7 claims
    stats = worldcheck.check(_cp(texts), proj)
    assert len(_calls(log)) == worldcheck.MAX_PROBES == 5
    assert stats["confirmed"] == 5
    assert stats["skipped"] == 2


def test_check_non_carried_items_never_probed(monkeypatch, fake_gh):
    gh, log = fake_gh()
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"], carried=False)
    stats = worldcheck.check(cp, "/p/A")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _calls(log) == []


def test_check_dedup_same_ref_probes_once_stamps_all(monkeypatch, fake_gh, proj):
    gh, log = fake_gh("echo '{\"state\":\"MERGED\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review", "PR #60 review pending on Omar"])
    stats = worldcheck.check(cp, proj)
    assert len(_calls(log)) == 1
    assert stats["contradicted"] == 2
    for item in cp["working_context"]["open_questions"]:
        assert item["_worldcheck"]["note"] == "#60 merged"


def test_check_probes_run_in_project_cwd(monkeypatch, fake_gh, tmp_path):
    gh, log = fake_gh()
    _enable_probes(monkeypatch, gh)
    project = tmp_path / "projroot"
    project.mkdir()
    worldcheck.check(_cp(["PR #60 awaiting review"]), str(project))
    calls = _calls(log)
    assert len(calls) == 1
    cwd = calls[0].split("|", 1)[0]
    assert cwd == str(project.resolve())


def test_check_issue_claim_uses_issue_probe(monkeypatch, fake_gh, proj):
    gh, log = fake_gh("echo '{\"state\":\"CLOSED\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["issue #171 still open"])
    stats = worldcheck.check(cp, proj)
    assert stats["contradicted"] == 1
    assert "issue view 171" in _calls(log)[0]
    assert cp["working_context"]["open_questions"][0]["_worldcheck"]["note"] == "#171 closed"


def test_github_repo_gate_against_real_git(tmp_path):
    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    repo = tmp_path / "with-remote"
    repo.mkdir()
    git("init", cwd=repo)
    git("remote", "add", "origin", "https://github.com/example/example.git", cwd=repo)
    assert worldcheck._github_repo(str(repo)) is True

    bare = tmp_path / "no-remote"
    bare.mkdir()
    git("init", cwd=bare)
    assert worldcheck._github_repo(str(bare)) is False

    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert worldcheck._github_repo(str(plain)) is False


# ---- #397 slice 2: file-exists claims ---------------------------------------


def test_path_claim_present_vocabulary():
    claim = worldcheck.path_claim_of("the fix lives in plugin/daimon_briefing/carry.py")
    assert claim is not None
    assert claim.path == "plugin/daimon_briefing/carry.py"
    assert claim.expected == frozenset({"EXISTS"})


def test_path_claim_absent_vocabulary():
    claim = worldcheck.path_claim_of("the old shim was deleted — legacy/adapter.py is gone")
    assert claim is not None
    assert claim.path == "legacy/adapter.py"
    assert claim.expected == frozenset({"MISSING"})


def test_path_claim_bare_path_without_vocabulary_is_none():
    # A path MENTION is not a claim — same bar as slice 1's bare "#N".
    assert worldcheck.path_claim_of("see plugin/daimon_briefing/carry.py for context") is None


def test_path_claim_conflicting_vocabulary_is_none():
    assert worldcheck.path_claim_of("added then deleted src/tmp.py") is None


def test_path_claim_rejects_absolute_and_home_paths():
    # Outside the project root: `Path.exists()` here answers about the WRONG
    # tree, exactly like slice 1's cross-repo "#N" refusal.
    assert worldcheck.path_claim_of("the config lives in /etc/daimon/prod.toml") is None
    assert worldcheck.path_claim_of("the config lives in ~/.config/app/settings.json") is None


def test_path_claim_rejects_url_paths():
    assert worldcheck.path_claim_of(
        "the doc lives in https://example.com/docs/guide.md") is None


def test_path_claim_rejects_prose_abbreviations():
    # "e.g." is [word].[word] shaped; only whitelisted source extensions count.
    assert worldcheck.path_claim_of("added a guard, e.g. for the null case") is None


def test_path_claim_rejects_parent_traversal():
    assert worldcheck.path_claim_of("the fix lives in ../other-repo/main.py") is None


def test_path_claim_long_input_completes():
    # Scar 22: every capture-path regex gets a long-input completion test —
    # completion IS the signal, no timing assert.
    assert worldcheck.path_claim_of("lives in " + "a-b." * 12500) is None
    assert worldcheck.path_claim_of("lives in " + "x" * 50000) is None


def test_check_file_exists_confirmed(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    (project / "src").mkdir(parents=True)
    (project / "src" / "fix.py").write_text("x = 1\n")
    # No gh, no GitHub remote: a deterministic disk probe must not need either.
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: False)
    cp = _cp(["the fix lives in src/fix.py"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "file-exists:confirmed": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_check_file_exists_contradicted_stamps_item(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["the fix lives in src/fix.py"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 0, "contradicted": 1, "skipped": 0,
                     "file-exists:contradicted": 1}
    assert cp["working_context"]["open_questions"][0]["_worldcheck"] == {
        "note": "src/fix.py missing", "status": "missing"}


def test_check_file_absent_claim_contradicted_when_file_returned(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    (project / "legacy").mkdir(parents=True)
    (project / "legacy" / "adapter.py").write_text("x = 1\n")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["legacy/adapter.py was deleted in the cleanup"])
    stats = worldcheck.check(cp, str(project))
    assert stats["contradicted"] == 1
    assert cp["working_context"]["open_questions"][0]["_worldcheck"] == {
        "note": "legacy/adapter.py exists", "status": "exists"}


def test_check_file_exists_never_spawns_a_subprocess(monkeypatch, tmp_path):
    # Contract: file-exists is a pure Path.exists() probe (issue #397).
    project = tmp_path / "repo"
    project.mkdir()

    def _boom(*a, **k):
        raise AssertionError("file-exists must never spawn a process")

    monkeypatch.setattr(worldcheck.subprocess, "Popen", _boom)
    monkeypatch.setattr(worldcheck.subprocess, "run", _boom)
    stats = worldcheck.check(_cp(["the fix lives in src/fix.py"]), str(project))
    assert stats["contradicted"] == 1


def test_check_file_exists_symlink_escape_is_skipped(monkeypatch, tmp_path):
    # A symlink whose target sits OUTSIDE the project root answers about
    # another tree — refuse rather than guess.
    project = tmp_path / "repo"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n")
    (project / "link").symlink_to(outside)
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["the fix lives in link/secret.py"]), str(project))
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "file-exists:skipped": 1}


# ---- #397 slice 2: branch-state claims --------------------------------------


def test_branch_claim_present_vocabulary():
    claim = worldcheck.branch_claim_of("work continues on branch feat/397-slice-2, unmerged")
    assert claim is not None
    assert claim.name == "feat/397-slice-2"
    assert claim.expected == frozenset({"EXISTS"})


def test_branch_claim_absent_vocabulary():
    claim = worldcheck.branch_claim_of("branch feat/old was deleted after the merge")
    assert claim is not None
    assert claim.name == "feat/old"
    assert claim.expected == frozenset({"MISSING"})


def test_branch_claim_requires_the_branch_keyword():
    # Without an explicit "branch" keyword any token could be a branch name.
    assert worldcheck.branch_claim_of("feat/397 is still unmerged") is None


def test_branch_claim_without_vocabulary_is_none():
    assert worldcheck.branch_claim_of("opened from branch feat/397") is None


def test_branch_claim_strips_backticks():
    claim = worldcheck.branch_claim_of("branch `feat/x` is still current")
    assert claim is not None
    assert claim.name == "feat/x"


def test_branch_claim_rejects_traversal_names():
    # A name that would resolve outside refs/heads is never probed.
    assert worldcheck.branch_claim_of("branch ../../etc/passwd is gone") is None
    assert worldcheck.branch_claim_of("branch feat/../../../etc is gone") is None


def test_branch_claim_long_input_completes():
    assert worldcheck.branch_claim_of("branch " + "a/b." * 12500) is None


def _git_repo(path, branches=(), packed=()):
    """A minimal on-disk .git the branch probe can read without git itself."""
    git = path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    for name in branches:
        ref = git / "refs" / "heads" / name
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text("0" * 40 + "\n")
    if packed:
        (git / "packed-refs").write_text(
            "# pack-refs with: peeled fully-peeled sorted\n"
            + "".join(f"{'0' * 40} refs/heads/{n}\n" for n in packed))
    return path


def test_check_branch_present_confirmed(monkeypatch, tmp_path):
    project = _git_repo(tmp_path / "repo", branches=["feat/397-slice-2"])
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "branch-state:confirmed": 1}


def test_check_branch_gone_contradicted(monkeypatch, tmp_path):
    project = _git_repo(tmp_path / "repo", branches=["main"])
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 0, "contradicted": 1, "skipped": 0,
                     "branch-state:contradicted": 1}
    assert cp["working_context"]["open_questions"][0]["_worldcheck"] == {
        "note": "branch feat/397-slice-2 gone", "status": "gone"}


def test_check_branch_found_in_packed_refs(monkeypatch, tmp_path):
    # A branch with no loose ref file is NOT gone — packed-refs is the other
    # half of git's ref storage, and missing it would fabricate a
    # contradiction on every freshly cloned repo.
    project = _git_repo(tmp_path / "repo", packed=["feat/397-slice-2"])
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    stats = worldcheck.check(cp, str(project))
    assert stats["confirmed"] == 1


def test_check_branch_outside_git_repo_is_skipped(monkeypatch, tmp_path):
    project = tmp_path / "plain"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "branch-state:skipped": 1}


def test_check_branch_dot_git_file_without_gitdir_pointer_is_skipped(
    monkeypatch, tmp_path
):
    project = tmp_path / "repo"
    project.mkdir()
    (project / ".git").write_text("something else entirely\n")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    assert worldcheck.check(cp, str(project))["skipped"] == 1


def test_check_branch_probe_follows_relative_worktree_gitdir(monkeypatch, tmp_path):
    # `gitdir:` may be recorded relative to the worktree, not absolute.
    main = _git_repo(tmp_path / "main", branches=["feat/397-slice-2"])
    tree = tmp_path / "wt"
    gitdir = tree / "nested" / "gd"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../../../main/.git\n")
    (tree / ".git").write_text("gitdir: nested/gd\n")
    assert main.exists()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    assert worldcheck.check(cp, str(tree))["confirmed"] == 1


def test_check_branch_probe_follows_worktree_gitdir(monkeypatch, tmp_path):
    # In a git worktree `.git` is a FILE pointing at the worktree's gitdir,
    # whose refs live in the COMMON dir. Reading the wrong one reports every
    # branch gone (the bench lane runs in worktrees — #267).
    main = _git_repo(tmp_path / "main", branches=["feat/397-slice-2"])
    common = main / ".git"
    wt_gitdir = common / "worktrees" / "wt"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")
    tree = tmp_path / "wt"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["work continues on branch feat/397-slice-2, unmerged"])
    stats = worldcheck.check(cp, str(tree))
    assert stats["confirmed"] == 1


# ---- #397 slice 2: dependency-version claims --------------------------------


def test_dep_claim_pinned_to():
    claim = worldcheck.dep_claim_of("requests is pinned to 2.31.0 for the retry fix")
    assert claim is not None
    assert claim.name == "requests"
    assert claim.version == "2.31.0"


def test_dep_claim_operator_form():
    claim = worldcheck.dep_claim_of("we ship rich==13.7.1 in the pretty extra")
    assert claim is not None
    assert claim.name == "rich"
    assert claim.version == "13.7.1"


def test_dep_claim_bumped_strips_v_prefix():
    claim = worldcheck.dep_claim_of("bumped pytest to v8.2")
    assert claim is not None
    assert claim.name == "pytest"
    assert claim.version == "8.2"


def test_dep_claim_future_tense_is_none():
    # "should bump X to 2.1" is a PROPOSAL, not a state claim.
    assert worldcheck.dep_claim_of("we should bump requests to 2.31.0") is None


def test_dep_claim_without_a_name_is_none():
    assert worldcheck.dep_claim_of("pinned to 2.1 for now") is None


def test_dep_claim_long_input_completes():
    # Scar 22: completion IS the signal — three patterns scan this text.
    assert worldcheck.dep_claim_of("x" * 50000) is None
    assert worldcheck.dep_claim_of("a.b-" * 12500) is None
    assert worldcheck.dep_claim_of("rich==13.7.1 " + "y" * 50000) is not None


def test_check_dependency_version_confirmed(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["requests is pinned to 2.31.0 for the retry fix"])
    stats = worldcheck.check(cp, str(project))
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "dependency-version:confirmed": 1}


def test_check_dependency_version_prefix_claim_is_satisfied(monkeypatch, tmp_path):
    # "pinned to 2.31" is satisfied by 2.31.0 — a coarser claim is not a
    # false one.
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["requests is pinned to 2.31"]), str(project))
    assert stats["confirmed"] == 1


def test_check_dependency_version_contradicted(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.4"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["requests is pinned to 2.31.0 for the retry fix"])
    stats = worldcheck.check(cp, str(project))
    assert stats["contradicted"] == 1
    assert cp["working_context"]["open_questions"][0]["_worldcheck"] == {
        "note": "requests 2.32.4 not 2.31.0", "status": "changed"}


def test_check_dependency_version_reads_package_json(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "package.json").write_text(
        '{"dependencies": {"react": "^18.3.1"}}\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["react is pinned to 18.3.1"]), str(project))
    assert stats["confirmed"] == 1


def test_check_dependency_version_lockfile_wins_over_manifest(monkeypatch, tmp_path):
    # uv.lock is ground truth; pyproject's range would otherwise read as a
    # second, conflicting answer and skip every real Python project.
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    (project / "pyproject.toml").write_text(
        '[project]\ndependencies = ["requests>=2.0"]\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["requests is pinned to 2.31.0"]), str(project))
    assert stats["confirmed"] == 1


def test_check_dependency_version_ambiguous_answer_is_skipped(monkeypatch, tmp_path):
    # Two different versions for one name in the SAME file (transitive
    # duplicates in a lockfile): don't guess — skip, like slice 1's
    # conflicting-vocabulary refusal.
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
        '[[package]]\nname = "requests"\nversion = "2.32.4"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["requests is pinned to 2.31.0"]), str(project))
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "dependency-version:skipped": 1}


def test_check_dependency_version_unknown_name_is_skipped(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["nosuchpkg is pinned to 1.0"]), str(project))
    assert stats["skipped"] == 1


def test_check_dependency_version_unreadable_manifest_is_skipped(
    monkeypatch, tmp_path
):
    # Patched rather than chmod'd: a permission test passes trivially when
    # the suite runs as root, which it does in some CI images.
    project = tmp_path / "repo"
    project.mkdir()
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.31.0"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)

    def _unreadable(self, *a, **k):
        raise OSError("nope")

    monkeypatch.setattr(worldcheck.Path, "read_text", _unreadable)
    stats = worldcheck.check(_cp(["requests is pinned to 2.31.0"]), str(project))
    assert stats["skipped"] == 1


def test_check_no_manifest_on_disk_is_skipped(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    stats = worldcheck.check(_cp(["requests is pinned to 2.31.0"]), str(project))
    assert stats["skipped"] == 1


# ---- #397 slice 2: shared budget, cap, and class priority -------------------


def test_pr_state_claim_wins_over_slice_2_classes():
    # Fixed priority: an item making a PR/issue claim keeps its slice-1
    # reading even when a path also appears in the text.
    cls, claim = worldcheck.claim_for("PR #60 awaiting review, lives in src/fix.py")
    assert cls == "pr-state"
    assert claim.num == "60"


def test_claim_for_returns_none_when_no_class_matches():
    assert worldcheck.claim_for("we talked about the retry semantics") is None


def test_shared_probe_cap_is_allocated_in_item_order(monkeypatch, tmp_path):
    # MAX_PROBES is aggregate across ALL classes (#397). Allocation follows
    # checkpoint order so no class is privileged; the 6th claim skips.
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    texts = [f"the fix lives in src/f{n}.py" for n in range(worldcheck.MAX_PROBES + 1)]
    stats = worldcheck.check(_cp(texts), str(project))
    assert stats["contradicted"] == worldcheck.MAX_PROBES == 5
    assert stats["skipped"] == 1
    assert stats["file-exists:skipped"] == 1


def test_exhausted_budget_skips_local_probes(monkeypatch, tmp_path):
    # The aggregate wall-clock budget bounds every class, not just `gh`.
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", -1.0)
    stats = worldcheck.check(_cp(["the fix lives in src/fix.py"]), str(project))
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "file-exists:skipped": 1}


def test_local_probes_survive_a_hanging_gh(monkeypatch, fake_gh, tmp_path):
    # Execution order protects the deterministic classes: a `gh` probe that
    # eats the whole budget must not starve a disk probe that costs nothing.
    project = tmp_path / "repo"
    project.mkdir()
    gh, _log = fake_gh("sleep 5\necho '{\"state\":\"OPEN\"}'")
    _enable_probes(monkeypatch, gh)
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", 0.2)
    cp = _cp(["PR #60 awaiting review", "the fix lives in src/fix.py"])
    stats = worldcheck.check(cp, str(project))
    assert stats["pr-state:skipped"] == 1
    assert stats["file-exists:contradicted"] == 1


def test_mixed_classes_report_per_class_counters(monkeypatch, tmp_path):
    project = _git_repo(tmp_path / "repo", branches=["feat/live"])
    (project / "src").mkdir()
    (project / "src" / "fix.py").write_text("x = 1\n")
    (project / "uv.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.4"\n')
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["the fix lives in src/fix.py",
              "branch feat/live is still current",
              "requests is pinned to 2.31.0"])
    stats = worldcheck.check(cp, str(project))
    assert stats["confirmed"] == 2
    assert stats["contradicted"] == 1
    assert stats["file-exists:confirmed"] == 1
    assert stats["branch-state:confirmed"] == 1
    assert stats["dependency-version:contradicted"] == 1


def test_probe_failure_in_one_class_never_raises(monkeypatch, tmp_path):
    # Silent skip on ANY probe failure — the briefing is never blocked.
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)

    def _boom(*a, **k):
        raise RuntimeError("disk exploded")

    monkeypatch.setattr(worldcheck, "_probe_path", _boom)
    stats = worldcheck.check(_cp(["the fix lives in src/fix.py"]), str(project))
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "file-exists:skipped": 1}


def test_check_rejects_a_torn_checkpoint_or_missing_project():
    # Fail-safe guard: nothing to iterate and nowhere to probe FROM.
    zero = {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert worldcheck.check(None, "/p/A") == zero
    assert worldcheck.check(_cp(["PR #60 awaiting review"]), "") == zero


def test_slice_2_claims_are_carried_only(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    project.mkdir()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    cp = _cp(["the fix lives in src/fix.py"], carried=False)
    assert worldcheck.check(cp, str(project)) == {
        "confirmed": 0, "contradicted": 0, "skipped": 0}


# ---- rendering: ADDED lines only --------------------------------------------


def test_line_renders_worldcheck_flag_with_confirm_reject():
    item = {"text": "PR #60 awaiting review", "trust": "inferred", "id": "o-abc123",
            "carried_from": "S-prev",
            "_worldcheck": {"note": "#60 merged", "status": "merged"}}
    line = briefing._line(item)
    # The pre-existing pinned prefix is untouched — the flag is an ADDED line.
    assert line.startswith("- [~ inferred] PR #60 awaiting review [carried]")
    assert "⚠ state changed since capture: #60 merged" in line
    assert "confirm: daimon resolve o-abc123 --status merged" in line
    assert "reject: daimon reverify o-abc123" in line


def test_line_without_id_renders_flag_only():
    item = {"text": "PR #60 awaiting review", "trust": "inferred",
            "carried_from": "S-prev",
            "_worldcheck": {"note": "#60 merged", "status": "merged"}}
    line = briefing._line(item)
    assert "⚠ state changed since capture: #60 merged" in line
    assert "daimon resolve" not in line
    assert "daimon reverify" not in line


def test_line_unstamped_item_byte_identical():
    item = {"text": "PR #60 awaiting review", "trust": "inferred",
            "carried_from": "S-prev"}
    assert briefing._line(item) == "- [~ inferred] PR #60 awaiting review [carried]"


def test_line_renders_slice_2_stamps_through_the_same_surface():
    # #397: new claim classes stamp the SAME {note,status} shape, so the
    # render path needs no per-class knowledge.
    item = {"text": "the fix lives in src/fix.py", "trust": "inferred",
            "id": "o-abc123", "carried_from": "S-prev",
            "_worldcheck": {"note": "src/fix.py missing", "status": "missing"}}
    line = briefing._line(item)
    assert "⚠ state changed since capture: src/fix.py missing" in line
    assert "confirm: daimon resolve o-abc123 --status missing" in line
    assert "reject: daimon reverify o-abc123" in line


# ---- config flag ------------------------------------------------------------


def test_worldcheck_flag_default_off(monkeypatch):
    monkeypatch.delenv("DAIMON_WORLDCHECK", raising=False)
    assert config.worldcheck_enabled() is False


def test_worldcheck_flag_opt_in(monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    assert config.worldcheck_enabled() is True


# ---- CLI wiring -------------------------------------------------------------


def _write_claim_checkpoint(project="/p/A"):
    cp = _cp(["PR #60 awaiting review", "issue #12 still open"])
    store.write_checkpoint("S-now", cp, project_dir=project)
    return cp


def _usage_lines(tmp_path):
    log = tmp_path / ".daimon" / "logs" / "usage.log"
    if not log.exists():
        return []
    return log.read_text().splitlines()


def test_cli_brief_flag_off_never_probes(monkeypatch, tmp_path, capsys):
    _write_claim_checkpoint()
    monkeypatch.delenv("DAIMON_WORLDCHECK", raising=False)

    def _boom(*a, **k):
        raise AssertionError("worldcheck.check must not run with the flag off")

    monkeypatch.setattr(worldcheck, "check", _boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "state changed since capture" not in out
    assert not any("worldcheck" in ln for ln in _usage_lines(tmp_path))


def test_cli_brief_flag_on_flags_contradiction_and_counts(
    monkeypatch, tmp_path, capsys, fake_gh, proj
):
    # A real project dir: probes spawn with cwd=<project>, so it must exist.
    _write_claim_checkpoint(project=proj)
    body = (
        'case "$*" in\n'
        "  *\"pr view 60\"*) echo '{\"state\":\"MERGED\"}' ;;\n"
        "  *) echo '{\"state\":\"OPEN\"}' ;;\n"
        "esac"
    )
    gh, _log = fake_gh(body)
    _enable_probes(monkeypatch, gh)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", proj)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "⚠ state changed since capture: #60 merged" in out
    assert "confirm: daimon resolve o-000001 --status merged" in out
    assert "reject: daimon reverify o-000001" in out
    usage = _usage_lines(tmp_path)
    assert sum(1 for ln in usage if ln.endswith(" worldcheck:contradicted")) == 1
    assert sum(1 for ln in usage if ln.endswith(" worldcheck:confirmed")) == 1
    # #397: the aggregate counters keep their slice-1 meaning AND every
    # outcome also lands under its class, so the fires-true rate is
    # measurable per class before any further expansion.
    assert sum(
        1 for ln in usage if ln.endswith(" worldcheck:pr-state:contradicted")) == 1
    assert sum(
        1 for ln in usage if ln.endswith(" worldcheck:pr-state:confirmed")) == 1


def test_cli_brief_emits_per_class_counters_for_slice_2(
    monkeypatch, tmp_path, capsys
):
    project = tmp_path / "repo"
    project.mkdir()
    cp = _cp(["the fix lives in src/fix.py"])
    store.write_checkpoint("S-now", cp, project_dir=str(project))
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(project))
    rc = cli.main(["brief"])
    assert rc == 0
    assert "⚠ state changed since capture: src/fix.py missing" in capsys.readouterr().out
    usage = _usage_lines(tmp_path)
    assert sum(
        1 for ln in usage if ln.endswith(" worldcheck:file-exists:contradicted")) == 1
    assert sum(1 for ln in usage if ln.endswith(" worldcheck:contradicted")) == 1


def test_cli_brief_slug_path_never_probes(monkeypatch, capsys):
    _write_claim_checkpoint(project="/p/A")
    slug = store.project_slug("/p/A")
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")

    def _boom(*a, **k):
        raise AssertionError("--slug briefs must never probe (wrong repo context)")

    monkeypatch.setattr(worldcheck, "check", _boom)
    rc = cli.main(["brief", "--slug", slug])
    assert rc == 0
    assert "state changed since capture" not in capsys.readouterr().out


def test_cli_brief_global_fallback_never_probes(monkeypatch, capsys):
    # Global pointer belongs to ANOTHER project — probing this cwd's repo
    # against that checkpoint's claims would answer for the wrong repo.
    cp = _cp(["PR #60 awaiting review"])
    store.write_checkpoint("S-other", cp)  # global pointer only
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/never-seen")

    def _boom(*a, **k):
        raise AssertionError("global-fallback briefs must never probe")

    monkeypatch.setattr(worldcheck, "check", _boom)
    rc = cli.main(["brief"])
    assert rc == 0
    assert "state changed since capture" not in capsys.readouterr().out


def test_cli_brief_worldcheck_failure_is_silent(monkeypatch, capsys):
    # A broken worldcheck must never take the briefing down (fail-open).
    _write_claim_checkpoint()
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")

    def _boom(*a, **k):
        raise RuntimeError("probe machinery exploded")

    monkeypatch.setattr(cli.worldcheck, "check", _boom)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #60" in out  # briefing still rendered
    assert "state changed since capture" not in out


def test_stats_surfaces_worldcheck_counters(monkeypatch, tmp_path, capsys):
    # Counters ride the existing usage.log -> `daimon stats` aggregation.
    log_dir = tmp_path / ".daimon" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "usage.log").write_text(
        "2026-07-21T00:00:00Z worldcheck:contradicted\n"
        "2026-07-21T00:00:01Z worldcheck:confirmed\n"
        "2026-07-21T00:00:02Z worldcheck:confirmed\n"
    )
    rc = cli.main(["stats", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    usage = data.get("usage") or {}
    assert usage.get("worldcheck:contradicted") == 1
    assert usage.get("worldcheck:confirmed") == 2
