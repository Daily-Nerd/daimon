"""#693 PR 2: the standing-rulings briefing section.

Active rulings render as an always-present section at the TOP of the
deterministic briefing — skeleton furniture, outside the budget drop order —
because the briefing is the surface hooks and MCP actually consume. The
section is loaded fail-open from the ledger and never blocks a render.
"""

from daimon_briefing import briefing, config, refutations


PROJECT = "/p/ruling-brief"


def _rule(verdict, *, channel="cli-tty", ratified=True, subject=None,
          scope="this project"):
    return refutations.assert_ruling(
        subject=subject or f"subject for {verdict[:24]}",
        verdict=verdict,
        scope=scope,
        evidence=["issue:693"],
        channel=channel,
        ratified=ratified,
        project_dir=PROJECT,
    )


def test_active_ruling_renders_at_top_of_briefing(tmp_checkpoint_dir,
                                                  sample_checkpoint):
    _rule("never ship a Friday deploy")
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "Standing rulings" in out
    assert "§ never ship a Friday deploy" in out
    # Top means top: the section precedes every cognitive section.
    assert out.index("Standing rulings") < out.index("VERIFY BEFORE TRUSTING")


def test_candidate_and_retired_rulings_never_render(tmp_checkpoint_dir,
                                                    sample_checkpoint):
    _rule("candidate text stays out", channel="cli-agent", ratified=False)
    retired = _rule("retired text stays out", subject="retired subject")
    refutations.retire(retired, channel="cli-tty", project_dir=PROJECT)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "candidate text stays out" not in out
    assert "retired text stays out" not in out
    assert "Standing rulings" not in out  # no active rulings -> no section


def test_agent_authored_text_is_labeled(tmp_checkpoint_dir, sample_checkpoint):
    ruling_id = _rule("agent drafted this rule", channel="cli-agent",
                      ratified=False)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "agent drafted this rule" in out
    assert "agent-written" in out


def test_human_authored_text_carries_no_author_label(tmp_checkpoint_dir,
                                                     sample_checkpoint):
    _rule("human wrote this rule")
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "agent-written" not in out


def test_rulings_survive_budget_pressure(tmp_checkpoint_dir, sample_checkpoint,
                                         monkeypatch):
    _rule("rulings are skeleton furniture")
    # A budget small enough to force every droppable section to trim.
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "40")
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "§ rulings are skeleton furniture" in out


def test_full_cap_section_stays_within_budget_share(tmp_checkpoint_dir):
    # The design pins the worst case: a full cap of maximum-length rulings
    # costs ~17-20% of the default 3000-token budget, never more.
    for n in range(config.ruling_cap()):
        _rule(f"rule {n} " + "x" * (refutations._MAX_RULING_TEXT
                                    - len(f"rule {n} ")),
              subject=f"subject {n}")
    lines = briefing.ruling_lines(PROJECT)
    section = "\n".join(lines)
    assert briefing.estimate_tokens(section) <= 600  # 20% of 3000
    assert briefing.estimate_tokens(section) >= 450  # the share is real


def test_renderer_backstops_count_and_text(tmp_checkpoint_dir, monkeypatch):
    # Hand-edited ledgers can exceed the cap or the text bound; the renderer
    # holds the line on its own (the design's count-and-text backstop).
    for n in range(3):
        _rule(f"backstop rule number {n}", subject=f"backstop subject {n}")
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")
    lines = briefing.ruling_lines(PROJECT)
    body = [ln for ln in lines if ln.lstrip().startswith("§")]
    assert len(body) == 2


def test_ruling_loader_fails_open(tmp_checkpoint_dir, sample_checkpoint,
                                  monkeypatch):
    _rule("this ruling will not load")

    def boom(**kwargs):
        raise OSError("ledger unreadable")

    monkeypatch.setattr(briefing.refutations, "listing", boom)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert out  # the briefing still renders
    assert "Standing rulings" not in out


def test_no_rulings_render_is_byte_identical_to_legacy(tmp_checkpoint_dir,
                                                       sample_checkpoint):
    b = briefing.build(sample_checkpoint)
    assert (briefing.render(sample_checkpoint, project_dir=PROJECT)
            == briefing.render_plain(b))


def _hand_ruled_row(subject, verdict, project=PROJECT):
    """A ledger row appended RAW — the hand-edited / version-skew shape the
    renderer backstops exist for (assert_ruling's write-time guards refuse
    it, which is the point)."""
    row = refutations._stamp(
        "ruled", refutations.make_id(subject, "tests"), "cli-tty")
    row.update({"subject": subject, "verdict": verdict, "scope": "tests",
                "anchors": [], "revisit_when": "", "evidence": [],
                "ratified": True})
    assert refutations.append(row, project_dir=project)
    return row["refutation_id"]


def test_over_cap_truncation_is_loud(tmp_checkpoint_dir, monkeypatch):
    for n in range(3):
        _rule(f"loud rule number {n}", subject=f"loud subject {n}")
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")
    lines = briefing.ruling_lines(PROJECT)
    joined = "\n".join(lines)
    assert "over cap" in joined  # never a silent truncation
    assert "daimon ruling list" in joined  # and it says where the rest live


def test_overlength_verdict_clips_with_visible_marker(tmp_checkpoint_dir):
    long_verdict = "x" * (refutations._MAX_RULING_TEXT + 40)
    _hand_ruled_row("hand edited subject", long_verdict)
    lines = briefing.ruling_lines(PROJECT)
    body = [ln for ln in lines if ln.startswith("§")]
    assert len(body) == 1
    assert long_verdict not in body[0]  # clipped
    assert "…" in body[0]               # and visibly so


