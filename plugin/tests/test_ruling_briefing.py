"""#693 PR 2: the standing-rulings briefing section.

Active rulings render as an always-present section at the TOP of the
deterministic briefing — skeleton furniture, outside the budget drop order —
because the briefing is the surface hooks and MCP actually consume. The
section is loaded fail-open from the ledger and never blocks a render.
"""

import json

import pytest

from daimon_briefing import briefing, config, refutations, store


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


def test_ruling_lines_fail_open_on_cap_read_error(tmp_checkpoint_dir,
                                                  monkeypatch):
    _rule("cap failure hides me safely")
    calls = []

    def boom():
        calls.append(1)
        raise ValueError("corrupt env")

    monkeypatch.setattr(briefing.config, "ruling_cap", boom)
    assert briefing.ruling_lines(PROJECT) == []
    assert calls, "the failure simulation never fired"


def test_ruling_loader_fails_open(tmp_checkpoint_dir, sample_checkpoint,
                                  monkeypatch):
    _rule("this ruling will not load")
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise OSError("ledger unreadable")

    # #962: active_rulings now reads through briefing.rulings_read, which
    # reads events() (strict) + fold() directly rather than
    # refutations.listing() — this is the seam that must still fail open.
    monkeypatch.setattr(briefing.refutations, "events", boom)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert out  # the briefing still renders
    assert "Standing rulings" not in out
    assert calls, "the failure simulation never fired"


def test_ruling_loader_fails_open_on_an_unreadable_ledger(
        tmp_checkpoint_dir, sample_checkpoint):
    """The real #962 shape, not a monkeypatch stand-in: a ledger replaced by
    a directory must degrade the same way a raised OSError does."""
    _rule("this ruling will not load either")
    refutations._path(PROJECT).unlink()
    refutations._path(PROJECT).mkdir()
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert out
    assert "Standing rulings" not in out


def test_ruling_loader_fails_open_when_path_resolution_itself_raises(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    """#962 F1: `refutations._path` sits ABOVE the ledger read (it reaches
    `config.checkpoint_dir()`, which can raise on a corrupt env file — a
    `UnicodeDecodeError`, not an `OSError`). That seam must fail open too,
    not just the read/fold path below it."""
    _rule("this ruling will not load for a third reason")
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")

    monkeypatch.setattr(briefing.refutations, "_path", boom)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert out  # the briefing still renders, never a raised exception
    assert "Standing rulings" not in out
    assert calls, "the failure simulation never fired"


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


# ---- #961 slice 4: a policy-carrying ruling counts, and still activates ---
#
# Design decision 4: a policy ruling counts against DAIMON_RULING_CAP and
# activates like any other — a policy invisible to the ledger would be the
# config-flag alternative the issue rejected. #1089 changed WHAT it renders
# (a compact line built from its fields, never the human verdict prose —
# see the "#1089: compact code-enforced rulings" block below), but not
# THAT it renders or that it counts.


def test_a_policy_ruling_renders_in_the_standing_rulings_section(
        tmp_checkpoint_dir, sample_checkpoint):
    refutations.assert_ruling(
        subject="requests from p-sender",
        verdict="agent may accept work asks from p-sender",
        scope="cross-project requests", evidence=["issue:961"],
        channel="cli-tty", ratified=True,
        request_policy={"sender": "p-sender", "kind": "work",
                        "verb": "accept", "by": "agent"},
        project_dir=PROJECT)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    # #1089: the compact line, built from the policy's own fields — never
    # the human-authored verdict prose, which is what daimon's own code (the
    # request fold and write boundary) enforces regardless of whether the
    # agent ever reads it.
    assert "§ policy: p-sender's agent may accept work asks here" in out
    assert "agent may accept work asks from p-sender" not in out


def test_a_policy_ruling_counts_toward_the_rendered_cap(
        tmp_checkpoint_dir, sample_checkpoint, monkeypatch):
    # Both activate under the DEFAULT cap (`_guard_ruling_cap` refuses at
    # activation, never at render, so there is no way to over-activate
    # directly) — the cap is then LOWERED, the render-side backstop's own
    # documented trigger ("a hand-edited ledger, or simply LOWERING the cap
    # after activations", `ruling_lines`'s own docstring).
    refutations.assert_ruling(
        subject="requests from p-sender",
        verdict="agent may accept work asks from p-sender",
        scope="cross-project requests", evidence=["issue:961"],
        channel="cli-tty", ratified=True,
        request_policy={"sender": "p-sender", "kind": "work",
                        "verb": "accept", "by": "agent"},
        project_dir=PROJECT)
    _rule("an ordinary prose ruling, over the cap")
    monkeypatch.setattr(config, "ruling_cap", lambda: 1)
    out = briefing.render(sample_checkpoint, project_dir=PROJECT)
    assert "+1 active ruling over cap" in out


# ---- #1089: compact code-enforced rulings ----------------------------------
#
# A ruling whose authority lives in daimon's own code (a `request_policy` the
# fold and write boundary enforce, or an `enforce` check the pre-action hook
# denies against) does not need its full verdict prose in the render budget:
# the agent is bound by it whether or not it ever reads the text. Plain
# rulings (no policy, no check) are UNCHANGED — every test above this marker
# already pins that byte-for-byte.

RECIPIENT = "/other/projects/fabcap"


def _open_policy(to="p-recipient", subject="asks toward p-recipient",
                 verdict="agent may open info asks toward p-recipient"):
    return refutations.assert_ruling(
        subject=subject, verdict=verdict, scope="cross-project requests",
        evidence=["issue:1089"], channel="cli-tty", ratified=True,
        request_policy={"to": to, "kind": "info", "verb": "open",
                        "by": "agent"},
        project_dir=PROJECT)


def _accept_policy(sender="p-sender", kind="work",
                   subject="requests from p-sender",
                   verdict="agent may accept asks from p-sender"):
    return refutations.assert_ruling(
        subject=subject, verdict=verdict, scope="cross-project requests",
        evidence=["issue:1089"], channel="cli-tty", ratified=True,
        request_policy={"sender": sender, "kind": kind, "verb": "accept",
                        "by": "agent"},
        project_dir=PROJECT)


def _check_ruling(intent, subject="a public post rule"):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in publishing",
        scope="publishing", evidence=["issue:1089"], channel="cli-tty",
        ratified=True,
        check={"match": "gh pr create", "body": "#!/bin/sh\nexit 0\n",
              "intent": intent},
        project_dir=PROJECT)


