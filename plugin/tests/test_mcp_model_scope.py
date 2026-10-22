"""On the model surface the host, never the caller, chooses a read scope
(#1223 PR 2).

Every MCP tool takes its selector arguments (`slug`, `all_projects`,
`project`) through ONE decision, `view.model_read`. A request that leaves the
server's own bucket is refused out loud unless the host set
DAIMON_MODEL_CROSS_PROJECT; under DAIMON_TENANT_SCOPED the grant has no
effect. Each test plants a FOREIGN bucket with content that matches, then
runs every tool and every selector with the grant off and on, tenant off and
on, so a new selector that skips the decision fails here."""
import json

import pytest

from daimon_briefing import (briefing, config, mcp_server, mcp_tools,
                             recall, requests, store, view)
from daimon_briefing.surfaces import Writer

MINE = "/repo/mine"
OTHER = "/repo/other-tenant"
THIRD = "/repo/third-asker"
TERM = "walrusprobe"
MARK = f"the {TERM} gateway reverts every manual edit"
OWN_SLUG = store.project_slug(MINE)
OTHER_SLUG = store.project_slug(OTHER)
GRANT_REFUSAL = "DAIMON_MODEL_CROSS_PROJECT"


def _checkpoint(session, text):
    return {
        "session_id": session, "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "active_topic": {"text": text, "trust": "inferred"},
            "recent_decisions": [{"text": text, "trust": "inferred"}]},
        "epistemic_snapshot": {}}


@pytest.fixture
def world(tmp_checkpoint_dir, monkeypatch):
    """Own bucket and a foreign one that matches TERM; the server runs in
    MINE. The foreign bucket also holds a request, so `requests_inbox` for it
    is not vacuous."""
    store.write_checkpoint("S-mine", _checkpoint("S-mine", "own plain notes"),
                           project_dir=MINE, writer=Writer.HUMAN)
    store.write_checkpoint("S-other", _checkpoint("S-other", MARK),
                           project_dir=OTHER, writer=Writer.HUMAN)
    store.write_checkpoint("S-third", _checkpoint("S-third", "asker notes"),
                           project_dir=THIRD, writer=Writer.HUMAN)
    requests.open_request(to=OTHER_SLUG, ask="foreign ask", why="because",
                          channel="cli-agent", project_dir=THIRD)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", MINE)
    recall.rebuild()


@pytest.fixture(params=[False, True], ids=["no-grant", "grant"])
def grant(request, monkeypatch):
    if request.param:
        monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    return request.param


@pytest.fixture(params=[False, True], ids=["open", "tenant"])
def tenant(request, monkeypatch):
    if request.param:
        monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    return request.param


def _call(name, arguments):
    got = mcp_server._handle_tools_call({"name": name,
                                         "arguments": arguments})
    return got["content"][0]["text"], got["isError"]


# Every selector that leaves the own bucket, per tool, with a marker that
# only the foreign bucket's answer carries.
FOREIGN = [
    ("daimon_recall", {"query": TERM, "slug": OTHER_SLUG}, TERM),
    ("daimon_recall", {"query": TERM, "all_projects": True}, TERM),
    ("daimon_brief", {"slug": OTHER_SLUG}, TERM),
    ("daimon_brief", {"project": OTHER}, TERM),
    ("daimon_status", {"project": OTHER}, OTHER_SLUG),
    ("requests_inbox", {"project": OTHER}, "foreign ask"),
]

OWN = [
    ("daimon_recall", {"query": "plain"}),
    ("daimon_recall", {"query": "plain", "slug": OWN_SLUG}),
    ("daimon_brief", {}),
    ("daimon_brief", {"project": MINE}),
    ("daimon_brief", {"slug": OWN_SLUG}),
    ("daimon_status", {}),
    ("daimon_status", {"project": MINE}),
    ("requests_inbox", {}),
    ("requests_inbox", {"project": MINE}),
    ("daimon_projects", {}),
]


@pytest.mark.parametrize("tool,args,marker", FOREIGN,
                         ids=[f"{t}-{'+'.join(a)}" for t, a, _ in FOREIGN])
