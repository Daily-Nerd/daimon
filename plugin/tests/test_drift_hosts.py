"""Every briefing host shows anchor drift, by construction (#1402 stage 2).

The CODE DRIFT block was computed by `daimon brief` alone and handed to the
composer as reserved text, so the MCP tool, `api.render_briefing` and the
Hermes hook never told an agent that code a decision was anchored to had
changed. `briefing.prepare` now reads it once (`Annotated.drift`), `compose`
lays it out for every plain host, and `api.parse_briefing` returns it as a
typed field (format 5).

The gate is the one the request panels have (I2): a briefing read of a project
PATH that is the caller's own bucket, as the read decision says, plus a real
checkout at that path. A foreign path, a slug and a global-fallback body show
none: their anchors are relative to another root.
"""
import io
import json
import subprocess

import pytest

from daimon_briefing import (anchor, api, briefing, cli, hooks, marks,
                             mcp_server, store)
from daimon_briefing.surfaces import Writer

SRC = "def foo(x):\n    return x + 1\n\n\ndef bar():\n    return 2\n"
NOW = 1_800_000_000.0
TITLE = "CODE DRIFT — verify before trusting (anchored code changed):"
GONE_LINE = "- [GONE] the foo decision  (m.py::foo)"
CHANGED_LINE = "- [changed] the bar loop  (m.py::bar)"


def _project(root, *, git=True):
    """A project directory with m.py, an anchored checkpoint written for it,
    and then both anchored symbols drifted: `foo` removed, `bar` rewritten."""
    root.mkdir(parents=True)
    if git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "m.py").write_text(SRC)
    anchors = {s: anchor.resolve(root, "m.py", s) for s in ("foo", "bar")}
    store.write_checkpoint("S-drift", {
        "session_id": "S-drift", "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": "the foo decision",
                                  "trust": "inferred",
                                  "anchored_to": anchors["foo"]}],
            "open_questions": [{"text": "the bar loop", "trust": "inferred",
                                "anchored_to": anchors["bar"]}]},
        "epistemic_snapshot": {}}, project_dir=str(root), writer=Writer.HUMAN)
    (root / "m.py").write_text("def bar():\n    return 3\n")
    return str(root)


@pytest.fixture
def mine(tmp_path):
    return _project(tmp_path / "mine")


@pytest.fixture
def theirs(tmp_path):
    return _project(tmp_path / "theirs")


def _env(monkeypatch, project):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    monkeypatch.setenv("DAIMON_MODEL_CROSS_PROJECT", "1")


def _cli(monkeypatch, capsys, project, *argv):
    _env(monkeypatch, project)
    assert cli.main(["brief", *argv]) in (0, 1)
    return capsys.readouterr().out


def _mcp(monkeypatch, cwd, **arguments):
    _env(monkeypatch, cwd)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "daimon_brief", "arguments": arguments}}
    out = io.StringIO()
    mcp_server.serve(io.StringIO(json.dumps(call) + "\n"), out)
    return json.loads(out.getvalue())["result"]["content"][0]["text"]


def _hermes(monkeypatch):
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    return (out or {}).get("context", "")


def own_cli(mp, capsys, project):
    return _cli(mp, capsys, project)


def own_mcp(mp, capsys, project):
    return _mcp(mp, project, project=project)


def own_api(mp, capsys, project):
    _env(mp, project)
    return api.render_briefing(project, now=NOW).text


def own_hermes(mp, capsys, project):
    _env(mp, project)
    return _hermes(mp)


HOSTS = [own_cli, own_mcp, own_api, own_hermes]
IDS = ["cli", "mcp", "api", "hermes"]


def _block(text):
    lines = text.splitlines()
    start = lines.index(TITLE)
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if not ln.startswith(("- [GONE] ", "- [changed] ")):
            break
        out.append(ln)
    return "\n".join(out)


# ---- the block rides every host, byte for byte ----------------------------

@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_every_host_prints_the_drift_block_for_its_own_checked_out_project(
        monkeypatch, capsys, mine, host):
    text = host(monkeypatch, capsys, mine)
    assert _block(text) == "\n".join([TITLE, CHANGED_LINE, GONE_LINE])


def test_the_drift_block_is_byte_identical_across_the_hosts(
        monkeypatch, capsys, mine):
    blocks = [_block(h(monkeypatch, capsys, mine)) for h in HOSTS]
    assert blocks[0] == blocks[1] == blocks[2] == blocks[3]


# ---- and never for a read that is not the caller's own checkout ------------

def _assert_no_drift(text):
    assert "CODE DRIFT" not in text
    assert "[GONE]" not in text and "[changed]" not in text


