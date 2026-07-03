"""Recall index (#112): derived sqlite3+FTS5 over local + team checkpoints.

The index is NEVER source of truth — every test here exercises the
rebuild-by-scan contract: corrupt it, delete it, add data behind its back, and
recall must still answer correctly (or empty), never traceback.
"""

import json
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


def _write_team_file(author_slug, sid, cp, project_dir=None):
    """Lay down a foreign teammate's mirrored checkpoint directly (no pointer —
    the team dir is append-only files, exactly what _dual_write_team produces)."""
    d = config.team_dir() / "local" / "authors" / author_slug
    d.mkdir(parents=True, exist_ok=True)
    blob = dict(cp)
    blob.setdefault("author", author_slug)
    if project_dir is not None:
        blob["project_slug"] = store.project_slug(project_dir)
    (d / f"{sid}.json").write_text(
        json.dumps(blob, ensure_ascii=False), encoding="utf-8"
    )


# ---- rebuild: scans local flat dir + team dir ----


def test_rebuild_indexes_local_items(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", decisions=[{"text": "Adopt sqlite for the recall index",
                              "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    n = recall.rebuild()
    assert n > 0
    hits = recall.search("sqlite", all_projects=True)
    assert any("recall index" in h["text"] for h in hits)
    assert hits[0]["author"] == "ada"
    assert hits[0]["kind"] == "decision"


def test_rebuild_indexes_team_items(tmp_checkpoint_dir, monkeypatch):
    _write_team_file(
        "grace", "S-g",
        _cp("S-g", beliefs=[{"text": "Zoneless flamingo pipelines are stable",
                             "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    recall.rebuild()
    hits = recall.search("flamingo", all_projects=True)
    assert len(hits) == 1
    assert hits[0]["author"] == "grace"
    assert hits[0]["kind"] == "belief"


def test_search_matches_quote_text(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", questions=[{
            "text": "Chunk threshold for the serializer",
            "trust": "verbatim",
            "quote": "do we chunk below the kumquat line or single-pass?",
        }]),
        project_dir="/repo/x",
    )
    hits = recall.search("kumquat", all_projects=True)
    assert len(hits) == 1
    assert hits[0]["text"] == "Chunk threshold for the serializer"
    assert hits[0]["kind"] == "question"


def test_search_indexes_every_cognitive_section(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    cp = _cp("S1", topic="albatross topic",
             decisions=[{"text": "albatross decision", "trust": "inferred"}],
             questions=[{"text": "albatross question", "trust": "inferred"}],
             beliefs=[{"text": "albatross belief", "trust": "inferred"}])
    cp["epistemic_snapshot"]["uncertainties"] = [
        {"text": "albatross uncertainty", "trust": "inferred"}]
    cp["epistemic_snapshot"]["contradictions_flagged"] = [
        {"text": "albatross contradiction", "trust": "inferred"}]
    store.write_checkpoint("S1", cp, project_dir="/repo/x")
    hits = recall.search("albatross", all_projects=True)
    kinds = {h["kind"] for h in hits}
    assert kinds == {"topic", "decision", "question", "belief",
                     "uncertainty", "contradiction"}


# ---- project scoping ----


def test_search_scopes_to_project(tmp_checkpoint_dir, monkeypatch):
    _write_team_file("grace", "S-x", _cp("S-x", decisions=[
        {"text": "pelican decision in x", "trust": "inferred"}]),
        project_dir="/repo/x")
    _write_team_file("grace", "S-y", _cp("S-y", decisions=[
        {"text": "pelican decision in y", "trust": "inferred"}]),
        project_dir="/repo/y")
    hits = recall.search("pelican", project_dir="/repo/x")
    assert [h["text"] for h in hits] == ["pelican decision in x"]
    both = recall.search("pelican", all_projects=True)
    assert len(both) == 2


def test_local_checkpoint_attributed_via_bucket_pointer(tmp_checkpoint_dir, monkeypatch):
    # LEGACY attribution path: a pre-stamp local file carries no project_slug,
    # so the per-project bucket pointer alongside it attributes the session.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    cp = _cp("S-legacy", decisions=[
        {"text": "ocelot decision", "trust": "inferred"}])
    d = config.checkpoint_dir()
    slug = store.project_slug("/repo/x")
    (d / slug).mkdir(parents=True, exist_ok=True)
    (d / "S-legacy.json").write_text(json.dumps(cp), encoding="utf-8")
    (d / slug / "latest.json").write_text(json.dumps(cp), encoding="utf-8")
    hits = recall.search("ocelot", project_dir="/repo/x")
    assert len(hits) == 1
    assert hits[0]["project_slug"] == slug


def test_local_attribution_survives_pointer_rotation(tmp_checkpoint_dir, monkeypatch):
    # Bucket pointers keep only the last N writes: a session older than the
    # pointer window used to index with project_slug NULL — invisible to scoped
    # recall forever, which contradicts proactive recall's whole purpose
    # (surfacing FORGOTTEN prior work). write_checkpoint stamps project_slug
    # into the local file, so attribution outlives the pointers.
    import shutil
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "ocelot decision", "trust": "inferred"}]), project_dir="/repo/x")
    shutil.rmtree(config.checkpoint_dir() / store.project_slug("/repo/x"))
    hits = recall.search("ocelot", project_dir="/repo/x")
    assert len(hits) == 1
    assert hits[0]["project_slug"] == store.project_slug("/repo/x")


