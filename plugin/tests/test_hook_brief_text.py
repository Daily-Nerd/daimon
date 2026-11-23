"""What the briefing hooks inject when `daimon brief` has no checkpoint to show.

The CLI prints a human pointer ("No checkpoint yet ...") when there is nothing
to brief, and the hooks used to read "the output starts with that line" as
"stay silent". A folder no bucket is bound to prints the unbound notice ABOVE
the pointer, so that test read the one case worth speaking about as silence.
`lib.injectable` drops the pointer and keeps what is above it: the hooks now
speak for an unbound folder and stay silent everywhere else (tenant scope,
SEALED, a bound folder with nothing to brief).
"""

import importlib.util

import pytest

from daimon_briefing import identity, marks

from .test_claude_hooks import BRIEF_HOOK
from .test_claude_hooks import _run as run_claude
from .test_gemini_hooks import START_HOOK as GEMINI_START
from .test_gemini_hooks import _additional_context
from .test_gemini_hooks import _run as run_gemini
from .test_read_layers import PLUGIN

NOTICE = marks.warning(identity.UNBOUND_NOTICE)
POINTER = ("No checkpoint yet — nothing to brief. Checkpoints are written "
           "automatically at session end; to backfill one manually, run "
           "`daimon serialize <transcript>`.")


@pytest.fixture(scope="module")
def lib():
    path = PLUGIN.parent / "hook" / "_daimon_hook_lib.py"
    spec = importlib.util.spec_from_file_location("_hook_lib_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- the helper -------------------------------------------------------------------------


def test_the_pointer_alone_is_nothing_to_say(lib):
    assert lib.injectable(POINTER + "\n") == ""
    assert lib.injectable("") == ""
    assert lib.injectable("   \n  ") == ""


def test_a_note_above_the_pointer_is_kept_and_the_pointer_is_not(lib):
    assert lib.injectable(f"{NOTICE}\n\n{POINTER}\n") == NOTICE


def test_a_real_briefing_is_returned_whole(lib):
    text = "While you were away, here's where we left off.\n- a decision\n"
    assert lib.injectable(text) == text.strip()


def test_only_a_trailing_pointer_is_dropped(lib):
    """A line that merely starts the same way inside a body is body."""
    text = "- one\nNo checkpoint yet in this note\n- two"
    assert lib.injectable(text) == text


def test_the_old_prefix_contract_for_a_pointer_first_output_still_holds(lib):
    assert lib.injectable(POINTER) == ""


# ---- the hooks --------------------------------------------------------------------------


def test_the_claude_hook_says_the_notice_for_an_unbound_folder(
        tmp_checkpoint_dir, tmp_path):
    proc = run_claude(BRIEF_HOOK, {"cwd": "/p/never-seen"}, tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == NOTICE


def test_the_claude_hook_stays_silent_under_tenant_scope(
        tmp_checkpoint_dir, tmp_path):
    proc = run_claude(BRIEF_HOOK, {"cwd": "/p/never-seen"}, tmp_path,
                      extra_env={"DAIMON_TENANT_SCOPED": "1"})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_the_gemini_hook_says_the_notice_for_an_unbound_folder(
        tmp_checkpoint_dir, tmp_path):
    proc = run_gemini(GEMINI_START, {"cwd": "/p/never-seen",
                                     "source": "startup"}, tmp_path)
    assert proc.returncode == 0
    assert _additional_context(proc.stdout).strip() == NOTICE


def test_the_gemini_hook_stays_silent_under_tenant_scope(
        tmp_checkpoint_dir, tmp_path):
    proc = run_gemini(GEMINI_START, {"cwd": "/p/never-seen",
                                     "source": "startup"}, tmp_path,
                      extra_env={"DAIMON_TENANT_SCOPED": "1"})
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_the_hooks_are_one_helper_not_four_string_tests():
    """No hook decides "nothing to say" by matching the CLI's pointer text."""
    for name in ("daimon-session-brief.py", "daimon-gemini-session-start.py",
                 "daimon-codex-session-start.py",
                 "daimon-kimi-user-prompt-submit.py"):
        text = (PLUGIN.parent / "hook" / name).read_text(encoding="utf-8")
        assert "lib.injectable(" in text, name
        assert 'startswith("No checkpoint yet")' not in text, name

