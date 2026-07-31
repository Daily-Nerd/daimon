"""#470 stage 1: idf-weighted mass gate for suggest(), dark behind
DAIMON_RECALL_IDF_GATE.

The gate judges a candidate session by the summed idf of its covered terms —
two generic words shared with a short prompt are a vocabulary coincidence,
not prior work. Everything here is flag-gated and fail-open: flag off (the
default) the code path must be provably inert, and any df-table trouble means
the gate passes (absent evidence never gates, the #450/#452 direction).
"""

import math
import sqlite3

import pytest

from daimon_briefing import config, recall, store


def _cp(sid, topic="working on something", decisions=None, questions=None,
        beliefs=None, created=None):
    cp = {
        "session_id": sid,
        "working_context": {
            "active_topic": {"text": topic, "trust": "inferred"},
            "open_questions": questions or [],
            "recent_decisions": decisions or [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": beliefs or [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }
    if created:
        cp["created"] = created
    return cp


# ---- pin test: the two tokenizers must never drift ----
#
# _df_terms exists because naive salient_terms reuse would truncate a long
# item's df vocabulary at _TERM_CAP and zero out single-term items entirely.
# The subset/equality pair below pins the shared fold/length/stopword rules:
# any rule change that reaches only one of the two tokenizers fails here.


@pytest.mark.parametrize("text", [
    "",
    "yes",
    "deployment",
    "the and or please help",
    "Can you please help me fix the auth token expiry check in middleware?",
    "Necesito arreglar la autenticación de la sesión con tokens",
    "auth_token stays whole and sesión folds to sesion",
    " ".join(f"uniqueterm{i:02d}" for i in range(30)),
    "token " * 30 + "expiry " * 5,
    '"quoted" AND weird (syntax) NEAR/2 tokens 🔥',
])
def test_salient_terms_always_subset_of_df_terms(text):
    assert set(recall.salient_terms(text)) <= recall._df_terms(text)


@pytest.mark.parametrize("text", [
    "auth token expiry",
    "Can you please help me fix the auth token expiry check in middleware?",
    "Necesito arreglar la autenticación de la sesión con tokens",
    " ".join(f"uniqueterm{i:02d}" for i in range(24)),
])
def test_df_terms_equal_salient_terms_inside_the_band(text):
    # With >=2 and <=24 salient tokens neither the cap nor the floor bites, so
    # the two tokenizers must agree exactly.
    sal = recall.salient_terms(text)
    assert 2 <= len(sal) <= 24, "fixture drifted out of the band"
    assert set(sal) == recall._df_terms(text)


def test_df_terms_keep_single_term_items():
    # salient_terms floors at _MIN_TERMS (a one-word prompt is not a retrieval
    # request); a one-word ITEM is still a document for df purposes.
    assert recall.salient_terms("deployment") == []
    assert recall._df_terms("deployment") == {"deployment"}


def test_df_terms_are_uncapped_beyond_term_cap():
    text = " ".join(f"uniqueterm{i:02d}" for i in range(30))
    assert len(recall.salient_terms(text)) == recall._TERM_CAP
    assert len(recall._df_terms(text)) == 30


# ---- rebuild builds the per-project df tables in the same temp-db pass ----


def _df_rows(slug):
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        term_df = dict(conn.execute(
            "SELECT term, df FROM term_df WHERE project_slug = ?",
            (slug,)).fetchall())
        meta = conn.execute(
            "SELECT n_items, median_idf FROM df_meta WHERE project_slug = ?",
            (slug,)).fetchone()
    finally:
        conn.close()
    return term_df, meta


def test_rebuild_builds_term_df_counts(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", topic="wombat topic",
            decisions=[
                {"text": "alpaca bison decision", "trust": "inferred"},
                {"text": "alpaca camel decision", "trust": "inferred"},
            ]),
        project_dir="/repo/x")
    recall.rebuild()
    slug = store.project_slug("/repo/x")
    term_df, meta = _df_rows(slug)
    # Three indexed items for the project: topic + 2 decisions.
    assert meta is not None and meta[0] == 3
    assert term_df["alpaca"] == 2     # once per ITEM, not per occurrence
    assert term_df["bison"] == 1
    assert term_df["camel"] == 1
    assert term_df["decision"] == 2
    assert term_df["wombat"] == 1


def test_rebuild_df_tokenizes_text_plus_quote(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", questions=[{"text": "gateway chunk threshold",
                              "trust": "verbatim",
                              "quote": "the kumquat line rules chunking"}]),
        project_dir="/repo/x")
    recall.rebuild()
    term_df, _meta = _df_rows(store.project_slug("/repo/x"))
    # Coverage substring-tests text AND quote, so df must count both.
    assert term_df["kumquat"] == 1
    assert term_df["gateway"] == 1


