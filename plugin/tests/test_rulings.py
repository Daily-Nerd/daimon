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
import hashlib
import json
import os
from pathlib import Path

import pytest

from daimon_briefing import config, redact, refutations, store


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


def test_cli_propose_refusal_over_cap_scope_names_the_artifact_destination(
        tmp_checkpoint_dir, capsys):
    """#920: an over-cap `scope` on `ruling propose` names an artifact
    pointer and the checkpoint, the same shape #916 gave `request open` —
    but never `daimon log` (nothing reads a ref-less note back).

    `scope` is the field to exercise here, not `subject`/`verdict`: a
    ruling's subject and verdict are ALSO bound by `_guard_ruling_text`'s
    280-char `_MAX_RULING_TEXT` (#693, "a standing rule that long is a
    document, not a ruling"), which is stricter than and fires before the
    general 2000-char `_text` cap this fix targets — so the over-cap
    `RefutationTooLong` branch is unreachable for those two fields on a
    RULING specifically (it is still reachable for a plain refutation via
    `refute add`, which has no such guard; see test_refutations.py)."""
    long_scope = "x" * (refutations._MAX_TEXT + 1)
    rc = cli.main([
        "ruling", "propose", "--subject", "public posts",
        "--verdict", "internal numbers never appear in public posts",
        "--scope", long_scope,
        "--evidence", "issue:693", "--by", "agent", "--project", PROJECT])
    assert rc == 1
    out = capsys.readouterr().out
    assert "is too long" in out
    assert "pointer" in out
    assert "write-checkpoint" in out
    assert "daimon-end" in out
    assert "daimon log" not in out


def test_cli_propose_normal_length_records_with_no_destination_text(
        tmp_checkpoint_dir, capsys):
    rc = cli.main([
        "ruling", "propose", "--subject", "public posts",
        "--verdict", "internal numbers never appear in public posts",
        "--scope", "publishing", "--evidence", "issue:693",
        "--by", "agent", "--project", PROJECT])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pointer" not in out
    assert "write-checkpoint" not in out


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
    # FILTER belongs to the commit that shipped the polarity. Asserts on the
    # ROWS the endpoint payload serves — a source grep passed with the bug
    # reintroduced (review round 2, mutation-proved).
    from daimon_ui import server as ui_server
    ruling_id = _rule(channel="cli-tty", ratified=True)
    ref_id = _refute()
    payload = ui_server._refutations_payload(PROJECT)
    ids = {r["refutation_id"] for r in payload["rows"]}
    assert ref_id in ids
    assert ruling_id not in ids


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
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    from daimon_briefing import store
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
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


def test_module_revise_never_activates_a_candidate_ruling(tmp_checkpoint_dir):
    # In-process writers reach revise() directly; activation without the
    # ceremony would be the revise --ratify side door one layer down. The
    # only activation paths are ratify() and found-with-ratify.
    ruling_id = _rule()
    with pytest.raises(refutations.RefutationError):
        refutations.revise(
            ruling_id, channel="cli-tty", evidence=["issue:693"],
            verdict="activated with no ceremony", ratified=True,
            project_dir=PROJECT)
    assert refutations.get(
        ruling_id, project_dir=PROJECT)["state"] == "candidate"


def test_destructive_forget_of_an_active_ruling_confirms_at_the_tty(
        tmp_checkpoint_dir, _tty, monkeypatch, capsys):
    # Deleting what renders is strictly more power than rewriting it, and
    # rewriting confirms. Declining writes nothing.
    from daimon_briefing import store
    ruling_id = _rule(channel="cli-tty", ratified=True)
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "unrelated", "trust": "inferred"}]},
    }, project_dir=PROJECT)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = cli.main(["forget", "internal numbers never appear in public posts",
                   "--project", PROJECT])
    assert rc == 1
    assert refutations.get(ruling_id, project_dir=PROJECT) is not None
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    rc = cli.main(["forget", "internal numbers never appear in public posts",
                   "--project", PROJECT])
    assert rc == 0
    assert refutations.get(ruling_id, project_dir=PROJECT) is None


def test_an_inert_key_mismatched_ratify_does_not_reset_the_proposal_bound(
        tmp_checkpoint_dir):
    # The adversary controls when a human ratify lands inert (revise during
    # the confirm window); an inert verdict must not hand back a proposal
    # slot. An honoured ratify still resets the bound.
    from daimon_briefing import normalize
    ruling_id = _rule(channel="cli-tty", ratified=True)
    for n in range(3):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:693"],
            verdict=f"proposal number {n}", project_dir=PROJECT)
    stale_key = normalize.content_key("a text that was never displayed")
    refutations.ratify(ruling_id, channel="cli-tty",
                       verdict_key=stale_key, project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:693"],
            verdict="a fourth proposal", project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a proposal after an honoured verdict", project_dir=PROJECT)


# --- error, json, and printer paths (patch coverage) -----------------------


def test_propose_collision_points_at_the_holding_polarity(tmp_checkpoint_dir):
    _rule()
    with pytest.raises(refutations.RefutationError, match="ruling revise"):
        _rule()
    _refute()
    with pytest.raises(refutations.RefutationError, match="refute revise"):
        refutations.assert_ruling(
            subject="single-pass migration", verdict="a rule",
            scope="migrations", evidence=["issue:693"],
            channel="cli-agent", project_dir=PROJECT)


def test_module_retire_error_paths(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="unknown ruling"):
        refutations.retire("r-000000000000", channel="cli-tty",
                           project_dir=PROJECT)
    ref_id = _refute()
    with pytest.raises(refutations.RefutationError, match="refute overturn"):
        refutations.retire(ref_id, channel="cli-tty", project_dir=PROJECT)
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="already retired"):
        refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)


def test_module_write_failures_surface(tmp_checkpoint_dir, monkeypatch):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    monkeypatch.setattr(refutations, "append", lambda *a, **k: False)
    with pytest.raises(refutations.RefutationError, match="not written"):
        refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="not written"):
        refutations.assert_ruling(
            subject="another area", verdict="another rule", scope="elsewhere",
            evidence=["issue:693"], channel="cli-agent", project_dir=PROJECT)


def test_listing_refuses_unknown_states(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="unknown state"):
        refutations.listing(states={"bogus"}, project_dir=PROJECT)


