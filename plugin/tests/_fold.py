"""Test-side shim for `requests.fold`, which takes a `FoldInputs` bundle with
no defaults (#1338). Tests that fold hand-built rows spell the pieces they
care about here; production composers get theirs from `requests.fold_inputs`."""

from daimon_briefing import requests


def fold_rows(rows, policies=frozenset(), *, policies_by_to=None,
              own_slug="", open_policies=None):
    return requests.fold(rows, requests.FoldInputs(
        policies=policies, policies_by_to=policies_by_to, own_slug=own_slug,
        open_policies=open_policies or {}))
