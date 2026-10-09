"""`store.attach_anchor` and `daimon anchor --attach` (#1132 PR 11b, D11.5).

The write patches ONE item of the raw own body, found by `(section, key,
index)`, and rewrites the rest byte for byte: a quarantined item the view
filters out of every reader's body must survive the rewrite."""

import json

import pytest

from daimon_briefing import cli, store, trust
from daimon_briefing.surfaces import Writer

PROJECT = "/p/attach-anchor"
TOPIC = "the weekly sync cadence"
QUARANTINED = "SENTINEL-q1 the migration owner is unknown"
VISIBLE = "the migration plan needs a rollback step"
ANCHOR = {"qualified_name": "pkg/m.py::foo", "file": "pkg/m.py",
          "symbol": "foo", "body_hash": "0" * 64}


def _write():
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "active_topic": {"text": TOPIC, "trust": "inferred"},
              "open_questions": [{"text": QUARANTINED, "trust": "inferred"},
                                 {"text": VISIBLE, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)


def _raw():
    return store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                  admit=store.Admit.ANY)


def _quarantine():
    trust.propose(text=QUARANTINED, kind="question", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)


def test_the_topic_singleton_takes_an_anchor(tmp_checkpoint_dir):
    _write()
    path = store.attach_anchor(
        PROJECT, ("working_context", "active_topic", None), ANCHOR)
    assert path is not None
    assert _raw()["working_context"]["active_topic"]["anchored_to"] == ANCHOR


def test_a_list_item_takes_an_anchor_by_position(tmp_checkpoint_dir):
    _write()
    store.attach_anchor(PROJECT, ("working_context", "open_questions", 1), ANCHOR)
    questions = _raw()["working_context"]["open_questions"]
    assert "anchored_to" not in questions[0]
    assert questions[1]["anchored_to"] == ANCHOR


def test_the_rewrite_keeps_every_withheld_byte_in_place(tmp_checkpoint_dir):
    _write()
    _quarantine()
    before = _raw()["working_context"]["open_questions"][0]
    store.attach_anchor(PROJECT, ("working_context", "open_questions", 1), ANCHOR)
    after = _raw()["working_context"]["open_questions"]
    assert after[0] == before and after[0]["text"] == QUARANTINED
    assert after[1]["anchored_to"] == ANCHOR


def test_a_locator_that_no_longer_resolves_writes_nothing(tmp_checkpoint_dir):
    _write()
    latest = store.project_latest_path(PROJECT)
    bytes_before = latest.read_bytes()
    with pytest.raises(LookupError):
        store.attach_anchor(PROJECT, ("working_context", "open_questions", 9),
                            ANCHOR)
    with pytest.raises(LookupError):
        store.attach_anchor(PROJECT, ("working_context", "no_such_key", 0),
                            ANCHOR)
    assert latest.read_bytes() == bytes_before


def test_the_expected_text_guards_a_changed_body(tmp_checkpoint_dir):
    _write()
    with pytest.raises(LookupError):
        store.attach_anchor(PROJECT, ("working_context", "open_questions", 1),
                            ANCHOR, text="something else entirely")
    store.attach_anchor(PROJECT, ("working_context", "open_questions", 1),
                        ANCHOR, text=VISIBLE)


def test_no_checkpoint_and_no_session_id_are_refused(tmp_checkpoint_dir):
    with pytest.raises(LookupError):
        store.attach_anchor(PROJECT, ("working_context", "active_topic", None),
                            ANCHOR)
    cp = {"working_context": {"active_topic": {"text": TOPIC}}}
    store.write_checkpoint("S-x", cp, project_dir=PROJECT, writer=Writer.HUMAN)
    latest = store.project_latest_path(PROJECT)
    payload = json.loads(latest.read_text())
    payload.pop("session_id", None)
    latest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="session_id"):
        store.attach_anchor(PROJECT, ("working_context", "active_topic", None),
                            ANCHOR)


def test_a_disabled_write_returns_none_and_attaches_nothing(
        tmp_checkpoint_dir, monkeypatch):
    _write()
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert store.attach_anchor(
        PROJECT, ("working_context", "active_topic", None), ANCHOR) is None
    monkeypatch.delenv("DAIMON_DISABLE")
    assert "anchored_to" not in _raw()["working_context"]["active_topic"]


# ---- the verb ------------------------------------------------------------------


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = (tmp_path / "proj").resolve()
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text("def foo():\n    return 1\n")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    return root


def _store(root):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {
              "active_topic": {"text": TOPIC, "trust": "inferred"},
              "open_questions": [{"text": QUARANTINED, "trust": "inferred"},
                                 {"text": VISIBLE, "trust": "inferred"}]}}
    store.write_checkpoint("S-1", cp, project_dir=root, writer=Writer.HUMAN)
    trust.propose(text=QUARANTINED, kind="question", reason="fabricated",
                  evidence=["issue:1"], channel="cli-tty", project_dir=root)


def _raw_of(root):
    return store.read_latest_body(project_dir=root, route=store.Route.OWN,
                                  admit=store.Admit.ANY)


def test_the_verb_attaches_to_the_topic_singleton(tmp_checkpoint_dir, proj, capsys):
    _store(proj)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "weekly sync",
                   "--project", str(proj)])
    out = capsys.readouterr()
    assert rc == 0, out
    assert f"attached pkg/m.py::foo to: {TOPIC}" in out.out
    assert "anchored_to" in _raw_of(proj)["working_context"]["active_topic"]


def test_a_withheld_match_is_ignored_entirely(tmp_checkpoint_dir, proj, capsys):
    """The needle matches a quarantined item and a visible one: the visible
    one takes the anchor, the quarantined one is neither named nor counted,
    and it is still in the rewritten bytes."""
    _store(proj)
    before = _raw_of(proj)["working_context"]["open_questions"][0]
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "migration",
                   "--project", str(proj)])
    out = capsys.readouterr()
    assert rc == 0, out
    assert "SENTINEL-q1" not in out.out + out.err
    assert "withheld" not in (out.out + out.err).lower()
    questions = _raw_of(proj)["working_context"]["open_questions"]
    assert questions[0] == before
    assert questions[1]["anchored_to"]["qualified_name"] == "pkg/m.py::foo"


def test_two_visible_matches_list_only_visible_text(tmp_checkpoint_dir, proj, capsys):
    _store(proj)
    cp = _raw_of(proj)
    cp["working_context"]["open_questions"].append(
        {"text": "the migration schedule slips", "trust": "inferred"})
    store.write_checkpoint("S-2", dict(cp, session_id="S-2",
                           created="2026-08-02T00:00:00Z"),
                           project_dir=proj, writer=Writer.HUMAN)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "migration",
                   "--project", str(proj)])
    err = capsys.readouterr().err
    assert rc != 0
    assert VISIBLE in err and "the migration schedule slips" in err
    assert "SENTINEL-q1" not in err


def test_only_a_withheld_match_reads_as_no_match(tmp_checkpoint_dir, proj, capsys):
    _store(proj)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "owner is unknown",
                   "--project", str(proj)])
    err = capsys.readouterr().err
    assert rc != 0 and "no cognitive item text contains 'owner is unknown'" in err
    assert "SENTINEL-q1" not in err
    assert "anchored_to" not in _raw_of(proj)["working_context"]["open_questions"][0]
