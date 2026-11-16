"""The CLI surface as data: one walker over the argparse tree.

The help golden test (`tests/test_cli_help_golden.py`) and the documentation
generator (`daimon_briefing/_docgen.py`) both read the surface through this
module, so they cannot disagree about what a command, an argument or a
subcommand is. It reads `parser._actions` (option strings, metavar, choices,
default, required, help) rather than formatted `--help` text: the layout of
formatted help differs between Python versions, the structure does not.
"""
import argparse
from dataclasses import dataclass


def subparsers_action(parser):
    """The `_SubParsersAction` of `parser`, or None for a leaf."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def walk(parser, path=()):
    """Every `(path, parser)` of the tree, depth first, in registration order."""
    yield path, parser
    action = subparsers_action(parser)
    if action is not None:
        for name, sub in action.choices.items():
            yield from walk(sub, path + (name,))


def children(parser):
    """`[(name, one-line help, parser)]` for each direct subcommand, in the
    order they were registered. An alias is a subcommand of its own."""
    action = subparsers_action(parser)
    if action is None:
        return []
    helps = {a.dest: a.help for a in action._choices_actions}
    return [(name, helps.get(name) or "", sub)
            for name, sub in action.choices.items()]


@dataclass(frozen=True)
class Argument:
    flags: tuple          # option strings; empty for a positional
    value: str            # metavar, choices or dest; "" for a bare flag
    default: str          # rendered default; "" when there is none worth showing
    required: bool
    help: str


def _value(action) -> str:
    takes_value = action.nargs != 0
    if not takes_value:
        return ""
    if action.choices is not None:
        return "|".join(str(c) for c in action.choices)
    name = action.metavar or (action.dest.upper() if action.option_strings
                              else action.dest)
    if action.nargs == "+":
        return f"{name} ..."
    if action.nargs in ("?", "*"):
        return f"[{name}]"
    return str(name)


def _default(action) -> str:
    d = action.default
    if d is None or d is False or d == [] or d is argparse.SUPPRESS:
        return ""
    if d is True:
        return "true"
    return str(d)


def arguments(parser) -> list[Argument]:
    """The documented arguments of one parser, positionals first, in the order
    they were added. The help flag, the version flag and subcommand selectors
    are not arguments of the command."""
    out = []
    for a in parser._actions:
        if isinstance(a, (argparse._HelpAction, argparse._VersionAction,
                          argparse._SubParsersAction)):
            continue
        if a.help is argparse.SUPPRESS:
            continue
        out.append(Argument(
            flags=tuple(a.option_strings),
            value=_value(a) if a.option_strings else (
                _value(a) or a.dest),
            default=_default(a),
            required=bool(a.required) if a.option_strings else a.nargs
            not in ("?", "*"),
            help=" ".join((a.help or "").split())))
    out.sort(key=lambda x: bool(x.flags))   # stable: positionals first
    return out
