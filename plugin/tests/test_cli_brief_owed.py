"""#885: the recipient-side OWED panel + owed_delivered stamp.

The third request panel. PR 2 gave the recipient its undecided asks and PR 3
gave the sender its verdicts; neither surface showed a recipient the work it
had already AGREED to, because both filters ran off a sender-side frozenset
that treats `accepted` as settled. For the recipient `accepted` is where the
work starts and `done` is the close.

Mirrors test_cli_brief_requests.py and test_cli_brief_verdicts.py: same D2
same-project gate, same post-print stamp timing.
"""

from daimon_briefing import briefing, cli, requests, store


def _seed_checkpoint(project_dir, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir)
    return store.project_slug(project_dir)


def _owed_ask(recipient_dir, ask="port the corroboration model",
              sender_dir="/p/cbo-sender"):
    """An ask addressed to `recipient_dir` and accepted by it — the state in
    which the recipient owes work and nothing surfaced it."""
    _seed_checkpoint(recipient_dir, "S-cbo-recipient")
    sender_slug = _seed_checkpoint(sender_dir, "S-cbo-sender")
    q_id = requests.open_request(to=store.project_slug(recipient_dir), ask=ask,
                                 why="because", channel="cli-agent",
                                 project_dir=sender_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_dir)
    return q_id


def test_cli_brief_shows_the_owed_panel_on_the_normal_path(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/cbo-recipient-a"
    _owed_ask(recipient, sender_dir="/p/cbo-sender-a")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert briefing._OWED_PANEL_HEADER in out
    assert "port the corroboration model" in out


def test_the_owed_panel_heading_renders_verbatim(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """Registered the same way the other two request headings are (§3
    amendment 2026-08-27). Rename it and this fails deliberately: change the
    registration first and this assertion second, never the reverse."""
    assert briefing._OWED_PANEL_HEADER == \
        "Requests you accepted and still owe:"
    recipient = "/p/cbo-recipient-headings"
    _owed_ask(recipient, sender_dir="/p/cbo-sender-headings")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    assert briefing._OWED_PANEL_HEADER in capsys.readouterr().out


def test_the_owed_panel_is_absent_when_nothing_is_owed(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """Skeleton furniture, but empty furniture is noise — same posture as
    the other two panels."""
    recipient = "/p/cbo-recipient-empty"
    _seed_checkpoint(recipient, "S-cbo-empty")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    assert briefing._OWED_PANEL_HEADER not in capsys.readouterr().out


def test_the_owed_panel_disappears_once_done(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/cbo-recipient-done"
    q_id = _owed_ask(recipient, sender_dir="/p/cbo-sender-done")
    requests.done(q_id, channel="cli-tty", evidence="shipped it",
                  project_dir=recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    assert briefing._OWED_PANEL_HEADER not in capsys.readouterr().out


def test_owed_panel_lines_are_empty_without_a_project(tmp_checkpoint_dir):
    """The D2 gate: no worldcheck project, no panel — same contract as
    `request_panel_lines` and `verdict_panel_lines`."""
    assert briefing.owed_panel_lines(None) == []


def test_owed_panel_lines_fail_open(tmp_checkpoint_dir, monkeypatch):
    """ANY composer error costs the panel, never the briefing."""
    def _boom(*a, **k):
        raise RuntimeError("ledger unreadable")
    monkeypatch.setattr(requests, "owed_renderable", _boom)
    assert briefing.owed_panel_lines("/p/cbo-boom") == []


def test_owed_panel_names_the_verb_that_closes_it(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """The whole reason this is a second panel and not more rows in the
    inbox one: an undecided ask offers accept and reject, an owed one offers
    `done`, and the reader must not have to infer which from a glyph."""
    recipient = "/p/cbo-recipient-verb"
    _owed_ask(recipient, sender_dir="/p/cbo-sender-verb")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    assert "daimon request done" in capsys.readouterr().out


def test_owed_overflow_line_does_not_pluralise_owed(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """"owed" is not a count noun. The sibling panels count nouns ("+2 more
    waiting", "+2 more decided") and copying their `plural` idiom here
    produced "+4 more oweds" on the live ledger."""
    recipient = "/p/cbo-recipient-plural"
    _seed_checkpoint(recipient, "S-cbo-plural")
    sender_slug = _seed_checkpoint("/p/cbo-sender-plural", "S-cbo-sp")
    for n in range(requests.RENDER_CAP + 2):
        q_id = requests.open_request(to=store.project_slug(recipient),
                                     ask=f"owed ask number {n}", why="because",
                                     channel="cli-agent",
                                     project_dir=sender_slug)
        requests.accept(q_id, channel="cli-tty", project_dir=recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "(+2 more owed —" in out
    assert "oweds" not in out


def test_cli_rich_brief_carries_the_owed_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """The rich path renders its own Panel per skeleton section, so the plain
    path passing proves nothing about it. Same coverage posture as the three
    sibling panels."""
    from daimon_briefing import render
    recipient = "/p/cbo-recipient-rich"
    _owed_ask(recipient, ask="rich brief carries this",
              sender_dir="/p/cbo-sender-rich")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert "rich brief carries this" in out
    assert "Requests you accepted" in out
