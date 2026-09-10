#!/usr/bin/env python3
"""Sync the intentionally-duplicated hook-shipped files from their canonical
sources.

Some hook files are deliberately duplicated: standalone host adapters cannot
import the venv-only ``daimon_briefing`` package, so a copy of ``redact.py``
(and the packaged adapter scripts) has to sit next to them. Edit the canonical
source, then run this script to propagate the change byte-for-byte:

    uv run python scripts/sync_hooks.py

``--check`` reports drift and exits non-zero without writing anything (CI use).
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Single source of truth for the hook-file mirror topology: (canonical source,
# copy that must stay byte-identical), both repo-root-relative. The drift guard
# in plugin/tests/test_hooks_install.py imports this — do not keep a second list.
SYNC_PAIRS = (
    ("plugin/daimon_briefing/redact.py", "plugin/daimon_briefing/_hooks/redact.py"),
    ("plugin/daimon_briefing/redact.py", "hook/redact.py"),
    # #943: same direction as redact.py — the PACKAGE file is canonical and
    # both standalone copies are derivatives. Edit
    # plugin/daimon_briefing/checks_runtime.py, then run this script.
    ("plugin/daimon_briefing/checks_runtime.py",
     "plugin/daimon_briefing/_hooks/checks_runtime.py"),
    ("plugin/daimon_briefing/checks_runtime.py", "hook/checks_runtime.py"),
    # #943 slice 3: the adapter core, same direction and same reason. The
    # per-host scripts are thin and load this from their own directory.
    ("plugin/daimon_briefing/checks_host.py",
     "plugin/daimon_briefing/_hooks/checks_host.py"),
    ("plugin/daimon_briefing/checks_host.py", "hook/checks_host.py"),
    ("hook/daimon-windsurf-hooks.py", "plugin/daimon_briefing/_hooks/daimon-windsurf-hooks.py"),
    ("hook/daimon-codex-session-start.py", "plugin/daimon_briefing/_hooks/daimon-codex-session-start.py"),
    ("hook/daimon-codex-stop.py", "plugin/daimon_briefing/_hooks/daimon-codex-stop.py"),
    ("hook/daimon-codex-session-end.py", "plugin/daimon_briefing/_hooks/daimon-codex-session-end.py"),
    ("hook/daimon-codex-pre-action.py", "plugin/daimon_briefing/_hooks/daimon-codex-pre-action.py"),
    # #988: the Kimi Code adapter. Same direction as the Codex scripts —
    # hook/ is canonical, the packaged copy is the derivative.
    ("hook/daimon-kimi-user-prompt-submit.py",
     "plugin/daimon_briefing/_hooks/daimon-kimi-user-prompt-submit.py"),
    ("hook/daimon-kimi-session-end.py",
     "plugin/daimon_briefing/_hooks/daimon-kimi-session-end.py"),
    ("hook/daimon-kimi-stop.py",
     "plugin/daimon_briefing/_hooks/daimon-kimi-stop.py"),
    ("hook/_daimon_hook_lib.py", "plugin/daimon_briefing/_hooks/_daimon_hook_lib.py"),
    # #1000: the plugin-root skills/ tree is discoverable only from a source
    # checkout — the wheel ships daimon_briefing/ alone. `daimon skill install`
    # writes directory-form skills into the host's own skills dir, so it can
    # only deliver what the PACKAGE holds. skills/ stays canonical (it is where
    # the skill is authored and what Claude Code's plugin loader reads); the
    # packaged copy is the derivative.
    ("skills/daimon-end/SKILL.md",
     "plugin/daimon_briefing/_skills/daimon-end/SKILL.md"),
)


def _drifted(root):
    """Return the (src, dst) pairs whose copy differs from its canonical source."""
    out = []
    for src, dst in SYNC_PAIRS:
        source_path = root / src
        if not source_path.exists():
            sys.exit(f"manifest error: canonical source missing: {src}")
        source = source_path.read_bytes()
        copy = root / dst
        if not copy.exists() or copy.read_bytes() != source:
            out.append((src, dst))
    return out


def check(root=REPO_ROOT):
    drifted = _drifted(root)
    if drifted:
        print("hook copies out of sync (run: uv run python scripts/sync_hooks.py):")
        for src, dst in drifted:
            print(f"  {dst}  <-  {src}")
        return 1
    print("hook copies in sync")
    return 0


def sync(root=REPO_ROOT):
    drifted = _drifted(root)
    for src, dst in drifted:
        (root / dst).write_bytes((root / src).read_bytes())
        print(f"synced {dst}  <-  {src}")
    if not drifted:
        print("hook copies already in sync")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync hook-shipped duplicate files.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift and exit non-zero; write nothing",
    )
    args = parser.parse_args(argv)
    return check(REPO_ROOT) if args.check else sync(REPO_ROOT)


if __name__ == "__main__":
    sys.exit(main())
