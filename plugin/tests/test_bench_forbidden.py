"""Committed forbidden-hit cases (#405): material that must NOT surface.

Three suppression paths, each asserted against the ASSEMBLED BRIEF (top-k
withhold-filtered rows) rather than the raw retriever output — the leak that
matters is what reaches the prompt:

  (a) a FORGOTTEN item      — value-keyed forget tombstone (#402) drops it at
                              capture, so it never enters the index.
  (b) an OUT-OF-SCOPE item  — another project's checkpoint is never scoped into
                              this project's recall.
  (c) a TRUST-DOWNGRADED    — an item whose standing was downgraded (superseded
      item                    / resolved) is still RETURNED by recall (ranked
                              down, flagged) but WITHHELD from the delivered
                              brief; scoring the raw retriever output would be a
                              false leak.

All three run the real store + recall + assembly with zero model quota — the
serializer is never called; checkpoints are written directly and recall does the
lexical retrieval it does in production.
"""

from daimon_briefing import normalize, recall, store

from tests.bench import adapter, metrics


def _cp(active_topic: str, decisions=None, trust: str = "inferred") -> dict:
    """A minimal, schema-shaped checkpoint the store can write and recall index."""
    return {
        "working_context": {
            "active_topic": {"text": active_topic, "trust": trust},
            "open_questions": [],
            "recent_decisions": [{"text": d, "trust": trust} for d in (decisions or [])],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [], "uncertainties": [], "contradictions_flagged": [],
        },
        "worker_queue": [],
    }


def _brief_hits(results: list[dict], forbidden: list[str], k: int = 5):
    brief = metrics.assembled_brief_text(results, k)
    return metrics.forbidden_hits_found(brief, forbidden)


def test_case_a_forgotten_item_never_reaches_the_brief(tmp_path):
    env = adapter._question_env(tmp_path, "case_a", "2")
    proj = env["DAIMON_PROJECT_DIR"]
    query = "migration rollback plan"
    # non-PII so redaction is a no-op and the tombstone key matches the item text
    secret = "the acme_migration rollback plan for the cutover"
    with adapter._env(env):
        # tombstone the value BEFORE capture (value-keyed forget, #402)
        store.append_event("x-000000", f"forgotten:{normalize.content_key(secret)}",
                           project_dir=proj)
        # one benign item that DOES match the query + the forbidden one alongside it
        store.write_checkpoint(
            "a1", _cp("migration rollback runbook overview", decisions=[secret]),
            project_dir=proj)
        results = recall.search(query, project_dir=proj, limit=50)

    # capture worked (the benign item indexed) but the forgotten value is gone
    assert results, "expected the benign item to index"
    assert _brief_hits(results, [secret]) == []


def test_case_b_out_of_scope_project_item_never_reaches_the_brief(tmp_path):
    env = adapter._question_env(tmp_path, "case_b", "2")
    proj_a = env["DAIMON_PROJECT_DIR"]
    proj_b = str(tmp_path / "some_other_project")
    query = "database choice for reporting"
    forbidden = ["clientsecret_zeta database"]
    with adapter._env(env):
        store.write_checkpoint(
            "a1", _cp("we chose the database for reporting dashboards"),
            project_dir=proj_a)
        # a DIFFERENT project holds the forbidden secret, also query-matching
        store.write_checkpoint(
            "b1", _cp("clientsecret_zeta database choice for reporting"),
            project_dir=proj_b)
        scoped = recall.search(query, project_dir=proj_a, limit=50)
        cross = recall.search(query, all_projects=True, limit=50)

    # scoped recall never surfaces the other project's item
    assert _brief_hits(scoped, forbidden) == []
    # control: the item WOULD leak without project scoping — proves the guard,
    # not merely that the string was never indexed
    assert _brief_hits(cross, forbidden) == forbidden


def test_case_c_trust_downgraded_item_is_withheld_from_the_brief(tmp_path):
    env = adapter._question_env(tmp_path, "case_c", "2")
    proj = env["DAIMON_PROJECT_DIR"]
    query = "deployment target region"
    stale = "deployment target region is us-east-legacy"
    with adapter._env(env):
        # the stale claim rides a recent_decision (a list item — it gets a
        # stamped id; active_topic does not), alongside a live benign topic
        store.write_checkpoint(
            "c1", _cp("deployment notes overview", decisions=[stale]),
            project_dir=proj)
        # recover the stamped item id, then downgrade its standing (superseded)
        cp = store.read_latest_body(proj, route=store.Route.OWN_ELSE_GLOBAL,
                                    admit=store.Admit.ANY)
        item_id = cp["working_context"]["recent_decisions"][0]["id"]
        store.append_event(item_id, "superseded-by:d-newid1", project_dir=proj)
        results = recall.search(query, project_dir=proj, limit=50)

    # recall STILL returns the row (ranked down, flagged) — the raw retriever leaks
    assert any(r.get("superseded_by") for r in results), \
        "expected the downgraded item to still be retrieved"
    assert metrics.forbidden_hits_found(
        " ".join(str(r.get("text") or "") for r in results), [stale]) == [stale]
    # but the assembled brief WITHHOLDS it — no leak reaches the prompt
    assert _brief_hits(results, [stale]) == []
