"""PR 12: `daimon unforget` lifts a forget on every ref that carries its key.

A forget now stands on the item id, on every sibling id and on a key ref of
its own, so lifting it means lifting all of them in one append. The verb is
human-only, binds by the exact id the forget receipt printed, and says out loud
when something else still withholds the value (another project, a teammate's
published row, a ledger it could not prove) instead of reporting a lift that
did not happen.
"""
import json
from datetime import datetime

import pytest

from daimon_briefing import cli, config, forget, jsonl, normalize, store, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/unforget"
OTHER = "/p/unforget-other"
VALUE = "zqxunforget3310 the artifact bucket is rotated every quarter"
KEEPER = "an unrelated decision that must survive"
KEY = normalize.content_key(VALUE)


def _item(text):
    return {"text": text, "trust": "inferred"}


def _seed(project=PROJECT):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [_item(VALUE), _item(KEEPER)],
                            "open_questions": [_item(VALUE)]},
    }, project_dir=project, writer=Writer.HUMAN)
    stored = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    return (next(i["id"] for i in stored["working_context"]["recent_decisions"]
                 if i["text"] == VALUE),
            next(i["id"] for i in stored["working_context"]["open_questions"]
                 if i["text"] == VALUE))


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _forgotten(tty):
    """A project that forgot VALUE through the real verb; the decision id."""
    decision, question = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    return decision, question


def _unforget(decision, *extra, project=PROJECT):
    return cli.main(["unforget", decision, "--project", project, *extra])


def _events_rows():
    return jsonl.read(store._events_path(store._resolved(PROJECT))).rows


# ---- the channel --------------------------------------------------------------