def test_local_and_team_copies_not_double_indexed(tmp_checkpoint_dir, monkeypatch):
    # DAIMON_TEAM=1 dual-writes the SAME checkpoint to flat dir + team dir;
    # recall must index it once, not twice.
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "wombat decision", "trust": "inferred"}]), project_dir="/repo/x")
    hits = recall.search("wombat", all_projects=True)
    assert len(hits) == 1


# ---- supersession: whole-checkpoint recency per (author, project) ----


def test_supersession_flags_older_checkpoint_items(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", decisions=[{"text": "narwhal decision v1", "trust": "inferred"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new", decisions=[{"text": "narwhal decision v2", "trust": "inferred"}],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("narwhal", all_projects=True)
    by_sid = {h["session_id"]: h for h in hits}
    assert by_sid["S-old"]["superseded_by"] == "S-new"
    assert by_sid["S-new"]["superseded_by"] is None
    # superseded items are ranked DOWN, not hidden
    assert hits[0]["session_id"] == "S-new"
    assert hits[-1]["session_id"] == "S-old"


def test_supersession_is_per_author(tmp_checkpoint_dir, monkeypatch):
    # grace's older checkpoint must NOT be superseded by ada's newer one.
    _write_team_file("ada", "S-a", _cp(
        "S-a", decisions=[{"text": "quokka call by ada", "trust": "inferred"}],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    _write_team_file("grace", "S-g", _cp(
        "S-g", decisions=[{"text": "quokka call by grace", "trust": "inferred"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("quokka", all_projects=True)
    assert all(h["superseded_by"] is None for h in hits)


# ---- derived, never source of truth ----


def test_search_auto_refreshes_when_new_checkpoint_lands(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "first axolotl decision", "trust": "inferred"}]), project_dir="/repo/x")
    assert recall.search("axolotl", all_projects=True)  # builds the index
    store.write_checkpoint("S2", _cp("S2", decisions=[
        {"text": "second axolotl decision", "trust": "inferred"}],
        created="2030-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("axolotl", all_projects=True)  # must auto-refresh
    assert {h["session_id"] for h in hits} == {"S1", "S2"}


def test_corrupt_db_auto_rebuilds(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "ibis decision", "trust": "inferred"}]), project_dir="/repo/x")
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_text("this is not a sqlite database", encoding="utf-8")
    hits = recall.search("ibis", all_projects=True)  # must silently rebuild
    assert len(hits) == 1


def test_stale_meta_db_auto_rebuilds(tmp_checkpoint_dir, monkeypatch):
    # A structurally-valid sqlite file that is not OUR schema (e.g. a truncated
    # or foreign db) must also be treated as corrupt and rebuilt.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "heron decision", "trust": "inferred"}]), project_dir="/repo/x")
    db = config.recall_db()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE unrelated(x)")
    conn.commit()
    conn.close()
    hits = recall.search("heron", all_projects=True)
    assert len(hits) == 1


def test_rebuild_skips_torn_and_pointer_files(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "tapir decision", "trust": "inferred"}]), project_dir="/repo/x")
    (tmp_checkpoint_dir / "torn.json").write_text("{not json", encoding="utf-8")
    recall.rebuild()
    hits = recall.search("tapir", all_projects=True)
    assert len(hits) == 1  # pointer copies (latest/prev) never double-index S1


