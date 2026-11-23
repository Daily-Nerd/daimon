"""Foreign text never carries an instruction to a reader's terminal.

A stored baton, item, quote or ruling is data written by someone else. Before
`display.printable` a baton holding `ESC[2K` or an OSC title sequence reached
the terminal raw, on the plain path and in the rich panel, and so did the text
of a checkpoint item or a ruling. The boundaries are the view (checkpoint
copy, masked ledger leaf) and the baton lines, and `handoff.validate` refuses
such a baton at write time.
"""
import re

import pytest

from daimon_briefing import (api, briefing, cli, display, marks,
                             refutations, render, serializer, store)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/handoff-terminal"
AGENT_TITLE = "HANDOFF (written by an agent, unverified, "
HOSTILE = "a\x1b[2A\x1b[2K\x1b]0;pwn\x07b\rc\x85d e f\x7fg\x00h"
# What a terminal acts on; tab and newline are the only controls allowed.
ACTS = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f  ]")


def _plant_baton(note, source="agent"):
    spec = {"item_ref": "", "status": "active", "note": note,
            "kind": "handoff", "source": source, "item_text": ""}
    assert store.append_events([spec], project_dir=PROJECT,
                               writer=Writer.HUMAN)


def _cp(text):
    return {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
            "working_context": {
                "active_topic": {"text": "topic " + text, "trust": "inferred"},
                "recent_decisions": [{"text": "decision " + text,
                                      "trust": "inferred",
                                      "quote": "quote " + text}]},
            "epistemic_snapshot": {}}