def _hand_ruled_row_with(subject, verdict, *, extra, project=PROJECT):
    """Same shape as `_hand_ruled_row` above, widened to carry arbitrary
    extra fields — the hand-edited / version-skew shape the #1089 fallback
    tests exist for: `assert_ruling`'s own validators refuse a malformed
    `request_policy`/`check`, so only a raw append can produce one."""
    row = refutations._stamp(
        "ruled", refutations.make_id(subject, "tests"), "cli-tty")
    row.update({"subject": subject, "verdict": verdict, "scope": "tests",
                "anchors": [], "revisit_when": "", "evidence": [],
                "ratified": True})
    row.update(extra)
    assert refutations.append(row, project_dir=project)
    return row["refutation_id"]


def test_a_verb_open_policy_renders_one_compact_line(tmp_checkpoint_dir):
    _open_policy(to="daimon")
    lines = briefing.ruling_lines(PROJECT)
    joined = "\n".join(lines)
    assert "§ policy: agent may open info asks → daimon" in joined
    assert "agent may open info asks toward p-recipient" not in joined


def test_a_verb_accept_policy_renders_one_compact_line(tmp_checkpoint_dir):
    _accept_policy(sender="fabcap", kind="work")
    lines = briefing.ruling_lines(PROJECT)
    joined = "\n".join(lines)
    assert "§ policy: fabcap's agent may accept work asks here" in joined
    assert "agent may accept asks from p-sender" not in joined


