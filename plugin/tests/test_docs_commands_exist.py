"""A command or flag written in a guide exists in the CLI.

A guide is a task sequence, so it is full of `daimon <command> --flag`
lines, and nothing ties them to the argparse parsers: a renamed flag or a
removed verb leaves a guide that tells the reader to run something that
fails. This test reads every `daimon ...` command in the code blocks and
inline code of the guide pages and the quickstart, in both languages, and
checks the command path and each `--flag` against the parser tree that
`daimon_briefing.cli` builds. Placeholders (`<id>`, `[--note]`, quoted text)
are skipped; a word that should name a subcommand and does not is an error.
"""
import re
import shlex
from pathlib import Path

import pytest

from daimon_briefing import cli
from daimon_briefing.cli._surface import children

REPO = Path(__file__).resolve().parents[2]
PAGES = [
    *sorted((REPO / "website/docs/guides").glob("*.md")),
    *sorted((REPO / "website/i18n/es/docusaurus-plugin-content-docs/current"
             / "guides").glob("*.md")),
    REPO / "website/docs/getting-started/quickstart.md",
    REPO / "website/i18n/es/docusaurus-plugin-content-docs/current"
    / "getting-started/quickstart.md",
]

_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
_INLINE = re.compile(r"`(daimon [^`]+)`")
_WORD = re.compile(r"[a-z][a-z-]*$")


def _commands(text):
    for block in _FENCE.findall(text):
        joined = block.replace("\\\n", " ")
        for line in joined.splitlines():
            line = line.strip().removeprefix("$ ")
            if line.startswith("daimon "):
                yield line
    for span in _INLINE.findall(_FENCE.sub("", text)):
        yield span


def _check(command):
    """Return an error string, or None."""
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        tokens = command.split()
    parser = cli.build_parser()
    path = ["daimon"]
    rest = tokens[1:]
    while rest:
        kids = {name: sub for name, _h, sub in children(parser)}
        if not kids:
            break
        word = rest[0]
        if word in kids:
            parser = kids[word]
            path.append(word)
            rest = rest[1:]
        elif _WORD.match(word) and not word.startswith("--"):
            return f"`{word}` is not a command of `{' '.join(path)}`"
        else:
            break
    flags = {f for a in parser._actions for f in a.option_strings}
    for token in rest:
        for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", token):
            if flag not in flags and flag != "--help":
                return f"`{flag}` is not a flag of `{' '.join(path)}`"
    return None


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(REPO)))
def test_every_command_and_flag_in_the_page_exists(page):
    errors = []
    for command in _commands(page.read_text(encoding="utf-8")):
        problem = _check(command)
        if problem:
            errors.append(f"{command}  ->  {problem}")
    assert not errors, "\n".join(errors)


def test_the_check_catches_a_renamed_flag_and_a_removed_verb():
    assert _check("daimon request open --to x --ask a --why b") is None
    assert "not a flag" in _check("daimon request open --nonsense x")
    assert "not a command" in _check("daimon request launch --to x")
    assert _check("daimon heal --all  # every session") is None
