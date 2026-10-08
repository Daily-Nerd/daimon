"""Forget says which surfaces it reached (#1132 PR 10b, D10.5).

Every deleter returns `Reached`, a list of the ids it removed (so every
`== [...]` assertion in the suites holds) that also says whether the ledger
was PROVEN and the value is gone. A torn line is written back verbatim by
`jsonl.rewrite` and may hold the value, so a plaintext ledger with one is NOT
reached. `daimon forget` lists what it did not reach and exits 4.
"""

import errno

import pytest

from daimon_briefing import (amendments, cli, config, jsonl, ledger_repair,
                             normalize, refutations, relations, requests,
                             store, trust)
from daimon_briefing.jsonl import Health, Reached

PROJECT = "/p/forget-reached"
VALUE = "zqxreachcanary5530 rotate the staging credentials on fridays"
KEY = normalize.content_key(VALUE)
ITEM = "r-abcabcabcabc"


# ---- seeds: one real row per ledger, by the real writer ---------------------

def _seed_refutations():
    refutations.assert_refutation(
        subject=VALUE, verdict="it deadlocked", scope="migrations",
        evidence=["measurement:trace-1"], channel="cli-tty", ratified=True,
        project_dir=PROJECT)
    return refutations._path(PROJECT)


def _seed_amendments():
    amendments.propose(item_id=ITEM, change="progressed", evidence=VALUE,
                       channel="cli-agent", project_dir=PROJECT)
    return amendments._path(PROJECT)


def _seed_requests():
    requests.open_request(to="-p-recipient", ask=VALUE, why="because",
                          channel="cli-agent", project_dir=PROJECT)
    return requests._path(PROJECT)


def _seed_trust():
    trust.propose(text=VALUE, kind="decision", reason="looks made up",
                  evidence=["issue:1"], channel="cli-tty", project_dir=PROJECT)
    return trust._path(PROJECT)


def _seed_relations():
    relations.propose(
        type_="revision-of",
        from_endpoint={"session_id": "S2", "field": "recent_decisions",
                       "item_id": ITEM},
        to_endpoint={"session_id": "S1", "field": "recent_decisions",
                     "item_id": "r-def123456789"},
        matched_by=["carry-absolute"], matcher_version="lineage-v1",
        channel="lab-import", project_dir=PROJECT)
    return relations._path(PROJECT)


# (seed, deleter, plaintext)
LEDGERS = {
    "refutations": (_seed_refutations,
                    lambda: refutations.forget_content_key(
                        KEY, project_dir=PROJECT), True),
    "amendments": (_seed_amendments,
                   lambda: amendments.forget_content_key(
                       KEY, project_dir=PROJECT), True),
    "requests": (_seed_requests,
                 lambda: requests.forget_content_key(
                     KEY, project_dir=PROJECT), True),
    "trust": (_seed_trust,
              lambda: trust.redact_content_key(KEY, project_dir=PROJECT),
              True),
    "relations": (_seed_relations,
                  lambda: relations.forget_item_id(ITEM, project_dir=PROJECT),
                  False),
}
PLAINTEXT = [n for n, v in LEDGERS.items() if v[2]]


def _plant(path, tail):
    path.write_bytes(path.read_bytes() + tail)


# ---- the Reached contract, per deleter --------------------------------------

@pytest.mark.parametrize("name", sorted(LEDGERS))
def test_a_clean_ledger_is_reached_and_the_ids_compare_as_a_list(
        tmp_checkpoint_dir, name):
    seed, delete, _ = LEDGERS[name]
    seed()
    out = delete()
    assert isinstance(out, Reached) and isinstance(out, list)
    assert out.reached is True
    assert out.state is Health.OK and out.torn == 0
    assert out == list(out) and len(out) == 1


@pytest.mark.parametrize("name", sorted(LEDGERS))
def test_an_absent_ledger_counts_as_reached(tmp_checkpoint_dir, name):
    _, delete, _ = LEDGERS[name]
    out = delete()
    assert out == [] and out.reached is True and out.state is Health.ABSENT


