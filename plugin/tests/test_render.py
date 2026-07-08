import sys

import pytest

from daimon_briefing import render


def _force_rich_present(monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "rich", types.ModuleType("rich"))


def _force_rich_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "rich", None)


def test_supports_rich_false_when_daimon_plain(monkeypatch):
    _force_rich_present(monkeypatch)
    monkeypatch.setattr(render, "_isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    assert render.supports_rich() is False


def test_supports_rich_false_when_no_color(monkeypatch):
    _force_rich_present(monkeypatch)
    monkeypatch.setattr(render, "_isatty", lambda: True)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")
    assert render.supports_rich() is False


def test_supports_rich_false_when_not_tty(monkeypatch):
    _force_rich_present(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "_isatty", lambda: False)
    assert render.supports_rich() is False


def test_supports_rich_false_when_rich_absent(monkeypatch):
    _force_rich_absent(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "_isatty", lambda: True)
    assert render.supports_rich() is False


def test_supports_rich_true_when_all_conditions_met(monkeypatch):
    _force_rich_present(monkeypatch)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    monkeypatch.setattr(render, "_isatty", lambda: True)
    assert render.supports_rich() is True


def test_render_brief_plain_matches_briefing_text(monkeypatch, sample_checkpoint, capsys):
    # DAIMON_PLAIN=1 from the autouse fixture forces the plain path.
    from daimon_briefing import briefing
    render.render_brief(sample_checkpoint)
    out = capsys.readouterr().out
    expected = briefing.render(sample_checkpoint)
    assert out.strip() == expected.strip()


def test_render_brief_rich_smoke(monkeypatch, sample_checkpoint, capsys):
    # Rich path: assert on CONTENT only, not format. Rich emits ANSI escapes,
    # box-drawing, and width-dependent wrapping, so exact-format assertions here
    # would be brittle. Format correctness is covered on the plain path.
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_brief(sample_checkpoint)
    out = capsys.readouterr().out
    assert "VERIFY BEFORE TRUSTING" in out
    assert "PR #6" in out


def test_render_brief_no_content(capsys):
    # #29: the old hint said "Run `serialize` first" — a dead end (serialize
    # needs a transcript path and is hook-internal). Point at the real flow.
    render.render_brief({})
    out = capsys.readouterr().out
    assert "No checkpoint yet" in out
    assert "Run `serialize` first" not in out
    assert "session end" in out  # checkpoints come from hooks automatically


def _serialize_status_data(last):
    return {
        "project": "/repo/x",
        "proj": {"exists": False},
        "glob": {"exists": False},
        "same": False,
        "last": last,
        "outstanding": [],
        "identity": None,
        "health": None,
        "team": None,
    }


def test_render_status_rich_reports_spawn_without_result(monkeypatch, capsys):
    # #29: plain status always prints spawn + result lines; the rich branch
    # rendered NOTHING for a spawn-with-no-result (in-progress/hung serialize).
    # Same command must state the same facts regardless of `rich`.
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status(_serialize_status_data(
        {"spawn": {"session_id": "S-hung", "age": "5m"}, "result": None}))
    out = capsys.readouterr().out
    assert "S-hung" in out          # the spawn is visible
    assert "none logged yet" in out  # and the missing result is stated


def test_render_status_rich_and_plain_state_same_serialize_facts(monkeypatch, capsys):
    data = _serialize_status_data(
        {"spawn": {"session_id": "S-1", "age": "2m"},
         "result": {"outcome": "success", "line": "ok S-1"}})
    render.render_status(data)
    plain = capsys.readouterr().out
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status(data)
    rich_out = capsys.readouterr().out
    for fact in ("S-1", "success"):
        assert fact in plain and fact in rich_out


def test_render_brief_notes_version_mismatch(sample_checkpoint, capsys):
    # #93: a checkpoint whose format_version differs from the current one gets a
    # note instead of silently rendering against a changed schema.
    render.render_brief({**sample_checkpoint, "format_version": "D-000"})
    out = capsys.readouterr().out
    assert "D-000" in out and "format" in out.lower()


def test_render_brief_no_note_for_current_version(sample_checkpoint, capsys):
    from daimon_briefing import serializer
    render.render_brief({**sample_checkpoint, "format_version": serializer.PROMPT_VERSION})
    out = capsys.readouterr().out
    assert "format" not in out.lower()


def test_render_brief_no_note_for_legacy_checkpoint(sample_checkpoint, capsys):
    # Legacy checkpoint (no format_version) renders normally, no note.
    render.render_brief(sample_checkpoint)
    out = capsys.readouterr().out
    assert "format" not in out.lower()


def _status_data():
    return {
        "project": "/p/A",
        "proj": {"exists": True, "session_id": "S-proj", "age": "2d", "path": "/c/A/latest.json"},
        "glob": {"exists": True, "session_id": "S-proj", "age": "2d",
                 "path": "/c/latest.json", "same_session_as_project": True},
        "last": {"result": {"outcome": "success", "line": "wrote checkpoint: /c/x.json (took 1s)"},
                 "spawn": {"session_id": "S-proj", "age": "1m"}},
    }


def test_render_status_plain_exact_format(capsys):
    # Plain output is deterministic — assert exact lines, not substrings.
    render.render_status(_status_data())
    out = capsys.readouterr().out
    assert out == (
        "project: /p/A\n"
        "project checkpoint: session S-proj, written 2d ago\n"
        "  /c/A/latest.json\n"
        "global checkpoint: same as project "
        "(this project produced the most recent checkpoint anywhere)\n"
        "  /c/latest.json\n"
        "last serialize result: success — wrote checkpoint: /c/x.json (took 1s)\n"
        "last serialize spawn: session S-proj, 1m ago\n"
    )


def test_render_status_plain_global_fallback_exact_format(capsys):
    # Distinct-session global → fallback wording with its own session + age.
    data = _status_data()
    data["glob"] = {"exists": True, "session_id": "S-glob", "age": "5d",
                    "path": "/c/latest.json"}
    render.render_status(data)
    out = capsys.readouterr().out
    assert "global checkpoint (fallback): session S-glob, written 5d ago\n" in out
    assert "  /c/latest.json\n" in out


def test_render_status_rich_smoke(monkeypatch, capsys):
    # Rich path: content-only (see test_render_brief_rich_smoke for rationale).
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_status(_status_data())
    out = capsys.readouterr().out
    assert "S-proj" in out
    assert "success" in out


def _outstanding_sample():
    return [
        {"sid": "S-A", "kind": "error", "class": "healable", "age": 180,
         "age_str": "3m", "transcript": "/t/S-A.jsonl", "project": "/p/A",
         "spawned": True, "line": "error: boom (transcript: /t/S-A.jsonl) after 3s"},
        {"sid": "S-C", "kind": "hung", "class": "hung", "age": 2400,
         "age_str": "40m", "transcript": None, "project": "/p/C",
         "spawned": True, "line": None},
    ]


def test_render_status_outstanding_block_plain_exact(capsys):
    data = _status_data()
    data["outstanding"] = _outstanding_sample()
    render.render_status(data)
    out = capsys.readouterr().out
    assert "⚠ 2 sessions failed to serialize (no checkpoint):\n" in out
    assert "  - S-A  error 3m ago — run `daimon heal`\n" in out
    assert "  - S-C  spawned 40m ago, no result (hung/killed; transcript unavailable)\n" in out


def test_render_status_outstanding_retry_exhausted_hint(capsys):
    data = _status_data()
    data["outstanding"] = [{"sid": "S-A", "kind": "error", "class": "retry-exhausted",
                            "age": 180, "age_str": "3m", "transcript": "/t/S-A.jsonl",
                            "project": "/p/A", "spawned": True, "line": "error: boom"}]
    render.render_status(data)
    out = capsys.readouterr().out
    assert "  - S-A  error 3m ago — retry attempted, still failing\n" in out


def test_render_status_unrecoverable_hint(capsys):
    data = _status_data()
    data["outstanding"] = [{"sid": "S-Z", "kind": "error", "class": "unrecoverable",
                            "age": 300, "age_str": "5m", "transcript": "/gone/S-Z.jsonl",
                            "project": "/p/Z", "spawned": False, "line": "error: boom"}]
    render.render_status(data)
    out = capsys.readouterr().out
    assert "  - S-Z  error 5m ago — transcript unavailable, cannot auto-heal\n" in out


def test_render_status_no_block_when_outstanding_empty(capsys):
    data = _status_data()
    data["outstanding"] = []
    render.render_status(data)
    out = capsys.readouterr().out
    assert "failed to serialize" not in out


def test_render_status_outstanding_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    data = _status_data()
    data["outstanding"] = _outstanding_sample()
    render.render_status(data)
    out = capsys.readouterr().out
    assert "S-A" in out and "S-C" in out
    assert "heal" in out


def _cfg_ready():
    return {"ready": True, "resolved_backend": "command", "command_source": "claude-cli",
            "command": "claude", "has_api_key": False, "has_model": False,
            "env_file": "/home/u/.daimon/env"}


def test_render_configure_plain_exact_format(capsys):
    render.render_configure(_cfg_ready())
    out = capsys.readouterr().out
    assert out == (
        "✓ ready — backend: command (claude CLI, zero-config)\n"
        "  env file: /home/u/.daimon/env\n"
    )


def test_render_configure_rich_smoke(monkeypatch, capsys):
    # Rich path: content-only (see test_render_brief_rich_smoke for rationale).
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_configure(_cfg_ready())
    out = capsys.readouterr().out
    assert "ready" in out.lower()


# --- _explain(): one-line backend explanation, all branches -----------------

@pytest.mark.parametrize("st, expected", [
    # command/claude-cli ready, resolved via zero-config claude CLI
    ({"resolved_backend": "command", "ready": True, "command_source": "claude-cli",
      "command": "claude"},
     "backend: command (claude CLI, zero-config)"),
    # command ready, but resolved from an explicit command (not the CLI auto-detect)
    ({"resolved_backend": "command", "ready": True, "command_source": "env",
      "command": "/usr/bin/claude"},
     "backend: command (/usr/bin/claude)"),
    # claude-cli backend alias, ready, zero-config
    ({"resolved_backend": "claude-cli", "ready": True, "command_source": "claude-cli",
      "command": "claude"},
     "backend: claude-cli (claude CLI, zero-config)"),
    # command backend, not ready
    ({"resolved_backend": "command", "ready": False, "command_source": "",
      "command": ""},
     "no backend — install the claude CLI or set litellm creds"),
    # litellm ready
    ({"resolved_backend": "litellm", "ready": True, "has_api_key": True,
      "has_model": True},
     "backend: litellm"),
    # litellm not ready, only api_key missing
    ({"resolved_backend": "litellm", "ready": False, "has_api_key": False,
      "has_model": True},
     "backend: litellm — missing: api_key"),
    # litellm not ready, only model missing
    ({"resolved_backend": "litellm", "ready": False, "has_api_key": True,
      "has_model": False},
     "backend: litellm — missing: model"),
    # litellm not ready, both missing — order is api_key, then model
    ({"resolved_backend": "litellm", "ready": False, "has_api_key": False,
      "has_model": False},
     "backend: litellm — missing: api_key, model"),
    # litellm not ready but nothing flagged missing — generic no-backend fallback
    ({"resolved_backend": "litellm", "ready": False, "has_api_key": True,
      "has_model": True},
     "no backend — install the claude CLI or set litellm creds"),
])
def test_explain_branches(st, expected):
    assert render._explain(st) == expected


def test_render_brief_honors_llm_briefing_when_present(monkeypatch, sample_checkpoint, capsys):
    # When DAIMON_LLM_BRIEFING is opted in, the CLI brief must surface the LLM
    # narrative (same source of truth as the hermes hook), not the deterministic text.
    from daimon_briefing import briefing
    monkeypatch.setattr(briefing.config, "llm_briefing", lambda: True)
    # #30 post-validation: the fake LLM narrative must keep every verbatim
    # quote intact or the render path (correctly) rejects it.
    sentinel = ('LLM-NARRATIVE-SENTINEL — "I\'ll merge it myself later from '
                'the GitHub UI" / "do we chunk below 1200 lines or '
                'single-pass?" / "we adopt the D-007 prompt for the serializer"')
    monkeypatch.setattr(briefing, "_render_llm", lambda cp: sentinel)
    # plain path (autouse DAIMON_PLAIN=1 keeps supports_rich False)
    render.render_brief(sample_checkpoint)
    out = capsys.readouterr().out
    assert "LLM-NARRATIVE-SENTINEL" in out


def test_render_brief_appends_drift_block(monkeypatch, sample_checkpoint, capsys):
    # Plain drift block is deterministic — assert the exact header + line format.
    drift = [{"item": {"text": "Adopt D-007 prompt"}, "kind": "soft",
              "anchor": {"qualified_name": "plugin/x.py::run"}}]
    render.render_brief(sample_checkpoint, drift=drift)
    out = capsys.readouterr().out
    assert "CODE DRIFT — verify before trusting (anchored code changed):\n" in out
    assert "- [changed] Adopt D-007 prompt  (plugin/x.py::run)\n" in out


def test_render_brief_no_drift_block_when_empty(monkeypatch, sample_checkpoint, capsys):
    render.render_brief(sample_checkpoint, drift=[])
    out = capsys.readouterr().out
    assert "CODE DRIFT" not in out.upper()


def test_render_brief_drift_malformed_anchor_label(sample_checkpoint, capsys):
    # A malformed anchor (no qualified_name) renders a label, not empty parens.
    drift = [{"item": {"text": "broken-item"}, "kind": "hard", "anchor": {}}]
    render.render_brief(sample_checkpoint, drift=drift)
    out = capsys.readouterr().out
    assert "- [GONE] broken-item  (malformed anchor)\n" in out


def test_rich_brief_shows_overflow_marker(capsys):
    import pytest
    pytest.importorskip("rich")
    from daimon_briefing import render

    b = {
        "external": [], "open_loops": [],
        "decisions": [{"text": f"d{i}", "trust": "inferred"} for i in range(8, 18)],
        "decisions_overflow": 8,
        "active_topic": None, "beliefs": [], "uncertainties": [],
    }
    render._rich_brief(b)
    out = capsys.readouterr().out
    assert "earlier decision" in out


def test_rich_brief_no_marker_when_not_capped(capsys):
    import pytest
    pytest.importorskip("rich")
    from daimon_briefing import render

    b = {
        "external": [], "open_loops": [],
        "decisions": [{"text": "d0", "trust": "inferred"}],
        "decisions_overflow": 0,
        "active_topic": None, "beliefs": [], "uncertainties": [],
    }
    render._rich_brief(b)
    out = capsys.readouterr().out
    assert "earlier decision" not in out


def test_rich_brief_shows_contradictions_section(capsys):
    # #101: contradictions flow through the same build() intermediate into the
    # rich path, as their own panel, style-matched to the other sections.
    import pytest
    pytest.importorskip("rich")
    from daimon_briefing import render

    b = {
        "external": [], "open_loops": [], "decisions": [], "decisions_overflow": 0,
        "active_topic": None, "beliefs": [], "uncertainties": [],
        "contradictions": [{"text": "cache cold vs warm", "trust": "inferred"}],
    }
    render._rich_brief(b)
    out = capsys.readouterr().out
    assert "Contradictions flagged" in out
    assert "cache cold vs warm" in out


def _identity_status_data(identity, health):
    return {
        "project": identity["git_root"],
        "proj": {"exists": True, "session_id": "P", "age": "1m", "path": "/p"},
        "glob": {"exists": False},
        "same": False, "last": None, "outstanding": [],
        "identity": identity, "health": health,
    }


def test_plain_status_shows_identity_and_verdict(capsys):
    from daimon_briefing import render
    ident = {"cwd": "/a/b", "git_root": "/a", "slug": "-a"}
    render.render_status(_identity_status_data(ident, {"ok": True, "verdict": "✓ fresh", "warnings": []}))
    out = capsys.readouterr().out
    assert "identity:" in out and "/a/b" in out and "git-root /a" in out and "-a" in out
    assert "✓ fresh" in out


def test_plain_status_shows_split_warning(capsys):
    from daimon_briefing import render
    ident = {"cwd": "/a/b", "git_root": "/a", "slug": "-a"}
    health = {"ok": False, "verdict": "⚠ split: related bucket '-a-sub' has newer work",
              "warnings": ["split: related bucket '-a-sub' has newer work"]}
    render.render_status(_identity_status_data(ident, health))
    out = capsys.readouterr().out
    assert "-a-sub" in out


def test_rich_status_shows_identity_and_verdict(capsys):
    import pytest
    pytest.importorskip("rich")
    from daimon_briefing import render
    ident = {"cwd": "/a/b", "git_root": "/a", "slug": "-a"}
    render._rich_status(_identity_status_data(ident, {"ok": True, "verdict": "✓ fresh", "warnings": []}))
    out = capsys.readouterr().out
    assert "/a/b" in out and "git-root /a" in out and "-a" in out
    assert "fresh" in out


def test_rich_status_shows_split_warning(capsys):
    import pytest
    pytest.importorskip("rich")
    from daimon_briefing import render
    ident = {"cwd": "/a/b", "git_root": "/a", "slug": "-a"}
    health = {"ok": False, "verdict": "⚠ split: related bucket '-a-sub' has newer work",
              "warnings": ["split: related bucket '-a-sub' has newer work"]}
    render._rich_status(_identity_status_data(ident, health))
    out = capsys.readouterr().out
    assert "-a-sub" in out


def test_render_heal_target_dry_run(capsys):
    from daimon_briefing import render
    plan = {"target": {"sid": "S-A", "transcript": "/t/a.jsonl", "project": "/p",
                       "age_str": "3m", "line": "x"}, "skipped": [], "note": ""}
    render.render_heal(plan, dry_run=True)
    out = capsys.readouterr().out
    assert "would heal S-A" in out and "/t/a.jsonl" in out


def test_render_heal_target_real(capsys):
    from daimon_briefing import render
    plan = {"target": {"sid": "S-A", "transcript": "/t/a.jsonl", "project": "/p",
                       "age_str": "3m", "line": "x"}, "skipped": [], "note": ""}
    render.render_heal(plan, dry_run=False)
    out = capsys.readouterr().out
    assert "healing S-A" in out and "would heal" not in out


def test_render_heal_note_and_skipped(capsys):
    from daimon_briefing import render
    plan = {"target": None,
            "skipped": [{"sid": "H1", "age_str": "40m", "reason": "spawned, no result (hung/killed) — transcript unavailable"}],
            "note": "nothing to heal — 1 failure can't be auto-repaired:"}
    render.render_heal(plan, dry_run=False)
    out = capsys.readouterr().out
    assert "nothing to heal — 1 failure can't be auto-repaired:" in out
    assert "H1" in out and "hung/killed" in out


# ---- Teammates section (#111): attributed, never merged, empty = no-op ----


def _teammate_sections():
    from daimon_briefing import briefing
    cp = {
        "working_context": {
            "active_topic": {"text": "Refactoring the store", "trust": "inferred"},
            "recent_decisions": [
                {"text": "Use atomic writes", "trust": "verbatim", "quote": "atomic or bust"},
            ],
            "open_questions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }
    return [("grace", briefing.build(cp))]


def test_render_brief_teammates_plain(sample_checkpoint, capsys):
    render.render_brief(sample_checkpoint, teammates=_teammate_sections())
    out = capsys.readouterr().out
    assert "Teammates — where they left off:" in out
    assert "[grace]" in out
    assert "Active topic: Refactoring the store" in out
    assert "Use atomic writes" in out


def test_render_brief_teammates_none_is_noop(sample_checkpoint, capsys):
    render.render_brief(sample_checkpoint, teammates=None)
    without = capsys.readouterr().out
    render.render_brief(sample_checkpoint, teammates=[])
    empty = capsys.readouterr().out
    assert without == empty
    assert "Teammates" not in without  # empty team → byte-identical, no section


def test_render_brief_teammates_rich_smoke(monkeypatch, sample_checkpoint, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_brief(sample_checkpoint, teammates=_teammate_sections())
    out = capsys.readouterr().out
    assert "grace" in out
    assert "Refactoring the store" in out


# ---- skill: `daimon skill list|install|uninstall` (#66) --------------------


def test_render_skill_list_plain_exact_format(capsys):
    render.render_skill_list([("claude", ["global"]), ("cursor", ["global", "project"])])
    out = capsys.readouterr().out
    assert out == "claude  (global)\ncursor  (global, project)\n"


def test_render_skill_list_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_skill_list([("claude", ["global"]), ("cursor", ["global", "project"])])
    out = capsys.readouterr().out
    assert "claude" in out and "cursor" in out


def test_render_skill_lines_plain_no_footer(capsys):
    render.render_skill_lines(["installed daimon skill (full) -> /h/.claude/skills/daimon/SKILL.md"])
    out = capsys.readouterr().out
    assert out == "installed daimon skill (full) -> /h/.claude/skills/daimon/SKILL.md\n"


def test_render_skill_lines_plain_with_footer(capsys):
    render.render_skill_lines(["installed daimon skill (full) -> /d"], footer=("Re-run `daimon skill install x`",))
    out = capsys.readouterr().out
    assert out == "installed daimon skill (full) -> /d\n\nRe-run `daimon skill install x`\n"


def test_render_skill_lines_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_skill_lines(
        ["warning: /d is 9,000 bytes — cursor truncates this file at 6,000 bytes; "
         "trim your own rules or use --project",
         "installed daimon skill (full) -> /d"],
        footer=("Re-run `daimon skill install cursor`",),
    )
    out = capsys.readouterr().out
    assert "truncates this file" in out
    assert "installed daimon skill" in out
    assert "Re-run `daimon skill install cursor`" in out


# ---- stats: `daimon stats` (#68 rich parity) --------------------------------


def _stats_sample():
    return {
        "usage": {"brief": 2},
        "capture": {
            "success": 2, "skipped": 1, "errors": 1, "fallback_serializes": 1,
            "hosts": {"session-end": 1, "windsurf-cascade": 1},
            "max_serialize_seconds": 42, "total_serialize_seconds": 50,
        },
        "store": {
            "checkpoints": 3, "project_buckets": 2,
            "items_by_kind": {"decision": 2, "belief": 1},
            "items_verbatim": 2, "items_inferred": 1, "items_untagged": 0,
            "items_carried": 1,
        },
    }


def _stats_empty():
    return {
        "usage": {},
        "capture": {"success": 0, "skipped": 0, "errors": 0,
                    "fallback_serializes": 0, "hosts": {},
                    "max_serialize_seconds": 0, "total_serialize_seconds": 0},
        "store": {"checkpoints": 0, "project_buckets": 0, "items_by_kind": {},
                  "items_verbatim": 0, "items_inferred": 0, "items_untagged": 0,
                  "items_carried": 0},
    }


def test_render_stats_plain_exact_format(capsys):
    render.render_stats(_stats_sample())
    out = capsys.readouterr().out
    assert out == (
        "usage (local, never transmitted):\n"
        "  brief: 2\n"
        "capture:\n"
        "  serialized: 2  skipped: 1  errors: 1  via fallback backend: 1\n"
        "  spawns by host: session-end: 1, windsurf-cascade: 1\n"
        "  serialize seconds: max 42, avg 25\n"
        "store:\n"
        "  checkpoints: 3  project buckets: 2\n"
        "  items by kind: belief: 1, decision: 2\n"
        "  trust: verbatim 2, inferred 1, untagged 0  (carried: 1)\n"
    )


def test_render_stats_plain_empty_world(capsys):
    render.render_stats(_stats_empty())
    out = capsys.readouterr().out
    assert out == (
        "usage (local, never transmitted):\n"
        "  none recorded yet\n"
        "capture:\n"
        "  serialized: 0  skipped: 0  errors: 0  via fallback backend: 0\n"
        "store:\n"
        "  checkpoints: 0  project buckets: 0\n"
        "  trust: verbatim 0, inferred 0, untagged 0  (carried: 0)\n"
    )


def test_render_stats_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_stats(_stats_sample())
    out = capsys.readouterr().out
    assert "usage" in out.lower()
    assert "brief" in out
    assert "verbatim" in out


def test_render_stats_plain_events_section(capsys):
    data = _stats_sample()
    data["events"] = {"lines": 12, "fold_ms": 0.34, "resolved_refs": 7}
    render.render_stats(data)
    out = capsys.readouterr().out
    assert "events (this project):" in out
    assert "log lines: 12" in out
    assert "resolved refs: 7" in out
    assert "fold: 0.34ms" in out


def test_render_stats_plain_omits_events_when_absent(capsys):
    # optional section, mirrors retention — no events key, no section
    render.render_stats(_stats_sample())
    assert "events (this project):" not in capsys.readouterr().out


def test_render_stats_rich_events_section(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    data = _stats_sample()
    data["events"] = {"lines": 12, "fold_ms": 0.34, "resolved_refs": 7}
    render.render_stats(data)
    out = capsys.readouterr().out
    assert "events" in out.lower()
    assert "12" in out


# ---- recall: `daimon recall` (#68 rich parity) ------------------------------


def test_render_recall_lines_plain_no_matches(capsys):
    render.render_recall_lines(["no matches"])
    assert capsys.readouterr().out == "no matches\n"


def test_render_recall_lines_plain_exact_format(capsys):
    render.render_recall_lines(
        ["[alice] [verbatim] [decision] did the thing (S1, 2h ago)"]
    )
    out = capsys.readouterr().out
    assert out == "[alice] [verbatim] [decision] did the thing (S1, 2h ago)\n"


def test_render_recall_lines_rich_smoke_preserves_brackets(monkeypatch, capsys):
    # Bracketed content ([author], [trust], [kind]) must survive rich markup
    # parsing untouched — rich would otherwise silently eat "[alice]" as an
    # (invalid) style tag. Regression guard for that data-loss failure mode.
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_recall_lines(
        ["[alice] [verbatim] [decision] did the thing (S1, 2h ago)"]
    )
    out = capsys.readouterr().out
    assert "[alice]" in out
    assert "[verbatim]" in out
    assert "[decision]" in out


# ---- hooks: `daimon hooks list|install` (#68 rich parity) -------------------


def test_render_hooks_list_plain_exact_format(capsys):
    render.render_hooks_list(
        ["windsurf  (daimon-windsurf-hooks.py; events: pre_user_prompt, post_cascade_response)"]
    )
    out = capsys.readouterr().out
    assert out == "windsurf  (daimon-windsurf-hooks.py; events: pre_user_prompt, post_cascade_response)\n"


def test_render_hooks_list_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_hooks_list(["windsurf  (entry.py; events: a, b)"])
    out = capsys.readouterr().out
    assert "windsurf" in out


def test_render_hooks_install_plain_exact_format(capsys):
    render.render_hooks_install([
        "installed 2 file(s) to /h/.daimon/hooks",
        "",
        "Register this command for the events below "
        "(host hooks config — see the host's hooks documentation):",
        "  command: python3 /h/.daimon/hooks/daimon-windsurf-hooks.py",
        "  event:   pre_user_prompt",
        "  event:   post_cascade_response",
        "",
        "Re-run `daimon hooks install windsurf` after every "
        "`uv tool upgrade daimon-briefing`.",
    ])
    out = capsys.readouterr().out
    assert out == (
        "installed 2 file(s) to /h/.daimon/hooks\n"
        "\n"
        "Register this command for the events below "
        "(host hooks config — see the host's hooks documentation):\n"
        "  command: python3 /h/.daimon/hooks/daimon-windsurf-hooks.py\n"
        "  event:   pre_user_prompt\n"
        "  event:   post_cascade_response\n"
        "\n"
        "Re-run `daimon hooks install windsurf` after every "
        "`uv tool upgrade daimon-briefing`.\n"
    )


def test_render_hooks_install_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_hooks_install(["installed 1 file(s) to /h/.daimon/hooks"])
    out = capsys.readouterr().out
    assert "installed" in out


# ---- team: `daimon team init|sync|status` (#68 rich parity) ----------------


def test_render_team_init_plain_exact_format(capsys):
    render.render_team_init([
        "initialized team sidecar: /h/.daimon/team/x",
        "checkpoints now sync there — `daimon team sync` runs opportunistically "
        "at session start",
    ])
    out = capsys.readouterr().out
    assert out == (
        "initialized team sidecar: /h/.daimon/team/x\n"
        "checkpoints now sync there — `daimon team sync` runs opportunistically "
        "at session start\n"
    )


def test_render_team_init_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_team_init(["initialized team sidecar: /x"])
    assert "initialized" in capsys.readouterr().out


def test_render_team_sync_plain_exact_format(capsys):
    render.render_team_sync(["x: 1 committed, pushed"])
    assert capsys.readouterr().out == "x: 1 committed, pushed\n"


def test_render_team_sync_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_team_sync(["x: 1 committed, pushed"])
    assert "committed" in capsys.readouterr().out


def test_render_team_status_plain_exact_format(capsys):
    render.render_team_status(["x: fresh — 0 unpushed checkpoint(s), authors: Ada"])
    assert capsys.readouterr().out == "x: fresh — 0 unpushed checkpoint(s), authors: Ada\n"


def test_render_team_status_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_team_status(["x: fresh — 0 unpushed checkpoint(s), authors: Ada"])
    assert "fresh" in capsys.readouterr().out


# ---- #75: residual rich-parity — write-checkpoint, anchor --attach,
# configure results, brief note, heal abort


def test_render_write_checkpoint_plain_exact_format(capsys):
    render.render_write_checkpoint(["wrote checkpoint: /tmp/x.json (source: introspection)"])
    assert capsys.readouterr().out == "wrote checkpoint: /tmp/x.json (source: introspection)\n"


def test_render_write_checkpoint_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_write_checkpoint(["wrote checkpoint: /tmp/x.json (source: introspection)"])
    assert "wrote checkpoint" in capsys.readouterr().out


def test_render_anchor_attach_plain_exact_format(capsys):
    render.render_anchor_attach(["attached store.py::Store.save to: the save item"])
    assert capsys.readouterr().out == "attached store.py::Store.save to: the save item\n"


def test_render_anchor_attach_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_anchor_attach(["attached store.py::Store.save to: the save item"])
    assert "attached" in capsys.readouterr().out


def test_render_configure_lines_plain_exact_format(capsys):
    render.render_configure_lines(["backend test: ok (1.2s round trip)", "wrote /tmp/env"])
    assert capsys.readouterr().out == "backend test: ok (1.2s round trip)\nwrote /tmp/env\n"


def test_render_configure_lines_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_configure_lines(["backend test: ok (1.2s round trip)"])
    assert "round trip" in capsys.readouterr().out


def test_render_brief_note_plain_exact_format(capsys):
    line = ("⚠ no checkpoint for this project — showing the global "
            "checkpoint (fallback), possibly another project's.")
    render.render_brief_note([line])
    assert capsys.readouterr().out == line + "\n"


def test_render_brief_note_rich_smoke(monkeypatch, capsys):
    # ⚠-prefixed lines take the warning styling on the rich path; content-only
    # assertion per the house rule (format is covered plain).
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_brief_note(["⚠ no checkpoint for this project"])
    assert "no checkpoint for this project" in capsys.readouterr().out


def test_render_heal_abort_plain_exact_format(capsys):
    render.render_heal_abort(["heal aborted: transcript for S-1 vanished"])
    assert capsys.readouterr().out == "heal aborted: transcript for S-1 vanished\n"


def test_render_heal_abort_rich_smoke(monkeypatch, capsys):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_heal_abort(["heal aborted: transcript for S-1 vanished"])
    assert "heal aborted" in capsys.readouterr().out
