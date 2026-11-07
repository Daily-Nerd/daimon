"""#1281: the wall-clock `overdue` signal for requests the sender still has
open. Derived on read from the folded record and the injected clock, never
written, and independent of whether the recipient ever runs a session."""

import json

import pytest

from daimon_briefing import briefing, cli, clock, config, requests, store
from daimon_briefing.surfaces import Writer

SENDER = "/p/overdue-sender"
RECIPIENT = "/p/overdue-recipient"
T0 = 1_700_000_000 * 10 ** 9          # a simulated calendar, far from "now"
DAY = 86_400 * 10 ** 9


def at(offset_days):
    """The simulated clock reading `offset_days` after T0."""
    return clock.use(clock.StepClock(T0 + int(offset_days * DAY)))


def _ask(ask="publish the schema", **kw):
    with at(0):
        return requests.open_request(
            to=store.project_slug(RECIPIENT), ask=ask, why="needed",
            channel="cli-agent", project_dir=SENDER, **kw)


def _record(q_id):
    return requests.sender_join(project_dir=SENDER)[q_id]


def _overdue_at(q_id, days):
    with at(days):
        return requests.is_overdue(_record(q_id))


# ---- config ----------------------------------------------------------------

def test_request_overdue_days_default_override_malformed(monkeypatch):
    monkeypatch.delenv("DAIMON_REQUEST_OVERDUE_DAYS", raising=False)
    assert config.request_overdue_days() == 7.0
    monkeypatch.setenv("DAIMON_REQUEST_OVERDUE_DAYS", "2.5")
    assert config.request_overdue_days() == 2.5
    monkeypatch.setenv("DAIMON_REQUEST_OVERDUE_DAYS", "garbage")
    assert config.request_overdue_days() == 7.0


# ---- the derived state -----------------------------------------------------

def test_an_ask_nobody_surfaced_goes_overdue_on_the_clock_alone(
        tmp_checkpoint_dir):
    q_id = _ask()
    record = _record(q_id)
    assert not record["surfaced"] and not record["delivered"]
    assert _overdue_at(q_id, 6) is False
    assert _overdue_at(q_id, 8) is True


def test_an_accepted_ask_with_no_done_goes_overdue(tmp_checkpoint_dir):
    q_id = _ask()
    with at(1):
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    assert _overdue_at(q_id, 7) is False
    assert _overdue_at(q_id, 9) is True


def test_a_needs_info_ask_goes_overdue(tmp_checkpoint_dir):
    q_id = _ask()
    with at(1):
        requests.needs_info(q_id, channel="cli-tty", note="which one?",
                            project_dir=RECIPIENT)
    assert _overdue_at(q_id, 9) is True


def test_done_and_rejected_are_never_overdue(tmp_checkpoint_dir):
    done_id = _ask("finished work")
    rejected_id = _ask("refused work")
    with at(1):
        requests.done(done_id, channel="cli-tty", evidence="shipped",
                      project_dir=RECIPIENT)
        requests.reject(rejected_id, channel="cli-tty", note="no",
                        project_dir=RECIPIENT)
    assert _overdue_at(done_id, 400) is False
    assert _overdue_at(rejected_id, 400) is False


def test_a_reply_resets_the_age(tmp_checkpoint_dir):
    q_id = _ask()
    with at(1):
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    with at(6):
        requests.reply(q_id, "halfway there", channel="cli-agent",
                       project_dir=RECIPIENT)
    assert _overdue_at(q_id, 10) is False     # 4 days since the reply
    assert _overdue_at(q_id, 14) is True      # 8 days since the reply


def test_a_revise_resets_the_age(tmp_checkpoint_dir):
    q_id = _ask()
    with at(5):
        requests.revise(q_id, channel="cli-agent", why="sharper reason",
                        project_dir=SENDER)
    assert _overdue_at(q_id, 9) is False      # 4 days since the revise
    assert _overdue_at(q_id, 13) is True


def test_a_done_claim_resets_the_age(tmp_checkpoint_dir):
    q_id = _ask()
    with at(1):
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    with at(6):
        requests.done(q_id, channel="cli-agent", evidence="quote",
                      project_dir=RECIPIENT)
    record_state = _record(q_id)["state"]
    assert record_state == "done"             # accepted + claim settles it
    assert _overdue_at(q_id, 20) is False


def test_the_threshold_comes_from_the_environment(tmp_checkpoint_dir,
                                                  monkeypatch):
    q_id = _ask()
    assert _overdue_at(q_id, 2) is False
    monkeypatch.setenv("DAIMON_REQUEST_OVERDUE_DAYS", "1")
    assert _overdue_at(q_id, 2) is True
    monkeypatch.setenv("DAIMON_REQUEST_OVERDUE_DAYS", "30")
    assert _overdue_at(q_id, 20) is False


