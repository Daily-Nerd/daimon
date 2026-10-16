"""The briefing text is a contract (#1132 PR 7b): daimon owns its format and
`api.parse_briefing` reads it back. A downstream consumer (a chat bridge, a
host hook) takes the first line that is not a warning as the header, a line
starting with the ruling mark as a ruling, and a line starting with an item
mark as an item. So every note that precedes the greeting must carry the
warning mark, or it steals the header.

The cases run over REAL output: the CLI, the MCP tool and the Hermes hook,
each over a store written by the real writers."""

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

import daimon_briefing
from daimon_briefing import (api, briefing, cli, hooks, ledger, marks,
                             mcp_tools, store, testing, trust)

KEPT = "an unrelated decision that stays visible"
Q = "SENTINEL-quarantined ship the migration on Friday"
RULING = "never ship a Friday deploy"


class Box(NamedTuple):
    project: str
    other: str


@pytest.fixture
def box(tmp_path):
    """The store every real-output case reads: a sandbox whose settings name
    `project` as the caller's own, and a second project beside it."""
    root = tmp_path.resolve() / "sbx"
    project, other = root / "p" / "contract", root / "p" / "contract-other"
    with testing.sandbox(root, settings={"DAIMON_PROJECT_DIR": str(project)}):
        yield Box(str(project), str(other))


def _seed(box, *decisions, project=None):
    testing.seed_checkpoint(
        project or box.project,
        testing.sample_checkpoint(*(decisions or (KEPT,)),
                                  topic="the contract topic",
                                  questions=("who owns the rollout of it",)))


def _ruling(box, verdict=RULING):
    return testing.seed_ruling(box.project, subject="subject of the ruling",
                               verdict=verdict, evidence=("issue:693",))


def _plant(box, name, data, project=None):
    bucket = (store.config.checkpoint_dir()
              / store.project_slug(project or box.project))
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / name).write_bytes(data)


def _brief(capsys, *argv):
    rc = cli.main(["brief", *argv])
    return rc, capsys.readouterr().out


# ---- the parser ----


def test_the_parser_applies_the_documented_algorithm():
    raw = ("\n  \nWhile you were away\n§ a ruling\n- [tag] item one\n"
           "* item two\nstray prose\n⚠ a warning\n"
           "VERIFY BEFORE TRUSTING (x)\n- ⚠ an item that warns\n")
    got = api.parse_briefing(raw)
    assert got.header == "While you were away"
    assert got.standing_rulings == ("§ a ruling",)
    assert got.items == ("- [tag] item one", "* item two")
    assert got.warnings == ("⚠ a warning", "VERIFY BEFORE TRUSTING (x)",
                            "- ⚠ an item that warns")
    assert got.raw == raw


def test_the_parser_of_nothing_is_empty():
    got = api.parse_briefing("")
    assert (got.header, got.standing_rulings, got.items, got.warnings) == (
        "", (), (), ())


