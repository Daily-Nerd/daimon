"""#1236 slice 4: the briefing head note for an imported `latest`.

When the checkpoint `brief` serves as latest came through the import door
(`capture_door: "import"`, stamped by slice 1), the head carries one note
under the greeting, in the plain and the rich render, and the active topic
line says the topic is imported and how old it is. A live capture that
becomes latest ends both with no state: the key is absent on a live door.

Every store here is written by `store.write_checkpoint`, in the isolated
checkpoint dir the conftest fixture points at.
"""

import pytest

from daimon_briefing import api, briefing, cli, marks, render, store
from daimon_briefing.surfaces import Writer

PROJECT = "/p/imported-history"
NEWEST = "2026-10-14T10:00:00Z"
DAY = 86400.0
# The clock the briefing reads: 12 days and an hour past the newest import.
NOW = store._created_epoch(NEWEST) + 12 * DAY + 3600
NOTE = (f"{marks.WARNING_MARK} Imported history: 3 past sessions, the newest "
        "ended 12d ago (2026-10-14), before daimon captured anything live "
        "here. Check it before acting on it.")


def _checkpoint(sid, created, *, door=None, topic="the release cadence"):
    cp = {
        "session_id": sid, "created": created, "schema_version": 3,
        "working_context": {
            "active_topic": {"text": topic, "trust": "inferred"},
            "open_questions": [
                {"text": f"who owns the rollout after {sid}",
                 "trust": "inferred"}],
            "recent_decisions": [
                {"text": f"ship the fix behind a flag ({sid})",
                 "trust": "inferred"}]},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    if door is not None:
        cp["capture_door"] = door
    return cp


def _write(cp, project=PROJECT):
    store.write_checkpoint(cp["session_id"], cp, project_dir=project,
                           writer=Writer.HUMAN)


def _import_three(project=PROJECT):
    """Three imported sessions, oldest first, as `daimon import` writes them;
    the newest is latest."""
    _write(_checkpoint("S-imp-1", "2026-10-09T10:00:00Z", door="import"),
           project)
    _write(_checkpoint("S-imp-2", "2026-10-12T10:00:00Z", door="import"),
           project)
    _write(_checkpoint("S-imp-3", NEWEST, door="import"), project)


def _brief(now=NOW):
    return briefing.machine_brief(PROJECT, now, loops_pointer=False)


# ---- the plain head ---------------------------------------------------------


def test_the_note_sits_right_under_the_greeting(tmp_checkpoint_dir):
    _import_three()
    got = _brief()
    lines = got.text.splitlines()
    assert lines[0] == marks.GREETING
    assert lines[1] == ""
    assert lines[2] == NOTE
    assert got.notes == (NOTE,)


def test_the_active_topic_line_says_imported_and_how_old(tmp_checkpoint_dir):
    _import_three()
    text = _brief().text
    assert "Active topic (imported, 12d ago): the release cadence" in text


def test_one_session_reads_in_the_singular(tmp_checkpoint_dir):
    _write(_checkpoint("S-imp-1", NEWEST, door="import"))
    text = _brief().text
    assert ("Imported history: 1 past session, which ended 12d ago "
            "(2026-10-14), before daimon captured anything live here."
            in text)


def test_an_import_from_today_says_so(tmp_checkpoint_dir):
    _write(_checkpoint("S-imp-1", NEWEST, door="import"))
    text = _brief(now=store._created_epoch(NEWEST) + 3600).text
    assert "1 past session, which ended today (2026-10-14)" in text
    assert "Active topic (imported, today): the release cadence" in text


def test_the_note_is_one_warning_line_for_a_machine_reader(
        tmp_checkpoint_dir):
    _import_three()
    parsed = api.parse_briefing(_brief().text)
    assert NOTE in parsed.warnings
    assert parsed.header == marks.GREETING


# ---- a live capture ends it, with no state ----------------------------------


def test_a_live_latest_ends_the_note_and_the_qualifier(tmp_checkpoint_dir):
    _import_three()
    assert "Imported history" in _brief().text
    live = _checkpoint("S-live", "2026-10-26T10:00:00Z")
    _write(live)
    after = _brief()
    assert "Imported history" not in after.text
    assert "(imported" not in after.text
    assert after.notes == ()
    # Nothing else changes: the render is the one a store holding only the
    # live checkpoint gives.
    for path in store._session_files(tmp_checkpoint_dir):
        if path.stem.startswith("S-imp-"):
            path.unlink()
    assert _brief().text == after.text


def test_an_old_checkpoint_without_the_key_reads_as_live(tmp_checkpoint_dir):
    _write(_checkpoint("S-old", NEWEST))
    text = _brief().text
    assert "Imported history" not in text
    assert "Active topic: the release cadence" in text


# ---- charged with the head, never with the body -----------------------------


def test_the_note_survives_the_byte_ceiling(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "200")
    _import_three()
    text = _brief().text
    assert NOTE in text
    assert "bytes over the 200-byte budget" in text
    # The body gave way (the open loop is cut); the note did not.
    assert "(0 of 1 shown; 1 cut for budget)" in text


# ---- the CLI renders, plain and rich ----------------------------------------


def test_cli_brief_plain_carries_the_note_and_the_qualifier(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _import_three()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setattr("time.time", lambda: NOW)
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert NOTE in out
    assert "Active topic (imported, 12d ago): the release cadence" in out


def test_cli_brief_rich_carries_the_note_and_the_qualifier(
        tmp_checkpoint_dir, capsys, monkeypatch):
    pytest.importorskip("rich")
    _import_three()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setattr("time.time", lambda: NOW)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "Imported history: 3 past sessions" in out
    assert "Active topic (imported, 12d ago)" in out
