"""Slice 1 of project identity: the three roles of the slug transform.

`store.project_slug` used to be the sanitizer for author names and remote
URLs, the seed of a bucket name for a location, and the owner of a project's
slug, all at once. Slice 1 names the first two (`scope.segment`,
`store.location_slug`) without changing one answer. These tests pin the places
where the split could drift silently: the three names agree on every input, and
the one thing only the location role does (a SEALED declaration) stays out of
the sanitizer.
"""

import pytest

from daimon_briefing import hostgit, scope, store, teamproject
from tests.test_hostgit_declare import home  # noqa: F401
from tests.test_hostgit_sealed import fake_repo
from tests.test_project_identity_pins import GOLDEN

AUTHORS_AND_URLS = [
    "Jane Doe", "jane.doe@example.com", "j_doe", "Zoë O'Neil", "日本",
    "git@github.com:acme/widget", "https://host/a/b.git", "  padded  ",
    "a/b", "a_b", "----", "...", "", "   ", None,
]


@pytest.mark.parametrize("raw", [g[0] for g in GOLDEN] + AUTHORS_AND_URLS)
def test_the_three_names_agree_on_every_undeclared_input(raw):
    assert scope.segment(raw) == store.location_slug(raw) == store.project_slug(raw)


def test_a_project_slug_is_untouched_by_the_sanitizer():
    assert scope.segment("-Users-b-code-x") == "-Users-b-code-x"


def test_logical_segments_keep_dropping_empties_and_folding_dots():
    assert teamproject.logical_segments("a b//../c.d/ ") == ("a-b", "--", "c-d")
    assert teamproject.logical_segments("") == ()
    assert teamproject.logical_segments(None) == ()


def test_a_sealed_declaration_names_a_location_but_never_sanitizes_text(home):  # noqa: F811
    repo = fake_repo(home / "r")
    root = str(repo.resolve())
    hostgit.declare(repo, slug="acme-widget")
    assert store.location_slug(root) == "acme-widget"
    assert store.project_slug(root) == "acme-widget"
    # An author name or remote URL that happens to equal a declared root is
    # still just text: it folds by the char rule.
    assert scope.segment(root) == root.replace("/", "-").replace(".", "-")
