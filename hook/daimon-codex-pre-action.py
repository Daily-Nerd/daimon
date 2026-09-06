#!/usr/bin/env python3
"""Daimon pre-action check hook for Codex (#943), PreToolUse on a shell action.

THIS IS THE FIRST DAIMON HOOK THAT CAN FAIL A HOST ACTION. Every other hook
in this directory observes a session and cannot change it. This one runs
before a shell action and, when a ruling a human ratified carries a check
armed to enforce and that check finds a violation, it returns the host's
structured deny and the action does not run.

Everything it decides lives in `checks_host.py`, loaded from THIS file's own
directory by file location, never by name off `sys.path`. A host is a profile
row there; this script is the row's name and nothing else. If you are looking
for the decision logic, the mode table or the JSON shapes, they are there.

Output contract: stdout is empty or exactly one JSON object, stderr is empty,
and the exit code is always 0. Codex documents exit 2 as an
unconditional block, so an escaping exception that happened to exit non-zero
would block an action no check ever judged. The deny is the deliberate path.
"""

import importlib.util
import sys
from pathlib import Path


def _load_core():
    """The shared adapter core from beside this file, or None.

    File-location import (never `import checks_host`) so it never depends on
    `sys.path` state and never collides with an unrelated top-level module.
    None is a partial install: the hook then says nothing at all, because
    there is nothing to check with and a traceback before every shell action
    is worse than the missing check it would be reporting."""
    path = Path(__file__).resolve().parent / "checks_host.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "_daimon_checks_host", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 — a broken module must never crash a hook
        return None


def main() -> int:
    core = _load_core()
    if core is None:
        # Silent, and that is the profile rather than an oversight: Codex
        # documents no message channel, so a diagnostic addressed to one
        # would be text on a stream the operator never sees, on a host that
        # parses that stream. The Claude Code sibling of this script does
        # emit one. The firing log would be the honest surface here, and
        # there is no runtime loaded to write it with.
        return 0
    return core.main("codex")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail open, and silently: stdout is
        # parsed as JSON by the host, so a diagnostic printed there is a
        # decision it cannot read.
        sys.exit(0)
