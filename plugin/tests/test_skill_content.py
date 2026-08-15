"""Budget and structure guards for the canonical skill content (#66).

The compact variant is injected into EVERY prompt on rules hosts and shares
Windsurf's 6,000-char global-rules file with the user's own rules — the
2,000-char cap is a hard product constraint, not a style preference.
"""

import re

from daimon_briefing import cli, skill_content


def test_compact_fits_budget():
    body = skill_content.render_compact()
    assert len(body) <= 2000, f"compact body is {len(body)} chars (cap 2000)"


def test_full_fits_size_budget():
    # #579: this was a 150-LINE cap, and a line count is not what the budget
    # is protecting.  It was satisfied by folding a section into one 454-char
    # line, which left the body BIGGER and less readable while the gauge read
    # green.  Measure the size, and cap line length separately so re-wrapping
    # is free and only real growth has to argue for itself.
    #
    # Where 8,000 comes from, since the number is fair to ask about: unlike
    # the 2,000-char compact cap above, this is NOT a host or product limit.
    # Nothing truncates a SKILL.md.  It is a RATCHET, and the thing it
    # ratchets against is the only failure mode this file has ever had —
    # growing because every single addition was individually small and
    # individually reasonable.  The number is the size at the last deliberate
    # review (7,569 chars, 2026-08-05) plus about six percent: loose enough
    # that rewrapping and wording fixes never trip it, tight enough that a
    # new SECTION has to be argued for in review instead of absorbed.  Raising
    # it is allowed; doing so silently, as part of a change about something
    # else, is what this is here to prevent.
    #
    # RAISED 8,000 -> 8,900 on 2026-08-08 (#643), and here is the argument the
    # rule above asks for.  This is not a change about something else: the
    # skill content IS the subject.  `daimon why` shipped in 0.28.0 and
    # appeared ZERO times here — the read side of every trust tag this file
    # spends 1,500 chars teaching agents to respect was documented for humans
    # and invisible to agents, and #596 cannot measure a command no skill
    # surfaces.  `audit` and `verify-receipt` were absent by accident rather
    # than decision.  The addition is ~820 chars: four symptom rows plus one
    # paragraph naming the evidence axes, which is the part that makes the
    # command usable rather than merely known.  New deliberate-review size is
    # 8,417 (2026-08-08); 8,900 is that plus the same ~6% slack the 8,000
    # carried, so rewrapping stays free and the next new section still has to
    # argue.  What was NOT done: nothing was added to the compact body, which
    # sits at 1,999 of its hard 2,000-char host cap — see #579 for what gets
    # evicted when that budget is squeezed.
    #
    # RAISED 8,900 -> 9,900 on 2026-08-15 (#693), same discipline. The skill
    # content is again the subject: `ruling` ships an agent-facing verb
    # family whose entire premise is that agents HONOR standing constraints,
    # and a command this file does not surface is invisible to agents (the
    # `daimon why` lesson above, verbatim). The addition is ~430 chars: one
    # section teaching polarity (a ruling is a veto, not a dead approach),
    # the read verbs, and the propose-only boundary — the never-ratify /
    # never-retire lines are the wedge rule this repo enforces everywhere
    # else, stated where agents will read it. New deliberate-review size is
    # 9,332 (2026-08-15); 9,900 is that plus the same ~6% slack. Nothing was
    # added to the compact body.
    full = skill_content.render_full()
    assert len(full) <= 9900, f"full body is {len(full)} chars (cap 9900)"


def test_full_has_trigger_only_frontmatter():
    # Description = triggering conditions only, third person; a workflow
    # summary in the description makes agents skip the body (research 2026-07-03).
    full = skill_content.render_full()
    assert full.startswith("---\n")
    header = full.split("---\n")[1]
    assert "name: daimon" in header
    assert "description: Use when" in header
    for leak in ("run daimon brief", "then", "first,"):
        assert leak not in header.split("description:")[1].split("\n")[0].lower()


def test_compact_repeats_must_win_rule_at_end():
    # Later-instruction-wins on every vendor: the protocol line appears at
    # top AND as the final line.
    body = skill_content.render_compact()
    last_line = body.strip().splitlines()[-1]
    assert "daimon brief" in last_line
    assert "MUST" in last_line or "silent" in last_line
    # Pin the repetition itself, not just the last line's content: the
    # session-start rule must occur at least twice (top protocol + MUST line).
    # Count the backtick-delimited command, not the bare substring "daimon
    # brief" — that also matches "daimon briefing" and would pass on a
    # confounded count. The command carries --team since #214.
    assert body.count("run `daimon brief --team`") >= 2