def test_an_agent_channel_is_refused_and_writes_nothing(tmp_checkpoint_dir,
                                                        monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    decision, _question = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    capsys.readouterr()
    before = store._events_path(store._resolved(PROJECT)).read_bytes()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    assert _unforget(decision) == 1
    refusal = capsys.readouterr().out
    assert "requires an interactive terminal" in refusal
    assert store._events_path(store._resolved(PROJECT)).read_bytes() == before


def test_the_refusal_is_identical_for_a_forgotten_id_an_unknown_one_and_a_dry_run(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    decision, _question = _seed()
    assert cli.main(["forget", decision, "--project", PROJECT]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)
    outputs = []
    for argv in (["unforget", decision, "--project", PROJECT],
                 ["unforget", "r-000000000000", "--project", PROJECT],
                 ["unforget", decision, "--project", PROJECT, "--dry-run"],
                 ["unforget", "r-000000000000", "--project", PROJECT,
                  "--dry-run"]):
        rc = cli.main(argv)
        outputs.append((rc, capsys.readouterr()))
    assert len({(rc, out.out, out.err) for rc, out in outputs}) == 1
    assert outputs[0][0] == 1


def test_there_is_no_flag_that_declares_an_agent_channel(capsys):
    with pytest.raises(SystemExit):
        cli.main(["unforget", "r-000000000000", "--by", "agent"])
    capsys.readouterr()


# ---- the lift -------------------------------------------------------------------

def test_one_append_lifts_the_id_the_siblings_and_the_key_ref(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    decision, question = _forgotten(tty)
    capsys.readouterr()
    appends = []
    real = jsonl.append_lines
    monkeypatch.setattr(jsonl, "append_lines", lambda path, lines, **kw: (
        appends.append((path.name, list(lines))), real(path, lines, **kw))[1])
    assert _unforget(decision, "--reason", "was wrong to forget") == 0
    out = capsys.readouterr().out
    [lines] = [lines for name, lines in appends if name == "events.jsonl"]
    rows = [json.loads(line) for line in lines]
    assert len(rows) == 3
    assert {r["item_ref"] for r in rows if not store.is_key_ref(r["item_ref"])} \
        == {decision, question}
    assert sum(store.is_key_ref(r["item_ref"]) for r in rows) == 1
    for row in rows:
        assert row["status"] == store.LIFT_STATUS and row["kind"] == "lift"
        assert row["source"] == "cli" and row["note"] == "was wrong to forget"
    assert KEY not in store.forgotten_content_keys(PROJECT)
    assert store.project_standing(PROJECT) == {}
    assert "the row your teammates already folded stays" in out
    assert KEY not in out and VALUE not in out and "forgotten-key" not in out


def test_a_forget_and_an_unforget_in_the_same_second_leave_the_value_lifted(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 10, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(store, "datetime", Frozen)
    decision, _ = _forgotten(tty)
    assert KEY in store.forgotten_content_keys(PROJECT)
    assert _unforget(decision) == 0
    assert KEY not in store.forgotten_content_keys(PROJECT)
    capsys.readouterr()


def test_the_lift_restores_nothing_and_a_new_capture_is_admitted_again(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    assert _unforget(decision) == 0
    capsys.readouterr()
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    assert VALUE not in json.dumps(stored)
    store.write_checkpoint("S2", {
        "session_id": "S2", "created": "2026-08-02T00:00:00Z",
        "working_context": {"recent_decisions": [_item(VALUE)]},
    }, project_dir=PROJECT, writer=Writer.HUMAN)
    again = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                   admit=store.Admit.ANY)
    assert VALUE in json.dumps(again)


def test_the_memos_drop_with_the_lift(tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    slug = store.project_slug(config.resolve_project_dir(PROJECT))
    assert KEY in store.all_forgotten_content_keys()
    assert KEY in view.judge(slug).snap.forgotten
    assert _unforget(decision) == 0
    capsys.readouterr()
    assert KEY not in store.all_forgotten_content_keys()
    assert KEY not in view.judge(slug).snap.forgotten
    assert KEY not in view.forgotten_keys()


def test_a_legacy_one_row_tombstone_lifts_with_a_single_row(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _seed()
    assert store.append_event(
        decision, f"forgotten:{KEY}", kind="tombstone", project_dir=PROJECT,
        allow_disabled=True, tombstone=True, writer=Writer.HUMAN)
    assert _unforget(decision) == 0
    capsys.readouterr()
    lifts = [r for r in _events_rows() if r.get("kind") == "lift"]
    assert [r["item_ref"] for r in lifts] == [decision]
    assert KEY not in store.forgotten_content_keys(PROJECT)


def test_an_id_whose_forget_was_already_lifted_is_a_clean_no_op(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    assert _unforget(decision) == 0
    before = store._events_path(store._resolved(PROJECT)).read_bytes()
    capsys.readouterr()
    assert _unforget(decision) == 0
    assert "already lifted" in capsys.readouterr().out
    assert store._events_path(store._resolved(PROJECT)).read_bytes() == before


def test_an_id_that_was_never_forgotten_is_unknown(tmp_checkpoint_dir, tty,
                                                   capsys):
    decision, _ = _seed()
    assert _unforget(decision) == 1
    assert "no forgotten item with that id" in capsys.readouterr().out
    assert [r for r in _events_rows() if r.get("kind") == "lift"] == []


def test_dry_run_counts_the_refs_and_writes_nothing(tmp_checkpoint_dir, tty,
                                                    capsys):
    decision, _ = _forgotten(tty)
    capsys.readouterr()
    before = store._events_path(store._resolved(PROJECT)).read_bytes()
    assert _unforget(decision, "--dry-run") == 0
    assert "would lift 3 ref(s)" in capsys.readouterr().out
    assert store._events_path(store._resolved(PROJECT)).read_bytes() == before


def test_a_lift_is_never_written_while_daimon_is_disabled(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    decision, _ = _forgotten(tty)
    capsys.readouterr()
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert _unforget(decision) == 1
    assert "not written" in capsys.readouterr().out
    assert KEY in store.forgotten_content_keys(PROJECT)


# ---- the verdict: it proves the lift or exits 4 -------------------------------------

def test_another_project_that_forgot_the_value_keeps_it_withheld(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    # another project on this machine forgot the same value (a second forget
    # of it by the verb is refused: the value already reads as forgotten)
    assert forget.write_tombstones(
        forget.Target(KEY, "i-other", ("i-other",), OTHER)).reached
    capsys.readouterr()
    other_bytes = store._events_path(store._resolved(OTHER)).read_bytes()
    assert _unforget(decision) == 4
    out = capsys.readouterr().out
    assert "still withheld by another project on this machine" in out
    assert OTHER not in out and store.project_slug(OTHER) not in out
    assert store._events_path(store._resolved(OTHER)).read_bytes() == other_bytes
    assert KEY not in store.forgotten_content_keys(PROJECT)


def _teammate_publishes(key=KEY, author="Grace"):
    adir = config.team_dir() / "team-a" / "authors" / author
    adir.mkdir(parents=True)
    with open(adir / store._TOMBSTONE_NAME, "ab") as handle:
        handle.write((json.dumps({"ts": "2026-08-02T00:00:00Z", "key": key,
                                  "author": author}) + "\n").encode())


def test_a_teammates_published_row_keeps_it_withheld(tmp_checkpoint_dir, tty,
                                                     monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    decision, _ = _forgotten(tty)
    _teammate_publishes()
    capsys.readouterr()
    assert _unforget(decision) == 4
    out = capsys.readouterr().out
    assert "withheld by a teammate's published row" in out
    assert "Grace" not in out and "team-a" not in out
    assert KEY not in store.forgotten_content_keys(PROJECT)


def test_a_ledger_that_cannot_be_proven_is_exit_4_with_no_name(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    assert forget.write_tombstones(
        forget.Target(KEY, "i-other", ("i-other",), OTHER)).reached
    capsys.readouterr()
    other = store._events_path(store._resolved(OTHER))
    mode = other.stat().st_mode
    other.chmod(0)
    try:
        assert _unforget(decision) == 4
        out = capsys.readouterr().out
    finally:
        other.chmod(mode)
    assert "a ledger could not be proven" in out
    assert store.project_slug(OTHER) not in out


def test_a_teammates_unproven_ledger_is_exit_4(tmp_checkpoint_dir, tty,
                                               monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    decision, _ = _forgotten(tty)
    adir = config.team_dir() / "team-a" / "authors" / "Grace"
    adir.mkdir(parents=True)
    with open(adir / store._TOMBSTONE_NAME, "ab") as handle:
        handle.write(b'{"ts": "2026-08-02T00:00:00Z", "key": "00aa')   # torn
    capsys.readouterr()
    assert _unforget(decision) == 4
    assert "a ledger could not be proven" in capsys.readouterr().out
    assert KEY not in store.forgotten_content_keys(PROJECT)


def test_the_own_published_row_stays_and_is_not_republished(
        tmp_checkpoint_dir, tty, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    decision, _ = _forgotten(tty)
    own = list((config.team_dir()).rglob(store._TOMBSTONE_NAME))
    assert own and KEY in own[0].read_text(encoding="utf-8")
    assert _unforget(decision) == 0
    capsys.readouterr()
    assert KEY in own[0].read_text(encoding="utf-8")
    assert store.republish_tombstones(project_dir=PROJECT).keys == frozenset()
    assert KEY not in store.foreign_forgotten_content_keys()


# ---- the activity feed -------------------------------------------------------------

def test_the_lift_row_reads_as_a_lift_in_the_activity_feed(
        tmp_checkpoint_dir, tty, capsys):
    from daimon_ui import reader
    decision, _ = _forgotten(tty)
    assert _unforget(decision) == 0
    capsys.readouterr()
    slug = store.project_slug(config.resolve_project_dir(PROJECT))
    rows = reader.project_activity(slug)["rows"]
    lifts = [r for r in rows if r["kind"] == "lift"]
    assert lifts and all(r["extra"]["status"] == store.LIFT_STATUS
                         for r in lifts)
    assert not [r for r in rows if store.is_key_ref(r["item_ref"] or "")]
    assert "forgotten-key" not in json.dumps(rows)


def test_the_key_of_an_id_is_read_from_its_last_tombstone_row(
        tmp_checkpoint_dir, tty, capsys):
    decision, question = _forgotten(tty)
    capsys.readouterr()
    assert forget.key_of(PROJECT, decision) == KEY
    assert forget.key_of(PROJECT, question) == KEY
    assert forget.key_of(PROJECT, "r-000000000000") == ""
    # still found after a legacy reopen replaced the id's latest event
    store.append_event(decision, "reopen", project_dir=PROJECT,
                       writer=Writer.HUMAN)
    assert forget.key_of(PROJECT, decision) == KEY


def test_a_lift_with_nothing_standing_writes_nothing(tmp_checkpoint_dir):
    _seed()
    assert forget.lift(PROJECT, KEY) == forget.Lifted((), True)
    assert [r for r in _events_rows() if r.get("kind") == "lift"] == []


def test_a_second_unforget_still_says_another_project_withholds_the_value(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    assert forget.write_tombstones(
        forget.Target(KEY, "i-other", ("i-other",), OTHER)).reached
    assert _unforget(decision) == 4
    capsys.readouterr()
    before = store._events_path(store._resolved(PROJECT)).read_bytes()
    assert _unforget(decision) == 4
    out = capsys.readouterr().out
    assert "still withheld by another project on this machine" in out
    assert "lifted the forget" not in out
    assert store._events_path(store._resolved(PROJECT)).read_bytes() == before


def test_a_retry_after_exit_4_cannot_turn_green_while_a_ledger_is_unproven(
        tmp_checkpoint_dir, tty, capsys):
    decision, _ = _forgotten(tty)
    assert forget.write_tombstones(
        forget.Target("0123456789abcdef", "i-other", ("i-other",), OTHER)).reached
    other = store._events_path(store._resolved(OTHER))
    mode = other.stat().st_mode
    other.chmod(0)
    try:
        assert _unforget(decision) == 4
        capsys.readouterr()
        # nothing stands here any more, but the verdict still runs
        assert _unforget(decision) == 4
        out = capsys.readouterr().out
        assert "already lifted here" in out
        assert "a ledger could not be proven" in out
    finally:
        other.chmod(mode)
    assert _unforget(decision) == 0
    assert "already lifted here" in capsys.readouterr().out


def test_the_lift_proves_itself_through_the_views_machine_wide_set():
    import inspect

    from daimon_briefing.cli import lifecycle
    assert "view.machine_sets(" in inspect.getsource(lifecycle._cmd_unforget)