def test_rebuild_df_is_per_project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-x", _cp("S-x", decisions=[{"text": "pelican caching adopted",
                                      "trust": "inferred"}]),
        project_dir="/repo/x")
    store.write_checkpoint(
        "S-y", _cp("S-y", decisions=[{"text": "pelican caching rejected",
                                      "trust": "inferred"}]),
        project_dir="/repo/y")
    recall.rebuild()
    df_x, meta_x = _df_rows(store.project_slug("/repo/x"))
    df_y, meta_y = _df_rows(store.project_slug("/repo/y"))
    assert df_x["pelican"] == 1 and df_y["pelican"] == 1  # never pooled
    assert meta_x[0] == 2 and meta_y[0] == 2  # topic + decision each


def test_rebuild_precomputes_median_idf(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", topic="ostrich topic",
            decisions=[{"text": "ostrich ledger decision", "trust": "inferred"}]),
        project_dir="/repo/x")
    recall.rebuild()
    _term_df, meta = _df_rows(store.project_slug("/repo/x"))
    # Vocabulary: ostrich df=2, topic/ledger/decision df=1 over n=2 items.
    # idfs sorted: [0, ln2, ln2, ln2] -> median = ln2.
    assert meta is not None
    assert meta[1] == pytest.approx(math.log(2))


def test_schema_version_bumped_for_df_tables():
    # #470 added term_df/df_meta — an old db has neither table, so the bump
    # must funnel it into the standard silent rebuild.
    assert int(recall._SCHEMA_VERSION) >= 6


# ---- the flag (config helper, _get pattern) ----


def test_recall_idf_gate_flag_default_off(monkeypatch):
    monkeypatch.delenv("DAIMON_RECALL_IDF_GATE", raising=False)
    assert config.recall_idf_gate() is False


def test_recall_idf_gate_flag_truthy(monkeypatch):
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    assert config.recall_idf_gate() is True


def test_recall_idf_gate_flag_falsy(monkeypatch):
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "0")
    assert config.recall_idf_gate() is False


# ---- the gate in suggest(), dark by default ----
#
# Corpus shape for the gate scenarios: 40 filler DECISIONS sharing the generic
# pair "deploy pipeline" (decisions on purpose — open questions are exempt by
# design, so a question-filler corpus could never demonstrate the drop). With
# n_items ~= 43, idf(deploy) = idf(pipeline) = ln(43/41) ~= 0.05: a session
# covered only by that pair carries ~0.1 of mass, far under _IDF_MIN_MASS,
# while three df=1 terms carry 3*ln(43) ~= 11.3, comfortably over it.

_GENERIC_PROMPT = "checking the deploy pipeline again"


def _seed_generic_corpus(project="/repo/x"):
    filler = [{"text": f"deploy pipeline filler{i:02d} note",
               "trust": "inferred"} for i in range(40)]
    store.write_checkpoint("S-hist", _cp("S-hist", decisions=filler),
                           project_dir=project)


def test_flag_off_output_identical_on_a_gateable_scenario(
        tmp_checkpoint_dir, monkeypatch):
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-gen", _cp("S-gen", decisions=[{"text": "deploy pipeline rework",
                                          "trust": "inferred"}]),
        project_dir="/repo/x")
    monkeypatch.delenv("DAIMON_RECALL_IDF_GATE", raising=False)
    absent = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                            current_session="S-now", limit=5)
    assert absent and any(r["session_id"] == "S-gen" for r in absent)
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "0")
    falsy = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                           current_session="S-now", limit=5)
    assert falsy == absent  # flag absent/falsy: provably inert


def test_flag_on_silences_generic_two_term_session(
        tmp_checkpoint_dir, monkeypatch):
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-gen", _cp("S-gen", decisions=[{"text": "deploy pipeline rework",
                                          "trust": "inferred"}]),
        project_dir="/repo/x")
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    out = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                         current_session="S-now", limit=5)
    assert out == []


def test_flag_on_rare_term_session_passes(tmp_checkpoint_dir, monkeypatch):
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-rare", _cp("S-rare", decisions=[{
            "text": "zyxwvut quorblatz recalibration rework",
            "trust": "inferred"}]),
        project_dir="/repo/x")
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    out = recall.suggest("zyxwvut quorblatz recalibration status",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-rare"


def test_exempt_rows_survive_a_low_mass_session(tmp_checkpoint_dir, monkeypatch):
    # Mirrors the cli age-gate exemptions (#452): a pinned standing rule and a
    # still-open question survive a session that fails mass; the sibling
    # decision (highest importance, so it would otherwise be the session's
    # chosen row) does not.
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-mix", _cp(
            "S-mix",
            beliefs=[{"text": "deploy pipeline freeze standing rule",
                      "trust": "inferred", "pinned": True, "importance": 5}],
            questions=[{"text": "deploy pipeline flakiness cause unknown",
                        "trust": "inferred", "importance": 5}],
            decisions=[{"text": "deploy pipeline retry added",
                        "trust": "inferred", "importance": 9}]),
        project_dir="/repo/x")
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    out = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                         current_session="S-now", limit=5)
    assert out and all(r["session_id"] == "S-mix" for r in out)
    for r in out:
        assert r["pinned"] or (r["kind"] == "question"
                               and not r["superseded_by"])


