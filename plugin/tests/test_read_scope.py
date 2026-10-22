"""One owner decides every read scope, and an unknown project reads nothing
(#1223).

`view.read_scope` returns a `Scope` (Own, Named, All or Denied). Before it,
`view.read_scopes` returned `None` for an unknown project, and each consumer
read that `None` its own way: `recall.query` searched every bucket,
`recall.find` and `recall.suggest` read nothing, and the team read, the audit
corpus, the session listings and the Hermes hook all widened. Every consumer
test here plants a FOREIGN bucket that matches, then asks as a project that
resolves to no slug, with tenant scope on and off."""
import inspect
import json
import typing

import pytest

from daimon_briefing import config, hooks, recall, store, view
from daimon_briefing.cli import audit
from daimon_briefing.surfaces import Writer

OTHER = "/repo/other-tenant"
MINE = "/repo/mine"
TERM = "walrusprobe"
BELIEF = f"the {TERM} gateway reverts every manual edit in the other tenant"
PROMPT = f"{TERM} gateway reverts every manual edit"


@pytest.fixture(params=[False, True], ids=["open", "tenant"])
def tenant(request, monkeypatch):
    if request.param:
        monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    return request.param


def _checkpoint(session):
    return {
        "session_id": session, "created": "2026-06-20T00:00:00Z",
        "working_context": {
            "active_topic": {"text": f"notes {session}", "trust": "inferred"},
            "open_questions": [{
                "text": BELIEF, "trust": "verbatim", "quote": BELIEF[:40],
                "importance": 9, "first_seen": "2026-06-20T00:00:00Z"}],
            "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


@pytest.fixture
def foreign(tmp_checkpoint_dir):
    """A matching checkpoint in a bucket that is not the asker's."""
    store.write_checkpoint("S-foreign", _checkpoint("S-foreign"),
                           project_dir=OTHER, writer=Writer.HUMAN)
    store.write_checkpoint("S-foreign2", _checkpoint("S-foreign2"),
                           project_dir=OTHER, writer=Writer.HUMAN)
    return store.project_slug(OTHER)


# ---- the owner -------------------------------------------------------------


def test_the_scope_cases_are_distinct_values_never_none():
    assert view.read_scope(MINE) != view.Denied()
    assert view.read_scope(None) == view.Denied()
    assert view.read_scope(None, all_projects=True) == view.All()
    assert view.read_scope(None, slug="-x") == view.Named("-x")


def test_an_unknown_project_is_denied_under_tenant_scope_too(tenant):
    assert view.read_scope(None) == view.Denied()
    expected = view.Denied() if tenant else view.All()
    assert view.read_scope(None, all_projects=True) == expected


def test_own_lists_the_asker_first_then_the_hosts_extras_once(monkeypatch):
    own = store.project_slug(MINE)
    monkeypatch.setenv("DAIMON_EXTRA_READ_SLUGS", f"b,{own},b,c")
    assert view.read_scope(MINE).slugs == (own, "b", "c")


def test_read_scopes_is_gone():
    assert not hasattr(view, "read_scopes")


# ---- every recall consumer reads nothing, and they agree -------------------


def test_the_three_recall_consumers_agree_on_an_unknown_project(
        foreign, tenant):
    found_id = recall.search(TERM, project_dir=OTHER)[0]["item_id"]
    assert found_id
    assert recall.search(TERM, project_dir=None) == []
    assert recall.query(TERM, project_dir=None).rows == []
    assert isinstance(recall.find(found_id, project_dir=None), recall.Absent)
    assert recall.lookup_item(found_id, project_dir=None) is None
    assert recall.suggest(PROMPT, project_dir=None) == []


def test_an_unknown_project_that_asks_for_all_projects_follows_the_policy(
        foreign, tenant):
    """The one honored widening: all_projects reads everything on an open
    install, and under tenant scope an unknown project still reads nothing."""
    rows = recall.search(TERM, project_dir=None, all_projects=True)
    if tenant:
        assert rows == []
    else:
        assert {r["project_slug"] for r in rows} == {foreign}


def test_a_denied_filter_matches_no_row_even_if_a_caller_forgets_to_stop():
    clause, params = recall._scope_filter(view.Denied())
    assert clause == " AND 0" and params == ()


# ---- the Hermes hook -------------------------------------------------------


def test_hermes_with_no_project_identity_gets_no_briefing(
        foreign, tenant, monkeypatch):
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    out = hooks.pre_llm_call(
        session_id="S-h", user_message="hi", conversation_history=[],
        is_first_turn=True, model="m", platform="cli")
    assert out is None


# ---- the team read ---------------------------------------------------------


def test_read_team_of_an_unknown_project_reads_nothing(
        tmp_checkpoint_dir, tenant):
    adir = config.team_dir() / "local" / "authors" / "grace"
    adir.mkdir(parents=True)
    (adir / "S1.json").write_text(json.dumps({
        "session_id": "S1", "created": "2026-10-01T00:00:00Z",
        "author": "grace", "project_slug": store.project_slug(OTHER),
        "working_context": {"recent_decisions": [
            {"text": BELIEF, "trust": "inferred"}]},
    }), encoding="utf-8")
    assert [a for a, _ in store.read_team(project_dir=OTHER)] == ["grace"]
    assert store.read_team(project_dir=None) == []


# ---- the audit corpus and the session listings -----------------------------


def test_the_audit_corpus_of_an_unknown_project_is_empty(foreign, tenant):
    assert list(audit._corpus(None, want_all=False)) == []
    if tenant:
        assert list(audit._corpus(None, want_all=True)) == []


def test_the_audit_corpus_of_an_open_install_all_is_every_bucket(foreign):
    ids = {sid for sid, _cp, _notes in audit._corpus(None, want_all=True)}
    assert {"S-foreign", "S-foreign2"} <= ids


def test_the_session_listings_of_an_unknown_project_are_empty(
        tmp_checkpoint_dir, tenant):
    """An unstamped session file belongs to no project, so a project that is
    nobody must not match it."""
    store.write_checkpoint("S-unstamped", _checkpoint("S-unstamped"),
                           project_dir=None, writer=Writer.HUMAN)
    store.write_checkpoint("S-foreign", _checkpoint("S-foreign"),
                           project_dir=OTHER, writer=Writer.HUMAN)
    assert view.sessions(None).rows == ()
    assert view.sessions(None).unreadable == 0
    assert view.open_sessions(None, ["S-unstamped", "S-foreign"],
                              live=False) == {}


# ---- no consumer takes a raw None scope -----------------------------------


def _scope_params():
    from daimon_briefing import briefing
    from daimon_briefing.cli import audit as audit_cli
    for module in (recall, view, store, briefing, audit_cli, hooks):
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if fn.__module__ != module.__name__:
                continue
            for pname in inspect.signature(fn).parameters:
                if pname in ("scope", "scopes"):
                    yield module.__name__, name, fn, pname


def test_every_scope_parameter_is_annotated_as_a_scope():
    seen = list(_scope_params())
    assert seen, "the census found no scope-taking function at all"
    for module, name, fn, pname in seen:
        hints = typing.get_type_hints(fn)
        assert hints.get(pname) == view.Scope, (
            f"{module}.{name}({pname}) must take a view.Scope, "
            f"not {hints.get(pname)!r}")


def test_no_scope_type_admits_none():
    assert type(None) not in typing.get_args(view.Scope)


# ---- the exhaustiveness hook and the branches only a scope can reach ------


class _NotAScope:
    pass


def test_a_scope_case_nobody_handles_raises_instead_of_widening(
        foreign, monkeypatch):
    recall.search(TERM, project_dir=OTHER)      # builds the index
    bogus = typing.cast(typing.Any, _NotAScope())
    with pytest.raises(AssertionError):
        view.unreachable_scope(bogus)
    with pytest.raises(AssertionError):
        recall._scope_filter(bogus)  
    with pytest.raises(AssertionError):
        recall._closed_still(bogus, config.recall_db(), [])
    monkeypatch.setattr(view, "read_scope",
                        lambda *a, **k: bogus if "all_projects" in k else view.Denied())
    with pytest.raises(AssertionError):
        list(audit._corpus(MINE, want_all=False))


def test_closed_still_narrows_to_a_named_scope_and_ignores_a_denied_one(
        tmp_checkpoint_dir):
    path = tmp_checkpoint_dir / "missing.db"     # no index: nothing is closed
    assert recall._closed_still(view.Named("-x"), path, []) is False
    assert recall._closed_still(view.Denied(), path, []) is False


def test_the_audit_corpus_of_a_named_scope_reads_that_bucket(
        foreign, monkeypatch):
    monkeypatch.setattr(view, "read_scope",
                        lambda *a, **k: (view.Named(foreign) if "all_projects" in k
                                          else view.Own(foreign)))
    ids = {sid for sid, _cp, _n in audit._corpus(MINE, want_all=False)}
    assert ids == {"S-foreign", "S-foreign2"}


def test_the_audit_corpus_of_a_known_project_is_its_own_bucket(foreign):
    ids = {sid for sid, _cp, _n in audit._corpus(OTHER, want_all=False)}
    assert ids == {"S-foreign", "S-foreign2"}