def test_a_foreign_selector_needs_the_host_grant(world, grant, tenant, tool,
                                                 args, marker):
    text, is_err = _call(tool, args)
    if grant and not tenant:
        assert is_err is False
        assert marker in text      # not vacuous: the foreign answer is there
    else:
        assert is_err is True
        assert marker not in text
        assert (config.TENANT_SCOPE_REFUSAL if tenant
                else config.MODEL_SCOPE_REFUSAL) == text
        if not tenant:
            assert GRANT_REFUSAL in text


@pytest.mark.parametrize("tool,args", OWN,
                         ids=[f"{t}-{'+'.join(a) or 'bare'}" for t, a in OWN])
def test_a_request_inside_the_own_bucket_is_never_refused(world, grant,
                                                          tenant, tool, args):
    _, is_err = _call(tool, args)
    assert is_err is False


def test_the_project_path_bypass_returns_nothing_without_the_grant(world):
    """daimon_brief {"project": <other dir>} used to return another project's
    briefing on a default install and under tenant scope."""
    text, is_err = _call("daimon_brief", {"project": OTHER})
    assert is_err is True
    assert TERM not in text


def test_projects_lists_only_the_own_bucket_without_the_grant(world, grant,
                                                              tenant):
    text, is_err = _call("daimon_projects", {})
    assert is_err is False
    slugs = {r["slug"] for r in json.loads(text)}
    if grant and not tenant:
        assert slugs == {OWN_SLUG, OTHER_SLUG, store.project_slug(THIRD)}
    else:
        assert slugs == {OWN_SLUG}


def test_the_grant_is_read_from_the_env_file_like_every_flag(
        world, tmp_path, monkeypatch):
    env = tmp_path / "env"
    env.write_text("DAIMON_MODEL_CROSS_PROJECT=1\n", encoding="utf-8")
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env))
    _, is_err = _call("daimon_brief", {"project": OTHER})
    assert is_err is False


@pytest.mark.parametrize("value", ["0", "", "no", "off", "2"])
def test_only_a_truthy_value_grants(world, monkeypatch, value):
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", value)
    _, is_err = _call("daimon_brief", {"project": OTHER})
    assert is_err is True


def test_the_conflict_guards_still_answer_first(world, grant):
    _, is_err = _call("daimon_recall", {"query": "x", "slug": OTHER_SLUG,
                                        "all_projects": True})
    assert is_err is True
    text, is_err = _call("daimon_brief", {"slug": OTHER_SLUG,
                                          "project": OTHER})
    assert is_err is True and "pass one" in text


def test_the_decision_is_one_function_with_a_typed_answer(world, monkeypatch):
    read = view.model_read(project=MINE)
    assert read.scope == view.Own(OWN_SLUG, ())
    with pytest.raises(view.ScopeRefused) as refused:
        view.model_read(slug=OTHER_SLUG)
    assert str(refused.value) == config.MODEL_SCOPE_REFUSAL
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    assert view.model_read(slug=OTHER_SLUG).scope == view.Named(OTHER_SLUG)
    assert view.model_read(all_projects=True).scope == view.All()
    got = view.model_read(project=OTHER)
    assert got.scope == view.Own(OTHER_SLUG, ())
    assert got.project == store.project_slug(OTHER) or got.project.endswith(
        "other-tenant")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    with pytest.raises(view.ScopeRefused) as refused:
        view.model_read(all_projects=True)
    assert str(refused.value) == config.TENANT_SCOPE_REFUSAL


# ---- the structural guard: a new selector cannot skip the decision --------

NON_SELECTORS = {"query", "limit", "session"}
FOREIGN_VALUE = {"slug": OTHER_SLUG, "all_projects": True, "project": OTHER}


def _declared(monkeypatch):
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    return mcp_server._tool_descriptors()