def test_the_parsed_briefing_is_frozen_and_holds_tuples():
    import dataclasses
    got = api.parse_briefing("h\n- i\n")
    assert isinstance(got.items, tuple) and isinstance(got.warnings, tuple)
    assert isinstance(got.standing_rulings, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        got.header = "x"  # type: ignore[misc]


def test_the_parser_is_built_from_the_shared_marks():
    assert api.parse_briefing(f"h\n{marks.RULING_MARK} r\n").standing_rulings
    for mark in marks.ITEM_MARKS:
        assert api.parse_briefing(f"h\n{mark} i\n").items == (f"{mark} i",)
    for phrase in marks.WARNING_MARKERS:
        assert api.parse_briefing(f"h\nx {phrase} y\n").warnings


# ---- real output ----


def test_a_plain_brief_has_the_greeting_rulings_and_items(
        box, capsys):
    _seed(box)
    _ruling(box)
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING == marks.GREETING
    assert got.standing_rulings and all(
        r.startswith(marks.RULING_MARK) for r in got.standing_rulings)
    assert any(RULING in r for r in got.standing_rulings)
    assert got.items and all(i.startswith(marks.ITEM_MARKS)
                             for i in got.items)
    assert any(KEPT in i for i in got.items)


def test_a_cross_project_brief_keeps_the_greeting_as_header(
        box, capsys):
    _seed(box, project=box.other)
    _seed(box)
    slug = store.project_slug(box.other)
    rc, out = _brief(capsys, "--slug", slug)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    assert [w for w in got.warnings if "cross-project briefing" in w
            and slug in w] == [f"{marks.WARNING_MARK} cross-project "
                               f"briefing — project: {slug}"]


def test_a_serialize_in_flight_note_is_a_warning(
        box, monkeypatch, capsys):
    _seed(box)
    monkeypatch.setattr(ledger, "serialize_in_flight", lambda slug: True)
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    [note] = [w for w in got.warnings if "serialize is in flight" in w]
    assert note.startswith(f"{marks.WARNING_MARK} a serialize")
    assert "⏳" not in out


def test_an_unreadable_ledger_note_is_a_warning(
        box, capsys):
    _seed(box)
    _plant(box, "events.jsonl", b"\xff\xfe not utf-8\n")
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    assert any(w.startswith(f"{marks.WARNING_MARK} events.jsonl is unreadable")
               for w in got.warnings)
    assert any(KEPT in i for i in got.items)


def test_a_closed_view_keeps_the_greeting_and_lists_no_items(
        box, capsys):
    _seed(box)
    _plant(box, "trust.jsonl", b"<<<<<<< HEAD\n")
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    assert got.items == ()
    assert any("trust.jsonl is unreadable" in w for w in got.warnings)
    assert briefing.CLOSED_LINE in out


def test_the_withheld_trailer_is_neither_header_item_nor_warning(
        box, capsys):
    _seed(box, KEPT, Q)
    trust.propose(text=Q, kind="decision", reason="made up",
                  evidence=["issue:1"], channel="cli-tty",
                  project_dir=box.project)
    rc, out = _brief(capsys)
    assert rc == 0
    assert "withheld" in out
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    blob = "\n".join([got.header, *got.standing_rulings, *got.items,
                      *got.warnings])
    assert "withheld" not in blob          # dropped, by design: pinned here


def test_the_no_briefing_line_is_plain_and_not_a_warning(
        box, capsys):
    """Without a checkpoint of its own the project gets only orientation, and
    that line is the first non-warning line, so it IS the header. It must not
    pose as a warning."""
    _seed(box, project=box.other)
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header.startswith("No briefing for this project yet")
    assert got.warnings == ()


def test_the_teammate_counts_on_the_header_only_path_are_plain(
        box, monkeypatch, capsys):
    from daimon_briefing.cli import brief as brief_mod

    def fake(project, counts):
        counts.resolved, counts.quarantined = 2, 3
        return []

    monkeypatch.setattr(brief_mod, "_team_briefings", fake)
    _seed(box, project=box.other)
    rc, out = _brief(capsys, "--team")
    assert rc == 0
    assert "2 resolved item(s) withheld (a teammate's)" in out
    assert "3 quarantined item(s) withheld (a teammate's)" in out
    assert api.parse_briefing(out).warnings == ()


def test_a_missing_bucket_line_is_plain(
        box, capsys):
    rc, out = _brief(capsys, "--slug", "-no-such-bucket")
    assert rc == 1
    assert api.parse_briefing(out).warnings == ()


def test_the_mcp_tool_output_parses(box):
    _seed(box)
    _ruling(box)
    out = mcp_tools.HANDLERS["daimon_brief"](
        {"slug": store.project_slug(box.project)}).text
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    assert got.standing_rulings and got.items


def test_the_mcp_closed_view_parses(box):
    _seed(box)
    _plant(box, "trust.jsonl", b"<<<<<<< HEAD\n")
    out = mcp_tools.HANDLERS["daimon_brief"](
        {"slug": store.project_slug(box.project)}).text
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING and got.items == ()


def test_the_hermes_hook_output_parses(box):
    _seed(box)
    _ruling(box)
    out = hooks.pre_llm_call(session_id="S-new", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    got = api.parse_briefing(out["context"])
    assert got.header == briefing.GREETING
    assert got.standing_rulings and got.items


# ---- the note callers, by AST (scar 0054: never by text matching) ----


def _brief_note_calls():
    path = Path(daimon_briefing.__file__).parent / "cli" / "brief.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = {"render_brief_note": [], "render_brief_line": []}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in out):
            out[node.func.attr].append(node)
    return out


def _literals(call):
    return [n.value for n in ast.walk(call)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_no_note_caller_passes_the_warning_mark_itself():
    calls = _brief_note_calls()
    assert calls["render_brief_note"]
    for call in calls["render_brief_note"]:
        for text in _literals(call):
            assert marks.WARNING_MARK not in text, (call.lineno, text)
            assert "⏳" not in text, (call.lineno, text)


def test_the_plain_lines_do_not_go_through_the_warning_renderer():
    calls = _brief_note_calls()
    plain = [t for c in calls["render_brief_line"] for t in _literals(c)]
    assert any(t.startswith("No briefing for this project yet")
               for t in plain)
    assert any(t.startswith("no checkpoint bucket for slug") for t in plain)
    warned = [t for c in calls["render_brief_note"] for t in _literals(c)]
    assert not any(t.startswith(("No briefing", "no checkpoint bucket"))
                   for t in warned)


def test_the_warning_renderer_adds_the_mark_once(capsys, monkeypatch):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: False)
    render.render_brief_note(["a plain note", f"{marks.WARNING_MARK} a note "
                              "that already warns"])
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"{marks.WARNING_MARK} a plain note",
                     f"{marks.WARNING_MARK} a note that already warns"]


def test_the_plain_renderer_adds_nothing(capsys, monkeypatch):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: False)
    render.render_brief_line(["a plain line"])
    assert capsys.readouterr().out.splitlines() == ["a plain line"]


