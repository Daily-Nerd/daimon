"""PR 13 (D6): a teammate's published quarantine reaches every verdict.

The foreign pairs are merged into `Snapshot.quarantined` and into every
`Judge`, the slug-less one included, so a value a teammate withheld is
withheld machine-wide. The reader's own `quarantine_ids` stay own-only: a
verdict with no id is a teammate's. The published file is planted by hand
here; the writer-to-reader round trip lives in test_teamsync and the
sentinel world. The planted rows bypass the publisher's strict shape on purpose
(short event ids, shared ids): they pin what the READER does with a row."""

import json

import pytest

from daimon_briefing import config, schema, store, trust, view
from daimon_briefing.surfaces import Writer

DECISION = next(f for f in schema.ITEM_FIELDS if f.kind == "decision")
BELIEF = next(f for f in schema.ITEM_FIELDS if f.kind == "belief")
TEXT = "the signing seed lives in the old vault under the stairs"
KEY = trust.value_key(TEXT)
PROJECT = "/p/foreign-q"


@pytest.fixture(autouse=True)
def _me(monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")


def _publish(author="grace", *, key=KEY, kind="decision", state="active",
             tid="tr-0123456789ab", order=1, event_id="e1"):
    adir = config.team_dir() / "team-a" / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    row = {"version": 1, "ts": "2026-10-09T12:00:00Z", "order": order,
           "event_id": event_id, "quarantine_id": tid, "kind": kind,
           "value_key": key, "state": state, "author": author}
    with open(adir / "quarantines.jsonl", "ab") as handle:
        handle.write(json.dumps(row).encode() + b"\n")


def _write(project=PROJECT, text=TEXT):
    cp = {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": text, "id": "d-aaaaaa"}]},
          "epistemic_snapshot": {}}
    store.write_checkpoint("S-1", cp, project_dir=project, writer=Writer.HUMAN)
    return store.project_slug(project)


def _own_quarantine(project=PROJECT, text=TEXT):
    return trust.propose(text=text, kind="decision", reason="mine",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


ITEM = {"text": TEXT, "id": "d-aaaaaa"}


def test_the_snapshot_carries_foreign_pairs_and_no_foreign_ids(
        tmp_checkpoint_dir):
    slug = _write()
    _publish()
    snap = view.snapshot(slug)
    assert ("decision", KEY) in snap.quarantined
    assert snap.quarantine_ids == {}
    assert snap.quarantined_keys == frozenset({KEY})


def test_classify_withholds_a_foreign_pair_with_no_id(tmp_checkpoint_dir):
    slug = _write()
    _publish()
    verdict = view.classify(DECISION, ITEM, view.snapshot(slug))
    assert isinstance(verdict, view.Withheld)
    assert verdict.reason == "quarantine" and verdict.quarantine_id is None


def test_classification_is_kind_scoped(tmp_checkpoint_dir):
    slug = _write()
    _publish(kind="belief")
    snap = view.snapshot(slug)
    assert isinstance(view.classify(DECISION, ITEM, snap), view.Visible)
    assert isinstance(view.classify(BELIEF, ITEM, snap), view.Withheld)


def test_the_own_id_wins_when_both_hold_the_pair(tmp_checkpoint_dir):
    slug = _write()
    tid = _own_quarantine()
    _publish()
    verdict = view.classify(DECISION, ITEM, view.snapshot(slug))
    assert verdict.quarantine_id == tid


def test_a_pulled_release_lifts_it(tmp_checkpoint_dir):
    slug = _write()
    _publish()
    assert isinstance(view.classify(DECISION, ITEM, view.snapshot(slug)),
                      view.Withheld)
    _publish(state="released", order=2, event_id="e2")
    assert isinstance(view.classify(DECISION, ITEM, view.snapshot(slug)),
                      view.Visible)


def test_the_judge_of_a_bucket_withholds_it(tmp_checkpoint_dir):
    slug = _write()
    _publish()
    judge = view.judge(slug)
    assert isinstance(judge.verdict(DECISION, ITEM), view.Withheld)
    assert judge.empty is False


def test_a_bucket_without_a_trust_ledger_still_withholds_it(
        tmp_checkpoint_dir):
    slug = _write()
    assert not (config.checkpoint_dir() / slug / "trust.jsonl").exists()
    _publish()
    assert ("decision", KEY) in view.judge(slug).snap.quarantined


@pytest.mark.parametrize("slug", [None, "", "no-such-bucket", "../x"])
def test_the_slugless_judge_withholds_it(tmp_checkpoint_dir, slug):
    _publish()
    judge = view.judge(slug)
    assert ("decision", KEY) in judge.snap.quarantined
    assert isinstance(judge.verdict(DECISION, ITEM), view.Withheld)
    assert judge.empty is False


def test_the_judge_is_empty_without_a_foreign_pair(tmp_checkpoint_dir):
    slug = _write()
    assert view.judge(slug).empty is True
    assert view.judge(None).empty is True


def test_prose_masks_a_whole_string_foreign_value(tmp_checkpoint_dir):
    slug = _write()
    _publish()
    snap = view.snapshot(slug)
    got = view.prose_verdict(TEXT, snap)
    assert got is not None and got.reason == "quarantine"
    assert got.quarantine_id is None
    assert view.prose_verdict("some other prose entirely", snap) is None


def test_prose_masking_is_any_kind(tmp_checkpoint_dir):
    slug = _write()
    _publish(kind="belief")
    assert view.prose_verdict(TEXT, view.snapshot(slug)) is not None


def test_prose_supplies_the_own_id_when_it_holds_the_pair(tmp_checkpoint_dir):
    slug = _write()
    tid = _own_quarantine()
    _publish()
    assert view.prose_verdict(TEXT, view.snapshot(slug)).quarantine_id == tid


def test_prose_tests_membership_on_a_cached_key_set(tmp_checkpoint_dir):
    slug = _write()
    _publish()
    snap = view.snapshot(slug)
    assert snap.quarantined_keys is snap.quarantined_keys
    assert view.prose_verdict("some other prose entirely", snap) is None


def test_machine_sets_reads_the_author_once(tmp_checkpoint_dir, monkeypatch):
    _publish()
    calls = []
    real = config.author
    monkeypatch.setattr(config, "author",
                        lambda: calls.append(1) or real())
    forgotten, pairs = view.machine_sets()
    assert ("decision", KEY) in pairs and isinstance(forgotten, frozenset)
    assert len(calls) == 1
    view.machine_sets()
    view.forgotten_keys()
    assert len(calls) == 1


def test_forgotten_keys_is_the_first_of_machine_sets(tmp_checkpoint_dir):
    _publish()
    assert view.forgotten_keys() == view.machine_sets()[0]


def test_a_raise_in_the_accessor_closes_the_judge_and_is_not_kept(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write()
    real = view.machine_sets

    def boom():
        raise RuntimeError("walk failed")

    monkeypatch.setattr(view, "machine_sets", boom)
    assert view.judge(slug).closed is True
    assert view.judge(None).closed is True
    monkeypatch.setattr(view, "machine_sets", real)
    assert view.judge(slug).closed is False
    assert view.judge(None).closed is False


def test_a_raise_with_the_forgotten_set_in_hand_closes_the_judge_too(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write()

    def boom():
        raise RuntimeError("walk failed")

    monkeypatch.setattr(view, "foreign_pairs", boom)
    assert view.judge(slug, forgotten=frozenset()).closed is True


def test_a_raise_in_the_teammates_sets_closes_the_snapshot(
        tmp_checkpoint_dir, monkeypatch):
    """O3: a reader that cannot prove what teammates withheld does not open.
    The forgotten values and the quarantined ones stay out of every read."""
    slug = _write()
    _publish()

    def boom():
        raise RuntimeError("walk failed")

    monkeypatch.setattr(view, "machine_sets", boom)
    snap = view.snapshot(slug)
    assert snap.closed is True
    assert snap.health["events.jsonl"].value == "unreadable"
    item = {"text": TEXT, "id": "d-aaaaaa"}
    got = view.classify(DECISION, item, snap)
    assert isinstance(got, view.Withheld) and got.reason == "closed"


def test_brief_prints_the_closed_refusal_not_the_item_when_the_sets_raise(
        tmp_checkpoint_dir, monkeypatch, capsys):
    from daimon_briefing import cli
    _write()
    capsys.readouterr()
    assert cli.main(["brief", "--project", PROJECT]) == 0
    assert TEXT in capsys.readouterr().out          # open before

    def boom():
        raise RuntimeError("walk failed")

    monkeypatch.setattr(view, "machine_sets", boom)
    assert cli.main(["brief", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert TEXT not in out and "events.jsonl is unreadable" in out


def test_a_failure_of_the_local_forget_fold_alone_keeps_the_old_posture(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write()

    def boom(*_a, **_k):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(store, "fold_resolutions", boom)
    snap = view.snapshot(slug)
    assert snap.health["events.jsonl"].value == "unreadable"
    assert snap.closed is False


def test_a_failed_local_fold_keeps_the_teammates_pairs_and_forgets(
        tmp_checkpoint_dir, monkeypatch):
    """#103 keeps the snapshot OPEN when the local forget fold raises; the
    healthy teammates' half must still withhold in that degraded snapshot."""
    from daimon_briefing import normalize
    slug = _write()
    _publish()
    tomb = config.team_dir() / "team-a" / "authors" / "grace"
    forgotten = "a value grace forgot, not the quarantined one"
    (tomb / "tombstones.jsonl").write_text(
        json.dumps({"key": normalize.content_key(forgotten)}) + "\n")

    def boom(*_a, **_k):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(store, "fold_resolutions", boom)
    snap = view.snapshot(slug)
    assert snap.closed is False                       # the #103 posture
    assert snap.health["events.jsonl"].value == "unreadable"
    assert ("decision", KEY) in snap.quarantined
    assert normalize.content_key(forgotten) in snap.forgotten
    item = {"text": TEXT, "id": "d-aaaaaa"}
    assert isinstance(view.classify(DECISION, item, snap), view.Withheld)


def test_a_failed_local_fold_and_a_failed_teammate_walk_close_the_snapshot(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write()

    def boom(*_a, **_k):
        raise RuntimeError("fold failed")

    monkeypatch.setattr(store, "fold_resolutions", boom)
    monkeypatch.setattr(store, "foreign_team", boom)
    snap = view.snapshot(slug)
    assert snap.closed is True
