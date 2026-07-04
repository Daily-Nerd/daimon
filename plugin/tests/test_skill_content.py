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
    assert "name: using-daimon-memory" in header
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
    assert body.count("daimon brief") >= 2


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