# ---- the marks, defined once ----


def test_the_format_is_defined_in_one_place():
    assert marks.GREETING == "While you were away — here's where we left off."
    assert marks.RULING_MARK == "§"
    assert marks.ITEM_MARKS == ("-", "*")
    assert marks.WARNING_MARK == "⚠"
    assert marks.WARNING_MARKERS == ("⚠", "VERIFY BEFORE TRUSTING")
    assert briefing.GREETING is marks.GREETING
    assert briefing._line({"text": "x", "trust": "inferred"}).startswith(
        marks.ITEM_MARKS[0] + " ")


def test_a_ruling_line_starts_with_the_ruling_mark(
        box, capsys):
    _seed(box)
    _ruling(box)
    _, out = _brief(capsys)
    assert f"{marks.RULING_MARK} {RULING}" in out


def test_an_admission_refused_note_is_a_warning_and_never_the_header(
        box, monkeypatch, capsys):
    # #1132 PR 10b: the note a project's briefing carries for sessions its
    # events ledger refused. A consumer (anamnesis) reads it as a warning.
    _seed(box)
    note = ("⚠ 2 session(s) of this project not serialized: events.jsonl is "
            "unreadable; run: daimon ledger repair events, then daimon heal")
    monkeypatch.setattr(ledger, "admission_notes", lambda slug: (note,))
    rc, out = _brief(capsys)
    assert rc == 0
    got = api.parse_briefing(out)
    assert got.header == briefing.GREETING
    assert note in got.warnings
    assert all(note not in i for i in got.items)
    # And as the first line of a hand-built text, where a note above the
    # greeting must not become the header.
    assert api.parse_briefing(f"{note}\n{marks.GREETING}\n").header == (
        marks.GREETING)
