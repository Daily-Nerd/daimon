"""#1333: the fold names the effect of every row a request verb writes.

The fold is the single owner of request state. Before this, it decided each
row's fate in silence, so a held claim, a discarded claim and a no-op all read
the same to the person who ran the verb. These tests pin the closed effect set
(landed, held as claim, ignored, discarded a claim), that the CLI prints the
fold's word for it and derives nothing itself, and that a claim a `revised`
row clears stays on the record.
"""

import json

import pytest

from daimon_briefing import cli, mcp_tools, requests, store
from daimon_briefing.surfaces import Writer

SENDER = "/p/effects-sender"
OTHER = "/p/effects-recipient"
ASK = "bump the tag"
WHY = "the release needs it"
EVIDENCE = "shipped in abc123"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return SENDER


@pytest.fixture
def recipient(project):
    store.write_checkpoint("S-r", {
        "session_id": "S-r", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "the recipient shipped something", "trust": "inferred"}]},
    }, project_dir=OTHER, writer=Writer.HUMAN)
    return store.project_slug(OTHER)


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _self_ask(project):
    return requests.open_request(
        to=store.project_slug(project), ask=ASK, why=WHY,
        channel="cli-agent", project_dir=project)


def _foreign_ask(project, recipient):
    return requests.open_request(
        to=recipient, ask=ASK, why=WHY, channel="cli-agent",
        project_dir=project)


def _effect(record, event_id):
    return record["row_effects"][event_id]


def _out(capsys):
    return capsys.readouterr().out


# ---- the fold names the fate of each row -----------------------------------


def test_an_agent_done_before_accept_is_held_as_a_claim(project):
    q_id = _self_ask(project)
    event_id = requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                             project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "open" and record["done_pending"] is True
    assert _effect(record, event_id)["effect"] == "held_as_claim"


