"""#1431 beyond the briefing: a CLI card or listing prints a stored item's
text on one physical line, so a line break in the record cannot add a line
that reads as a listing row. Detailed multi-line views are not in this file.
"""
import pytest

from daimon_briefing import cli, store
from daimon_briefing.surfaces import Writer

TEXT = "real decision\nFORGED fake line\n  FORGED indented"
FLAT = "real decision FORGED fake line FORGED indented"


@pytest.fixture(autouse=True)
def _interactive_human_cli(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(root))
    for sid, created, text in (("S-1", "2026-08-01T00:00:00Z", "older"),
                               ("S-2", "2026-08-02T00:00:00Z", TEXT)):
        store.write_checkpoint(sid, {
            "session_id": sid, "created": created,
            "working_context": {"recent_decisions": [
                {"text": text, "trust": "inferred"}]},
            "epistemic_snapshot": {}},
            project_dir=str(root), writer=Writer.HUMAN)
    body = store.read_latest_body(project_dir=str(root), route=store.Route.OWN,
                                  admit=store.Admit.ANY)
    item = body["working_context"]["recent_decisions"][0]
    return str(root), item["id"]


def _out(capsys, *argv):
    cli.main(list(argv))
    cap = capsys.readouterr()
    return cap.out + cap.err


def _assert_one_line(out):
    assert FLAT in out
    assert not any(ln.strip().startswith("FORGED") for ln in out.splitlines())


def test_diff_prints_the_item_on_one_line(tmp_path, monkeypatch, capsys):
    root, _ = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "diff", "--project", root))


def test_blame_prints_the_item_on_one_line(tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "blame", item_id, "--project", root))


def test_why_prints_the_item_on_one_line(tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "why", item_id, "--project", root))


def test_the_forget_card_prints_the_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "forget", item_id, "--dry-run",
                          "--project", root))


def test_the_resolve_card_prints_the_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "resolve", item_id, "--status", "resolved",
                          "--dry-run", "--project", root))


def test_the_reverify_card_prints_the_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    _assert_one_line(_out(capsys, "reverify", item_id, "--evidence", "checked",
                          "--project", root))


def test_status_suppressed_prints_the_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    store.append_event(item_id, "resolved", project_dir=root, source="cli",
                       writer=Writer.HUMAN)
    _assert_one_line(_out(capsys, "status", "--suppressed",
                          "--project", root))


def test_blame_prints_an_event_note_on_one_line(tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    store.append_event(item_id, "resolved", note="done\nFORGED event row",
                       project_dir=root, source="cli", writer=Writer.HUMAN)
    out = _out(capsys, "blame", item_id, "--project", root)
    assert "done FORGED event row" in out
    assert not any(ln.strip().startswith("FORGED") for ln in out.splitlines())


def test_a_restated_wording_is_shown_on_one_line(tmp_path, monkeypatch):
    from daimon_briefing import view
    from daimon_briefing.cli import history
    root, _ = _project(tmp_path, monkeypatch)
    shown = history._was("old\nFORGED wording", view.snapshot(root))
    assert shown == 'was "old FORGED wording"'


def test_status_lists_a_superseded_candidate_on_one_line(
        tmp_path, monkeypatch, capsys):
    root, item_id = _project(tmp_path, monkeypatch)
    store.append_event(item_id, "supersede-candidate:d-abcdef01",
                       project_dir=root, source="cli", writer=Writer.HUMAN)
    out = _out(capsys, "status", "--suppressed", "--project", root)
    assert "likely superseded" in out
    _assert_one_line(out)


def test_the_amendment_card_prints_its_stored_text_on_one_line():
    from daimon_briefing.cli import lifecycle
    card = lifecycle._amendment_card({
        "kind": "amend", "id": "a-1", "waiting_since": "",
        "amend": {"loop_id": "o-1", "loop_text": "loop\nFORGED row",
                  "evidence": "quote\nFORGED row", "note": "n\nFORGED row"}})
    assert not any(ln.strip().startswith("FORGED") for ln in card)
    assert any("loop FORGED row" in ln for ln in card)
    assert any("n FORGED row" in ln for ln in card)


def _attach_project(tmp_path, monkeypatch, texts):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "mod.py").write_text("def fn():\n    pass\n", encoding="utf-8")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(root))
    store.write_checkpoint("S-att", {
        "session_id": "S-att", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": t, "trust": "inferred"} for t in texts]},
        "epistemic_snapshot": {}},
        project_dir=str(root), writer=Writer.HUMAN)
    return str(root)


def test_anchor_attach_echo_prints_the_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root = _attach_project(tmp_path, monkeypatch, ["wire it\nFORGED echo"])
    out = _out(capsys, "anchor", "mod.py", "fn", "--attach", "wire it",
               "--project", root)
    assert "wire it FORGED echo" in out
    assert not any(ln.strip().startswith("FORGED") for ln in out.splitlines())


def test_anchor_attach_ambiguity_lists_each_item_on_one_line(
        tmp_path, monkeypatch, capsys):
    root = _attach_project(tmp_path, monkeypatch,
                           ["wire it a\nFORGED one", "wire it b\nFORGED two"])
    out = _out(capsys, "anchor", "mod.py", "fn", "--attach", "wire it",
               "--project", root)
    assert "wire it a FORGED one" in out
    assert not any(ln.strip().startswith("FORGED") for ln in out.splitlines())