def _cli_brief(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


# ---- the baton: every output, stored text from before the write check -------

def test_a_hostile_stored_baton_reaches_no_machine_output(tmp_checkpoint_dir):
    store.write_checkpoint("S-1", _cp("ok"), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    _plant_baton(HOSTILE)
    text = api.render_briefing(PROJECT).text
    assert AGENT_TITLE in text and not ACTS.search(text)
    parsed = api.parse_briefing(text)
    assert parsed.handoff is not None and parsed.handoff.tier == "agent"
    assert not ACTS.search(parsed.handoff.text)


def test_a_hostile_stored_baton_reaches_no_plain_output(
        tmp_checkpoint_dir, monkeypatch, capsys):
    store.write_checkpoint("S-1", _cp("ok"), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    _plant_baton(HOSTILE)
    out = _cli_brief(capsys, monkeypatch)
    assert AGENT_TITLE in out and not ACTS.search(out)


def test_a_hostile_stored_baton_reaches_no_rich_panel(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_handoff({"ts": "2026-08-04T18:09:41Z", "tier": "agent",
                           "note": HOSTILE})
    out = capsys.readouterr().out
    assert AGENT_TITLE in out and not ACTS.search(out)


def test_a_hostile_timestamp_cannot_reach_the_title(capsys):
    render.render_handoff({"ts": "2026\x1b[2K", "tier": "agent", "note": "x"})
    assert not ACTS.search(capsys.readouterr().out)


def test_a_baton_cannot_forge_the_human_label_by_erasing_the_title(
        tmp_checkpoint_dir):
    store.write_checkpoint("S-1", _cp("ok"), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    _plant_baton("\x1b[2A\x1b[2K" + marks.handoff_title("human", "t") + ":")
    text = api.render_briefing(PROJECT).text
    assert text.startswith(AGENT_TITLE) and not ACTS.search(text)


# ---- the class: stored items and ledger prose reach a printer clean ----------

def test_a_stored_item_carries_no_terminal_instruction(
        tmp_checkpoint_dir, monkeypatch, capsys):
    store.write_checkpoint("S-1", _cp(HOSTILE), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    machine = api.render_briefing(PROJECT).text
    assert "decision a" in machine and not ACTS.search(machine)
    assert not ACTS.search(_cli_brief(capsys, monkeypatch))


def test_a_stored_ruling_carries_no_terminal_instruction(
        tmp_checkpoint_dir, monkeypatch, capsys):
    store.write_checkpoint("S-1", _cp("ok"), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    refutations.assert_ruling(
        subject="a subject", verdict="never " + HOSTILE, scope="this",
        evidence=["issue:1"], channel="cli-tty", ratified=True,
        project_dir=PROJECT)
    machine = api.render_briefing(PROJECT).text
    assert "never a" in machine and not ACTS.search(machine)
    assert not ACTS.search(_cli_brief(capsys, monkeypatch))


def test_printable_keeps_tab_and_newline_and_drops_the_rest():
    assert display.printable("a\tb\nc\x1bd\re f") == "a\tb\ncdef"
    assert display.printable(None) is None
    node = {"a": ["x\x1b", {"b": "y\x07"}], "n": 3}
    assert display.printable_tree(node) == {"a": ["x", {"b": "y"}], "n": 3}


# ---- write time ---------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "a\x1b[2K", "a\x1b]0;title\x07b", "a\rb", "a\x85b", "a b",
    "a b", "a\x7fb", "a\x00b", "a\x0bb", "a\x9bb"])
def test_write_handoff_refuses_a_control_character(tmp_checkpoint_dir, bad):
    with pytest.raises(api.HandoffUnprintable):
        api.write_handoff(PROJECT, bad)
    assert store.active_handoff(PROJECT) is None


def test_write_handoff_keeps_newline_and_tab(tmp_checkpoint_dir):
    api.write_handoff(PROJECT, "first\n\tsecond")
    assert store.active_handoff(PROJECT)["note"] == "first\n\tsecond"


def test_the_cli_refuses_a_control_character_too(tmp_checkpoint_dir, capsys):
    rc = cli.main(["handoff", "a\x1b[2Kb", "--by", "agent",
                   "--project", PROJECT])
    assert rc == 1 and "control character" in capsys.readouterr().err
    assert store.active_handoff(PROJECT) is None


def test_superseding_never_echoes_a_hostile_prior_baton(
        tmp_checkpoint_dir, capsys):
    _plant_baton("old\x1b[2K")
    assert cli.main(["handoff", "new", "--by", "agent",
                     "--project", PROJECT]) == 0
    assert not ACTS.search(capsys.readouterr().err)


# ---- the byte cap and the budget note ----------------------------------------

def test_the_cap_is_in_bytes_as_well_as_chars(tmp_checkpoint_dir):
    four_byte = "\U0001f600"
    with pytest.raises(api.HandoffTooLong) as caught:
        api.write_handoff(PROJECT, four_byte * 2000)
    assert caught.value.size == 8000 and caught.value.length == 2000
    assert caught.value.byte_limit == api.HANDOFF_MAX_BYTES == 3000
    api.write_handoff(PROJECT, four_byte * 750)        # exactly 3000 bytes
    assert len(store.active_handoff(PROJECT)["note"].encode()) == 3000


def test_the_cli_names_the_byte_cap(tmp_checkpoint_dir, capsys):
    rc = cli.main(["handoff", "\U0001f600" * 800, "--by", "agent",
                   "--project", PROJECT])
    err = capsys.readouterr().err
    assert rc == 1 and "3000 bytes" in err and "3200 bytes" in err


def test_the_cut_note_names_the_real_budget_not_a_clamped_one(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "1500")
    store.write_checkpoint("S-1", {
        **_cp("ok"), "working_context": {"recent_decisions": [
            {"text": f"decision {i} " + "padding words " * 20,
             "trust": "inferred"} for i in range(30)]}},
        project_dir=PROJECT, writer=Writer.HUMAN)
    _plant_baton("\U0001f600" * 2000)                  # stored before the cap
    text = api.render_briefing(PROJECT).text
    assert "over the 1500-byte budget" in text
    assert "1-byte budget" not in text


# ---- parse and strip, pinned -----------------------------------------------

def test_verify_phrase_inside_a_baton_line_is_not_a_warning(
        tmp_checkpoint_dir):
    store.write_checkpoint("S-1", _cp("ok"), project_dir=PROJECT,
                           writer=Writer.HUMAN)
    api.write_handoff(PROJECT, "VERIFY BEFORE TRUSTING the cache key")
    parsed = api.parse_briefing(api.render_briefing(PROJECT).text)
    assert parsed.handoff.text == "VERIFY BEFORE TRUSTING the cache key"
    assert not any("the cache key" in w for w in parsed.warnings)


def test_the_strip_starts_at_the_exact_title_and_goes_to_the_end():
    # The same deliberate over-approximation as the DAIMON BRIEFING header:
    # over-stripping costs a downgrade, under-stripping mints a false
    # `verbatim`. Only the exact title plus ", " starts it.
    for who in (marks.HANDOFF_BY_HUMAN, marks.HANDOFF_BY_AGENT):
        said = f"I wrote {marks.HANDOFF_LEAD}{who}, 2026) and more"
        assert serializer.strip_injected(said) == "I wrote "
    loose = "HANDOFF (we should hand this off) and more"
    assert serializer.strip_injected(loose) == loose
    assert briefing.handoff_block(None) == ""
