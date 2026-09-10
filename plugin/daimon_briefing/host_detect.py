"""Which agent hosts exist on this machine, and who already serves each one
(#1001).

Two halves that fail apart, so they are written apart.

`detect` and `resolve_channels` are PURE READS. They never write, never
prompt, never reach the network, and never raise on another tool's file
format. Detection runs before any consent has been given, so it may not
create the directories it is looking for: a probe that mkdirs its own answer
reports every host as present on the second run.

`decide` is the policy, and it is deliberately boring. Installing is a write,
and the operator has to be able to predict which files a command touches from
the words they typed.

`present` is a filesystem or PATH fact. `channel` is who serves the host
today: `plugin` (a distribution daimon's own install verbs must never write
over), `settings` (daimon wrote it, so re-running refreshes it), or `none`. A
missing or unreadable registry is never `plugin`: missing is not a value.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class Host:
    name: str
    present: bool
    signal: str
    wirable: bool
    channel: str = "none"
    hint: str | None = None


# host -> (config directory relative to home, executable name). The directory
# is the strong signal and the binary is the fallback: Kimi Code installs its
# binary at ~/.kimi-code/bin/kimi, which is on a person's PATH and not on a
# subprocess's, so a PATH-only probe would call it absent on the very machine
# it is installed on.
HOST_SPECS: dict[str, tuple[str, str]] = {
    "claude": (".claude", "claude"),
    "codex": (".codex", "codex"),
    "cursor": (".cursor", "cursor"),
    "gemini": (".gemini", "gemini"),
    "kimi": (".kimi-code", "kimi"),
    "windsurf": (".codeium/windsurf", "windsurf"),
}

_CLAUDE_PLUGIN_HINT = (
    "claude hooks ship inside the daimon plugin, not through `hooks install`")


def _hook_hosts() -> dict:
    # Lazy: the CLI package imports this module, and the host table lives
    # there because brief and status consume it too.
    import daimon_briefing.cli as _cli

    return _cli._HOOK_HOSTS


def _wirable(name: str, kind: str, project: bool) -> tuple[bool, str | None]:
    """Can THIS verb serve this host, and if not, what should the operator be
    told instead. A host that vanishes from the table under one verb reads as
    "not installed here" rather than "this verb cannot serve it"."""
    if kind == "skill":
        from . import skill_install

        scope = "project" if project else "global"
        if skill_install.HOSTS.get(name, {}).get(scope) is not None:
            return True, None
        if skill_install.HOSTS.get(name, {}).get("project") is not None:
            return False, (f"{name} has no global rules file (rules live in IDE "
                           f"settings); use --project inside a repo")
        return False, f"daimon ships no skill for {name}"
    hosts = _hook_hosts()
    spec = hosts.get(name)
    if kind == "hook-remove":
        # Only a registration daimon WROTE can be unregistered. Everywhere
        # else it is a snippet a person pasted somewhere daimon cannot see,
        # and a verb that reports success having touched nothing is worse
        # than one that says it cannot help.
        if spec is not None and spec.get("register") == "kimi":
            return True, None
        if name == "claude":
            return False, _CLAUDE_PLUGIN_HINT
        return False, f"daimon does not own the hook registration for {name}"
    if spec is not None:
        return True, None
    if name == "claude":
        return False, _CLAUDE_PLUGIN_HINT
    return False, f"daimon ships no hooks for {name}"


def detect(home: Path, *, kind: str, project: bool = False,
           path_env: str | None = None) -> list[Host]:
    """Every known host, present or not, in a stable order."""
    which_path = path_env if path_env is not None else os.environ.get("PATH", "")
    found: list[Host] = []
    for name in sorted(HOST_SPECS):
        rel, binary = HOST_SPECS[name]
        cfg = home / rel
        signal = ""
        try:
            is_dir = cfg.is_dir()
        except OSError:
            is_dir = False
        if is_dir:
            signal = str(cfg)
        elif shutil.which(binary, path=which_path):
            signal = f"{binary} on PATH"
        wirable, hint = _wirable(name, kind, project)
        found.append(Host(name=name, present=bool(signal), signal=signal,
                          wirable=wirable, hint=hint))
    return found


def plugin_serves_claude(home: Path) -> bool:
    """The daimon plugin is registered with Claude Code. Unreadable or absent
    is False: a missing registry never counts as served."""
    reg = home / ".claude" / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(reg.read_text(encoding="utf-8")).get("plugins")
    except (OSError, ValueError, AttributeError):
        return False
    if not isinstance(plugins, dict):
        return False
    return any(key.split("@")[0] == "daimon" and entries
               for key, entries in plugins.items())


def _skill_installed(name: str, *, home: Path, cwd: Path, project: bool) -> bool:
    from . import skill_install

    entry = skill_install.HOSTS.get(name, {}).get("project" if project else "global")
    if entry is None:
        return False
    try:
        return ((cwd if project else home) / entry[0]).exists()
    except OSError:
        return False


def _hooks_installed(home: Path) -> set[str]:
    import daimon_briefing.cli as _cli

    try:
        report = _cli._hooks_status_report(home)
    except Exception:  # noqa: BLE001 - a read must not crash on a weird tree
        return set()
    return {row["host"] for row in report if row.get("installed")}


def resolve_channels(found: list[Host], *, home: Path, kind: str,
                     cwd: Path | None = None,
                     project: bool = False) -> list[Host]:
    """Fill in who serves each PRESENT host. Absent hosts keep `none`: a
    channel on a host that is not here is a claim about a machine that does
    not exist."""
    cwd = cwd if cwd is not None else Path.cwd()
    installed_hooks = _hooks_installed(home) if kind != "skill" else set()
    for host in found:
        if not host.present:
            continue
        # The plugin channel is reported even where the verb cannot write, so
        # the table explains claude rather than leaving it blank.
        if host.name == "claude" and plugin_serves_claude(home):
            host.channel = "plugin"
            continue
        if not host.wirable:
            continue
        if kind == "skill":
            if _skill_installed(host.name, home=home, cwd=cwd, project=project):
                host.channel = "settings"
        elif host.name in installed_hooks:
            host.channel = "settings"
    return found


def render_table(found: list[Host]) -> list[str]:
    lines = []
    for host in found:
        state = "present" if host.present else "absent"
        tail = (f"channel={host.channel}" if host.wirable
                else f"not wirable, {host.hint}")
        line = f"{host.name:9} {state:8} {tail}"
        if host.present:
            line += f"  ({host.signal})"
        lines.append(line)
    return lines


@dataclass
class Decision:
    install: list[str]
    lines: list[str]


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_yes_no(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _explicit(command: str, names: list[str]) -> list[str]:
    return [f"  daimon {command} {n}" for n in names] + [
        f"  daimon {command} --all"]


def decide(found: list[Host], *, interactive: bool, all_flag: bool,
           ask: Callable[[str], bool], command: str) -> Decision:
    """Which hosts this install writes to.

    A host already on the `settings` channel stays a candidate: re-running
    install is the documented post-upgrade step, because the installed skill
    is a static copy of content that moves with the CLI. Only `plugin` takes a
    host out of the running, and that is not a refusal to be overridden: it is
    a different distribution channel that daimon's own install verbs do not
    write over.
    """
    candidates = [h for h in found
                  if h.present and h.wirable and h.channel != "plugin"]
    lines = [f"{h.name}: already served by the daimon plugin, not asked"
             for h in found if h.present and h.channel == "plugin"]
    if not candidates:
        # An empty machine and a fully served one are two different answers.
        # Blaming the plugin when nothing was detected sends the reader
        # looking for a plugin that is not there.
        if not any(h.present for h in found):
            lines.append("nothing to install: no agent host detected on this machine")
        else:
            lines.append("nothing to install: every detected host is already "
                         "served or cannot be wired by this command")
        return Decision([], lines)
    names = sorted(h.name for h in candidates)
    if all_flag:
        return Decision(names, lines)
    if len(names) == 1:
        lines.append(f"{names[0]}: the only host this command can serve, "
                     f"installing without asking")
        return Decision(names, lines)
    if interactive:
        # ONE question. The operator is answering "wire this machine", and a
        # per-host interrogation turns a single decision into five.
        if ask(f"install for {', '.join(names)}?"):
            return Decision(names, lines)
        lines.append("declined, nothing written")
        return Decision([], lines)
    lines.append("several hosts detected and no terminal to ask on; "
                 "nothing written. Run one of:")
    lines.extend(_explicit(command, names))
    return Decision([], lines)


def decide_removal(found: list[Host], *, interactive: bool, all_flag: bool,
                   ask: Callable[[str], bool], command: str) -> Decision:
    """Which hosts this removal touches.

    The install ladder writes to a lone detected host without asking, because
    a file appearing is what the operator asked for. Removal is the other
    direction: it gets a confirmation even for a single candidate, and with no
    terminal and no `--all` it writes nothing and prints the explicit
    commands.
    """
    served = [h for h in found
              if h.present and h.wirable and h.channel == "settings"]
    lines: list[str] = []
    if not served:
        lines.append("nothing to remove: no detected host is served by daimon")
        return Decision([], lines)
    names = sorted(h.name for h in served)
    if all_flag:
        return Decision(names, lines)
    if interactive:
        if ask(f"remove daimon from {', '.join(names)}?"):
            return Decision(names, lines)
        lines.append("declined, nothing written")
        return Decision([], lines)
    lines.append("nothing written: removal is never unasked. Run one of:")
    lines.extend(_explicit(command, names))
    return Decision([], lines)


def run_all(names: list[str], runner: Callable[[str], int]) -> int:
    """Worst exit code wins, and every host still runs. One host failing must
    not be masked by the others succeeding: a provisioning script reads the
    exit code, not the prose."""
    rc = 0
    for name in names:
        rc = max(rc, runner(name))
    return rc