# ---- hostile queries: FTS5 syntax must never escape as a traceback ----


@pytest.mark.parametrize("query", [
    '"', '""', "'", 'foo AND', 'AND', 'OR', 'NOT', '(((', 'foo)', '*', 'foo*bar',
    'NEAR(', 'a NEAR/2 b', 'col:foo', '-foo', '🔥', '🔥"', 'foo "bar', '  ',
])
def test_hostile_queries_never_raise(tmp_checkpoint_dir, monkeypatch, query):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1"), project_dir="/repo/x")
    hits = recall.search(query, all_projects=True)
    assert isinstance(hits, list)


def test_plain_operator_words_still_match_as_terms(tmp_checkpoint_dir, monkeypatch):
    # "AND"/"OR" typed by a user are search words, not FTS5 operators.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "auth AND caching rework", "trust": "inferred"}]),
        project_dir="/repo/x")
    hits = recall.search("auth AND caching", all_projects=True)
    assert len(hits) == 1


def test_empty_query_returns_empty(tmp_checkpoint_dir):
    assert recall.search("", all_projects=True) == []


# ---- #25: AND-then-OR fallback — a richer cue must never zero out recall ----


def test_multi_term_query_falls_back_to_or_when_and_matches_nothing(
        tmp_checkpoint_dir, monkeypatch):
    # Field find (2026-07-03): "science" hit, "ACB" hit, "science ACB" -> no
    # matches. Strict AND punishes cue enrichment (encoding specificity says
    # more cue should IMPROVE retrieval). When AND yields nothing, retry the
    # same quoted tokens with OR and return ranked partial matches.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "vulture research arc closed", "trust": "inferred"}]),
        project_dir="/repo/x")
    store.write_checkpoint("S2", _cp("S2", decisions=[
        {"text": "condor migration shipped", "trust": "inferred"}]),
        project_dir="/repo/x")
    hits = recall.search("vulture condor", all_projects=True)
    texts = " ".join(h["text"] for h in hits)
    assert "vulture" in texts and "condor" in texts


def test_and_semantics_stay_primary_when_terms_cooccur(
        tmp_checkpoint_dir, monkeypatch):
    # The fallback fires ONLY on an empty AND result: when one item holds all
    # terms, precision wins and the single-term item must NOT dilute results.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "osprey harrier combined rework", "trust": "inferred"}]),
        project_dir="/repo/x")
    store.write_checkpoint("S2", _cp("S2", decisions=[
        {"text": "osprey solo note", "trust": "inferred"}]),
        project_dir="/repo/x")
    hits = recall.search("osprey harrier", all_projects=True)
    assert [h["text"] for h in hits] == ["osprey harrier combined rework"]


def test_or_fallback_respects_project_scope(tmp_checkpoint_dir, monkeypatch):
    # Partial matches must not leak across projects: the fallback query keeps
    # the same slug filter as the AND pass.
    _write_team_file("grace", "S-x", _cp("S-x", decisions=[
        {"text": "vulture decision in x", "trust": "inferred"}]),
        project_dir="/repo/x")
    _write_team_file("grace", "S-y", _cp("S-y", decisions=[
        {"text": "condor decision in y", "trust": "inferred"}]),
        project_dir="/repo/y")
    hits = recall.search("vulture condor", project_dir="/repo/x")
    assert [h["text"] for h in hits] == ["vulture decision in x"]


