"""#268 slice 3: corroboration EVENTS — emission, reader, fold.

Slice 2 gave `carry.merge` a pure predicate that observes independent
agreement and appends `(item_id, origin_session, origin_author)` triples to a
sink. This slice is what turns those observations into durable, auditable
rows, and what reads them back.

The whole design is shaped by one rule: a corroboration must be able to RAISE
trust without ever gaining the power to lower it. Two mechanisms enforce that,
and most tests here exist to pin them.

1. NAMESPACED refs. Rows land on `corroboration:<item_id>`, never on the bare
   item id. `store.resolutions` folds on `item_ref` alone and `is_resolved`
   resolves any status outside reopen/supersede-candidate (scar 0025), so a
   row on the bare ref would HIDE the item it describes — from the briefing,
   from carry, from recall — and would displace a human's superseded-by
   verdict as the latest event (the #376 trap). The namespace makes both
   impossible by construction; `is_resolved`'s `corroborat` prefix is the
   belt to that pair of braces.

2. NO item_text, ever. events.jsonl is append-only and never rewritten, so a
   forgotten value's plaintext landing there is permanent (#419). A
   corroboration row carries a pointer and a status, nothing else.

The emission gates all refuse in the same direction as slice 2's predicate: a
missed corroboration costs a boost, a forged one costs the axis.
"""

import json
import os
import time

import pytest

from daimon_briefing import (briefing, capture, cli, hooks, normalize, recall,
                             store, transcript)

PROJECT = "/p/corroborate"
ITEM = "o-a1d001"
ORIGIN = "S-origin"
OBSERVER = "S-witness"


def _events_file(tmp_checkpoint_dir, project=PROJECT):
    slug = store.project_slug(project)
    d = tmp_checkpoint_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    return d / "events.jsonl"


def _write_events(tmp_checkpoint_dir, *rows, project=PROJECT):
    path = _events_file(tmp_checkpoint_dir, project)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _corr(observer=OBSERVER, ts="2026-07-10T10:00:00Z", item=ITEM):
    return {"ts": ts, "kind": "corroboration",
            "item_ref": f"corroboration:{item}",
            "status": f"corroborated-by:{observer}", "source": "serializer"}


def _evt(status, ts="2026-07-10T10:00:00Z", item=ITEM, **kw):
    return {"ts": ts, "kind": "resolution", "item_ref": item,
            "status": status, "source": "cli", **kw}


# ---------------------------------------------------------------------------
# The reader: store.corroborations
# ---------------------------------------------------------------------------


