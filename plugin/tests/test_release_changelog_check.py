"""scripts/release_changelog_check.py: every commit that release-please lists
must be in the release section, whatever its commit date (#1305).

release-please walks main newest-commit-date first and stops at the previous
release commit (``Manifest.buildPullRequests``, src/manifest.ts). PRs merge
with a merge commit, so a branch commit keeps the date it was written on. A
branch written before a release commit and merged after it sorts behind that
commit and is never reached. The repos below rebuild that history with real
git and explicit commit dates; no network, no rg.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import release_changelog_check as rcc  # noqa: E402


def _git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    proc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _commit(repo: Path, subject: str, date: str) -> str:
    _git(repo, "commit", "--allow-empty", "-m", subject, date=date)
    return _git(repo, "rev-parse", "HEAD")


def _day(n: int) -> str:
    return f"2026-11-{n:02d}T12:00:00+00:00"


@pytest.fixture
def lost_branch_repo(tmp_path: Path) -> dict[str, str]:
    """The 0.64.3 history: a fix written on day 2, a release commit on day 5
    (tagged v0.1.1), the branch merged on day 6 behind it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "chore: init", _day(1))
    _git(repo, "tag", "v0.1.0")
    _git(repo, "checkout", "-q", "-b", "late")
    late = _commit(repo, "fix(requests): require consent before an answer", _day(2))
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "chore(main): release 0.1.1", _day(5))
    _git(repo, "tag", "v0.1.1")
    _git(repo, "checkout", "-q", "-b", "recent")
    recent = _commit(repo, "fix(requests): start consent at the release", _day(6))
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge recent", "recent", date=_day(6))
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge late", "late", date=_day(7))
    return {"repo": str(repo), "late": late, "recent": recent}


def _section(*shas: str) -> str:
    lines = [f"* **x:** a change ([{s[:7]}](https://e/commit/{s}))" for s in shas]
    return (
        "## [0.1.2](https://e/compare/v0.1.1...v0.1.2) (2026-11-07)\n\n\n"
        "### Bug Fixes\n\n" + "\n".join(lines) + "\n\n"
        "## [0.1.1](https://e/compare/v0.1.0...v0.1.1) (2026-11-05)\n\n\n"
    )


def test_walk_by_date_loses_the_branch_but_ancestry_finds_it(lost_branch_repo):
    repo = Path(lost_branch_repo["repo"])
    # What release-please sees: newest date first, stop at the release commit.
    by_date = _git(repo, "log", "--format=%H", "--date-order", "HEAD").split()
    stop = by_date.index(_git(repo, "rev-parse", "v0.1.1"))
    assert lost_branch_repo["late"] not in by_date[:stop]
    # What the invariant asks for: reachable from head, not from the tag.
    found = [c.sha for c in rcc.releasable_commits(repo, "v0.1.1", "HEAD")]
    assert lost_branch_repo["late"] in found


def test_reports_the_commit_the_section_left_out(lost_branch_repo):
    repo = Path(lost_branch_repo["repo"])
    section = _section(lost_branch_repo["recent"])  # the 0.64.3 text
    missing = rcc.missing_from_section(repo, "v0.1.1", "HEAD", section)
    assert [c.sha for c in missing] == [lost_branch_repo["late"]]
    assert missing[0].subject.startswith("fix(requests): require consent")


def test_passes_when_every_commit_is_listed(lost_branch_repo):
    repo = Path(lost_branch_repo["repo"])
    section = _section(lost_branch_repo["recent"], lost_branch_repo["late"])
    assert rcc.missing_from_section(repo, "v0.1.1", "HEAD", section) == []


def test_main_exits_1_and_names_the_commit(lost_branch_repo, tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(_section(lost_branch_repo["recent"]))
    code = rcc.main(
        ["--repo", lost_branch_repo["repo"], "--changelog", str(changelog),
         "--head", "HEAD"]
    )  # fmt: skip
    out = capsys.readouterr()
    assert code == 1
    assert lost_branch_repo["late"][:7] in out.out + out.err
    assert "require consent" in out.out + out.err


def test_main_exits_0_when_complete(lost_branch_repo, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        _section(lost_branch_repo["recent"], lost_branch_repo["late"])
    )
    code = rcc.main(
        ["--repo", lost_branch_repo["repo"], "--changelog", str(changelog),
         "--head", "HEAD"]
    )  # fmt: skip
    assert code == 0


@pytest.mark.parametrize(
    ("subject", "listed"),
    [
        ("feat(a): x", True),
        ("fix: x", True),
        ("perf(a): x", True),
        ("revert: x", True),
        ("docs(a): x", True),
        ("refactor!: x", True),  # a breaking change is listed whatever the type
        ("refactor(a): x", False),
        ("chore(main): release 1.0.0", False),
        ("test: x", False),
        ("ci: x", False),
        ("build: x", False),
        ("style: x", False),
        ("Merge pull request #1 from o/b", False),
        ("not conventional", False),
    ],
)
def test_which_subjects_release_please_lists(subject, listed):
    assert rcc.is_listed_subject(subject) is listed


def test_latest_section_reads_version_and_previous_tag():
    version, prev, body = rcc.latest_section(_section("a" * 40))
    assert (version, prev) == ("0.1.2", "v0.1.1")
    assert "## [0.1.1]" not in body
    assert ("a" * 40) in body


def test_latest_section_without_a_header_is_an_error():
    with pytest.raises(ValueError):
        rcc.latest_section("# Changelog\n\nnothing here\n")
