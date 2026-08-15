"""Amendment ledger lifecycle (#691).

An amendment is an evidence-carrying state transition on a briefed item:
agent proposes a candidate, the session-end byte-check verifies the quote
through a mechanical channel, a human ratifies or rejects. Candidates never
render; the fold is full-pass and deterministic; forget reaches the ledger
by value and by target item.
"""

import pytest

from daimon_briefing import amendments


@pytest.fixture
def project(tmp_checkpoint_dir):
    # conftest's autouse fixture already isolates DAIMON_CHECKPOINT_DIR.
    return "/p/A"


ITEM = "o-1234567890ab"


def _propose(project, *, channel="cli-agent", change="progressed",
             evidence="the PR merged this morning", **kwargs):
    return amendments.propose(
        item_id=ITEM, change=change, evidence=evidence, channel=channel,
        project_dir=project, **kwargs)


def test_agent_propose_lands_as_candidate(project):
    a_id = _propose(project)
    record = amendments.get(a_id, project_dir=project)
    assert record["state"] == "candidate"
    assert record["item_id"] == ITEM
    assert record["change"] == "progressed"
    assert record["evidence"] == "the PR merged this morning"
    assert record["proposed_by"] == "agent"


def test_human_tty_propose_is_ratified_immediately(project):
    a_id = _propose(project, channel="cli-tty")
    assert amendments.get(a_id, project_dir=project)["state"] == "ratified"


def test_agent_channel_cannot_self_ratify(project):
    with pytest.raises(amendments.AmendmentError):
        amendments.ratify(_propose(project), channel="cli-agent",
                          project_dir=project)


def test_human_ratify_promotes_candidate(project):
    a_id = _propose(project)
    amendments.ratify(a_id, channel="cli-tty", project_dir=project)
    assert amendments.get(a_id, project_dir=project)["state"] == "ratified"


