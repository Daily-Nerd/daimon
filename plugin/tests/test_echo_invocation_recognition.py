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
    # #778: the module spelling, which is how the CLI runs from a source
    # checkout and is therefore what anyone testing a branch produces. It is
    # in no manifest section — `daimon_briefing.cli` is reachable because
    # `cli/__main__.py` exists — so a test driven off `[project.scripts]`
    # would not have covered it.
    "python -m daimon_briefing.cli decide",
    "python3 -m daimon_briefing.cli brief",
    "uv run --project plugin python -m daimon_briefing.cli decide",
    "uv run --quiet python -m daimon_briefing refute ratify r-0123456789ab",
    "/usr/bin/python3.12 -m daimon_briefing.cli status",
    # #778: `timeout` is the one wrapper with real field usage, and the case
    # that matters is `timeout N daimon handoff`, whose output is the densest
    # memory content daimon renders.
    "timeout 120 daimon handoff \"clear the backup volume\"",
    "timeout 60 daimon loops",
    "timeout -k 5 30s daimon brief",
    "timeout 60 DAIMON_ENV_FILE=/tmp/e daimon status",
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
    # #778: measured against the real corpus rather than invented. This repo's
    # own directory is named `daimon` and its slug is `Daily-Nerd/daimon`, so
    # prose and paths mentioning it vastly outnumber invocations. Every line
    # below is a real corpus shape, and each one must keep its witness value.
    "git status",
    "gh issue create --repo Daily-Nerd/daimon --title 'fix: x'",
    "gh run watch --repo Daily-Nerd/daimon 12345",
    "bat plugin/daimon_briefing/cli/__init__.py",
    "git commit -m 'fix: create workdir before daimon brief spawn'",
    # A commit heredoc quoting a skill name. Observed as a real false positive
    # while measuring, which is why it is pinned here rather than imagined.
    "git commit -F - <<'EOF'\nchore(readme): document daimon-briefing\nEOF",
    # #781: a backtick opens a command substitution in shell, but in this
    # repository it far more often opens a markdown inline code span, because
    # every issue body, PR body, docstring and commit message here discusses
    # daimon's own commands. Measured: 127 rows were flagged ONLY by the
    # backtick delimiter and NONE was a shell substitution. Each line below is
    # a real corpus shape.
    "git commit -m 'fix: `daimon status` explained every unsigned checkpoint'",
    "cat > body.md <<'EOF'\n- `daimon --help` now ends with the docs URLs\nEOF",
    "python3 - <<'PY'\n\"\"\"Do items trace to `daimon brief` output?\"\"\"\nPY",
    "gh issue comment 1 --body 'the echo counter in `daimon status` is capped'",
]


@pytest.mark.parametrize("cmd", RECOGNISED)
def test_daimon_invocations_are_recognised(cmd):
    assert transcript._is_daimon_tool_use(_use(cmd)), (
        f"unrecognised invocation reaches the extractor unflagged: {cmd!r}")


@pytest.mark.parametrize("cmd", NOT_RECOGNISED)
def test_commands_about_daimon_stay_witnesses(cmd):
    assert not transcript._is_daimon_tool_use(_use(cmd)), (
        f"over-broad match destroys a legitimate witness: {cmd!r}")


def test_modern_command_substitution_still_matches():
    """#781 removes the backtick from the delimiter class, so the substitution
    it protected has to be covered by the spelling people actually write.
    `$(...)` reaches the matcher through the `(` delimiter, which is why
    dropping the backtick costs no genuine coverage."""
    for cmd in ["V=$(daimon --version)",
                "echo \"at $(daimon status --json | head -1)\"",
                "test -n \"$(daimon loops)\""]:
        assert transcript._is_daimon_tool_use(_use(cmd)), cmd


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
