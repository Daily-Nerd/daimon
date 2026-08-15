"""#693: pinned rulings — positive-polarity records on the refutation ledger.

Polarity lives in the FOUNDING EVENT NAME (`ruled` vs `asserted`), never in a
caller-supplied field: an older reader's `events()` drops unknown event names
and its fold treats the orphan lifecycle rows as inert, so an old install
never renders a ruling as a refutation. The lifecycle is deliberately
polarity-asymmetric: for a refutation, demotion fails toward LESS constraint;
for a ruling the identical mechanic would remove a standing human constraint
at agent initiative, so agents get proposal events and only human channels
change what renders.
"""
import pytest

from daimon_briefing import refutations


PROJECT = "/p/rulings"


def _rule(*, channel="cli-agent", ratified=False, **overrides):
    values = {
        "subject": "public posts",
        "verdict": "internal numbers never appear in public posts",
        "scope": "publishing",
        "evidence": ["issue:693"],
        "channel": channel,
        "ratified": ratified,
        "project_dir": PROJECT,
    }
    values.update(overrides)
    return refutations.assert_ruling(**values)


def _refute(**overrides):
    values = {
        "subject": "single-pass migration",
        "verdict": "it deadlocked under concurrent writes",
        "scope": "migrations",
        "evidence": ["measurement:deadlock"],
        "channel": "cli-agent",
        "project_dir": PROJECT,
    }
    values.update(overrides)
    return refutations.assert_refutation(**values)


def test_ruled_founds_a_candidate_with_ruling_polarity(tmp_checkpoint_dir):
    ruling_id = _rule()
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["polarity"] == "ruling"


def test_refutations_fold_with_refutation_polarity(tmp_checkpoint_dir):
    ref_id = _refute()
    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["polarity"] == "refutation"


