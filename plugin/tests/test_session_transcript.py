"""One definition of "is this file a session transcript of host H" (#1362).

A host's project folder is shared: other tools write `.jsonl` logs there. The
catch-up sweep and the serializer both ask `session_transcript.not_a_transcript`
whether a file is one of those. It says yes only when the name does not fit the
host's naming AND no row carries a host marker, so a drifted or oddly named real
transcript stays a loud failure (scar 0045).
"""
import json
import sys
import uuid
from pathlib import Path

import pytest

from daimon_briefing import (ledger, session_transcript
                             as st)
from tests.conftest import FIXTURES

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from sync_hooks import SYNC_PAIRS  # noqa: E402

REPO = Path(__file__).parents[2]
PROJECT = "/p/not-a-transcript"
TOOL_ROWS = [{"ts": "2026-08-16T02:23:06Z", "session": "s", "file": "/m.md"}] * 3


def _write(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


UUID_NAME = f"{uuid.uuid4()}.jsonl"
ROLLOUT_NAME = f"rollout-2026-11-01T10-20-30-{uuid.uuid4()}.jsonl"


@pytest.mark.parametrize("host,name,fits", [
    ("claude-code", UUID_NAME, True),
    ("claude-code", "memory-index-reads.jsonl", False),
    ("claude-code", UUID_NAME.replace(".jsonl", ".log"), False),
    ("codex", ROLLOUT_NAME, True),
    ("codex", "notes.jsonl", False),
])
def test_name_fits_the_host_pattern(host, name, fits):
    assert st.name_fits(host, Path("/x") / name) is fits


def test_a_host_with_unverified_markers_is_never_called_foreign(tmp_path):
    # Kimi and Windsurf: no verified marker census, so today's loud behaviour
    # stays: nothing is "not a transcript" there.
    odd = _write(tmp_path / "log.jsonl", TOOL_ROWS)
    for host in ("kimi", "windsurf", "gemini", "manual", None):
        assert st.not_a_transcript(odd, host) is False


def test_a_tool_log_in_the_claude_folder_is_not_a_transcript(tmp_path):
    odd = _write(tmp_path / "memory-index-reads.jsonl", TOOL_ROWS)
    assert st.not_a_transcript(odd, "claude-code") is True


def test_a_real_name_is_a_transcript_whatever_the_rows_say(tmp_path):
    drifted = _write(tmp_path / UUID_NAME, [{"foo": 1}])
    assert st.not_a_transcript(drifted, "claude-code") is False
    rollout = _write(tmp_path / ROLLOUT_NAME, [{"foo": 1}])
    assert st.not_a_transcript(rollout, "codex") is False


@pytest.mark.parametrize("row", [
    {"type": "user", "message": {"role": "user", "content": "hi"}},
    {"type": "session_meta", "payload": {}},
    {"role": "user", "content": "hi"},
    {"message": {"content": "x"}},
    {"payload": {"type": "x"}},
])
def test_a_host_marker_on_any_row_keeps_an_oddly_named_file_a_candidate(
        tmp_path, row):
    odd = _write(tmp_path / "renamed.jsonl", [*TOOL_ROWS, row])
    assert st.not_a_transcript(odd, "claude-code") is False
    assert st.not_a_transcript(odd, "codex") is False


@pytest.mark.parametrize("text", ["", "\n\n", "{}\n", "not json\n",
                                  '[1, 2]\n', '{"ts": 1}\nnot json\n'])
def test_empty_unparsable_or_empty_object_rows_stay_loud(tmp_path, text):
    odd = tmp_path / "odd.jsonl"
    odd.write_text(text, encoding="utf-8")
    assert st.not_a_transcript(odd, "claude-code") is False


def test_a_missing_file_is_not_called_foreign(tmp_path):
    assert st.not_a_transcript(tmp_path / "gone.jsonl", "claude-code") is False


def test_every_row_of_a_real_claude_and_codex_fixture_carries_a_marker():
    # Verified on real files (all rows of 200 Claude and 212 Codex files have a
    # string `type`); pinned here on the shapes the loader reads.
    for name in ("claude_session_owner_only.jsonl",
                 "claude_attachment_rows.jsonl"):
        for line in (FIXTURES / name).read_text().splitlines():
            if line.strip():
                assert st.row_has_host_marker(json.loads(line)), (name, line)


def test_the_rollout_pattern_has_one_owner():
    assert ledger._ROLLOUT_STEM_RE is st.ROLLOUT_STEM_RE


def test_the_module_is_shipped_beside_the_hooks_by_the_sync_mechanism():
    dests = {dst for src, dst in SYNC_PAIRS
             if src == "plugin/daimon_briefing/session_transcript.py"}
    assert dests == {"plugin/daimon_briefing/_hooks/session_transcript.py",
                     "hook/session_transcript.py"}


@pytest.mark.parametrize("rel", [
    "hook/daimon-hooks.py", "hook/codex-hooks.py",
    "plugin/daimon_briefing/codex_hooks.py"])
def test_the_installers_of_both_sweeping_hosts_ship_the_module(rel):
    # Claude Code (daimon-hooks.py) and Codex (both installers) run the sweep.
    # An install without the module silently reverts to spawning every file.
    text = (REPO / rel).read_text(encoding="utf-8")
    assert '"session_transcript.py"' in text.split("MODULES = ")[1].split(")")[0]