def test_overdue_reads_the_injected_clock_not_the_wall_clock(
        tmp_checkpoint_dir, monkeypatch):
    """The ask is stamped in 2023. If `now` came from the wall clock it
    would read years overdue; under a simulated clock it is one day old."""
    q_id = _ask()
    monkeypatch.setattr("time.time", lambda: pytest.fail("wall clock read"))
    monkeypatch.setattr("time.time_ns",
                        lambda: pytest.fail("wall clock read"))
    with at(1):
        assert requests.is_overdue(_record(q_id)) is False
        live = requests.liveness(_record(q_id))
    assert 0 < live["age_seconds"] < 2 * 86_400


def test_liveness_carries_the_last_movement_age_and_flag(tmp_checkpoint_dir):
    q_id = _ask()
    with at(1):
        requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
    with at(10):
        live = requests.liveness(_record(q_id))
    assert live["last_movement_at"] == _record(q_id)["updated_at"]
    assert live["overdue"] is True
    assert 8.9 * 86_400 < live["age_seconds"] < 9.1 * 86_400


def test_a_record_with_no_readable_movement_time_is_not_called_overdue():
    record = {"state": "open", "updated_at": None}
    assert requests.liveness(record) == {
        "last_movement_at": None, "age_seconds": None, "overdue": False}
    assert requests.is_overdue({"state": "open", "updated_at": "garbage"}) \
        is False


def test_stale_keeps_its_own_meaning(tmp_checkpoint_dir):
    """Never surfaced -> no anchor -> never stale, however old. Overdue is
    the new, separate question."""
    q_id = _ask()
    with at(400):
        record = _record(q_id)
        assert requests.is_stale(record, project_dir=RECIPIENT) is False
        assert requests.is_overdue(record) is True


# ---- `request list` --------------------------------------------------------

