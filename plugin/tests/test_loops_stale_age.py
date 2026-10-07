"""#1128 PR 2: `daimon loops` age column, stale marker and `--stale`, and the
briefing note pointer that sends a reader there (never on a fallback or
cross-project route, where `daimon loops` would list a different project)."""

import datetime as dt
import time

from daimon_briefing import briefing, cli, hooks, mcp_tools, store

PROJECT = "/repo/loops-age"
OTHER = "/repo/somewhere-else"


def _iso(days_ago):
    t = dt.datetime.fromtimestamp(time.time() - days_ago * 86400, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _padded(tag):
    return f"{tag} " + "padding words about the state of things " * 6


def _items():
    stale = [{"text": _padded(f"stale-{i}"), "trust": "inferred",
              "carried_from": "S-prev", "first_seen": _iso(20 + i)}
             for i in range(8)]
    fresh = [{"text": _padded(f"fresh-{i}"), "trust": "inferred",
              "carried_from": "S-prev", "first_seen": _iso(1)}
             for i in range(2)]
    native = [{"text": "native loop", "trust": "inferred",
               "first_seen": _iso(2)}]
    return stale + fresh + native


def _write(project=PROJECT, items=None):
    cp = {"session_id": "S-loops",
          "working_context": {"open_questions": items or _items(),
                              "recent_decisions": []},
          "epistemic_snapshot": {}}
    store.write_checkpoint("S-loops", cp, project_dir=project)


def _annotated(project=PROJECT):
    body = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                  admit=store.Admit.ANY)
    return briefing.annotate(body, briefing.AnnotateContext(route=project),
                             time.time())


def _row_for(out, tag):
    return next(ln for ln in out.splitlines() if tag in ln)


def test_loops_rows_carry_an_age_and_a_stale_marker(tmp_checkpoint_dir, capsys):
    _write()
    assert cli.main(["loops", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "[20d stale]" in _row_for(out, "stale-0")
    assert "[1d]" in _row_for(out, "fresh-0")
    assert "[2d]" in _row_for(out, "native loop")
    assert "stale]" not in _row_for(out, "fresh-0")


def test_loops_stale_lists_exactly_the_annotate_stale_set(tmp_checkpoint_dir, capsys):
    _write()
    annotated = _annotated()
    expected = {i["id"] for i in annotated.stale_items}
    assert len(expected) == 8
    assert cli.main(["loops", "--stale", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    listed = {ln.split()[0] for ln in out.splitlines() if ln.strip()}
    assert listed == expected
    assert "fresh-0" not in out and "native loop" not in out


def test_loops_stale_with_nothing_stale(tmp_checkpoint_dir, capsys):
    _write(items=[{"text": "just born", "trust": "inferred",
                   "carried_from": "S-prev", "first_seen": _iso(1)}])
    assert cli.main(["loops", "--stale", "--project", PROJECT]) == 0
    assert "no stale carried loops" in capsys.readouterr().out


def test_loops_keeps_route_own(tmp_checkpoint_dir, capsys):
    # Pre-existing decision: loops never lists another project's checkpoint.
    _write(project=OTHER)
    assert cli.main(["loops", "--stale", "--project", PROJECT]) == 0
    assert "no checkpoint" in capsys.readouterr().out.lower()


def test_loops_calls_the_shared_preparation(tmp_checkpoint_dir, monkeypatch, capsys):
    _write()
    seen = []
    real = briefing.prepare

    def _spy(project, now, **kw):
        seen.append(project)
        return real(project, now, **kw)
    monkeypatch.setattr(briefing, "prepare", _spy)
    assert cli.main(["loops", "--project", PROJECT]) == 0
    assert seen == [PROJECT]


# ---- the briefing note pointer, per route and host ----


def _tight(monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setenv("DAIMON_STALE_DAYS", "7")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "900")


def test_cli_brief_note_points_at_stale_listing(tmp_checkpoint_dir, monkeypatch, capsys):
    _tight(monkeypatch)
    _write()
    assert cli.main(["brief", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "unverified over 7d hidden" in out
    assert "See: daimon loops --stale" in out


def test_cli_brief_fallback_route_has_no_pointer(tmp_checkpoint_dir, monkeypatch, capsys):
    _tight(monkeypatch)
    _write(project=OTHER)
    assert cli.main(["brief", "--project", PROJECT, "--global-fallback"]) == 0
    out = capsys.readouterr().out
    assert "unverified over 7d hidden" in out
    assert "See:" not in out


def test_cli_brief_slug_route_has_no_pointer(tmp_checkpoint_dir, monkeypatch, capsys):
    _tight(monkeypatch)
    _write(project=OTHER)
    assert cli.main(["brief", "--slug", store.project_slug(OTHER)]) == 0
    out = capsys.readouterr().out
    assert "unverified over 7d hidden" in out
    assert "See:" not in out


def test_mcp_brief_pointer_only_for_the_own_project(tmp_checkpoint_dir, monkeypatch):
    _tight(monkeypatch)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _write()
    _write(project=OTHER)
    own = mcp_tools.HANDLERS["daimon_brief"]({})
    assert "See: daimon loops --stale" in own
    slug = mcp_tools.HANDLERS["daimon_brief"]({"slug": store.project_slug(OTHER)})
    assert "unverified over 7d hidden" in slug and "See:" not in slug
    foreign = mcp_tools.HANDLERS["daimon_brief"]({"project": OTHER})
    assert "unverified over 7d hidden" in foreign and "See:" not in foreign


def _hermes():
    return hooks.pre_llm_call(session_id="S-new", user_message="hi",
                              conversation_history=[], is_first_turn=True,
                              model="m", platform="cli")


def test_hermes_injection_pointer_on_own_route(tmp_checkpoint_dir, monkeypatch):
    _tight(monkeypatch)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _write()
    assert "See: daimon loops --stale" in _hermes()["context"]


def test_hermes_injection_fallback_route_has_no_pointer(tmp_checkpoint_dir, monkeypatch):
    _tight(monkeypatch)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    _write(project=OTHER)
    ctx = _hermes()["context"]
    assert "unverified over 7d hidden" in ctx and "See:" not in ctx
