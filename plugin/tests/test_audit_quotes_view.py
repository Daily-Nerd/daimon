"""`audit quotes` reads the stored checkpoints through the view (#1132 PR 11b):
a quarantined or forgotten verbatim item is neither audited, counted nor
printed, `--all` visits each bucket the reader may see, and a closed trust
ledger audits nothing rather than everything."""

import json

import pytest

from daimon_briefing import cli, normalize, store, trust
from daimon_briefing.surfaces import Writer

GOOD = "adopt the D-007 prompt for the serializer"
BAD = "this sentence is nowhere in the source transcript"
HIDDEN_TEXT = "SENTINEL-hidden fabricated decision"


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    d = tmp_path / ".claude" / "projects"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(d))
    return d


def _transcript(projects_dir, slug, session_id, text):
    d = projects_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{session_id}.jsonl").write_text(
        json.dumps({"role": "user", "content": text}) + "\n", encoding="utf-8")


def _checkpoint(session_id, slug, decisions):
    return {"session_id": session_id, "created": "2026-07-07T10:00:00Z",
            "project_slug": slug,
            "working_context": {"active_topic": {"text": "t", "trust": "inferred"},
                                "open_questions": [],
                                "recent_decisions": decisions},
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []}}


def _store(project, projects_dir, session_id, decisions):
    slug = store.project_slug(project)
    _transcript(projects_dir, slug, session_id,
                f"we decided to {GOOD} last week")
    store.write_checkpoint(session_id, _checkpoint(session_id, slug, decisions),
                           project_dir=project, writer=Writer.HUMAN)


def _verbatim(text, quote):
    return {"text": text, "trust": "verbatim", "quote": quote}


def _run(capsys, *argv):
    rc = cli.main(["audit", "quotes", *argv])
    out = capsys.readouterr()
    return rc, out.out + out.err


def test_a_quarantined_failing_quote_is_not_audited_counted_or_printed(
        tmp_checkpoint_dir, projects_dir, capsys):
    _store("/p/A", projects_dir, "SA", [
        _verbatim("a good decision", GOOD), _verbatim(HIDDEN_TEXT, BAD)])
    trust.propose(text=HIDDEN_TEXT, kind="decision", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir="/p/A")
    rc, out = _run(capsys, "--project", "/p/A", "--json")
    report = json.loads(out)
    assert rc == 0
    assert report["checkable"] == 1 and report["failed"] == 0
    assert report["failures"] == []
    assert HIDDEN_TEXT not in out and BAD not in out
    rc, out = _run(capsys, "--project", "/p/A")
    assert rc == 0 and "SENTINEL-hidden" not in out


def test_a_forgotten_failing_quote_is_not_audited_or_printed(
        tmp_checkpoint_dir, projects_dir, capsys):
    _store("/p/A", projects_dir, "SA", [
        _verbatim("a good decision", GOOD), _verbatim(HIDDEN_TEXT, BAD)])
    key = normalize.content_key(HIDDEN_TEXT)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir="/p/A", writer=Writer.HUMAN)
    rc, out = _run(capsys, "--project", "/p/A", "--json")
    report = json.loads(out)
    assert rc == 0 and report["checkable"] == 1 and report["failures"] == []
    assert "SENTINEL-hidden" not in out


def test_a_visible_failing_quote_is_still_reported(tmp_checkpoint_dir,
                                                   projects_dir, capsys):
    _store("/p/A", projects_dir, "SA", [
        _verbatim("a good decision", GOOD),
        _verbatim("a visible fabricated decision", BAD)])
    rc, out = _run(capsys, "--project", "/p/A")
    assert rc == 1 and "a visible fabricated decision" in out


def test_all_visits_every_bucket_and_default_visits_one(tmp_checkpoint_dir,
                                                        projects_dir, capsys):
    _store("/p/A", projects_dir, "SA", [_verbatim("a", GOOD)])
    _store("/p/B", projects_dir, "SB", [_verbatim("b", GOOD)])
    _, narrow = _run(capsys, "--project", "/p/A", "--json")
    _, wide = _run(capsys, "--project", "/p/A", "--all", "--json")
    assert json.loads(narrow)["scanned"] == 1
    assert json.loads(wide)["scanned"] == 2


def test_a_closed_trust_ledger_audits_nothing(tmp_checkpoint_dir, projects_dir,
                                              capsys):
    _store("/p/A", projects_dir, "SA", [
        _verbatim("a good decision", GOOD), _verbatim(HIDDEN_TEXT, BAD)])
    path = store.ledger_file("/p/A", "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")
    rc, out = _run(capsys, "--project", "/p/A", "--json")
    report = json.loads(out)
    assert rc == 3 and report["checkable"] == 0
    assert "SENTINEL-hidden" not in out
