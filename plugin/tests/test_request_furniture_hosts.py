"""Every briefing host shows the request furniture, by construction (#1402).

The request panels, the decision count, the version note and the withheld
trailer were built and printed by one host (`daimon brief`), so the MCP tool,
`api.render_briefing` and the Hermes hook never showed them: an agent briefed
through them never saw a request addressed to its project. `briefing.prepare`
now builds them once and `briefing.compose` lays them out for every plain
host, so the bytes are the same by construction and these tests hold each host
to it. Same shape as `test_handoff_hosts` (the baton had the same bug).

Stamps (`surfaced`) are committed at each entry's delivery boundary from the
manifest of cards printed whole: the CLI after the flush, MCP after the
JSON-RPC response is written, `api.render_briefing` at its return, Hermes at
the hook's return."""
import io
import json

import pytest

from daimon_briefing import (api, briefing, cli, clock, config, hooks,
                             host_mcp_caps, marks, mcp_server, mcp_tools,
                             requests, store)
from tests import _furniture_seed as seed

HEADERS = ("Requests waiting on you (from other projects):",
           "Decisions on requests you sent:",
           "Requests you accepted and still owe:",
           "Requests you sent that nobody is moving:")
COUNT_PREFIX = "1 decision waiting on you here"
VERSION = "⚠ checkpoint format 0.0.1 != current"
NOW = (seed.T0 + 10 * seed.DAY) / 10 ** 9


def _env(monkeypatch, project=seed.PROJECT):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)


def _cli(monkeypatch, capsys):
    _env(monkeypatch)
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


def _serve(monkeypatch, arguments, *, out=None):
    """One `daimon_brief` call through the real server loop, so the delivery
    boundary (the response written) is the real one."""
    _env(monkeypatch)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "daimon_brief", "arguments": arguments}}
    out = out if out is not None else io.StringIO()
    mcp_server.serve(io.StringIO(json.dumps(call) + "\n"), out)
    reply = json.loads(out.getvalue())
    return reply["result"]["content"][0]["text"]


def _mcp(monkeypatch, capsys):
    return _serve(monkeypatch, {"project": seed.PROJECT})


def _api(monkeypatch, capsys):
    return api.render_briefing(seed.PROJECT, now=NOW).text


def _hermes(monkeypatch, capsys):
    _env(monkeypatch)
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    return (out or {}).get("context", "")


HOSTS = [_cli, _mcp, _api, _hermes]
IDS = ["cli", "mcp", "api", "hermes"]


def _state(name, tmp_checkpoint_dir):
    if name == "briefing":
        return seed.seed()
    if name == "no-checkpoint":
        return seed.seed(checkpoint_for_project=False, buckets=False)
    if name == "empty":
        return seed.seed(buckets=False, trailer=False, decisions=(),
                         loops=())
    ids = seed.seed(trailer=False)
    bucket = tmp_checkpoint_dir / store.project_slug(seed.PROJECT)
    (bucket / "trust.jsonl").write_bytes(b"<<<<<<< HEAD\n")
    return ids


STATES = ["briefing", "no-checkpoint", "closed", "empty"]


def _stamped(project=seed.PROJECT):
    return sorted((r["event"], r["request_id"])
                  for r in requests.events(project_dir=project)
                  if r.get("event") in ("surfaced", "verdict_surfaced"))


# ---- 1 + 2: every host prints the same blocks, in every state -------------

@pytest.mark.parametrize("host", HOSTS, ids=IDS)
@pytest.mark.parametrize("state", STATES)
def test_every_host_prints_the_count_and_the_four_panels(
        tmp_checkpoint_dir, monkeypatch, capsys, host, state):
    ids = _state(state, tmp_checkpoint_dir)
    text = seed.normalize(host(monkeypatch, capsys), ids)
    assert COUNT_PREFIX in text
    for header in HEADERS:
        assert header in text
    for role in ("request", "verdict", "owed", "overdue"):
        assert f"q-{role}" in text


