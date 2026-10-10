"""PR 13 (D6): what a reader is told about a teammate's quarantine.

The marker names no id and no author; the cure is the teammate's. A teammate's
quarantine withholds on every channel, a terminal included (H8): the human
exemptions exist for the OWNER of a quarantine. The published file is planted
by hand: this is the reader's side."""

import json

import pytest

from daimon_briefing import config, display, refutations, store, view
from daimon_briefing.surfaces import Writer
from tests import _masking as m

TEAMMATE_CURE = "the withheld text is quarantined by a teammate; they release it"


@pytest.fixture(autouse=True)
def _me(monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")


def _publish(text=m.QUARANTINED, *, author="grace", kind="decision",
             state="active", order=1, event_id="e1"):
    from daimon_briefing import trust
    adir = config.team_dir() / "team-a" / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    key = trust.value_key(text)
    row = {"version": 1, "ts": "2026-10-09T12:00:00Z", "order": order,
           "event_id": event_id, "quarantine_id": "tr-" + key[:12],
           "kind": kind, "value_key": key, "state": state, "author": author}
    with open(adir / "quarantines.jsonl", "ab") as handle:
        handle.write(json.dumps(row).encode() + b"\n")


def _record(**kw):
    values = dict(subject="a plain subject", verdict=m.VISIBLE, scope="census",
                  evidence=["measurement:replay"], channel="cli-agent",
                  project_dir=m.PROJECT)
    values.update(kw)
    return refutations.assert_refutation(**values)


# ---- the notes name both ledgers ---------------------------------------------


def test_the_author_notes_name_the_forget_and_quarantine_ledgers():
    assert display.author_skipped_note().endswith(
        "a teammate's published forget or quarantine ledger cannot be read; "
        "their checkpoints are not admitted")
    assert not hasattr(display, "author_degraded_note")


def test_an_unreadable_quarantine_file_is_the_same_note(tmp_checkpoint_dir):
    adir = config.team_dir() / "team-a" / "authors" / "grace"
    adir.mkdir(parents=True)
    (adir / "quarantines.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert view.team_notes() == (display.author_skipped_note(),)


# ---- the cure line -----------------------------------------------------------


def test_every_refute_verb_refuses_on_every_channel_and_names_the_teammate(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _record(verdict=m.QUARANTINED)
    _publish()
    for tty, extra in ((True, ()), (False, ("--by", "agent"))):
        m.human(monkeypatch, tty=tty)
        for verb, args in (("ratify", ()),
                           ("revise", ("--verdict", "new", "--evidence",
                                       "measurement:x")),
                           ("overturn", ("--evidence", "measurement:x"))):
            rc, out, err = m.run(capsys, "refute", verb, rid, *args, *extra)
            assert rc == 2 and "SECRET" not in out + err, (verb, tty)
            assert f"refute {verb} refused" in err, (verb, tty)
            assert f"note: {TEAMMATE_CURE}" in err, (verb, tty)
            assert "trust show" not in err and "trust release" not in err


def test_an_own_quarantine_keeps_its_own_cure(tmp_checkpoint_dir, capsys,
                                              monkeypatch):
    rid = _record(verdict=m.QUARANTINED)
    tid = m.quarantine()
    _publish()
    m.human(monkeypatch, tty=True)
    rc, _out, err = m.run(capsys, "refute", "ratify", rid)
    assert rc == 2 and f"daimon trust release {tid}" in err
    assert TEAMMATE_CURE not in err


# ---- trust list --------------------------------------------------------------


def test_trust_list_has_a_footer_when_teammates_quarantines_are_in_force(
        tmp_checkpoint_dir, capsys, monkeypatch):
    m.human(monkeypatch, tty=False)
    _publish()
    _publish(m.FORGOTTEN, author="kay")
    rc, out, _err = m.run(capsys, "trust", "list")
    assert rc == 0
    assert "no quarantines recorded for this project" in out
    assert ("2 quarantine(s) published by teammates are in force; they "
            "release their own") in out
    assert "SECRET" not in out and "grace" not in out


def test_trust_list_footer_follows_the_own_rows(tmp_checkpoint_dir, capsys,
                                                monkeypatch):
    m.human(monkeypatch, tty=False)
    tid = m.quarantine()
    _publish(m.FORGOTTEN)
    rc, out, _err = m.run(capsys, "trust", "list")
    assert rc == 0 and tid in out
    assert out.rstrip().splitlines()[-1].startswith("1 quarantine(s) published")


def test_trust_list_has_no_footer_without_teammates(tmp_checkpoint_dir, capsys,
                                                    monkeypatch):
    m.human(monkeypatch, tty=False)
    m.quarantine()
    rc, out, _err = m.run(capsys, "trust", "list")
    assert rc == 0 and "published by teammates" not in out


def test_trust_list_json_is_unchanged_by_a_teammates_quarantine(
        tmp_checkpoint_dir, capsys, monkeypatch):
    m.human(monkeypatch, tty=False)
    _publish()
    rc, out, _err = m.run(capsys, "trust", "list", "--json")
    assert rc == 0 and json.loads(out) == []


def test_team_listing_off_a_terminal_is_kind_and_count_only(
        tmp_checkpoint_dir, capsys, monkeypatch):
    m.human(monkeypatch, tty=False)
    _publish()
    _publish(m.FORGOTTEN, author="kay")
    _publish(m.VISIBLE, author="kay", kind="belief")
    rc, out, _err = m.run(capsys, "trust", "list", "--team")
    assert rc == 0
    assert "belief" in out and "decision" in out
    for word in ("grace", "kay", "2026-10-09", "SECRET"):
        assert word not in out
    rc, out, _err = m.run(capsys, "trust", "list", "--team", "--json")
    assert json.loads(out) == [{"kind": "belief", "count": 1},
                               {"kind": "decision", "count": 2}]


def test_team_listing_at_a_terminal_adds_author_directory_and_ts(
        tmp_checkpoint_dir, capsys, monkeypatch):
    m.human(monkeypatch, tty=True)
    _publish()
    rc, out, _err = m.run(capsys, "trust", "list", "--team")
    assert rc == 0 and "grace" in out and "decision" in out
    assert "2026-10-09T12:00:00Z" in out and "SECRET" not in out
    rc, out, _err = m.run(capsys, "trust", "list", "--team", "--json")
    assert json.loads(out) == [{"kind": "decision", "author": "grace",
                                "ts": "2026-10-09T12:00:00Z"}]


def test_team_listing_without_claims_says_so(tmp_checkpoint_dir, capsys,
                                             monkeypatch):
    m.human(monkeypatch, tty=False)
    rc, out, _err = m.run(capsys, "trust", "list", "--team")
    assert rc == 0 and "no quarantines published by teammates" in out
    rc, out, _err = m.run(capsys, "trust", "list", "--team", "--json")
    assert rc == 0 and json.loads(out) == []


def test_team_quarantines_is_the_views_claims(tmp_checkpoint_dir):
    _publish()
    assert view.team_quarantines() == (
        ("decision", "grace", "2026-10-09T12:00:00Z"),)


# ---- forget ------------------------------------------------------------------


def _item(text=m.QUARANTINED, item_id="d-aaaaaa"):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": text, "id": item_id}]},
          "epistemic_snapshot": {}}
    store.write_checkpoint("S-1", cp, project_dir=m.PROJECT,
                           writer=Writer.HUMAN)
    return item_id


def _events():
    return (config.checkpoint_dir() / m.SLUG / "events.jsonl")


@pytest.mark.parametrize("tty", [True, False])
def test_forget_on_a_teammates_pair_is_refused_on_every_channel(
        tmp_checkpoint_dir, capsys, monkeypatch, tty):
    iid = _item()
    _publish()
    m.human(monkeypatch, tty=tty)
    rc, out, err = m.run(capsys, "forget", iid)
    assert rc == 2 and "SECRET" not in out + err
    assert "a teammate quarantined this; they release it" in err
    assert not _events().exists() or "forgotten" not in _events().read_text()


def test_forget_at_a_terminal_still_works_on_an_own_quarantine(
        tmp_checkpoint_dir, capsys, monkeypatch):
    iid = _item()
    m.quarantine()
    _publish()
    m.human(monkeypatch, tty=True)
    rc, _out, err = m.run(capsys, "forget", iid)
    assert rc == 0, err


def test_forget_dry_run_on_a_teammates_pair_is_refused_too(
        tmp_checkpoint_dir, capsys, monkeypatch):
    iid = _item()
    _publish()
    m.human(monkeypatch, tty=True)
    rc, _out, err = m.run(capsys, "forget", iid, "--dry-run")
    assert rc == 2 and "a teammate quarantined this" in err


# ---- the stale text ----------------------------------------------------------


def test_the_trust_help_no_longer_says_write_only():
    from daimon_briefing import cli
    from daimon_briefing.cli import trust as cli_trust
    assert "write-only" not in (cli_trust.__doc__ or "")
    parser = cli.build_parser()
    help_text = parser.format_help() + "".join(
        a.help or "" for a in parser._actions)
    assert "write-only in this release" not in help_text


# ---- fix round 1: every exact-id binding verb, and nothing raw on a terminal --


def _no_event_for(item_id):
    return (not _events().exists()) or item_id not in _events().read_text()


@pytest.mark.parametrize("tty", [True, False])
@pytest.mark.parametrize("verb,extra", [
    ("resolve", ()), ("reverify", ("--evidence", "checked it")),
    ("forget", ())])
def test_every_exact_id_verb_refuses_a_teammates_pair_on_every_channel(
        tmp_checkpoint_dir, capsys, monkeypatch, tty, verb, extra):
    iid = _item()
    _publish()
    m.human(monkeypatch, tty=tty)
    argv = (["--by", "agent", "--evidence", "quoted"] if verb == "resolve"
            and not tty else [])
    rc, out, err = m.run(capsys, verb, iid, *extra, *argv)
    if verb == "reverify" and not tty:
        # reverify is a human verb: refused before the bind, nothing written
        assert rc == 1 and _no_event_for(iid)
        return
    assert rc == 2, (verb, tty, out, err)
    assert "a teammate quarantined this; they release it" in err
    assert "SECRET" not in out + err
    assert _no_event_for(iid), (verb, tty)


@pytest.mark.parametrize("verb,extra", [
    ("resolve", ()), ("reverify", ("--evidence", "checked it"))])
def test_the_owner_of_a_quarantine_still_binds_it_at_a_terminal(
        tmp_checkpoint_dir, capsys, monkeypatch, verb, extra):
    iid = _item()
    m.quarantine()
    _publish()          # the pair is also a teammate's, but the owner's id wins
    m.human(monkeypatch, tty=True)
    rc, _out, err = m.run(capsys, verb, iid, *extra)
    assert rc == 0, err


def test_teammate_pair_is_one_helper():
    from daimon_briefing.cli import _ledger
    from daimon_briefing.view import Withheld
    teammate = Withheld("d-1", "decision", "quarantine", None, "k")
    owner = Withheld("d-1", "decision", "quarantine", "tr-0123456789ab", "k")
    closed = Withheld("d-1", "decision", "closed", None, "k")
    assert _ledger.teammate_pair(teammate)
    assert not _ledger.teammate_pair(owner)
    assert not _ledger.teammate_pair(closed)
    assert not _ledger.teammate_pair(None)


@pytest.mark.parametrize("tty", [True, False])
def test_nothing_from_a_teammates_file_reaches_the_terminal_unvalidated(
        tmp_checkpoint_dir, capsys, monkeypatch, tty):
    from daimon_briefing import trust
    m.human(monkeypatch, tty=tty)
    key = trust.value_key(m.QUARANTINED)
    for author, ts in (("grace", "\x1b]0;PWNED\x07\x1b[2Jfake"),
                       ("gr\x1b[2Jace", "2026-10-09T12:00:00Z"),
                       ("kay", "2026-10-09T12:00:00Z")):
        adir = config.team_dir() / "team-a" / "authors" / author
        adir.mkdir(parents=True, exist_ok=True)
        row = {"version": 1, "ts": ts, "order": 1, "event_id": "a" * 32,
               "quarantine_id": "tr-" + key[:12], "kind": "decision",
               "value_key": key, "state": "active", "author": author}
        with open(adir / "quarantines.jsonl", "ab") as handle:
            handle.write(json.dumps(row).encode() + b"\n")
    for flags in ((), ("--json",)):
        rc, out, err = m.run(capsys, "trust", "list", "--team", *flags)
        assert rc == 0 and "\x1b" not in out + err and "\x07" not in out + err
    rc, out, _err = m.run(capsys, "trust", "list", "--team")
    assert "kay" in out or not tty