def test_human_founding_with_ratify_activates_and_stamps_activated_at(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activated_at"]


def test_an_old_reader_drops_ruled_rows_and_their_lifecycle_is_inert(
        tmp_checkpoint_dir, monkeypatch):
    # An old install's EVENTS has no `ruled`: the founding row is dropped at
    # admission and the later human ratify becomes an orphan the fold skips.
    ruling_id = _rule()
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    old_events = frozenset(e for e in refutations.EVENTS if e not in (
        "ruled", "revision-proposed"))
    monkeypatch.setattr(refutations, "EVENTS", old_events)
    assert refutations.get(ruling_id, project_dir=PROJECT) is None


def test_human_ratify_activates_a_ruling(tmp_checkpoint_dir):
    ruling_id = _rule()
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activated_at"]


def test_mechanical_activated_is_inert_on_a_ruling(tmp_checkpoint_dir):
    ruling_id = _rule()
    row = refutations._stamp("activated", ruling_id, "mechanical")
    assert refutations.append(row, project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_agent_revised_row_is_inert_on_an_active_ruling(tmp_checkpoint_dir):
    # Fold-enforced, not CLI convention: a hand-appended agent-authority
    # `revised` row must not demote or rewrite an active ruling.
    ruling_id = _rule(channel="cli-tty", ratified=True)
    row = refutations._stamp("revised", ruling_id, "cli-agent")
    row["verdict"] = "the opposite of the standing rule"
    assert refutations.append(row, project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["verdict"] == "internal numbers never appear in public posts"


def test_a_retired_ruling_cannot_be_resurrected_by_ratified_revise(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError):
        refutations.revise(
            ruling_id, channel="cli-tty", evidence=["issue:693"],
            verdict="revived text", ratified=True, project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "overturned"


def test_ruling_text_is_capped_at_write_time(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError):
        _rule(verdict="x" * 281)


def test_human_retire_needs_no_evidence(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "overturned"


def test_agent_retire_leaves_the_ruling_active(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.retire(ruling_id, channel="cli-agent", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record.get("overturn_proposed")


def test_ratify_bound_to_a_stale_verdict_key_does_not_activate(
        tmp_checkpoint_dir):
    # The confirm-window attack: an agent revision lands between the print
    # and the append. The ratify row carries the key of the text it
    # DISPLAYED; the fold refuses to activate any other text.
    from daimon_briefing import normalize
    ruling_id = _rule()
    displayed_key = normalize.content_key(
        "internal numbers never appear in public posts")
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="the opposite rule entirely", project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty",
                       verdict_key=displayed_key, project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_ratify_with_matching_verdict_key_activates(tmp_checkpoint_dir):
    from daimon_briefing import normalize
    ruling_id = _rule()
    refutations.ratify(
        ruling_id, channel="cli-tty",
        verdict_key=normalize.content_key(
            "internal numbers never appear in public posts"),
        project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"


def test_absent_key_ratify_rows_activate_normally(tmp_checkpoint_dir):
    # Backward compat: every pre-existing ledger row is absent-key.
    ref_id = _refute()
    refutations.ratify(ref_id, channel="cli-tty", project_dir=PROJECT)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "active"


def test_the_cap_refuses_activation_past_n_active_rulings(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")
    for n in range(2):
        _rule(subject=f"area {n}", verdict=f"standing rule number {n}",
              scope=f"scope-{n}", channel="cli-tty", ratified=True)
    third = _rule(subject="area 2", verdict="standing rule number 2",
                  scope="scope-2")
    with pytest.raises(refutations.RefutationError):
        refutations.ratify(third, channel="cli-tty", project_dir=PROJECT)
    # Candidates are never counted or blocked.
    _rule(subject="area 3", verdict="standing rule number 3", scope="scope-3")


def test_revise_ratify_path_is_also_cap_guarded(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_RULING_CAP", "1")
    _rule(subject="area a", verdict="rule a", scope="scope-a",
          channel="cli-tty", ratified=True)
    second = _rule(subject="area b", verdict="rule b", scope="scope-b")
    with pytest.raises(refutations.RefutationError):
        refutations.revise(
            second, channel="cli-tty", evidence=["issue:693"],
            verdict="rule b sharpened", ratified=True, project_dir=PROJECT)


def test_agent_revise_on_an_active_ruling_becomes_a_proposal(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a sharper phrasing of the rule", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["verdict"] == "internal numbers never appear in public posts"
    assert record["revision_proposed"]["verdict"] == (
        "a sharper phrasing of the rule")


def test_a_fourth_open_proposal_is_refused(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    for n in range(3):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:693"],
            verdict=f"proposal number {n}", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:693"],
            verdict="proposal number 3", project_dir=PROJECT)


def test_human_revise_preserves_active_and_does_not_restamp_activated_at(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    before = refutations.get(ruling_id, project_dir=PROJECT)["activated_at"]
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:693"],
        verdict="the rule with a typo fixed", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["verdict"] == "the rule with a typo fixed"
    assert record["activated_at"] == before


def test_text_authored_by_tracks_the_text_not_the_touch(tmp_checkpoint_dir):
    ruling_id = _rule()  # agent-authored text
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["text_authored_by"] == "agent"
    # A human edit that carries no text key must not relabel the text.
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:693"],
        scope="publishing widened", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["text_authored_by"] == "agent"
    # A human edit that rewrites the text does.
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:693"],
        verdict="the human phrasing of the rule", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["text_authored_by"] == "human"


def test_proposals_do_not_move_a_rulings_updated_at(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    before = refutations.get(ruling_id, project_dir=PROJECT)["updated_at"]
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a proposal", project_dir=PROJECT)
    refutations.retire(ruling_id, channel="cli-agent", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["updated_at"] == before


def test_guard_never_returns_rulings(tmp_checkpoint_dir):
    _rule(channel="cli-tty", ratified=True,
          subject="guard subject shared", scope="scope-r",
          anchors=["issue:693"])
    hits = refutations.guard("does issue:693 allow this",
                             project_dir=PROJECT)
    assert hits == []


def test_forget_doomed_set_derives_from_raw_lines(
        tmp_checkpoint_dir, monkeypatch):
    # A reader whose EVENTS predates `ruled` must still DELETE ruled rows:
    # the doomed set comes from raw lines, not the events() admission filter.
    from daimon_briefing import normalize
    ruling_id = _rule()
    old_events = frozenset(e for e in refutations.EVENTS if e not in (
        "ruled", "revision-proposed"))
    monkeypatch.setattr(refutations, "EVENTS", old_events)
    removed = refutations.forget_content_key(
        normalize.content_key("internal numbers never appear in public posts"),
        project_dir=PROJECT)
    assert ruling_id in removed
    path = refutations._path(PROJECT)
    assert "internal numbers" not in (
        path.read_text(encoding="utf-8") if path.exists() else "")


# --- CLI surface -----------------------------------------------------------

from daimon_briefing import cli  # noqa: E402


@pytest.fixture
def _tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def test_cli_propose_records_a_candidate_without_escalation_hint(
        tmp_checkpoint_dir, capsys):
    rc = cli.main([
        "ruling", "propose", "--subject", "public posts",
        "--verdict", "internal numbers never appear in public posts",
        "--scope", "publishing", "--evidence", "issue:693",
        "--by", "agent", "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "candidate" in out
    assert "ruling ratify" not in out


def test_cli_ratify_prints_text_discloses_render_and_confirms(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    ruling_id = _rule()
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "internal numbers never appear in public posts" in out
    assert "render into every future session" in out
    assert refutations.get(ruling_id, project_dir=PROJECT)["state"] == "active"


def test_cli_ratify_declined_leaves_the_candidate(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    ruling_id = _rule()
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    assert refutations.get(
        ruling_id, project_dir=PROJECT)["state"] == "candidate"


def test_cli_ratify_is_loud_when_the_text_changed_mid_confirmation(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    ruling_id = _rule()

    def _inject_revision(prompt=""):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:693"],
            verdict="the opposite rule entirely", project_dir=PROJECT)
        return "y"

    monkeypatch.setattr("builtins.input", _inject_revision)
    rc = cli.main(["ruling", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    out = capsys.readouterr().out
    assert "changed during confirmation" in out
    assert refutations.get(
        ruling_id, project_dir=PROJECT)["state"] == "candidate"


def test_cli_refute_verbs_refuse_ruling_ids(
        tmp_checkpoint_dir, _tty, capsys):
    ruling_id = _rule()
    rc = cli.main(["refute", "ratify", ruling_id, "--project", PROJECT])
    assert rc == 1
    assert "daimon ruling" in capsys.readouterr().out


def test_cli_lists_are_polarity_scoped(tmp_checkpoint_dir, capsys):
    _rule(channel="cli-tty", ratified=True)
    _refute()
    assert cli.main(["refute", "list", "--project", PROJECT]) == 0
    refute_out = capsys.readouterr().out
    assert "single-pass migration" in refute_out
    assert "internal numbers" not in refute_out
    assert cli.main(["ruling", "list", "--project", PROJECT]) == 0
    ruling_out = capsys.readouterr().out
    assert "internal numbers" in ruling_out
    assert "single-pass migration" not in ruling_out


def test_cli_search_returns_both_polarities_labelled(
        tmp_checkpoint_dir, capsys):
    _rule(channel="cli-tty", ratified=True,
          subject="shared area", scope="scope-r",
          verdict="the standing rule about the shared area")
    _refute(subject="shared area findings",
            verdict="the shared area approach deadlocked", scope="scope-f")
    assert cli.main(["refute", "search", "shared", "area",
                     "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "the standing rule about the shared area" in out
    assert "shared area findings" in out


def test_cli_show_renders_a_retired_ruling_as_retired(
        tmp_checkpoint_dir, _tty, capsys):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "retired" in out
    assert "overturned" not in out


# --- review round 1 findings ----------------------------------------------


def test_viewer_refutations_lane_excludes_rulings(tmp_checkpoint_dir):
    # The lane renders `refute list`; the polarity parameter exists so the
    # viewer and the CLI cannot drift apart. PR2 adds a rulings lane; the
    # FILTER belongs to the commit that shipped the polarity.
    from daimon_ui import server as ui_server
    _rule(channel="cli-tty", ratified=True)
    _refute()
    rows = refutations.listing(polarity="refutation", project_dir=PROJECT)
    assert all(r.get("polarity") == "refutation" for r in rows)
    import inspect
    assert 'polarity="refutation"' in inspect.getsource(ui_server)


def test_cli_ruling_revise_refuses_ratify_on_a_candidate(
        tmp_checkpoint_dir, _tty, capsys):
    # The ratification ceremony (full text, disclosure, confirm, key binding)
    # lives in `ruling ratify`; `revise --ratify` walking around it would
    # activate text the human was never shown.
    ruling_id = _rule()
    rc = cli.main(["ruling", "revise", ruling_id,
                   "--verdict", "sharpened text", "--evidence", "issue:693",
                   "--ratify", "--project", PROJECT])
    assert rc == 1
    out = capsys.readouterr().out
    assert "ruling ratify" in out
    assert refutations.get(
        ruling_id, project_dir=PROJECT)["state"] == "candidate"


def test_cli_revising_an_active_rulings_text_requires_confirmation(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = cli.main(["ruling", "revise", ruling_id,
                   "--verdict", "a different rule", "--evidence", "issue:693",
                   "--project", PROJECT])
    assert rc == 1
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["verdict"] == "internal numbers never appear in public posts"
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["ruling", "revise", ruling_id,
                   "--verdict", "a different rule", "--evidence", "issue:693",
                   "--project", PROJECT])
    assert rc == 0
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["verdict"] == "a different rule"
    assert record["state"] == "active"


def test_forget_refuses_an_active_ruling_without_a_terminal(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # `forget` takes no --by and no confirm; without this gate it is an
    # agent path that un-renders a human-ratified standing constraint.
    from daimon_briefing import store
    ruling_id = _rule(channel="cli-tty", ratified=True)
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "unrelated", "trust": "inferred"}]},
    }, project_dir=PROJECT)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False,
                        raising=False)
    rc = cli.main(["forget", "internal numbers never appear in public posts",
                   "--project", PROJECT])
    assert rc == 1
    assert "ruling" in capsys.readouterr().out
    assert refutations.get(ruling_id, project_dir=PROJECT) is not None


def test_forget_from_a_terminal_still_reaches_a_ruling(
        tmp_checkpoint_dir, _tty, capsys):
    from daimon_briefing import store
    ruling_id = _rule(channel="cli-tty", ratified=True)
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "unrelated", "trust": "inferred"}]},
    }, project_dir=PROJECT)
    rc = cli.main(["forget", "internal numbers never appear in public posts",
                   "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ruling" in out  # the receipt names what it removed
    assert refutations.get(ruling_id, project_dir=PROJECT) is None


def test_search_lines_carry_the_polarity_word(tmp_checkpoint_dir, capsys):
    _rule(channel="cli-tty", ratified=True,
          subject="shared area", scope="scope-r",
          verdict="the standing rule about the shared area")
    _refute(subject="shared area findings",
            verdict="the shared area approach deadlocked", scope="scope-f")
    assert cli.main(["refute", "search", "shared", "area",
                     "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "[ruling" in out
    assert "[refutation" in out


def test_module_overturn_refuses_a_ruling_id(tmp_checkpoint_dir):
    # Fold-and-module-enforced, not CLI convention: in-process writers reach
    # overturn() directly.
    ruling_id = _rule(channel="cli-tty", ratified=True)
    with pytest.raises(refutations.RefutationError):
        refutations.overturn(
            ruling_id, channel="cli-tty", evidence=["issue:693"],
            project_dir=PROJECT)


def test_age_and_order_survive_proposals_across_real_seconds(
        tmp_checkpoint_dir, monkeypatch):
    # The earlier assertions all wrote inside one second, so they compared
    # X == X regardless of the code. Step the clock so they bite.
    base = refutations.time.time_ns()
    ticks = iter(range(1, 50))
    monkeypatch.setattr(
        refutations.time, "time_ns",
        lambda: base + next(ticks) * 2_000_000_000)
    ruling_id = _rule(channel="cli-tty", ratified=True)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    before_updated, before_activated = (
        record["updated_at"], record["activated_at"])
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a proposal", project_dir=PROJECT)
    refutations.retire(ruling_id, channel="cli-agent", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["updated_at"] == before_updated
    assert record["activated_at"] == before_activated
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:693"],
        scope="publishing widened", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["updated_at"] != before_updated
    assert record["activated_at"] == before_activated


def test_ruling_list_surfaces_over_cap_state(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_RULING_CAP", "3")
    for n in range(3):
        _rule(subject=f"area {n}", verdict=f"standing rule number {n}",
              scope=f"scope-{n}", channel="cli-tty", ratified=True)
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")
    assert cli.main(["ruling", "list", "--project", PROJECT]) == 0
    assert "over cap" in capsys.readouterr().out
