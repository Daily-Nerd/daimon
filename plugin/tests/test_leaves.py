"""The CLI leaf iterator the write guard and the read census share (#1132
PR 6b): one definition of "a leaf command", so the two can never disagree."""

import argparse

from daimon_briefing import cli
from tests import _leaves


def test_the_iterator_yields_every_leaf_once():
    leaves = list(_leaves.iter_commands(cli.build_parser()))
    assert len(leaves) == len(set(leaves)) == 87
    assert ("serialize",) in leaves and ("hooks", "install") in leaves


def test_a_group_with_subcommands_is_never_a_leaf():
    leaves = set(_leaves.iter_commands(cli.build_parser()))
    for group in (("refute",), ("ruling",), ("hooks",), ("mcp",), ("team",)):
        assert group not in leaves


def test_the_write_guard_uses_the_shared_iterator():
    from tests import test_write_audit_guard as guard
    assert guard._iter_commands is _leaves.iter_commands


def test_leaf_parsers_expose_their_actions():
    parser = cli.build_parser()
    by_leaf = _leaves.leaf_parsers(parser)
    assert set(by_leaf) == set(_leaves.iter_commands(parser))
    assert all(isinstance(p, argparse.ArgumentParser) for p in by_leaf.values())
