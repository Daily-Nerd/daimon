"""`view.match`: how a binding verb finds its target in the live own body
(#1132 PR 11b). Stores are written by the real writers."""

import itertools

import pytest

from daimon_briefing import normalize, schema, store, trust, view
from daimon_briefing.surfaces import Writer

PROJECT = "/p/match"
S_TOPIC = "SENTINEL-topic the weekly sync cadence"
S_QUESTION = "SENTINEL-question who owns the migration"
S_DECISION = "SENTINEL-decision adopt the strangler pattern"
S_BARE = "SENTINEL-bare contradiction stored as a plain string"
KEEP = "an unrelated decision that stays visible"


def _write():
    cp = {
        "session_id": "S-1", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": S_TOPIC, "trust": "inferred"},
            "open_questions": [{"text": S_QUESTION, "trust": "inferred"}],
            "recent_decisions": [{"text": S_DECISION, "trust": "inferred"},
                                 {"text": KEEP, "trust": "inferred"}]},
        "epistemic_snapshot": {"contradictions_flagged": [S_BARE]},
    }
    store.write_checkpoint("S-1", cp, project_dir=PROJECT, writer=Writer.HUMAN)


def _raw():
    return store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                  admit=store.Admit.ANY)


def _id_of(text):
    for _fld, item in schema.iter_items(_raw()):
        if item.get("text") == text:
            return item["id"]
    raise AssertionError(text)


def _forget(text):
    key = normalize.content_key(text)
    store.append_event(f"i-gone-{key[:6]}", f"forgotten:{key}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT,
                       writer=Writer.HUMAN)


def _quarantine(text, kind):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _close_trust():
    path = store.ledger_file(PROJECT, "trust.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n")


def _texts(got):
    return [h.item["text"] for h in got.hits]


# ---- how ------------------------------------------------------------------


def test_how_must_be_a_known_mode(tmp_checkpoint_dir):
    _write()
    with pytest.raises(ValueError):
        view.match(PROJECT, "x", how="fuzzy")


def test_terms_is_the_default_and_binds_an_exact_id_or_the_same_item(
        tmp_checkpoint_dir):
    _write()
    assert _texts(view.match(PROJECT, _id_of(KEEP))) == [KEEP]
    assert _texts(view.match(PROJECT, "adopt the strangler pattern")) == [
        S_DECISION]
    assert (view.match(PROJECT, "adopt the strangler pattern")
            == view.match(PROJECT, "adopt the strangler pattern",
                          how="terms"))


def test_id_mode_matches_the_exact_id_only(tmp_checkpoint_dir):
    _write()
    item_id = _id_of(KEEP)
    assert _texts(view.match(PROJECT, item_id, how="id")) == [KEEP]
    assert view.match(PROJECT, "an unrelated decision", how="id") == \
        view.Match((), 0)
    assert view.match(PROJECT, item_id[:-1], how="id") == view.Match((), 0)


def test_substring_is_case_insensitive_and_reaches_the_topic_singleton(
        tmp_checkpoint_dir):
    _write()
    got = view.match(PROJECT, "WEEKLY SYNC", how="substring")
    assert _texts(got) == [S_TOPIC]
    assert got.hits[0].field.kind == "topic"
    assert got.hits[0].at == ("working_context", "active_topic", None)
    assert got.withheld == 0 and got.exact is None


def test_substring_reports_the_list_position_of_a_hit(tmp_checkpoint_dir):
    _write()
    got = view.match(PROJECT, "stays visible", how="substring")
    assert got.hits[0].at == ("working_context", "recent_decisions", 1)


def test_substring_ignores_a_withheld_match_and_never_counts_it(
        tmp_checkpoint_dir):
    _write()
    _quarantine(S_QUESTION, "question")
    _forget(S_DECISION)
    got = view.match(PROJECT, "SENTINEL", how="substring")
    assert _texts(got) == [S_TOPIC]
    assert got.withheld == 0 and got.exact is None


# ---- the count -------------------------------------------------------------


def test_a_forgotten_candidate_is_not_counted_and_not_named(tmp_checkpoint_dir):
    _write()
    _forget(S_DECISION)
    by_text = view.match(PROJECT, "adopt the strangler pattern")
    assert by_text == view.Match((), 0)
    by_id = view.match(PROJECT, _id_of(S_DECISION))
    assert by_id == view.Match((), 0) and by_id.exact is None


