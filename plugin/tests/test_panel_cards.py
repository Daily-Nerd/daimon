"""#1132 PR 7b-2: the request and verdict panels capture their cards ONCE, at
the read that builds the panel lines, and what the brief stamps as surfaced is
exactly what that read printed in full."""

import re

import pytest

from daimon_briefing import briefing, cli, render, requests, store

SENDER = "/p/pc-sender"
RECIPIENT = "/p/pc-recipient"


def _checkpoint(project, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project)


def _ask(sender, recipient, ask):
    return requests.open_request(
        to=store.project_slug(recipient), ask=ask, why="because",
        channel="cli-agent", project_dir=sender)


def test_the_request_panel_returns_its_cards_with_the_lines(tmp_checkpoint_dir):
    _checkpoint(RECIPIENT, "S-pc-r")
    rid = _ask(SENDER, RECIPIENT, "publish the schema")
    lines, cards = briefing.request_panel(RECIPIENT)
    assert lines == briefing.request_panel_lines(RECIPIENT)
    assert [c.request_id for c in cards] == [rid]
    assert cards[0].stamp is True and cards[0].reply_event_id is None


def test_a_surfaced_request_card_owes_no_stamp(tmp_checkpoint_dir):
    _checkpoint(RECIPIENT, "S-pc-r2")
    rid = _ask(SENDER, RECIPIENT, "publish the schema")
    requests.stamp_surfaced(rid, project_dir=RECIPIENT)
    _lines, cards = briefing.request_panel(RECIPIENT)
    assert [(c.request_id, c.stamp) for c in cards] == [(rid, False)]


def test_the_verdict_panel_returns_its_cards_with_the_lines(tmp_checkpoint_dir):
    _checkpoint(SENDER, "S-pc-s")
    _checkpoint(RECIPIENT, "S-pc-r3")
    rid = _ask(SENDER, RECIPIENT, "publish the schema")
    requests.accept(rid, channel="cli-tty", project_dir=RECIPIENT)
    lines, cards = briefing.verdict_panel(SENDER)
    assert lines == briefing.verdict_panel_lines(SENDER)
    assert [(c.request_id, c.stamp) for c in cards] == [(rid, True)]


def test_a_panel_without_a_project_or_after_a_read_failure_has_no_cards(
        monkeypatch):
    assert briefing.request_panel(None) == ([], ())
    assert briefing.verdict_panel(None) == ([], ())

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(requests, "decision_renderable", boom)
    monkeypatch.setattr(requests, "verdict_renderable", boom)
    assert briefing.request_panel("/p/x") == ([], ())
    assert briefing.verdict_panel("/p/x") == ([], ())


def _cp():
    return {"session_id": "S", "working_context": {"recent_decisions": [
        {"id": "d-aaaaaa", "text": "a decision", "trust": "inferred"}]}}


def test_select_keeps_the_cards_and_names_the_ones_printed_whole():
    b = briefing.build(_cp())
    cards = {"request": (briefing.Card("q-1", True),),
             "verdict": (briefing.Card("q-2", True),)}
    lines = ["Requests waiting on you (from other projects):", "→ q-1  x",
             "  From: y"]
    vlines = ["Decisions on requests you sent:", "✓ accepted  q-2  z",
              "  To: w"]
    whole = briefing.select(b, None, request_lines=lines, verdict_lines=vlines,
                            cards=cards)
    assert whole.cards == cards
    assert whole.printed_cards() == cards
    cut = briefing.select(b, 1, request_lines=lines, verdict_lines=vlines,
                          cards=cards)
    assert cut.cards == cards
    assert cut.printed_cards() == {"request": (), "verdict": ()}


def test_render_brief_returns_the_cards_it_printed_and_none_without_a_gate(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: False)
    _checkpoint(RECIPIENT, "S-pc-r4")
    rid = _ask(SENDER, RECIPIENT, "publish the schema")
    got = render.render_brief(_cp(), project_dir=RECIPIENT,
                              worldcheck_project=RECIPIENT)
    assert {k: [c.request_id for c in v] for k, v in got.items()} == {
        "request": [rid], "verdict": []}
    assert render.render_brief(_cp(), project_dir=RECIPIENT) == {
        "request": (), "verdict": ()}
    capsys.readouterr()


def test_the_removed_post_print_re_read_is_gone():
    assert not hasattr(render, "_card_ids")


def _stamped(project):
    return {r["request_id"] for r in requests.events(project_dir=project)
            if r.get("event") in ("surfaced", "verdict_surfaced")}


@pytest.mark.parametrize("budget", [None, "700"])
def test_the_stamped_set_equals_the_printed_set(
        tmp_checkpoint_dir, monkeypatch, capsys, budget):
    """The brief stamps what its panels printed in full, nothing else:
    request ids found in the output are exactly the ids stamped."""
    project = "/p/pc-both"
    _checkpoint(project, "S-pc-both")
    incoming = [_ask("/p/pc-in", project, f"incoming ask {n}")
                for n in range(3)]
    sent = []
    for n in range(2):
        rid = _ask(project, "/p/pc-out", f"sent ask {n}")
        requests.accept(rid, channel="cli-tty", project_dir="/p/pc-out")
        sent.append(rid)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    if budget:
        monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", budget)
        monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "0")
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    printed = {i for i in incoming + sent if re.search(re.escape(i), out)}
    assert printed, "fixture: the budget must still print some card"
    assert _stamped(project) == printed


def test_the_brief_reads_each_panel_once(tmp_checkpoint_dir, monkeypatch,
                                         capsys):
    _checkpoint(RECIPIENT, "S-pc-once")
    _ask(SENDER, RECIPIENT, "publish the schema")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)
    calls = {"decision": 0, "verdict": 0}
    real_d, real_v = requests.decision_renderable, requests.verdict_renderable

    def d(*a, **k):
        calls["decision"] += 1
        return real_d(*a, **k)

    def v(*a, **k):
        calls["verdict"] += 1
        return real_v(*a, **k)

    monkeypatch.setattr(requests, "decision_renderable", d)
    monkeypatch.setattr(requests, "verdict_renderable", v)
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    assert calls == {"decision": 1, "verdict": 1}