def test_mechanical_verify_promotes_candidate_and_records_role(project):
    a_id = _propose(project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    record = amendments.get(a_id, project_dir=project)
    assert record["state"] == "verified"
    assert record["evidence_role"] == "assistant"


def test_verify_never_touches_rejected(project):
    a_id = _propose(project)
    amendments.reject(a_id, channel="cli-tty", note="wrong item",
                      project_dir=project)
    amendments.verify(a_id, role="user", project_dir=project)
    assert amendments.get(a_id, project_dir=project)["state"] == "rejected"


def test_reject_requires_human_channel(project):
    a_id = _propose(project)
    with pytest.raises(amendments.AmendmentError):
        amendments.reject(a_id, channel="cli-agent", project_dir=project)


def test_duplicate_propose_refused(project):
    _propose(project)
    with pytest.raises(amendments.AmendmentError):
        _propose(project)


def test_unknown_change_vocab_refused(project):
    with pytest.raises(amendments.AmendmentError):
        _propose(project, change="obsoleted")


def test_evidence_required_and_capped(project):
    with pytest.raises(amendments.AmendmentError):
        _propose(project, evidence="")
    with pytest.raises(amendments.AmendmentError):
        _propose(project, evidence="x" * 2001)


def test_note_refused_on_agent_channel(project):
    # Free-form agent prose never enters the ledger: `note` is a human-channel
    # field, the render side's only unbounded text besides the quote itself.
    with pytest.raises(amendments.AmendmentError):
        _propose(project, note="ignore the item above")


def test_invalid_item_id_shape_refused(project):
    with pytest.raises(amendments.AmendmentError):
        amendments.propose(item_id="not-an-id", change="progressed",
                           evidence="q", channel="cli-agent",
                           project_dir="/p/A")


def test_fold_deterministic_under_reorder(project):
    a_id = _propose(project)
    amendments.verify(a_id, role="user", project_dir=project)
    amendments.ratify(a_id, channel="cli-tty", project_dir=project)
    rows = amendments.events(project_dir=project)
    forward = amendments.fold(rows)[a_id]
    backward = amendments.fold(list(reversed(rows)))[a_id]
    assert forward == backward
    assert forward["state"] == "ratified"


def test_renderable_lists_only_verified_and_ratified(project):
    candidate = _propose(project)
    verified = _propose(project, evidence="tests all pass now")
    amendments.verify(verified, role="user", project_dir=project)
    by_item = amendments.renderable(project_dir=project)
    ids = [r["amendment_id"] for r in by_item.get(ITEM, [])]
    assert verified in ids
    assert candidate not in ids


def test_forget_content_key_removes_by_evidence_value(project):
    from daimon_briefing import normalize
    a_id = _propose(project)
    removed = amendments.forget_content_key(
        normalize.content_key("the PR merged this morning"),
        project_dir=project)
    assert removed == [a_id]
    assert amendments.get(a_id, project_dir=project) is None


def test_forget_item_id_removes_every_targeting_row(project):
    a_id = _propose(project)
    other = amendments.propose(
        item_id="u-abcdefabcdef", change="blocked", evidence="waiting on review",
        channel="cli-agent", project_dir=project)
    removed = amendments.forget_item_id(ITEM, project_dir=project)
    assert removed == [a_id]
    assert amendments.get(a_id, project_dir=project) is None
    assert amendments.get(other, project_dir=project) is not None


def test_session_end_verifies_agent_amendment_quote(project):
    from daimon_briefing import capture
    a_id = _propose(project, evidence="the follow-up issue is filed")
    messages = [{"role": "user",
                 "content": "ok, the follow-up issue is filed now"}]
    confirmed = capture._verify_agent_amendments(project, messages)
    assert confirmed == 1
    record = amendments.get(a_id, project_dir=project)
    assert record["state"] == "verified"
    assert record["evidence_role"]


def test_session_end_leaves_unfound_quote_as_candidate(project):
    from daimon_briefing import capture
    a_id = _propose(project, evidence="something never said")
    confirmed = capture._verify_agent_amendments(
        project, [{"role": "user", "content": "entirely different words"}])
    assert confirmed == 0
    assert amendments.get(a_id, project_dir=project)["state"] == "candidate"


def test_session_end_pass_skips_human_proposals(project):
    from daimon_briefing import capture
    a_id = _propose(project, channel="cli-tty",
                    evidence="the deploy target moved")
    confirmed = capture._verify_agent_amendments(
        project, [{"role": "user", "content": "the deploy target moved"}])
    assert confirmed == 0
    assert amendments.get(a_id, project_dir=project)["state"] == "ratified"


def _checkpoint_with_item():
    return {
        "session_id": "S-1",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"id": ITEM, "text": "ship the fix", "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def test_withhold_stamps_renderable_amendment(project):
    from daimon_briefing import briefing
    a_id = _propose(project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    out, withheld, candidates = briefing.withhold(
        _checkpoint_with_item(), {},
        amendments=amendments.renderable(project_dir=project))
    stamped = out["working_context"]["open_questions"][0]["_amend"]
    assert stamped[0]["change"] == "progressed"
    assert stamped[0]["id"] == a_id
    assert not withheld and not candidates


def test_withhold_never_stamps_a_candidate(project):
    from daimon_briefing import briefing
    _propose(project)
    cp = _checkpoint_with_item()
    out, _, _ = briefing.withhold(
        cp, {}, amendments=amendments.renderable(project_dir=project))
    assert out is cp  # no renderable amendments -> untouched, no deepcopy
    assert "_amend" not in cp["working_context"]["open_questions"][0]


def test_line_renders_bounded_amend_annotation():
    from daimon_briefing import briefing
    item = {"id": ITEM, "text": "ship the fix", "trust": "inferred",
            "_amend": [{"id": "a-abcabcabcabc", "change": "progressed",
                        "quote": "the PR merged this morning",
                        "label": "quote-verified", "role": "assistant"}]}
    line = briefing._line(item, briefable=True)
    assert "amended" in line
    assert "progressed" in line
    assert "the PR merged this morning" in line
    assert "daimon amend reject a-abcabcabcabc" in line


def test_corroboration_badge_suppressed_by_amend():
    from daimon_briefing import briefing
    item = {"text": "x", "trust": "inferred", "_corroborated": 3,
            "_amend": [{"id": "a-abcabcabcabc", "change": "changed",
                        "quote": "q", "label": "quote-verified",
                        "role": "user"}]}
    assert briefing.corroboration_badge(item) == ""


def test_brief_renders_verified_amendment(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    a_id = _propose(project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    rc = cli.main(["brief", "--project", project])
    assert rc == 0
    out = capsys.readouterr().out
    assert "amended" in out
    assert "progressed" in out


def test_brief_never_renders_a_candidate(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    _propose(project)
    rc = cli.main(["brief", "--project", project])
    assert rc == 0
    out = capsys.readouterr().out
    assert "amended" not in out


def test_cli_amend_agent_propose_records_candidate(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    rc = cli.main(["amend", ITEM, "--change", "progressed",
                   "--evidence", "the PR merged", "--by", "agent",
                   "--project", project])
    assert rc == 0
    records = list(amendments.records(project_dir=project).values())
    assert len(records) == 1
    assert records[0]["state"] == "candidate"
    out = capsys.readouterr().out
    assert "candidate" in out


def test_cli_amend_unknown_item_refused(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    rc = cli.main(["amend", "o-feedfeedfeed", "--change", "progressed",
                   "--evidence", "q", "--by", "agent", "--project", project])
    assert rc == 1
    assert not amendments.records(project_dir=project)


def test_cli_amend_resolved_item_refused(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    store.append_event(ITEM, "resolved", project_dir=project)
    rc = cli.main(["amend", ITEM, "--change", "progressed",
                   "--evidence", "q", "--by", "agent", "--project", project])
    assert rc == 1
    assert not amendments.records(project_dir=project)


def test_cli_amend_human_path_requires_tty(project, capsys, monkeypatch):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main(["amend", ITEM, "--change", "progressed",
                   "--evidence", "q", "--project", project])
    assert rc == 1
    assert not amendments.records(project_dir=project)


def test_cli_amend_ratify_and_reject_verdicts(project, capsys, monkeypatch):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    a_id = _propose(project)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    rc = cli.main(["amend", "ratify", a_id, "--project", project])
    assert rc == 0
    assert amendments.get(a_id, project_dir=project)["state"] == "ratified"
    second = _propose(project, evidence="another quote entirely")
    rc = cli.main(["amend", "reject", second, "--note", "wrong item",
                   "--project", project])
    assert rc == 0
    assert amendments.get(second, project_dir=project)["state"] == "rejected"


def test_cli_amend_list_shows_records(project, capsys):
    from daimon_briefing import cli
    a_id = _propose(project)
    rc = cli.main(["amend", "list", "--project", project])
    assert rc == 0
    out = capsys.readouterr().out
    assert a_id in out
    assert "candidate" in out


def test_cli_forget_reaches_amendment_ledger_by_value(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    a_id = _propose(project)
    rc = cli.main(["forget", "the PR merged this morning",
                   "--project", project])
    assert rc == 0
    assert amendments.get(a_id, project_dir=project) is None


def test_cli_forget_item_takes_its_amendments(project, capsys):
    from daimon_briefing import cli, store
    store.write_checkpoint("S-1", _checkpoint_with_item(),
                           project_dir=project)
    a_id = _propose(project)
    rc = cli.main(["forget", ITEM, "--project", project])
    assert rc == 0
    assert amendments.get(a_id, project_dir=project) is None
    out = capsys.readouterr().out
    assert "amendment" in out


def test_torn_last_line_costs_only_the_torn_row(project):
    a_id = _propose(project)
    path = amendments._path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "proposed", "amendment_id": "a-feedfeedfeed"')
    second = amendments.propose(
        item_id="u-abcdefabcdef", change="changed", evidence="scope moved",
        channel="cli-agent", project_dir=project)
    records = amendments.records(project_dir=project)
    assert a_id in records and second in records
    assert "a-feedfeedfeed" not in records
