"""#766 slice 5 — the one-line decision count on the briefing.

A new governed public string family: "N decisions waiting on you here
(M elsewhere) - daimon decide". `here` is exactly what bare `daimon decide`
lists (`pending.queue`); `elsewhere` is the INTEGER-ONLY fold `foreign_counts`
already exposes (scar 0055: never `foreign_queues`, the text path). The
wording and scope are frozen; a change to either routes through the
project's public-vocabulary process, not a local edit. Mirrors
test_cli_brief_requests.py's fixture shape.
"""

from daimon_briefing import briefing, cli, pending, render, requests, store


def _seed_checkpoint(project_dir, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir)
    return store.project_slug(project_dir)


def _open_ask(recipient_dir, ask, sender_dir, seed_recipient=True):
    """Seeds the SENDER's bucket and opens an ask addressed to `recipient_dir`."""
    sender_slug = _seed_checkpoint(sender_dir, f"S-{sender_dir.rsplit('/', 1)[-1]}")
    if seed_recipient:
        _seed_checkpoint(recipient_dir, f"S-{recipient_dir.rsplit('/', 1)[-1]}")
    to = store.project_slug(recipient_dir)
    return requests.open_request(to=to, ask=ask, why="because",
                                 channel="cli-agent", project_dir=sender_slug)


def test_frozen_wording_of_the_decision_count_line():
    """The two templates are registered public strings, frozen the same way
    the panel headings are (see test_the_two_request_panel_headings_render_
    verbatim). Rename either and this fails — deliberately."""
    assert briefing._DECISION_COUNT_LINE_PLURAL == \
        "{n} decisions waiting on you here{elsewhere} - daimon decide"
    assert briefing._DECISION_COUNT_LINE_SINGULAR == \
        "1 decision waiting on you here{elsewhere} - daimon decide"


