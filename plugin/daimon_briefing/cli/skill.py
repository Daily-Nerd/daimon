"""`daimon skill` verbs — portable agent-skill shipping (#66; #708 move).

Shared helpers that remain in the package `__init__` are reached through the
module object (`_cli.<name>`).
"""

import functools
import sys
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import config, render


def _resolve_project_cwd() -> Path:
    """--project writes relative to the repo root, not whatever subdirectory
    the command was run from — same git-toplevel normalization _resolve_project
    uses for checkpoint routing (#74); falls back to plain cwd outside a repo
    or when git is unavailable (resolve_project_root's own contract)."""
    return Path(config.resolve_project_root(str(Path.cwd())))

def _cmd_skill_list(args) -> int:
    from .. import skill_install
    rows = []
    for host in sorted(skill_install.HOSTS):
        scopes = [s for s in ("global", "project")
                  if skill_install.HOSTS[host].get(s) is not None]
        rows.append((host, scopes))
    render.render_skill_list(rows)
    return 0

def _cmd_skill_show(args) -> int:
    from .. import skill_content
    print(skill_content.render_compact() if args.compact
          else skill_content.render_full(), end="")
    return 0

def _install_one(host: str, *, project: bool, cwd: Path) -> int:
    from .. import skill_install
    try:
        lines = skill_install.install(
            host, project=project, home=Path.home(), cwd=cwd)
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines, footer=(
        f"Re-run `daimon skill install {host}` after every "
        "`uv tool upgrade daimon-briefing` to refresh the content.",
    ))
    return 0


def _uninstall_one(host: str, *, project: bool, cwd: Path) -> int:
    from .. import skill_install
    try:
        lines = skill_install.uninstall(
            host, project=project, home=Path.home(), cwd=cwd)
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines)
    return 0


def _command_words(verb: str, project: bool) -> str:
    """What to print back at the operator so they can run one host by hand.
    The scope flag rides along: a suggestion that silently changes scope is
    worse than no suggestion."""
    return f"skill {verb} --project" if project else f"skill {verb}"


def _cmd_skill_install(args) -> int:
    """A named host installs exactly that host, detected or not: provisioning
    a machine before the agent is installed on it is a real workflow. With no
    host, look at the machine and decide (#1001)."""
    from . import _lifecycle

    if (rc := _lifecycle.flag_guard(args)) is not None:
        return rc
    cwd = _resolve_project_cwd()
    if args.host:
        return _install_one(args.host, project=args.project, cwd=cwd)
    return _lifecycle.run_detected(
        kind="skill", command=_command_words("install", args.project),
        args=args, cwd=cwd, project=args.project,
        runner=lambda name: _install_one(name, project=args.project, cwd=cwd))


def _cmd_skill_uninstall(args) -> int:
    from . import _lifecycle

    if (rc := _lifecycle.flag_guard(args)) is not None:
        return rc
    cwd = _resolve_project_cwd()
    if args.host:
        return _uninstall_one(args.host, project=args.project, cwd=cwd)
    return _lifecycle.run_detected(
        kind="skill", command=_command_words("uninstall", args.project),
        args=args, cwd=cwd, project=args.project, removal=True,
        runner=lambda name: _uninstall_one(name, project=args.project, cwd=cwd))


def _cmd_skill_status(args) -> int:
    """The audit the hooks half has had since #266 (#1006).

    Every host page says to re-run install after an upgrade. Nothing could
    tell a person whether they had, and on the three directory-form hosts the
    artifact carried no version marker to check by eye either.

    Non-zero exit on drift, so CI and a provisioning script can gate on it."""
    import json

    from .. import skill_install

    report = skill_install.audit(home=Path.home(), cwd=_resolve_project_cwd())
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        render.render_skill_status(report)
    return 1 if any(r["drift"] for r in report) else 0


def register(sub, fmt) -> None:
    """Register the `skill` parser family on the top-level subparsers."""
    p_skill = sub.add_parser(
        "skill",
        help="install the daimon agent skill into a host's rules/skills file (#66)")
    skill_sub = p_skill.add_subparsers(dest="skill_cmd", required=True)
    skill_sub.add_parser = functools.partial(skill_sub.add_parser, formatter_class=fmt)
    ps_list = skill_sub.add_parser("list", help="hosts with a skill renderer")
    ps_list.set_defaults(func=_cli._cmd_skill_list)
    ps_show = skill_sub.add_parser(
        "show", help="print the canonical skill content")
    ps_show.add_argument("--compact", action="store_true",
                          help="print the rules-host variant instead of SKILL.md")
    ps_show.set_defaults(func=_cli._cmd_skill_show)
    ps_status = skill_sub.add_parser(
        "status",
        help="audit each installed skill against what this CLI would write "
             "now (CURRENT/STALE/MISSING/BROKEN/NOT INSTALLED); non-zero exit "
             "on drift. --json for machines")
    ps_status.add_argument("--json", action="store_true",
                           help="machine-readable output")
    ps_status.set_defaults(func=_cmd_skill_status)
    ps_install = skill_sub.add_parser(
        "install", help="write the skill for a host (global scope by default); "
                        "with no host, detect what this machine runs")
    ps_install.add_argument("host", nargs="?",
                            help="host to install (see `daimon skill list`); "
                                 "omit to detect the hosts on this machine")
    ps_install.add_argument("--project", action="store_true",
                             help="write into the current repo instead of $HOME")
    ps_install.add_argument("--all", action="store_true",
                            help="install for every detected host without "
                                 "asking (unattended provisioning)")
    ps_install.set_defaults(func=_cli._cmd_skill_install)
    ps_uninstall = skill_sub.add_parser(
        "uninstall", help="remove exactly what install wrote")
    ps_uninstall.add_argument("host", nargs="?")
    ps_uninstall.add_argument("--project", action="store_true")
    ps_uninstall.add_argument("--all", action="store_true",
                              help="remove from every detected host daimon serves")
    ps_uninstall.set_defaults(func=_cli._cmd_skill_uninstall)
