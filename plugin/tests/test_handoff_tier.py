"""The baton's authority tier follows the row that was written.

`daimon handoff` already stamped the observed `source` on its row (#1293), but
both render paths printed one fixed "left deliberately by previous session"
label whatever the row said, so a baton an agent wrote read as a person's. The
label now derives from the stored tier, and a host has an in-process writer
that is agent tier whatever terminal the process holds.
"""
import pytest

from daimon_briefing import authority, cli, jsonl, render, store
from tests._prepared import render_brief
from daimon_briefing.surfaces import Writer

PROJECT = "/p/handoff-tier"
TS = "2026-08-04T18:09:41Z"
HUMAN_LABEL = "HANDOFF (left deliberately by previous session, "
AGENT_LABEL = "HANDOFF (written by an agent, unverified, "


def _tty(monkeypatch, value):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: value, raising=False)


def _baton_rows():
    path = store._events_path(store._resolved(PROJECT))
    return [r for r in jsonl.read(path).rows if r.get("kind") == "handoff"]


def _brief(capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(["brief"]) == 0
    return capsys.readouterr().out


def _with_checkpoint(sample_checkpoint):
    store.write_checkpoint("S-a", sample_checkpoint, project_dir=PROJECT,
                           writer=Writer.HUMAN)


# ---- the CLI: the tier is the observed write path ---------------------------

def test_an_agent_run_handoff_renders_as_agent_text_without_a_checkpoint(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _tty(monkeypatch, False)
    assert cli.main(["handoff", "ship it", "--project", PROJECT]) == 0
    capsys.readouterr()
    out = _brief(capsys, monkeypatch)
    assert AGENT_LABEL in out and HUMAN_LABEL not in out
    assert "→ ship it" in out


def test_an_agent_run_handoff_renders_as_agent_text_with_a_checkpoint(
        tmp_checkpoint_dir, monkeypatch, capsys, sample_checkpoint):
    _with_checkpoint(sample_checkpoint)
    _tty(monkeypatch, False)
    assert cli.main(["handoff", "ship it", "--project", PROJECT]) == 0
    capsys.readouterr()
    out = _brief(capsys, monkeypatch)
    assert out.splitlines()[0].startswith(AGENT_LABEL)
    assert HUMAN_LABEL not in out


def test_a_human_channel_handoff_keeps_the_human_label(
        tmp_checkpoint_dir, monkeypatch, capsys, sample_checkpoint):
    _with_checkpoint(sample_checkpoint)
    _tty(monkeypatch, True)
    assert cli.main(["handoff", "ship it", "--project", PROJECT]) == 0
    capsys.readouterr()
    out = _brief(capsys, monkeypatch)
    assert out.splitlines()[0].startswith(HUMAN_LABEL)
    assert AGENT_LABEL not in out


def test_by_agent_narrows_a_terminal_session_to_the_agent_tier(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _tty(monkeypatch, True)
    assert cli.main(["handoff", "ship it", "--by", "agent",
                     "--project", PROJECT]) == 0
    [row] = _baton_rows()
    assert authority.of_row("events", row) == "agent"
    assert store.active_handoff(PROJECT)["tier"] == "agent"


def test_by_agent_also_narrows_a_clear(tmp_checkpoint_dir, monkeypatch):
    _tty(monkeypatch, True)
    assert cli.main(["handoff", "--clear", "--by", "agent",
                     "--project", PROJECT]) == 0
    [row] = _baton_rows()
    assert authority.of_row("events", row) == "agent"


# ---- the read: what the stored row holds, old rows included -----------------

def _plant(source):
    spec = {"item_ref": "", "status": "active", "note": "old baton",
            "kind": "handoff", "source": source, "item_text": ""}
    assert store.append_events([spec], project_dir=PROJECT, writer=Writer.HUMAN)


@pytest.mark.parametrize("source,tier", [
    ("cli", "human"), ("cli-tty", "human"), ("ui", "human"),
    ("agent", "agent"), ("serializer", "agent"),
    ("a-source-nobody-defined", None), ("", None),
])
def test_the_tier_is_read_off_the_stored_source(
        tmp_checkpoint_dir, source, tier):
    _plant(source)
    assert store.active_handoff(PROJECT)["tier"] == tier


@pytest.mark.parametrize("source", ["a-source-nobody-defined", ""])
def test_a_row_without_a_trustworthy_source_does_not_read_as_a_person(
        tmp_checkpoint_dir, monkeypatch, capsys, source):
    _plant(source)
    out = _brief(capsys, monkeypatch)
    assert AGENT_LABEL in out and HUMAN_LABEL not in out


def test_a_legacy_cli_row_keeps_the_human_label(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _plant("cli")
    out = _brief(capsys, monkeypatch)
    assert HUMAN_LABEL in out and AGENT_LABEL not in out


# ---- both render paths, plain and rich --------------------------------------

def _baton(tier):
    return {"ts": TS, "note": "merge PR first\nthen review", "tier": tier}


@pytest.mark.parametrize("tier,label", [("human", HUMAN_LABEL),
                                        ("agent", AGENT_LABEL),
                                        (None, AGENT_LABEL)])
def test_the_plain_seam_labels_by_tier(capsys, tier, label):
    render.render_handoff(_baton(tier))
    out = capsys.readouterr().out
    assert out.startswith(f"{label}{TS}):\n→ merge PR first\n→ then review\n")


@pytest.mark.parametrize("tier,label", [("human", HUMAN_LABEL),
                                        ("agent", AGENT_LABEL),
                                        (None, AGENT_LABEL)])
def test_the_rich_seam_labels_by_tier(monkeypatch, capsys, tier, label):
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    render.render_handoff(_baton(tier))
    out = capsys.readouterr().out
    assert label in out and "╭" in out
    other = AGENT_LABEL if label == HUMAN_LABEL else HUMAN_LABEL
    assert other not in out


@pytest.mark.parametrize("rich", [False, True])
def test_render_brief_labels_an_agent_baton(
        monkeypatch, capsys, sample_checkpoint, rich):
    monkeypatch.setattr(render, "supports_rich", lambda: rich)
    render_brief(sample_checkpoint, handoff=_baton("agent"))
    out = capsys.readouterr().out
    assert AGENT_LABEL in out and HUMAN_LABEL not in out