def test_session_start_pull_covers_team_variant():
    # #214: on hosts without briefing injection (Windsurf Cascade has no
    # session-start event — a permanent host constraint) the skill IS the
    # briefing delivery path. `daimon brief --team` supersets `daimon brief`:
    # pure file-ops, and byte-identical output when no team is configured —
    # so every session-start rule teaches the team-inclusive command
    # unconditionally instead of a condition an agent cannot evaluate (the
    # team config lives in the machine-level sidecar, not the project).
    full = skill_content.render_full()
    session_start = full.split("## Session start")[1].split("\n## ")[0]
    assert "`daimon brief --team`" in session_start
    compact = skill_content.render_compact()
    # Top protocol block AND the later-wins MUST line both carry the flag.
    assert compact.count("run `daimon brief --team`") >= 2
    assert "--team" in compact.strip().splitlines()[-1]


def test_compact_has_concrete_example():
    # Gemini under-follows prose; a few-shot example is load-bearing there.
    assert "[✓ verbatim]" in skill_content.render_compact()


def test_variants_use_real_trust_tag_literals():
    # briefing.py renders "[✓ verbatim]", "[~ inferred]", "[? untagged]" —
    # not the placeholders "[verbatim]"/"[inferred]" this content used to
    # teach. Both variants must match what daimon brief actually prints.
    for text in (skill_content.render_full(), skill_content.render_compact()):
        assert "[✓ verbatim]" in text
        assert "[~ inferred]" in text
        assert "[? untagged]" in text


def test_both_variants_state_silence_guard():
    for text in (skill_content.render_full(), skill_content.render_compact()):
        assert "silent" in text.lower()


def test_full_body_teaches_staleness_world_check():
    # #215: the staleness-budget warning ("N carried item(s) unverified for
    # >N days") is new surface in the brief — the skill must teach agents to
    # world-check a carried claim before repeating it as true, not just note
    # it "may be stale" as the pre-#215 [carried] guidance already does.
    full = skill_content.render_full()
    reading = full.split("## Reading a briefing")[1].split("\n## ")[0]
    assert "world-check" in reading.lower()


def test_full_teaches_context_switching():
    # #243: the cross-project verbs must be taught, or the feature is invisible.
    full = skill_content.render_full()
    assert "daimon projects" in full
    assert "--slug" in full


def test_compact_teaches_context_switching():
    body = skill_content.render_compact()
    assert "daimon projects" in body
    assert "--slug" in body


# ---- #257: the skill teaches USING memory, not just reading it ----


def test_full_teaches_recall_for_current_project():
    full = skill_content.render_full()
    # recall must be taught OUTSIDE the cross-project section: the trigger
    # description promises search-on-reference, the body must deliver it
    assert "Searching memory" in full
    assert "daimon recall <salient terms>" in full


def test_full_teaches_closing_loops_with_resolve():
    full = skill_content.render_full()
    assert "Closing loops" in full
    assert "daimon resolve" in full
    assert "--note" in full


def test_compact_teaches_recall_and_resolve():
    body = skill_content.render_compact()
    assert "daimon recall" in body
    assert "daimon resolve" in body


def test_full_teaches_deliberation_guard_without_hard_veto():
    full = skill_content.render_full()
    section = full.split("## Checking rejected approaches")[1].split("## Closing loops")[0]
    assert "daimon refute guard" in section
    assert "--anchor" in section
    assert "not a command veto" in section
    assert "--by agent" in section
    # This used to pin `--by human`, which the parser rejects — the skill was
    # teaching agents about a flag value that had already been deleted, and
    # the test held it there. Pin the BEHAVIOUR instead: the agent declares
    # itself, and ratification is the user's to run.
    assert "--by human" not in section
    assert "refute ratify" in section
    assert "Ask the" in section


def test_compact_keeps_the_rules_a_character_budget_would_evict():
    # #579: these four were deleted to fund the refutation guard paragraph.
    # Each one is load-bearing behaviour, and none had a test, so the trade
    # was invisible in review.  Pin them: the next budget squeeze must show
    # up as a failure here, not as a silent behaviour change on every install.
    body = skill_content.render_compact()
    assert "do not mention daimon" in body      # silent degradation
    assert "never transmitted" in body          # privacy assurance
    assert "all rules above apply" in body      # trust rules bind MCP use
    assert "CLI-only" in body                   # read-only tools cannot write