def test_a_quarantined_candidate_is_counted_and_an_exact_id_is_named(
        tmp_checkpoint_dir):
    _write()
    _quarantine(S_QUESTION, "question")
    fuzzy = view.match(PROJECT, "who owns the migration")
    assert fuzzy.hits == () and fuzzy.withheld == 1 and fuzzy.exact is None
    exact = view.match(PROJECT, _id_of(S_QUESTION))
    assert exact.hits == () and exact.withheld == 1
    assert exact.exact.reason == "quarantine"
    assert exact.exact.item_id == _id_of(S_QUESTION)
    assert exact.exact.quarantine_id.startswith("tr-")


def test_the_exact_withheld_never_carries_a_forgotten_key(tmp_checkpoint_dir):
    _write()
    _close_trust()
    exact = view.match(PROJECT, _id_of(KEEP))
    assert exact.exact.reason == "closed"


def test_a_closed_snapshot_counts_every_hit_and_says_it_is_closed(
        tmp_checkpoint_dir):
    _write()
    _close_trust()
    got = view.match(PROJECT, "adopt the strangler pattern")
    assert got.hits == () and got.withheld == 1 and got.closed is True
    exact = view.match(PROJECT, _id_of(S_DECISION))
    assert exact.exact is not None and exact.exact.reason == "closed"
    assert view.match(PROJECT, "zzz qqq www").closed is True


def test_match_with_no_checkpoint_or_no_candidate_is_empty(tmp_checkpoint_dir):
    assert view.match(PROJECT, "anything") == view.Match((), 0)
    _write()
    assert view.match(PROJECT, "zzz qqq www") == view.Match((), 0)


def test_hits_plus_withheld_plus_forgotten_is_the_raw_match_count(
        tmp_checkpoint_dir):
    from daimon_briefing import carry
    queries = ["strangler pattern", "migration owns", "unrelated decision visible"]
    kinds = ["question", "decision"]
    texts = {"question": S_QUESTION, "decision": S_DECISION}
    for mask in itertools.product([0, 1, 2], repeat=len(kinds)):
        import shutil
        from daimon_briefing import config
        root = config.checkpoint_dir()
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        view._judge_memo.clear()
        _write()
        for flag, kind in zip(mask, kinds):
            if flag == 1:
                _forget(texts[kind])
            elif flag == 2:
                _quarantine(texts[kind], kind)
        items = [(f, i) for f, i in schema.iter_items(_raw())
                 if not f.singleton and i.get("id")]
        generic = carry._generic_terms([str(i.get("text") or "") for _f, i in items])
        for query in queries:
            raw = [i for _f, i in items if carry._same_item(
                query, str(i.get("text") or ""), generic)]
            forgotten = sum(
                1 for i in raw for kind in kinds
                if mask[kinds.index(kind)] == 1 and i["text"] == texts[kind])
            got = view.match(PROJECT, query)
            assert len(got.hits) + got.withheld + forgotten == len(raw), (
                mask, query)


# ---- the never-guess rule ---------------------------------------------------


def test_sole_binds_one_visible_hit_and_nothing_else(tmp_checkpoint_dir):
    _write()
    got = view.match(PROJECT, "adopt the strangler pattern")
    assert got.sole is got.hits[0]
    assert view.Match((), 0).sole is None


def test_sole_refuses_a_visible_hit_beside_a_withheld_one(tmp_checkpoint_dir):
    """Two items match the query; one is quarantined. Binding the visible one
    would be a guess about a value the reader cannot see."""
    _write()
    cp = _raw()
    twin = "SENTINEL-question who owns the migration plan"
    cp["working_context"]["open_questions"].append(
        {"text": twin, "trust": "inferred"})
    store.write_checkpoint("S-2", dict(cp, session_id="S-2",
                           created="2026-08-02T00:00:00Z"),
                           project_dir=PROJECT, writer=Writer.HUMAN)
    _quarantine(S_QUESTION, "question")
    got = view.match(PROJECT, "who owns the migration")
    assert len(got.hits) == 1 and got.withheld == 1
    assert got.sole is None
