"""#1087: judgeable decide rows for pending amendments.

Design: vault decisions/2026-09-22-amendment-confirm-fatigue-design.md.

The `decide` row for a quote-verified amendment used to show the evidence
quote and a bare item id — undecidable by construction (pending.py's own
`_amendment_rows` docstring already said so). This tests the fix: the row
carries the target loop's own text, its current state and the claimed
change, and a neutral `found` line that says WHERE a quote was found
(never WHO said it, since a plain-text `daimon serialize` can turn any file
into a "user" message). Multi-id `amend ratify`/`reject` are all-or-nothing;
rows sharing identical evidence text collapse into one fan-out card.
"""

import json

import pytest

from daimon_briefing import amendments, capture, cli, pending, store, transcript


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


ITEM = "o-1234567890ab"


def _checkpoint_with_item(item_id=ITEM, text="ship the fix"):
    return {
        "session_id": f"S-{item_id}",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"id": item_id, "text": text, "trust": "inferred"}],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def _write_jsonl(path, objs):
    path.write_text("\n".join(json.dumps(o) for o in objs) + "\n",
                    encoding="utf-8")
    return path


def _propose_and_verify_via_transcript(project, tmp_path, *, evidence,
                                       jsonl_objs, item_id=ITEM,
                                       change="progressed",
                                       fname="session.jsonl"):
    """The shipping pipeline, exactly as the design's Tests section requires:
    propose as an agent candidate, then byte-check it through REAL messages
    from `transcript.from_file` on a jsonl fixture, run through
    `capture._verify_agent_amendments` — never `amendments.verify(role=...)`
    with a hand-picked role, which would only assert its own fixture."""
    a_id = amendments.propose(item_id=item_id, change=change,
                              evidence=evidence, channel="cli-agent",
                              project_dir=project)
    p = _write_jsonl(tmp_path / fname, jsonl_objs)
    messages = transcript.from_file(p)
    confirmed = capture._verify_agent_amendments(project, messages)
    assert confirmed == 1
    return a_id


# ---- found: WHERE the quote was found, never WHO said it -------------------


def test_found_in_assistant_turn_renders_agents_own_words(
        project, tmp_path, capsys):
    a_id = _propose_and_verify_via_transcript(
        project, tmp_path,
        evidence="Codex Part A: all four measured, all pass.",
        jsonl_objs=[
            {"type": "assistant", "uuid": "a-1", "message": {
                "role": "assistant", "content": [
                    {"type": "text",
                     "text": "Codex Part A: all four measured, all pass."}]}},
        ])
    assert amendments.get(a_id, project_dir=project)["evidence_role"] == "assistant"
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "agent's own words ⚠" in out


def test_found_in_user_turn_renders_neutral_never_you_said(
        project, tmp_path, capsys):
    a_id = _propose_and_verify_via_transcript(
        project, tmp_path,
        evidence="the follow-up issue is filed",
        jsonl_objs=[
            {"type": "user", "uuid": "u-1", "message": {
                "role": "user",
                "content": "ok, the follow-up issue is filed now"}},
        ])
    assert amendments.get(a_id, project_dir=project)["evidence_role"] == "user"
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in a user turn" in out
    assert "you said" not in out.lower()


def test_found_in_tool_result_renders_in_tool_output(
        project, tmp_path, capsys):
    a_id = _propose_and_verify_via_transcript(
        project, tmp_path,
        evidence="42 passed in 1.2s",
        jsonl_objs=[
            {"type": "user", "uuid": "u-1",
             "message": {"role": "user", "content": "run the tests"}},
            {"type": "assistant", "uuid": "a-2", "message": {
                "role": "assistant", "content": [
                    {"type": "text", "text": "running the suite"},
                    {"type": "tool_use", "name": "Bash",
                     "input": {"command": "pytest"}}]}},
            {"type": "user", "uuid": "t-3", "message": {
                "role": "user", "content": [
                    {"type": "tool_result", "content": "42 passed in 1.2s"}]},
             "toolUseResult": {"stdout": "42 passed in 1.2s", "stderr": ""}},
        ])
    assert amendments.get(a_id, project_dir=project)["evidence_role"] == "tool"
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "in tool output" in out