def test_single_term_miss_stays_empty(tmp_checkpoint_dir, monkeypatch):
    # One token has no OR variant — a genuine miss stays a miss.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "vulture research arc closed", "trust": "inferred"}]),
        project_dir="/repo/x")
    assert recall.search("homework", all_projects=True) == []


def test_search_empty_world_returns_empty(tmp_checkpoint_dir):
    # No checkpoints anywhere, no db — search must not invent or crash.
    assert recall.search("anything", all_projects=True) == []


def test_fingerprint_detects_same_second_delete_plus_add(tmp_checkpoint_dir, monkeypatch):
    # Delete A + add C where count AND max-mtime stay identical: a count+newest
    # fingerprint serves stale rows. The fingerprint must see the name change.
    import os
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-A", _cp("S-A", topic="alpha topic"), project_dir="/repo/x")
    store.write_checkpoint("S-B", _cp("S-B", topic="beta topic"), project_dir="/repo/x")
    assert len(recall.search("alpha", all_projects=True)) >= 1  # index built

    d = tmp_checkpoint_dir
    frozen = max((d / "S-A.json").stat().st_mtime, (d / "S-B.json").stat().st_mtime)
    (d / "S-A.json").unlink()
    (d / "S-C.json").write_text(
        json.dumps({**_cp("S-C", topic="gamma topic"), "author": "ada"}),
        encoding="utf-8",
    )
    os.utime(d / "S-C.json", (frozen, frozen))
    os.utime(d / "S-B.json", (frozen, frozen))

    assert recall.search("gamma", all_projects=True), "new file invisible — stale fingerprint"
    assert recall.search("alpha", all_projects=True) == []  # deleted file gone


def test_search_survives_rebuild_oserror(tmp_checkpoint_dir, monkeypatch):
    # Disk-full (or any OSError) during the derived rebuild must degrade to [],
    # never propagate out of search().
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1"), project_dir="/repo/x")
    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(recall, "rebuild", boom)
    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    hits = recall.search("something", all_projects=True)
    assert hits == []


# ---- #120: recall honors the team retention window (parity with read_team) ----


def test_recall_skips_aged_out_teammate_items(tmp_checkpoint_dir, monkeypatch):
    # An aged-out teammate checkpoint is invisible to brief --team (read_team's
    # retention window, #113) — recall must agree, not resurrect it.
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "30")
    _write_team_file(
        "grace", "S-old",
        {**_cp("S-old", topic="ancient kraken refactor"),
         "created": "2020-01-01T00:00:00Z"},
        project_dir="/repo/x",
    )
    _write_team_file(
        "grace", "S-new",
        {**_cp("S-new", topic="fresh kraken redesign")},
        project_dir="/repo/x",
    )
    recall.rebuild()
    hits = recall.search("kraken", all_projects=True)
    texts = " ".join(h["text"] for h in hits)
    assert "fresh" in texts
    assert "ancient" not in texts  # aged out — matches brief --team


def test_recall_retention_zero_keeps_all_team_items(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "0")
    _write_team_file(
        "grace", "S-old",
        {**_cp("S-old", topic="ancient kraken refactor"),
         "created": "2020-01-01T00:00:00Z"},
        project_dir="/repo/x",
    )
    recall.rebuild()
    assert len(recall.search("kraken", all_projects=True)) == 1


def test_recall_own_local_history_not_windowed(tmp_checkpoint_dir, monkeypatch):
    # Retention is a TEAM-view concept: your own local flat-store history stays
    # fully searchable however old it is.
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "30")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-mine",
        {**_cp("S-mine", topic="paleolithic basilisk migration"),
         "created": "2019-06-01T00:00:00Z"},
        project_dir="/repo/x",
    )
    recall.rebuild()
    assert len(recall.search("basilisk", all_projects=True)) == 1


