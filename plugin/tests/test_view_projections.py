"""`view.open`, `team`, `chain`, `lookup` and `match` (#1132 PR 6a). Stores are
written by the real writers (`store.write_checkpoint`, `store.append_event`,
`trust.propose`)."""

import pytest

from daimon_briefing import config, normalize, schema, store, trust, view

PROJECT = "/p/view-projections"
S_TOPIC = "SENTINEL-topic the weekly sync cadence"
S_QUESTION = "SENTINEL-question who owns the migration"
S_DECISION = "SENTINEL-decision adopt the strangler pattern"
S_BELIEF = "SENTINEL-belief the cache is write through"
S_UNCERTAIN = "SENTINEL-uncertainty whether the index survives restarts"
S_BARE = "SENTINEL-bare contradiction stored as a plain string"
KEEP = "an unrelated decision that stays visible"


def _checkpoint(sid="S-1", created="2026-08-01T00:00:00Z", **texts):
    t = {"topic": S_TOPIC, "question": S_QUESTION, "decision": S_DECISION,
         "belief": S_BELIEF, "uncertainty": S_UNCERTAIN, "bare": S_BARE}
    t.update(texts)
    return {
        "session_id": sid, "created": created,
        "working_context": {
            "active_topic": {"text": t["topic"], "trust": "inferred"},
            "open_questions": [{"text": t["question"], "trust": "inferred"}],
            "recent_decisions": [{"text": t["decision"], "trust": "inferred"},
                                 {"text": KEEP, "trust": "inferred"}]},
        "epistemic_snapshot": {
            "strong_beliefs": [{"text": t["belief"], "trust": "inferred"}],
            "uncertainties": [{"text": t["uncertainty"], "trust": "inferred"}],
            "contradictions_flagged": [t["bare"]]},
    }


def _write(project=PROJECT, **kw):
    cp = _checkpoint(**kw)
    store.write_checkpoint(cp["session_id"], cp, project_dir=project)
    return cp["session_id"]


def _raw_latest(project=PROJECT):
    return store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                  admit=store.Admit.ANY)


def _all_texts(cp):
    out = []
    for _fld, item in schema.iter_items(cp, dicts_only=False):
        out.append(item if isinstance(item, str) else item.get("text"))
    return out


def _forget(text):
    key = normalize.content_key(text)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=PROJECT)


def _quarantine(text, kind):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


ALL = [("topic", S_TOPIC), ("question", S_QUESTION), ("decision", S_DECISION),
       ("belief", S_BELIEF), ("uncertainty", S_UNCERTAIN),
       ("contradiction", S_BARE)]


# ---- open -----------------------------------------------------------------


def test_open_requires_the_live_keyword(tmp_checkpoint_dir):
    with pytest.raises(TypeError):
        view.open(PROJECT)  # type: ignore[call-arg]


def test_open_with_no_checkpoint_returns_an_empty_opened(tmp_checkpoint_dir):
    got = view.open(PROJECT, live=False)
    assert got.checkpoint is None and got.withheld == () and got.suppressed == 0


def test_open_with_nothing_withheld_is_a_faithful_copy(tmp_checkpoint_dir):
    _write()
    raw = _raw_latest()
    got = view.open(PROJECT, live=False)
    assert got.checkpoint == raw and got.checkpoint is not raw
    assert got.withheld == ()
    got.checkpoint["working_context"]["open_questions"].clear()
    assert _raw_latest() == raw                       # nothing written, copy only


@pytest.mark.parametrize("kind,text", ALL, ids=[k for k, _ in ALL])
def test_a_forgotten_value_leaves_no_copy_in_any_field(
        tmp_checkpoint_dir, kind, text):
    _write()
    _forget(text)
    got = view.open(PROJECT, live=False)
    assert text not in _all_texts(got.checkpoint)
    assert [w.kind for w in got.withheld] == [kind]
    assert got.withheld[0].reason == "forgotten"
    assert KEEP in _all_texts(got.checkpoint)


@pytest.mark.parametrize("kind,text", ALL, ids=[k for k, _ in ALL])
def test_a_quarantine_withholds_only_its_own_kind(tmp_checkpoint_dir, kind, text):
    _write()
    q_id = _quarantine(text, kind)
    got = view.open(PROJECT, live=False)
    assert text not in _all_texts(got.checkpoint)
    assert [(w.kind, w.reason, w.quarantine_id) for w in got.withheld] == [
        (kind, "quarantine", q_id)]