def test_guard_open_proposals_tolerates_bad_order_values(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    row = refutations._stamp("revision-proposed", ruling_id, "cli-agent")
    row["order"] = "not-a-number"
    assert refutations.append(row, project_dir=PROJECT)
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a proposal", project_dir=PROJECT)


def test_ruling_cap_env_falls_back_on_garbage(monkeypatch):
    from daimon_briefing import config
    monkeypatch.setenv("DAIMON_RULING_CAP", "not-a-number")
    assert config.ruling_cap() == 7


def test_forget_doomed_scan_skips_unparseable_rows(
        tmp_checkpoint_dir):
    from daimon_briefing import normalize
    ruling_id = _rule()
    path = refutations._path(PROJECT)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write('"a json string, not a dict"\n')
    removed = refutations.forget_content_key(
        normalize.content_key("internal numbers never appear in public posts"),
        project_dir=PROJECT)
    assert ruling_id in removed


def test_cli_error_and_json_paths(tmp_checkpoint_dir, _tty, monkeypatch,
                                  capsys):
    # Unknown ids and cross-verb pointers on every ruling verb.
    assert cli.main(["ruling", "ratify", "r-000000000000",
                     "--project", PROJECT]) == 1
    assert cli.main(["ruling", "show", "r-000000000000",
                     "--project", PROJECT]) == 1
    assert cli.main(["ruling", "revise", "r-000000000000",
                     "--evidence", "issue:693", "--project", PROJECT]) == 1
    ref_id = _refute()
    assert cli.main(["ruling", "ratify", ref_id, "--project", PROJECT]) == 1
    assert cli.main(["ruling", "show", ref_id, "--project", PROJECT]) == 1
    assert cli.main(["ruling", "revise", ref_id, "--evidence", "issue:693",
                     "--project", PROJECT]) == 1
    assert cli.main(["refute", "revise", "--evidence", "issue:693",
                     "--verdict", "it deadlocked again", ref_id,
                     "--project", PROJECT]) == 0
    capsys.readouterr()
    # Cross-verb gates on the remaining refute verbs.
    ruling_id = _rule()
    assert cli.main(["refute", "revise", ruling_id, "--evidence", "issue:693",
                     "--project", PROJECT]) == 1
    assert cli.main(["refute", "overturn", ruling_id, "--evidence",
                     "issue:693", "--project", PROJECT]) == 1
    assert cli.main(["refute", "show", ruling_id, "--project", PROJECT]) == 1
    # Agent ratify path prints its refusal directly.
    assert cli.main(["ruling", "ratify", ruling_id, "--by", "agent",
                     "--project", PROJECT]) == 1
    assert "human channel" in capsys.readouterr().out
    # Propose failure surfaces (duplicate), --json success, escalation hint.
    assert cli.main(["ruling", "propose", "--subject", "public posts",
                     "--verdict", "x", "--scope", "publishing",
                     "--evidence", "issue:693", "--by", "agent",
                     "--project", PROJECT]) == 1
    assert cli.main(["ruling", "propose", "--subject", "json area",
                     "--verdict", "a json rule", "--scope", "json-scope",
                     "--evidence", "issue:693", "--by", "agent", "--json",
                     "--project", PROJECT]) == 0
    capsys.readouterr()
    human_id = _rule(subject="human area", verdict="a human proposed rule",
                     scope="human-scope", channel="cli-tty")
    assert cli.main(["ruling", "show", human_id, "--project", PROJECT]) == 0
    capsys.readouterr()


def test_cli_detail_json_and_ceremony_paths(tmp_checkpoint_dir, _tty,
                                            monkeypatch, capsys):
    ruling_id = _rule(channel="cli-tty", ratified=True,
                      anchors=["issue:693"],
                      revisit_when="when the policy changes")
    refutations.revise(  # pending revision proposal for the printer
        ruling_id, channel="cli-agent", evidence=["issue:693"],
        verdict="a pending proposal", project_dir=PROJECT)
    refutations.retire(  # pending retirement proposal for the printer
        ruling_id, channel="cli-agent", project_dir=PROJECT)
    assert cli.main(["ruling", "show", ruling_id, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Anchors:" in out
    assert "Revisit when:" in out
    assert "Pending revision proposal" in out
    assert "Pending retirement proposal" in out
    assert cli.main(["ruling", "show", ruling_id, "--json",
                     "--project", PROJECT]) == 0
    capsys.readouterr()
    # revise ceremony with a subject change, module refusal after confirm
    # (cap), retire --json, list --json over-cap stderr.
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "revise", ruling_id,
                     "--subject", "public posts widened",
                     "--evidence", "issue:693", "--project", PROJECT]) == 0
    capsys.readouterr()
    assert cli.main(["ruling", "retire", ruling_id, "--json",
                     "--project", PROJECT]) == 0
    capsys.readouterr()
    second = _rule(subject="second area", verdict="second rule",
                   scope="second-scope", channel="cli-tty", ratified=True)
    monkeypatch.setenv("DAIMON_RULING_CAP", "1")
    third = _rule(subject="third area", verdict="third rule",
                  scope="third-scope")
    assert cli.main(["ruling", "ratify", third, "--project", PROJECT]) == 1
    assert "not ratified" in capsys.readouterr().out
    assert cli.main(["ruling", "list", "--json", "--project", PROJECT]) == 0
    captured = capsys.readouterr()
    assert "second rule" in captured.out
    # ratify --json sends the ceremony to stderr and JSON to stdout
    monkeypatch.setenv("DAIMON_RULING_CAP", "7")
    assert cli.main(["ruling", "ratify", third, "--json",
                     "--project", PROJECT]) == 0
    captured = capsys.readouterr()
    assert "render into every future session" in captured.err
    assert '"refutation_id"' in captured.out
    assert second  # silence unused warning


def test_cli_remaining_ruling_paths(tmp_checkpoint_dir, _tty, monkeypatch,
                                    capsys):
    # Human propose escalation hint; empty list; channel errors off-tty.
    # Nothing has been written from PROJECT yet, so the empty list is the
    # "no bucket" answer: same stdout, exit 1 and a stderr line since #948.
    assert cli.main(["ruling", "list", "--project", PROJECT]) == 1
    captured = capsys.readouterr()
    assert "no rulings" in captured.out
    assert "no bucket" in captured.err
    assert cli.main(["ruling", "propose", "--subject", "human area",
                     "--verdict", "a human proposed rule",
                     "--scope", "human-scope", "--evidence", "issue:693",
                     "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "Next: daimon ruling ratify" in out
    # Agent CLI revise on an active ruling records a proposal, with --json
    # and plain variants; agent retire note; already-retired error; candidate
    # revision note; ratify/revise channel errors without a terminal.
    active_id = _rule(channel="cli-tty", ratified=True)
    assert cli.main(["ruling", "revise", active_id, "--by", "agent",
                     "--verdict", "an agent counterproposal",
                     "--evidence", "issue:693", "--project", PROJECT]) == 0
    assert "Proposal recorded" in capsys.readouterr().out
    assert cli.main(["ruling", "revise", active_id, "--by", "agent",
                     "--verdict", "another counterproposal", "--json",
                     "--evidence", "issue:693", "--project", PROJECT]) == 0
    capsys.readouterr()
    cand = _rule(subject="cand area", verdict="cand rule", scope="cand-scope")
    assert cli.main(["ruling", "revise", cand, "--verdict", "cand rule v2",
                     "--evidence", "issue:693", "--project", PROJECT]) == 0
    assert "not load-bearing" in capsys.readouterr().out
    assert cli.main(["ruling", "retire", active_id, "--by", "agent",
                     "--project", PROJECT]) == 0
    assert "Retirement proposed" in capsys.readouterr().out
    assert cli.main(["ruling", "retire", active_id,
                     "--project", PROJECT]) == 0
    assert cli.main(["ruling", "retire", active_id,
                     "--project", PROJECT]) == 1
    assert "already retired" in capsys.readouterr().out
    # Post-ceremony module refusal: human revise steals another identity.
    a = _rule(subject="ident a", verdict="rule a", scope="scope-a",
              channel="cli-tty", ratified=True)
    b = _rule(subject="ident b", verdict="rule b", scope="scope-b",
              channel="cli-tty", ratified=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert cli.main(["ruling", "revise", b, "--subject", "ident a",
                     "--scope", "scope-a", "--evidence", "issue:693",
                     "--project", PROJECT]) == 1
    assert "not revised" in capsys.readouterr().out
    assert a
    # Channel errors: no terminal and no --by flag.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False,
                        raising=False)
    assert cli.main(["ruling", "ratify", cand, "--project", PROJECT]) == 1
    assert cli.main(["ruling", "revise", cand, "--verdict", "x",
                     "--evidence", "issue:693", "--project", PROJECT]) == 1
    capsys.readouterr()


# ---- "no rulings" and "no bucket" are different answers (#948) ----


def test_ruling_list_separates_an_empty_project_from_an_unwritten_one(
        tmp_checkpoint_dir, capsys):
    """A path that has never been written from is the shape a mis-resolved
    --project takes. Reporting it as "no rulings for this project" at exit 0
    is what let the #948 routing defect stay invisible: the read looked
    successful. stdout is unchanged so scripts parsing it keep working; the
    diagnosis goes to stderr and the exit code, matching `daimon status`."""
    from daimon_briefing import cli

    assert cli.main(["ruling", "list", "--project", PROJECT]) == 1
    captured = capsys.readouterr()
    assert "no rulings for this project" in captured.out
    assert store.project_slug(PROJECT) in captured.err
    assert "no bucket" in captured.err


def test_ruling_list_json_on_an_unwritten_project_still_prints_an_empty_list(
        tmp_checkpoint_dir, capsys):
    from daimon_briefing import cli

    assert cli.main(["ruling", "list", "--json", "--project", PROJECT]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert store.project_slug(PROJECT) in captured.err


def test_ruling_list_on_a_bucket_with_no_ledger_is_a_clean_empty_read(
        tmp_checkpoint_dir, capsys):
    """The bucket exists, so the project HAS written; it simply holds no
    rulings. That is a successful empty read, exit 0."""
    from daimon_briefing import cli

    (tmp_checkpoint_dir / (store.project_slug(PROJECT) or "")).mkdir(
        parents=True)

    assert cli.main(["ruling", "list", "--project", PROJECT]) == 0
    assert "no rulings for this project" in capsys.readouterr().out


def test_ruling_list_on_a_ledger_holding_only_refutations_is_exit_zero(
        tmp_checkpoint_dir, capsys):
    from daimon_briefing import cli

    _refute()
    capsys.readouterr()

    assert cli.main(["ruling", "list", "--project", PROJECT]) == 0
    assert "no rulings for this project" in capsys.readouterr().out


# ---- #962: an unreadable ledger is a THIRD answer, never "no rulings" ----
#
# `bucket_exists` only proves the project's bucket DIRECTORY is there; it
# never opens refutations.jsonl. A ledger replaced by a directory, with its
# read permission pulled, or stuck in a symlink loop, used to degrade to the
# same empty read as a project that genuinely has no active rulings — the
# one case #948 left open. `briefing.rulings_read` is the shared reader that
# must tell all four facts apart (a fourth, "unresolved", covers the ledger
# path itself never getting worked out), and `active_rulings`/`ruling list`
# both sit on it now.


def _break_ledger_as_a_directory():
    path = refutations._path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()


def _break_ledger_by_permission():
    _rule(channel="cli-tty", ratified=True)
    refutations._path(PROJECT).chmod(0o000)


def _break_ledger_with_a_symlink_loop():
    """#962 F2: `Path.exists()` swallows ELOOP into a bare False the same
    way it swallows ENOENT, so a ledger stuck in a symlink loop used to read
    as "no ledger yet" even under `strict=True` — the read never happened,
    so there was nothing to re-raise."""
    path = refutations._path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(path)
    except OSError:
        pytest.skip("platform refuses a self-referential symlink")


_LEDGER_BREAKERS = [
    pytest.param(_break_ledger_as_a_directory, id="directory-in-its-place"),
    pytest.param(
        _break_ledger_by_permission, id="chmod-000",
        marks=pytest.mark.skipif(
            hasattr(os, "geteuid") and os.geteuid() == 0,
            reason="root reads a 000 file, so the branch is unreachable")),
    pytest.param(_break_ledger_with_a_symlink_loop, id="symlink-loop"),
]


@pytest.mark.parametrize("break_ledger", _LEDGER_BREAKERS)
def test_rulings_read_reports_unreadable_never_no_bucket_or_a_clean_empty(
        break_ledger, tmp_checkpoint_dir):
    from daimon_briefing import briefing

    break_ledger()
    read = briefing.rulings_read(PROJECT)
    assert read.state == "unreadable"
    assert read.rows == []
    assert read.path == refutations._path(PROJECT)


@pytest.mark.parametrize("break_ledger", _LEDGER_BREAKERS)
def test_active_rulings_still_fails_open_on_an_unreadable_ledger(
        break_ledger, tmp_checkpoint_dir):
    from daimon_briefing import briefing

    break_ledger()
    assert briefing.active_rulings(PROJECT) == []


@pytest.mark.parametrize("break_ledger", _LEDGER_BREAKERS)
def test_events_default_still_swallows_an_unreadable_ledger(
        break_ledger, tmp_checkpoint_dir):
    """#962 gives `events()` a `strict=True` escape hatch; every existing
    caller that never asks for it keeps today's fail-open contract exactly,
    byte for byte."""
    break_ledger()
    assert refutations.events(PROJECT) == []


@pytest.mark.parametrize("break_ledger", _LEDGER_BREAKERS)
def test_events_strict_reraises_instead_of_swallowing(
        break_ledger, tmp_checkpoint_dir):
    break_ledger()
    with pytest.raises(OSError):
        refutations.events(PROJECT, strict=True)


# ---- #962 F1/F3: "unresolved" is its own fourth state, never "no-bucket" ----
#
# `bucket_exists` and the read below both need a resolved path FIRST.
# `refutations._path` can come back None (no project identifies at all) or
# RAISE (a `config` failure — a corrupt `~/.daimon/env` byte reaching
# `config.checkpoint_dir()` is one real way in). Either must report
# "unresolved" with `path=None`, never fall through to "no-bucket" (which
# would name a path that was never actually produced) and never escape as a
# bare exception out of `active_rulings`, the pinned fail-open API.


@pytest.mark.parametrize("project_dir", [None, ""])
def test_rulings_read_state_unresolved_when_no_project_identifies_at_all(
        project_dir, tmp_checkpoint_dir):
    from daimon_briefing import briefing

    read = briefing.rulings_read(project_dir)
    assert read.state == "unresolved"
    assert read.path is None
    assert read.rows == []


def test_rulings_read_state_unresolved_when_path_resolution_raises(
        tmp_checkpoint_dir, monkeypatch):
    """The real #962 F1 shape: a bad byte in `~/.daimon/env` reaches
    `config.checkpoint_dir()` through `refutations._path`, as a
    `UnicodeDecodeError`, not an `OSError` — `config._file_values` only
    catches the latter. `_path` itself never catches anything."""
    from daimon_briefing import briefing

    def boom(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    monkeypatch.setattr(refutations, "_path", boom)
    read = briefing.rulings_read(PROJECT)
    assert read.state == "unresolved"
    assert read.path is None
    assert read.rows == []


def test_rulings_read_path_is_none_iff_state_is_unresolved(
        tmp_checkpoint_dir, monkeypatch):
    """The four-state contract's own invariant, pinned directly: every other
    state always carries a real path, and only "unresolved" ever carries
    None."""
    from daimon_briefing import briefing

    cases = [briefing.rulings_read(None)]  # unresolved
    cases.append(briefing.rulings_read(PROJECT))  # no-bucket
    (tmp_checkpoint_dir / (store.project_slug(PROJECT) or "")).mkdir(
        parents=True)
    cases.append(briefing.rulings_read(PROJECT))  # read (empty)
    _rule(channel="cli-tty", ratified=True)
    cases.append(briefing.rulings_read(PROJECT))  # read (rows)
    for read in cases:
        assert (read.path is None) == (read.state == "unresolved"), read


def test_active_rulings_fails_open_when_path_resolution_raises(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import briefing

    def boom(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    monkeypatch.setattr(refutations, "_path", boom)
    assert briefing.active_rulings(PROJECT) == []


def test_active_rulings_fails_open_even_if_rulings_read_itself_forgets_to(
        tmp_checkpoint_dir, monkeypatch):
    """#962 F1: `active_rulings` carries its OWN guard, independent of
    `rulings_read`'s, so a future bug in the strict sibling cannot silently
    turn the pinned fail-open API into a crasher."""
    from daimon_briefing import briefing

    def boom(*args, **kwargs):
        raise RuntimeError("rulings_read itself broke")

    monkeypatch.setattr(briefing, "rulings_read", boom)
    assert briefing.active_rulings(PROJECT) == []


def test_rulings_read_state_no_bucket_for_an_unwritten_project(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing

    read = briefing.rulings_read(PROJECT)
    assert read.state == "no-bucket"
    assert read.rows == []


def test_rulings_read_state_read_for_a_bucket_with_no_ledger_yet(
        tmp_checkpoint_dir):
    """A bucket holding only checkpoints has been written from; it simply
    carries no ledger, and that is a clean empty read, never "unreadable"."""
    from daimon_briefing import briefing

    (tmp_checkpoint_dir / (store.project_slug(PROJECT) or "")).mkdir(
        parents=True)

    read = briefing.rulings_read(PROJECT)
    assert read.state == "read"
    assert read.rows == []


def test_rulings_read_state_read_matches_active_rulings(tmp_checkpoint_dir):
    from daimon_briefing import briefing

    ruling_id = _rule(channel="cli-tty", ratified=True)
    read = briefing.rulings_read(PROJECT)
    assert read.state == "read"
    assert [r["refutation_id"] for r in read.rows] == [ruling_id]
    assert read.rows == briefing.active_rulings(PROJECT)


def test_rulings_read_skips_malformed_lines_and_keeps_the_good_rows(
        tmp_checkpoint_dir):
    from daimon_briefing import briefing

    ruling_id = _rule(channel="cli-tty", ratified=True)
    path = refutations._path(PROJECT)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json\n")
        fh.write('"a json string, not a dict"\n')

    read = briefing.rulings_read(PROJECT)
    assert read.state == "read"
    assert [r["refutation_id"] for r in read.rows] == [ruling_id]


def test_cli_ruling_list_reports_an_unreadable_ledger_distinctly(
        tmp_checkpoint_dir, capsys):
    _break_ledger_as_a_directory()

    rc = cli.main(["ruling", "list", "--project", PROJECT])
    captured = capsys.readouterr()
    assert rc == 1
    # #948/scar 0057's contract holds: stdout is unchanged either way.
    assert "no rulings for this project" in captured.out
    assert str(refutations._path(PROJECT)) in captured.err


def test_cli_ruling_list_json_still_prints_its_payload_on_an_unreadable_ledger(
        tmp_checkpoint_dir, capsys):
    _break_ledger_as_a_directory()

    rc = cli.main(["ruling", "list", "--json", "--project", PROJECT])
    captured = capsys.readouterr()
    assert rc == 1
    assert json.loads(captured.out) == []
    assert str(refutations._path(PROJECT)) in captured.err


def test_cli_ruling_list_no_bucket_case_is_unregressed(tmp_checkpoint_dir,
                                                       capsys):
    """The pre-existing #948 branch (no bucket at all) must still fire on
    its own message, not fall through to the new #962 wording."""
    rc = cli.main(["ruling", "list", "--project", PROJECT])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no bucket for" in captured.err
    assert "cannot read" not in captured.err


def test_cli_ruling_list_reports_unresolved_without_naming_a_path(
        tmp_checkpoint_dir, capsys, monkeypatch):
    """A `_path` raise is caught inside `rulings_read` itself and never
    surfaces this far as an exception, so the CLI branch is proven directly
    against the shared reader's own contract: "unresolved" has no path to
    report, and the stderr line must not pretend otherwise."""
    from daimon_briefing import briefing

    monkeypatch.setattr(
        briefing, "rulings_read",
        lambda project_dir=None: briefing.RulingsRead(
            rows=[], state="unresolved", path=None))
    rc = cli.main(["ruling", "list", "--project", PROJECT])
    captured = capsys.readouterr()
    assert rc == 1
    assert "no rulings for this project" in captured.out
    assert "None" not in captured.err
    assert "cannot resolve" in captured.err


def test_cli_list_json_over_cap_goes_to_stderr(tmp_checkpoint_dir,
                                               monkeypatch, capsys):
    for n in range(2):
        _rule(subject=f"cap area {n}", verdict=f"cap rule {n}",
              scope=f"cap-scope-{n}", channel="cli-tty", ratified=True)
    monkeypatch.setenv("DAIMON_RULING_CAP", "1")
    assert cli.main(["ruling", "list", "--json", "--project", PROJECT]) == 0
    captured = capsys.readouterr()
    assert "over cap" in captured.err
    assert "over cap" not in captured.out


def test_module_agent_cannot_found_ratified(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="human channel"):
        _rule(ratified=True)  # channel defaults to cli-agent


def test_dry_run_warns_about_active_rulings(tmp_checkpoint_dir, _tty,
                                            capsys):
    from daimon_briefing import store
    ruling_id = _rule(channel="cli-tty", ratified=True)
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "unrelated", "trust": "inferred"}]},
    }, project_dir=PROJECT)
    rc = cli.main(["forget", "internal numbers never appear in public posts",
                   "--dry-run", "--project", PROJECT])
    assert rc == 0
    assert "removes ACTIVE ruling" in capsys.readouterr().out
    assert refutations.get(ruling_id, project_dir=PROJECT) is not None


# --- #860: the post-write re-read ------------------------------------------
#
# Every ruling verb that WRITES then re-reads the record to render it. The
# lookup verbs (show, and the pre-write guards) already check for absence,
# because a user-supplied id may name nothing. The write verbs did not, and
# the reason is a reasonable one: the record was written a moment earlier, so
# it must be there.
#
# It must be there unless a competing writer removes it in between, which is
# the window #857 confirmed reachable in the request ledger. In that window
# the write SUCCEEDED and only the render could not resolve, so the command
# has to report that rather than dying on a None.


def _vanishes_after_the_write(monkeypatch, write_name):
    """Model a competing writer between a verb's write and its own re-read.

    Sharper than a call counter: it is pinned to the write actually
    completing, so it stays faithful no matter how many reads a given verb
    makes on its way there.
    """
    real_write = getattr(refutations, write_name)
    real_get = refutations.get
    state = {"written": False}

    def wrapped_write(*args, **kwargs):
        out = real_write(*args, **kwargs)
        state["written"] = True
        return out

    def wrapped_get(*args, **kwargs):
        return None if state["written"] else real_get(*args, **kwargs)

    monkeypatch.setattr(refutations, write_name, wrapped_write)
    monkeypatch.setattr(refutations, "get", wrapped_get)
    return state


_RULING_WRITE_VERBS = [
    ("propose", "assert_ruling", [
        "ruling", "propose", "--subject", "public posts",
        "--verdict", "internal numbers never appear in public posts",
        "--scope", "publishing", "--evidence", "issue:693", "--by", "agent"]),
    ("ratify", "ratify", ["ruling", "ratify", "RULING_ID"]),
    ("revise", "revise", [
        "ruling", "revise", "RULING_ID",
        "--verdict", "internal numbers never ship in public posts",
        "--evidence", "issue:693"]),
    ("retire", "retire", ["ruling", "retire", "RULING_ID"]),
]


@pytest.mark.parametrize("verb,write_name,argv", _RULING_WRITE_VERBS,
                         ids=[v[0] for v in _RULING_WRITE_VERBS])
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_cli_ruling_write_verbs_survive_the_record_vanishing(
        verb, write_name, argv, as_json, tmp_checkpoint_dir, _tty,
        monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    # `propose` CREATES; pre-seeding the same subject/scope would collide and
    # the verb would refuse before writing anything, which the `written`
    # assertion below catches rather than letting the test pass vacuously.
    ruling_id = ("" if verb == "propose"
                 else _rule(ratified=(verb in {"revise", "retire"}),
                            channel=("cli-tty" if verb in {"revise", "retire"}
                                     else "cli-agent")))
    capsys.readouterr()

    state = _vanishes_after_the_write(monkeypatch, write_name)
    args = [ruling_id if a == "RULING_ID" else a for a in argv]
    args += ["--project", PROJECT]
    if as_json:
        args.append("--json")

    rc = cli.main(args)

    assert state["written"], "the write never ran, so this proves nothing"
    assert rc == 0
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert out.strip(), "a vanished record must still report what happened"


# ---- #927: the in-process host surface, pinned ----


def test_a_library_side_signed_ratification_activates_and_says_so(tmp_checkpoint_dir):
    """A host that verified an operator out of band writes the record itself
    through the library, never through a CLI flag. The rendered tier is the
    honest part: "ratified (signed)", never "human-ratified" unqualified."""
    rid = _rule(anchors=["chat:user:123"])
    refutations.ratify(rid, channel="signed", note="operator role verified",
                       project_dir=PROJECT)
    (row,) = refutations.listing(states={"active"}, polarity="ruling",
                                 project_dir=PROJECT)
    assert row["refutation_id"] == rid
    assert row["activation_channel"] == "signed"
    assert row["activation"] == "ratified (signed)"
    assert "human-ratified" not in row["activation"]


def test_a_ui_ratification_is_a_human_channel_too(tmp_checkpoint_dir):
    rid = _rule()
    refutations.ratify(rid, channel="ui", project_dir=PROJECT)
    (row,) = refutations.listing(states={"active"}, polarity="ruling",
                                 project_dir=PROJECT)
    assert row["activation"] == "ratified (ui)"


def test_an_unmapped_channel_name_is_refused_at_ratify(tmp_checkpoint_dir):
    """Authority is derived from the channel the caller observed and only the
    mapped names count; a host cannot invent a human channel by naming one."""
    rid = _rule()
    with pytest.raises(refutations.RefutationError, match="requires a human channel"):
        refutations.ratify(rid, channel="chat-admin", project_dir=PROJECT)
    assert refutations.get(rid, project_dir=PROJECT)["state"] == "candidate"


def test_active_rulings_carries_the_anchors_a_host_matches_on(tmp_checkpoint_dir):
    from daimon_briefing import briefing
    rid = _rule(anchors=["chat:user:123", "chat:channel:9"])
    refutations.ratify(rid, channel="signed", project_dir=PROJECT)
    (row,) = briefing.active_rulings(PROJECT)
    assert row["refutation_id"] == rid
    assert row["anchors"] == ["chat:user:123", "chat:channel:9"]
    assert row["verdict"] == "internal numbers never appear in public posts"


# ---- #940: what a host may rely on, and where a change becomes visible ----

# The keys the CLI reference promises a host process can read. The doc says
# them in PROSE, because the reader-facing vocabulary gate is line-level and
# rejects one of these names outright (scar 0069). So this list is the
# machine-checkable half of that promise: rename or drop a key here and the
# published contract is broken, whatever the page still says.
DOCUMENTED_HOST_KEYS = frozenset({
    "refutation_id", "state", "subject", "scope", "anchors",
    "activation", "activation_channel", "evidence", "verdict",
})


def test_listing_carries_every_key_the_reference_promises(tmp_checkpoint_dir):
    rid = _rule(anchors=["chat:user:123"])
    refutations.ratify(rid, channel="signed", project_dir=PROJECT)
    (row,) = refutations.listing(states={"active"}, polarity="ruling",
                                 project_dir=PROJECT)
    assert DOCUMENTED_HOST_KEYS <= row.keys(), \
        DOCUMENTED_HOST_KEYS - row.keys()


def test_active_rulings_carries_them_too(tmp_checkpoint_dir):
    """The reference tells a host these two readers agree. If they diverge, a
    host that switched readers on our advice would silently read fewer fields
    rather than fail."""
    from daimon_briefing import briefing
    rid = _rule()
    refutations.ratify(rid, channel="signed", project_dir=PROJECT)
    (row,) = briefing.active_rulings(PROJECT)
    assert DOCUMENTED_HOST_KEYS <= row.keys(), \
        DOCUMENTED_HOST_KEYS - row.keys()


def test_the_reference_states_where_a_change_to_this_surface_shows_up(
        tmp_checkpoint_dir):
    """daimon is pre-1.0 with bump-minor-pre-major, so a break ships as a
    MINOR release and the version number alone will not warn anyone. The
    promise we can keep is that the change is VISIBLE, so the page must keep
    saying where. Both language mirrors, because a host reading the Spanish
    page is owed the same warning."""
    root = Path(__file__).parent.parent.parent
    pages = [
        root / "website/docs/reference/cli.md",
        root / ("website/i18n/es/docusaurus-plugin-content-docs"
                "/current/reference/cli.md"),
    ]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "CHANGELOG.md" in text, f"{page} stopped naming the changelog"
        assert "1.0" in text, f"{page} stopped saying daimon is pre-1.0"


def test_the_reference_names_the_resolver_a_host_shares_with_the_cli(
        tmp_checkpoint_dir):
    """#948: a host passing `project_dir` gets the CLI's resolution, and the
    page has to say so, or a host standing in a subdirectory writes a bucket
    its own `daimon ruling list` never reads. Both language mirrors, because a
    host reading the Spanish page is owed the same contract."""
    root = Path(__file__).parent.parent.parent
    pages = [
        root / "website/docs/reference/cli.md",
        root / ("website/i18n/es/docusaurus-plugin-content-docs"
                "/current/reference/cli.md"),
    ]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "resolve_project_dir" in text, \
            f"{page} stopped naming the shared resolver"
        assert "git" in text, f"{page} stopped saying where a path resolves to"


def test_the_reference_names_all_four_rulings_read_states(tmp_checkpoint_dir):
    """#962: `rulings_read`'s four states are pinned prose, not just code —
    a host reading either language page must see every state name it can
    get back, or one of them can drift silently out of the doc."""
    root = Path(__file__).parent.parent.parent
    pages = [
        root / "website/docs/reference/cli.md",
        root / ("website/i18n/es/docusaurus-plugin-content-docs"
                "/current/reference/cli.md"),
    ]
    states = ('"unresolved"', '"no-bucket"', '"unreadable"', '"read"')
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "rulings_read" in text, f"{page} dropped the sibling reader"
        for state in states:
            assert state in text, f"{page} is missing state {state}"


def _check(**overrides):
    values = {
        "match": r"\bgh (pr|issue|release) (create|edit|comment)\b",
        "body": "#!/bin/sh\nrg -q -- '\\u2014' \"$DAIMON_CHECK_SUBJECT\" && exit 1\nexit 0\n",
    }
    values.update(overrides)
    return values


def test_check_validator_computes_sha_over_the_stored_body():
    out = refutations._check(_check())
    assert out["intent"] == "warn"
    assert out["sha256"] == hashlib.sha256(
        out["body"].encode("utf-8")).hexdigest()
    assert set(out) == {"match", "intent", "body", "sha256"}


def test_check_validator_returns_none_for_none():
    assert refutations._check(None) is None


def test_check_validator_ignores_a_caller_supplied_sha():
    out = refutations._check({**_check(), "sha256": "deadbeef"})
    assert out["sha256"] != "deadbeef"


@pytest.mark.parametrize("body", [
    "~/.claude/voicegate.sh",
    "/usr/local/bin/gate",
    "./scripts/gate.sh",
])
def test_check_body_that_is_a_path_is_refused(body):
    with pytest.raises(refutations.RefutationError, match="must be the script"):
        refutations._check(_check(body=body))


def test_check_body_over_cap_is_refused():
    with pytest.raises(refutations.RefutationError, match="8192"):
        refutations._check(_check(body="x" * (refutations._MAX_CHECK_BODY + 1)))


_SECRET_BODY = (
    "grep -q 'AKIAIOSFODNN7EXAMPLE' \"$DAIMON_CHECK_SUBJECT\" && exit 1\n"
    "exit 0\n")


def test_the_secret_shaped_body_this_module_uses_really_does_redact():
    """Guards the two refusal tests below: if redact ever stops catching this
    shape they must fail loudly, not pass because nothing was detected."""
    scrubbed, counts = redact.redact_text(_SECRET_BODY)
    assert scrubbed != _SECRET_BODY
    assert counts


def test_check_body_with_a_secret_literal_is_refused():
    with pytest.raises(refutations.RefutationError, match="secret-shaped"):
        refutations._check(_check(body=_SECRET_BODY))


def test_check_match_with_a_secret_literal_is_refused():
    with pytest.raises(refutations.RefutationError, match="secret-shaped"):
        refutations._check(_check(match="AKIAIOSFODNN7EXAMPLE"))


def test_check_match_is_stored_stripped():
    out = refutations._check(_check(match="  gh pr create  "))
    assert out["match"] == "gh pr create"


def test_check_body_is_stored_exactly_as_given():
    body = "#!/bin/sh\n  echo   spaced\nexit 0\n"
    out = refutations._check(_check(body=body))
    assert out["body"] == body


def test_check_match_must_compile():
    with pytest.raises(refutations.RefutationError, match="not a valid regex"):
        refutations._check(_check(match="gh (pr"))


def test_check_match_over_cap_is_refused():
    with pytest.raises(refutations.RefutationError, match="200"):
        refutations._check(_check(match="a" * (refutations._MAX_CHECK_MATCH + 1)))


def test_check_intent_outside_the_set_is_refused():
    with pytest.raises(refutations.RefutationError, match="intent"):
        refutations._check(_check(intent="block"))


def test_check_requires_both_match_and_body():
    with pytest.raises(refutations.RefutationError, match="match"):
        refutations._check({"body": "exit 0\n"})
    with pytest.raises(refutations.RefutationError, match="body"):
        refutations._check({"match": "gh"})


def test_candidate_check_lifecycle_is_proposed(tmp_checkpoint_dir):
    ruling_id = _rule(check=_check())
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["match"] == _check()["match"]
    assert record["check"]["sha256"]
    assert record["check_lifecycle"] == "proposed"


def test_ruling_without_check_has_no_lifecycle_key(tmp_checkpoint_dir):
    ruling_id = _rule()
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert "check" not in record
    assert "check_lifecycle" not in record


def test_human_founding_with_ratify_arms_the_check(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["check_lifecycle"] == "armed"


def test_agent_revise_of_a_candidate_replaces_the_check(tmp_checkpoint_dir):
    ruling_id = _rule(check=_check())
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:943"],
        check=_check(body="exit 0\n"), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["body"] == "exit 0\n"
    assert record["check_lifecycle"] == "proposed"


def test_agent_revise_of_an_active_ruling_leaves_the_armed_check(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    armed_sha = refutations.get(ruling_id, project_dir=PROJECT)["check"]["sha256"]
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:943"],
        check=_check(body="exit 0\n"), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check"]["sha256"] == armed_sha
    assert record["check_lifecycle"] == "armed"
    assert record["revision_proposed"]["check"]["body"] == "exit 0\n"


def test_human_revise_of_an_active_ruling_replaces_and_stays_armed(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:943"],
        check=_check(body="exit 0\n"), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["check"]["body"] == "exit 0\n"
    assert record["check_lifecycle"] == "armed"


def test_human_in_process_revise_arms_the_supplied_check_without_a_pin(
        tmp_checkpoint_dir):
    """#943: pinning the in-process contract. `ratify` binds a check to the
    hash the ceremony DISPLAYED because the human is confirming content
    someone else wrote. An in-process human channel supplied the body in the
    same call, so there is no display-then-swap window to close and no pin to
    demand: it arms as given, and the host owns that confirmation."""
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    refutations.revise(
        ruling_id, channel="signed", evidence=["issue:943"],
        check=_check(body="exit 0\n"), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["check"]["body"] == "exit 0\n"
    assert record["check_lifecycle"] == "armed"


def test_retired_ruling_check_is_disarmed(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["check_lifecycle"] == "disarmed"


def test_revise_with_only_a_check_is_a_change(tmp_checkpoint_dir):
    ruling_id = _rule()
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:943"],
        check=_check(), project_dir=PROJECT)
    assert refutations.get(ruling_id, project_dir=PROJECT)["check_lifecycle"] == "proposed"


def test_revise_refuses_a_check_on_a_refutation(tmp_checkpoint_dir):
    ref_id = _refute()
    before = refutations.get(ref_id, project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="only a ruling"):
        refutations.revise(
            ref_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(), project_dir=PROJECT)
    after = refutations.get(ref_id, project_dir=PROJECT)
    assert after["revision"] == before["revision"]
    assert "check" not in after


def test_ratify_pinned_to_the_current_check_hash_activates(tmp_checkpoint_dir):
    ruling_id = _rule(check=_check())
    sha = refutations.get(ruling_id, project_dir=PROJECT)["check"]["sha256"]
    refutations.ratify(ruling_id, channel="cli-tty", check_sha256=sha,
                       project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["check_lifecycle"] == "armed"


def test_ratify_pinned_to_a_stale_check_hash_is_inert(tmp_checkpoint_dir):
    ruling_id = _rule(check=_check())
    stale = refutations.get(ruling_id, project_dir=PROJECT)["check"]["sha256"]
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:943"],
        check=_check(body="rm -rf / # never\n"), project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", check_sha256=stale,
                       project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["check_lifecycle"] == "proposed"


def test_ratify_without_a_pin_is_inert_when_the_record_carries_a_check(
        tmp_checkpoint_dir):
    ruling_id = _rule(check=_check())
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["check_lifecycle"] == "proposed"


def test_ratify_without_a_pin_still_activates_a_ruling_with_no_check(
        tmp_checkpoint_dir):
    ruling_id = _rule()
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    assert refutations.get(ruling_id, project_dir=PROJECT)["state"] == "active"


def test_unbound_ratify_does_not_reset_the_proposal_cap_on_a_checked_ruling(
        tmp_checkpoint_dir):
    """#943: the containment half of the unbound-ratify gate. A ratify row
    with NO pin is inert on a ruling that carries a check, and an inert row
    must not hand back a proposal slot — the adversary controls when it
    happens, by adding the check during the confirm window."""
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    for i in range(3):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body=f"exit 0 # {i}\n"), project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="open"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body="exit 0 # 3\n"), project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="open"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body="exit 0 # 4\n"), project_dir=PROJECT)


def test_unbound_ratify_still_resets_the_proposal_cap_with_no_check(
        tmp_checkpoint_dir):
    """The silent half: with no check on the record an unbound ratify is a
    real human verdict, so it still clears the counter."""
    ruling_id = _rule(channel="cli-tty", ratified=True)
    for i in range(3):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            verdict=f"internal numbers never appear in posts {i}",
            project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="open"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            verdict="internal numbers never appear in posts 3",
            project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:943"],
        verdict="internal numbers never appear in posts 4",
        project_dir=PROJECT)


def test_stale_check_pin_ratify_does_not_reset_the_proposal_cap(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, check=_check())
    # Create three revision-proposed rows
    for i in range(3):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body=f"exit 0 # {i}\n"), project_dir=PROJECT)
    # Fourth revision should fail (cap reached)
    with pytest.raises(refutations.RefutationError, match="open"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body="exit 0 # 3\n"), project_dir=PROJECT)
    # Apply a stale check pin (fold-inert because it doesn't match current check)
    refutations.ratify(ruling_id, channel="cli-tty", check_sha256="0" * 64,
                       project_dir=PROJECT)
    # Fourth revision should STILL fail (stale pin doesn't reset cap)
    with pytest.raises(refutations.RefutationError, match="open"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:943"],
            check=_check(body="exit 0 # 4\n"), project_dir=PROJECT)


def test_check_validator_refuses_a_non_object():
    with pytest.raises(refutations.RefutationError, match="must be an object"):
        refutations._check("exit 0")


# ---- #943 slice 2: a body that reaches outside itself ---------------------
#
# The slice 1 refusal above catches a body that IS a bare path. A multi-line
# body that merely CALLS one passes it, and the check then travels to another
# machine as a script whose real content is a file that is not there. The
# runner sees that as exit 127, which is honest but late: the ruling was
# already ratified and every host reports unresolved forever.


@pytest.mark.parametrize("body", [
    "#!/bin/sh\nsh ~/.claude/voicegate.sh \"$DAIMON_CHECK_SUBJECT\"\n",
    "#!/bin/sh\nexec $HOME/gate \"$DAIMON_CHECK_SUBJECT\"\n",
    "#!/bin/sh\n. /Users/someone/lib/rules.sh\nexit 0\n",
    "#!/bin/sh\n/home/someone/gate || exit 1\n",
    "#!/bin/sh\ntest -f /root/allowlist && exit 0\nexit 1\n",
    "#!/bin/sh\nexit 0  # see ~/notes/why.md\n",
])
def test_a_check_body_line_reaching_a_host_local_root_is_refused(body):
    with pytest.raises(refutations.RefutationError,
                       match="host-local path"):
        refutations._check(_check(body=body))


@pytest.mark.parametrize("body", [
    "#!/bin/sh\nsh ./scripts/gate.sh \"$DAIMON_CHECK_SUBJECT\"\nexit 0\n",
    "#!/bin/sh\ngrep -q FORBIDDEN \"$DAIMON_CHECK_SUBJECT\" && exit 1\nexit 0\n",
    "#!/bin/sh\ntest -x /usr/bin/grep || exit 0\nexit 0\n",
    "#!/bin/sh\necho \"$DAIMON_CHECK_COMMAND\" | grep -q gh && exit 1\nexit 0\n",
    "#!/bin/sh\nmktemp -d /tmp/check.XXXX >/dev/null\nexit 0\n",
])
def test_a_body_that_stays_inside_itself_is_accepted(body):
    assert refutations._check(_check(body=body))["body"] == body


def test_the_refusal_names_the_line_it_found(_capture=None):
    """A body is up to 8192 bytes. "somewhere in here" is not a fix."""
    body = "#!/bin/sh\nexit 0\nsh ~/gate.sh\n"
    with pytest.raises(refutations.RefutationError) as excinfo:
        refutations._check(_check(body=body))
    assert "line 3" in str(excinfo.value)


def test_the_single_token_path_refusal_still_says_what_it_always_said():
    """The two rules overlap and the older, more specific one must win: a
    bare path gets the message that explains what a body IS."""
    with pytest.raises(refutations.RefutationError, match="must be the script"):
        refutations._check(_check(body="~/.claude/voicegate.sh"))


# ---- #961 slice 4: request_policy, the ruling hook ------------------------
#
# A ruling may carry a `request_policy`, permission for another project's
# agent to record `accept` on a `work` ask this project owes it. Same
# doctrine as `check` (#943): validated on the way in, stored on the
# founding row, pinned at ratify by `policy_sha256`, gated in the fold
# identically. The consuming side (requests.fold's widened exception,
# requests.accept's write boundary) is covered in test_requests.py; this
# section is the refutations-ledger half — validation, storage, the pin,
# and refutations.active_request_policies.


def _policy(**overrides):
    values = {"sender": "p-sender", "kind": "work", "verb": "accept",
             "by": "agent"}
    values.update(overrides)
    return values


def test_policy_validator_accepts_the_documented_shape():
    out = refutations._policy(_policy())
    assert out["sender"] == "p-sender"
    assert out["kind"] == "work"
    assert out["verb"] == "accept"
    assert out["by"] == "agent"
    assert len(out["sha256"]) == 64


def test_policy_validator_accepts_kind_info_as_a_no_op_shape():
    """#961 slice 4 design decision: info is a genuine, if inert, policy —
    refused nowhere, since it can never be the thing that authorizes an
    accept (an info ask needs no ruling in the first place)."""
    out = refutations._policy(_policy(kind="info"))
    assert out["kind"] == "info"


def test_policy_validator_returns_none_for_none():
    assert refutations._policy(None) is None


def test_policy_validator_refuses_a_non_object():
    with pytest.raises(refutations.RefutationError, match="must be an object"):
        refutations._policy("sender=p-sender")


@pytest.mark.parametrize("missing", ["sender", "kind", "verb", "by"])
def test_policy_validator_refuses_a_missing_key(missing):
    values = _policy()
    del values[missing]
    with pytest.raises(refutations.RefutationError, match="missing"):
        refutations._policy(values)


def test_policy_validator_refuses_an_extra_key():
    with pytest.raises(refutations.RefutationError, match="unexpected"):
        refutations._policy(_policy(extra="nope"))


def test_policy_validator_refuses_a_wildcard_sender():
    """Slice 4's own binding answer: sender="*" is not permitted."""
    with pytest.raises(refutations.RefutationError, match="wildcard"):
        refutations._policy(_policy(sender="*"))


def test_policy_validator_refuses_an_empty_sender():
    with pytest.raises(refutations.RefutationError, match="bucket slug"):
        refutations._policy(_policy(sender=""))


def test_policy_validator_refuses_an_unknown_kind():
    with pytest.raises(refutations.RefutationError, match="kind"):
        refutations._policy(_policy(kind="everything"))


def test_policy_validator_refuses_an_unknown_verb():
    with pytest.raises(refutations.RefutationError, match="verb"):
        refutations._policy(_policy(verb="reject"))


def test_policy_validator_refuses_an_unknown_by():
    with pytest.raises(refutations.RefutationError, match="by"):
        refutations._policy(_policy(by="human"))


def test_policy_validator_hash_is_stable_for_the_same_four_fields():
    a = refutations._policy(_policy())
    b = refutations._policy(_policy())
    assert a["sha256"] == b["sha256"]


def test_policy_validator_hash_changes_with_sender():
    a = refutations._policy(_policy())
    b = refutations._policy(_policy(sender="p-other"))
    assert a["sha256"] != b["sha256"]


def test_assert_ruling_stores_the_request_policy_on_the_founding_row(
        tmp_checkpoint_dir):
    ruling_id = _rule(request_policy=_policy())
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["request_policy"]["sender"] == "p-sender"
    assert record["state"] == "candidate"


def test_assert_refutation_never_carries_a_request_policy():
    """Only `assert_ruling` takes the kwarg; `assert_refutation` has no
    parameter for it at all, so passing one is a TypeError, not a silent
    drop — the same posture `check` already holds for refutations."""
    with pytest.raises(TypeError):
        refutations.assert_refutation(
            subject="x", verdict="y", scope="z", evidence=["issue:961"],
            channel="cli-agent", request_policy=_policy(),
            project_dir=PROJECT)


def test_revise_refuses_a_request_policy_on_a_refutation(tmp_checkpoint_dir):
    ref_id = _refute()
    before = refutations.get(ref_id, project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="only a ruling"):
        refutations.revise(
            ref_id, channel="cli-agent", evidence=["issue:961"],
            request_policy=_policy(), project_dir=PROJECT)
    after = refutations.get(ref_id, project_dir=PROJECT)
    assert after["revision"] == before["revision"]
    assert "request_policy" not in after


def test_revise_with_only_a_request_policy_is_a_change(tmp_checkpoint_dir):
    ruling_id = _rule()
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:961"],
        request_policy=_policy(), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["request_policy"]["sender"] == "p-sender"


def test_revise_refuses_both_setting_and_clearing_request_policy(
        tmp_checkpoint_dir):
    ruling_id = _rule(request_policy=_policy())
    with pytest.raises(refutations.RefutationError, match="cannot both"):
        refutations.revise(
            ruling_id, channel="cli-agent", evidence=["issue:961"],
            request_policy=_policy(sender="p-other"),
            clear_request_policy=True, project_dir=PROJECT)


def test_clear_request_policy_writes_an_explicit_null(tmp_checkpoint_dir):
    """#961 slice 4: presence, not truthiness — the fold must be able to
    tell "revised, policy removed" apart from "revised, policy untouched"."""
    ruling_id = _rule(channel="cli-tty", ratified=True, request_policy=_policy())
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:961"],
        clear_request_policy=True, project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["request_policy"] is None


def test_an_ordinary_revise_leaves_request_policy_untouched(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, request_policy=_policy())
    refutations.revise(
        ruling_id, channel="cli-tty", evidence=["issue:961"],
        verdict="a different verdict text", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["request_policy"]["sender"] == "p-sender"


def test_ratify_pinned_to_the_current_policy_hash_activates(
        tmp_checkpoint_dir):
    ruling_id = _rule(request_policy=_policy())
    sha = refutations.get(ruling_id, project_dir=PROJECT)["request_policy"]["sha256"]
    refutations.ratify(ruling_id, channel="cli-tty", policy_sha256=sha,
                       project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"


def test_ratify_pinned_to_a_stale_policy_hash_is_inert(tmp_checkpoint_dir):
    ruling_id = _rule(request_policy=_policy())
    stale = refutations.get(ruling_id, project_dir=PROJECT)["request_policy"]["sha256"]
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:961"],
        request_policy=_policy(sender="p-other"), project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", policy_sha256=stale,
                       project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_ratify_without_a_pin_is_inert_when_the_record_carries_a_policy(
        tmp_checkpoint_dir):
    ruling_id = _rule(request_policy=_policy())
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "candidate"


def test_ratify_without_a_pin_still_activates_a_ruling_with_no_policy(
        tmp_checkpoint_dir):
    ruling_id = _rule()
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    assert refutations.get(ruling_id, project_dir=PROJECT)["state"] == "active"


def test_human_in_process_revise_arms_the_supplied_policy_without_a_pin(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, request_policy=_policy())
    refutations.revise(
        ruling_id, channel="signed", evidence=["issue:961"],
        request_policy=_policy(sender="p-other"), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["request_policy"]["sender"] == "p-other"


def test_policy_carrying_proposal_is_visible_on_the_record(tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True)
    refutations.revise(
        ruling_id, channel="cli-agent", evidence=["issue:961"],
        request_policy=_policy(), project_dir=PROJECT)
    record = refutations.get(ruling_id, project_dir=PROJECT)
    assert record["revision_proposed"]["request_policy"]["sender"] == "p-sender"
    # The active record's own policy is untouched by an agent proposal.
    assert record.get("request_policy") is None


def test_a_policy_ruling_counts_against_the_ruling_cap(tmp_checkpoint_dir,
                                                        monkeypatch):
    monkeypatch.setattr(config, "ruling_cap", lambda: 1)
    _rule(channel="cli-tty", ratified=True, request_policy=_policy(),
         subject="first")
    with pytest.raises(refutations.RefutationError, match="cap"):
        _rule(channel="cli-tty", ratified=True, subject="second",
             verdict="a different verdict")


# ---- refutations.active_request_policies -----------------------------------


def test_active_request_policies_returns_empty_with_no_rulings(
        tmp_checkpoint_dir):
    assert refutations.active_request_policies(project_dir=PROJECT) == frozenset()


def test_active_request_policies_returns_an_active_pinned_grant(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, request_policy=_policy())
    sha = refutations.get(ruling_id, project_dir=PROJECT)["request_policy"]["sha256"]
    out = refutations.active_request_policies(project_dir=PROJECT)
    assert ("p-sender", "work", "accept", "agent", ruling_id, sha) in out


def test_active_request_policies_excludes_a_candidate_ruling(
        tmp_checkpoint_dir):
    _rule(request_policy=_policy())  # never ratified
    assert refutations.active_request_policies(project_dir=PROJECT) == frozenset()


def test_active_request_policies_excludes_an_overturned_ruling(
        tmp_checkpoint_dir):
    ruling_id = _rule(channel="cli-tty", ratified=True, request_policy=_policy())
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    assert refutations.active_request_policies(project_dir=PROJECT) == frozenset()


def test_active_request_policies_excludes_a_ruling_with_no_policy(
        tmp_checkpoint_dir):
    _rule(channel="cli-tty", ratified=True)
    assert refutations.active_request_policies(project_dir=PROJECT) == frozenset()


def test_active_request_policies_is_fail_open_on_an_unreadable_ledger(
        tmp_checkpoint_dir, monkeypatch):
    """Mirrors briefing.active_rulings' own fail-open posture (#940): an
    unreadable ruling ledger must read as NO policy, never raise."""
    monkeypatch.setattr(refutations, "records",
                        lambda project_dir=None: (_ for _ in ()).throw(
                            OSError("simulated ledger failure")))
    assert refutations.active_request_policies(project_dir=PROJECT) == frozenset()