@pytest.mark.parametrize("name", PLAINTEXT)
def test_a_torn_line_in_a_plaintext_ledger_is_not_reached(
        tmp_checkpoint_dir, name):
    seed, delete, _ = LEDGERS[name]
    path = seed()
    _plant(path, b'{"torn": ')
    out = delete()
    assert out.reached is False
    assert (out.state, out.torn) == (Health.DEGRADED, 1)
    assert len(out) == 1, "the rows it could read are still removed"


def test_a_torn_line_in_relations_is_reached_it_holds_no_plaintext(
        tmp_checkpoint_dir):
    seed, delete, _ = LEDGERS["relations"]
    _plant(seed(), b'{"torn": ')
    out = delete()
    assert out.reached is True and out.state is Health.DEGRADED


@pytest.mark.parametrize("name", sorted(LEDGERS))
def test_a_garbage_line_is_not_reached(tmp_checkpoint_dir, name):
    seed, delete, _ = LEDGERS[name]
    _plant(seed(), b"<<<<<<< conflict\n")
    out = delete()
    assert out.reached is False and out.state is Health.UNREADABLE


@pytest.mark.parametrize("name", sorted(LEDGERS))
def test_a_transient_ledger_is_not_reached(tmp_checkpoint_dir, monkeypatch,
                                           name):
    seed, delete, _ = LEDGERS[name]
    path = seed()
    real = jsonl._read_bytes

    def flaky(p):
        if p == path:
            raise OSError(errno.EAGAIN, "busy")
        return real(p)
    monkeypatch.setattr(jsonl, "_read_bytes", flaky)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)
    out = delete()
    assert out.reached is False and out.state is Health.TRANSIENT


@pytest.mark.parametrize("name", sorted(LEDGERS))
def test_a_failed_rewrite_is_not_reached_even_on_a_clean_ledger(
        tmp_checkpoint_dir, monkeypatch, name):
    seed, delete, _ = LEDGERS[name]
    seed()

    def boom(*a, **k):
        raise OSError(errno.EIO, "disk")
    monkeypatch.setattr(jsonl, "rewrite", boom)
    out = delete()
    assert out == [] and out.reached is False


def test_amendments_by_item_id_is_reached_too(tmp_checkpoint_dir):
    _seed_amendments()
    out = amendments.forget_item_id(ITEM, project_dir=PROJECT)
    assert isinstance(out, Reached) and out.reached is True and len(out) == 1


def test_a_deleter_whose_value_is_not_there_is_still_reached(tmp_checkpoint_dir):
    _seed_refutations()
    out = refutations.forget_content_key("0" * 64, project_dir=PROJECT)
    assert out == [] and out.reached is True


# ---- the aggregate ----------------------------------------------------------

def test_scrubbed_names_what_it_did_not_reach(tmp_checkpoint_dir):
    _plant(_seed_refutations(), b'{"torn": ')
    _plant(_seed_relations(), b"<<<<<<< conflict\n")
    done = ledger_repair.scrub_forgotten_key(
        KEY, item_id=ITEM, sibling_ids=(), text=VALUE, project_dir=PROJECT)
    by_name = {u.name: u for u in done.unreached}
    assert set(by_name) == {"refutations.jsonl", "relations.jsonl"}
    assert (by_name["refutations.jsonl"].state,
            by_name["refutations.jsonl"].torn) == (Health.DEGRADED, 1)
    assert by_name["refutations.jsonl"].plaintext is True
    assert by_name["relations.jsonl"].plaintext is False
    assert len(done.refutations) == 1


def test_scrubbed_with_every_ledger_reached_has_nothing_unreached(
        tmp_checkpoint_dir):
    _seed_refutations()
    done = ledger_repair.scrub_forgotten_key(
        KEY, item_id=ITEM, sibling_ids=(), text=VALUE, project_dir=PROJECT)
    assert done.unreached == ()


def test_a_per_sibling_deleter_ands_across_its_calls(tmp_checkpoint_dir):
    _plant(_seed_amendments(), b'{"torn": ')
    done = ledger_repair.scrub_forgotten_key(
        KEY, item_id=ITEM, sibling_ids={"r-111111111111"}, text="",
        project_dir=PROJECT)
    assert [u.name for u in done.unreached] == ["amendments.jsonl"]


