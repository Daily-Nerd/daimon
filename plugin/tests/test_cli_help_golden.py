"""The CLI surface is frozen while `cli/__init__.py` is split into modules
(#1132 PR 5): `--help` of the root parser and of every subparser, recursively,
plus the command tuples, must stay byte-identical. The registration order is
part of the surface (argparse lists subcommands in the order they were added),
so a move that reorders two `register` calls fails here.

Regenerate deliberately (a real CLI change, never a move):
    UPDATE_CLI_GOLDEN=1 uv run pytest tests/test_cli_help_golden.py
"""

import argparse
import os
from pathlib import Path

from daimon_briefing import cli

GOLDEN = Path(__file__).parent / "golden" / "cli_help.txt"


def _walk(parser, path=()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _walk(sub, path + (name,))


def render() -> str:
    parts = []
    walked = list(_walk(cli.build_parser()))
    parts.append("COMMANDS\n" + "\n".join(
        " ".join(path) or "<root>" for path, _p in sorted(walked)) + "\n")
    for path, parser in walked:
        parts.append("=== " + (" ".join(path) or "<root>") + " ===\n"
                     + parser.format_help())
    return "\n".join(parts)


def test_the_cli_help_surface_is_unchanged(monkeypatch):
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "100")
    text = render()
    if os.environ.get("UPDATE_CLI_GOLDEN"):
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")
