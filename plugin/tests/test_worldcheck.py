"""#365 slice 1: deterministic external-state spot-check for carried PR/issue-
state claims at briefing render.

Contract pinned here:
- claim_of: conservative claim-class extraction — a repo-local "#N" ref PLUS a
  state word, never bare refs, never cross-repo refs, never conflicting words.
- check: read-only `gh` probes under a strict aggregate budget + probe cap;
  confirmed items untouched, contradicted items stamped with a transient
  `_worldcheck` annotation, everything else skipped SILENTLY.
- rendering: the stamped item gains an ADDED flag line reusing the existing
  confirm/reject command surface (`daimon resolve` / `daimon reverify`) —
  existing pinned literals never change.
- CLI wiring: DAIMON_WORLDCHECK opt-in (default OFF, byte-identical off-path),
  cross-project (--slug) and global-fallback briefs never probe, and outcomes
  land in usage.log as worldcheck:confirmed/contradicted/skipped.

All gh probes in this suite hit a fake `gh` script on disk — zero network.
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
    assert stats == {"confirmed": 1, "contradicted": 0, "skipped": 0}
    item = cp["working_context"]["open_questions"][0]
    assert "_worldcheck" not in item
    assert len(_calls(log)) == 1


def test_check_contradicted_stamps_item(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("echo '{\"state\":\"MERGED\",\"mergedAt\":\"2026-07-20T00:00:00Z\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 1, "skipped": 0}
    item = cp["working_context"]["open_questions"][0]
    assert item["_worldcheck"] == {"note": "#60 merged", "status": "merged"}


def test_check_gh_missing_skips_silently(monkeypatch):
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: None)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: True)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, "/p/A")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}
    assert "_worldcheck" not in cp["working_context"]["open_questions"][0]


def test_check_no_github_remote_skips_without_probing(monkeypatch, fake_gh):
    gh, log = fake_gh()
    monkeypatch.setattr(worldcheck, "_gh_path", lambda: gh)
    monkeypatch.setattr(worldcheck, "_github_repo", lambda project: False)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, "/p/A")
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}
    assert _calls(log) == []


def test_check_probe_failure_skips(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("exit 1")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}


def test_check_bad_json_skips(monkeypatch, fake_gh, proj):
    gh, _log = fake_gh("echo 'not json at all'")
    _enable_probes(monkeypatch, gh)
    stats = worldcheck.check(_cp(["PR #60 awaiting review"]), proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}


def test_check_unknown_state_vocabulary_skips(monkeypatch, fake_gh, proj):
    # Only OPEN/CLOSED/MERGED may reach the rendered flag (the note rides
    # into briefing text — bounded vocabulary, not a passthrough).
    gh, _log = fake_gh("echo '{\"state\":\"WEIRD\"}'")
    _enable_probes(monkeypatch, gh)
    cp = _cp(["PR #60 awaiting review"])
    stats = worldcheck.check(cp, proj)
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}
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
    assert stats == {"confirmed": 0, "contradicted": 0, "skipped": 1}


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
