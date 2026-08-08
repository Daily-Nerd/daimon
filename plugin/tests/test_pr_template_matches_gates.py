"""The PR template must not drift from the gates it describes.

The template told contributors to name a branch `type/description` without
saying which types are accepted, while the authoritative list lived in a regex
inside the workflow. That gap is not free: a branch named for its content area
fails validation, and a PR's head branch cannot be retargeted, so the fix is
closing and reopening rather than renaming.

Listing the types in the template only helps while the list stays true, so it
is pinned to the regex rather than copied and trusted.
"""
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / ".github/PULL_REQUEST_TEMPLATE.md"
WORKFLOW = REPO / ".github/workflows/pr-validation.yml"


def _gate_types():
    """The branch-name types the workflow actually accepts."""
    m = re.search(r"\^\(([a-z|]+)\)/", WORKFLOW.read_text(encoding="utf-8"))
    assert m, "branch-name regex not found; this test is measuring nothing"
    return m.group(1).split("|")


def test_the_regex_is_still_shaped_the_way_this_test_assumes():
    # Guard against the test silently passing if the workflow is restructured.
    types = _gate_types()
    assert "feat" in types and "fix" in types
    assert len(types) >= 8


@pytest.mark.parametrize("branch_type", _gate_types())
def test_every_accepted_type_is_documented(branch_type):
    body = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(rf"\b{re.escape(branch_type)}\b", body), (
        f"branch type {branch_type!r} is accepted by pr-validation.yml but is "
        "not listed in the PR template, so a contributor cannot discover it")


def test_the_template_does_not_invent_types_the_gate_rejects():
    # The opposite drift: documenting a type the workflow would reject sends a
    # contributor into a close-and-reopen for no reason.
    body = TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"type is one of:\s*\n\s*([a-z ]+)\n", body)
    assert m, "the template no longer lists the types in the expected shape"
    documented = m.group(1).split()
    assert set(documented) <= set(_gate_types()), (
        f"template documents types the gate rejects: "
        f"{sorted(set(documented) - set(_gate_types()))}")
