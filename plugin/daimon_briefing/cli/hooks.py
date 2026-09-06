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

def _checks_line() -> str:
    """What this project has armed, after materializing it (#943).

    Installing the hook and writing what the hook reads are one step. Left
    apart, the supported install ends with a gate in place and an empty
    checks directory, and every surface then reads that as "armed, never
    fired" rather than "never wired".

    Never a failure. The scripts and the registration already landed; a
    bookkeeping refusal reported as a failed install sends the operator
    looking for a problem in the half that worked. The report's own words go
    out unrewritten, because the reason a sync refused is more specific than
    anything this line could say for it.

    No `--slug`: that flag reaches ten human-only decision verbs and is
    refused outright on a tenant-scoped home, and widening it to a verb that
    materializes executables would hand bucket-choosing power past the check
    that gates it (#899, #948)."""
    from .. import checks

    report = checks.sync(_cli._resolve_project(None))
    if report.ok:
        return f"checks: {report.armed} armed for {report.slug}"
    return f"checks: {report.reason}"


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

        lines = codex_hooks.install(pkg, Path.home())
        render.render_hooks_install(lines + ["", _checks_line()])
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
    lines += ["", _checks_line()]
    render.render_hooks_install(lines)
    return 0

def _cmd_hooks_status(args) -> int:
    """#943 slice 5: the scripts audit, plus the file those scripts READ.

    A current hook copy over a stale manifest is the shape spec 10 names as a
    risk: it fails open and is byte-identical to a clean allow, so the audit
    that reports script drift has to report manifest drift too or the two
    halves of "the gate is in place" never get checked together.

    The `--json` shape stays a list of host entries, because that is a
    contract and a script wanting the manifest has `daimon check sync
    --check --json`. The exit code folds both kinds of drift either way, so
    CI catches a stale manifest with or without the flag.

    The block is an extra: an unreadable checks directory drops it rather
    than the report the verb actually owes."""
    from .. import checks

    from .check import audit_lines

    report = _cli._hooks_status_report(Path.home())
    try:
        audit = checks.audit(_cli._resolve_project(None))
    except Exception:  # noqa: BLE001
        audit = None
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        render.render_hooks_status(
            report,
            trailing=audit_lines(audit, " (this project)") if audit else ())
    drift = any(h["drift"] for h in report) or bool(audit and audit.drift)
    return 1 if drift else 0


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
             "(CURRENT/STALE/MISSING/NOT INSTALLED), and this project's check "
             "manifest against its ledger; non-zero exit on either drift",
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
