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


def _write_nested_team_file(logical, author_slug, sid, cp, project_dir=None):
    """A #200 nested-era teammate blob: projects/<logical…>/authors/<slug>/."""
    d = (config.team_dir() / "local" / "projects").joinpath(
        *logical.split("/")) / "authors" / author_slug
    d.mkdir(parents=True, exist_ok=True)
    blob = dict(cp)
    blob.setdefault("author", author_slug)
    blob.setdefault("team_project", logical)
    if project_dir is not None:
        blob["project_slug"] = store.project_slug(project_dir)
    (d / f"{sid}.json").write_text(
        json.dumps(blob, ensure_ascii=False), encoding="utf-8"
    )


def test_rebuild_indexes_nested_team_items(tmp_checkpoint_dir, monkeypatch):
    # #200: blobs under projects/**/authors/* feed the index too.
    _write_nested_team_file(
        "core/cosmo/dusters/finance-1", "grace", "S-n",
        _cp("S-n", beliefs=[{"text": "Nested capybara ledgers reconcile",
                             "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    recall.rebuild()
    hits = recall.search("capybara", all_projects=True)
    assert len(hits) == 1
    assert hits[0]["author"] == "grace"
    # The project_slug stamp still scopes nested blobs to their project.
    scoped = recall.search("capybara", project_dir="/repo/x")
    assert len(scoped) == 1


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


# ---- supersession v3 (#234): item-level evidence flags, recency only ranks ----


def test_recency_alone_never_sets_the_superseded_flag(tmp_checkpoint_dir, monkeypatch):
    # v3 (#234): the old whole-checkpoint flag measured at coin-flip
    # precision. Mere recency now populates `frontier` (a silent rank
    # tiebreak) and NEVER superseded_by.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", decisions=[{"text": "narwhal decision v1", "trust": "inferred"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new", decisions=[{"text": "narwhal decision v2", "trust": "inferred"}],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("narwhal", all_projects=True)
    by_sid = {h["session_id"]: h for h in hits}
    assert by_sid["S-old"]["superseded_by"] is None
    assert by_sid["S-new"]["superseded_by"] is None
    assert by_sid["S-new"]["frontier"] == 1
    assert by_sid["S-old"]["frontier"] == 0
    # frontier tiebreak: the newest checkpoint's row edges out the older one
    assert hits[0]["session_id"] == "S-new"
    assert hits[-1]["session_id"] == "S-old"


def test_typed_link_text_target_sets_superseded_flag(tmp_checkpoint_dir, monkeypatch):
    # #234 tier 1: a supersedes link with a free-text target resolves to the
    # unique older same-kind item sharing >= 3 salient terms — that item (and
    # only that item) gets the flag.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old",
        decisions=[
            {"text": "adopt the pelican cache eviction strategy for briefings",
             "trust": "inferred"},
            {"text": "unrelated walrus formatting choice", "trust": "inferred"},
        ],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new",
        decisions=[{
            "text": "reversed: drop pelican cache eviction, it thrashed",
            "trust": "inferred",
            "links": [{"type": "supersedes",
                       "target": "pelican cache eviction strategy briefings"}],
        }],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("pelican OR walrus", all_projects=True, limit=10)
    by_text = {h["text"]: h for h in hits}
    assert by_text["adopt the pelican cache eviction strategy for briefings"][
        "superseded_by"] == "S-new"
    assert by_text["unrelated walrus formatting choice"]["superseded_by"] is None
    assert by_text["reversed: drop pelican cache eviction, it thrashed"][
        "superseded_by"] is None


def test_typed_link_ambiguous_text_target_never_guesses(tmp_checkpoint_dir, monkeypatch):
    # Two distinct older texts match the target -> nobody gets flagged
    # (a wrong supersession fabricates staleness; same bias as bind_links).
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old",
        decisions=[
            {"text": "toucan retry budget applies to gateway calls",
             "trust": "inferred"},
            {"text": "toucan retry budget applies to serializer calls",
             "trust": "inferred"},
        ],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new",
        decisions=[{
            "text": "dropped the toucan retry budget entirely",
            "trust": "inferred",
            "links": [{"type": "supersedes",
                       "target": "toucan retry budget applies"}],
        }],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("toucan", all_projects=True, limit=10)
    assert all(h["superseded_by"] is None for h in hits)


def test_typed_link_id_target_sets_superseded_flag(tmp_checkpoint_dir, monkeypatch):
    # A bound (id-shape) target marks the carrying rows directly.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    old = _cp("S-old", decisions=[{"text": "ibis pagination decision",
                                   "trust": "inferred", "id": "r-abc123"}],
              created="2021-01-01T00:00:00Z")
    store.write_checkpoint("S-old", old, project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new",
        decisions=[{
            "text": "replaced ibis pagination with cursors",
            "trust": "inferred",
            "links": [{"type": "supersedes", "target": "r-abc123"}],
        }],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("ibis", all_projects=True, limit=10)
    by_text = {h["text"]: h for h in hits}
    assert by_text["ibis pagination decision"]["superseded_by"] == "S-new"
    assert by_text["replaced ibis pagination with cursors"]["superseded_by"] is None


def test_event_resolution_sets_superseded_flag(tmp_checkpoint_dir, monkeypatch):
    # #234 tier 1b: a logged resolution marks the item; a strictly newer
    # reopen un-marks it (ties stay unmarked — never guess).
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", questions=[
            {"text": "should the gannet exporter batch writes",
             "trust": "inferred", "id": "o-111aaa"},
            {"text": "does the gannet importer need locks",
             "trust": "inferred", "id": "o-222bbb"},
        ],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    ev = config.checkpoint_dir() / slug / "events.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-111aaa", "status": "resolved", "source": "cli"}\n'
        '{"ts": "2026-01-01T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-222bbb", "status": "resolved", "source": "cli"}\n'
        '{"ts": "2026-01-02T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-222bbb", "status": "reopened", "source": "cli"}\n',
        encoding="utf-8")
    hits = recall.search("gannet", all_projects=True, limit=10)
    by_text = {h["text"]: h for h in hits}
    assert by_text["should the gannet exporter batch writes"][
        "superseded_by"] == "resolved"
    assert by_text["does the gannet importer need locks"]["superseded_by"] is None