def test_request_list_json_gains_the_liveness_fields(
        tmp_checkpoint_dir, monkeypatch, capsys):
    q_id = _ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", SENDER)
    with at(8):
        assert cli.main(["request", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    row = next(r for r in rows if r["request_id"] == q_id)
    assert row["overdue"] is True
    assert row["last_movement_at"] == row["updated_at"]
    assert 7.9 * 86_400 < row["age_seconds"] < 8.1 * 86_400
    assert row["state"] == "open" and row["ask"] == "publish the schema"


def test_request_list_text_marks_the_overdue_row(
        tmp_checkpoint_dir, monkeypatch, capsys):
    old = _ask("an old ask")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", SENDER)
    with at(8):
        assert cli.main(["request", "list"]) == 0
    out = capsys.readouterr().out
    assert old in out
    assert "OVERDUE" in out
    assert "last moved 8d ago" in out


def test_request_list_text_is_quiet_about_a_fresh_row(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _ask("a fresh ask")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", SENDER)
    with at(1):
        assert cli.main(["request", "list"]) == 0
    out = capsys.readouterr().out
    assert "OVERDUE" not in out
    assert "last moved 1d ago" in out


def test_request_inbox_json_is_unchanged(tmp_checkpoint_dir, monkeypatch,
                                         capsys):
    """The recipient side keeps its shape: overdue is the sender's signal."""
    _ask()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", RECIPIENT)
    assert cli.main(["request", "inbox", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and all("overdue" not in r for r in rows)


# ---- the sender's briefing panel -------------------------------------------

def test_the_panel_lists_overdue_asks_one_line_each(tmp_checkpoint_dir):
    q_id = _ask("port the model")
    with at(9):
        lines = briefing.overdue_panel_lines(SENDER)
    assert lines[0] == briefing._OVERDUE_PANEL_HEADER
    body = [ln for ln in lines[1:] if q_id in ln]
    assert len(body) == 1
    assert "port the model" in body[0]
    assert "9d" in body[0] and "open" in body[0]
    assert store.project_slug(RECIPIENT) in body[0]


def test_the_panel_is_empty_when_nothing_is_overdue(tmp_checkpoint_dir):
    _ask()
    with at(2):
        assert briefing.overdue_panel_lines(SENDER) == []
    assert briefing.overdue_panel_lines(None) == []


def test_the_panel_is_capped_with_a_counted_overflow(tmp_checkpoint_dir):
    with clock.use(clock.StepClock(T0, step_ns=60 * 10 ** 9)):
        for i in range(requests.RENDER_CAP + 2):
            requests.open_request(
                to=store.project_slug(RECIPIENT), ask=f"ask number {i}",
                why="w", channel="cli-agent", project_dir=SENDER)
    with at(10):
        lines = briefing.overdue_panel_lines(SENDER)
    cards = [ln for ln in lines[1:] if not ln.startswith(" ")]
    assert len(cards) == requests.RENDER_CAP
    assert any("+2 more overdue" in ln and "daimon request list" in ln
               for ln in lines)


def test_the_longest_waiting_ask_is_listed_first(tmp_checkpoint_dir):
    older = _ask("older ask")
    with clock.use(clock.StepClock(T0 + 3 * DAY)):
        newer = requests.open_request(
            to=store.project_slug(RECIPIENT), ask="newer ask", why="w",
            channel="cli-agent", project_dir=SENDER)
    with at(12):
        lines = briefing.overdue_panel_lines(SENDER)
    ids = [ln.split()[1] for ln in lines[1:] if ln.startswith("!")]
    assert ids == [older, newer]


def test_an_ask_the_verdict_panel_shows_is_not_repeated(tmp_checkpoint_dir):
    shown = _ask("accepted, in the verdict panel")
    quiet = _ask("open, nobody answered")
    with at(1):
        requests.accept(shown, channel="cli-tty", project_dir=RECIPIENT)
    with at(10):
        verdict_lines, verdict_cards = briefing.verdict_panel(SENDER)
        assert shown in "\n".join(verdict_lines)
        shown_ids = {c.request_id for c in verdict_cards}
        lines = briefing.overdue_panel_lines(SENDER, shown_ids=shown_ids)
    body = "\n".join(lines)
    assert quiet in body
    assert shown not in body


def test_a_verdict_overflow_ask_is_not_treated_as_shown(tmp_checkpoint_dir):
    """Only the rows the verdict panel printed count as shown; one it cut to
    its overflow line still owes the sender a look."""
    ids = []
    for i in range(requests.RENDER_CAP + 1):
        with clock.use(clock.StepClock(T0 + i * 60 * 10 ** 9)):
            q_id = requests.open_request(
                to=store.project_slug(RECIPIENT), ask=f"accepted {i}",
                why="w", channel="cli-agent", project_dir=SENDER)
            requests.accept(q_id, channel="cli-tty", project_dir=RECIPIENT)
        ids.append(q_id)
    with at(10):
        verdict_lines, verdict_cards = briefing.verdict_panel(SENDER)
        shown_ids = {c.request_id for c in verdict_cards}
        lines = briefing.overdue_panel_lines(SENDER, shown_ids=shown_ids)
    assert len(shown_ids) == requests.RENDER_CAP
    (cut,) = set(ids) - shown_ids
    assert cut in "\n".join(lines)


def test_a_recipient_mute_does_not_hide_the_ask_from_its_sender(
        tmp_checkpoint_dir):
    """Suppression is the recipient's panel attention (D5); the sender's join
    never learns of it, and a muted ask is exactly one nobody is moving."""
    q_id = _ask()
    with at(1):
        requests.suppress(q_id, channel="cli-tty", project_dir=RECIPIENT)
    with at(10):
        assert q_id in "\n".join(briefing.overdue_panel_lines(SENDER))


def test_collapsed_panel_points_at_request_list():
    assert briefing._PANEL_POINTERS[3] == "daimon request list"


def _seed_checkpoint(project_dir, session):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)


def test_cli_brief_shows_the_overdue_panel(tmp_checkpoint_dir, monkeypatch,
                                           capsys):
    _seed_checkpoint(SENDER, "S-overdue-sender")
    q_id = _ask("chase this one")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", SENDER)
    with at(9):
        assert cli.main(["brief"]) == 0
    out = capsys.readouterr().out
    assert briefing._OVERDUE_PANEL_HEADER in out
    assert q_id in out


def test_the_overdue_panel_renders_through_the_pure_renderer(
        tmp_checkpoint_dir):
    """`render_plain` is the path the host API, MCP and Hermes briefings
    share, so the panel has to ride it."""
    b = {"working_context": {}}
    panel = [briefing._OVERDUE_PANEL_HEADER, "! q-x  some ask  [open, 9d]"]
    text = briefing.render_plain(b, overdue_lines=panel)
    assert briefing._OVERDUE_PANEL_HEADER in text


def test_the_panel_fails_open_like_its_siblings(tmp_checkpoint_dir,
                                                monkeypatch):
    """A panel is furniture: a composer error must not cost the briefing."""
    def boom(*a, **kw):
        raise RuntimeError("ledger exploded")
    monkeypatch.setattr(requests, "overdue_renderable", boom)
    assert briefing.overdue_panel_lines(SENDER) == []


def test_the_rich_brief_prints_the_overdue_panel(capsys):
    from daimon_briefing import render
    render._rich_brief({"open_loops": []}, overdue_lines=[
        briefing._OVERDUE_PANEL_HEADER, "! q-x  some ask  [open, 9d, to y]"])
    out = capsys.readouterr().out
    assert "nobody is moving" in out and "q-x" in out
