"""#1128: the shared annotation step. Every host that renders a briefing
(CLI, MCP, Hermes) goes through briefing.annotate, so the stale marks and
the other render-time annotations cannot differ by host. (#1132 PR 7a: the
hosts call `briefing.prepare`; `annotate` is its in-hand form.)"""

import datetime as dt
import time

from daimon_briefing import briefing, cli, hooks, mcp_tools, store, worldcheck

NOW = 1_800_000_000.0
PROJECT = "/repo/annotate"


def _iso(days_before_now, now=NOW):
    t = dt.datetime.fromtimestamp(now - days_before_now * 86400, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cp(items):
    return {
        "session_id": "S-annotate",
        "working_context": {"open_questions": items, "recent_decisions": []},
        "epistemic_snapshot": {},
    }


def _carried(text, **extra):
    return {"text": text, "trust": "inferred", "carried_from": "S-prev",
            "id": "o-" + format(sum(map(ord, text)) % 0xFFFFFF, "06x"), **extra}


def _ctx(route=PROJECT, **kw):
    return briefing.AnnotateContext(route=route, **kw)


def test_annotate_stamps_stale_carried_items():
    cp = _cp([_carried("old claim", first_seen=_iso(12))])
    out = briefing.annotate(cp, _ctx(), NOW)
    item = out.checkpoint["working_context"]["open_questions"][0]
    assert round(item["_stale_carried_days"]) == 12
    assert out.stale_items == [item]
    assert "_stale_carried_days" not in cp["working_context"]["open_questions"][0]


def test_annotate_fresh_item_is_not_stamped():
    cp = _cp([_carried("fresh claim", first_seen=_iso(2))])
    out = briefing.annotate(cp, _ctx(), NOW)
    assert out.stale_items == []
    assert "_stale_carried_days" not in out.checkpoint["working_context"]["open_questions"][0]


def test_future_stamp_clamps_to_age_zero():
    cp = _cp([_carried("from the future", first_seen=_iso(-5))])
    assert briefing._carried_age_days(
        cp["working_context"]["open_questions"][0], {}, NOW) == 0.0
    out = briefing.annotate(cp, _ctx(), NOW)
    assert out.stale_items == []


def test_missing_stamp_is_fail_open_not_stale():
    cp = _cp([_carried("no stamps at all")])
    out = briefing.annotate(cp, _ctx(), NOW)
    assert out.stale_items == []


def test_annotate_survives_an_unreadable_ledger_and_says_so(
        tmp_checkpoint_dir):
    """A ledger that cannot be read is a health value, not an exception: the
    item stays and the note names the ledger (never its content)."""
    bucket = tmp_checkpoint_dir / store.project_slug(PROJECT)
    bucket.mkdir(parents=True)
    (bucket / "events.jsonl").write_bytes(b"\xff\xfe not utf-8\n")
    cp = _cp([_carried("old claim", first_seen=_iso(12))])
    out = briefing.annotate(cp, _ctx(), NOW)
    assert out.checkpoint["working_context"]["open_questions"][0]["text"] == "old claim"
    assert out.withheld == ()
    assert out.notes[0].startswith("⚠ events.jsonl is unreadable")


def test_annotate_without_worldcheck_project_never_probes(monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")

    def _boom(*a, **k):
        raise AssertionError("worldcheck must not run")
    monkeypatch.setattr(worldcheck, "check", _boom)
    out = briefing.annotate(_cp([_carried("x", first_seen=_iso(1))]), _ctx(), NOW)
    assert out.worldcheck is None


def test_annotate_runs_worldcheck_only_when_flag_and_project(monkeypatch):
    calls = []

    def _fake(cp, project):
        calls.append(project)
        return {"fired": 1, worldcheck.LEDGER_KEY: [{"row": 1}]}
    monkeypatch.setattr(worldcheck, "check", _fake)
    cp = _cp([_carried("x", first_seen=_iso(1))])
    monkeypatch.setenv("DAIMON_WORLDCHECK", "0")
    briefing.annotate(cp, _ctx(worldcheck_project=PROJECT), NOW)
    assert calls == []
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    out = briefing.annotate(cp, _ctx(worldcheck_project=PROJECT), NOW)
    assert calls == [PROJECT]
    assert out.worldcheck == {"fired": 1}
    assert out.ledger_rows == [{"row": 1}]


def _write_stale(project=PROJECT):
    cp = _cp([_carried("stale carried claim", first_seen=_iso(12, time.time()))])
    store.write_checkpoint("S-annotate", cp, project_dir=project)


def test_mcp_brief_carries_stale_marks(tmp_checkpoint_dir):
    _write_stale()
    out = mcp_tools.HANDLERS["daimon_brief"]({"slug": store.project_slug(PROJECT)})
    assert "[? unverified] stale carried claim" in out


def test_hermes_injection_carries_stale_marks(tmp_checkpoint_dir, monkeypatch):
    _write_stale()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(session_id="S-new", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert "[? unverified] stale carried claim" in out["context"]


def test_every_host_path_calls_prepare(tmp_checkpoint_dir, monkeypatch, capsys):
    _write_stale()
    seen = []
    real = briefing.prepare

    def _spy(project, now, **kw):
        seen.append(project)
        return real(project, now, **kw)
    monkeypatch.setattr(briefing, "prepare", _spy)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief", "--project", PROJECT]) == 0
    assert len(seen) == 1
    mcp_tools.HANDLERS["daimon_brief"]({"slug": store.project_slug(PROJECT)})
    assert len(seen) == 2
    hooks.pre_llm_call(session_id="S-new", user_message="hi",
                       conversation_history=[], is_first_turn=True,
                       model="m", platform="cli")
    assert len(seen) == 3
    assert cli.main(["loops", "--project", PROJECT]) == 0
    assert len(seen) == 4
