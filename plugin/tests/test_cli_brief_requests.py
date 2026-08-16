"""#694 PR 2: the request panel + surfaced stamp on the CLI `brief` path.

D2's gate — panel and stamp inject ONLY on the CLI same-project brief path,
keyed on the caller-supplied `worldcheck_project` parameter, never on route
or surface detection. Mirrors test_worldcheck.py's never-probes shape for
--slug / global-fallback: monkeypatch the composer to explode and assert it
is never called on an excluded path.
"""

from daimon_briefing import cli, requests, store


def _seed_checkpoint(project_dir, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir)
    return store.project_slug(project_dir)


def _open_ask(recipient_dir, ask="publish the schema",
             sender_dir="/p/cbr-sender", seed_recipient=True):
    """Seeds the SENDER's bucket and opens an ask addressed to `recipient_dir`.

    Also gives the RECIPIENT its own checkpoint by default: without one,
    `daimon brief` falls into the global-pointer-fallback branch (a project
    with zero checkpoints of its own never reaches `_render_briefing_body`
    at all, so `worldcheck_project` is never computed) — a separate,
    pre-existing #96 behavior this test file is not exercising."""
    sender_slug = _seed_checkpoint(sender_dir, "S-cbr-sender")
    if seed_recipient:
        _seed_checkpoint(recipient_dir, "S-cbr-recipient")
    to = store.project_slug(recipient_dir)
    return requests.open_request(to=to, ask=ask, why="because",
                                 channel="cli-agent", project_dir=sender_slug)


def test_cli_brief_shows_the_panel_on_the_normal_path(tmp_checkpoint_dir,
                                                       monkeypatch, capsys):
    recipient = "/p/cbr-recipient-a"
    _open_ask(recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    assert "publish the schema" in capsys.readouterr().out


def test_cli_brief_stamps_surfaced_after_the_print(tmp_checkpoint_dir,
                                                    monkeypatch, capsys):
    recipient = "/p/cbr-recipient-b"
    q_id = _open_ask(recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.needs_surfaced_stamp(record) is False
    assert set(record["surfaced"]) == {0}


def test_cli_brief_stamp_is_write_once_across_repeated_briefs(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/cbr-recipient-c"
    q_id = _open_ask(recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    assert cli.main(["brief"]) == 0
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert len(record["surfaced"]) == 1


def test_cli_brief_slug_path_never_stamps_or_shows_the_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/cbr-recipient-d"
    _open_ask(recipient)
    slug = store.project_slug(recipient)

    def _boom(*a, **k):
        raise AssertionError("--slug briefs must never touch the composer")

    monkeypatch.setattr(requests, "inbox_renderable", _boom)
    monkeypatch.setattr(requests, "stamp_surfaced", _boom)
    rc = cli.main(["brief", "--slug", slug])
    assert rc == 0
    out = capsys.readouterr().out
    assert "publish the schema" not in out


def test_cli_brief_global_fallback_never_stamps_or_shows_the_panel(
        tmp_checkpoint_dir, monkeypatch, capsys):
    recipient = "/p/cbr-recipient-e"
    # Recipient has no checkpoint of its own, so `brief` falls back to the
    # global pointer — the branch under test.
    _open_ask(recipient, seed_recipient=False)
    # Global pointer belongs to a THIRD, unrelated project.
    store.write_checkpoint("S-other", {
        "session_id": "S-other", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "y", "trust": "inferred"}]},
    })  # no project_dir -> global pointer only
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/cbr-never-seen")

    def _boom(*a, **k):
        raise AssertionError("global-fallback briefs must never touch the "
                             "composer")

    monkeypatch.setattr(requests, "inbox_renderable", _boom)
    monkeypatch.setattr(requests, "stamp_surfaced", _boom)
    rc = cli.main(["brief"])
    assert rc == 0


def test_cli_brief_a_crash_before_the_stamp_leaves_it_unstamped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # D1's safe direction: a failure between print and stamp must re-surface
    # the card next brief, never silently claim it was shown.
    recipient = "/p/cbr-recipient-f"
    q_id = _open_ask(recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)

    def _boom(*a, **k):
        raise RuntimeError("simulated crash between print and stamp")

    monkeypatch.setattr(requests, "stamp_surfaced", _boom)
    rc = cli.main(["brief"])  # fail-open: the brief itself must still succeed
    assert rc == 0
    assert "publish the schema" in capsys.readouterr().out
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.needs_surfaced_stamp(record) is True


def test_cli_brief_no_addressed_requests_shows_no_panel(tmp_checkpoint_dir,
                                                         monkeypatch, capsys):
    recipient = "/p/cbr-recipient-g"
    store.write_checkpoint("S-cbr-g", {
        "session_id": "S-cbr-g", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "z", "trust": "inferred"}]},
    }, project_dir=recipient)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", recipient)
    rc = cli.main(["brief"])
    assert rc == 0
    assert "Requests waiting on you" not in capsys.readouterr().out
