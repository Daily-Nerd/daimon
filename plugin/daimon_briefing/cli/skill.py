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

def _cmd_skill_install(args) -> int:
    from .. import skill_install
    try:
        lines = skill_install.install(
            args.host, project=args.project, home=Path.home(),
            cwd=_resolve_project_cwd())
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines, footer=(
        f"Re-run `daimon skill install {args.host}` after every "
        "`uv tool upgrade daimon-briefing` to refresh the content.",
    ))
    return 0

def _cmd_skill_uninstall(args) -> int:
    from .. import skill_install
    try:
        lines = skill_install.uninstall(
            args.host, project=args.project, home=Path.home(),
            cwd=_resolve_project_cwd())
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines)
    return 0


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
    ps_install = skill_sub.add_parser(
        "install", help="write the skill for a host (global scope by default)")
    ps_install.add_argument("host", help="host to install (see `daimon skill list`)")
    ps_install.add_argument("--project", action="store_true",
                             help="write into the current repo instead of $HOME")
    ps_install.set_defaults(func=_cli._cmd_skill_install)
    ps_uninstall = skill_sub.add_parser(
        "uninstall", help="remove exactly what install wrote")
    ps_uninstall.add_argument("host")
    ps_uninstall.add_argument("--project", action="store_true")
    ps_uninstall.set_defaults(func=_cli._cmd_skill_uninstall)
