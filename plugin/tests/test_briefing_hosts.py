"""The briefing hosts read through the view (#1132 PR 7a): prose masking in the
rulings and panels, the closed view, the ledger-health notes, the withheld
trailer, teammates and the routes. Stores are written by the real writers."""

import time

import pytest

from daimon_briefing import (amendments, briefing, cli, hooks, mcp_tools,
                             normalize, refutations, requests, store, trust)

PROJECT = "/p/hosts"
SENDER = "/p/hosts-sender"
Q = "SENTINEL-quarantined ship the migration on Friday"
F = "SENTINEL-forgotten adopt the strangler pattern"
KEPT = "an unrelated decision that stays visible"
MARKER_Q = "[withheld: quarantine tr-"
MARKER_F = "[withheld: forgotten]"


def _cp(*decisions, topic=None):
    wc = {"recent_decisions": [{"text": t, "trust": "inferred"}
                               for t in decisions]}
    if topic:
        wc["active_topic"] = {"text": topic, "trust": "inferred"}
    return {"session_id": "S-1", "created": "2026-08-01T00:00:00Z",
            "working_context": wc, "epistemic_snapshot": {}}


def _seed(*decisions, project=PROJECT, **kw):
    store.write_checkpoint("S-1", _cp(*(decisions or (KEPT,)), **kw),
                           project_dir=project)


