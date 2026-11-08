#!/usr/bin/env python3
"""Fail when a release section leaves out a commit the release contains.

Invariant: every commit reachable from the release head and not from the
previous release tag, whose subject release-please lists, appears in that
release's CHANGELOG section.

release-please cannot promise this. It walks ``main`` newest commit date
first and stops at the previous release commit (``Manifest.buildPullRequests``
in release-please src/manifest.ts, v17.6.0). PRs merge with a merge commit, so
a branch commit keeps the date it was written on; one written before a release
commit and merged after it sorts behind that commit and is never reached
(#1305, release 0.64.3). Ancestry has no such blind spot, so this script asks
git: ``git log --no-merges <previous tag>..<head>``.

    python3 scripts/release_changelog_check.py --head <rev>
    python3 scripts/release_changelog_check.py --head v0.64.3 --version 0.64.3

The section checked is the newest one in CHANGELOG.md unless ``--version``
names another. The previous tag comes from the ``compare/vA...vB`` link in the
section header unless ``--prev`` is given. Commits are matched by full sha,
the form release-please writes into every line's link.

Exit 0 when nothing is missing, 1 when a commit is, 2 on a usage or git error.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Types release-please puts in the changelog when the config sets no
# ``changelog-sections``: the conventional-changelog preset shows these and
# hides style, chore, refactor, test, build and ci. A ``!`` lists any type.
_LISTED_TYPES = ("feat", "fix", "perf", "revert", "docs")
_SUBJECT_RE = re.compile(r"^(?P<type>[A-Za-z]+)(?:\(.*?\))?(?P<bang>!)?: ")
_HEADER_RE = re.compile(
    r"^## \[(?P<version>[^\]]+)\]\((?P<url>[^)]*)\)", re.MULTILINE
)
_COMPARE_RE = re.compile(r"/compare/(?P<prev>[^/]+?)\.\.\.[^/]+$")


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str


def is_listed_subject(subject: str) -> bool:
    """True when release-please would write this commit subject as a line."""
    match = _SUBJECT_RE.match(subject)
    if match is None:
        return False
    return bool(match.group("bang")) or match.group("type") in _LISTED_TYPES


def releasable_commits(repo: Path, prev: str, head: str) -> list[Commit]:
    """Listed commits reachable from ``head`` and not from ``prev``, any date."""
    out = subprocess.run(
        ["git", "log", "--no-merges", "--format=%H%x1f%s", f"{prev}..{head}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    commits = []
    for row in out.splitlines():
        sha, _, subject = row.partition("\x1f")
        if is_listed_subject(subject):
            commits.append(Commit(sha, subject))
    return commits


def missing_from_section(
    repo: Path, prev: str, head: str, section: str
) -> list[Commit]:
    return [c for c in releasable_commits(repo, prev, head) if c.sha not in section]


def _sections(text: str) -> list[tuple[str, str, str]]:
    headers = list(_HEADER_RE.finditer(text))
    found = []
    for i, header in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        compare = _COMPARE_RE.search(header.group("url"))
        prev = compare.group("prev") if compare else ""
        found.append((header.group("version"), prev, text[header.start() : end]))
    return found


def latest_section(text: str) -> tuple[str, str, str]:
    """(version, previous tag, section text) of the newest CHANGELOG section."""
    sections = _sections(text)
    if not sections:
        raise ValueError("no '## [version](url)' section header found")
    return sections[0]


def section_for(text: str, version: str) -> tuple[str, str, str]:
    for section in _sections(text):
        if section[0] == version:
            return section
    raise ValueError(f"no section for version {version}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--head", required=True, help="revision the release contains")
    parser.add_argument("--changelog", default="CHANGELOG.md", type=Path)
    parser.add_argument("--repo", default=".", type=Path)
    parser.add_argument("--version", help="section to check, default the newest")
    parser.add_argument("--prev", help="previous release tag, default from the header")
    args = parser.parse_args(argv)
    try:
        text = args.changelog.read_text(encoding="utf-8")
        if args.version:
            version, prev, section = section_for(text, args.version)
        else:
            version, prev, section = latest_section(text)
        prev = args.prev or prev
        if not prev:
            raise ValueError(f"section {version} has no compare link; pass --prev")
        missing = missing_from_section(args.repo, prev, args.head, section)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or ""
        sys.stderr.write(f"release_changelog_check: {exc} {detail}\n")
        return 2
    if missing:
        print(f"BLOCKED: section {version} omits {len(missing)} commit(s) in")
        print(f"{prev}..{args.head} that release-please lists:")
        for commit in missing:
            print(f"  {commit.sha[:7]} {commit.subject}")
        print("release-please stopped its date-ordered walk at the previous release")
        print("commit and never reached commits dated before it (#1305). Add the")
        print("lines above by hand before merging this release PR, in two places:")
        print("the section in CHANGELOG.md on the release branch, and the release")
        print("PR body, which is where release-please takes the GitHub release notes")
        print("from (src/strategies/base.ts, buildRelease, release-please 17.6.0).")
        return 1
    print(f"ok: section {version} lists every commit in {prev}..{args.head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
