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

#439 slice 3 adds receipt-validity, the first class whose subject is daimon's
OWN artifact: a carried item's ORIGIN checkpoint verified through the vitni CLI
(full signature + structure + binding, not the cheap byte match the briefing
already does). It samples ONE origin per day by rotation, and a FAILED
verification both flags the item and feeds the #376 rejection ledger — written
by the CLI, never by worldcheck, which still writes nothing to disk.

All gh probes in this suite hit a fake `gh` script on disk — zero network.
Slice 2's probes touch only tmp_path trees — zero network, zero subprocess.
Slice 3's probes hit the shared fake vitni CLI (tests/conftest.py) — a local
subprocess, still zero network.
"""

import json
import subprocess
import time

import pytest

from daimon_briefing import briefing, cli, config, receipts, store, worldcheck


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


@pytest.fixture(autouse=True)
def _generous_worldcheck_budget(monkeypatch):
    """#718: `check()`'s aggregate wall-clock budget (BUDGET_SECONDS, 0.8s in
    production) races REAL elapsed time — under a loaded or coverage-
    instrumented run, a probe that would ordinarily return well inside the
    budget can still get killed mid-flight by `_run_probes`'s deadline check,
    failing a test for a reason that has nothing to do with the semantics
    under test (observed: test_check_dedup_same_ref_probes_once_stamps_all).

    Every test in this module gets an effectively-infinite budget by
    default, so the deadline can never bind here. Tests that deliberately
    exercise the deadline CONTRACT ITSELF (search this file for
    `BUDGET_SECONDS`) set their own small value or a negative
    already-exhausted one inside the test body — that call simply overrides
    this fixture's default, the same monkeypatch-layering every other
    per-test override in this suite already relies on."""
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", 300.0)


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


def test_check_confirmed_stamps_ground_not_contradiction(monkeypatch, fake_gh, proj):
    # #525: a confirmation earns a quiet transient stamp so the render can
    # mark solid ground; the contradiction surface stays absent.
    gh, log = fake_gh("echo '{\"state\":\"OPEN\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "pr-state:confirmed": 1}
    item = cp["working_context"]["open_questions"][0]
    assert "_worldcheck" not in item
    assert item["_worldcheck_confirmed"] is True
    assert len(_calls(log)) == 1


def test_check_contradicted_never_stamps_ground(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("echo '{\"state\":\"MERGED\",\"mergedAt\":\"2026-07-20T00:00:00Z\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    worldcheck.check(cp, proj)
    item = cp["working_context"]["open_questions"][0]
    assert "_worldcheck_confirmed" not in item


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
    # branch gone for anyone working out of a worktree.
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


# ---- #439 slice 3: receipt-validity -----------------------------------------
#
# The first class whose subject is daimon's OWN artifact rather than the world
# around it: a carried item's ORIGIN checkpoint is verified through the vitni
# CLI (full signature + structure + binding, not just the cheap byte match the
# briefing already does). Everything the slice-1 contract promised still holds
# — one aggregate budget, one shared probe cap, silent skip on any failure,
# nothing written to disk from inside worldcheck.


def _cp_origins(origins, texts=None):
    """Checkpoint whose carried open_questions each name an origin session.
    `texts` defaults to a non-claim text per item, so the only claim available
    is the receipt one (the classes stay independently observable)."""
    texts = texts or [f"note {i}" for i in range(len(origins))]
    cp = _cp(texts)
    for item, origin in zip(cp["working_context"]["open_questions"], origins):
        if origin is not None:
            item["origin_session"] = origin
    return cp


def _two_axis_check(proj, monkeypatch, fake_gh, *, gh_state,
                    vitni_verdict=None, n=1):
    """Run check() over `n` carried items that each carry BOTH a text claim
    (PR #6<i> awaiting review — distinct probe targets) and a
    receipt-validity claim on ONE shared signed origin. The caller's
    `receipts_on` fixture supplies the vitni side; `gh_state` drives the
    text-claim probes and `vitni_verdict` (None = valid) the receipt one.
    Returns (stats, checkpoint). With n >= 6 the probe plan fills at
    MAX_PROBES before items 5+ plant their text probes — the #833
    starvation shape."""
    if vitni_verdict is not None:
        monkeypatch.setenv("FAKE_VITNI_VERDICT", vitni_verdict)
    gh, _log = fake_gh(f"echo '{{\"state\":\"{gh_state}\"}}'")
    _enable_probes(monkeypatch, gh)
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"] * n,
                     texts=[f"PR #6{i} awaiting review" for i in range(n)])
    return worldcheck.check(cp, proj), cp


def _pubkey_dir(tmp_path, monkeypatch):
    """A keys dir holding only the cached PUBLIC key — worldcheck verifies, it
    never signs, so the seed is deliberately absent."""
    kdir = tmp_path / "keys"
    kdir.mkdir(exist_ok=True)
    (kdir / "signing.pub.json").write_text(json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg",
         "alg": "EdDSA", "status": "active"}))
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(kdir))
    return kdir


def _signed_origin(session, project, *, sidecar=True, tamper=False,
                   receipts_era=True, slug=None):
    """Write a receipt-era origin checkpoint (+ its `.receipt` sidecar) exactly
    where store/receipts expect them. `tamper=True` signs OTHER bytes, which is
    what an edited-after-signing artifact looks like from brief time."""
    cp = {"session_id": session,
          "project_slug": slug if slug is not None else store.project_slug(project),
          "working_context": {}, "epistemic_snapshot": {}}
    if receipts_era:
        cp["receipts"] = True
    path = config.checkpoint_dir() / f"{session}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cp, indent=2), encoding="utf-8")
    if sidecar:
        signed = b"other bytes entirely" if tamper else path.read_bytes()
        # The JWS is session-derived so a test can tell WHICH origin was
        # verified from the CLI capture alone.
        path.with_suffix(".receipt").write_text(json.dumps({
            "jws": f"{session}.bbb.ccc",
            "receipt": {"outputs_hash": receipts._multibase_sha256(signed)},
            "kid": "daimon-1", "performer_id": "tester"}))
    return path


@pytest.fixture
def receipts_on(tmp_path, monkeypatch, fake_cli):
    """Receipt-validity fully armed: feature flag on, a fake vitni CLI on
    DAIMON_VITNI_CLI, a cached public key. Returns the CLI capture path — its
    NON-existence is how a test proves no subprocess ever ran."""
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    _pubkey_dir(tmp_path, monkeypatch)
    return fake_cli


def _vitni_calls(capture):
    if not capture.exists():
        return []
    return [json.loads(x) for x in capture.read_text().splitlines() if x.strip()]


def test_receipt_valid_confirms_and_leaves_item_untouched(receipts_on, proj):
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "receipt-validity:confirmed": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    calls = _vitni_calls(receipts_on)
    assert [c["cmd"] for c in calls] == ["verify"]


def test_receipt_cli_verify_payload_matches_the_receipts_contract(receipts_on, proj):
    # Same subcommand and same stdin shape `receipts._verify_receipt` builds —
    # only the runner differs (Popen under worldcheck's deadline instead of a
    # blocking subprocess.run with a 10s timeout).
    _signed_origin("S-origin", proj)
    worldcheck.check(_cp_origins(["S-origin"]), proj)
    sent = json.loads(_vitni_calls(receipts_on)[0]["stdin"])
    assert sent["signed_receipt"] == "S-origin.bbb.ccc"
    assert sent["policy"] == {"expected_binding": "local",
                              "expected_method": receipts.METHOD}
    assert list(sent["keys"]) == ["tester"]
    assert list(sent["keys"]["tester"]) == ["daimon-1"]


def test_receipt_cli_rejection_stamps_and_feeds_the_ledger(
    receipts_on, proj, monkeypatch
):
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "signature_invalid")
    cp = _cp_origins(["S-origin"])
    _signed_origin("S-origin", proj)
    stats = worldcheck.check(cp, proj)
    assert stats["contradicted"] == 1
    assert stats["receipt-validity:contradicted"] == 1
    item = cp["working_context"]["open_questions"][0]
    # Bounded literals only: the note rides into briefing output and the
    # hook-injected LLM context, so no session id and no CLI text may reach it.
    assert item["_worldcheck"] == {
        "note": "signed receipt failed verification", "status": "unverified"}
    assert "S-origin" not in item["_worldcheck"]["note"]
    assert stats[worldcheck.LEDGER_KEY] == [("o-000001", "receipt", "receipt-invalid")]


def test_receipt_tampered_bytes_flag_without_any_subprocess(receipts_on, proj):
    # Phase (a) is FREE (sidecar + sha256, no exec). A byte mismatch is already
    # a terminal answer, so the CLI must never be spawned for it.
    _signed_origin("S-origin", proj, tamper=True)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats["receipt-validity:contradicted"] == 1
    assert cp["working_context"]["open_questions"][0]["_worldcheck"] == {
        "note": "signed receipt no longer matches checkpoint bytes",
        "status": "unverified"}
    assert stats[worldcheck.LEDGER_KEY] == [("o-000001", "receipt", "receipt-tampered")]
    assert _vitni_calls(receipts_on) == []


def test_receipt_era_origin_with_no_sidecar_is_tampered(receipts_on, proj):
    # Marked receipt-era but the receipt is gone: removed or lost, and
    # provenance cannot be confirmed — the same verdict verify-receipt gives.
    _signed_origin("S-origin", proj, sidecar=False)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats["receipt-validity:contradicted"] == 1
    assert _vitni_calls(receipts_on) == []


def test_receipt_pre_receipt_origin_skips_silently(receipts_on, proj):
    # An origin written before receipts existed has nothing to verify. It is a
    # SKIP, not a contradiction — no retroactive downgrades (#204).
    _signed_origin("S-origin", proj, sidecar=False, receipts_era=False)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    assert _vitni_calls(receipts_on) == []


def test_receipt_item_without_origin_session_makes_no_claim(receipts_on, proj):
    # Pre-#268 carried items carry no origin binding — nothing to check, and
    # nothing to count either (the same bar a bare "#48" mention meets).
    stats = worldcheck.check(_cp_origins([None]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _vitni_calls(receipts_on) == []


def test_receipt_feature_off_makes_no_claim(receipts_on, proj, monkeypatch):
    monkeypatch.delenv("DAIMON_RECEIPTS", raising=False)
    _signed_origin("S-origin", proj)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _vitni_calls(receipts_on) == []


def test_receipt_gc_d_origin_makes_no_claim(receipts_on, proj):
    # A GC'd origin is a NORMAL skip: the store keeps a bounded number of
    # per-session files, so an old origin being gone says nothing about the
    # claim. Never a contradiction.
    stats = worldcheck.check(_cp_origins(["S-gone"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _vitni_calls(receipts_on) == []


def test_receipt_foreign_project_origin_makes_no_claim(receipts_on, proj):
    # A checkpoint that exists but belongs elsewhere cannot vouch for a claim
    # here — capture._origin_on_disk's rule, reused verbatim.
    _signed_origin("S-origin", proj, slug="some-other-project")
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _vitni_calls(receipts_on) == []


def test_receipt_missing_cli_skips_silently(receipts_on, proj, monkeypatch):
    monkeypatch.setenv("DAIMON_VITNI_CLI", "vitni-verify-that-does-not-exist")
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_receipt_missing_pubkey_skips_silently(receipts_on, proj, tmp_path,
                                               monkeypatch):
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(tmp_path / "no-keys-here"))
    _signed_origin("S-origin", proj)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert _vitni_calls(receipts_on) == []


def test_receipt_garbage_cli_output_skips(receipts_on, proj, monkeypatch):
    # Unparseable output is not a rejection: a broken verifier must never be
    # read as "this receipt is bad" (don't-guess bias, the module's stance).
    monkeypatch.setenv("FAKE_VITNI_MODE", "garbage")
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_receipt_nonzero_rc_skips(receipts_on, proj, monkeypatch):
    monkeypatch.setenv("FAKE_VITNI_MODE", "rc1")
    _signed_origin("S-origin", proj)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}


def test_receipt_hanging_cli_is_killed_at_the_deadline(receipts_on, proj,
                                                       monkeypatch):
    # receipts._run_cli would wait _CLI_TIMEOUT=10s — twelve times the WHOLE
    # worldcheck budget. Under worldcheck's own deadline machinery it is killed
    # and skipped, and the render is never blocked.
    monkeypatch.setenv("FAKE_VITNI_MODE", "hang")
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", 0.3)
    _signed_origin("S-origin", proj)
    start = time.monotonic()
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert time.monotonic() - start < 3.0
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}


def test_receipt_probes_one_origin_per_day_by_rotation(receipts_on, proj,
                                                       monkeypatch):
    # Day-bucket ROTATION, not a hash-modulo gate: a sha1(origin) % N scheme
    # leaves some origins deterministically never sampled (permanent blind
    # spots), while rotation covers every origin over consecutive days.
    _signed_origin("S-a", proj)
    _signed_origin("S-b", proj)
    monkeypatch.setattr(worldcheck, "_day_bucket", lambda: 4)  # 4 % 2 == 0
    worldcheck.check(_cp_origins(["S-a", "S-b"]), proj)
    assert len(_vitni_calls(receipts_on)) == 1
    monkeypatch.setattr(worldcheck, "_day_bucket", lambda: 5)  # 5 % 2 == 1
    stats = worldcheck.check(_cp_origins(["S-a", "S-b"]), proj)
    assert len(_vitni_calls(receipts_on)) == 2
    # One probe per brief either way: the other origin makes no claim at all.
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0,
                     "receipt-validity:confirmed": 1}


def test_receipt_rotation_reaches_every_origin_over_days(receipts_on, proj,
                                                         monkeypatch):
    _signed_origin("S-a", proj)
    _signed_origin("S-b", proj)
    _signed_origin("S-c", proj)
    for day in range(3):
        monkeypatch.setattr(worldcheck, "_day_bucket", lambda d=day: d)
        stats = worldcheck.check(_cp_origins(["S-a", "S-b", "S-c"]), proj)
        assert stats["receipt-validity:confirmed"] == 1  # exactly one per day
    # Three consecutive days, three distinct origins verified: no origin is
    # left permanently unsampled, which is the whole point of rotation.
    sent = {json.loads(c["stdin"])["signed_receipt"] for c in
            _vitni_calls(receipts_on)}
    assert sent == {"S-a.bbb.ccc", "S-b.bbb.ccc", "S-c.bbb.ccc"}


def test_receipt_one_probe_stamps_every_item_sharing_that_origin(
    receipts_on, proj, monkeypatch
):
    # Dedup by origin, same as slice 1 dedups by ref: ONE probe, every claim
    # naming that target scored against it.
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "signature_invalid")
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin", "S-origin"])
    stats = worldcheck.check(cp, proj)
    assert len(_vitni_calls(receipts_on)) == 1
    assert stats["receipt-validity:contradicted"] == 2
    assert len(stats[worldcheck.LEDGER_KEY]) == 2
    for item in cp["working_context"]["open_questions"]:
        assert item["_worldcheck"]["status"] == "unverified"


def test_receipt_never_overwrites_a_text_class_stamp(receipts_on, proj,
                                                     monkeypatch, fake_gh):
    # An item can carry BOTH a text claim and a receipt claim. The text stamp
    # is the more actionable one (it names what changed and drives a `daimon
    # resolve --status`), so it WINS — but the receipt outcome still counts and
    # still reaches the rejection ledger.
    stats, cp = _two_axis_check(proj, monkeypatch, fake_gh, gh_state="MERGED",
                                vitni_verdict="signature_invalid")
    item = cp["working_context"]["open_questions"][0]
    assert item["_worldcheck"] == {"note": "#60 merged", "status": "merged"}
    assert stats["pr-state:contradicted"] == 1
    assert stats["receipt-validity:contradicted"] == 1
    assert stats[worldcheck.LEDGER_KEY] == [("o-000001", "receipt", "receipt-invalid")]


def test_two_axis_item_counts_once_in_the_aggregates(receipts_on, proj,
                                                     monkeypatch, fake_gh):
    """#830: the three aggregates are counted per ITEM (the docstring's
    promise, and what cli's usage counters emit under), so an item carrying
    BOTH a text claim and a receipt-validity claim increments them once —
    while the per-class keys keep the per-claim detail."""
    stats, _cp_out = _two_axis_check(proj, monkeypatch, fake_gh,
                                     gh_state="MERGED",
                                     vitni_verdict="signature_invalid")
    assert (stats["confirmed"] + stats["contradicted"] + stats["skipped"]) == 1
    assert stats["contradicted"] == 1
    assert stats["pr-state:contradicted"] == 1
    assert stats["receipt-validity:contradicted"] == 1


def test_two_axis_rollup_contradiction_takes_precedence(receipts_on, proj,
                                                        monkeypatch, fake_gh):
    """#833 precedence rollup (supersedes the #832 first-outcome pin): a
    text-confirmed, receipt-contradicted item aggregates as CONTRADICTED —
    the aggregate agrees with the surface the item renders with, since a
    contradiction on any axis outranks the ground marker at render time.
    Per-claim surfaces are unchanged: per-class keys, both stamps, and the
    ledger row all stay."""
    stats, cp = _two_axis_check(proj, monkeypatch, fake_gh, gh_state="OPEN",
                                vitni_verdict="signature_invalid")
    assert stats["contradicted"] == 1
    assert stats["confirmed"] == 0
    assert stats["pr-state:confirmed"] == 1
    assert stats["receipt-validity:contradicted"] == 1
    assert stats[worldcheck.LEDGER_KEY] == [
        ("o-000001", "receipt", "receipt-invalid")]
    item = cp["working_context"]["open_questions"][0]
    assert item["_worldcheck_confirmed"] is True
    assert item["_worldcheck"]["status"] == "unverified"


def test_starved_text_claim_does_not_swallow_a_receipt_contradiction(
        receipts_on, proj, monkeypatch, fake_gh):
    """#833: six items share one origin, so the probe plan fills at
    MAX_PROBES (items 1-4's text probes + the shared receipt key) and items
    5-6's text claims starve to "skipped" — while the shared receipt answer
    still lands and comes back invalid. Those items are stamped, rendered,
    and ledgered as contradicted; the aggregate must agree, never count
    them under the starved axis."""
    stats, _cp_out = _two_axis_check(proj, monkeypatch, fake_gh,
                                     gh_state="OPEN",
                                     vitni_verdict="signature_invalid", n=6)
    assert stats["pr-state:confirmed"] == 4
    assert stats["pr-state:skipped"] == 2          # the starved text claims
    assert stats["receipt-validity:contradicted"] == 6
    assert stats["contradicted"] == 6
    assert stats["confirmed"] == 0
    assert stats["skipped"] == 0


def test_starved_text_claim_does_not_swallow_a_receipt_confirmation(
        receipts_on, proj, monkeypatch, fake_gh):
    """#833 mirror case: text starved to "skipped", receipt confirmed — the
    item carries the solid-ground stamp, so the aggregate says confirmed,
    never skipped."""
    stats, cp = _two_axis_check(proj, monkeypatch, fake_gh, gh_state="OPEN",
                                n=6)
    assert stats["pr-state:skipped"] == 2
    assert stats["receipt-validity:confirmed"] == 6
    assert stats["confirmed"] == 6
    assert stats["skipped"] == 0
    for item in cp["working_context"]["open_questions"]:
        assert item["_worldcheck_confirmed"] is True


def test_single_axis_aggregates_are_unchanged(receipts_on, proj):
    """#830 regression guard: items with ONE claim keep counting exactly as
    before the per-item rollup."""
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin", "S-origin"])
    stats = worldcheck.check(cp, proj)
    assert (stats["confirmed"], stats["contradicted"], stats["skipped"]) \
        == (2, 0, 0)
    assert stats["receipt-validity:confirmed"] == 2


def test_receipt_claims_are_carried_only(receipts_on, proj):
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    for item in cp["working_context"]["open_questions"]:
        item.pop("carried_from")
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert _vitni_calls(receipts_on) == []


def test_receipt_ledger_key_absent_when_nothing_failed(receipts_on, proj):
    # The reserved key only appears when there is something to write — the
    # counter dict the CLI iterates stays all-ints on the happy path.
    _signed_origin("S-origin", proj)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert worldcheck.LEDGER_KEY not in stats


def test_receipt_idless_item_yields_no_ledger_row(receipts_on, proj, monkeypatch):
    # A ledger entry nobody can trace back is noise (serializer's rule).
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "signature_invalid")
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    cp["working_context"]["open_questions"][0].pop("id")
    stats = worldcheck.check(cp, proj)
    assert stats["receipt-validity:contradicted"] == 1
    assert worldcheck.LEDGER_KEY not in stats


def test_receipt_check_writes_nothing_to_disk(receipts_on, proj, monkeypatch):
    # worldcheck's hardest constraint: transient stamps only. The ledger write
    # is the CLI's job, precisely so this stays true.
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "signature_invalid")
    _signed_origin("S-origin", proj)

    def _boom(*a, **k):
        raise AssertionError("worldcheck must never write to the ledger itself")

    monkeypatch.setattr(store, "append_verification", _boom)
    stats = worldcheck.check(_cp_origins(["S-origin"]), proj)
    assert stats["receipt-validity:contradicted"] == 1


# ---- #439 fail-open branches ------------------------------------------------
#
# Every branch below is a SILENT SKIP by contract, which is exactly why each
# needs its own test: a fail-open `except` that quietly returns the wrong
# answer looks identical to one that works. The assertions pin the OUTCOME
# (all-zeros or a skipped counter, never a stamp, never a raise), not merely
# that nothing escaped.


def test_receipt_unknown_project_slug_makes_no_claim(receipts_on):
    # An unnameable project cannot be compared against an origin's
    # `project_slug`, so eligibility is unanswerable and nothing is claimed.
    # store.project_slug answers None for a blank dir — the same "unknown
    # project" the ledger and event writers refuse to write for.
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, "   ")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    assert _vitni_calls(receipts_on) == []


def test_receipt_eligibility_scan_stops_at_an_exhausted_budget(
    receipts_on, proj, monkeypatch
):
    # The scan reads one checkpoint per DISTINCT origin, so it runs UNDER the
    # aggregate deadline like every probe. An already-spent budget must stop
    # it before the first read, not merely discard the result afterwards.
    _signed_origin("S-a", proj)
    _signed_origin("S-b", proj)
    monkeypatch.setattr(worldcheck, "BUDGET_SECONDS", -1.0)

    def _boom(*a, **k):
        raise AssertionError("an exhausted budget must stop the scan cold")

    monkeypatch.setattr(store, "read_checkpoint", _boom)
    cp = _cp_origins(["S-a", "S-b"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 0}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    assert _vitni_calls(receipts_on) == []


def test_receipt_origin_gc_d_between_collection_and_probe_skips(
    receipts_on, proj, monkeypatch
):
    # Eligibility and the probe are two separate reads, and GC runs on its own
    # schedule between them. A checkpoint that was there at collection and
    # gone at probe time is a SKIP: its absence says nothing about the claim.
    _signed_origin("S-origin", proj)
    real = store.read_checkpoint
    calls = []

    def _vanishing(session_id):
        calls.append(session_id)
        return real(session_id) if len(calls) == 1 else None

    monkeypatch.setattr(store, "read_checkpoint", _vanishing)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert len(calls) == 2  # once for eligibility, once at probe time
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    assert _vitni_calls(receipts_on) == []


def test_receipt_phase_a_failure_skips_without_spawning(receipts_on, proj,
                                                        monkeypatch):
    # Phase (a) is a file read plus a sha256, and either can fail on a torn
    # store. A raise there must not be read as tampering and must not fall
    # through to the CLI — an unanswerable check is a skip, not a verdict.
    _signed_origin("S-origin", proj)

    def _boom(_checkpoint):
        raise OSError("store went away mid-read")

    monkeypatch.setattr(receipts, "verbatim_degraded", _boom)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    assert _vitni_calls(receipts_on) == []


def test_receipt_unwritable_stdin_skips_without_leaking_the_payload(
    receipts_on, proj, monkeypatch
):
    # The child can die between spawn and write (a killed verifier, a full
    # pipe on a wedged host). The write raises, the probe answers nothing, and
    # the briefing renders — the payload simply never arrives.
    _signed_origin("S-origin", proj)
    real_popen = worldcheck.subprocess.Popen

    class _DeadStdin:
        def write(self, _payload):
            raise BrokenPipeError("child already gone")

        def close(self):
            raise AssertionError("close is unreachable once write raised")

    def _popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        if proc.stdin is not None:
            proc.stdin.close()  # hand the child EOF; the fake replaces the handle
            proc.stdin = _DeadStdin()
        return proc

    monkeypatch.setattr(worldcheck.subprocess, "Popen", _popen)
    cp = _cp_origins(["S-origin"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1,
                     "receipt-validity:skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]
    # The CLI ran but received NOTHING — proof the failure was the write, not
    # a rejection the verifier actually reached a conclusion about.
    assert [c["stdin"] for c in _vitni_calls(receipts_on)] == [""]


# ---- #439 CLI wiring --------------------------------------------------------


def _verification_rows(project):
    path = config.checkpoint_dir() / store.project_slug(project) / "verification.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def test_cli_brief_receipt_failure_appends_a_verification_row(
    monkeypatch, tmp_path, capsys, receipts_on, proj
):
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "signature_invalid")
    _signed_origin("S-origin", proj)
    cp = _cp_origins(["S-origin"])
    store.write_checkpoint("S-now", cp, project_dir=proj)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", proj)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "⚠ state changed since capture: signed receipt failed verification" in out
    rows = _verification_rows(proj)
    assert len(rows) == 1
    assert rows[0]["check"] == "receipt"
    assert rows[0]["reason"] == "receipt-invalid"
    assert rows[0]["item_ref"] == "o-000001"
    # A POINTER and a REASON CODE, never the item's text (#376).
    assert "note 0" not in json.dumps(rows[0])
    usage = _usage_lines(tmp_path)
    assert sum(
        1 for ln in usage if ln.endswith(" worldcheck:receipt-validity:contradicted")) == 1
    assert sum(1 for ln in usage if ln.endswith(" worldcheck:contradicted")) == 1


def test_cli_brief_receipt_confirmed_writes_no_verification_row(
    monkeypatch, tmp_path, capsys, receipts_on, proj
):
    _signed_origin("S-origin", proj)
    store.write_checkpoint("S-now", _cp_origins(["S-origin"]), project_dir=proj)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", proj)
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    assert _verification_rows(proj) == []
    usage = _usage_lines(tmp_path)
    assert sum(
        1 for ln in usage if ln.endswith(" worldcheck:receipt-validity:confirmed")) == 1