def test_here_3_elsewhere_2_renders_the_exact_plural_line(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-a"
    _open_ask(recipient, "one", "/p/dc-s1")
    _open_ask(recipient, "two", "/p/dc-s2", seed_recipient=False)
    _open_ask(recipient, "three", "/p/dc-s3", seed_recipient=False)
    # elsewhere: two asks addressed to a THIRD, unrelated bucket
    _open_ask("/p/dc-other", "four", "/p/dc-s4", seed_recipient=False)
    _open_ask("/p/dc-other", "five", "/p/dc-s5", seed_recipient=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert ("3 decisions waiting on you here (2 elsewhere) - daimon decide"
            in out)


def test_here_1_elsewhere_0_renders_the_singular_line_with_no_parenthetical(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-b"
    _open_ask(recipient, "only one", "/p/dc-s6")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 decision waiting on you here - daimon decide" in out
    assert "elsewhere" not in out


def test_both_zero_renders_no_line_and_no_stray_blank_line(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-c"
    _seed_checkpoint(recipient, "S-dc-recipient-c")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "waiting on you here" not in out
    assert "daimon decide" not in out
    # No stray blank line: the plain-render's skeleton section is absent
    # entirely, so the greeting is followed directly by "Decisions made:"
    # (the only section the seeded checkpoint carries).
    assert "\n\n\nDecisions made:" not in out


def test_here_0_elsewhere_2_still_renders_the_zero_here_line(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # Rule 4 silences BOTH-zero only; a foreign backlog with nothing local
    # still surfaces the elsewhere count.
    recipient = "/p/dc-recipient-d"
    _seed_checkpoint(recipient, "S-dc-recipient-d")
    _open_ask("/p/dc-other-2", "six", "/p/dc-s7", seed_recipient=False)
    _open_ask("/p/dc-other-2", "seven", "/p/dc-s8", seed_recipient=False)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert ("0 decisions waiting on you here (2 elsewhere) - daimon decide"
            in out)


def test_tenant_scoped_omits_the_parenthetical_and_never_calls_foreign_counts(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-e"
    _open_ask(recipient, "only one", "/p/dc-s9")
    _open_ask("/p/dc-other-3", "eight", "/p/dc-s10", seed_recipient=False)

    def _boom(*a, **k):
        raise AssertionError("tenant-scoped brief must never call "
                             "foreign_counts")

    monkeypatch.setattr(pending, "foreign_counts", _boom)
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 decision waiting on you here - daimon decide" in out
    assert "elsewhere" not in out


def test_the_count_line_sits_directly_above_the_request_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-f"
    _open_ask(recipient, "publish the schema", "/p/dc-s11")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    idx_count = next(i for i, ln in enumerate(lines)
                     if "waiting on you here" in ln)
    idx_panel = next(i for i, ln in enumerate(lines)
                     if ln == briefing._REQUEST_PANEL_HEADER)
    assert idx_panel == idx_count + 2  # one blank line between blocks


def test_no_checkpoint_day_one_path_still_renders_the_line(
        tmp_checkpoint_dir):
    # Same precedent as test_render_with_no_checkpoint_still_shows_the_panel
    # (test_request_briefing.py): call briefing.render directly with a None
    # checkpoint rather than through the CLI, since every write_checkpoint
    # ALSO sets the global fallback pointer, which would otherwise route the
    # CLI path away from the b-is-None skeleton branch this test targets.
    recipient = "/p/dc-recipient-g"
    _open_ask(recipient, "publish the schema", "/p/dc-s12",
             seed_recipient=False)
    out = briefing.render(None, project_dir=recipient,
                          worldcheck_project=recipient)
    assert out is not None
    assert "1 decision waiting on you here - daimon decide" in out


def test_render_brief_no_checkpoint_branch_still_renders_the_line(
        tmp_checkpoint_dir, capsys):
    # `briefing.render`'s own b-is-None path is covered above; render.py has
    # a SEPARATE b-is-None branch (render_brief's own, not delegated to
    # briefing.render) that must carry the same line.
    recipient = "/p/dc-recipient-j"
    _open_ask(recipient, "publish the schema", "/p/dc-s15",
             seed_recipient=False)
    render.render_brief(None, project_dir=recipient,
                        worldcheck_project=recipient)
    out = capsys.readouterr().out
    assert "1 decision waiting on you here - daimon decide" in out


def test_slug_path_never_shows_the_count_line(tmp_checkpoint_dir, monkeypatch,
                                              capsys):
    recipient = "/p/dc-recipient-h"
    _open_ask(recipient, "publish the schema", "/p/dc-s13")
    slug = store.project_slug(recipient)

    def _boom(*a, **k):
        raise AssertionError("--slug briefs must never touch the decide "
                             "composer")

    monkeypatch.setattr(pending, "queue", _boom)
    rc = cli.main(["brief", "--slug", slug])
    assert rc == 0
    out = capsys.readouterr().out
    assert "waiting on you here" not in out


def test_fail_open_a_broken_composer_never_breaks_the_brief(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/dc-recipient-i"
    _open_ask(recipient, "publish the schema", "/p/dc-s14")

    def _boom(*a, **k):
        raise RuntimeError("simulated composer failure")

    monkeypatch.setattr(pending, "queue", _boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "waiting on you here" not in out


def test_a_broken_foreign_counts_still_renders_a_good_here_count(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # The two reads fail INDEPENDENTLY: a broken `elsewhere` must not blank
    # a perfectly good `here`.
    recipient = "/p/dc-recipient-k"
    _open_ask(recipient, "publish the schema", "/p/dc-s16")

    def _boom(*a, **k):
        raise RuntimeError("simulated foreign_counts failure")

    monkeypatch.setattr(pending, "foreign_counts", _boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 decision waiting on you here - daimon decide" in out
    assert "elsewhere" not in out


def test_the_count_line_survives_budget_pressure(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Same precedent as test_rulings_survive_budget_pressure
    # (test_ruling_briefing.py): the count line is skeleton furniture,
    # outside _DROP_ORDER, so a budget tight enough to force every droppable
    # cognitive section to trim must not cost this line.
    recipient = "/p/dc-recipient-l"
    _open_ask(recipient, "publish the schema", "/p/dc-s17")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "40")
    out = briefing.render(sample_checkpoint, project_dir=recipient,
                          worldcheck_project=recipient)
    assert out is not None
    assert "1 decision waiting on you here - daimon decide" in out
    # Budget pressure is real, not a no-op: at least one cognitive section
    # actually got trimmed under a 40-token budget.
    assert "trimmed for budget" in out


def test_cli_rich_brief_carries_the_count_line(tmp_checkpoint_dir,
                                               monkeypatch, capsys):
    # Precedent: test_cli_rich_brief_carries_rulings (test_ruling_briefing.py)
    # and test_cli_rich_brief_carries_the_owed_panel (test_cli_brief_owed.py)
    # both monkeypatch render.supports_rich True and read the plain text back
    # through capsys — rich strips styling under a non-terminal stream but
    # the substance text still lands.
    recipient = "/p/dc-recipient-m"
    _open_ask(recipient, "publish the schema", "/p/dc-s18")
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 decision waiting on you here - daimon decide" in out


def test_decision_count_line_returns_none_with_no_project_dir(
        tmp_checkpoint_dir):
    # Mirrors test_panel_empty_when_no_project_dir (test_request_briefing.py)
    # for request_panel_lines: `project_dir=None` is the legacy-caller /
    # hand-built-checkpoint guard, reachable only by calling the function
    # directly — every real caller already checks worldcheck_project first.
    assert briefing.decision_count_line(None) is None