def test_a_quarantine_of_another_kind_hides_nothing(tmp_checkpoint_dir):
    _write()
    _quarantine(S_DECISION, "belief")
    got = view.open(PROJECT, live=False)
    assert S_DECISION in _all_texts(got.checkpoint) and got.withheld == ()


def test_a_withheld_singleton_is_deleted_from_the_copy(tmp_checkpoint_dir):
    _write()
    _forget(S_TOPIC)
    got = view.open(PROJECT, live=False)
    assert "active_topic" not in got.checkpoint["working_context"]


def test_a_closed_snapshot_empties_every_field(tmp_checkpoint_dir):
    _write()
    _quarantine(S_DECISION, "decision")
    with open(config.checkpoint_dir() / store.project_slug(PROJECT)
              / "trust.jsonl", "ab") as handle:       # independent byte writer
        handle.write(b"<<<<<<< HEAD\n")
    got = view.open(PROJECT, live=False)
    assert got.snapshot.closed is True
    assert _all_texts(got.checkpoint) == []
    assert {w.reason for w in got.withheld} == {"closed"}
    assert len(got.withheld) == 7


def test_live_drops_resolved_loops_and_counts_them(tmp_checkpoint_dir):
    _write()
    item_id = _raw_latest()["working_context"]["open_questions"][0]["id"]
    store.append_event(item_id, "resolved", project_dir=PROJECT)
    kept = view.open(PROJECT, live=False)
    dropped = view.open(PROJECT, live=True)
    assert S_QUESTION in _all_texts(kept.checkpoint) and kept.suppressed == 0
    assert S_QUESTION not in _all_texts(dropped.checkpoint)
    assert dropped.suppressed == 1 and dropped.withheld == ()


def test_withheld_beats_suppressed(tmp_checkpoint_dir):
    _write()
    item_id = _raw_latest()["working_context"]["open_questions"][0]["id"]
    store.append_event(item_id, "resolved", project_dir=PROJECT)
    _forget(S_QUESTION)
    got = view.open(PROJECT, live=True)
    assert got.suppressed == 0
    assert [w.reason for w in got.withheld] == ["forgotten"]


def test_a_non_list_field_value_is_left_alone(tmp_checkpoint_dir):
    _write()
    path = config.checkpoint_dir() / store.project_slug(PROJECT) / "latest.json"
    import json
    cp = json.loads(path.read_text())
    cp["working_context"]["open_questions"] = "torn"
    cp["working_context"]["active_topic"] = "also torn"
    path.write_text(json.dumps(cp))
    got = view.open(PROJECT, live=False)
    assert got.checkpoint["working_context"]["open_questions"] == "torn"


# ---- team -----------------------------------------------------------------