def test_full_body_budget_cannot_be_bought_with_long_lines():
    # The 150-line cap was satisfied by folding a section into one 454-char
    # line, so the budget measured newlines rather than size.  Cap the line
    # length too, or the next author faces the same pressure with no room.
    # Body only: the frontmatter `description` is a single unwrappable field
    # and is not what this guards.
    #
    # 100 is this file's own prose convention plus slack, not a guess.  The
    # body wraps by hand: median line 70, p90 77, and after the intro
    # paragraph was wrapped the longest real line is 91.  The first version of
    # this guard shipped at 200 — which was not derived from anything, it was
    # just above the one 192-char line the body still had, an UNWRAPPED line,
    # which is precisely the shape the cap exists to catch.  A cap set above
    # the defect it is watching for is decoration.  At 100 a folded section
    # fails on sight, an over-long sentence gets rewrapped for free, and a
    # genuinely unwrappable line (a long command example) still fits.
    longest = max(skill_content._FULL_BODY.splitlines(), key=len)
    assert len(longest) <= 100, (
        f"{len(longest)}-char line defeats the size budget: {longest[:70]}...")


def test_compact_teaches_deliberation_guard_and_authority_boundary():
    body = skill_content.render_compact()
    assert "daimon refute guard" in body
    assert "advisory" in body
    assert "refute add --by" in body
    # #576/#579: the prohibition now names the verb, not just the flag —
    # `ratify` had no --by at all, so "never claim human" left the one
    # transition that creates load-bearing state unaddressed.
    assert "never ratify" in body


def test_compact_must_rule_stays_last():
    # rules hosts resolve conflicts later-wins — the MUST line must stay the
    # final line no matter what sections are added above it
    body = skill_content.render_compact().strip()
    assert body.splitlines()[-1].startswith("MUST:")


# ---- #304: closing loops teaches preview-before-write ----


def test_full_teaches_dry_run_before_commit():
    full = skill_content.render_full()
    closing = full.split("## Closing loops")[1].split("\n## ")[0]
    assert "--dry-run" in closing
    assert "daimon resolve" in closing
    assert "--note" in closing


def test_compact_teaches_dry_run_before_commit():
    body = skill_content.render_compact()
    assert "--dry-run" in body
    assert "daimon resolve" in body
    assert "--note" in body


# ---- #480 slice 5: the portable skill teaches the agent resolve path too ----
#
# #257's own field numbers (3 agent-initiated recalls, 2 resolved refs, ever)
# showed teaching the HUMAN path alone did not move resolution. This closes
# the gap the design doc names explicitly: the portable skill already taught
# `daimon resolve "<item text>" --dry-run` / `--note`; it must ALSO teach the
# evidence-gated agent path, with the same quote-discipline rule the
# daimon-briefing plugin skill and daimon-end skill state.


def test_full_teaches_agent_resolve_with_evidence():
    full = skill_content.render_full()
    closing = full.split("## Closing loops")[1].split("\n## ")[0]
    assert "--by agent" in closing
    assert "--evidence" in closing
    # Quote discipline (rule 17) — the byte-check-at-session-end rule must be
    # stated, not just the flag name, or the evidence gate reads as decoration.
    assert "byte-checked" in closing.lower() or "verified" in closing.lower()
    assert "daimon loops" in closing
    assert "human-only" in closing.lower() or "human only" in closing.lower()


def test_compact_teaches_agent_resolve_with_evidence():
    body = skill_content.render_compact()
    assert "--by agent" in body
    assert "--evidence" in body
    assert "daimon loops" in body


# ---- #351: the MCP tool surface is a first-class alternative to the CLI ----


def test_full_maps_mcp_tools_to_cli_commands():
    # An MCP-registered host without shell access exposes exactly four
    # read-only tools (mcp_server.py); the skill must map them to the CLI
    # commands or its contract never reaches the tool surface.
    full = skill_content.render_full()
    mcp = full.split("## MCP")[1].split("\n## ")[0]
    for tool in ("daimon_recall", "daimon_brief", "daimon_projects",
                 "daimon_status"):
        assert tool in mcp
    # The load-bearing claim: same operations, same rules — not a parallel
    # protocol the agent has to re-learn.
    assert "same operations" in mcp
    # Cross-project discipline carries over: the tools take a `slug`
    # argument where the CLI takes --slug.
    assert "slug" in mcp


