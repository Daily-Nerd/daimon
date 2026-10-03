"""#1128: the fail-open branches of briefing.annotate, the CLI's worldcheck
bookkeeping, and the paths that print a trailer or stamp a verdict."""

import datetime as dt
import time

import pytest

from daimon_briefing import (briefing, cli, render, requests, store, trust,
                             worldcheck)

from .test_briefing_select import NOW, _fixture_checkpoint

ROUTE = "/repo/failopen"


def _iso(days):
    t = dt.datetime.fromtimestamp(time.time() - days * 86400, dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cp():
    return {
        "session_id": "S-fo",
        "working_context": {
            "open_questions": [
                {"text": "old carried claim", "trust": "inferred",
                 "carried_from": "S-prev", "id": "o-aaaaaa",
                 "first_seen": _iso(12)}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {},
    }


def _boom(*a, **k):
    raise RuntimeError("boom")


def _annotate(cp=None):
    return briefing.annotate(cp if cp is not None else _cp(),
                             briefing.AnnotateContext(route=ROUTE), time.time())


def _item(out):
    return out.checkpoint["working_context"]["open_questions"][0]


def test_annotate_returns_a_non_checkpoint_untouched():
    for bad in (None, {}, "not a checkpoint"):
        out = briefing.annotate(bad, briefing.AnnotateContext(route=ROUTE), NOW)
        assert out.checkpoint == bad
        assert out.withheld == [] and out.stale_items == []
        assert out.worldcheck is None and out.ledger_rows == []


def test_withhold_failing_costs_only_the_withhold(monkeypatch):
    monkeypatch.setattr(briefing, "withhold", _boom)
    out = _annotate()
    assert out.withheld == [] and out.events == {}
    assert round(_item(out)["_stale_carried_days"]) == 12   # the rest still ran


def test_quarantine_read_failing_costs_only_the_withhold(monkeypatch):
    monkeypatch.setattr(trust, "active_value_keys", _boom)
    out = _annotate()
    assert out.withheld == []
    assert out.stale_items            # stale stamping is its own step


def test_corroboration_failing_leaves_the_badge_absent(monkeypatch):
    monkeypatch.setattr(briefing, "mark_corroborated", _boom)
    out = _annotate()
    assert "_corroborated" not in _item(out)
    assert out.stale_items            # later steps unaffected


def test_stale_stamping_failing_leaves_the_mark_absent(monkeypatch):
    monkeypatch.setattr(briefing, "stamp_stale_carried", _boom)
    out = _annotate()
    assert out.stale_items == []
    assert "_stale_carried_days" not in _item(out)
    text = briefing.render_plain(briefing.build(out.checkpoint))
    assert "old carried claim" in text and "[? unverified]" not in text


def test_worldcheck_failing_costs_only_the_worldcheck(monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setattr(worldcheck, "check", _boom)
    out = briefing.annotate(
        _cp(), briefing.AnnotateContext(route=ROUTE, worldcheck_project=ROUTE),
        time.time())
    assert out.worldcheck is None and out.ledger_rows == []
    assert out.stale_items


def test_card_ids_fail_open_to_nothing_stamped(monkeypatch):
    empty = {"request": frozenset(), "verdict": frozenset()}
    monkeypatch.setattr(requests, "decision_renderable", _boom)
    monkeypatch.setattr(requests, "verdict_renderable", _boom)
    assert render._card_ids("/p/x") == empty
    assert render._card_ids(None) == empty


def test_is_flagged_ignores_a_non_item():
    assert briefing._is_flagged(None) is False
    assert briefing._is_flagged("x") is False


# ---- the CLI's worldcheck bookkeeping ----


def _write_checkpoint(project):
    store.write_checkpoint("S-fo", _cp(), project_dir=project)


def test_cli_brief_records_worldcheck_counters_and_ledger(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _write_checkpoint(ROUTE)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", ROUTE)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setattr(worldcheck, "check", lambda cp, project: {
        "fired": 2, worldcheck.LEDGER_KEY: [("o-aaaaaa", "pr-state", "merged")]})
    usage, receipt, ledger = [], [], []
    monkeypatch.setattr(cli, "_note_usage", usage.append)
    monkeypatch.setattr(cli, "_note_receipt_probe_usage",
                        lambda project, stats: receipt.append(dict(stats)))
    monkeypatch.setattr(cli, "_write_worldcheck_ledger",
                        lambda rows, route: ledger.append(list(rows)))
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    assert usage.count("worldcheck:fired") == 2
    assert receipt == [{"fired": 2}]               # LEDGER_KEY popped first
    assert ledger == [[("o-aaaaaa", "pr-state", "merged")]]


def test_cli_brief_survives_worldcheck_bookkeeping_failing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _write_checkpoint(ROUTE)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", ROUTE)
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setattr(worldcheck, "check",
                        lambda cp, project: {"fired": 1, worldcheck.LEDGER_KEY: []})
    real = cli._note_usage
    monkeypatch.setattr(cli, "_note_usage", lambda c: _boom() if c.startswith("worldcheck:") else real(c))
    assert cli.main(["brief"]) == 0
    assert "old carried claim" in capsys.readouterr().out


# ---- trailer and teammates on the rich path ----


def test_rich_path_prints_the_trailer(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_brief(_fixture_checkpoint(), trailer=["3 resolved item(s) withheld"])
    assert "3 resolved item(s) withheld" in capsys.readouterr().out


def test_rich_teammate_panel_shows_the_cap_note(monkeypatch, capsys):
    pytest.importorskip("rich")
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "2")
    b = briefing.build(_fixture_checkpoint(), now=NOW)
    render._rich_teammates([("grace", b)])
    assert "2 of 43 shown" in capsys.readouterr().out


# ---- a verdict card the budget cut is not stamped ----


def test_a_collapsed_verdict_panel_is_not_stamped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender, recipient = "/p/fo-sender", "/p/fo-recipient"
    for proj, sess in ((sender, "S-fo-s"), (recipient, "S-fo-r")):
        store.write_checkpoint(sess, {
            "session_id": sess, "created": "2026-08-16T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": "x", "trust": "inferred"}]}}, project_dir=proj)
    q_id = requests.open_request(
        to=store.project_slug(recipient), ask="publish the schema", why="b",
        channel="cli-agent", project_dir=sender)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "120")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "publish the schema" not in out
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record) is True