def test_the_recipient_shows_its_short_label_not_the_raw_slug(
        tmp_checkpoint_dir):
    # A real bucket slug is the flattened absolute path (#913); the compact
    # line must show the project's own short name, never that path.
    slug = store.project_slug(RECIPIENT)
    store.write_checkpoint("S0", {"session_id": "S0", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir=RECIPIENT)
    _open_policy(to=slug)
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ policy: agent may open info asks → fabcap" in joined
    assert slug not in joined


def test_slug_label_falls_back_when_the_bucket_has_no_stamped_name(
        tmp_checkpoint_dir):
    # A pre-#672 checkpoint: the bucket exists, but its pointer never got a
    # `project_name` at all — the "found the bucket, nothing readable in
    # it" half of the lookup, distinct from "no bucket at all".
    recipient = "/other/torn/project"
    slug = store.project_slug(recipient)
    store.write_checkpoint("S0", {"session_id": "S0", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir=recipient)
    latest = config.checkpoint_dir() / slug / "latest.json"
    data = json.loads(latest.read_text(encoding="utf-8"))
    del data["project_name"]
    latest.write_text(json.dumps(data), encoding="utf-8")
    label = briefing._slug_label(slug)
    assert label == slug.rsplit("-", 1)[-1]
    assert label != slug


def test_slug_label_skips_other_buckets_before_finding_a_match(
        tmp_checkpoint_dir):
    store.write_checkpoint("S0", {"session_id": "S0", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir="/other/projects/anamnesis")
    store.write_checkpoint("S1", {"session_id": "S1", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir="/other/projects/fabcap")
    target = store.project_slug("/other/projects/fabcap")
    assert briefing._slug_label(target) == "fabcap"


def test_the_policy_legend_renders_once_with_one_policy_ruling(
        tmp_checkpoint_dir):
    _open_policy()
    lines = briefing.ruling_lines(PROJECT)
    legend = [ln for ln in lines if "answerable from existing artifacts" in ln]
    assert len(legend) == 1
    assert "no change or effort" in legend[0]
    assert "stays work" in legend[0]


def test_the_policy_legend_renders_once_with_two_policy_rulings(
        tmp_checkpoint_dir):
    _open_policy(to="daimon", subject="asks toward daimon",
                verdict="agent may open info asks toward daimon")
    _accept_policy(sender="fabcap", subject="requests from fabcap",
                   verdict="agent may accept asks from fabcap")
    lines = briefing.ruling_lines(PROJECT)
    legend = [ln for ln in lines if "answerable from existing artifacts" in ln]
    assert len(legend) == 1


def test_no_legend_when_no_policy_ruling_is_present(tmp_checkpoint_dir):
    _rule("an ordinary prose ruling")
    lines = briefing.ruling_lines(PROJECT)
    assert not any("answerable from existing artifacts" in ln for ln in lines)


def test_no_legend_when_the_only_policy_is_over_cap(tmp_checkpoint_dir,
                                                     monkeypatch):
    # The legend describes a policy line the reader can actually see; a
    # policy ruling pushed past the cap note must not still claim one.
    _rule("prose ruling that fills the cap")
    _open_policy()
    monkeypatch.setattr(config, "ruling_cap", lambda: 1)
    lines = briefing.ruling_lines(PROJECT)
    assert not any("answerable from existing artifacts" in ln for ln in lines)
    assert any("over cap" in ln for ln in lines)


def test_a_policy_ruling_with_unreadable_fields_falls_back_to_prose(
        tmp_checkpoint_dir):
    ruling_id = _hand_ruled_row_with(
        "malformed policy subject", "the prose still has to render",
        extra={"request_policy": {"verb": "flee", "kind": "info",
                                  "by": "agent"}})
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ the prose still has to render" in joined
    assert "§ policy:" not in joined
    assert ruling_id  # the row activated; the render fell back, not away


def test_a_policy_that_is_not_even_a_dict_falls_back_to_prose(
        tmp_checkpoint_dir):
    _hand_ruled_row_with(
        "not-a-dict policy subject", "still has to render somehow",
        extra={"request_policy": "sender=p-sender"})
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ still has to render somehow" in joined


@pytest.mark.parametrize("extra_policy", [
    # `by` is anything but "agent" — the shipping writer never stores this,
    # but a hand-edited row can.
    {"verb": "open", "kind": "info", "to": "p-recipient", "by": "human"},
    # `kind` missing/falsy.
    {"verb": "open", "kind": "", "to": "p-recipient", "by": "agent"},
    # `verb=open` with no `to` at all.
    {"verb": "open", "kind": "info", "by": "agent"},
    # `verb=accept` with no `sender` at all.
    {"verb": "accept", "kind": "work", "by": "agent"},
])
def test_every_malformed_policy_shape_falls_back_to_prose(
        tmp_checkpoint_dir, extra_policy):
    _hand_ruled_row_with(
        "another malformed policy subject", "prose for a malformed policy",
        extra={"request_policy": extra_policy})
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ prose for a malformed policy" in joined
    assert "§ policy:" not in joined
    assert "§ policy:" not in joined


def test_a_policy_line_that_raises_falls_back_to_prose(tmp_checkpoint_dir,
                                                        monkeypatch):
    # The bucket-label lookup is the one part of a well-formed policy that
    # touches the filesystem; it must not be able to blank a ruling.
    def boom():
        raise OSError("checkpoint dir unreadable")

    monkeypatch.setattr(briefing.store, "list_buckets", boom)
    _open_policy()
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ policy:" not in joined
    assert "agent may open info asks toward p-recipient" in joined


@pytest.mark.parametrize("host", ["claude-code", "codex"])
def test_an_enforce_check_ruling_renders_compact_on_a_host_that_delivers_it(
        tmp_checkpoint_dir, monkeypatch, host):
    ruling_id = _check_ruling("enforce")
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", host)
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert (f"§ enforced: a public post rule (daimon ruling show "
            f"{ruling_id})") in joined
    assert "the rule for a public post rule" not in joined


@pytest.mark.parametrize("host", ["windsurf", "kimi", "some-future-host"])
def test_an_enforce_check_ruling_stays_prose_on_a_host_that_does_not_deliver_it(
        tmp_checkpoint_dir, monkeypatch, host):
    _check_ruling("enforce")
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", host)
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "the rule for a public post rule" in joined
    assert "§ enforced:" not in joined


def test_an_enforce_check_ruling_stays_prose_with_no_host_known(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.delenv("DAIMON_CAPTURE_HOST", raising=False)
    _check_ruling("enforce")
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "the rule for a public post rule" in joined
    assert "§ enforced:" not in joined


@pytest.mark.parametrize("host", ["claude-code", "codex"])
@pytest.mark.parametrize("intent", ["warn", "record-only"])
def test_a_non_enforce_check_ruling_always_stays_prose(
        tmp_checkpoint_dir, monkeypatch, intent, host):
    _check_ruling(intent)
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", host)
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "the rule for a public post rule" in joined
    assert "§ enforced:" not in joined


def test_a_check_ruling_with_unreadable_fields_falls_back_to_prose(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    _hand_ruled_row_with(
        "malformed check subject", "the check prose still has to render",
        extra={"check": "not-a-dict-either"})
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ the check prose still has to render" in joined
    assert "§ enforced:" not in joined


def test_a_check_ruling_with_an_empty_subject_falls_back_to_prose(
        tmp_checkpoint_dir, monkeypatch):
    # An `enforce` check whose ruling record itself carries no subject text
    # (a hand-edited row, never a shipped write) has nothing to name in the
    # compact line, so it stays prose rather than rendering a bare pointer.
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    _hand_ruled_row_with(
        "will be overwritten below", "prose for an empty-subject check",
        extra={"check": {"match": "gh pr create",
                         "body": "#!/bin/sh\nexit 0\n", "intent": "enforce"},
              "subject": ""})
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ prose for an empty-subject check" in joined
    assert "§ enforced:" not in joined


def test_a_check_line_that_raises_falls_back_to_prose(tmp_checkpoint_dir,
                                                       monkeypatch):
    def boom():
        raise RuntimeError("host lookup blew up")

    monkeypatch.setattr(briefing.config, "capture_host", boom)
    _check_ruling("enforce")
    joined = "\n".join(briefing.ruling_lines(PROJECT))
    assert "§ enforced:" not in joined
    assert "the rule for a public post rule" in joined


def test_a_policy_ruling_is_smaller_than_its_prose_would_have_been(
        tmp_checkpoint_dir):
    # A realistic near-max verdict (#1089's own measurement: a policy row
    # cost 221 B rendered as prose, no cheaper than any other ruling class).
    verdict = ("agent may accept work asks from p-sender here at any time, "
              "no exceptions, this has been true since the ruling activated "
              "and stays true until it is retired by a human " + "x" * 80)
    assert len(verdict) <= 280
    refutations.assert_ruling(
        subject="requests from p-sender", verdict=verdict,
        scope="cross-project requests", evidence=["issue:1089"],
        channel="cli-tty", ratified=True,
        request_policy={"sender": "p-sender", "kind": "work",
                        "verb": "accept", "by": "agent"},
        project_dir=PROJECT)
    actual = len("\n".join(briefing.ruling_lines(PROJECT)).encode("utf-8"))
    baseline = len(
        (briefing._RULING_HEADER + "\n" + f"§ {verdict}").encode("utf-8"))
    assert actual < baseline


def test_plain_rulings_still_render_byte_identical_to_today(
        tmp_checkpoint_dir):
    # The header, the `§ ` prefix, and the over-cap note are pinned as
    # UNCHANGED for a plain (no policy, no check) ruling (#1089).
    _rule("plain rulings are untouched by #1089")
    lines = briefing.ruling_lines(PROJECT)
    assert lines == [briefing._RULING_HEADER,
                     "§ plain rulings are untouched by #1089"]


def test_slug_label_falls_back_to_the_tail_of_a_path_shaped_slug(
        tmp_checkpoint_dir):
    # No bucket exists for this slug, so there is no stamped project_name to
    # read; a real (`store.project_slug`-shaped) slug still must not render
    # in full (#1089).
    slug = store.project_slug("/some/nested/path/anamnesis")
    label = briefing._slug_label(slug)
    assert label == "anamnesis"
    assert label != slug


def test_slug_label_passes_through_a_short_non_path_token(tmp_checkpoint_dir):
    # `request_policy`'s slug field is validated as `[\\w-]{1,255}` (#961), not
    # required to look like a flattened path — a short mnemonic bucket name
    # with no bucket on disk is already a label, not a slug to shorten.
    assert briefing._slug_label("p-sender") == "p-sender"
