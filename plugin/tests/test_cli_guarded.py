"""`cli.guarded` (#1132 PR 11a): a read verb that answers through the view
turns any exception into one `error:` line and exit 2, with nothing rendered
around it. `jsonl.Refused` is the one exception that passes through, because
`main` already prints it (10b)."""

import pytest

from daimon_briefing import cli, jsonl, store, view
from daimon_briefing.cli import history, search
from daimon_briefing.surfaces import Writer

PROJECT = "/p/guarded"


def test_the_return_value_of_a_verb_passes_through():
    @cli.guarded
    def _cmd_probe(args):
        return 7

    assert _cmd_probe(None) == 7


def test_an_exception_is_one_line_naming_the_verb_and_the_type(capsys):
    @cli.guarded
    def _cmd_probe_verb(args):
        print("half an answer")        # a verb prints only after the view
        raise RuntimeError("SECRET message that could carry content")

    assert _cmd_probe_verb(None) == 2
    out, err = capsys.readouterr()
    assert err == ("error: probe verb could not be read (RuntimeError); "
                   "nothing was shown\n")
    assert "SECRET" not in out + err


def test_a_refusal_passes_through_for_main_to_print():
    @cli.guarded
    def _cmd_probe(args):
        raise jsonl.Refused("events.jsonl", "unreadable", "EIO", "run: daimon status")

    with pytest.raises(jsonl.Refused):
        _cmd_probe(None)


def test_the_wrapper_keeps_the_verbs_name_and_docstring():
    @cli.guarded
    def _cmd_probe(args):
        """Documented."""

    assert _cmd_probe.__name__ == "_cmd_probe"
    assert _cmd_probe.__doc__ == "Documented."


@pytest.mark.parametrize("verb", [search._cmd_why, history._cmd_blame,
                                  history._cmd_diff])
def test_the_id_verbs_of_this_slice_are_guarded(verb):
    assert hasattr(verb, "__wrapped__"), verb.__name__


def _checkpoint():
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-09-01T10:00:00Z",
        "author": "ada",
        "working_context": {
            "active_topic": {"text": "guard", "trust": "inferred"},
            "recent_decisions": [{"text": "a fact that stays", "id":
                                  "r-aaaaaaaaaaaa", "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT, writer=Writer.HUMAN)
    store.write_checkpoint("S-2", {
        "session_id": "S-2", "created": "2026-09-02T10:00:00Z",
        "author": "ada",
        "working_context": {
            "active_topic": {"text": "guard", "trust": "inferred"},
            "recent_decisions": [{"text": "a fact that stays", "id":
                                  "r-aaaaaaaaaaaa", "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT, writer=Writer.HUMAN)


@pytest.mark.parametrize("argv, name", [
    (["why", "r-aaaaaaaaaaaa"], "lineage"),
    (["blame", "r-aaaaaaaaaaaa"], "lineage"),
    (["diff"], "snapshot"),
])
def test_a_view_that_raises_shows_nothing_and_exits_two(
        tmp_checkpoint_dir, monkeypatch, capsys, argv, name):
    _checkpoint()

    def boom(*_a, **_k):
        raise RuntimeError("the view failed")

    monkeypatch.setattr(view, name, boom)
    assert cli.main([*argv, f"--project={PROJECT}"]) == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert err.count("\n") == 1 and err.startswith("error: ")
    assert "could not be read (RuntimeError)" in err
    assert "a fact that stays" not in out + err