def test_missing_role_renders_source_unknown(project, capsys):
    # Not reachable through the real pipeline (verify_agent_evidence always
    # returns a role string), so this exercises the fold's own defensive
    # branch directly: a malformed/blank evidence_role on disk.
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="q", channel="cli-agent",
                              project_dir=project)
    row = amendments._stamp("verified", a_id, "mechanical")
    row["evidence_role"] = ""
    assert amendments.append(row, project_dir=project)
    assert amendments.found_label(
        amendments.get(a_id, project_dir=project)["evidence_role"]
    ) == "source unknown ⚠"


def test_found_label_pure_mapping():
    assert amendments.found_label("user") == "in a user turn"
    assert amendments.found_label("tool") == "in tool output"
    assert amendments.found_label("assistant") == "agent's own words ⚠"
    assert amendments.found_label("unknown") == "source unknown ⚠"
    assert amendments.found_label(None) == "source unknown ⚠"
    assert amendments.found_label("") == "source unknown ⚠"


def test_found_label_never_says_you_said():
    for role in ("user", "tool", "assistant", "unknown", None, "", "garbage"):
        assert "you said" not in amendments.found_label(role).lower()


# ---- the row: loop text + current state -------------------------------------


def test_row_shows_loop_text_from_lookup_item(project, capsys):
    store.write_checkpoint("S-1", _checkpoint_with_item(text="ship the fix"),
                           project_dir=project)
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ship the fix" in out
    assert ITEM in out
    assert "progressed" in out


def test_row_loop_text_unavailable_when_lookup_misses_row_stays(
        project, capsys):
    # No checkpoint written for this item at all -> lookup_item misses.
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "(loop text unavailable)" in out
    assert a_id in out  # the row stays in the queue


def test_row_state_defaults_open_arrow_claimed_change(project):
    a_id = amendments.propose(item_id=ITEM, change="blocked",
                              evidence="waiting on infra",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    row = pending.queue(project_dir=project)["rows"][0]
    amend = row["amend"]
    assert amend["state_from"] == "open"
    assert amend["state_to"] == "blocked"


def test_row_state_reads_prior_ratified_amendment_as_baseline(project):
    first = amendments.propose(item_id=ITEM, change="blocked",
                               evidence="waiting on infra",
                               channel="cli-tty", project_dir=project)
    assert amendments.get(first, project_dir=project)["state"] == "ratified"
    second = amendments.propose(item_id=ITEM, change="progressed",
                                evidence="infra cleared this morning",
                                channel="cli-agent", project_dir=project)
    amendments.verify(second, role="assistant", project_dir=project)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["id"] == second
    assert row["amend"]["state_from"] == "blocked"
    assert row["amend"]["state_to"] == "progressed"


# ---- _loop_text / _current_state: unit-level fail-open branches ------------


def test_loop_text_empty_item_id_is_unavailable():
    assert pending._loop_text("", "slug-x") == "(loop text unavailable)"


def test_loop_text_lookup_error_is_unavailable(monkeypatch):
    def boom(item_id, project_dir=None, slug=None):
        raise RuntimeError("index corrupt")

    monkeypatch.setattr(pending.recall, "lookup_item", boom)
    assert pending._loop_text(ITEM, "slug-x") == "(loop text unavailable)"


def test_loop_text_blank_text_field_is_unavailable(monkeypatch):
    monkeypatch.setattr(pending.recall, "lookup_item",
                        lambda item_id, project_dir=None, slug=None:
                        {"text": "   "})
    assert pending._loop_text(ITEM, "slug-x") == "(loop text unavailable)"


def test_loop_text_truncates_over_cap(monkeypatch):
    long_text = "x" * 200
    monkeypatch.setattr(pending.recall, "lookup_item",
                        lambda item_id, project_dir=None, slug=None:
                        {"text": long_text})
    out = pending._loop_text(ITEM, "slug-x")
    assert len(out) == pending._LOOP_TEXT_CAP
    assert out.endswith("…")


def test_current_state_unreadable_records_returns_question_mark():
    assert pending._current_state(None, ITEM, "a-exclude") == "?"


# ---- lookup_item scoping (#1087 fact 4) -------------------------------------


def test_lookup_item_called_with_slug(project, monkeypatch):
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    calls = []
    from daimon_briefing import recall
    real = recall.lookup_item

    def spy(item_id, project_dir=None, slug=None):
        calls.append({"item_id": item_id, "slug": slug})
        return real(item_id, project_dir=project_dir, slug=slug)

    monkeypatch.setattr(pending.recall, "lookup_item", spy)
    pending.queue(project_dir=project)
    assert calls
    assert calls[0]["slug"] == store.project_slug(project)


def test_extra_read_slug_loop_text_never_leaks_across_projects(
        project, monkeypatch):
    other = "/p/OTHER"
    store.write_checkpoint(
        "S-other",
        _checkpoint_with_item(text="OTHER PROJECT SECRET TEXT"),
        project_dir=other)
    monkeypatch.setenv("DAIMON_EXTRA_READ_SLUGS", store.project_slug(other))
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)
    result = pending.queue(project_dir=project)
    row = result["rows"][0]
    assert "OTHER PROJECT SECRET TEXT" not in json.dumps(row)
    assert row["amend"]["loop_text"] == "(loop text unavailable)"


