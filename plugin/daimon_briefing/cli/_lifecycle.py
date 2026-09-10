"""The no-host-named branch shared by `hooks` and `skill` (#1001).

Look at the machine first, print what is there, then decide. Detection only
applies when no host is named: an explicit host is the old command, unchanged,
because every runbook and provisioning script already names one.

Plain `print`, not the Rich renderers: this half of the output is a report a
person reads before a write happens, and it has to look the same piped into a
file as it does on a terminal.
"""

import sys
from pathlib import Path
from typing import Callable

from .. import host_detect


def flag_guard(args) -> int | None:
    """`--all` and a named host answer the same question two different ways.
    Silently letting one win would make the command's own words unreliable."""
    if getattr(args, "all", False) and getattr(args, "host", None):
        print(f"error: --all covers every detected host, so it cannot be "
              f"combined with the named host '{args.host}'. Drop one.",
              file=sys.stderr)
        return 2
    return None


def run_detected(*, kind: str, command: str, args, runner: Callable[[str], int],
                 cwd: Path, removal: bool = False, project: bool = False) -> int:
    home = Path.home()
    found = host_detect.detect(home, kind=kind, project=project)
    found = host_detect.resolve_channels(found, home=home, kind=kind, cwd=cwd,
                                         project=project)
    for line in host_detect.render_table(found):
        print(line)
    chooser = host_detect.decide_removal if removal else host_detect.decide
    decision = chooser(found, interactive=host_detect.is_interactive(),
                       all_flag=getattr(args, "all", False),
                       ask=host_detect.ask_yes_no, command=command)
    for line in decision.lines:
        print(line)
    return host_detect.run_all(decision.install, runner)