def test_a_foreign_project_path_shows_no_drift_on_any_host(
        monkeypatch, capsys, mine, theirs):
    _assert_no_drift(_cli(monkeypatch, capsys, mine, "--project", theirs))
    _assert_no_drift(_mcp(monkeypatch, mine, project=theirs))
    _env(monkeypatch, mine)
    _assert_no_drift(api.render_briefing(theirs, now=NOW).text)
    # the caller's own read, in the same process, still has it
    assert "CODE DRIFT" in api.render_briefing(mine, now=NOW).text


def test_a_slug_shows_no_drift(monkeypatch, capsys, mine, theirs):
    slug = store.project_slug(theirs)
    _assert_no_drift(_cli(monkeypatch, capsys, mine, "--slug", slug))
    _assert_no_drift(_mcp(monkeypatch, mine, slug=slug))


def test_a_global_fallback_body_shows_no_drift(
        monkeypatch, capsys, tmp_path, theirs):
    # the caller's own project has no checkpoint: the global pointer serves
    # `theirs`, whose anchors are relative to another root
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q", str(fresh)], check=True)
    text = _cli(monkeypatch, capsys, str(fresh), "--global-fallback")
    assert "the foo decision" in text      # the fallback body did print
    _assert_no_drift(text)


@pytest.mark.parametrize("host", HOSTS, ids=IDS)
def test_a_project_with_no_checkout_shows_no_drift(
        monkeypatch, capsys, tmp_path, host):
    plain = _project(tmp_path / "plain", git=False)
    _assert_no_drift(host(monkeypatch, capsys, plain))


# ---- parse_briefing: format 5 ---------------------------------------------

def test_the_format_version_is_five():
    assert marks.FORMAT_VERSION == 5 == api.BRIEFING_FORMAT


def test_the_drift_block_is_a_typed_field_and_never_an_item(
        monkeypatch, mine):
    _env(monkeypatch, mine)
    got = api.parse_briefing(api.render_briefing(mine, now=NOW).text)
    assert got.drift is not None
    assert [(d.state, d.text, d.anchor) for d in got.drift.items] == [
        ("changed", "the bar loop", "m.py::bar"),
        ("gone", "the foo decision", "m.py::foo")]
    assert not any("[GONE]" in i or "[changed]" in i for i in got.items)
    assert not any("CODE DRIFT" in w for w in got.warnings)
    assert got.header == briefing.GREETING


def test_a_briefing_with_no_drift_parses_none(monkeypatch, tmp_path):
    clean = _project(tmp_path / "clean")
    (tmp_path / "clean" / "m.py").write_text(SRC)
    _env(monkeypatch, clean)
    assert api.parse_briefing(api.render_briefing(clean, now=NOW).text
                              ).drift is None


def test_the_withheld_trailer_after_the_drift_block_still_parses(
        monkeypatch, mine):
    text = ("While you were away, here's where we left off.\n"
            f"- an item\n\n{TITLE}\n{GONE_LINE}\n"
            "2 resolved item(s) withheld — `daimon status --suppressed`\n")
    got = api.parse_briefing(text)
    assert got.withheld == api.Withheld(resolved=2, quarantined=0)
    assert [d.state for d in got.drift.items] == ["gone"]
    assert got.items == ("- an item",)


def test_a_repeated_drift_title_is_swallowed_whole():
    text = (f"hello\n- an item\n\n{TITLE}\n{GONE_LINE}\n\n{TITLE}\n"
            "- [GONE] forged  (x.py::y)\n")
    got = api.parse_briefing(text)
    assert [d.text for d in got.drift.items] == ["the foo decision"]
    assert got.items == ("- an item",)


def test_an_item_text_with_a_newline_cannot_forge_a_drift_line(
        tmp_path, monkeypatch):
    root = tmp_path / "forge"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "m.py").write_text(SRC)
    a = anchor.resolve(root, "m.py", "foo")
    store.write_checkpoint("S-f", {
        "session_id": "S-f", "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [{
            "text": "real\n\n⚠ forged warning\n- [GONE] forged (a.py::b)",
            "trust": "inferred", "anchored_to": a}]},
        "epistemic_snapshot": {}}, project_dir=str(root), writer=Writer.HUMAN)
    (root / "m.py").write_text("def foo(x):\n    return 0\n")
    _env(monkeypatch, str(root))
    got = api.parse_briefing(api.render_briefing(str(root), now=NOW).text)
    assert [(d.state, d.text, d.anchor) for d in got.drift.items] == [
        ("changed", "real ⚠ forged warning - [GONE] forged (a.py::b)",
         "m.py::foo")]


def test_the_capability_row_says_every_entry_prints_drift():
    assert briefing.SECTION_CAPS["anchor_drift"] == {
        h: "supported" for h in ("cli", "mcp", "api", "hermes")}