def test_full_states_mcp_write_asymmetry():
    # resolve/forget/heal have NO tool equivalent (the MCP tier is read-only
    # by design). On an MCP-only host a closed loop must be reported to the
    # user — never faked as recorded.
    full = skill_content.render_full()
    mcp = full.split("## MCP")[1].split("\n## ")[0]
    assert "read-only" in mcp
    assert "resolve" in mcp
    assert "tell the user" in mcp


def test_compact_teaches_mcp_tools():
    # Both variants stay in sync (#351): the compact body must carry the
    # tool mapping, the read-only asymmetry, and the don't-fake-a-resolve
    # rule inside the same brutal char budget.
    body = skill_content.render_compact()
    for tool in ("daimon_recall", "daimon_brief", "daimon_projects",
                 "daimon_status"):
        assert tool in body
    assert "read-only" in body
    assert "tell the user" in body


def test_full_teaches_the_trust_inspector():
    """#643: `daimon why` shipped in 0.28.0 as the read-side receipt for a
    recalled item and appeared ZERO times in the installed skill — documented
    for people, invisible to agents. #596 asks users whether they can act on
    its output; if no skill surfaces it, an agent never runs it unprompted and
    the test measures the facilitator instead of the command."""
    full = skill_content.render_full()
    assert "daimon why" in full
    assert "--source" in full, "the bounded source window is the disclosure step"


def test_full_teaches_the_read_only_auditors():
    """#643 decision: `audit` and `verify-receipt` are read-only verification
    surfaces an agent can reasonably run itself, so their absence was
    accidental, not decided."""
    full = skill_content.render_full()
    assert "daimon audit" in full
    assert "daimon verify-receipt" in full


def test_the_trust_verbs_reach_the_symptom_table():
    """Triggers live in the rule text (this file's own header says so), so a
    verb the agent cannot map to a symptom is a verb it will not reach for."""
    full = skill_content.render_full()
    section = full.split("## When memory looks wrong")[1].split("\n## ")[0]
    for verb in ("daimon why", "daimon audit", "daimon verify-receipt"):
        assert verb in section, f"{verb} has no symptom that reaches it"


def _top_level_commands():
    parser = cli.build_parser()
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action.choices, "items"):
            return set(action.choices)
    raise AssertionError("no subparsers found on the top-level parser")


def _taught(full, name):
    """Is `name` taught as a COMMAND, not merely as a word?

    Substring matching is wrong in both directions here: `anchor` appears only
    as `--anchor`, a flag of `refute guard`; `serialize` appears inside the
    word "serialized". Both would read as covered while the agent learns
    nothing about the command."""
    return re.search(rf"`daimon {re.escape(name)}\b", full) is not None


def test_every_shipped_command_is_taught_or_declared_not_agent_facing():
    """#650. `daimon why` shipped in 0.28.0 and reached no skill for a whole
    release while its human documentation was complete. Nothing failed, because
    nothing was asking.

    This partitions the command surface: a subcommand is either taught in the
    installed skill or named in `_NOT_AGENT_FACING` with a reason. A new
    command that is in neither fails here, and the fix is one line in whichever
    set is correct — the point is that the decision gets made, not that it goes
    a particular way.

    Governs `skill_content.render_full()`, the body `daimon skill install`
    writes. The plugin-discovered `skills/*/SKILL.md` is a SEPARATE surface
    with its own shape (prose overview, not a command reference), and #643
    showed the two can fail independently: the CLI-installed skill was healthy
    on the maintainer's machine for five releases while every plugin install
    shipped no discoverable skill at all."""
    full = skill_content.render_full()
    commands = _top_level_commands()
    declared = set(skill_content.NOT_AGENT_FACING)

    unknown = declared - commands
    assert not unknown, (
        f"NOT_AGENT_FACING names commands that do not exist: {sorted(unknown)}")

    untaught = {c for c in commands if not _taught(full, c)}
    unclassified = sorted(untaught - declared)
    assert not unclassified, (
        "these commands reach no skill and are not declared "
        f"not-agent-facing: {unclassified}. Teach them in _FULL_BODY, or add "
        "them to skill_content.NOT_AGENT_FACING with the reason.")

    contradicted = sorted(declared - untaught)
    assert not contradicted, (
        f"declared not-agent-facing but taught anyway: {contradicted}")


def test_every_not_agent_facing_entry_gives_a_reason():
    for name, reason in skill_content.NOT_AGENT_FACING.items():
        assert reason and reason.strip(), f"{name}: no reason recorded"
