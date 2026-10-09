"""`view.masked`: ledger prose judged once, at the presentation boundary
(#1132 PR 11c, D11.8).

A forgotten whole value is blank (a list member dropped), a quarantined one is
the withheld marker, a closed trust ledger masks the value copies only. Rows
are copied, never mutated, and `judge` is asked once per bucket per call.
"""

import copy
import dataclasses

import pytest

from daimon_briefing import normalize, store, view

PROJECT = "/p/masked/own"
SLUG = store.project_slug(PROJECT)
OTHER = "other-bucket"

FORGOTTEN = "the forgotten value of this test"
QUARANTINED = "the quarantined value of this test"
QID = "tr-aaaaaaaaaaaa"


def snap(*, forgotten=(), quarantined=(), closed=False):
    keys = {normalize.content_key(t): t for t in quarantined}
    return dataclasses.replace(
        view.Snapshot.empty(),
        forgotten=frozenset(normalize.content_key(t) for t in forgotten),
        quarantined=frozenset(("decision", k) for k in keys),
        quarantine_ids={("decision", k): QID for k in keys},
        closed=closed)


def masked(ledger, rows, **kw):
    return view.masked(PROJECT, ledger, rows, **kw)


def test_a_forgotten_scalar_is_blank_and_other_fields_stay():
    out = masked("requests.jsonl", [{"ask": FORGOTTEN, "why": "kept",
                                      "state": "open"}],
                 snap=snap(forgotten=[FORGOTTEN]))
    assert out == [{"ask": "", "why": "kept", "state": "open"}]


def test_a_forgotten_list_member_is_dropped():
    row = {"evidence": ["issue:1", FORGOTTEN, "issue:2"]}
    out = masked("refutations.jsonl", [row], snap=snap(forgotten=[FORGOTTEN]))
    assert out == [{"evidence": ["issue:1", "issue:2"]}]


def test_a_quarantined_value_is_the_marker_with_its_record_id():
    out = masked("requests.jsonl", [{"ask": QUARANTINED}],
                 snap=snap(quarantined=[QUARANTINED]))
    assert out == [{"ask": f"[withheld: quarantine {QID}]"}]


def test_folded_nested_and_list_of_dict_paths_are_judged():
    row = {"replies": [{"note": QUARANTINED, "ts": 1}, {"note": "fine"}],
           "done_evidence": FORGOTTEN, "state": "done"}
    out = masked("requests.jsonl", [row],
                 snap=snap(forgotten=[FORGOTTEN], quarantined=[QUARANTINED]))
    assert out[0]["replies"] == [
        {"note": f"[withheld: quarantine {QID}]", "ts": 1}, {"note": "fine"}]
    assert out[0]["done_evidence"] == ""
    proposal = {"revision_proposed": {"check": {"match": QUARANTINED,
                                                 "body": "keep"}}}
    got = masked("refutations.jsonl", [proposal],
                 snap=snap(quarantined=[QUARANTINED]))
    assert got[0]["revision_proposed"]["check"] == {
        "match": f"[withheld: quarantine {QID}]", "body": "keep"}


def test_a_closed_ledger_masks_value_copies_and_keeps_human_prose():
    closed = snap(closed=True)
    keeps = masked("requests.jsonl", [{"ask": "a standing ask"}], snap=closed)
    assert keeps == [{"ask": "a standing ask"}]
    marks = masked("trust.jsonl", [{"reason": "r", "evidence": ["issue:1"]}],
                   snap=closed)
    closed_marker = "[withheld: trust ledger unreadable]"
    assert marks == [{"reason": closed_marker, "evidence": [closed_marker]}]
    assert masked("amendments.jsonl", [{"evidence": "e", "note": "n"}],
                  snap=closed) == [{"evidence": closed_marker, "note": "n"}]
    assert masked("events.jsonl", [{"item_text": "t", "note": "n",
                                    "status": "reopen"}], snap=closed) == [
        {"item_text": closed_marker, "note": "n", "status": "reopen"}]


def test_rows_are_copied_never_mutated_and_a_clean_row_is_equal():
    rows = [{"ask": QUARANTINED, "replies": [{"note": FORGOTTEN}]},
            {"ask": "clean"}]
    before = copy.deepcopy(rows)
    out = masked("requests.jsonl", rows,
                 snap=snap(forgotten=[FORGOTTEN], quarantined=[QUARANTINED]))
    assert rows == before
    assert out[1] == rows[1] and out[1] is not rows[1]
    out[1]["ask"] = "changed"
    assert rows == before


def test_an_empty_input_is_an_empty_list():
    assert masked("requests.jsonl", [], snap=snap()) == []


def test_an_undeclared_ledger_name_is_refused():
    with pytest.raises(LookupError):
        masked("nonesuch.jsonl", [{}], snap=snap())


def test_a_deleter_marker_is_never_printed(monkeypatch):
    marker = store._FORGOTTEN_FIELD_MARKER.format("ab12cd34ef56ab12")
    # trust keeps its human wording, the key never travels
    got = masked("trust.jsonl", [{"reason": marker,
                                  "evidence": ["issue:1", marker]}],
                 snap=snap())
    assert got == [{"reason": "(value forgotten)",
                    "evidence": ["issue:1", "(value forgotten)"]}]
    # every other ledger blanks it, as view._judged does
    assert masked("events.jsonl", [{"note": marker, "item_text": marker}],
                  snap=snap()) == [{"note": "", "item_text": ""}]
    # an event status keeps its class token without the key
    out = masked("events.jsonl", [{"status": "resolved " + marker}],
                 snap=snap())
    assert out == [{"status": "resolved"}]
    assert "ab12cd34ef56ab12" not in str(out)