def test_supersede_candidate_status_never_marks(tmp_checkpoint_dir, monkeypatch):
    # Candidates are the UNCONFIRMED tier (#111) — only a confirmed
    # resolution may flag.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", questions=[{"text": "heron cache warmup open question",
                             "trust": "inferred", "id": "o-333ccc"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    ev = config.checkpoint_dir() / slug / "events.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(
        '{"ts": "2026-01-01T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-333ccc", "status": "supersede-candidate:r-abc123",'
        ' "source": "serializer"}\n', encoding="utf-8")
    hits = recall.search("heron", all_projects=True, limit=10)
    assert hits and all(h["superseded_by"] is None for h in hits)


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


def test_suggest_surfaces_older_work_unflagged_without_evidence(
        tmp_checkpoint_dir, monkeypatch):
    # v3 (#234): prior work from an older checkpoint is NOT flagged by mere
    # recency — it surfaces clean. Only item-level evidence may flag (and
    # flagged items still surface, ranked down — flag, never hide, #112).
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
    assert out[0]["superseded_by"] is None


def test_suggest_flags_and_demotes_typed_superseded_item(
        tmp_checkpoint_dir, monkeypatch):
    # A typed supersedes link flags the old item in suggestions too —
    # included, demoted, never hidden (#112). Same-kind fixture: links bind
    # within a kind, mirroring carry.bind_links.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-old", _cp125("S-old", decisions=[{
            "text": "pin the litellm gateway cache for bad responses",
            "trust": "verbatim", "quote": "pin it",
            "importance": 9, "first_seen": "2026-06-20T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    store.write_checkpoint(
        "S-newer", _cp125("S-newer", decisions=[{
            "text": "unpinned the litellm gateway cache, old diagnosis wrong",
            "trust": "inferred",
            "links": [{"type": "supersedes",
                       "target": "pin litellm gateway cache bad responses"}],
        }], created="2026-06-25T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("debugging the litellm gateway cache pinning again",
                         project_dir="/repo/x", current_session="S-now")
    assert out
    flagged = [r for r in out if r["superseded_by"] == "S-newer"]
    assert flagged and flagged[0]["session_id"] == "S-old"


