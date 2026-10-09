"""PR 13 (D6): reading the quarantines teammates published.

One walk reads both team ledgers per foreign author directory: the forget
keys, the active quarantine pairs, and one health verdict per author. The
fixtures plant the files by hand on purpose: this is the READER's view of what
a sidecar clone holds, and the writer has its own tests."""

import json

import pytest

from daimon_briefing import config, jsonl, store
from daimon_briefing.jsonl import Health

KEY = "0123456789abcdef"
OTHER = "fedcba9876543210"
TID = "tr-0123456789ab"
TID2 = "tr-ba9876543210"
TOMB_KEY = "ab" * 8


@pytest.fixture(autouse=True)
def _me(monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")


def _row(state="active", *, tid=TID, key=KEY, kind="decision", order=1,
         event_id="e1", ts="2026-10-09T12:00:00Z", author="grace"):
    return {"version": 1, "ts": ts, "order": order, "event_id": event_id,
            "quarantine_id": tid, "kind": kind, "value_key": key,
            "state": state, "author": author}


def _lines(*rows):
    return b"".join(json.dumps(r).encode() + b"\n" for r in rows)


def _put(author, *, q=None, t=None, remote="team-a", nested=False, raw=False):
    base = config.team_dir() / remote
    if nested:
        base = base / "projects" / "squad" / "census"
    adir = base / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    if q is not None:
        (adir / "quarantines.jsonl").write_bytes(q if raw else _lines(*q))
    if t is not None:
        (adir / "tombstones.jsonl").write_bytes(
            t if raw else _lines(*({"ts": "x", "key": k} for k in t)))
    return adir


def test_a_published_active_row_is_a_foreign_pair(tmp_checkpoint_dir):
    _put("grace", q=[_row()])
    assert store.foreign_quarantines() == {("decision", KEY)}
    team = store.foreign_team()
    assert team.quarantines == {("decision", KEY)}
    assert team.unproven == frozenset()


def test_own_author_directory_is_excluded(tmp_checkpoint_dir):
    _put("ada", q=[_row(author="ada")])
    assert store.foreign_quarantines() == frozenset()


def test_the_local_remote_is_excluded(tmp_checkpoint_dir):
    _put("grace", q=[_row()], remote="local")
    assert store.foreign_quarantines() == frozenset()


def test_union_across_two_authors(tmp_checkpoint_dir):
    _put("grace", q=[_row()])
    _put("kay", q=[_row(key=OTHER, tid=TID2)])
    assert store.foreign_quarantines() == {("decision", KEY),
                                           ("decision", OTHER)}


def test_one_authors_release_does_not_lift_anothers_claim(tmp_checkpoint_dir):
    _put("grace", q=[_row()])
    _put("kay", q=[_row(order=1), _row("released", order=2, event_id="e2")])
    assert store.foreign_quarantines() == {("decision", KEY)}


def test_a_release_in_the_same_file_lifts_it(tmp_checkpoint_dir):
    _put("grace", q=[_row(), _row("released", order=2, event_id="e2")])
    assert store.foreign_quarantines() == frozenset()


def test_the_same_author_in_two_clones_unions(tmp_checkpoint_dir):
    _put("grace", q=[_row(), _row("released", order=2, event_id="e2")],
         remote="team-a")
    _put("grace", q=[_row()], remote="team-b", nested=True)
    assert store.foreign_quarantines() == {("decision", KEY)}


def test_the_nested_layout_is_read(tmp_checkpoint_dir):
    _put("grace", q=[_row()], nested=True)
    assert store.foreign_quarantines() == {("decision", KEY)}


def test_a_ledger_planted_in_dot_git_is_never_read(tmp_checkpoint_dir):
    planted = (config.team_dir() / "team-a" / ".git" / "m" / "authors"
               / "mallory")
    planted.mkdir(parents=True)
    (planted / "quarantines.jsonl").write_bytes(_lines(_row()))
    assert store.foreign_quarantines() == frozenset()


def test_claims_carry_author_kind_and_ts_and_nothing_else(tmp_checkpoint_dir):
    _put("grace", q=[_row(ts="2026-10-09T12:00:00Z")])
    assert store.foreign_team().claims == (
        ("grace", "decision", "2026-10-09T12:00:00Z"),)


# ---- one health verdict per author, over both ledgers ------------------------


def test_a_garbage_quarantine_file_makes_the_author_unproven_for_both(
        tmp_checkpoint_dir):
    adir = _put("grace", t=[TOMB_KEY])
    (adir / "quarantines.jsonl").write_bytes(
        _lines(_row()) + b"<<<<<<< HEAD\n")
    team = store.foreign_team()
    assert team.unproven == {"grace"}
    # Good lines of an unproven file still contribute: the set only grows.
    assert team.quarantines == {("decision", KEY)}
    assert TOMB_KEY in team.keys


def test_a_garbage_tombstone_file_makes_the_author_unproven_too(
        tmp_checkpoint_dir):
    adir = _put("grace", q=[_row()])
    (adir / "tombstones.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    assert store.foreign_team().unproven == {"grace"}
    assert store.foreign_tombstones().unproven == {"grace"}


def test_a_torn_tail_is_unproven_not_degraded_h7(tmp_checkpoint_dir):
    _put("grace", q=_lines(_row()) + b'{"ts": "x", "kind": "dec', raw=True)
    team = store.foreign_team()
    assert team.unproven == {"grace"}
    # The torn row is the newest claim; the rows before it still count.
    assert team.quarantines == {("decision", KEY)}


def test_a_torn_tombstone_tail_is_unproven_too(tmp_checkpoint_dir):
    _put("grace", t=_lines({"key": TOMB_KEY}) + b'{"key": "tor', raw=True)
    assert store.foreign_tombstones().unproven == {"grace"}


def test_an_over_cap_file_is_unproven_only(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 400)
    rows = [_row(key=f"{i:016x}", tid=f"tr-{i:012x}", event_id=f"e{i}")
            for i in range(10)]
    _put("grace", q=rows)
    team = store.foreign_team()
    assert team.unproven == {"grace"}
    assert 0 < len(team.quarantines) < 10


def test_a_transient_file_is_unproven(tmp_checkpoint_dir, monkeypatch):
    adir = _put("grace", q=[_row()])
    path = adir / "quarantines.jsonl"
    real = jsonl.read

    def fake(p, *a, **k):
        if p == path:
            return jsonl.Read(Health.TRANSIENT, [], detail="EBUSY")
        return real(p, *a, **k)

    monkeypatch.setattr(jsonl, "read", fake)
    assert store.foreign_team().unproven == {"grace"}


def test_a_directory_in_place_of_the_file_is_unproven_never_fatal(
        tmp_checkpoint_dir):
    adir = _put("grace", t=[TOMB_KEY])
    (adir / "quarantines.jsonl").mkdir()
    assert store.foreign_team().unproven == {"grace"}
    assert TOMB_KEY in store.foreign_forgotten_content_keys()


def test_an_absent_file_is_open(tmp_checkpoint_dir):
    _put("grace", t=[TOMB_KEY])
    team = store.foreign_team()
    assert team.unproven == frozenset()


def test_one_unreadable_author_never_hides_the_others(tmp_checkpoint_dir):
    adir = _put("grace", q=[_row()])
    (adir / "quarantines.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    _put("kay", q=[_row(key=OTHER, tid=TID2)])
    team = store.foreign_team()
    assert team.unproven == {"grace"}
    assert ("decision", OTHER) in team.quarantines


# ---- the old names are views --------------------------------------------------


def test_foreign_tombstones_keeps_its_shape(tmp_checkpoint_dir):
    _put("grace", t=[TOMB_KEY], q=[_row()])
    got = store.foreign_tombstones()
    assert type(got).__name__ == "ForeignTombstones"
    assert got.keys == {TOMB_KEY} and isinstance(got.keys, set)
    assert got.unproven == frozenset() and got._fields == ("keys", "unproven")
    assert store.foreign_forgotten_content_keys() == {TOMB_KEY}
    got.keys.add("mutated")
    assert store.foreign_forgotten_content_keys() == {TOMB_KEY}


# ---- the memo ------------------------------------------------------------------


def test_the_author_is_resolved_once_per_real_walk_never_on_a_hit(
        tmp_checkpoint_dir, monkeypatch):
    _put("grace", q=[_row()], t=[TOMB_KEY])
    calls = []
    real = config.author

    def counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(config, "author", counting)
    first = store.foreign_team()
    assert len(calls) == 1
    again = store.foreign_team()
    store.foreign_quarantines()
    store.foreign_forgotten_content_keys()
    assert len(calls) == 1 and again == first


def test_a_changed_file_drops_the_memo(tmp_checkpoint_dir):
    adir = _put("grace", q=[_row()])
    assert store.foreign_quarantines() == {("decision", KEY)}
    (adir / "quarantines.jsonl").write_bytes(
        _lines(_row(), _row("released", order=2, event_id="e2")))
    assert store.foreign_quarantines() == frozenset()
    _put("kay", q=[_row(key=OTHER, tid=TID2)])
    assert store.foreign_quarantines() == {("decision", OTHER)}


def test_a_new_author_identity_drops_the_memo(tmp_checkpoint_dir,
                                              monkeypatch):
    _put("grace", q=[_row()])
    _put("ada", q=[_row(key=OTHER, tid=TID2, author="ada")])
    assert store.foreign_quarantines() == {("decision", KEY)}
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    assert store.foreign_quarantines() == {("decision", OTHER)}


def test_a_raised_cap_drops_the_memo(tmp_checkpoint_dir, monkeypatch):
    rows = [_row(key=f"{i:016x}", tid=f"tr-{i:012x}", event_id=f"e{i}")
            for i in range(10)]
    _put("grace", q=rows)
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 400)
    assert store.foreign_team().unproven == {"grace"}
    monkeypatch.undo()
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    assert store.foreign_team().unproven == frozenset()


def test_a_transient_walk_is_never_memoised(tmp_checkpoint_dir, monkeypatch):
    adir = _put("grace", q=[_row()])
    path = adir / "quarantines.jsonl"
    real = jsonl.read
    state = {"fail": True}

    def flaky(p, *a, **k):
        if p == path and state["fail"]:
            return jsonl.Read(Health.TRANSIENT, [], detail="EBUSY")
        return real(p, *a, **k)

    monkeypatch.setattr(jsonl, "read", flaky)
    assert store.foreign_team().unproven == {"grace"}
    state["fail"] = False
    assert store.foreign_team().unproven == frozenset()
    assert store.foreign_quarantines() == {("decision", KEY)}


def test_no_team_dir_is_empty_and_cheap(tmp_checkpoint_dir, monkeypatch):
    def boom():
        raise AssertionError("config.author() forks git config")

    monkeypatch.setattr(config, "author", boom)
    assert not config.team_dir().exists()
    team = store.foreign_team()
    assert team.keys == frozenset() and team.quarantines == frozenset()
    assert team.claims == () and team.unproven == frozenset()


def test_a_dangling_link_in_place_of_the_file_reads_as_absent_never_fatal(
        tmp_checkpoint_dir):
    adir = _put("grace", t=[TOMB_KEY])
    (adir / "quarantines.jsonl").symlink_to(adir / "nowhere")
    team = store.foreign_team()
    assert team.unproven == frozenset() and team.quarantines == frozenset()
    assert TOMB_KEY in team.keys


# ---- fix round 1: nothing a teammate writes may raise, print raw or open ----


def test_a_file_with_an_infinite_order_cannot_hide_anyone_elses_claims(
        tmp_checkpoint_dir):
    _put("grace", t=[TOMB_KEY], q=[_row()])
    adir = _put("mallory")
    with open(adir / "quarantines.jsonl", "ab") as handle:   # byte writer
        handle.write(b'{"kind":"decision","value_key":"%s","state":"active",'
                     b'"order":Infinity}\n' % OTHER.encode())
        handle.write(b'{"kind":"decision","value_key":"%s","state":"active",'
                     b'"order":NaN}\n' % KEY.encode())
        handle.write(b'{"kind":"decision","value_key":"abcdef0123456789",'
                     b'"state":"active","order":1e999}\n')
    team = store.foreign_team()
    assert TOMB_KEY in team.keys and ("decision", KEY) in team.quarantines
    assert ("decision", OTHER) in team.quarantines
    assert team.unproven == frozenset()


def test_an_author_whose_fold_raises_is_unproven_and_the_rest_still_fold(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import policy
    _put("grace", t=[TOMB_KEY], q=[_row()])
    _put("kay", q=[_row(key=OTHER, tid=TID2, author="kay")])
    real = policy.fold_published_quarantines

    def boom(rows):
        if any(isinstance(r, dict) and r.get("author") == "kay" for r in rows):
            raise RuntimeError("fold exploded")
        return real(rows)

    monkeypatch.setattr(policy, "fold_published_quarantines", boom)
    team = store.foreign_team()           # nothing propagates
    assert team.unproven == {"kay"}
    assert ("decision", KEY) in team.quarantines
    assert ("decision", OTHER) not in team.quarantines
    assert TOMB_KEY in team.keys


def test_a_raising_author_is_not_kept_by_the_memo(tmp_checkpoint_dir,
                                                  monkeypatch):
    from daimon_briefing import policy
    _put("kay", q=[_row(key=OTHER, tid=TID2, author="kay")])
    real = policy.fold_published_quarantines
    state = {"boom": True}

    def flaky(rows):
        if state["boom"]:
            raise RuntimeError("once")
        return real(rows)

    monkeypatch.setattr(policy, "fold_published_quarantines", flaky)
    assert store.foreign_team().unproven == {"kay"}
    state["boom"] = False
    assert store.foreign_team().unproven == frozenset()


def test_an_off_shape_ts_reads_as_empty_in_the_claims(tmp_checkpoint_dir):
    adir = _put("grace")
    row = dict(_row(), ts="\x1b]0;PWNED\x07")
    with open(adir / "quarantines.jsonl", "ab") as handle:
        handle.write(json.dumps(row).encode() + b"\n")
    team = store.foreign_team()
    # the off-shape row is ignored by the fold, so it is not a claim at all
    assert team.quarantines == frozenset() and team.claims == ()
    assert all("\x1b" not in repr(c) for c in team.claims)


def test_a_directory_name_that_is_not_a_plain_name_is_unproven_not_read(
        tmp_checkpoint_dir):
    bad = "gr\x1b[2Jace"
    adir = _put(bad, q=[_row()])
    assert adir.exists()
    team = store.foreign_team()
    assert team.quarantines == frozenset() and team.claims == ()
    assert team.unproven == {bad}


def test_the_over_cap_warning_is_logged_once_per_stat_change(
        tmp_checkpoint_dir, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 400)
    store._OVERCAP_WARNED.clear()
    rows = [_row(key=f"{i:016x}", tid=f"tr-{i:012x}", event_id=f"e{i}")
            for i in range(10)]
    adir = _put("grace", q=rows)
    # a directory in place of a ledger never memoises: the walk repeats
    (adir / "tombstones.jsonl").mkdir()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            store.foreign_team()
    assert sum("exceeds" in r.message for r in caplog.records) == 1


# ---- round 2: a row version this reader does not know ------------------------


def test_an_unknown_row_version_marks_that_author_unproven_only(
        tmp_checkpoint_dir):
    _put("grace", q=[_row()])
    _put("kay", q=[_row(key=OTHER, tid=TID2, author="kay"),
                   dict(_row(key=KEY, tid=TID, author="kay"), version=2)])
    team = store.foreign_team()
    assert team.unproven == {"kay"}
    # the v2 row is not folded; grace is untouched; kay's v1 row still counts
    assert ("decision", KEY) in team.quarantines          # from grace
    assert ("decision", OTHER) in team.quarantines
    assert store.foreign_tombstones().unproven == {"kay"}


def test_version_one_and_a_text_version_behave_as_before(tmp_checkpoint_dir):
    _put("grace", q=[_row()])
    _put("kay", q=[dict(_row(key=OTHER, tid=TID2, author="kay"),
                        version="x")])
    team = store.foreign_team()
    assert team.unproven == frozenset()
    assert team.quarantines == {("decision", KEY)}   # kay's "x" row ignored


def test_an_unknown_version_note_is_the_author_skipped_one(
        tmp_checkpoint_dir):
    from daimon_briefing import display, view
    _put("kay", q=[dict(_row(author="kay"), version=2)])
    assert view.team_notes() == (display.author_skipped_note(),)


def test_a_directory_in_place_of_a_ledger_is_unreadable_never_over_cap(
        tmp_checkpoint_dir, monkeypatch, caplog):
    """A directory's st_size is 4096 on ext4 and tiny on APFS; with the cap at
    0 any size would trip an over-cap branch, so this is red on both."""
    import logging
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 0)
    store._OVERCAP_WARNED.clear()
    adir = _put("grace")
    (adir / "quarantines.jsonl").mkdir()
    with caplog.at_level(logging.WARNING):
        got = store._capped_rows(adir / "quarantines.jsonl")
    assert got.health is Health.UNREADABLE and got.over_cap is False
    assert got.rows == []
    assert not [r for r in caplog.records if "exceeds" in r.message]
    assert store.foreign_team().unproven == {"grace"}