# ---- fan-out card ------------------------------------------------------------


def test_fanout_of_five_renders_one_card_no_confirm_all(project, capsys):
    quote = "Codex Part A: all four measured, all pass."
    ids = []
    for i in range(5):
        item_id = f"o-{i:012x}"
        store.write_checkpoint(f"S-{i}",
                               _checkpoint_with_item(item_id=item_id,
                                                     text=f"loop {i}"),
                               project_dir=project)
        a_id = amendments.propose(item_id=item_id, change="progressed",
                                  evidence=quote, channel="cli-agent",
                                  project_dir=project)
        amendments.verify(a_id, role="assistant", project_dir=project)
        ids.append(a_id)
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count(quote) == 1
    for a_id in ids:
        assert f"daimon amend ratify {a_id}" in out
    assert "reject all" in out
    assert "confirm all" not in out.lower()
    reject_all_line = next(ln for ln in out.splitlines() if "reject all" in ln)
    for a_id in ids:
        assert a_id in reject_all_line


def test_amendment_card_shows_note_line_when_present():
    from daimon_briefing.cli import lifecycle
    row = {"kind": "amendment", "id": "a-abcabcabcabc",
          "headline": "the PR merged this morning",
          "waiting_since": "", "commands": [
              ("confirm", "daimon amend ratify a-abcabcabcabc"),
              ("reject", "daimon amend reject a-abcabcabcabc")],
          "amend": {"loop_id": ITEM, "loop_text": "ship the fix",
                    "state_from": "open", "state_to": "progressed",
                    "found": "agent's own words ⚠",
                    "note": "left over from a rejected-then-reproposed claim"}}
    card = lifecycle._amendment_card(row)
    assert any("note   left over from a rejected-then-reproposed claim" in ln
              for ln in card)