def test_a_torn_quarantine_sidecar_is_not_reached(tmp_checkpoint_dir):
    bucket = config.checkpoint_dir() / store.project_slug(PROJECT)
    bucket.mkdir(parents=True, exist_ok=True)
    side = bucket / "trust.quarantined-lines"
    side.write_bytes(b'{"ledger": "trust.jsonl", "text": "kept"}\n'
                     b'{"ledger": "trust.jsonl", "te')
    purged = ledger_repair.forget_quarantined_lines(
        KEY, text=VALUE, project_dir=PROJECT)
    assert [u.name for u in purged.unreached] == ["trust.quarantined-lines"]


# ---- the verb ----------------------------------------------------------------

def _forget(capsys, *extra):
    rc = cli.main(["forget", VALUE, "--project", PROJECT, *extra])
    return rc, capsys.readouterr()


def test_forget_exits_0_when_every_surface_was_reached(
        tmp_checkpoint_dir, capsys):
    _seed_refutations()
    rc, out = _forget(capsys)
    assert rc == 0 and "unreached" not in out.out


def test_forget_exits_4_and_lists_a_torn_plaintext_ledger(
        tmp_checkpoint_dir, capsys):
    _plant(_seed_refutations(), b'{"torn": ')
    rc, out = _forget(capsys)
    assert rc == 4
    text = out.out
    assert ("refutations.jsonl has 1 torn line(s) that forget cannot read; "
            "run: daimon ledger repair refutations, which moves them to "
            "refutations.quarantined-lines and re-scrubs by key; a value "
            "fused into a torn row needs manual review of that sidecar"
            ) in text
    assert VALUE not in text
    # The scrub itself still happened.
    assert VALUE not in refutations._path(PROJECT).read_text()


def test_forget_exits_4_for_an_unreadable_ledger_with_the_state_and_hint(
        tmp_checkpoint_dir, capsys):
    _plant(_seed_amendments(), b"<<<<<<< conflict\n")
    rc, out = _forget(capsys)
    assert rc == 4
    assert ("amendments.jsonl is unreadable (garbage); "
            "run: daimon ledger repair amendments") in out.out


def test_an_unreached_relations_ledger_is_reported_but_never_fails_forget(
        tmp_checkpoint_dir, capsys):
    _seed_refutations()
    _plant(_seed_relations(), b"<<<<<<< conflict\n")
    rc, out = _forget(capsys)
    assert rc == 0
    assert "relations.jsonl" in out.out and "no plaintext" in out.out


def test_forget_refuses_an_unproven_events_ledger_before_it_writes_anything(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed_refutations()
    events = store._events_path(PROJECT)
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_bytes(b"<<<<<<< conflict\n")
    ledger_before = refutations._path(PROJECT).read_bytes()
    rc, out = _forget(capsys)
    assert rc == 2
    assert "events.jsonl is unreadable" in out.err
    assert events.read_bytes() == b"<<<<<<< conflict\n"
    assert refutations._path(PROJECT).read_bytes() == ledger_before


def test_forget_does_not_ask_the_ruling_question_when_events_is_unproven(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # The preflight comes first, so nobody is asked "Remove? [y/N]" about a
    # deletion that cannot be recorded.
    refutations.assert_ruling(
        subject=VALUE, verdict="never do this", scope="all",
        evidence=["issue:1"], channel="cli-tty", ratified=True,
        project_dir=PROJECT)
    events = store._events_path(PROJECT)
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_bytes(b"<<<<<<< conflict\n")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("asked"))
    rc, _ = _forget(capsys)
    assert rc == 2


def test_the_forget_dry_run_writes_nothing_and_needs_no_proven_events(
        tmp_checkpoint_dir, capsys):
    _seed_refutations()
    events = store._events_path(PROJECT)
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_bytes(b"<<<<<<< conflict\n")
    rc, out = _forget(capsys, "--dry-run")
    assert rc == 0 and "would forget" in out.out