@pytest.mark.parametrize("state", STATES)
def test_the_panel_blocks_are_byte_identical_across_the_hosts(
        tmp_checkpoint_dir, monkeypatch, capsys, state):
    ids = _state(state, tmp_checkpoint_dir)
    texts = [seed.normalize(h(monkeypatch, capsys), ids) for h in HOSTS]
    for header in HEADERS:
        blocks = [seed.block(t, header) for t in texts]
        assert blocks[0] == blocks[1] == blocks[2] == blocks[3]
    counts = [[ln for ln in t.splitlines() if ln.startswith(COUNT_PREFIX)]
              for t in texts]
    assert counts[0] and counts[0] == counts[1] == counts[2] == counts[3]


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_the_version_note_and_the_withheld_trailer_ride_every_host(
        tmp_checkpoint_dir, monkeypatch, capsys, host):
    _state("briefing", tmp_checkpoint_dir)
    text = host(monkeypatch, capsys)
    assert VERSION in text
    assert "1 resolved item(s) withheld — `daimon status --suppressed`" in text
    assert "1 quarantined item(s) withheld — `daimon trust list`" in text


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_the_baton_and_the_rulings_still_lead_with_the_furniture(
        tmp_checkpoint_dir, monkeypatch, capsys, host):
    _state("briefing", tmp_checkpoint_dir)
    api.write_handoff(seed.PROJECT, "pick up the migration")
    text = host(monkeypatch, capsys)
    assert text.startswith(marks.HANDOFF_LEAD)
    assert "→ pick up the migration" in text
    assert text.index(HEADERS[0]) > text.index("→ pick up the migration")


# ---- 3: they parse to the same fields -------------------------------------

@pytest.mark.parametrize("state", STATES)
def test_the_hosts_parse_to_equal_furniture_fields(
        tmp_checkpoint_dir, monkeypatch, capsys, state):
    ids = _state(state, tmp_checkpoint_dir)
    parsed = [api.parse_briefing(h(monkeypatch, capsys)) for h in HOSTS]
    for got in parsed:
        assert [c.request_id for c in got.requests.cards] == [ids["request"]]
        assert [c.request_id for c in got.verdicts.cards] == [ids["verdict"]]
        assert [c.request_id for c in got.owed.cards] == [ids["owed"]]
        assert [c.request_id for c in got.overdue.cards] == [ids["overdue"]]
        assert got.decisions == api.DecisionCount(here=1, elsewhere=1)
    assert len({(g.decisions, g.withheld) for g in parsed}) == 1
    if state == "briefing":
        for got in parsed:
            assert any("checkpoint format" in w for w in got.warnings)
            assert got.withheld == api.Withheld(resolved=1, quarantined=1)


# ---- 4: whose ledger the panels read (owner decision, Q1 and Q2) ----------

def test_a_slug_read_shows_nothing_and_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed()
    slug = store.project_slug(seed.PROJECT)
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    for text in (_serve(monkeypatch, {"slug": slug}),):
        assert not any(h in text for h in HEADERS)
        assert COUNT_PREFIX not in text
    _env(monkeypatch)
    assert cli.main(["brief", "--slug", slug]) == 0
    out = capsys.readouterr().out
    assert not any(h in out for h in HEADERS) and COUNT_PREFIX not in out
    assert _stamped() == []
    assert ids


def test_a_foreign_path_read_shows_and_stamps_no_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """`daimon_brief(project=<foreign>)` under the cross-project grant reads
    another project's briefing; its agent never saw its requests, so the
    reader's call must not show them, and must not stamp them as seen."""
    seed.seed()
    seed.write_cp(seed.SENDER)
    foreign = requests.open_request(
        to=store.project_slug(seed.SENDER), ask="a foreign project's ask",
        why="needed", channel="cli-agent",
        project_dir=store.project_slug(seed.OTHER))
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")
    text = _serve(monkeypatch, {"project": seed.SENDER})
    assert "a foreign project's ask" not in text
    assert not any(h in text for h in HEADERS)
    assert foreign not in {rid for _, rid in _stamped(seed.SENDER)}
    assert _stamped(seed.SENDER) == []


@pytest.mark.parametrize("flag", ["--global-fallback"])
def test_a_fresh_project_shows_its_own_panels_under_a_global_fallback(
        tmp_checkpoint_dir, monkeypatch, capsys, flag):
    ids = seed.seed(checkpoint_for_project=False)
    _env(monkeypatch)
    assert cli.main(["brief", flag]) == 0
    out = seed.normalize(capsys.readouterr().out, ids)
    assert "→ q-request  publish the schema" in out
    assert _stamped() == sorted([("surfaced", ids["request"]),
                                 ("verdict_surfaced", ids["verdict"])])


