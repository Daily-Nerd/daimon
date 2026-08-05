"""The echo defense holds only as far as invocation recognition does (#585).

Daimon's own output is flagged by resolving a tool result back to the tool_use
that produced it and deciding whether that invocation was daimon. Keying on the
invocation rather than the output shape is the right call: it covers every
subcommand without enumerating render formats.

The cost is that a recognition miss is silent. Output that daimon produced but
whose invocation was not recognised reaches the extractor as ordinary
transcript content, which is the hole #512 and #577 exist to close.

A shell wrapper defeated the matcher: in `sh -c 'daimon ...'` the character
before `daimon` is a quote, and quotes are not in the delimiter class.

Widening that class is the wrong fix and is pinned against below. `rg "daimon"
cli.py` greps ABOUT daimon, and its output is a genuine witness; a blanket
tool-row strip was measured at 9.5% of the corpus's verifiable quotes against
0.06% for the invocation-scoped rule. Wrappers are therefore recognised
explicitly, the way `uv run` already is.
"""
import pytest

from daimon_briefing import transcript


def _use(cmd):
    return {"type": "tool_use", "id": "TU-1", "name": "Bash",
            "input": {"command": cmd}}


RECOGNISED = [
    # Already covered, kept so a regex change cannot quietly drop them.
    "daimon brief",
    "uv run --project plugin daimon loops",
    "cd /repo && daimon brief",
    "DAIMON_ENV_FILE=/tmp/e daimon status --suppressed",
    "/usr/local/bin/daimon recall --json foo | head",
    # The gap this closes.
    "sh -c 'daimon refute guard x'",
    'sh -c "daimon brief"',
    "bash -c 'daimon status'",
    "zsh -c 'daimon recall foo'",
    "/bin/sh -c 'daimon brief'",
    "bash -lc 'daimon brief'",
]

NOT_RECOGNISED = [
    # Talking ABOUT daimon. These outputs are legitimate witnesses and must
    # keep their evidentiary value.
    "rg daimon cli.py",
    'rg "daimon" cli.py',
    "rg 'daimon' cli.py",
    "grep -r daimon .",
    "echo daimon",
    "cat daimon.log",
    "ls ~/.daimon",
]


@pytest.mark.parametrize("cmd", RECOGNISED)
def test_daimon_invocations_are_recognised(cmd):
    assert transcript._is_daimon_tool_use(_use(cmd)), (
        f"unrecognised invocation reaches the extractor unflagged: {cmd!r}")


@pytest.mark.parametrize("cmd", NOT_RECOGNISED)
def test_commands_about_daimon_stay_witnesses(cmd):
    assert not transcript._is_daimon_tool_use(_use(cmd)), (
        f"over-broad match destroys a legitimate witness: {cmd!r}")


def test_mcp_tool_names_still_match():
    assert transcript._is_daimon_tool_use(
        {"type": "tool_use", "id": "x", "name": "mcp__daimon__brief", "input": {}})


def test_the_recognised_set_is_enumerated_not_sampled():
    # #585's point: the recognised set is a hand-maintained list of idioms, so
    # it has to be visible in one place rather than discovered by whoever hits
    # the next gap. This list IS that enumeration.
    assert len(RECOGNISED) >= 10
    assert any("sh -c" in c for c in RECOGNISED)
    assert any("uv run" in c for c in RECOGNISED)
    assert any("&&" in c for c in RECOGNISED)
