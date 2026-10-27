#!/usr/bin/env python3
"""Merge a pull request with a merge commit whose message release-please ignores.

Why this exists. PRs merge with a merge commit so every branch commit reaches
``main`` as itself and becomes one changelog line. release-please reads each
commit message and splits it wherever a blank line is followed by a
conventional type (``feat``, ``fix``, ...). GitHub's merge button writes

    Merge pull request #N from owner/branch

    <PR title>

and the PR title is conventional, so the merge commit counts as one more
``feat``/``fix`` and the change is listed twice. GitHub offers no
merge-message setting that leaves the title out:
the three allowed combinations put it in the title or in the body. The only
merge commit release-please ignores is one with the default subject and an
empty body, which the button cannot produce on its own. This script produces
it, then verifies the commit that landed.

    uv run python scripts/merge_pr.py <number>            # merge and verify
    uv run python scripts/merge_pr.py <number> --check    # verify a merged PR
    uv run python scripts/merge_pr.py <number> --dry-run  # print the command

Exit 0 when the merge commit on ``main`` carries no conventional line, 1 when
it does (the next changelog will double-list this PR; fix it like #1232), 2
when the PR is not mergeable or ``gh`` fails.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

# The types release-please's splitMessages recognises after a blank line
# (src/commit.ts). A body line of this shape becomes a second parsed commit.
_CONVENTIONAL_TYPES = "feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert"
_SPLIT_RE = re.compile(
    r"\r?\n\r?\n(?=(?:" + _CONVENTIONAL_TYPES + r")(?:\(.*?\))?!?: )"
)
_HEADER_RE = re.compile(r"^(?:" + _CONVENTIONAL_TYPES + r")(?:\(.*?\))?!?: ")


def conventional_lines(message: str) -> list[str]:
    """Return every line of ``message`` release-please would parse as a commit.

    The first line counts when it is itself conventional (``PR_TITLE``
    settings). Any later line counts when a blank line precedes it and it
    opens with a conventional type, which is the split release-please makes.
    """
    found: list[str] = []
    first = message.split("\n", 1)[0]
    if _HEADER_RE.match(first):
        found.append(first.rstrip("\r"))
    for chunk in _SPLIT_RE.split(message)[1:]:
        found.append(chunk.split("\n", 1)[0].rstrip("\r"))
    return found


def _gh(*args: str) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(2)
    return proc.stdout


def _pr(number: int) -> dict[str, object]:
    raw = _gh(
        "pr",
        "view",
        str(number),
        "--json",
        "state,mergeStateStatus,headRefName,headRepositoryOwner,mergeCommit",
    )
    data: dict[str, object] = json.loads(raw)
    return data


def _merge_commit_message(number: int) -> tuple[str, str]:
    data = _pr(number)
    merge = data.get("mergeCommit")
    if not isinstance(merge, dict) or not merge.get("oid"):
        sys.stderr.write(f"PR #{number} has no merge commit yet\n")
        raise SystemExit(2)
    sha = str(merge["oid"])
    message = _gh(
        "api", f"repos/{{owner}}/{{repo}}/commits/{sha}", "-q", ".commit.message"
    )
    return sha, message.rstrip("\n")


def verify(number: int) -> int:
    sha, message = _merge_commit_message(number)
    offending = conventional_lines(message)
    if offending:
        print(f"BLOCKED: merge commit {sha[:7]} of #{number} carries a line")
        print("release-please will count as a commit of its own:")
        for line in offending:
            print(f"  {line}")
        print("The next changelog lists this PR twice. Fix the committed")
        print("CHANGELOG.md after the release lands, as #1232 did.")
        return 1
    print(f"ok: merge commit {sha[:7]} of #{number} has no conventional line")
    return 0


def merge(number: int, dry_run: bool) -> int:
    data = _pr(number)
    state = data.get("state")
    mergeable = data.get("mergeStateStatus")
    if state != "OPEN" or mergeable != "CLEAN":
        sys.stderr.write(
            f"refused: PR #{number} is {state} with merge state {mergeable}; "
            "merge only an OPEN and CLEAN PR\n"
        )
        return 2
    owner_obj = data.get("headRepositoryOwner")
    owner = owner_obj.get("login") if isinstance(owner_obj, dict) else None
    subject = f"Merge pull request #{number} from {owner}/{data['headRefName']}"
    cmd = ["pr", "merge", str(number), "--merge", "--subject", subject, "--body", ""]
    if dry_run:
        print("gh " + " ".join(repr(a) if a == "" else a for a in cmd))
        return 0
    _gh(*cmd)
    return verify(number)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("number", type=int, help="pull request number")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--check",
        action="store_true",
        help="verify the merge commit of an already merged PR, merge nothing",
    )
    group.add_argument(
        "--dry-run", action="store_true", help="print the gh command and exit"
    )
    args = parser.parse_args(argv)
    if args.check:
        return verify(args.number)
    return merge(args.number, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
