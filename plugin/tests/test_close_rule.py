"""#1344: the rule that closing an accepted request needs no human is defined
once (`requests.CLOSE_RULE` and its short form) and every surface an agent
reads before deciding whether to close shows that text. Markdown cannot
import Python, so the skills carry the rule verbatim and these tests fail the
moment a copy drifts from the constant."""

import argparse
from pathlib import Path

from daimon_briefing import briefing, cli, requests, skill_content

from tests.test_cli_brief_owed import _owed_ask

_REPO = Path(__file__).resolve().parents[2]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_rule_text_is_the_approved_wording():
    # #1352 corrected the middle sentence: `reject` has no ruling-covered
    # agent path, only `accept` does (`request reject --by agent` refuses).
    assert requests.CLOSE_RULE == (
        "Closing an accepted request needs no human: run `daimon request "
        "done <id> --evidence ...` the moment evidence exists, in the same "
        "turn, not at session end. Only `accept` has an agent path, under "
        "a covering ruling; reject and needs-info are a person's. On an "
        "ask nobody accepted, an agent `done` is only a pending claim that "
        "lands when a person accepts.")
    assert requests.CLOSE_RULE_SHORT == (
        "Accepted and owed. Close one now, no human needed: "
        "`daimon request done <id> --evidence …`")


def test_every_skill_surface_carries_the_rule_verbatim():
    surfaces = {
        "briefing skill": (_REPO / "skills/daimon-briefing/SKILL.md"
                           ).read_text(encoding="utf-8"),
        "end skill": (_REPO / "skills/daimon-end/SKILL.md"
                      ).read_text(encoding="utf-8"),
        "packaged end skill": (_REPO / "plugin/daimon_briefing/_skills/"
                               "daimon-end/SKILL.md").read_text(
                                   encoding="utf-8"),
        "installed full body": skill_content.render_full(),
    }
    for name, text in surfaces.items():
        assert _flat(requests.CLOSE_RULE) in _flat(text), name


def test_the_hook_line_is_the_short_form(
        tmp_checkpoint_dir, capsys, monkeypatch):
    from tests.test_requests import _inject, _owed_ask as _req_owed_ask
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    project = "/p/sender"
    _req_owed_ask(project, sender_dir="/p/close-rule-hook")
    capsys.readouterr()
    assert _inject(project, session="S-close-rule") == 0
    assert requests.CLOSE_RULE_SHORT in capsys.readouterr().out


def test_the_owed_panel_carries_the_short_form(
        tmp_checkpoint_dir, monkeypatch):
    recipient = "/p/close-rule-panel"
    _owed_ask(recipient, sender_dir="/p/close-rule-panel-sender")
    lines = briefing.owed_panel_lines(recipient)
    assert any(requests.CLOSE_RULE_SHORT in line for line in lines)


def test_request_done_help_says_an_agent_may_close_without_asking():
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    assert parser is not None
    request = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction)
                   ).choices["request"]
    done = next(a for a in request._actions
                if isinstance(a, argparse._SubParsersAction)
                ).choices["done"]
    assert _flat(requests.CLOSE_RULE_HELP) in _flat(done.format_help())
