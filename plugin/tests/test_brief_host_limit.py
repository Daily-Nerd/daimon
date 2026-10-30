"""The WHOLE `daimon brief --label` stdout fits under the host hook limits.

Claude Code spills hook stdout past 10,000 UTF-16 units; Codex truncates at
about 10,000 characters. UTF-8 bytes are never fewer than either, so the
default byte budget (9728) is the safe side, provided the budget covers every
line the verb prints and not only the body: the label, the fallback warning
and the rest ride above the body."""
import pytest

from daimon_briefing import cli, config, store
from daimon_briefing.surfaces import Writer

HOST_UNITS = 10_000
DEFAULT_BUDGET = 9728
NEVER = "/p/never-seen"


def _heavy_checkpoint():
    # Multi-byte glyphs (3 bytes, 1 UTF-16 unit each) inflate bytes past chars.
    def item(i, kind, **extra):
        return {"text": f"{kind} {i} → " + ("padding … words " * 30),
                "trust": "inferred", "importance": 5,
                "first_seen": "2026-07-01T00:00:00Z", **extra}
    return {
        "session_id": "S-" + "x" * 60,
        "working_context": {
            "active_topic": {"text": "host limit stress", "trust": "inferred"},
            "open_questions": [item(i, "loop") for i in range(12)],
            "recent_decisions": [item(i, "decision") for i in range(12)],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [item(i, "belief") for i in range(30)],
            "uncertainties": [item(i, "doubt") for i in range(10)],
            "contradictions_flagged": [item(i, "contradiction") for i in range(5)],
        },
    }


@pytest.fixture
def fallback_world(tmp_checkpoint_dir, monkeypatch):
    # Worst case for the lines above the body: the label with the global
    # fallback suffix, plus the fallback warning.
    store.write_checkpoint("S-global", _heavy_checkpoint(),
                           project_dir="/p/elsewhere", writer=Writer.HUMAN)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", NEVER)
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "1")
    monkeypatch.delenv("DAIMON_BRIEF_MAX_TOKENS", raising=False)


def _brief_stdout(capsys):
    assert cli.main(["brief", "--label"]) == 0
    return capsys.readouterr().out


def test_default_budget_is_under_the_tightest_measured_host_limit(monkeypatch):
    monkeypatch.delenv("DAIMON_BRIEF_MAX_BYTES", raising=False)
    assert config.brief_max_bytes() == DEFAULT_BUDGET < HOST_UNITS


def test_whole_hook_stdout_fits_the_default_budget(
        fallback_world, monkeypatch, capsys):
    monkeypatch.delenv("DAIMON_BRIEF_MAX_BYTES", raising=False)
    out = _brief_stdout(capsys)
    assert "global fallback" in out.splitlines()[0]
    assert "(fallback)" in out
    assert len(out.encode("utf-8")) <= DEFAULT_BUDGET
    # What a host trims and counts: UTF-16 code units, never more than bytes.
    assert len(out.strip().encode("utf-16-le")) // 2 <= HOST_UNITS


def test_terminal_render_still_prints_the_lead_lines_first(
        fallback_world, monkeypatch, capsys):
    # A TTY is never captured by a host, so nothing is charged, but the label
    # and the warning must not vanish with the move into `render_brief`.
    monkeypatch.setattr("daimon_briefing.render.supports_rich", lambda: True)
    out = _brief_stdout(capsys)
    # Rich wraps at the terminal width, so only the start of each line holds.
    assert out.startswith("DAIMON BRIEFING (checkpoint:")
    assert "no checkpoint for this project" in out


def test_label_and_warnings_are_charged_to_the_budget(
        fallback_world, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_BRIEF_MAX_BYTES", "3000")
    out = _brief_stdout(capsys)
    assert out.splitlines()[0].startswith("DAIMON BRIEFING (checkpoint: S-")
    assert len(out.encode("utf-8")) <= 3000