def test_empty_verdict_row_never_renders_bare_glyph(tmp_checkpoint_dir):
    _hand_ruled_row("empty verdict subject", "")
    _hand_ruled_row("whitespace verdict subject", "   ")
    _rule("a real rule renders")
    lines = briefing.ruling_lines(PROJECT)
    assert all(ln.strip() != "§" for ln in lines)
    assert any("a real rule renders" in ln for ln in lines)


def test_authored_label_names_the_authority(tmp_checkpoint_dir):
    # `text_authored_by` is the AUTHORITY word (agent / mechanical), not the
    # channel — the label must state it, one vocabulary with
    # cli._print_ruling.
    ruling_id = refutations.assert_ruling(
        subject="mechanical subject", verdict="mechanically drafted rule",
        scope="tests", evidence=["issue:693"], channel="mechanical",
        project_dir=PROJECT)
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=PROJECT)
    lines = briefing.ruling_lines(PROJECT)
    assert any("[mechanical-written]" in ln for ln in lines)


def test_llm_branch_degrade_note_precedes_rulings(tmp_checkpoint_dir,
                                                  sample_checkpoint,
                                                  monkeypatch):
    # #204: the unverified-receipt note is the loudest line in the briefing;
    # the deterministic paths render it first and the LLM path must agree.
    _rule("note comes before me")
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    narrative = ('"I\'ll merge it myself later from the GitHub UI"\n'
                 '"do we chunk below 1200 lines or single-pass?"\n'
                 '"we adopt the D-007 prompt for the serializer"')
    monkeypatch.setattr(briefing, "_render_llm", lambda checkpoint: narrative)
    monkeypatch.setattr(briefing, "receipt_degraded", lambda checkpoint: True)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert out.index(briefing.DEGRADE_NOTE) < out.index("note comes before me")


def test_render_surfaces_rulings_when_checkpoint_has_nothing(
        tmp_checkpoint_dir):
    # A ruling ratified on day one — before any checkpoint holds items — must
    # still reach context; "nothing worth surfacing" is no longer true.
    _rule("day one rule reaches context")
    empty = {"session_id": "S0", "working_context": {},
             "epistemic_snapshot": {}}
    out = briefing.render(empty, project_dir=PROJECT)
    assert out is not None
    assert "§ day one rule reaches context" in out


def test_cli_brief_shows_rulings_with_no_checkpoint(tmp_checkpoint_dir,
                                                    capsys):
    from daimon_briefing import cli
    _rule("cli shows me before any checkpoint")
    assert cli.main(["brief", "--project", PROJECT]) in (0, 1)
    out = capsys.readouterr().out
    assert "§ cli shows me before any checkpoint" in out


def test_mcp_brief_shows_rulings_with_no_checkpoint(tmp_checkpoint_dir,
                                                    monkeypatch):
    from tests.test_mcp_server import rpc, _init, _call, _result
    _rule("mcp shows me before any checkpoint")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "§ mcp shows me before any checkpoint" in text


def test_hook_injects_rulings_with_no_checkpoint(tmp_checkpoint_dir,
                                                 monkeypatch):
    from daimon_briefing import hooks
    _rule("hook injects me on day one")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[],
        is_first_turn=True, model="m", platform="cli",
    )
    assert out is not None
    assert "§ hook injects me on day one" in out["context"]


def test_hook_injected_briefing_carries_rulings(tmp_checkpoint_dir,
                                                sample_checkpoint,
                                                monkeypatch):
    from daimon_briefing import hooks, store
    _rule("hook injection carries this")
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    out = hooks.pre_llm_call(
        session_id="S2", user_message="hi", conversation_history=[],
        is_first_turn=True, model="m", platform="cli",
    )
    assert "§ hook injection carries this" in out["context"]


def test_mcp_brief_carries_rulings(tmp_checkpoint_dir, sample_checkpoint,
                                   monkeypatch):
    from daimon_briefing import store
    from tests.test_mcp_server import rpc, _init, _call, _result
    _rule("mcp brief carries this")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=PROJECT)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _, out = rpc(_init(), _call("daimon_brief", {}))
    text, is_err = _result(out)
    assert is_err is False
    assert "§ mcp brief carries this" in text


def test_cli_brief_carries_rulings(tmp_checkpoint_dir, sample_checkpoint,
                                   capsys):
    from daimon_briefing import cli, store
    _rule("cli brief carries this")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=PROJECT)
    assert cli.main(["brief", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "§ cli brief carries this" in out


def test_cli_rich_brief_carries_rulings(tmp_checkpoint_dir, sample_checkpoint,
                                        monkeypatch, capsys):
    from daimon_briefing import cli, render, store
    _rule("rich brief carries this")
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=PROJECT)
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    assert cli.main(["brief", "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "rich brief carries this" in out


def test_llm_render_gets_deterministic_section_prepended(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    _rule("prepend me to the llm narrative")
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    # The canned narrative must keep every verbatim quote intact or the
    # #30 post-validation rejects it and the fallback path renders instead.
    narrative = ("LLM NARRATIVE BODY\n"
                 '"I\'ll merge it myself later from the GitHub UI"\n'
                 '"do we chunk below 1200 lines or single-pass?"\n'
                 '"we adopt the D-007 prompt for the serializer"')
    monkeypatch.setattr(briefing, "_render_llm", lambda checkpoint: narrative)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "LLM NARRATIVE BODY" in out
    assert "§ prepend me to the llm narrative" in out
    assert (out.index("prepend me to the llm narrative")
            < out.index("LLM NARRATIVE BODY"))