def _quarantine(text=Q, kind="decision", project=PROJECT):
    return trust.propose(text=text, kind=kind, reason="made up",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


def _forget(text=F, project=PROJECT):
    key = normalize.content_key(text)
    store.append_event("i-gone", f"forgotten:{key}", kind="tombstone",
                       tombstone=True, project_dir=project)


def _ruling(verdict, project=PROJECT):
    return refutations.assert_ruling(
        subject="subject of " + verdict[:20], verdict=verdict,
        scope="this project", evidence=["issue:693"], channel="cli-tty",
        ratified=True, project_dir=project)


def _plant(name, data, project=PROJECT):
    bucket = store.config.checkpoint_dir() / store.project_slug(project)
    bucket.mkdir(parents=True, exist_ok=True)
    (bucket / name).write_bytes(data)


def _brief(capsys, monkeypatch, *argv, project=PROJECT):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    rc = cli.main(["brief", *argv])
    return rc, capsys.readouterr().out


# ---- prose masking ---------------------------------------------------------


def test_a_ruling_that_is_a_quarantined_value_prints_the_marker(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _ruling(Q)
    _ruling("never ship a Friday deploy")
    tid = _quarantine()
    rc, out = _brief(capsys, monkeypatch)
    assert rc == 0
    assert Q not in out
    assert f"§ [withheld: quarantine {tid}]" in out
    assert "§ never ship a Friday deploy" in out


def test_a_forgotten_ruling_prints_the_forgotten_marker(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _ruling(F)
    _forget()
    _, out = _brief(capsys, monkeypatch)
    assert F not in out
    assert f"§ {MARKER_F}" in out


def _recipient_panels(capsys, monkeypatch):
    _seed()
    return _brief(capsys, monkeypatch)


def test_the_request_panel_masks_a_quarantined_ask(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _seed(project=SENDER)
    requests.open_request(to=store.project_slug(PROJECT), ask=Q, why="because",
                          channel="cli-agent", project_dir=SENDER)
    _quarantine()
    _, out = _brief(capsys, monkeypatch)
    assert briefing._REQUEST_PANEL_HEADER in out
    assert Q not in out
    assert f"{MARKER_Q}" in out


def test_the_owed_panel_masks_a_quarantined_ask(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _seed(project=SENDER)
    rid = requests.open_request(to=store.project_slug(PROJECT), ask=Q,
                                why="because", channel="cli-agent",
                                project_dir=SENDER)
    requests.accept(rid, channel="cli-tty", project_dir=PROJECT)
    _quarantine()
    _, out = _brief(capsys, monkeypatch)
    assert briefing._OWED_PANEL_HEADER in out
    assert Q not in out and MARKER_Q in out


def test_the_verdict_panel_masks_ask_note_evidence_and_reply(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed(project=SENDER)
    rid = requests.open_request(to=store.project_slug(PROJECT), ask=Q,
                                why="because", channel="cli-agent",
                                project_dir=SENDER)
    requests.accept(rid, channel="cli-tty", note=Q, project_dir=PROJECT)
    requests.reply(rid, Q, channel="cli-tty", project_dir=PROJECT)
    requests.done(rid, channel="cli-tty", evidence=Q, project_dir=PROJECT)
    _quarantine(project=SENDER)
    _, out = _brief(capsys, monkeypatch, project=SENDER)
    assert briefing._VERDICT_PANEL_HEADER in out
    assert Q not in out
    assert out.count(MARKER_Q) >= 3      # ask, note and evidence at least
    assert "Reply: [withheld" in out or "Reply (agent" in out


def test_the_panel_builders_mask_only_through_a_mask(tmp_checkpoint_dir):
    _seed()
    _seed(project=SENDER)
    requests.open_request(to=store.project_slug(PROJECT), ask=Q, why="b",
                          channel="cli-agent", project_dir=SENDER)
    plain = briefing.request_panel_lines(PROJECT)
    masked = briefing.request_panel_lines(PROJECT, mask=lambda t: "X")
    assert any(Q in ln for ln in plain)
    assert all(Q not in ln for ln in masked) and any("X" in ln for ln in masked)


def test_the_prose_mask_of_no_snapshot_masks_nothing():
    assert briefing.prose_mask(None)("anything") == "anything"


def test_the_mcp_tool_masks_a_quarantined_ruling(tmp_checkpoint_dir):
    _seed()
    _ruling(Q)
    _quarantine()
    out = mcp_tools.HANDLERS["daimon_brief"]({"project": PROJECT}).text
    assert Q not in out and f"§ {MARKER_Q}" in out


def test_the_hermes_injection_masks_a_quarantined_ruling(
        tmp_checkpoint_dir, monkeypatch):
    _seed()
    _ruling(Q)
    _quarantine()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")
    assert Q not in out["context"] and MARKER_Q in out["context"]


# ---- the closed view -------------------------------------------------------


def _close():
    _quarantine()
    _plant("trust.jsonl", b"<<<<<<< HEAD\n")


def _closed_world(handoff=False):
    _seed(KEPT, topic="the weekly sync")
    _ruling("never ship a Friday deploy")
    if handoff:
        store.append_event("", "active", note="pick up the migration",
                           kind="handoff", project_dir=PROJECT)
    _close()


def test_a_closed_brief_renders_the_head_and_furniture_but_no_item(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _closed_world()
    rc, out = _brief(capsys, monkeypatch)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0] == briefing.GREETING
    assert any(ln.startswith("⚠ trust.jsonl is unreadable") for ln in lines)
    assert "§ never ship a Friday deploy" in out
    assert briefing.CLOSED_LINE in out
    assert "No checkpoint yet" not in out
    assert KEPT not in out and "the weekly sync" not in out
    assert lines.index(briefing.CLOSED_LINE) > lines.index("§ never ship a Friday deploy")


def test_a_closed_brief_keeps_the_handoff_first(tmp_checkpoint_dir,
                                                monkeypatch, capsys):
    _closed_world(handoff=True)
    _, out = _brief(capsys, monkeypatch)
    lines = out.splitlines()
    assert lines[0].startswith("HANDOFF (left deliberately")
    assert "→ pick up the migration" in out
    assert briefing.GREETING in out and KEPT not in out


def test_a_closed_brief_keeps_the_panels(tmp_checkpoint_dir, monkeypatch,
                                         capsys):
    _closed_world()
    _seed(project=SENDER)
    requests.open_request(to=store.project_slug(PROJECT), ask="port the model",
                          why="b", channel="cli-agent", project_dir=SENDER)
    _, out = _brief(capsys, monkeypatch)
    assert briefing._REQUEST_PANEL_HEADER in out
    assert "port the model" in out       # human furniture is not masked


def test_a_closed_mcp_brief_says_why_and_shows_no_item(tmp_checkpoint_dir):
    _closed_world()
    out = mcp_tools.HANDLERS["daimon_brief"]({"project": PROJECT}).text
    assert out.startswith(briefing.GREETING)
    assert "⚠ trust.jsonl is unreadable" in out
    assert "§ never ship a Friday deploy" in out
    assert briefing.CLOSED_LINE in out
    assert KEPT not in out


def test_a_closed_hermes_injection_carries_the_note_and_the_rulings(
        tmp_checkpoint_dir, monkeypatch):
    _closed_world()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")["context"]
    assert out.startswith(briefing.GREETING)
    assert "⚠ trust.jsonl is unreadable" in out
    assert "§ never ship a Friday deploy" in out
    assert KEPT not in out and "the weekly sync" not in out


def test_the_hermes_injection_of_a_project_with_no_checkpoint_carries_notes(
        tmp_checkpoint_dir, monkeypatch):
    _ruling("never ship a Friday deploy")
    _plant("events.jsonl", b'{"kind": "resolution", "item_ref": "o-bb')
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(session_id="S-n", user_message="hi",
                             conversation_history=[], is_first_turn=True,
                             model="m", platform="cli")["context"]
    assert out.splitlines()[0] == "⚠ events.jsonl is degraded (torn)"
    assert "§ never ship a Friday deploy" in out


def test_loops_under_a_closed_view_lists_nothing_and_says_why(
        tmp_checkpoint_dir, monkeypatch, capsys):
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"open_questions": [
            {"text": "who owns it", "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    _close()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["loops"]) == 0
    out = capsys.readouterr().out
    assert "⚠ trust.jsonl is unreadable" in out
    assert "no open loops" in out and "who owns it" not in out


def test_loops_reports_a_preparation_failure_and_lists_nothing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()

    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(briefing, "prepare", boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["loops"]) == 2
    err = capsys.readouterr()
    assert err.out == "" and "could not be listed (RuntimeError)" in err.err


def test_the_slug_brief_reports_a_preparation_failure(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()

    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(briefing, "prepare", boom)
    assert cli.main(["brief", f"--slug={store.project_slug(PROJECT)}"]) == 2
    assert "could not be prepared (RuntimeError)" in capsys.readouterr().err


def test_the_mcp_tool_raises_a_tool_error_when_the_view_cannot_be_built(
        tmp_checkpoint_dir, monkeypatch):
    _seed()

    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(briefing, "prepare", boom)
    with pytest.raises(mcp_tools.ToolError, match="could not be prepared"):
        mcp_tools.HANDLERS["daimon_brief"]({"project": PROJECT})


def test_hermes_injects_nothing_when_the_view_cannot_be_built(
        tmp_checkpoint_dir, monkeypatch):
    _seed()

    def boom(*_a, **_k):
        raise RuntimeError("x")
    monkeypatch.setattr(briefing, "prepare", boom)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert hooks.pre_llm_call(session_id="S-n", user_message="hi",
                              conversation_history=[], is_first_turn=True,
                              model="m", platform="cli") is None


# ---- degraded ledgers and the byte budget ----------------------------------


def test_a_torn_ledger_prints_a_note_after_the_greeting_and_keeps_the_items(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _plant("events.jsonl", b'{"kind": "resolution", "item_ref": "o-bb')
    _, out = _brief(capsys, monkeypatch)
    lines = out.splitlines()
    assert lines[0] == briefing.GREETING
    assert lines[2] == "⚠ events.jsonl is degraded (torn)"
    assert KEPT in out


def test_a_healthy_brief_has_no_note_line(tmp_checkpoint_dir, monkeypatch,
                                          capsys):
    _seed()
    _, out = _brief(capsys, monkeypatch)
    assert "⚠" not in out


def _heavy(n=40):
    return {"decisions": [{"text": f"decision number {i} " + "x" * 120,
                           "trust": "inferred"} for i in range(n)],
            "external": [], "open_loops": [], "beliefs": [],
            "uncertainties": [], "contradictions": [], "active_topic": None,
            "now": time.time()}


def test_the_note_is_protected_furniture_charged_to_the_budget():
    notes = ("⚠ events.jsonl is degraded (torn)",
             "⚠ amendments.jsonl is unreadable (EIO)")
    b = _heavy()
    budget = 2200
    bare = briefing.select(b, budget)
    withn = briefing.select(b, budget, notes=notes)
    text = briefing.render_selection(withn)
    assert all(n in text for n in notes)
    assert text.splitlines()[0] == briefing.GREETING
    assert text.splitlines()[2:4] == list(notes)
    # charged: the same budget keeps fewer items, never more
    assert len(withn.kept["decisions"]) <= len(bare.kept["decisions"])
    size = len(text.encode("utf-8")) + withn.reserved
    assert size <= budget
    # never a drop candidate: nothing is dropped to make room twice
    assert not any(n in str(withn.dropped) for n in notes)


def test_the_notes_survive_an_over_budget_protected_set():
    b = _heavy(5)
    sel = briefing.select(b, 50, notes=("⚠ trust.jsonl is unreadable",))
    assert "⚠ trust.jsonl is unreadable" in briefing.render_selection(sel)


def test_render_plain_and_render_carry_notes(tmp_checkpoint_dir):
    b = _heavy(2)
    out = briefing.render_plain(b, notes=("⚠ x is unreadable",))
    assert out.splitlines()[2] == "⚠ x is unreadable"
    cp = _cp(KEPT)
    text = briefing.render(cp, notes=("⚠ x is unreadable",))
    assert text.splitlines()[2] == "⚠ x is unreadable"
    only = briefing.render(None, notes=("⚠ x is unreadable",))
    assert only == "⚠ x is unreadable"


def test_an_llm_briefing_carries_the_notes_ahead_of_the_narrative(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(briefing.config, "llm_briefing", lambda: True)
    monkeypatch.setattr(briefing, "_render_llm", lambda cp: "narrative")
    monkeypatch.setattr(briefing, "_validate_llm_render", lambda r, c: True)
    out = briefing.render(_cp(KEPT), notes=("⚠ x is unreadable",))
    assert out.index("⚠ x is unreadable") < out.index("narrative")


# ---- the withheld trailer --------------------------------------------------


def test_the_trailer_counts_resolved_and_quarantined_apart_and_never_forgotten(
        tmp_checkpoint_dir, monkeypatch, capsys):
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-08-01T00:00:00Z",
        "working_context": {"open_questions": [
            {"text": "the gateway retry loop", "trust": "inferred"}],
            "recent_decisions": [
                {"text": Q, "trust": "inferred"},
                {"text": F, "trust": "inferred"},
                {"text": KEPT, "trust": "inferred"}]},
        "epistemic_snapshot": {}}, project_dir=PROJECT)
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    loop = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(loop, "resolved", project_dir=PROJECT)
    _quarantine()
    _forget()
    _, out = _brief(capsys, monkeypatch)
    assert "1 resolved item(s) withheld — `daimon status --suppressed`" in out
    assert "1 quarantined item(s) withheld" in out
    assert F not in out and "forgotten" not in out.replace(
        "forgotten value", "")
    assert Q not in out


def test_there_is_no_quarantine_line_without_a_quarantine(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed()
    _, out = _brief(capsys, monkeypatch)
    assert "quarantined item(s)" not in out


# ---- teammates -------------------------------------------------------------


def _team_world(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-1", _cp(Q, KEPT), project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-1", _cp(KEPT), project_dir=PROJECT)


def test_teammate_blocks_carry_no_stamps(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing.cli.brief import _team_briefings
    _team_world(monkeypatch)
    cp = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                admit=store.Admit.ANY)
    item_id = [d for d in cp["working_context"]["recent_decisions"]
               if d["text"] == KEPT][0]["id"]
    amendments.propose(item_id=item_id, change="progressed",
                       evidence="the PR merged", channel="cli-tty",
                       project_dir=PROJECT)
    sections = _team_briefings(PROJECT)
    assert [a for a, _ in sections] == ["grace"]
    assert all("_amend" not in d for d in sections[0][1]["decisions"])
    # the reader's own briefing does carry the stamp
    own = briefing.prepare(PROJECT, time.time())
    assert any("_amend" in d
               for d in own.checkpoint["working_context"]["recent_decisions"])


def test_a_teammates_quarantined_item_is_counted_in_the_trailer(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _team_world(monkeypatch)
    _quarantine()
    rc, out = _brief(capsys, monkeypatch, "--team")
    assert rc == 0
    assert Q not in out
    assert "1 quarantined item(s) withheld (1 a teammate's)" in out


def test_the_header_only_team_path_counts_teammates_withheld_items(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-1", _cp(Q, KEPT), project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    _quarantine()
    # the reader has no checkpoint of their own, so the global pointer is
    # the fallback and the header-only path prints the team section
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/hosts-fresh")
    assert cli.main(["brief", "--team"]) == 0
    out = capsys.readouterr().out
    assert "No briefing for this project yet" in out
    assert Q not in out


# ---- routes ----------------------------------------------------------------


def test_the_global_fallback_body_is_filtered_by_the_readers_ledgers(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed(Q, KEPT, project=SENDER)
    _quarantine(project="/p/hosts-fresh")
    rc, out = _brief(capsys, monkeypatch, "--global-fallback",
                     project="/p/hosts-fresh")
    assert rc == 0
    assert "no checkpoint for this project — showing the global" in out
    assert KEPT in out and Q not in out


def test_the_header_only_fallback_prints_the_notes_of_the_readers_ledgers(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed(project=SENDER)
    _plant("events.jsonl", b'{"kind": "resolution", "item_ref": "o-bb',
           project="/p/hosts-fresh")
    rc, out = _brief(capsys, monkeypatch, project="/p/hosts-fresh")
    assert rc == 0
    assert out.splitlines()[0] == "⚠ events.jsonl is degraded (torn)"
    assert "No briefing for this project yet" in out
    assert KEPT not in out


def test_a_slug_brief_reads_only_the_named_bucket(tmp_checkpoint_dir,
                                                  monkeypatch, capsys):
    _seed(KEPT, project=SENDER)
    _quarantine(Q, project=SENDER)
    rc = cli.main(["brief", f"--slug={store.project_slug(SENDER)}"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cross-project briefing" in out and KEPT in out


def test_rulings_read_out_of_a_snapshot_whose_fold_raised_are_none(
        tmp_checkpoint_dir):
    import dataclasses

    from daimon_briefing import view
    _ruling("never ship a Friday deploy")
    snap = dataclasses.replace(view.snapshot(PROJECT), rulings=None)
    assert briefing.ruling_lines(PROJECT, snap=snap) == []
    assert briefing.ruling_lines(PROJECT, snap=view.snapshot(PROJECT))


def test_the_rich_brief_prints_the_notes(tmp_checkpoint_dir, monkeypatch,
                                         capsys):
    from daimon_briefing import render
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_brief(_cp(KEPT), notes=("⚠ events.jsonl is degraded (torn)",))
    out = capsys.readouterr().out
    assert "⚠ events.jsonl is degraded (torn)" in out
    render.render_brief(None, notes=("⚠ events.jsonl is degraded (torn)",))
    assert "⚠ events.jsonl is degraded (torn)" in capsys.readouterr().out