def test_team_withholds_through_the_readers_own_ledgers(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    _write(sid="g-1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    _quarantine(S_DECISION, "decision")
    got = view.team(PROJECT, live=False)
    assert [author for author, _o in got] == ["grace"]
    opened = got[0][1]
    assert S_DECISION not in _all_texts(opened.checkpoint)
    assert [w.reason for w in opened.withheld] == ["quarantine"]
    assert KEEP in _all_texts(opened.checkpoint)


def test_team_is_empty_without_teammates(tmp_checkpoint_dir):
    assert view.team(PROJECT, live=True) == ()


# ---- chain ----------------------------------------------------------------


def test_chain_is_newest_first_one_entry_per_session(tmp_checkpoint_dir):
    _write(sid="S-old", created="2026-08-01T00:00:00Z")
    _write(sid="S-new", created="2026-08-02T00:00:00Z")
    got = list(view.chain(PROJECT, live=False))
    assert [o.checkpoint["session_id"] for o in got] == ["S-new", "S-old"]


def test_chain_withholds_in_every_retained_copy(tmp_checkpoint_dir):
    _write(sid="S-old", created="2026-08-01T00:00:00Z")
    _write(sid="S-new", created="2026-08-02T00:00:00Z")
    _forget(S_BELIEF)
    for opened in view.chain(PROJECT, live=False):
        assert S_BELIEF not in _all_texts(opened.checkpoint)
        assert [w.kind for w in opened.withheld] == ["belief"]


def test_chain_skips_torn_and_session_less_files(tmp_checkpoint_dir):
    _write(sid="S-1")
    bucket = config.checkpoint_dir() / store.project_slug(PROJECT)
    (bucket / "prev-9.json").write_text("{torn")
    (bucket / "prev-8.json").write_text('{"working_context": {}}')
    assert len(list(view.chain(PROJECT, live=False))) == 1


# ---- lookup ---------------------------------------------------------------


def _id_of(text, project=PROJECT):
    for _fld, item in schema.iter_items(_raw_latest(project)):
        if item.get("text") == text:
            return item["id"]
    raise AssertionError(text)


def test_lookup_finds_the_newest_copy_and_every_occurrence(tmp_checkpoint_dir):
    _write(sid="S-old", created="2026-08-01T00:00:00Z")
    _write(sid="S-new", created="2026-08-02T00:00:00Z")
    item_id = _id_of(KEEP)
    got = view.lookup(PROJECT, item_id)
    assert isinstance(got, view.Found)
    assert got.item["text"] == KEEP and got.field.kind == "decision"
    assert [sid for sid, _created in got.occurrences] == ["S-new", "S-old"]


def test_lookup_of_a_withheld_item_is_withheld_and_carries_no_text(
        tmp_checkpoint_dir):
    _write()
    item_id = _id_of(S_DECISION)
    _forget(S_DECISION)
    got = view.lookup(PROJECT, item_id)
    assert isinstance(got, view.Withheld) and got.reason == "forgotten"
    _quarantine(S_QUESTION, "question")
    q = view.lookup(PROJECT, _id_of(S_QUESTION))
    assert isinstance(q, view.Withheld) and q.reason == "quarantine"


def test_lookup_of_an_unknown_id_is_absent(tmp_checkpoint_dir):
    _write()
    assert view.lookup(PROJECT, "o-ffffffffffff") == view.Absent()
    assert view.lookup("/p/no-such-project", "o-ffffffffffff") == view.Absent()


# ---- match ----------------------------------------------------------------


def test_match_by_exact_id(tmp_checkpoint_dir):
    _write()
    got = view.match(PROJECT, _id_of(KEEP))
    assert got.withheld == 0
    assert [h.item["text"] for h in got.hits] == [KEEP]
    assert got.hits[0].field.kind == "decision"


def test_match_by_text_binds_every_candidate_the_resolver_would(
        tmp_checkpoint_dir):
    _write()
    got = view.match(PROJECT, "adopt the strangler pattern")
    assert [h.item["text"] for h in got.hits] == [S_DECISION]


def test_match_withheld_candidates_are_counted_not_returned(tmp_checkpoint_dir):
    _write()
    _forget(S_DECISION)
    got = view.match(PROJECT, "adopt the strangler pattern")
    assert got.hits == () and got.withheld == 1
    exact = view.match(PROJECT, _id_of(S_DECISION))
    assert exact.hits == () and exact.withheld == 1


def test_match_with_no_checkpoint_or_no_candidate_is_empty(tmp_checkpoint_dir):
    assert view.match(PROJECT, "anything") == view.Match((), 0)
    _write()
    assert view.match(PROJECT, "zzz qqq www") == view.Match((), 0)


def test_hits_plus_withheld_is_the_raw_match_count(tmp_checkpoint_dir):
    """I4 over generated forget/quarantine subsets."""
    import itertools
    from daimon_briefing import carry
    queries = ["strangler pattern", "cache write through", "migration owns",
               "index restarts", "unrelated decision visible"]
    kinds = ["question", "decision", "belief", "uncertainty"]
    texts = {"question": S_QUESTION, "decision": S_DECISION,
             "belief": S_BELIEF, "uncertainty": S_UNCERTAIN}
    for mask in itertools.product([0, 1, 2], repeat=len(kinds)):
        _reset()
        _write()
        for flag, kind in zip(mask, kinds):
            if flag == 1:
                _forget(texts[kind])
            elif flag == 2:
                _quarantine(texts[kind], kind)
        raw = _raw_latest()
        items = [(f, i) for f, i in schema.iter_items(raw)
                 if not f.singleton and i.get("id")]
        generic = carry._generic_terms([str(i.get("text") or "") for _f, i in items])
        for query in queries:
            raw_count = sum(1 for _f, i in items if carry._same_item(
                query, str(i.get("text") or ""), generic))
            got = view.match(PROJECT, query)
            assert len(got.hits) + got.withheld == raw_count, (mask, query)


def _reset():
    import shutil
    root = config.checkpoint_dir()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
