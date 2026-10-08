"""#1128: the fail-open branches of briefing.prepare, the CLI's worldcheck
bookkeeping, and the paths that print a trailer or stamp a verdict."""

import datetime as dt
import time

import pytest

from daimon_briefing import (briefing, cli, render, requests, store, trust,
                             worldcheck)

from ._prepared import in_hand
from .test_briefing_select import NOW, _fixture_checkpoint
from daimon_briefing.surfaces import Writer

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
    return in_hand(cp if cp is not None else _cp(), ROUTE, time.time())


def _item(out):
    return out.checkpoint["working_context"]["open_questions"][0]


def test_prepare_over_a_non_checkpoint_has_nothing_to_stamp_or_probe():
    for bad in (None, {}, "not a checkpoint"):
        out = in_hand(bad, ROUTE, NOW)
        assert not out.checkpoint
        assert out.withheld == () and out.stale_items == []
        assert out.worldcheck is None and out.ledger_rows == []


def test_a_resolution_fold_that_raises_is_a_health_state(monkeypatch):
    """#1132 PR 7a: no try block hides it; the snapshot marks the ledger
    UNREADABLE, the loops it would have closed stay open, and the rest of the
    preparation still runs."""
    monkeypatch.setattr(store, "fold_resolutions", _boom)
    out = _annotate()
    assert out.withheld == () and len(out.events) == 0
    assert out.notes and out.notes[0].startswith("⚠ events.jsonl is unreadable")
    assert round(_item(out)["_stale_carried_days"]) == 12   # the rest still ran


def test_an_unreadable_trust_fold_closes_the_view_and_says_so(monkeypatch):
    """The reverse of the old posture: a trust ledger that cannot be read can
    no longer prove an item is not quarantined, so nothing is shown."""
    monkeypatch.setattr(trust, "records", _boom)
    out = _annotate()
    assert out.checkpoint["working_context"]["open_questions"] == []
    assert out.notes and out.notes[0].startswith("⚠ trust.jsonl is unreadable")
    assert out.stale_items == []


def test_a_corroboration_fold_that_raises_leaves_the_badge_absent(monkeypatch):
    monkeypatch.setattr(store, "fold_corroborations", _boom)
    out = _annotate()
    assert "_corroborated" not in _item(out)
    assert out.stale_items            # later steps unaffected
    assert out.notes[0].startswith("⚠ events.jsonl is unreadable")


def test_a_stamping_bug_is_not_swallowed(monkeypatch):
    """`prepare` has no try blocks around the stamps: a raise from them is a
    bug the host reports (the CLI prints one error line and exits 2)."""
    monkeypatch.setattr(briefing, "stamp_stale_carried", _boom)
    with pytest.raises(RuntimeError):
        _annotate()


def test_worldcheck_failing_costs_only_the_worldcheck(monkeypatch):
    monkeypatch.setenv("DAIMON_WORLDCHECK", "1")
    monkeypatch.setattr(worldcheck, "check", _boom)
    out = in_hand(_cp(), ROUTE, time.time(), worldcheck_project=ROUTE)
    assert out.worldcheck is None and out.ledger_rows == []
    assert out.stale_items


def test_panel_cards_fail_open_to_nothing_stamped(monkeypatch, capsys):
    """Retargeted from the removed post-print re-read: the manifest now comes
    from the panels' own read, and a composer that raises there yields no
    cards, so nothing is stamped."""
    monkeypatch.setattr(requests, "decision_renderable", _boom)
    monkeypatch.setattr(requests, "verdict_renderable", _boom)
    for project in ("/p/x", None):
        got = render.render_brief(_cp(), project_dir=project,
                                  worldcheck_project=project)
        assert got == {"request": (), "verdict": ()}
    capsys.readouterr()


def test_is_flagged_ignores_a_non_item():
    assert briefing._is_flagged(None) is False
    assert briefing._is_flagged("x") is False


# ---- the CLI's worldcheck bookkeeping ----


def _write_checkpoint(project):
    store.write_checkpoint("S-fo", _cp(), project_dir=project, writer=Writer.HUMAN)


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
                {"text": "x", "trust": "inferred"}]}}, project_dir=proj, writer=Writer.HUMAN)
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
