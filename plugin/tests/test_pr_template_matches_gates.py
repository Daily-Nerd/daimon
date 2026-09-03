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


def _policy_regex(name):
    """Compile one single-quoted path policy from the workflow env."""
    body = WORKFLOW.read_text(encoding="utf-8")
    m = re.search(rf"^\s+{re.escape(name)}: '([^']+)'$", body, re.MULTILINE)
    assert m, f"{name} not found; the policy test is measuring nothing"
    return re.compile(m.group(1))


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


@pytest.mark.parametrize("path", [
    "README.md",
    "plugin/README.md",
    "research/experiments/README.md",
    "docs/ARCHITECTURE.md",
    "docs/demo/daimon-demo.gif",
    "website/docs/concepts/architecture.md",
    "website/blog/2026-08-29-architecture.md",
    "website/static/img/architecture.png",
    # The Spanish mirror ships in the same PR as the page it mirrors; a
    # docs PR carrying it must stay docs-only or the mirror rule and the
    # bypass contradict each other on every mirrored change.
    "website/i18n/es/docusaurus-plugin-content-docs/current/index.md",
    "website/i18n/es/docusaurus-plugin-content-blog/2026-08-29-architecture.md",
])
def test_docs_issue_bypass_accepts_only_declared_public_docs(path):
    assert _policy_regex("DOCS_ONLY_PATH_RE").fullmatch(path)


@pytest.mark.parametrize("path", [
    "skills/daimon-briefing/SKILL.md",
    "docs/demo/daimon-demo.tape",
    "website/docs/concepts/interactive.mdx",
    "website/static/img/active-content.svg",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "website/docusaurus.config.ts",
    "website/package.json",
    # Translation JSON under i18n/ configures the site; prose only bypasses.
    "website/i18n/es/code.json",
    "website/i18n/es/docusaurus-theme-classic/navbar.json",
    "plugin/daimon_briefing/briefing.py",
])
def test_docs_issue_bypass_rejects_executable_or_governance_surfaces(path):
    assert not _policy_regex("DOCS_ONLY_PATH_RE").fullmatch(path)


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml",
    ".github/workflows/release.yaml",
    ".github/actions/setup/action.yml",
    ".github/dependabot.yml",
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".pre-commit-config.yaml",
    "release-please-config.json",
    ".release-please-manifest.json",
    "plugin/tests/test_pr_template_matches_gates.py",
])
def test_ci_issue_bypass_accepts_only_declared_governance_files(path):
    assert _policy_regex("CI_ONLY_PATH_RE").fullmatch(path)


@pytest.mark.parametrize("path", [
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    "scripts/sync_hooks.py",
    "plugin/uv.lock",
    "website/package-lock.json",
    "plugin/daimon_briefing/store.py",
])
def test_ci_issue_bypass_rejects_code_dependencies_and_issue_forms(path):
    assert not _policy_regex("CI_ONLY_PATH_RE").fullmatch(path)


def test_issue_bypasses_keep_their_required_labels_and_mixed_pr_guard():
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "[ \"$kind\" = docs ] && grep -qx 'type:docs'" in body
    assert "grep -qx 'type:ci'" in body
    assert "grep -qx 'status:approved'" in body
    assert 'kind=other' in body
    assert "--paginate --jq '.[].filename'" in body

    template = TEMPLATE.read_text(encoding="utf-8")
    assert "documentation-only PRs labeled type:docs" in template
    assert "CI-only PRs labeled type:ci AND status:approved" in template
    assert "Mixed changes never receive an issue bypass" in template