def test_corroborations_collects_distinct_observing_sessions(tmp_checkpoint_dir):
    # A full pass over the log, NOT the latest-wins fold: every witness counts
    # once, and a second row from the same session is the same witness.
    _write_events(tmp_checkpoint_dir,
                  _corr("S-a", ts="2026-07-10T10:00:00Z"),
                  _corr("S-b", ts="2026-07-11T10:00:00Z"),
                  _corr("S-a", ts="2026-07-12T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["origins"] == {"S-a", "S-b"}
    assert entry["latest_demotion_ts"] is None


def test_corroborations_is_keyed_by_the_BARE_item_id(tmp_checkpoint_dir):
    # The ref on disk is namespaced; the key callers index by is the item's own
    # id, so no reader has to know the namespace exists.
    _write_events(tmp_checkpoint_dir, _corr())
    fold = store.corroborations(project_dir=PROJECT)
    assert set(fold) == {ITEM}
    assert f"corroboration:{ITEM}" not in fold


def test_corroborations_ignores_malformed_and_unparseable_rows(tmp_checkpoint_dir):
    path = _write_events(tmp_checkpoint_dir,
                         _corr(),
                         {"ts": "x", "item_ref": "corroboration:", "status":
                          "corroborated-by:S-z"},          # no item id
                         {"ts": "x", "item_ref": f"corroboration:{ITEM}",
                          "status": "corroborated-by:"},   # no observer
                         {"ts": "x", "item_ref": f"corroboration:{ITEM}",
                          "status": "something-else"},     # not a corroboration
                         {"item_ref": ""},                 # no ref
                         [1, 2, 3])                        # not even a dict
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
    assert store.corroborations(project_dir=PROJECT)[ITEM]["origins"] == {OBSERVER}


def test_corroborations_fails_open_to_empty(tmp_checkpoint_dir):
    assert store.corroborations(project_dir=None) == {}
    assert store.corroborations(project_dir="/p/never-written") == {}
    path = _events_file(tmp_checkpoint_dir)
    path.write_bytes(b"\xff\xfe not utf-8\n")
    assert store.corroborations(project_dir=PROJECT) == {}


def test_items_with_no_corroboration_row_are_absent(tmp_checkpoint_dir):
    # A demotion alone creates no entry — this fold answers "what has been
    # corroborated", not "what has been resolved".
    _write_events(tmp_checkpoint_dir, _evt("resolved"))
    assert store.corroborations(project_dir=PROJECT) == {}


# ---- demotion: a corroboration counts only while nothing contradicts it ----


@pytest.mark.parametrize("status", [
    "resolved",                    # is_resolved -> a closed loop
    "superseded-by:o-b2c003",      # a human verdict
    "supersede-candidate:o-b2c003",  # a MACHINE contradiction still contradicts
    "forgotten:deadbeefcafe",      # a tombstone
    "some future lifecycle fact",  # free-form statuses resolve (is_resolved)
])
def test_a_later_demotion_zeroes_the_effective_count(tmp_checkpoint_dir, status):
    _write_events(tmp_checkpoint_dir,
                  _corr(ts="2026-07-10T10:00:00Z"),
                  _evt(status, ts="2026-07-11T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["origins"] == set()
    assert entry["latest_demotion_ts"] == "2026-07-11T10:00:00Z"
    assert entry["recorded"] == {OBSERVER}  # the ROW is still on the record


def test_a_reopen_does_not_restore_a_demoted_corroboration(tmp_checkpoint_dir):
    # Deliberate: reviving an item does not revive the agreement it lost.
    # Corroboration has to be re-earned by a witness, not by a status change.
    _write_events(tmp_checkpoint_dir,
                  _corr(ts="2026-07-10T10:00:00Z"),
                  _evt("superseded-by:o-b2c003", ts="2026-07-11T10:00:00Z"),
                  _evt("reopened", ts="2026-07-12T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["origins"] == set()
    assert entry["latest_demotion_ts"] == "2026-07-11T10:00:00Z"


def test_a_corroboration_newer_than_the_demotion_counts_again(tmp_checkpoint_dir):
    # The other side: a witness that spoke AFTER the contradiction is evidence
    # about the world as it stands now.
    _write_events(tmp_checkpoint_dir,
                  _corr("S-a", ts="2026-07-10T10:00:00Z"),
                  _evt("resolved", ts="2026-07-11T10:00:00Z"),
                  _corr("S-b", ts="2026-07-12T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["origins"] == {"S-b"}          # S-a stays discounted
    assert entry["recorded"] == {"S-a", "S-b"}


def test_the_latest_demotion_wins_over_an_older_one(tmp_checkpoint_dir):
    _write_events(tmp_checkpoint_dir,
                  _evt("resolved", ts="2026-07-09T10:00:00Z"),
                  _evt("supersede-candidate:o-b2c003", ts="2026-07-11T10:00:00Z"),
                  _corr(ts="2026-07-10T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["latest_demotion_ts"] == "2026-07-11T10:00:00Z"
    assert entry["origins"] == set()


def test_a_demotion_on_another_item_never_discounts_this_one(tmp_checkpoint_dir):
    _write_events(tmp_checkpoint_dir,
                  _corr(ts="2026-07-10T10:00:00Z"),
                  _evt("resolved", ts="2026-07-11T10:00:00Z", item="o-other0"))
    assert store.corroborations(project_dir=PROJECT)[ITEM]["origins"] == {OBSERVER}


def test_a_corroboration_row_is_not_itself_a_demotion(tmp_checkpoint_dir):
    # The rows share a log with the lifecycle stream. A corroboration must
    # never be read as a contradiction of the very item it supports.
    _write_events(tmp_checkpoint_dir,
                  _corr("S-a", ts="2026-07-10T10:00:00Z"),
                  _corr("S-b", ts="2026-07-11T10:00:00Z"))
    entry = store.corroborations(project_dir=PROJECT)[ITEM]
    assert entry["latest_demotion_ts"] is None
    assert entry["origins"] == {"S-a", "S-b"}


# ---------------------------------------------------------------------------
# The fold guard: is_resolved
# ---------------------------------------------------------------------------


def test_is_resolved_never_resolves_a_corroboration_status():
    # Belt to the namespace's braces (scar 0025): were a corroboration status
    # ever to reach the lifecycle fold on a bare ref, it must still not hide
    # the item.
    assert store.is_resolved({"status": f"corroborated-by:{OBSERVER}"}) is False
    assert store.is_resolved({"status": "CORROBORATED-BY:S-x"}) is False


# ---------------------------------------------------------------------------
# The emitter: capture._emit_corroborations
# ---------------------------------------------------------------------------

_TEXT = "the quorint ledger reconciliation drops entries on feed pauses"


def _seed_origin(project=PROJECT, session=ORIGIN, text=_TEXT):
    """Write a real checkpoint for `session` — G7's on-disk evidence — and
    return the id its item landed under. Read back through read_latest, never
    off the argument: write_checkpoint mutates the dict it is handed (scar
    0027), so the caller's copy is the gated one, not the extraction."""
    store.write_checkpoint(session, {
        "session_id": session,
        "working_context": {
            "active_topic": {"text": "seed", "trust": "inferred"},
            "open_questions": [{"text": text, "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }, project_dir=project)
    stored = store.read_latest(project_dir=project, fallback=False)
    return stored["working_context"]["open_questions"][0]["id"]


def _emit(observed, events=None, forgotten_ids=frozenset(), project=PROJECT,
          observer=OBSERVER):
    return capture._emit_corroborations(observed, events or {}, forgotten_ids,
                                        project, observer)


def _rows(tmp_checkpoint_dir, project=PROJECT):
    path = _events_file(tmp_checkpoint_dir, project)
    if not path.exists():
        return []
    return [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_emitted_row_shape_is_exact(tmp_checkpoint_dir):
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada")]) == 1

    row = _rows(tmp_checkpoint_dir)[0]
    assert row["kind"] == "corroboration"
    assert row["item_ref"] == f"corroboration:{item_id}"
    assert row["status"] == f"corroborated-by:{OBSERVER}"
    assert row["source"] == "serializer"
    # The ref NAMES the item, it never IS the item (scar 0025 / the #376
    # displacement trap).
    assert row["item_ref"] != item_id
    # No item_text KEY on the line at all — not empty, absent. events.jsonl is
    # append-only, so any value text written here would be permanent (#419).
    assert "item_text" not in row


def test_reserializing_the_same_session_appends_nothing(tmp_checkpoint_dir):
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada")]) == 1
    assert _emit([(item_id, ORIGIN, "ada")]) == 0
    assert len(_rows(tmp_checkpoint_dir)) == 1


def test_one_row_per_item_however_many_triples_arrive(tmp_checkpoint_dir):
    # Every triple in one call shares the same observing session, so the
    # second one is the same witness saying the same thing.
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada"), (item_id, ORIGIN, "ada")]) == 1
    assert len(_rows(tmp_checkpoint_dir)) == 1


def test_a_different_session_observing_the_same_item_does_append(
        tmp_checkpoint_dir):
    # Liveness for the idempotency gate: it keys on (item, OBSERVER), so a
    # genuinely independent second witness is never mistaken for a re-run.
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada")]) == 1
    assert _emit([(item_id, ORIGIN, "ada")], observer="S-third") == 1
    assert store.corroborations(project_dir=PROJECT)[item_id]["origins"] == {
        OBSERVER, "S-third"}


def test_a_demotion_does_not_let_the_same_session_re_emit(tmp_checkpoint_dir):
    # Idempotency binds to what was RECORDED, not to what currently counts.
    # Otherwise resolving an item would hand its existing witness a second
    # vote — re-earning corroboration with no new evidence.
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada")]) == 1
    store.append_event(item_id, "resolved", project_dir=PROJECT)
    assert store.corroborations(project_dir=PROJECT)[item_id]["origins"] == set()
    assert _emit([(item_id, ORIGIN, "ada")]) == 0


def test_a_forgotten_value_emits_nothing_and_leaks_nothing(tmp_checkpoint_dir):
    # #419's rule applied to this stream: a tombstoned VALUE must not be
    # boosted, and no trace of it may reach the append-only log.
    item_id = _seed_origin()
    control = _seed_origin(session="S-origin-2", text="route telemetry via zephyr")
    assert _emit([(item_id, ORIGIN, "ada"), (control, "S-origin-2", "ada")],
                 forgotten_ids={item_id}) == 1

    raw = _events_file(tmp_checkpoint_dir).read_text(encoding="utf-8")
    assert item_id not in raw       # not even the pointer
    assert _TEXT not in raw         # and never the value — append-only, forever
    assert control in raw           # liveness: a skip, not a mute


def test_an_id_tombstoned_in_the_lifecycle_fold_emits_nothing(
        tmp_checkpoint_dir):
    # The id-keyed half of the forget gate: the item's OWN ref carries a
    # tombstone, whatever text it holds today.
    item_id = _seed_origin()
    events = {item_id: {"status": f"forgotten:{normalize.content_key(_TEXT)}",
                        "source": "cli"}}
    assert _emit([(item_id, ORIGIN, "ada")], events=events) == 0
    assert _rows(tmp_checkpoint_dir) == []


def test_the_forgotten_sweep_reads_both_checkpoints_and_tolerates_no_prev():
    # The value-keyed gate has to look at BOTH sides: the merged checkpoint
    # (where the corroborated item ends up) and prev (where a tombstoned value
    # can still survive under a sibling id, #418). On a project's first
    # serialize there is no prev at all — that is a normal day, not an error.
    merged = {"working_context": {
        "open_questions": [{"text": _TEXT, "id": "o-merged1"},
                           {"text": "an ordinary live loop", "id": "o-live01"},
                           {"text": _TEXT}],          # id-less: nothing to skip
        "recent_decisions": []}}
    keys = {normalize.content_key(_TEXT)}
    assert capture._forgotten_item_ids(keys, merged, None) == {"o-merged1"}
    prev = {"working_context": {"open_questions": [{"text": _TEXT,
                                                    "id": "o-sibling"}],
                                "recent_decisions": []}}
    assert capture._forgotten_item_ids(keys, merged, prev) == {"o-merged1",
                                                               "o-sibling"}
    # Nothing forgotten here -> no hashing, no ids, no cost.
    assert capture._forgotten_item_ids(set(), merged, prev) == set()


def test_an_origin_with_no_checkpoint_on_disk_emits_nothing(
        tmp_checkpoint_dir):
    # G7: slice 2 can only prove the ITEM names a first writer. Whether that
    # writer ever existed is an on-disk question, and it belongs here — an
    # origin nobody can produce is not a witness.
    item_id = _seed_origin()
    assert _emit([(item_id, "S-never-written", "ada")]) == 0
    assert _rows(tmp_checkpoint_dir) == []


def test_an_origin_from_another_project_emits_nothing(tmp_checkpoint_dir):
    # G7 is scoped: a checkpoint that exists but belongs to a DIFFERENT
    # project cannot vouch for a claim in this one.
    item_id = _seed_origin()
    _seed_origin(project="/p/elsewhere", session="S-foreign")
    assert _emit([(item_id, "S-foreign", "ada")]) == 0
    assert _rows(tmp_checkpoint_dir) == []


def test_an_unnameable_observer_emits_nothing(tmp_checkpoint_dir):
    # The row's whole payload is WHO agreed. An anonymous one would count as
    # a witness that can never be checked.
    item_id = _seed_origin()
    assert _emit([(item_id, ORIGIN, "ada")], observer="") == 0
    assert _rows(tmp_checkpoint_dir) == []


def test_an_idless_triple_emits_nothing(tmp_checkpoint_dir):
    _seed_origin()
    assert _emit([("", ORIGIN, "ada")]) == 0
    assert _rows(tmp_checkpoint_dir) == []


def test_nothing_observed_costs_no_read(tmp_checkpoint_dir, monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("read the ledger for an empty observation list")

    monkeypatch.setattr(store, "corroborations", _boom)
    assert _emit([]) == 0


def test_emits_nothing_when_the_corroboration_fold_cannot_be_read(
        tmp_checkpoint_dir, monkeypatch):
    # Fail-safe direction, same as the supersede emitter's forgotten-keys
    # read: unable to prove this is not a duplicate -> write nothing. A missed
    # boost costs a count; a double-counted witness costs the axis.
    item_id = _seed_origin()

    def _boom(*args, **kwargs):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(store, "corroborations", _boom)
    assert _emit([(item_id, ORIGIN, "ada")]) == 0
    assert _rows(tmp_checkpoint_dir) == []


# ---------------------------------------------------------------------------
# THE INVARIANT: a corroboration row may raise trust and nothing else.
#
# Every read path that consumes events.jsonl is checked, because scar 0025 is
# not a theoretical risk — `resolutions` folds on item_ref alone and
# `is_resolved` resolves unknown statuses, so a row on the wrong ref hides the
# item from the briefing, from carry, and from recall at once.
# ---------------------------------------------------------------------------


def _corroborated_checkpoint(session="S-old", text=_TEXT, project=PROJECT):
    """A stored item with a corroboration row against it, as the emitter
    writes one. Returns (item id, stored checkpoint)."""
    store.write_checkpoint(session, {
        "session_id": session,
        "created": "2026-06-25T08:00:00Z",
        "working_context": {
            "active_topic": {"text": "seed", "trust": "inferred"},
            "open_questions": [{"text": text, "trust": "inferred",
                                "importance": 7}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }, project_dir=project)
    stored = store.read_latest(project_dir=project, fallback=False)
    item_id = stored["working_context"]["open_questions"][0]["id"]
    assert store.append_event(store.corroboration_ref(item_id),
                              f"corroborated-by:{OBSERVER}",
                              kind="corroboration", source="serializer",
                              project_dir=project)
    return item_id, stored


def test_a_corroborated_item_is_still_shown_by_the_briefing(tmp_checkpoint_dir):
    item_id, stored = _corroborated_checkpoint()
    out, withheld, stamped = briefing.withhold(
        stored, store.resolutions(project_dir=PROJECT))
    assert withheld == [] and stamped == []
    kept = out["working_context"]["open_questions"]
    assert [i["id"] for i in kept] == [item_id]
    # And no supersede-candidate annotation was invented for it either.
    assert "_supersede_candidate" not in kept[0]


def test_a_corroborated_item_is_still_carried(tmp_checkpoint_dir):
    # capture.run's resolved set, computed exactly as the pipeline computes it
    # (store.py's fold + liveness rule). A corroborated item that landed in
    # here would be silently dropped from the next checkpoint.
    item_id, _ = _corroborated_checkpoint()
    events = store.resolutions(project_dir=PROJECT)
    resolved = frozenset(ref for ref, evt in events.items()
                         if store.is_resolved(evt))
    assert item_id not in resolved
    assert store.corroboration_ref(item_id) not in resolved


def test_a_corroborated_item_is_still_recallable(tmp_checkpoint_dir,
                                                 monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    _corroborated_checkpoint()
    hits = recall.search("quorint", all_projects=True, limit=10)
    by_text = {h["text"]: h for h in hits}
    assert _TEXT in by_text                       # still searchable
    assert by_text[_TEXT]["superseded_by"] is None  # and unmarked


def test_a_corroboration_never_displaces_a_human_verdict(tmp_checkpoint_dir):
    # The #376 displacement trap, pinned: the human spoke about the ITEM, the
    # machine spoke about `corroboration:<item>`. Different refs, so the
    # lifecycle fold cannot even see the machine's row — the verdict stands as
    # latest no matter how many corroborations arrive after it.
    item_id, _ = _corroborated_checkpoint()
    store.append_event(item_id, "superseded-by:o-b2c003", source="cli",
                       project_dir=PROJECT)
    assert capture._emit_corroborations(
        [(item_id, "S-old", "ada")], {}, frozenset(), PROJECT, "S-later") == 1

    latest = store.resolutions(project_dir=PROJECT)[item_id]
    assert latest["status"] == "superseded-by:o-b2c003"
    assert latest["source"] == "cli"
    assert store.is_resolved(latest) is True   # still resolved, as the human said
    # The corroboration itself is on the record, merely discounted.
    entry = store.corroborations(project_dir=PROJECT)[item_id]
    assert entry["recorded"] == {OBSERVER, "S-later"}


# ---------------------------------------------------------------------------
# E2E through capture.run — both doors (#432 parity).
# ---------------------------------------------------------------------------

_PREV_TEXT = "the quorint ledger reconciliation drops entries on feed pauses"
_REWORDED = "quorint ledger reconciliation still dropping entries when the feed pauses"
_QUOTE = "reconciliation drops entries on feed pauses again today"

E2E_PROJECT = "/p/corroborate-e2e"


def _seed_prev(text=_PREV_TEXT, session=ORIGIN):
    """The ORIGIN session's checkpoint: the claim's first writer, on disk (so
    G7 can produce it) and old enough that carry's anachronism guard lets the
    new checkpoint merge."""
    store.write_checkpoint(session, {
        "session_id": session,
        "created": "2026-06-25T08:00:00Z",
        "working_context": {
            "active_topic": {"text": "prior topic", "trust": "inferred"},
            "open_questions": [{"text": text, "trust": "inferred",
                                "importance": 7,
                                "first_seen": "2026-06-20T00:00:00Z"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }, project_dir=E2E_PROJECT)


def _extraction(session, text):
    """What the fake LLM returns: THIS session's own witness — a verbatim item
    whose quote really is in the transcript, so verify_quotes stamps
    quote_verified (slice 2's G3 accepts nothing weaker)."""
    return json.dumps({
        "session_id": session,
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [{"text": text, "trust": "verbatim",
                                "quote": _QUOTE}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    })


def _transcript_for(tmp_path, session, first_stamp="10:00"):
    rows = []
    for i, (body, minute) in enumerate([
            (_QUOTE, first_stamp), ("noted, that matches what we saw", "10:01"),
            ("same failure again", "10:02")]):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append({"type": role, "message": {"role": role, "content": body},
                     "timestamp": f"2026-07-01T{minute}:00Z"})
    p = tmp_path / f"{session}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (1782000000, 1782000000))  # scar 0016: pin the mtime fallback
    return p


def _corroboration_rows(home, project=E2E_PROJECT):
    path = home / "checkpoints" / store.project_slug(project) / "events.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line
            in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("kind") == "corroboration"]


def test_both_capture_doors_emit_one_corroboration_row(
        tmp_path, fake_chat_factory, monkeypatch):
    """A session that independently restates a prior session's claim — its own
    verified verbatim, its own transcript — lands exactly one corroboration
    row, through the CLI door and through the SessionEnd hook alike (#432:
    both call capture.run, so neither may grow a private pipeline)."""
    session = "S-witness"
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", E2E_PROJECT)
    tpath = _transcript_for(tmp_path, session)
    per_door = {}

    for door in ("cli", "hook"):
        home = tmp_path / f"home-{door}"
        monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(home / "checkpoints"))
        monkeypatch.setenv("DAIMON_LOG_DIR", str(home / "logs"))
        _seed_prev()
        if door == "cli":
            monkeypatch.setattr(cli, "_chat",
                                fake_chat_factory(_extraction(session, _PREV_TEXT)))
            assert cli.main(["serialize", str(tpath)]) == 0
        else:
            monkeypatch.setattr(hooks, "_chat",
                                fake_chat_factory(_extraction(session, _PREV_TEXT)))
            monkeypatch.setattr(transcript, "from_session",
                                lambda sid: transcript.from_file(tpath))
            hooks.on_session_end(session_id=session, completed=True,
                                 interrupted=False, model="m", platform="cli",
                                 transcript_path=str(tpath))
        per_door[door] = _corroboration_rows(home)

    rows = per_door["cli"]
    assert len(rows) == 1, "the CLI door emitted no corroboration row"
    assert rows[0]["status"] == f"corroborated-by:{session}"
    assert rows[0]["source"] == "serializer"
    assert rows[0]["item_ref"].startswith("corroboration:")
    assert "item_text" not in rows[0]
    # Door parity: the append-time wall stamp is the one intended difference.
    for r in rows + per_door["hook"]:
        r.pop("ts", None)
    assert per_door["hook"] == rows

    fold = store.corroborations(project_dir=E2E_PROJECT)   # the hook's home
    item_id = rows[0]["item_ref"].split(":", 1)[1]
    assert fold[item_id]["origins"] == {session}


def test_corroboration_survives_carry_and_accumulates_across_sessions(
        tmp_path, fake_chat_factory, monkeypatch):
    # Two independent witnesses, one after the other, the second REWORDING the
    # claim (the twin rail) rather than restating it (the exact rail). The
    # count is per observing session, so it accumulates — and the item keeps
    # one identity across all three checkpoints.
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", E2E_PROJECT)
    _seed_prev()

    for session, text in (("S-witness-1", _PREV_TEXT),
                          ("S-witness-2", _REWORDED)):
        monkeypatch.setattr(cli, "_chat",
                            fake_chat_factory(_extraction(session, text)))
        assert cli.main(["serialize",
                         str(_transcript_for(tmp_path, session))]) == 0

    fold = store.corroborations(project_dir=E2E_PROJECT)
    assert len(fold) == 1, f"expected one corroborated item, got {fold}"
    entry = next(iter(fold.values()))
    assert entry["origins"] == {"S-witness-1", "S-witness-2"}
    assert entry["latest_demotion_ts"] is None
    # The claim is still a live, single item — never resolved by its own
    # corroboration, never duplicated by the rewording.
    latest = store.read_latest(project_dir=E2E_PROJECT, fallback=False)
    questions = latest["working_context"]["open_questions"]
    assert len(questions) == 1
    assert questions[0]["id"] == next(iter(fold))


def test_a_re_serialize_of_the_same_session_adds_no_second_row(
        tmp_path, fake_chat_factory, monkeypatch):
    # The idempotency gate where it actually bites: a duplicate SessionEnd, a
    # heal re-run, any second capture of one session. One session is one
    # witness however many times it speaks.
    #
    # Driven through capture.run rather than the CLI on purpose: the doors
    # short-circuit a byte-identical transcript before serialize even starts
    # (#185), which would make this pass without the gate existing.
    session = "S-witness"
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    _seed_prev()
    messages = transcript.from_file(_transcript_for(tmp_path, session))

    for _ in range(2):
        assert capture.run(
            session, messages, project=E2E_PROJECT,
            chat=fake_chat_factory(_extraction(session, _PREV_TEXT)),
            deadline=time.time() + 60) is not None

    home = tmp_path / ".daimon"
    assert len(_corroboration_rows(home)) == 1


def test_a_forgotten_claim_is_never_corroborated_end_to_end(
        tmp_path, fake_chat_factory, monkeypatch):
    # The value is tombstoned before the witnessing session runs. The
    # re-assertion must produce no corroboration row, and no trace of the
    # value in the append-only log (#419's guarantee, this stream's turn).
    session = "S-witness"
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", E2E_PROJECT)
    _seed_prev()
    store.append_event("o-sibling1",
                       f"forgotten:{normalize.content_key(_PREV_TEXT)}",
                       project_dir=E2E_PROJECT)

    monkeypatch.setattr(cli, "_chat",
                        fake_chat_factory(_extraction(session, _PREV_TEXT)))
    assert cli.main(["serialize", str(_transcript_for(tmp_path, session))]) == 0

    home = tmp_path / ".daimon"
    assert _corroboration_rows(home) == []
    raw = (home / "checkpoints" / store.project_slug(E2E_PROJECT)
           / "events.jsonl").read_text(encoding="utf-8")
    assert _PREV_TEXT not in raw


def test_carry_merge_still_runs_when_corroboration_emission_explodes(
        tmp_path, fake_chat_factory, monkeypatch):
    # Advisory-feature posture (the module's own contract): a broken emission
    # must never cost the checkpoint. The carried item still lands.
    session = "S-witness"
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", E2E_PROJECT)
    _seed_prev(text="an unrelated prior loop about zephyr batching")

    def _boom(*args, **kwargs):
        raise RuntimeError("emission exploded")

    monkeypatch.setattr(capture, "_emit_corroborations", _boom)
    monkeypatch.setattr(cli, "_chat",
                        fake_chat_factory(_extraction(session, _PREV_TEXT)))
    assert cli.main(["serialize", str(_transcript_for(tmp_path, session))]) == 0

    written = store.read_latest(project_dir=E2E_PROJECT, fallback=False)
    assert written["session_id"] == session
    texts = [q["text"] for q in written["working_context"]["open_questions"]]
    assert "an unrelated prior loop about zephyr batching" in texts
