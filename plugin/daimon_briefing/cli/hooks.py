"""`daimon hooks` verbs — packaged hook install and status (#708 move).

Host tables and drift helpers stay in the package `__init__` (brief/status
consume them too) and are reached through the module object (`_cli.<name>`).
"""

import functools
import json
import sys
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import render


def _cmd_hooks_list(args) -> int:
    lines = [f"{host}  ({_cli._host_scripts(spec)}; events: {', '.join(spec['events'])})"
             for host, spec in sorted(_cli._HOOK_HOSTS.items())]
    render.render_hooks_list(lines)
    return 0

def _cmd_hooks_install(args) -> int:
    """Copy the host's packaged hook script(s) to ~/.daimon/hooks/ — a STABLE
    path the host's hooks config points at once. Idempotent: re-running after
    `uv tool upgrade daimon-briefing` refreshes the scripts to match the
    installed CLI, which is the whole point (#43: a curl'd script drifts)."""
    from importlib import resources

    spec = _cli._HOOK_HOSTS.get(args.host)
    if spec is None:
        known = ", ".join(sorted(_cli._HOOK_HOSTS))
        print(f"error: unknown host '{args.host}' (known: {known})", file=sys.stderr)
        return 2
    pkg = resources.files("daimon_briefing._hooks")
    if spec.get("register") == "codex":
        # Codex owns its own install path: two scripts registered under two
        # events straight into ~/.codex/hooks.json (#262), not a printed snippet.
        from .. import codex_hooks

        render.render_hooks_install(codex_hooks.install(pkg, Path.home()))
        return 0
    target = _cli._hooks_target_dir()
    target.mkdir(parents=True, exist_ok=True)
    for name in spec["files"]:
        data = (pkg / name).read_bytes()
        dest = target / name
        dest.write_bytes(data)
        dest.chmod(dest.stat().st_mode | 0o100)  # u+x
    entry = target / spec["entry"]
    lines = [
        f"installed {len(spec['files'])} file(s) to {target}",
        "",
        "Register this command for the events below "
        "(host hooks config — see the host's hooks documentation):",
        f"  command: python3 {entry}",
    ]
    for ev in spec["events"]:
        lines.append(f"  event:   {ev}")
    lines.append("")
    lines.append("Re-run `daimon hooks install " + args.host +
                 "` after every `uv tool upgrade daimon-briefing`.")
    render.render_hooks_install(lines)
    return 0

def _cmd_hooks_status(args) -> int:
    report = _cli._hooks_status_report(Path.home())
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        render.render_hooks_status(report)
    return 1 if any(h["drift"] for h in report) else 0


def register(sub, fmt) -> None:
    """Register the `hooks` parser family on the top-level subparsers."""
    p_hooks = sub.add_parser(
        "hooks",
        help="ship host hook scripts from the package (#43): list, install, status",
    )
    hooks_sub = p_hooks.add_subparsers(dest="hooks_cmd", required=True)
    hooks_sub.add_parser = functools.partial(hooks_sub.add_parser, formatter_class=fmt)
    ph_list = hooks_sub.add_parser("list", help="hosts with packaged hook scripts")
    ph_list.set_defaults(func=_cli._cmd_hooks_list)
    ph_status = hooks_sub.add_parser(
        "status",
        help="audit installed hook copies against the packaged versions "
             "(CURRENT/STALE/MISSING/NOT INSTALLED); non-zero exit on drift",
    )
    ph_status.add_argument("--json", action="store_true", help="machine-readable output")
    ph_status.set_defaults(func=_cli._cmd_hooks_status)
    ph_install = hooks_sub.add_parser(
        "install",
        help="copy a host's hook script(s) to the stable path ~/.daimon/hooks/ "
             "and print the registration snippet — re-run after every upgrade",
    )
    ph_install.add_argument("host", help="host to install (see `daimon hooks list`)")
    ph_install.set_defaults(func=_cli._cmd_hooks_install)