def test_suggest_matches_accented_spanish_content(tmp_checkpoint_dir, monkeypatch):
    # #27: salient_terms folds diacritics (sesión -> sesion) but the overlap
    # gate substring-tested folded terms against an UNFOLDED haystack, so a
    # session whose only shared terms are accented was silenced even though
    # FTS5 matched the row. Spanish-first content must surface like ASCII.
    store.write_checkpoint(
        "S-es",
        _cp125("S-es", decisions=[{
            "text": "Definimos la sesión de autenticación con tokens",
            "trust": "verbatim", "importance": 8,
            "first_seen": "2026-06-20T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("quiero revisar la sesión de autenticación otra vez",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-es"


def test_suggest_ascii_prompt_matches_accented_item(tmp_checkpoint_dir, monkeypatch):
    # Users drop accents when typing fast: "sesion" must still hit "sesión".
    store.write_checkpoint(
        "S-es",
        _cp125("S-es", decisions=[{
            "text": "Definimos la sesión de autenticación con tokens",
            "trust": "verbatim", "importance": 8,
            "first_seen": "2026-06-20T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("revisar la sesion de autenticacion otra vez",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-es"


def test_suggest_accented_prompt_matches_ascii_item(tmp_checkpoint_dir, monkeypatch):
    # Opposite direction: accented prompt, ASCII-stored item (folding already
    # handled this via salient_terms — pin it so it never regresses).
    store.write_checkpoint(
        "S-ascii",
        _cp125("S-ascii", decisions=[{
            "text": "Definimos la sesion de autenticacion con tokens",
            "trust": "verbatim", "importance": 8,
            "first_seen": "2026-06-20T00:00:00Z",
        }], created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x",
    )
    out = recall.suggest("quiero revisar la sesión de autenticación otra vez",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-ascii"


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


# ---- #28 S5: index errors leave a breadcrumb instead of failing to silence ----


def test_search_index_error_writes_breadcrumb(tmp_checkpoint_dir, monkeypatch):
    # A broken index degrades to [] (fail-open) — but silently, a broken
    # recall is indistinguishable from "no prior work". The swallow must
    # leave a trace status can surface (#28).
    from daimon_briefing import config, store
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1"), project_dir="/repo/x")

    def boom():
        raise OSError("disk full")
    monkeypatch.setattr(recall, "rebuild", boom)
    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    assert recall.search("something", all_projects=True) == []
    breadcrumb = config.log_dir() / "recall-error.log"
    assert breadcrumb.exists()
    assert "disk full" in breadcrumb.read_text(encoding="utf-8")


def test_suggest_db_error_writes_breadcrumb(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import config, store
    import sqlite3 as sq
    store.write_checkpoint(
        "S-old",
        _cp("S-old", decisions=[{"text": "litellm gateway cache pinning",
                                 "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    def bad_connect(*a, **k):
        raise sq.OperationalError("database is locked")
    monkeypatch.setattr(recall.sqlite3, "connect", bad_connect)
    out = recall.suggest("debugging the litellm gateway cache pinning",
                         project_dir="/repo/x", current_session="S-now")
    assert out == []
    breadcrumb = config.log_dir() / "recall-error.log"
    assert breadcrumb.exists()
    assert "database is locked" in breadcrumb.read_text(encoding="utf-8")


def test_search_happy_path_writes_no_breadcrumb(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import config, store
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "pelican decision", "trust": "inferred"}]), project_dir="/repo/x")
    assert recall.search("pelican", all_projects=True)
    assert not (config.log_dir() / "recall-error.log").exists()


# ---- #31 audit tail: suggest truncation, term cap, supersession edges -------


def test_suggest_survives_busy_project_candidate_overflow(tmp_checkpoint_dir, monkeypatch):
    # #31 item 4: the candidate query was LIMIT 64 with no ORDER BY — on a
    # busy project (>64 matching rows) the strongest rows could be arbitrarily
    # truncated away, silencing prior work. Best-ranked rows must survive.
    filler = [{"text": f"quorint filler ledger entry number {i} alpha",
               "trust": "inferred", "importance": 5,
               "first_seen": "2026-06-20T00:00:00Z"} for i in range(68)]
    strong = [{"text": "quorint zephyr reconciliation drops entries on pause",
               "trust": "verbatim", "importance": 8,
               "first_seen": "2026-06-20T00:00:00Z"},
              {"text": "zephyr quorint retry loop confirmed unresolved",
               "trust": "inferred", "importance": 7,
               "first_seen": "2026-06-20T00:00:00Z"}]
    store.write_checkpoint(
        "S-busy", _cp125("S-busy", questions=filler + strong,
                         created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x")
    recall.rebuild()
    out = recall.suggest("quorint zephyr reconciliation status",
                         project_dir="/repo/x", current_session="S-now")
    assert out, "strong rows were truncated out of the candidate window"
    assert "zephyr" in out[0]["text"]


def test_suggest_rich_prompt_terms_beyond_twelve_still_match(tmp_checkpoint_dir, monkeypatch):
    # #31 item 5: _TERM_CAP dropped prompt terms 13+ — a richer cue silenced
    # a match (encoding-specificity inversion). Terms past the old cap must
    # still retrieve.
    store.write_checkpoint(
        "S-old", _cp125("S-old", questions=[{
            "text": "quorint zephyr reconciliation pipeline unresolved",
            "trust": "inferred", "importance": 7,
            "first_seen": "2026-06-20T00:00:00Z"}],
            created="2026-06-20T00:00:00Z"),
        project_dir="/repo/x")
    recall.rebuild()
    junk = ("alpine bravado charlemagne dolomite ellipse foxglove gargoyle "
            "hyacinth ignition jamboree kaleidoscope labyrinth")  # 12 salient
    out = recall.suggest(f"{junk} quorint zephyr",
                         project_dir="/repo/x", current_session="S-now")
    assert out and out[0]["session_id"] == "S-old"


def test_unattributed_sessions_never_supersede_each_other(tmp_checkpoint_dir, monkeypatch):
    # #31 item 6: all NULL-slug (unattributed) sessions shared one supersession
    # bucket — the newest unattributed session superseded unrelated
    # unattributed projects. Unattributed sessions must not supersede at all.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-un-old", _cp125("S-un-old", questions=[{
            "text": "gargantuan refactor of the flotilla parser pending",
            "trust": "inferred"}], created="2026-06-01T00:00:00Z"))
    store.write_checkpoint(
        "S-un-new", _cp125("S-un-new", questions=[{
            "text": "totally unrelated kraken deployment question open",
            "trust": "inferred"}], created="2026-06-20T00:00:00Z"))
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = conn.execute(
            "SELECT DISTINCT superseded_by FROM items"
            " WHERE session_id = 'S-un-old'").fetchall()
    finally:
        conn.close()
    assert rows == [(None,)], f"unattributed session was superseded: {rows}"


def test_frontier_same_second_tie_breaks_deterministically(tmp_checkpoint_dir, monkeypatch):
    # #31 item 7 (v3: the winner is now `frontier`, not a flag): newest-per-
    # (author, slug) tie-broke by arbitrary scan order on same-second recency.
    # Ties must break on session_id (greater wins), stable across rebuilds.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    same = "2026-06-20T00:00:00Z"
    # S-aaa written FIRST: pre-fix, the first-scanned session won ties, so
    # this ordering makes the old nondeterminism pick the wrong winner.
    store.write_checkpoint(
        "S-aaa", _cp125("S-aaa", questions=[{
            "text": "aardvark index rebuild question", "trust": "inferred"}],
            created=same), project_dir="/repo/x")
    store.write_checkpoint(
        "S-zzz", _cp125("S-zzz", questions=[{
            "text": "zeppelin cache warmup question", "trust": "inferred"}],
            created=same), project_dir="/repo/x")
    for _ in range(2):  # stable across rebuilds
        recall.rebuild()
        conn = sqlite3.connect(str(config.recall_db()))
        try:
            old = conn.execute("SELECT DISTINCT frontier FROM items"
                               " WHERE session_id = 'S-aaa'").fetchall()
            new = conn.execute("SELECT DISTINCT frontier FROM items"
                               " WHERE session_id = 'S-zzz'").fetchall()
        finally:
            conn.close()
        assert old == [(0,)]
        assert new == [(1,)]


# ---- #233: index_attribution — dark-matter visibility, read-only ----


def test_index_attribution_counts_unattributed_items(tmp_checkpoint_dir):
    store.write_checkpoint(
        "S1", _cp("S1", decisions=[{"text": "keep sqlite", "trust": "inferred"}]),
        project_dir="/repo/x",
    )
    # A stampless, pointerless flat file — exactly the legacy shape that
    # indexes with project_slug NULL (rotated-out pre-stamp session).
    (config.checkpoint_dir() / "S2.json").write_text(
        json.dumps(_cp("S2", decisions=[{"text": "orphan decision",
                                         "trust": "inferred"}])),
        encoding="utf-8",
    )
    recall.rebuild()
    att = recall.index_attribution()
    assert att is not None
    assert att["items"] == 4          # 2 topics + 2 decisions
    assert att["unattributed"] == 2   # S2's topic + decision


def test_index_attribution_none_when_db_missing(tmp_checkpoint_dir):
    # Read-only contract: no db -> None, and importantly NO rebuild happens
    # (status must never pay the rebuild cost).
    assert not config.recall_db().exists()
    assert recall.index_attribution() is None
    assert not config.recall_db().exists()


def test_index_attribution_none_on_corrupt_db(tmp_checkpoint_dir):
    config.recall_db().parent.mkdir(parents=True, exist_ok=True)
    config.recall_db().write_bytes(b"not a sqlite file at all")
    assert recall.index_attribution() is None


# ---- #240: stamped checkpoints outrank stampless in the supersession frontier ----


def test_stamped_checkpoint_outranks_stampless_legacy_in_newest_map(
        tmp_checkpoint_dir, monkeypatch):
    """The #240 inversion: a legacy stampless file competes with its mtime —
    which records when the file was touched (migration, copy), not when the
    session happened — and outranks the REAL latest, flagging live context
    superseded by an older session. Stamped must always beat stampless."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-new",
        _cp("S-new", decisions=[{"text": "the real latest decision",
                                 "trust": "inferred"}],
            created="2026-06-19T20:52:44Z"),
        project_dir="/repo/x",
    )
    # Legacy pre-stamp file: attributed via embedded slug, no `created` —
    # recency falls back to file mtime, which is NOW (newer than S-new's stamp).
    legacy = _cp("S-legacy", decisions=[{"text": "an old legacy decision",
                                         "trust": "inferred"}])
    legacy["project_slug"] = store.project_slug("/repo/x")
    (config.checkpoint_dir() / "S-legacy.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    recall.rebuild()
    rows = {r["session_id"]: r["frontier"]
            for r in recall.search("decision", all_projects=True, limit=20)}
    assert rows["S-new"] == 1     # the stamped latest IS the frontier
    assert rows["S-legacy"] == 0  # the mtime-newer legacy file is not


def test_link_shared_floor_stays_in_sync_with_carry():
    # recall._MIN_LINK_SHARED mirrors carry._MIN_SHARED (import would be
    # circular) — rebuild-side text resolution must not drift looser or
    # stricter than carry-time binding.
    from daimon_briefing import carry
    assert recall._MIN_LINK_SHARED == carry._MIN_SHARED


def test_typed_link_stopword_target_never_matches(tmp_checkpoint_dir, monkeypatch):
    # A target with no salient terms can identify nothing — skipped outright.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", decisions=[{"text": "keep the flamingo exporter synchronous",
                             "trust": "inferred"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new", decisions=[{
            "text": "made the flamingo exporter async after all",
            "trust": "inferred",
            "links": [{"type": "supersedes", "target": "the one about it"}],
        }],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("flamingo", all_projects=True, limit=10)
    assert all(h["superseded_by"] is None for h in hits)


def test_typed_link_never_matches_own_carried_copy(tmp_checkpoint_dir, monkeypatch):
    # Self/twin guard: the superseding item's own carried copy in an older
    # checkpoint shares the target's vocabulary by construction — it must
    # never be treated as the thing being superseded.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    reversal = {
        "text": "dropped condor batching for the exporter queue",
        "trust": "inferred", "id": "r-fff999",
        "links": [{"type": "supersedes",
                   "target": "condor batching exporter queue"}],
    }
    # Older checkpoint holds the carried copy of the reversal itself — and
    # nothing else matching — so a match here could only be the self-twin.
    store.write_checkpoint("S-old", _cp(
        "S-old", decisions=[dict(reversal)],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    store.write_checkpoint("S-new", _cp(
        "S-new", decisions=[dict(reversal)],
        created="2025-01-01T00:00:00Z"), project_dir="/repo/x")
    hits = recall.search("condor", all_projects=True, limit=10)
    assert all(h["superseded_by"] is None for h in hits)


def test_event_fold_tolerates_hostile_lines(tmp_checkpoint_dir, monkeypatch):
    # The events fold must skip torn JSON, note-kind lines, and ref-less
    # resolutions without dropping the valid mark that follows them.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-old", _cp(
        "S-old", questions=[{"text": "does the skua poller need jitter",
                             "trust": "inferred", "id": "o-444ddd"}],
        created="2021-01-01T00:00:00Z"), project_dir="/repo/x")
    slug = store.project_slug("/repo/x")
    ev = config.checkpoint_dir() / slug / "events.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text(
        'not json at all{{{\n'
        '{"ts": "2026-01-01T00:00:00Z", "kind": "note", "note": "hi"}\n'
        '{"ts": "2026-01-01T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "", "status": "resolved"}\n'
        '{"kind": "resolution", "item_ref": "o-444ddd", "status": "resolved"}\n'
        '{"ts": "2026-01-02T00:00:00Z", "kind": "resolution",'
        ' "item_ref": "o-444ddd", "status": "superseded-by:r-abc123"}\n',
        encoding="utf-8")
    hits = recall.search("skua", all_projects=True, limit=10)
    assert hits and hits[0]["superseded_by"] == "r-abc123"


# ---- --slug: address a bucket by its real identity (#243) ----


def test_search_slug_scopes_without_a_path(tmp_checkpoint_dir, monkeypatch):
    _write_team_file("grace", "S-x", _cp("S-x", decisions=[
        {"text": "toucan decision in x", "trust": "inferred"}]),
        project_dir="/repo/x")
    _write_team_file("grace", "S-y", _cp("S-y", decisions=[
        {"text": "toucan decision in y", "trust": "inferred"}]),
        project_dir="/repo/y")
    hits = recall.search("toucan", slug=store.project_slug("/repo/y"))
    assert [h["text"] for h in hits] == ["toucan decision in y"]


def test_search_slug_wins_over_project_dir(tmp_checkpoint_dir, monkeypatch):
    # Callers guard the conflict at the CLI; the library keeps one rule: an
    # explicit slug IS the scope.
    _write_team_file("grace", "S-x", _cp("S-x", decisions=[
        {"text": "ibis decision in x", "trust": "inferred"}]),
        project_dir="/repo/x")
    hits = recall.search("ibis", project_dir="/repo/y",
                         slug=store.project_slug("/repo/x"))
    assert [h["text"] for h in hits] == ["ibis decision in x"]


# ---- #245: events.jsonl is index content, so it must be fingerprint input ----


def test_resolve_event_invalidates_index_without_manual_rebuild(tmp_checkpoint_dir, monkeypatch):
    cp = {"working_context": {"open_questions": [
        {"text": "walrus question pending", "trust": "inferred"}]}}
    store.write_checkpoint("S-fp", cp, project_dir="/repo/x")
    hits = recall.search("walrus", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] is None  # indexed live

    iid = store.read_latest(project_dir="/repo/x", fallback=False)[
        "working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/repo/x")

    # NO manual rebuild: the event append alone must stale the fingerprint
    hits = recall.search("walrus", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] == "resolved"


def test_reopen_event_revives_item_without_manual_rebuild(tmp_checkpoint_dir, monkeypatch):
    cp = {"working_context": {"open_questions": [
        {"text": "narwhal question pending", "trust": "inferred"}]}}
    store.write_checkpoint("S-fp2", cp, project_dir="/repo/x")
    iid = store.read_latest(project_dir="/repo/x", fallback=False)[
        "working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/repo/x")
    hits = recall.search("narwhal", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] == "resolved"

    store.append_event(iid, "reopened", project_dir="/repo/x")
    hits = recall.search("narwhal", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] is None


# ---- warm(): eager freshness at write time (#246) ----


def test_warm_rebuilds_stale_index_so_search_pays_nothing(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint("S-warm", _cp("S-warm", decisions=[
        {"text": "quokka decision", "trust": "inferred"}]), project_dir="/repo/x")
    calls = []
    real = recall.rebuild
    monkeypatch.setattr(recall, "rebuild", lambda: (calls.append(1), real())[1])
    recall.warm()
    assert calls == [1]  # stale after the write -> warm rebuilt
    hits = recall.search("quokka", project_dir="/repo/x")
    assert [h["text"] for h in hits] == ["quokka decision"]
    assert calls == [1]  # read side found it fresh: no second rebuild


def test_warm_is_noop_when_already_fresh(tmp_checkpoint_dir, monkeypatch):
    store.write_checkpoint("S-warm2", _cp("S-warm2", decisions=[
        {"text": "axolotl decision", "trust": "inferred"}]), project_dir="/repo/x")
    recall.warm()
    calls = []
    monkeypatch.setattr(recall, "rebuild", lambda: calls.append(1))
    recall.warm()
    assert calls == []


def test_warm_never_raises(tmp_checkpoint_dir, monkeypatch):
    def boom():
        raise RuntimeError("index exploded")
    monkeypatch.setattr(recall, "_ensure_fresh", boom)
    recall.warm()  # must swallow — a write must never fail over its index


def test_warm_swallows_fts5_missing(tmp_checkpoint_dir, monkeypatch):
    def no_fts5():
        raise recall.RecallError("no FTS5")
    monkeypatch.setattr(recall, "_ensure_fresh", no_fts5)
    recall.warm()


# ---- #255: ONE liveness rule — the index fold reuses store.is_resolved ----


def _one_question_bucket(text, sid, project="/repo/x"):
    cp = {"working_context": {"open_questions": [
        {"text": text, "trust": "inferred"}]}}
    store.write_checkpoint(sid, cp, project_dir=project)
    return store.read_latest(project_dir=project, fallback=False)[
        "working_context"]["open_questions"][0]["id"]


def test_free_form_resolving_status_marks_resolved(tmp_checkpoint_dir):
    # store.is_resolved: unknown statuses resolve ("the writer bothered to
    # record a lifecycle fact") — the --status help's own example must not
    # diverge between brief and recall
    iid = _one_question_bucket("wombat question pending", "S-lv1")
    store.append_event(iid, "shipped in 0.9", project_dir="/repo/x")
    hits = recall.search("wombat", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] == "resolved"


def test_reopen_prefix_status_revives(tmp_checkpoint_dir):
    # help text: "a status starting with 'reopen' revives the item"
    iid = _one_question_bucket("gecko question pending", "S-lv2")
    store.append_event(iid, "resolved", project_dir="/repo/x")
    store.append_event(iid, "reopen-was-wrong", project_dir="/repo/x")
    hits = recall.search("gecko", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] is None


def test_reopen_status_is_case_insensitive(tmp_checkpoint_dir):
    iid = _one_question_bucket("heron question pending", "S-lv3")
    store.append_event(iid, "resolved", project_dir="/repo/x")
    store.append_event(iid, "Reopened", project_dir="/repo/x")
    hits = recall.search("heron", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] is None


def test_superseded_by_status_still_carries_target_id(tmp_checkpoint_dir):
    iid = _one_question_bucket("osprey question pending", "S-lv4")
    store.append_event(iid, "superseded-by:o-9f3a2b", project_dir="/repo/x")
    hits = recall.search("osprey", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] == "o-9f3a2b"


def test_supersede_candidate_never_marks(tmp_checkpoint_dir):
    # unconfirmed tier by design — a machine guess must never suppress
    iid = _one_question_bucket("bittern question pending", "S-lv5")
    store.append_event(iid, "supersede-candidate:o-9f3a2b",
                       source="serializer", project_dir="/repo/x")
    hits = recall.search("bittern", project_dir="/repo/x")
    assert hits and hits[0]["superseded_by"] is None


# ---- #288: the same carried item must not surface once per checkpoint ----


def test_search_dedupes_same_item_across_checkpoints(tmp_checkpoint_dir, monkeypatch):
    # A carried item lives in every checkpoint that carries it; recall must
    # return it once, sourced from the newest checkpoint (its supersession /
    # frontier state is the current one).
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    item = {"text": "Quokka panel unregister fix shipped", "trust": "verbatim",
            "quote": "the quokka panel fix is in"}
    store.write_checkpoint("S1", _cp("S1", decisions=[dict(item)]),
                           project_dir="/repo/x")
    store.write_checkpoint("S2", _cp("S2", decisions=[dict(item)]),
                           project_dir="/repo/x")
    hits = recall.search("quokka", all_projects=True)
    assert len(hits) == 1
    assert hits[0]["session_id"] == "S2"  # newest occurrence wins


def test_search_dedupe_keeps_distinct_items_sharing_words(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": "Wombat cache invalidation uses hashes", "trust": "inferred"},
        {"text": "Wombat cache warming happens at write", "trust": "inferred"},
    ]), project_dir="/repo/x")
    hits = recall.search("wombat cache", all_projects=True)
    assert len(hits) == 2  # different content, no merge


def test_search_dedupe_preserves_distinct_authors(tmp_checkpoint_dir, monkeypatch):
    # Two humans stating the same thing is attribution, not duplication.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    text = "Axolotl deploys are frozen on Fridays"
    store.write_checkpoint("S1", _cp("S1", decisions=[
        {"text": text, "trust": "inferred"}]), project_dir="/repo/x")
    _write_team_file("grace", "S-g", _cp("S-g", decisions=[
        {"text": text, "trust": "inferred"}]))
    hits = recall.search("axolotl", all_projects=True)
    assert len(hits) == 2
    assert {h["author"] for h in hits} == {"ada", "grace"}


def test_search_dedupe_backfills_to_limit(tmp_checkpoint_dir, monkeypatch):
    # Dedupe must not under-fill: with limit=2, one duplicated item plus two
    # distinct ones still yields 2 DISTINCT results, not a dup-padded list.
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    dup = {"text": "Numbat retries use exponential backoff", "trust": "inferred"}
    store.write_checkpoint("S1", _cp("S1", decisions=[
        dict(dup),
        {"text": "Numbat retries cap at five attempts", "trust": "inferred"},
    ]), project_dir="/repo/x")
    store.write_checkpoint("S2", _cp("S2", decisions=[dict(dup)]),
                           project_dir="/repo/x")
    hits = recall.search("numbat retries", all_projects=True, limit=2)
    assert len(hits) == 2
    assert len({h["text"] for h in hits}) == 2


def test_dedupe_rows_replace_branch_newest_arrives_later():
    # The newest copy can sort BELOW an older one (superseded-last ordering
    # demotes a superseded newest copy) — the survivor must still be the
    # newest row's content, sitting at the older row's (better) position.
    older = {"kind": "decision", "author": "ada", "text": "gecko retry policy",
             "created": 100.0, "session_id": "S-old"}
    newer = {"kind": "decision", "author": "ada", "text": "gecko retry policy",
             "created": 200.0, "session_id": "S-new"}
    other = {"kind": "decision", "author": "ada", "text": "gecko cache policy",
             "created": 150.0, "session_id": "S-mid"}
    out = recall._dedupe_rows([older, other, newer], want_n=10)
    assert [r["session_id"] for r in out] == ["S-new", "S-mid"]  # position kept


def test_dedupe_rows_older_duplicate_is_skipped():
    newer = {"kind": "decision", "author": "ada", "text": "gecko retry policy",
             "created": 200.0, "session_id": "S-new"}
    older = {"kind": "decision", "author": "ada", "text": "gecko retry policy",
             "created": 100.0, "session_id": "S-old"}
    out = recall._dedupe_rows([newer, older], want_n=10)
    assert [r["session_id"] for r in out] == ["S-new"]


def test_dedupe_rows_missing_created_treated_as_oldest():
    stamped = {"kind": "q", "author": "a", "text": "t", "created": 1.0,
               "session_id": "S-stamped"}
    unstamped = {"kind": "q", "author": "a", "text": "t",
                 "session_id": "S-unstamped"}
    out = recall._dedupe_rows([unstamped, stamped], want_n=10)
    assert out[0]["session_id"] == "S-stamped"


# ---- #317: scene traces indexed for retrieval ----


def test_rebuild_indexes_scene_text(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1",
        _cp("S1", decisions=[{"text": "Adopt sqlite for the recall index",
                              "trust": "inferred",
                              "scene": "chosen after the flatfile scan grew quadratic"}]),
        project_dir="/repo/x",
    )
    recall.rebuild()
    # "quadratic" appears ONLY in the scene — a hit proves scene is FTS-indexed
    hits = recall.search("quadratic", all_projects=True)
    assert any("recall index" in h["text"] for h in hits)


def test_schema_version_bumped_for_scene_column():
    # #317 added a scene column to items/items_fts — an old db must be discarded,
    # not queried with the new column list
    assert int(recall._SCHEMA_VERSION) >= 4
