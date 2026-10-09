"""`cli.guarded` words its one error line for what the verb does: a read verb
shows nothing when its view fails, a write verb may have written part of its
work before it stopped, so it says it stopped and where to look."""

import pytest

from daimon_briefing import cli, store, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/guarded-wording"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _boom(*_a, **_k):
    raise RuntimeError("secret detail that must not be printed")


def test_a_read_verb_keeps_the_nothing_was_shown_wording(
        tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setattr(view, "lineage", _boom)
    assert cli.main(["blame", "o-aaaaaaaaaaaa"]) == 2
    err = capsys.readouterr().err
    assert err == ("error: blame could not be read (RuntimeError); "
                   "nothing was shown\n")


@pytest.mark.parametrize("argv,verb", [
    (["resolve", "o-aaaaaaaaaaaa"], "resolve"),
    (["reverify", "o-aaaaaaaaaaaa", "--evidence", "x"], "reverify"),
    (["amend", "o-aaaaaaaaaaaa", "--change", "progressed", "--evidence", "x"],
     "amend propose"),
    (["forget", "o-aaaaaaaaaaaa"], "forget"),
], ids=["resolve", "reverify", "amend", "forget"])
def test_a_write_verb_says_it_stopped_and_where_to_look(
        tmp_checkpoint_dir, capsys, monkeypatch, argv, verb):
    store.write_checkpoint(
        "S-1", {"session_id": "S-1", "working_context": {"open_questions": [
            {"text": "a question", "trust": "inferred"}]}},
        project_dir=PROJECT, writer=Writer.HUMAN)
    monkeypatch.setattr(view, "match", _boom)
    monkeypatch.setattr(view, "judge", _boom)
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert err == (f"error: {verb} stopped (RuntimeError); run daimon status "
                   "to see the ledgers\n")
    assert "secret detail" not in err


def test_anchor_attach_says_it_stopped(tmp_checkpoint_dir, capsys, monkeypatch,
                                       tmp_path):
    root = (tmp_path / "proj").resolve()
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "m.py").write_text("def foo():\n    return 1\n")
    monkeypatch.setattr(view, "match", _boom)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "x",
                   "--project", str(root)])
    assert rc == 2
    assert capsys.readouterr().err == (
        "error: anchor stopped (RuntimeError); run daimon status to see the "
        "ledgers\n")