def test_fanout_differing_evidence_renders_separately(project, capsys):
    a1 = amendments.propose(item_id=ITEM, change="progressed",
                            evidence="quote one", channel="cli-agent",
                            project_dir=project)
    amendments.verify(a1, role="assistant", project_dir=project)
    a2 = amendments.propose(item_id="u-abcdefabcdef", change="progressed",
                            evidence="quote two", channel="cli-agent",
                            project_dir=project)
    amendments.verify(a2, role="assistant", project_dir=project)
    rc = cli.main(["decide"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reject all" not in out


# ---- multi-id verbs, all-or-nothing ------------------------------------------


def test_multi_id_ratify_one_bad_id_writes_nothing_names_it(
        project, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    before = amendments.get(good, project_dir=project)

    rc = cli.main(["amend", "ratify", good, "a-bogusbogus01",
                   "--project", project])

    assert rc != 0
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "a-bogusbogus01" in combined
    assert amendments.get(good, project_dir=project) == before


def test_multi_id_ratify_all_valid_ratifies_every_id(project, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    a1 = amendments.propose(item_id=ITEM, change="progressed",
                            evidence="q1", channel="cli-agent",
                            project_dir=project)
    amendments.verify(a1, role="assistant", project_dir=project)
    a2 = amendments.propose(item_id=ITEM, change="blocked",
                            evidence="q2", channel="cli-agent",
                            project_dir=project)
    amendments.verify(a2, role="assistant", project_dir=project)

    rc = cli.main(["amend", "ratify", a1, a2, "--project", project])

    assert rc == 0
    assert amendments.get(a1, project_dir=project)["state"] == "ratified"
    assert amendments.get(a2, project_dir=project)["state"] == "ratified"


def test_multi_id_ratify_dedupes_a_repeated_id(project, monkeypatch, capsys):
    """`daimon amend ratify a-1 a-1` is a paste/typo artifact, not two
    different things to confirm — it must ratify ONCE, not append a second
    `ratified` event for the same id."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    rc = cli.main(["amend", "ratify", a_id, a_id, "--project", project])

    assert rc == 0
    ratified_events = [
        e for e in amendments.events(project_dir=project)
        if e.get("amendment_id") == a_id and e.get("event") == "ratified"]
    assert len(ratified_events) == 1
    out = capsys.readouterr().out
    assert out.count(f"{a_id}: ratified") == 1


def test_multi_id_reject_dedupes_a_repeated_id(project, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    rc = cli.main(["amend", "reject", a_id, a_id, "--project", project])

    assert rc == 0
    rejected_events = [
        e for e in amendments.events(project_dir=project)
        if e.get("amendment_id") == a_id and e.get("event") == "rejected"]
    assert len(rejected_events) == 1
    out = capsys.readouterr().out
    assert out.count(f"{a_id}: rejected") == 1


def test_multi_id_reject_one_unknown_id_writes_nothing(
        project, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    before = amendments.get(good, project_dir=project)

    rc = cli.main(["amend", "reject", good, "a-doesnotexist9",
                   "--project", project])

    assert rc != 0
    combined = "".join(capsys.readouterr())
    assert "a-doesnotexist9" in combined
    assert amendments.get(good, project_dir=project) == before


def test_multi_id_ratify_refuses_agent_channel(project):
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="q1", channel="cli-agent",
                              project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    rc = cli.main(["amend", "ratify", a_id, "--by", "agent",
                   "--project", project])

    assert rc == 1
    assert amendments.get(a_id, project_dir=project)["state"] == "verified"


def test_multi_id_ratify_refuses_non_tty(project, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="q1", channel="cli-agent",
                              project_dir=project)
    amendments.verify(a_id, role="assistant", project_dir=project)

    rc = cli.main(["amend", "ratify", a_id, "--project", project])

    assert rc == 1
    assert amendments.get(a_id, project_dir=project)["state"] == "verified"


def test_ratify_many_all_or_nothing_library_level(project):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    with pytest.raises(amendments.AmendmentError) as exc_info:
        amendments.ratify_many([good, "a-unknownunkn0"], channel="cli-tty",
                               project_dir=project)
    assert "a-unknownunkn0" in str(exc_info.value)
    assert amendments.get(good, project_dir=project)["state"] == "verified"


def test_reject_many_all_or_nothing_library_level(project):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    with pytest.raises(amendments.AmendmentError) as exc_info:
        amendments.reject_many([good, "a-unknownunkn0"], channel="cli-tty",
                               project_dir=project)
    assert "a-unknownunkn0" in str(exc_info.value)
    assert amendments.get(good, project_dir=project)["state"] == "verified"


def test_ratify_many_requires_human_channel(project):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    with pytest.raises(amendments.AmendmentError):
        amendments.ratify_many([good], channel="cli-agent",
                               project_dir=project)


def test_reject_many_requires_human_channel(project):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    with pytest.raises(amendments.AmendmentError):
        amendments.reject_many([good], channel="cli-agent",
                               project_dir=project)


def test_ratify_many_refuses_an_already_ratified_id(project):
    already = amendments.propose(item_id=ITEM, change="progressed",
                                 evidence="q1", channel="cli-tty",
                                 project_dir=project)
    assert amendments.get(already, project_dir=project)["state"] == "ratified"
    with pytest.raises(amendments.AmendmentError) as exc_info:
        amendments.ratify_many([already], channel="cli-tty",
                               project_dir=project)
    assert "ratified" in str(exc_info.value)


def test_ratify_many_write_failure_raises_and_names_the_id(
        project, monkeypatch):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    with pytest.raises(amendments.AmendmentError, match="not written"):
        amendments.ratify_many([good], channel="cli-tty",
                               project_dir=project)


def test_reject_many_write_failure_raises_and_names_the_id(
        project, monkeypatch):
    good = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(good, role="assistant", project_dir=project)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    with pytest.raises(amendments.AmendmentError, match="not written"):
        amendments.reject_many([good], channel="cli-tty",
                               project_dir=project)


# ---- briefing + MCP: added `found` line, pinned literal untouched ----------


def test_briefing_line_carries_added_found_line_pinned_literal_unchanged():
    from daimon_briefing import briefing
    item = {"id": ITEM, "text": "ship the fix", "trust": "inferred",
            "_amend": {"rows": [
                {"id": "a-abcabcabcabc", "change": "progressed",
                 "quote": "the PR merged this morning",
                 "label": "quote-verified", "role": "assistant",
                 "state": "verified", "by": "agent", "note": ""}],
                "overflow": 0}}
    line = briefing._line(item, briefable=True)
    assert ('⚠ agent-proposed amendment — progressed (quote-verified, '
           'role: assistant), unconfirmed: '
           '"the PR merged this morning"') in line
    assert "confirm: daimon amend ratify a-abcabcabcabc" in line
    assert "reject: daimon amend reject a-abcabcabcabc" in line
    assert "found: agent's own words ⚠" in line


def test_briefing_line_found_for_user_role_is_neutral():
    from daimon_briefing import briefing
    item = {"id": ITEM, "text": "ship the fix", "trust": "inferred",
            "_amend": {"rows": [
                {"id": "a-abcabcabcabc", "change": "changed",
                 "quote": "q", "label": "quote-verified", "role": "user",
                 "state": "verified", "by": "agent", "note": ""}],
                "overflow": 0}}
    line = briefing._line(item, briefable=True)
    assert "found: in a user turn" in line
    assert "you said" not in line.lower()


def test_rich_brief_carries_the_found_line(monkeypatch, capsys):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    item = {"id": ITEM, "text": "ship the fix", "trust": "inferred",
            "_amend": {"rows": [
                {"id": "a-abcabcabcabc", "change": "progressed",
                 "quote": "the PR merged this morning",
                 "label": "quote-verified", "role": "tool",
                 "state": "verified", "by": "agent", "note": ""}],
                "overflow": 0}}
    render._rich_brief({"open_loops": [item]})
    out = capsys.readouterr().out
    assert "found: in tool output" in out


def test_mcp_brief_carries_the_found_line(project, monkeypatch):
    store.write_checkpoint("S-1", _checkpoint_with_item(), project_dir=project)
    a_id = amendments.propose(item_id=ITEM, change="progressed",
                              evidence="the PR merged this morning",
                              channel="cli-agent", project_dir=project)
    amendments.verify(a_id, role="user", project_dir=project)
    from daimon_briefing import mcp_tools
    text = mcp_tools.HANDLERS["daimon_brief"]({})
    assert "found: in a user turn" in text
    assert "you said" not in text.lower()