def test_an_undeclared_string_leaf_is_judged_by_value():
    row = {"ask": "x", "surprise": {"deep": [FORGOTTEN, QUARANTINED, "ok"]},
           "other": FORGOTTEN}
    out = masked("requests.jsonl", [row],
                 snap=snap(forgotten=[FORGOTTEN], quarantined=[QUARANTINED]))
    assert out[0]["other"] == ""
    assert out[0]["surprise"]["deep"] == [
        f"[withheld: quarantine {QID}]", "ok"]
    # closed never masks an undeclared leaf
    kept = masked("requests.jsonl", [{"ask": "x", "surprise": "y"}],
                  snap=snap(closed=True))
    assert kept == [{"ask": "x", "surprise": "y"}]


def test_judge_is_asked_once_per_call_with_a_bare_slug(monkeypatch):
    seen = []
    real = view.judge

    def spy(slug, **kw):
        seen.append(slug)
        return real(slug, **kw)

    monkeypatch.setattr(view, "judge", spy)
    rows = [{"ask": "one"}, {"ask": "two"}, {"ask": "three"}]
    view.masked(PROJECT, "requests.jsonl", rows)
    assert seen == [SLUG]            # a slug, never the path (fail-open trap)
    assert "/" not in seen[0]


def test_a_requests_row_is_judged_by_every_bucket_it_names(monkeypatch):
    calls = []
    quarantine_in_other = snap(quarantined=[QUARANTINED])

    def fake(slug, **_kw):
        calls.append(slug)
        return view.Judge(quarantine_in_other if slug == OTHER
                          else snap())

    monkeypatch.setattr(view, "judge", fake)
    rows = [{"ask": QUARANTINED, "from_slug": OTHER, "to": SLUG},
            {"ask": QUARANTINED, "from_slug": OTHER, "to": SLUG},
            {"ask": "fine", "from_slug": "", "to": SLUG}]
    out = view.masked(PROJECT, "requests.jsonl", rows)
    assert out[0]["ask"] == out[1]["ask"] == f"[withheld: quarantine {QID}]"
    assert out[2]["ask"] == "fine"
    assert sorted(calls) == sorted([SLUG, OTHER])    # memoized per slug


def test_a_requests_row_naming_a_path_is_not_judged_against_the_cwd(
        monkeypatch):
    calls = []

    def fake(slug, **_kw):
        calls.append(slug)
        return view.Judge(snap())

    monkeypatch.setattr(view, "judge", fake)
    view.masked(PROJECT, "requests.jsonl",
                [{"ask": "x", "from_slug": "/etc/passwd", "to": ".."}])
    assert calls == [SLUG]


def test_other_ledgers_ignore_slug_fields(monkeypatch):
    calls = []

    def fake(slug, **_kw):
        calls.append(slug)
        return view.Judge(snap())

    monkeypatch.setattr(view, "judge", fake)
    view.masked(PROJECT, "refutations.jsonl", [{"subject": "s", "to": OTHER}])
    assert calls == [SLUG]


def test_a_foreign_bucket_is_masked_by_the_snapshot_it_was_read_from():
    foreign = snap(quarantined=[QUARANTINED])
    out = view.masked(PROJECT, "refutations.jsonl",
                      [{"verdict": QUARANTINED}], snap=foreign)
    assert out == [{"verdict": f"[withheld: quarantine {QID}]"}]
    own = view.masked(PROJECT, "refutations.jsonl", [{"verdict": QUARANTINED}],
                      snap=snap())
    assert own == [{"verdict": QUARANTINED}]


def test_withheld_in_names_what_is_withheld_without_the_text():
    both = snap(forgotten=[FORGOTTEN], quarantined=[QUARANTINED])
    row = {"verdict": QUARANTINED, "evidence": [FORGOTTEN, "issue:1"],
           "revision_proposed": {"verdict": "fine"}}
    got = view.withheld_in(PROJECT, "refutations.jsonl", row, snap=both)
    assert sorted(w.reason for w in got) == ["forgotten", "quarantine"]
    assert next(w for w in got if w.reason == "quarantine"
                ).quarantine_id == QID
    assert view.withheld_in(PROJECT, "refutations.jsonl", {"verdict": "ok"},
                            snap=both) == ()
    assert [w.reason for w in view.withheld_in(
        PROJECT, "trust.jsonl", {"reason": "r"},
        snap=snap(closed=True))] == ["closed"]


def test_an_undeclared_leaf_holding_a_deleter_marker_is_dropped():
    marker = store._FORGOTTEN_FIELD_MARKER.format("0123456789abcdef")
    out = masked("requests.jsonl", [{"ask": "x", "extra": marker,
                                     "many": [marker, "kept"]}], snap=snap())
    assert out == [{"ask": "x", "extra": "", "many": ["kept"]}]


def test_an_event_status_that_was_only_a_marker_reads_as_nothing():
    marker = store._FORGOTTEN_FIELD_MARKER.format("0123456789abcdef")
    assert masked("events.jsonl", [{"status": marker}], snap=snap()) == [
        {"status": ""}]