def test_recall_refreshes_when_retention_knob_changes(tmp_checkpoint_dir, monkeypatch):
    # Retention changes index CONTENT without touching any file — the staleness
    # fingerprint must notice the knob change, or a stale index resurrects
    # aged-out items until an unrelated write.
    _write_team_file(
        "grace", "S-old",
        {**_cp("S-old", topic="ancient kraken refactor"),
         "created": "2020-01-01T00:00:00Z"},
        project_dir="/repo/x",
    )
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "0")
    assert len(recall.search("kraken", all_projects=True)) == 1  # kept: 0=all
    monkeypatch.setenv("DAIMON_TEAM_RETENTION_DAYS", "30")
    assert recall.search("kraken", all_projects=True) == []  # knob change seen


# ---- #125: schema v2 (importance + first_seen indexed) ----


def _cp125(sid, questions=None, decisions=None, created=None):
    return _cp(sid, topic=f"topic of {sid}", questions=questions,
               decisions=decisions, created=created)


def test_rebuild_indexes_importance_and_first_seen(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint(
        "S1",
        _cp125("S1", questions=[{
            "text": "gateway response cache pins bad responses",
            "trust": "inferred", "importance": 8,
            "first_seen": "2026-06-01T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    recall.rebuild()
    hits = recall.search("gateway cache", all_projects=True)
    assert hits and hits[0]["importance"] == 8
    assert hits[0]["first_seen"] == "2026-06-01T00:00:00Z"


def test_schema_v1_db_forces_rebuild(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint(
        "S1", _cp125("S1", decisions=[{"text": "flamingo pipeline adopted",
                                       "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    recall.rebuild()
    # Regress the schema stamp to v1 — next search must rebuild, not error on
    # the missing columns.
    conn = sqlite3.connect(str(config.recall_db()))
    conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    hits = recall.search("flamingo", all_projects=True)
    assert any("flamingo" in h["text"] for h in hits)


# ---- #125: salient-term extraction ----


def test_salient_terms_drop_stopwords_and_short_tokens():
    terms = recall.salient_terms(
        "Can you please help me fix the auth token expiry check in middleware?")
    assert "auth" in terms and "token" in terms and "middleware" in terms
    assert "please" not in terms and "the" not in terms and "me" not in terms


def test_salient_terms_too_few_means_silence():
    assert recall.salient_terms("yes") == []
    assert recall.salient_terms("continue please") == []


def test_salient_terms_dedupe_and_cap():
    terms = recall.salient_terms("token " * 30 + "expiry " * 5)
    assert terms.count("token") == 1
    assert len(terms) <= 12


def test_salient_terms_drop_spanish_stopwords():
    # Spanish function/filler words must not count as salient signal (#3):
    # unfiltered they inflate the _MIN_TERMS/_MIN_OVERLAP gates on
    # function-word coincidences and pollute carry's twin-dedup lexicon.
    terms = recall.salient_terms(
        "Por favor necesito ayuda para arreglar el problema del token en esta sesión")
    assert "token" in terms
    for filler in ("por", "favor", "necesito", "ayuda", "para", "arreglar",
                   "problema", "esta"):
        assert filler not in terms, filler


def test_salient_terms_spanish_filler_only_means_silence():
    # A prompt made entirely of Spanish fillers is never a retrieval request.
    assert recall.salient_terms("dale por favor entonces") == []


def test_salient_terms_keep_accented_words_whole_and_normalized():
    # ASCII-only tokenization fragments accented words ("sesión" -> "sesi","n")
    # and the fragments can never match the FTS5 index, which keeps words whole
    # and strips diacritics (unicode61 remove_diacritics). Terms must come out
    # whole and diacritic-normalized so they align with what FTS5 indexed.
    terms = recall.salient_terms("Necesito arreglar la autenticación de la sesión")
    assert "autenticacion" in terms
    assert "sesion" in terms
    assert "sesi" not in terms and "autenticaci" not in terms


def test_salient_terms_accented_stopwords_drop_in_both_spellings():
    # Users type both "tambien" and "también" — normalization funnels both
    # into one stopword entry.
    assert recall.salient_terms("también entonces quizás dale") == []


# ---- #125: suggest — the proactive gate ----


def _seed_history(project="/repo/x"):
    """One old session with a distinctive high-importance item."""
    store.write_checkpoint(
        "S-old",
        _cp125("S-old", questions=[{
            "text": "LiteLLM gateway response cache pins identical bad responses",
            "trust": "verbatim", "quote": "cache answers instantly",
            "importance": 9, "first_seen": "2026-06-20T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir=project,
    )


def test_suggest_surfaces_prior_work(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-old"
    assert "cache" in out[0]["text"]


def test_suggest_excludes_current_session(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-old")
    assert out == []


def test_suggest_excludes_given_sessions(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-now",
                         exclude_sessions={"S-old"})
    assert out == []


def test_suggest_requires_two_token_overlap(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    # Only ONE salient term ("gateway") appears in the stored item — one shared
    # word is coincidence, not prior work. Noise budget: silence.
    out = recall.suggest("configuring nginx gateway timeouts for websockets",
                         project_dir="/repo/x", current_session="S-now")
    assert out == []


def test_suggest_multi_topic_prompt_matches_across_items(tmp_checkpoint_dir, monkeypatch):
    # Field miss (2026-07-02): a two-topic prompt splits its salient terms
    # across a session's items — no single ~200-char item shares >=2 terms even
    # when the session is plainly the prior work. Overlap must be session-level
    # distinct-term coverage, not per-item.
    store.write_checkpoint(
        "S-old",
        _cp125("S-old",
               decisions=[{"text": "Fold kumquat repo into daimon research arm",
                           "trust": "verbatim", "importance": 8,
                           "first_seen": "2026-06-20T00:00:00Z"}],
               questions=[{"text": "flamingo backlog fully mined, nothing left",
                           "trust": "inferred", "importance": 6,
                           "first_seen": "2026-06-20T00:00:00Z"}],
               created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("what did we obtain from the flamingo work and the kumquat work",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-old"


def test_suggest_one_term_across_many_items_still_silent(tmp_checkpoint_dir, monkeypatch):
    # Coverage counts DISTINCT terms, not matching items: the same single shared
    # word appearing in three items is still one word — coincidence, not prior
    # work. Noise budget holds at the session level too.
    store.write_checkpoint(
        "S-old",
        _cp125("S-old",
               decisions=[{"text": "gateway timeout raised", "trust": "inferred"},
                          {"text": "gateway retries added", "trust": "inferred"}],
               questions=[{"text": "gateway logs unclear", "trust": "inferred"}],
               created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("configuring nginx gateway websockets",
                         project_dir="/repo/x", current_session="S-now")
    assert out == []


def test_suggest_unknown_project_is_silent(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir=None, current_session="S-now")
    assert out == []


def test_suggest_short_prompt_is_silent(tmp_checkpoint_dir, monkeypatch):
    _seed_history()
    assert recall.suggest("ok", project_dir="/repo/x", current_session="S-now") == []


def test_suggest_includes_superseded_flagged_not_hidden(tmp_checkpoint_dir, monkeypatch):
    # Supersession v1 is whole-checkpoint per (author, project): PRIOR WORK is
    # superseded almost by definition. Hiding it would leave only the latest
    # checkpoint — which the briefing already covered. Flag, never hide (#112).
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    _seed_history()
    store.write_checkpoint(
        "S-newer", _cp125("S-newer", decisions=[{"text": "moved on to other work",
                                                 "trust": "inferred"}],
                          created="2026-06-25T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-old"
    assert out[0]["superseded_by"] == "S-newer"


def test_suggest_caps_at_two_distinct_sessions(tmp_checkpoint_dir, monkeypatch):
    for i, sid in enumerate(["S-a", "S-b", "S-c"]):
        _write_team_file(
            f"author{i}", sid,
            _cp125(sid, questions=[{
                "text": f"litellm gateway cache pinning variant {sid}",
                "trust": "inferred", "importance": 5,
                "first_seen": "2026-06-20T00:00:00Z",
            }], created="2026-06-20T00:00:00Z"),
            project_dir="/repo/x",
        )
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-now")
    assert 0 < len(out) <= 2
    assert len({o["session_id"] for o in out}) == len(out)