def test_a_revise_over_a_pending_claim_discards_it_and_keeps_it(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    event_id = requests.revise(q_id, channel="cli-agent",
                               ask="delete the tag instead",
                               project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert _effect(record, event_id)["effect"] == "discarded_claim"
    assert record["done_pending"] is False and record["done_evidence"] == ""
    (kept,) = record["superseded_claims"]
    assert kept["evidence"] == EVIDENCE
    assert kept["by"] == "agent"
    assert kept["revision"] == 0
    assert kept["author"]


def test_a_superseded_claim_survives_the_verdict_and_every_refold(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="delete the tag instead",
                    project_dir=project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    first = requests.get(q_id, project_dir=project)
    assert first["state"] == "accepted"
    assert [c["evidence"] for c in first["superseded_claims"]] == [EVIDENCE]
    again = requests.get(q_id, project_dir=project)
    assert again["superseded_claims"] == first["superseded_claims"]


def test_a_second_claim_cleared_by_a_second_revise_is_kept_in_order(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence="first claim",
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="a", project_dir=project)
    requests.done(q_id, channel="cli-agent", evidence="second claim",
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="b", project_dir=project)
    kept = requests.get(q_id, project_dir=project)["superseded_claims"]
    assert [(c["evidence"], c["revision"]) for c in kept] == [
        ("first claim", 0), ("second claim", 1)]


def test_a_revise_with_no_claim_discards_nothing(project):
    q_id = _self_ask(project)
    event_id = requests.revise(q_id, channel="cli-agent", ask="a",
                               project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert _effect(record, event_id)["effect"] == "landed"
    assert record["superseded_claims"] == []


def test_a_verdict_on_a_finished_record_is_ignored_and_says_why(project):
    q_id = _self_ask(project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    accepted = requests.accept(q_id, channel="cli-tty", project_dir=project)
    rejected = requests.reject(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "done" and record["done_claimed"] is True
    for event_id in (accepted, rejected):
        got = _effect(record, event_id)
        assert got["effect"] == "ignored"
        # a claim still waiting on the byte-check is named in the reason
        assert got["reason"] == "already_done_unverified"


def test_a_verdict_on_a_human_done_is_ignored_without_the_byte_check(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-tty", evidence=EVIDENCE,
                  project_dir=project)
    event_id = requests.reject(q_id, channel="cli-tty", project_dir=project)
    got = _effect(requests.get(q_id, project_dir=project), event_id)
    assert (got["effect"], got["reason"]) == ("ignored", "already_done")


def test_a_repeated_suppress_is_ignored_the_first_lands(project):
    q_id = _self_ask(project)
    first = requests.suppress(q_id, channel="cli-tty", project_dir=project)
    second = requests.suppress(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert _effect(record, first)["effect"] == "landed"
    got = _effect(record, second)
    assert (got["effect"], got["reason"]) == ("ignored", "already_suppressed")


def test_a_repeated_needs_info_lands_and_says_it_was_a_repeat(project):
    q_id = _self_ask(project)
    first = requests.needs_info(q_id, channel="cli-tty", note="which tag?",
                                project_dir=project)
    second = requests.needs_info(q_id, channel="cli-tty", note="which tag?",
                                 project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert _effect(record, first)["reason"] == "needs_info"
    got = _effect(record, second)
    assert (got["effect"], got["reason"]) == ("landed", "needs_info_again")


def test_an_accept_on_a_pending_claim_lands_as_a_ratified_claim(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    event_id = requests.accept(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "done"
    got = _effect(record, event_id)
    assert (got["effect"], got["reason"]) == ("landed", "claim_ratified")


def test_a_forged_agent_accept_is_ignored_as_needing_a_human(project):
    q_id = _self_ask(project)
    row = requests._stamp("accepted", q_id, "cli-agent")
    assert requests.append(row, project_dir=project, writer=Writer.HUMAN)
    record = requests.get(q_id, project_dir=project)
    got = _effect(record, row["event_id"])
    assert (got["effect"], got["reason"]) == ("ignored", "needs_human_channel")
    assert record["state"] == "open"


def test_a_revise_on_a_decided_record_is_ignored(project):
    q_id = _self_ask(project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    row = requests._stamp("revised", q_id, "cli-agent")
    row["ask"] = "late"
    assert requests.append(row, project_dir=project, writer=Writer.HUMAN)
    got = _effect(requests.get(q_id, project_dir=project), row["event_id"])
    assert (got["effect"], got["reason"]) == ("ignored", "not_revisable")


def test_a_done_without_evidence_is_ignored(project):
    q_id = _self_ask(project)
    row = requests._stamp("done", q_id, "cli-agent")
    assert requests.append(row, project_dir=project, writer=Writer.HUMAN)
    got = _effect(requests.get(q_id, project_dir=project), row["event_id"])
    assert (got["effect"], got["reason"]) == ("ignored", "done_needs_evidence")


def test_a_reply_before_accept_is_ignored(project):
    q_id = _self_ask(project)
    row = requests._stamp("replied", q_id, "cli-agent")
    row["note"] = "working on it"
    assert requests.append(row, project_dir=project, writer=Writer.HUMAN)
    got = _effect(requests.get(q_id, project_dir=project), row["event_id"])
    assert (got["effect"], got["reason"]) == ("ignored", "reply_needs_accept")


def test_a_reply_on_an_accepted_ask_lands(project):
    q_id = _self_ask(project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    event_id = requests.reply(q_id, "halfway", channel="cli-agent",
                              project_dir=project)
    got = _effect(requests.get(q_id, project_dir=project), event_id)
    assert (got["effect"], got["reason"]) == ("landed", "reply_recorded")


def test_every_effect_comes_from_the_closed_set(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="x", project_dir=project)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    seen = {e["effect"] for e in record["row_effects"].values()}
    assert seen <= requests.EFFECTS
    assert {"landed", "held_as_claim", "ignored",
            "discarded_claim"} == requests.EFFECTS


def test_every_reason_belongs_to_one_effect_and_has_a_printable_line():
    for reason, (effect, template) in requests._EFFECT_REASONS.items():
        assert effect in requests.EFFECTS, reason
        line = requests.effect_line("q-0123456789ab", {
            "effect": effect, "reason": reason, "revision": 2})
        assert line.startswith("q-0123456789ab: "), reason
        assert "{" not in line, reason


def test_no_line_is_made_up_for_a_row_the_fold_did_not_record():
    assert requests.effect_line("q-0123456789ab", None) == ""
    assert requests.effect_line("q-0123456789ab", {}) == ""
    assert requests.effect_line(
        "q-0123456789ab", {"effect": "landed", "reason": "no-such-reason"}) == ""


# ---- the CLI says what the row it wrote did --------------------------------


def test_cli_done_before_accept_says_it_is_held_as_a_claim(project, capsys):
    q_id = _self_ask(project)
    assert cli.main(["request", "done", q_id, "--evidence", EVIDENCE,
                     "--by", "agent", "--project", project]) == 0
    out = _out(capsys)
    assert "still waits on a person" in out
    assert f"daimon request accept {q_id}" in out


def test_cli_done_on_a_foreign_ask_says_it_is_held_as_a_claim(
        project, recipient, capsys):
    q_id = _foreign_ask(project, recipient)
    assert cli.main(["request", "done", q_id, "--evidence", EVIDENCE,
                     "--by", "agent", "--project", OTHER]) == 0
    out = _out(capsys)
    assert "still waits on a person" in out


def test_cli_revise_names_the_claim_it_discarded(project, capsys):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    assert cli.main(["request", "revise", q_id, "--ask", "delete it instead",
                     "--by", "agent", "--project", project]) == 0
    out = _out(capsys)
    assert "discarded" in out
    assert "revision 0" in out
    assert "Superseded claim" in out and EVIDENCE in out


def test_cli_accept_after_a_discard_shows_the_superseded_claim(
        project, tty, capsys):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="delete it instead",
                    project_dir=project)
    assert cli.main(["request", "accept", q_id, "--project", project]) == 0
    out = _out(capsys)
    assert "Superseded claim" in out and EVIDENCE in out


def test_cli_accept_on_an_unverified_done_is_a_noop_naming_the_byte_check(
        project, tty, capsys):
    q_id = _self_ask(project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    assert cli.main(["request", "accept", q_id, "--project", project]) == 0
    out = _out(capsys)
    assert "no-op" in out
    assert "byte-check" in out
    assert "unverified" in out


def test_cli_reject_on_a_finished_record_is_a_noop(project, tty, capsys):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-tty", evidence=EVIDENCE,
                  project_dir=project)
    assert cli.main(["request", "reject", q_id, "--project", project]) == 0
    out = _out(capsys)
    assert "no-op" in out and "already done" in out


def test_cli_repeated_suppress_is_a_noop_and_the_first_is_not(
        project, tty, capsys):
    q_id = _self_ask(project)
    assert cli.main(["request", "suppress", q_id, "--project", project]) == 0
    assert "no-op" not in _out(capsys)
    assert cli.main(["request", "suppress", q_id, "--project", project]) == 0
    assert "no-op" in _out(capsys)


def test_cli_repeated_needs_info_says_it_was_already_waiting(
        project, tty, capsys):
    q_id = _self_ask(project)
    assert cli.main(["request", "needs-info", q_id, "--note", "which?",
                     "--project", project]) == 0
    capsys.readouterr()
    assert cli.main(["request", "needs-info", q_id, "--note", "which?",
                     "--project", project]) == 0
    assert "already" in _out(capsys)


def test_cli_accept_on_a_pending_claim_says_it_ratified_the_claim(
        project, tty, capsys):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    assert cli.main(["request", "accept", q_id, "--project", project]) == 0
    assert "ratified" in _out(capsys)


def test_cli_reply_says_it_was_recorded(project, capsys):
    q_id = _self_ask(project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    assert cli.main(["request", "reply", q_id, "--note", "halfway",
                     "--by", "agent", "--project", project]) == 0
    assert "reply recorded" in _out(capsys)


# ---- the cross-bucket path gets the effect too ------------------------------


def test_cross_bucket_accept_over_a_claim_is_named_through_the_join(
        project, recipient, tty, capsys):
    q_id = _foreign_ask(project, recipient)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=OTHER)
    assert cli.main(["request", "accept", q_id, "--project", OTHER]) == 0
    assert "ratified" in _out(capsys)


def test_cross_bucket_repeated_accept_is_a_noop_naming_the_byte_check(
        project, recipient, tty, capsys):
    q_id = _foreign_ask(project, recipient)
    requests.accept(q_id, channel="cli-tty", project_dir=OTHER)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=OTHER)
    assert cli.main(["request", "accept", q_id, "--project", OTHER]) == 0
    out = _out(capsys)
    assert "no-op" in out and "byte-check" in out


# ---- the superseded claim renders on every read surface ---------------------


def _discarded(project):
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=EVIDENCE,
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="delete it instead",
                    project_dir=project)
    return q_id


def test_request_list_renders_the_superseded_claim(project, capsys):
    _discarded(project)
    assert cli.main(["request", "list", "--project", project]) == 0
    out = _out(capsys)
    assert "Superseded claim" in out and EVIDENCE in out


def test_request_inbox_renders_the_superseded_claim(project, capsys):
    _discarded(project)
    assert cli.main(["request", "inbox", "--project", project]) == 0
    out = _out(capsys)
    assert "Superseded claim" in out and EVIDENCE in out


def test_request_json_carries_the_superseded_claim(project, capsys):
    _discarded(project)
    assert cli.main(["request", "list", "--json", "--project", project]) == 0
    rows = json.loads(_out(capsys))
    assert rows[0]["superseded_claims"][0]["evidence"] == EVIDENCE


def test_the_mcp_requests_inbox_carries_the_superseded_claim(
        project, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    _discarded(project)
    rows = json.loads(mcp_tools.HANDLERS["requests_inbox"]({}).text)
    assert rows[0]["superseded_claims"][0]["evidence"] == EVIDENCE


# ---- privacy: a superseded claim is prose, so it is declared and masked -----


def test_superseded_claim_evidence_is_a_declared_folded_prose_path(project):
    from daimon_briefing import surfaces
    from .test_folded_prose_census import _declared, _sentinel_paths, S
    q_id = _self_ask(project)
    requests.done(q_id, channel="cli-agent", evidence=S("claim"),
                  project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask=S("ask"),
                    project_dir=project)
    seen = _sentinel_paths(list(requests.records(project_dir=project).values()))
    assert "superseded_claims[].evidence" in seen
    assert seen - _declared("requests.jsonl") == set()
    assert surfaces.path_string(
        surfaces.FieldPath(("superseded_claims[]", "evidence"))
    ) in _declared("requests.jsonl")
