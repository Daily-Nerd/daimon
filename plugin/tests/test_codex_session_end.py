"""Codex SessionEnd is the primary capture event; Stop stays as crash insurance.

`Stop` fires at turn scope and is throttled, so the final tail of a session is
whatever the throttle happened to allow rather than the true end state.
`SessionEnd` fires once on graceful teardown with the complete transcript.

It cannot replace `Stop`. `run_session_end_hooks` is the last statement in
graceful teardown, so a crash, a SIGKILL or a closed terminal means it never
runs. Keeping both trades an unbounded worst case for a bounded one.

Adding the entry is safe on older builds: in `hook_config.rs` at
`rust-v0.142.0`, `deny_unknown_fields` sits on `HooksFile` and NOT on
`HookEventsToml`, the struct holding the event map, so serde skips an
unrecognised key instead of failing the file.
"""
import importlib.util
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
TAG = "codex-session-end"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hooks(rel):
    return {h["event"]: h for h in _load(REPO / rel, f"m_{abs(hash(rel))}").HOOKS}


@pytest.mark.parametrize("rel", [
    "hook/codex-hooks.py",
    "plugin/daimon_briefing/codex_hooks.py",
])
def test_session_end_is_registered_with_an_explicit_timeout(rel):
    entry = _hooks(rel).get("SessionEnd")
    assert entry is not None, f"{rel} does not register SessionEnd"
    cmd = entry["entry"]["hooks"][0]
    # Codex clamps above 3 with a user-visible warning, and omitting the field
    # silently yields 1, which a cold interpreter start can exceed.
    assert cmd["timeout"] == 3, "SessionEnd timeout must be set explicitly to 3"
    assert entry["script"] == "daimon-codex-session-end.py"


def test_both_hook_manifests_agree():
    # The standalone manager runs in whatever interpreter Codex invokes and
    # cannot import the package, so the shape lives in two files by necessity.
    assert _hooks("hook/codex-hooks.py") == _hooks(
        "plugin/daimon_briefing/codex_hooks.py")


def test_stop_is_still_registered():
    # SessionEnd cannot cover ungraceful exit. Losing Stop would trade a
    # bounded worst case for total loss on a crash.
    assert "Stop" in _hooks("hook/codex-hooks.py")


def test_the_new_script_is_in_the_sync_manifest():
    sync = _load(REPO / "scripts/sync_hooks.py", "sync_hooks")
    pairs = {src for src, _dst in sync.SYNC_PAIRS}
    assert "hook/daimon-codex-session-end.py" in pairs, (
        "a hand-copied hook outside SYNC_PAIRS drifts silently")


def test_ledger_attributes_the_new_tag_without_breaking_codex_stop():
    from daimon_briefing import ledger
    ts = "2026-08-05T23:00:00Z"
    new = f"{ts} {TAG}: spawned serialize for S-1 (transcript.jsonl)"
    old = f"{ts} codex-stop: spawned serialize for S-2 (transcript.jsonl)"

    assert ledger._SPAWN_RE.match(new), "new tag is invisible to spawn parsing"
    assert ledger._SPAWN_RE.match(old), "existing codex-stop lines must keep parsing"
    assert ledger._SPAWN_RE.match(new).group(2) == "S-1"

    assert ledger._STATS_HOST_RE.match(new).group(1) == TAG
    assert ledger._STATS_HOST_RE.match(old).group(1) == "codex-stop"


def test_the_tag_is_distinct_from_the_claude_code_session_end_tag():
    # `session-end` is already taken by the Claude Code adapter. Reusing it
    # would make per-host freshness unattributable, which is the measurement
    # this change exists to enable.
    from daimon_briefing import ledger
    line = "2026-08-05T23:00:00Z session-end: spawned serialize for S-3 (t.jsonl)"
    assert ledger._STATS_HOST_RE.match(line).group(1) == "session-end"
    assert TAG != "session-end"


def test_session_end_script_exists_and_bypasses_the_throttle():
    src = (REPO / "hook/daimon-codex-session-end.py").read_text(encoding="utf-8")
    assert "DAIMON_CODEX_MIN_SERIALIZE_INTERVAL" not in src, (
        "SessionEnd is the real end, not a heartbeat; it must not be throttled")
    assert TAG in src, "log lines must carry the distinct tag"
    # Fail-open: an advisory hook that raises is surfaced to the user as a hook
    # failure, so the top level has to swallow.
    assert re.search(r"except\s+(BaseException|Exception)", src), (
        "hook must not propagate an exception to Codex")