def test_resolved_question_is_not_exempt(tmp_checkpoint_dir, monkeypatch):
    # The question exemption is for OPEN questions only — a resolved one is
    # ordinary content and gates like any other row.
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-resq", _cp("S-resq", questions=[{
            "text": "deploy pipeline verification pending",
            "trust": "inferred"}]),
        project_dir="/repo/x")
    iid = store.read_latest(project_dir="/repo/x", fallback=False)[
        "working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/repo/x")
    monkeypatch.delenv("DAIMON_RECALL_IDF_GATE", raising=False)
    control = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                             current_session="S-now", limit=5)
    assert any(r["session_id"] == "S-resq" for r in control)  # gate did it
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    out = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                         current_session="S-now", limit=5)
    assert not any(r["session_id"] == "S-resq" for r in out)


def test_missing_df_term_contributes_median_idf(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "df.db"))
    conn.execute("CREATE TABLE term_df(project_slug TEXT, term TEXT,"
                 " df INTEGER, PRIMARY KEY(project_slug, term))")
    conn.execute("CREATE TABLE df_meta(project_slug TEXT PRIMARY KEY,"
                 " n_items INTEGER, median_idf REAL)")
    conn.execute("INSERT INTO term_df VALUES ('proj', 'common', 90)")
    conn.execute("INSERT INTO df_meta VALUES ('proj', 100, 5.0)")
    conn.commit()
    # 'ghost' has no df row (a substring hit on a non-token): it contributes
    # the MEDIAN. Were the fallback 0, S-med (two no-df terms) would fail;
    # were it the vocabulary max... there is no max here to reward — median
    # is neutral by construction. ln(100/90)+5.0 < 8; 5.0+5.0 >= 8.
    failed = recall._low_idf_mass_sessions(conn, "proj", {
        "S-low": {"common", "ghost"},
        "S-med": {"ghost", "ghost2"},
    })
    conn.close()
    assert failed == {"S-low"}


def test_gate_fails_open_without_df_rows(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "empty.db"))
    # No tables at all: any sqlite trouble means the gate passes.
    assert recall._low_idf_mass_sessions(
        conn, "proj", {"S1": {"alpha", "beta"}}) == set()
    conn.execute("CREATE TABLE term_df(project_slug TEXT, term TEXT,"
                 " df INTEGER, PRIMARY KEY(project_slug, term))")
    conn.execute("CREATE TABLE df_meta(project_slug TEXT PRIMARY KEY,"
                 " n_items INTEGER, median_idf REAL)")
    conn.commit()
    # Tables exist but the project has no df_meta row: still open.
    assert recall._low_idf_mass_sessions(
        conn, "proj", {"S1": {"alpha", "beta"}}) == set()
    conn.close()


def test_gate_is_per_project(tmp_checkpoint_dir, monkeypatch):
    # The same three terms are generic vocabulary in project A and rare signal
    # in project B — the gate must judge each project by its own df.
    filler_a = [{"text": f"gadget widget flange fillera{i:02d}",
                 "trust": "inferred"} for i in range(30)]
    store.write_checkpoint("S-a-hist", _cp("S-a-hist", decisions=filler_a),
                           project_dir="/repo/a")
    store.write_checkpoint(
        "S-a-x", _cp("S-a-x", decisions=[{"text": "gadget widget flange rework",
                                          "trust": "inferred"}]),
        project_dir="/repo/a")
    filler_b = [{"text": f"fillerb{i:02d} standalone ledger",
                 "trust": "inferred"} for i in range(30)]
    store.write_checkpoint("S-b-hist", _cp("S-b-hist", decisions=filler_b),
                           project_dir="/repo/b")
    store.write_checkpoint(
        "S-b-x", _cp("S-b-x", decisions=[{"text": "gadget widget flange rework",
                                          "trust": "inferred"}]),
        project_dir="/repo/b")
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    prompt = "gadget widget flange check"
    out_a = recall.suggest(prompt, project_dir="/repo/a",
                           current_session="S-now", limit=5)
    out_b = recall.suggest(prompt, project_dir="/repo/b",
                           current_session="S-now", limit=5)
    assert out_a == []
    assert out_b and out_b[0]["session_id"] == "S-b-x"


def test_gate_fails_open_when_term_df_table_is_gone(
        tmp_checkpoint_dir, monkeypatch):
    # A v6-stamped db whose df table was clobbered behind our back: the
    # fingerprint still matches, so no rebuild fires — the gate must open,
    # never silence suggestions over its own missing evidence.
    _seed_generic_corpus()
    store.write_checkpoint(
        "S-gen", _cp("S-gen", decisions=[{"text": "deploy pipeline rework",
                                          "trust": "inferred"}]),
        project_dir="/repo/x")
    monkeypatch.setenv("DAIMON_RECALL_IDF_GATE", "1")
    assert recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                          current_session="S-now", limit=5) == []
    conn = sqlite3.connect(str(config.recall_db()))
    conn.execute("DROP TABLE term_df")
    conn.commit()
    conn.close()
    out = recall.suggest(_GENERIC_PROMPT, project_dir="/repo/x",
                         current_session="S-now", limit=5)
    assert out and any(r["session_id"] == "S-gen" for r in out)