def test_hermes_with_a_global_fallback_shows_the_projects_own_panels(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed(checkpoint_for_project=False)
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    text = seed.normalize(_hermes(monkeypatch, capsys), ids)
    assert "→ q-request  publish the schema" in text


# ---- 7: a stamp is committed once per delivery ----------------------------

@pytest.mark.parametrize("host", HOSTS, ids=IDS)
@pytest.mark.parametrize("state", ["briefing", "no-checkpoint"])
def test_a_delivery_stamps_each_card_once_and_changes_no_byte_of_the_next(
        tmp_checkpoint_dir, monkeypatch, capsys, host, state):
    ids = _state(state, tmp_checkpoint_dir)
    assert _stamped() == []
    first = host(monkeypatch, capsys)
    assert _stamped() == sorted([("surfaced", ids["request"]),
                                 ("verdict_surfaced", ids["verdict"])])
    second = host(monkeypatch, capsys)
    assert len(_stamped()) == 2
    for header in HEADERS:
        assert seed.block(seed.normalize(first, ids), header) == \
            seed.block(seed.normalize(second, ids), header)


# ---- 8: no stamp without a delivery ---------------------------------------

def test_a_read_that_delivers_nothing_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    ids = seed.seed()
    got = api.preview_briefing(seed.PROJECT, now=NOW)
    assert ids["request"] in got.text and got.printed.request
    briefing.machine_brief(seed.PROJECT, NOW, loops_pointer=False)
    api.parse_briefing(got.text)
    _env(monkeypatch)
    mcp_tools.HANDLERS["daimon_brief"]({"project": seed.PROJECT})
    assert _stamped() == []


def test_a_response_that_could_not_be_written_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch):
    seed.seed()

    class Broken(io.StringIO):
        def write(self, s):
            raise BrokenPipeError("the host went away")

    with pytest.raises(BrokenPipeError):
        _serve(monkeypatch, {"project": seed.PROJECT}, out=Broken())
    assert _stamped() == []


# ---- 9: a collapsed panel is not a delivered card -------------------------

@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_a_panel_the_budget_collapsed_stamps_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys, host):
    seed.seed()
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "900")
    text = host(monkeypatch, capsys)
    got = api.parse_briefing(text)
    assert got.requests is not None and got.requests.cut == 1
    assert got.requests.cards == ()
    assert _stamped() == []
    assert len(text.encode()) <= 900


# ---- 10: the builders have one call site ----------------------------------

def test_the_composer_and_the_stamps_have_the_call_sites_the_design_names():
    import ast
    from pathlib import Path
    pkg = Path(store.__file__).parent
    want = {"version_note": {("briefing.py", "prepare")},
            "withheld_trailer": {("briefing.py", "compose"),
                                 ("brief.py", "_render_briefing_body")},
            "surfaced_effects": {("effects_commit.py", "rendered_effects"),
                                 ("brief.py", "_render_briefing_body"),
                                 ("brief.py", "_cmd_brief"),
                                 ("mcp_tools.py", "_brief"),
                                 ("hooks.py", "pre_llm_call")}}
    got: dict = {k: set() for k in want}
    for path in pkg.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    f = node.func
                    name = f.attr if isinstance(f, ast.Attribute) else \
                        getattr(f, "id", None)
                    if name in got:
                        got[name].add((path.name, fn.name))
    for name, sites in want.items():
        assert got[name] <= sites, (name, got[name] - sites)
        assert got[name], name


# ---- 11: the capability tables --------------------------------------------

def test_every_host_row_says_whether_it_briefs():
    assert set(host_mcp_caps.MCP_HOST_CAPS) >= {
        "claude-code", "codex", "kimi", "gemini", "windsurf"}
    for host, row in host_mcp_caps.MCP_HOST_CAPS.items():
        assert row["brief"].startswith(("supported", "unsupported")), host


def test_every_section_has_a_cell_for_every_entry():
    entries = {"cli", "mcp", "api", "hermes"}
    for section, row in briefing.SECTION_CAPS.items():
        assert set(row) == entries, section
        for cell in row.values():
            assert cell.startswith(("supported", "opt-in", "unsupported"))
    assert {"baton", "rulings", "decision_count", "request", "verdict",
            "owed", "overdue", "version_note", "withheld",
            "surfaced_stamps", "anchor_drift", "teammates"} == set(
        briefing.SECTION_CAPS)


# ---- 13: one clock ---------------------------------------------------------

def test_the_overdue_age_does_not_move_with_the_wall_clock(
        tmp_checkpoint_dir):
    ids = seed.seed()
    first = api.preview_briefing(seed.PROJECT, now=NOW).text
    with clock.use(clock.StepClock(seed.T0 + 400 * seed.DAY)):
        second = api.preview_briefing(seed.PROJECT, now=NOW).text
    assert first == second
    assert "10d" in first and ids["overdue"] in first
    assert config.model_cross_project() is False
