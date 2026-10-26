"""scripts/merge_pr.py: the merge commit must carry no line release-please
parses as a commit of its own, or the changelog lists the PR twice.

The regex under test mirrors release-please's ``splitMessages`` (src/commit.ts):
a blank line followed by a conventional type opens a new parsed commit.
"""

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from merge_pr import conventional_lines, main  # noqa: E402

# The shape GitHub writes under MERGE_MESSAGE + PR_TITLE, the repo setting.
_BUTTON_MESSAGE = (
    "Merge pull request #1243 from Daily-Nerd/feat/capture-cost\n"
    "\n"
    "feat(serialize): every checkpoint records what its capture cost in tokens"
)


def test_button_merge_body_counts_as_a_second_commit():
    assert conventional_lines(_BUTTON_MESSAGE) == [
        "feat(serialize): every checkpoint records what its capture cost in tokens"
    ]


def test_default_subject_with_empty_body_is_ignored():
    assert conventional_lines("Merge pull request #1244 from Daily-Nerd/feat/x") == []


def test_pr_title_as_subject_counts_even_with_empty_body():
    # PR_TITLE + BLANK: the subject itself is conventional.
    assert conventional_lines("fix(hooks): honour DAIMON_LOG_DIR (#1242)") == [
        "fix(hooks): honour DAIMON_LOG_DIR (#1242)"
    ]


def test_body_line_without_a_blank_line_before_it_is_not_split():
    # release-please splits only on a blank line; a single newline stays one
    # message whose header is the merge subject.
    message = "Merge pull request #1 from o/b\nfeat(x): y"
    assert conventional_lines(message) == []


@pytest.mark.parametrize(
    "line",
    [
        "feat!: breaking",
        "fix(scope)!: breaking with scope",
        "docs(website): prose",
        "chore: hidden type still parsed",
    ],
)
def test_every_release_please_type_is_caught_after_a_blank_line(line):
    assert conventional_lines("Merge pull request #1 from o/b\n\n" + line) == [line]


def test_prose_body_is_not_a_commit():
    message = "Merge pull request #1 from o/b\n\nSee the PR for the discussion."
    assert conventional_lines(message) == []


def test_check_and_dry_run_are_mutually_exclusive():
    with pytest.raises(SystemExit) as exc:
        main(["7", "--check", "--dry-run"])
    assert exc.value.code == 2
