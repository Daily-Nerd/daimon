"""#694 PR 3: the sender-side verdict panel + verdict_surfaced stamp on the
CLI `brief` path. Mirrors test_cli_brief_requests.py (PR 2's incoming panel)
exactly — same D2 gate, same D1 post-print stamp timing.
"""

from daimon_briefing import cli, requests, store


def _seed_checkpoint(project_dir, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir)
    return store.project_slug(project_dir)


def _accepted_ask(sender_dir, ask="publish the schema",
                  recipient_dir="/p/cbv-recipient", seed_sender=True):
    """Seeds the RECIPIENT's bucket, opens an ask FROM sender_dir, and
    accepts it — the sender's brief has a verdict to show."""
    if seed_sender:
        _seed_checkpoint(sender_dir, "S-cbv-sender")
    recipient_slug = _seed_checkpoint(recipient_dir, "S-cbv-recipient")
    q_id = requests.open_request(to=recipient_slug, ask=ask, why="because",
                                 channel="cli-agent", project_dir=sender_dir)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_dir)
    return q_id


def test_cli_brief_shows_the_verdict_panel_on_the_normal_path(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-a"
    _accepted_ask(sender, recipient_dir="/p/cbv-recipient-a")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "publish the schema" in out
    assert "accepted" in out


def test_the_two_request_panel_headings_render_verbatim(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """Both request-panel headings are registered public strings, frozen the
    same way the four older briefing headings are.

    Nothing pinned them before this: the panel tests assert the ASK text, and
    the only line that named a heading was a negative assertion, so a heading
    could have been renamed, mistyped, or dropped and the whole suite would
    still pass. That is how the sender-side heading carried the wrong word
    long enough to reach a reference page and its translation.

    Rename either string and this fails — deliberately. The fix is to change
    the registration first and this assertion second, never the reverse."""
    from daimon_briefing import briefing
    assert briefing._REQUEST_PANEL_HEADER == \
        "Requests waiting on you (from other projects):"
    assert briefing._VERDICT_PANEL_HEADER == "Decisions on requests you sent:"

    sender = "/p/cbv-sender-headings"
    _accepted_ask(sender, recipient_dir="/p/cbv-recipient-headings")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    assert cli.main(["brief"]) == 0
    assert briefing._VERDICT_PANEL_HEADER in capsys.readouterr().out


def test_cli_brief_stamps_verdict_surfaced_after_the_print(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-b"
    q_id = _accepted_ask(sender, recipient_dir="/p/cbv-recipient-b")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    record = requests.sender_join(project_dir=sender)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record) is False
    assert record["verdict_surfaced_at"]


def test_cli_brief_verdict_stamp_is_write_once_across_repeated_briefs(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-c"
    q_id = _accepted_ask(sender, recipient_dir="/p/cbv-recipient-c")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    assert cli.main(["brief"]) == 0
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    rows = [r for r in requests.events(project_dir=sender)
            if r.get("event") == "verdict_surfaced" and r.get("request_id") == q_id]
    assert len(rows) == 1


def test_cli_brief_slug_path_never_stamps_or_shows_the_verdict_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-d"
    _accepted_ask(sender, recipient_dir="/p/cbv-recipient-d")
    slug = store.project_slug(sender)

    def _boom(*a, **k):
        raise AssertionError("--slug briefs must never touch the composer")

    monkeypatch.setattr(requests, "verdict_renderable", _boom)
    monkeypatch.setattr(requests, "stamp_verdict_surfaced", _boom)
    rc = cli.main(["brief", "--slug", slug])
    assert rc == 0
    out = capsys.readouterr().out
    assert "publish the schema" not in out


def test_cli_brief_global_fallback_never_stamps_or_shows_the_verdict_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-e"
    _accepted_ask(sender, recipient_dir="/p/cbv-recipient-e",
                 seed_sender=False)
    store.write_checkpoint("S-other-v", {
        "session_id": "S-other-v", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "y", "trust": "inferred"}]},
    })  # no project_dir -> global pointer only
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/cbv-never-seen")

    def _boom(*a, **k):
        raise AssertionError("global-fallback briefs must never touch the "
                             "composer")

    monkeypatch.setattr(requests, "verdict_renderable", _boom)
    monkeypatch.setattr(requests, "stamp_verdict_surfaced", _boom)
    rc = cli.main(["brief"])
    assert rc == 0


def test_cli_brief_a_crash_before_the_verdict_stamp_leaves_it_unstamped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-f"
    q_id = _accepted_ask(sender, recipient_dir="/p/cbv-recipient-f")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)

    def _boom(*a, **k):
        raise RuntimeError("simulated crash between print and stamp")

    monkeypatch.setattr(requests, "stamp_verdict_surfaced", _boom)
    rc = cli.main(["brief"])
    assert rc == 0
    assert "publish the schema" in capsys.readouterr().out
    record = requests.sender_join(project_dir=sender)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record) is True


def test_cli_brief_no_decided_requests_shows_no_verdict_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    sender = "/p/cbv-sender-g"
    _seed_checkpoint(sender, "S-cbv-g")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", sender)
    rc = cli.main(["brief"])
    assert rc == 0
    assert "Decisions on requests you sent" not in capsys.readouterr().out


def test_cli_brief_shows_both_panels_together(tmp_checkpoint_dir,
                                              monkeypatch, capsys):
    # #694: PR 2's incoming panel and PR 3's verdict panel are independent
    # sections on the SAME project — a project can be both sender and
    # recipient at once.
    project = "/p/cbv-both"
    _seed_checkpoint(project, "S-cbv-both")
    sent_id = _accepted_ask(project, ask="the sent ask",
                            recipient_dir="/p/cbv-both-recipient",
                            seed_sender=False)
    incoming_sender = "/p/cbv-both-incoming-sender"
    _seed_checkpoint(incoming_sender, "S-cbv-both-incoming")
    requests.open_request(to=store.project_slug(project),
                          ask="the incoming ask", why="because",
                          channel="cli-agent", project_dir=incoming_sender)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "the sent ask" in out
    assert "the incoming ask" in out
    assert sent_id  # keeps the linter from flagging an unused id
