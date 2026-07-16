"""Budget and structure guards for the canonical skill content (#66).

The compact variant is injected into EVERY prompt on rules hosts and shares
Windsurf's 6,000-char global-rules file with the user's own rules — the
2,000-char cap is a hard product constraint, not a style preference.
"""

from daimon_briefing import skill_content


def test_compact_fits_budget():
    body = skill_content.render_compact()
    assert len(body) <= 2000, f"compact body is {len(body)} chars (cap 2000)"


def test_full_fits_line_budget():
    full = skill_content.render_full()
    assert len(full.splitlines()) <= 150


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