def test_every_declared_argument_is_a_classified_selector_or_not(
        world, monkeypatch):
    for tool in _declared(monkeypatch):
        props = set(tool["inputSchema"].get("properties", {}))
        unclassified = props - NON_SELECTORS - set(view.MODEL_SELECTORS)
        assert not unclassified, (
            f"{tool['name']} declares {sorted(unclassified)}: name it in "
            "NON_SELECTORS here if it cannot address a bucket, or add it to "
            "view.MODEL_SELECTORS so it goes through view.model_read")


def test_every_declared_selector_is_refused_without_the_grant(world,
                                                              monkeypatch):
    seen = set()
    for tool in _declared(monkeypatch):
        for prop in tool["inputSchema"].get("properties", {}):
            if prop not in view.MODEL_SELECTORS:
                continue
            seen.add((tool["name"], prop))
    assert seen, "no tool declares a selector: the walk is vacuous"
    monkeypatch.delenv("DAIMON_MODEL_CROSS_PROJECT")
    for name, prop in sorted(seen):
        args = {prop: FOREIGN_VALUE[prop], "query": TERM}
        text, is_err = _call(name, args)
        assert is_err is True, (name, prop)
        assert GRANT_REFUSAL in text, (name, prop)


def test_every_handler_has_a_descriptor_and_the_other_way_round(world,
                                                                monkeypatch):
    names = {t["name"] for t in _declared(monkeypatch)}
    assert names == set(mcp_tools.HANDLERS)


def test_selectors_are_taken_from_the_arguments_only_by_the_decision(
        world, monkeypatch):
    """A handler that reads a selector itself would answer a foreign value
    without asking the decision. Every selector in every handler's reach goes
    through `mcp_tools._read`, which calls `view.model_read` once."""
    calls = []
    real = view.model_read

    def spy(**kw):
        calls.append(kw)
        return real(**kw)

    monkeypatch.setattr(view, "model_read", spy)
    for name, args in OWN:
        calls.clear()
        _call(name, args)
        if name != "daimon_projects":
            assert len(calls) == 1, name


# ---- the teaching text offers cross-project reads only with the grant -----


def _described(monkeypatch, granted):
    if granted:
        monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    return {t["name"]: t for t in mcp_server._tool_descriptors()}


def test_without_the_grant_no_tool_offers_a_selector(monkeypatch):
    for name, tool in _described(monkeypatch, False).items():
        props = set(tool["inputSchema"].get("properties", {}))
        assert not props & set(view.MODEL_SELECTORS), name
        text = tool["description"]
        for word in ("all_projects", "pass slug", "a slug", "by slug"):
            assert word not in text, (name, word)


def test_with_the_grant_the_selectors_and_their_words_are_offered(
        monkeypatch):
    tools = _described(monkeypatch, True)
    assert {"slug", "all_projects"} <= set(
        tools["daimon_recall"]["inputSchema"]["properties"])
    assert {"slug", "project"} <= set(
        tools["daimon_brief"]["inputSchema"]["properties"])
    assert "project" in tools["daimon_status"]["inputSchema"]["properties"]
    assert "slug" in tools["daimon_projects"]["description"]


def test_tenant_scope_withholds_the_selectors_even_with_the_grant(
        monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    for name, tool in _described(monkeypatch, True).items():
        assert not set(tool["inputSchema"].get("properties", {})) & set(
            view.MODEL_SELECTORS), name


@pytest.mark.parametrize("tenant_on", [False, True], ids=["open", "tenant"])
def test_the_no_checkpoint_hint_names_daimon_projects_only_with_the_grant(
        tmp_checkpoint_dir, monkeypatch, tenant_on):
    store.write_checkpoint("S-b", _checkpoint("S-b", "elsewhere"),
                           project_dir=OTHER, writer=Writer.HUMAN)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", MINE)
    if tenant_on:
        monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    off = briefing.machine_brief(MINE, 0.0, loops_pointer=False).text
    assert "daimon_projects" not in off
    assert "1 project(s)" not in off
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    on = briefing.machine_brief(MINE, 0.0, loops_pointer=False).text
    assert ("daimon_projects" in on) is (not tenant_on)
