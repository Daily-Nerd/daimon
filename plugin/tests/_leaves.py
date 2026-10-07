"""The one definition of a CLI leaf command (#1132 PR 6b).

Shared by the write-audit guard (`test_write_audit_guard.py`) and the read
census (`test_read_sentinel.py`): both drive "every leaf of
`cli.build_parser()`", and a private copy in each would let them disagree
about what a leaf is. A plain helper module under `tests/` (not a conftest
fixture) because both tests need it at import time to build parametrisation.
"""

import argparse


def iter_commands(parser, prefix=()):
    """Every leaf command tuple the parser tree registers."""
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield prefix
        return
    for action in subs:
        for name, sub in action.choices.items():
            yield from iter_commands(sub, prefix + (name,))


def leaf_parsers(parser, prefix=()):
    """`{leaf tuple: its ArgumentParser}` for every leaf."""
    subs = [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        return {prefix: parser}
    out = {}
    for action in subs:
        for name, sub in action.choices.items():
            out.update(leaf_parsers(sub, prefix + (name,)))
    return out
