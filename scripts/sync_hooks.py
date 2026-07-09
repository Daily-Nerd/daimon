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
    ("hook/daimon-windsurf-hooks.py", "plugin/daimon_briefing/_hooks/daimon-windsurf-hooks.py"),
    ("hook/_daimon_hook_lib.py", "plugin/daimon_briefing/_hooks/_daimon_hook_lib.py"),
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
