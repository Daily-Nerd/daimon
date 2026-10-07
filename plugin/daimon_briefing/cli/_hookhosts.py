"""Hook host table and the plugin/hook/skill drift checks (moved out of cli/__init__.py, #1132 PR 5).

What each supported host installs and where, and whether what is installed
matches this package. Read by `daimon status`, `daimon hooks` and
`host_detect`; reached through `cli.<name>`, which re-exports every name.
"""

import hashlib
import json
from pathlib import Path
from typing import TypedDict

import daimon_briefing.cli as _cli

from .. import __version__


# ---- hooks: ship host hook scripts from the package (#43) ----

# host -> (files to install, entry script, events to register). The packaged
# copies live in daimon_briefing/_hooks/ and are drift-guarded against the
# repo's hook/ dir by tests/test_hooks_install.py. Claude Code is absent on
# purpose: the plugin marketplace owns that path.
class _HookHostSpec(TypedDict, total=False):
    """Per-key types for the hook host table (#842).

    A plain dict literal types this as a mapping to "tuple or str", so
    `spec["files"]` came out as a union and `pkg / name` read as dividing a
    Path by a sequence. The keys genuinely differ in type and genuinely differ
    in presence (codex carries `register` and no `entry`), which is what a
    TypedDict says and a value-type union cannot."""

    files: tuple[str, ...]
    entry: str
    events: tuple[str, ...]
    register: str


_HOOK_HOSTS: dict[str, _HookHostSpec] = {
    "windsurf": {
        # redact.py ships alongside the scripts so the standalone hooks can
        # scrub secrets at their write sites (#109) without importing the
        # venv-only package. A test keeps it byte-identical to the canonical
        # daimon_briefing/redact.py.
        "files": ("daimon-windsurf-hooks.py", "_daimon_hook_lib.py", "redact.py"),
        "entry": "daimon-windsurf-hooks.py",
        "events": ("pre_user_prompt", "post_cascade_response",
                   "post_cascade_response_with_transcript"),
    },
    # Codex needs two distinct scripts under two events plus a real hooks.json
    # registration, so it carries `register: "codex"` and the install command
    # delegates the whole flow to codex_hooks.install (#262). `files`/`events`
    # here drive `hooks list` only; codex_hooks owns the copy + registration.
    # #943 added daimon-codex-pre-action.py and the two modules it loads by
    # same-dir lookup. `files` is what the status audit walks, so a file the
    # install writes and this list omits goes stale invisibly. #1045 added
    # daimon-mcp-serve.py for the same reason: install_mcp() copies it into
    # this same directory and the MCP registration in config.toml points at
    # it, but it carries no event and is never in HOOKS. It joins this file
    # list only, never `events` below.
    "codex": {
        "files": ("daimon-codex-session-start.py", "daimon-codex-stop.py",
                  "daimon-codex-session-end.py", "daimon-codex-pre-action.py",
                  "daimon-action-recall.py",
                  "daimon-codex-user-prompt-submit.py",
                  "_daimon_hook_lib.py", "checks_runtime.py",
                  "checks_host.py", "daimon-mcp-serve.py"),
        "events": ("SessionStart", "Stop", "SessionEnd", "PreToolUse",
                   "UserPromptSubmit"),
        "register": "codex",
    },
    # #988. Like Codex, Kimi Code needs several scripts under several events
    # and a real registration written for it, so it carries `register: "kimi"`
    # and the install command delegates to kimi_hooks.install. Unlike every
    # other host, that registration is TOML in a file the host's own login
    # flow owns, which is why the installer edits text blocks rather than
    # re-serializing (see kimi_hooks).
    #
    # No SessionStart: measured, its stdout is dropped by the host, so the
    # briefing rides UserPromptSubmit instead. No PreToolUse: the deny channel
    # is unmeasured, so no check profile ships (see checks_host.PROFILES).
    # #1045 added daimon-mcp-serve.py, same reasoning as codex above: it is
    # what mcpServers.daimon in mcp.json points at, ships into this same
    # directory, and carries no event of its own.
    "kimi": {
        "files": ("daimon-kimi-user-prompt-submit.py",
                  "daimon-kimi-session-end.py", "daimon-kimi-stop.py",
                  "_daimon_hook_lib.py", "daimon-mcp-serve.py"),
        "events": ("UserPromptSubmit", "SessionEnd", "Stop"),
        "register": "kimi",
    },
}


def _hooks_target_dir() -> Path:
    return Path.home() / ".daimon" / "hooks"


def _host_scripts(spec) -> str:
    """Display label of a host's hook script(s): the single `entry` when the
    host has one, else the registered scripts (everything but shared helpers)."""
    if spec.get("entry"):
        return spec["entry"]
    return ", ".join(n for n in spec["files"]
                     if n not in ("_daimon_hook_lib.py", "redact.py"))


# ---- hooks status: audit installed copies against the packaged bytes (#266) ----
#
# A stale hook copy keeps *working* on old behavior after an upgrade, so drift is
# invisible until a briefing turns up wrong. `daimon hooks status` hashes the
# packaged bytes against what is installed and reports it, per host, per file.


def _host_install_dir(spec, home: Path) -> Path:
    """Where a host's hook files live. A host that registers its own scripts
    keeps them inside its own config tree (Codex: ~/.codex/hooks/; Kimi Code:
    ~/.kimi-code/hooks/, or under KIMI_CODE_HOME); everyone else shares the
    stable ~/.daimon/hooks/ that `hooks install` writes to."""
    if spec.get("register") == "codex":
        return home / ".codex" / "hooks"
    if spec.get("register") == "kimi":
        from .. import kimi_hooks

        return kimi_hooks.hooks_dir(home)
    return home / ".daimon" / "hooks"


def _hook_file_status(pkg, install_dir: Path, name: str) -> str:
    """CURRENT / STALE / MISSING for one installed file vs its packaged copy.
    ``exists()`` and ``read_bytes()`` both follow symlinks, so a symlinked
    install is judged by the bytes it points at (a broken link → MISSING)."""
    dest = install_dir / name
    if not dest.exists():
        return "MISSING"
    try:
        installed = dest.read_bytes()
    except OSError:
        return "MISSING"
    packaged = (pkg / name).read_bytes()
    match = hashlib.sha256(installed).digest() == hashlib.sha256(packaged).digest()
    return "CURRENT" if match else "STALE"


def _codex_registration_status(home: Path) -> str:
    """REGISTERED / PARTIAL / UNREGISTERED for the ~/.codex/hooks.json entries.
    Reuses codex_hooks' own loader and ownership check so this verdict can never
    drift from what `hooks install codex` writes."""
    from .. import codex_hooks

    settings = codex_hooks._load(home / ".codex" / "hooks.json")
    cfg = settings.get("hooks", {})
    if not isinstance(cfg, dict):
        cfg = {}
    found = sum(
        1 for hspec in codex_hooks.HOOKS
        if any(codex_hooks._is_ours(g, hspec["script"])
               for g in cfg.get(hspec["event"], []) if isinstance(g, dict))
    )
    if found == 0:
        return "UNREGISTERED"
    return "REGISTERED" if found == len(codex_hooks.HOOKS) else "PARTIAL"


def _registration_status(spec, home: Path) -> str | None:
    """REGISTERED / PARTIAL / UNREGISTERED for a host that writes its own
    registration, or None for one that only prints a snippet.

    Each host's verdict comes from that host's own installer module, so it can
    never drift from what `hooks install` actually writes."""
    kind = spec.get("register")
    if kind == "codex":
        return _codex_registration_status(home)
    if kind == "kimi":
        from .. import kimi_hooks

        return kimi_hooks.registration_status(home)
    return None


def _host_status_entry(host: str, spec, pkg, home: Path) -> dict:
    install_dir = _host_install_dir(spec, home)
    reg = _registration_status(spec, home)
    installed = any((install_dir / n).exists() for n in spec["files"])
    if reg is not None:
        # A self-registering host counts as installed if its hooks dir OR any
        # of our registration entries exist — either alone is a setup we must
        # audit, not ignore.
        installed = installed or install_dir.exists() or reg != "UNREGISTERED"
    entry: dict = {"host": host, "dir": str(install_dir),
                   "installed": installed, "registration": reg,
                   "files": [], "drift": False}
    if not installed:
        return entry
    entry["files"] = [{"name": n, "status": _hook_file_status(pkg, install_dir, n)}
                      for n in spec["files"]]
    file_drift = any(f["status"] in ("STALE", "MISSING") for f in entry["files"])
    reg_drift = reg is not None and reg != "REGISTERED"
    entry["drift"] = file_drift or reg_drift
    return entry


def _hooks_status_report(home: Path) -> list[dict]:
    from importlib import resources

    pkg = resources.files("daimon_briefing._hooks")
    return [_host_status_entry(host, spec, pkg, home)
            for host, spec in sorted(_HOOK_HOSTS.items())]


def _version_tuple(v: str) -> tuple:
    """Loose numeric compare, stdlib only (no packaging dependency). Trailing
    non-digits in a component are ignored, so '1.0.0rc1' sorts as (1, 0, 0)."""
    out = []
    for chunk in str(v).split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _plugin_drift(home: Path, cli_version: str) -> dict | None:
    """The installed Claude Code plugin's version vs this CLI's, or None when
    they agree or no plugin is installed (#554).

    Claude Code's hooks ship INSIDE the plugin rather than through
    `hooks install`, so #266's byte-hash audit never sees them: the host with
    the most users was the only one whose drift nothing reported. The two
    halves also move under different commands (`uv tool upgrade` for the CLI,
    the host's own plugin update for the hooks), and neither notices the other.

    The version comes from daimon's OWN manifest inside the installed tree.
    That file ships with the code that will actually execute; the host's record
    only says what it meant to install, and the two can disagree.
    """
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    installed = None
    for key, entries in plugins.items():
        if key.split("@")[0] != "daimon" or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            installed = _plugin_manifest_version(entry.get("installPath")) \
                or entry.get("version")
            if installed:
                break
        if installed:
            break
    if not installed or installed == cli_version:
        return None
    return {"installed": installed, "cli": cli_version,
            "behind": _version_tuple(installed) < _version_tuple(cli_version)}


def _plugin_manifest_version(install_path) -> str | None:
    if not install_path:
        return None
    manifest = Path(install_path) / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _plugin_drift_present() -> dict | None:
    """Same swallow-everything contract as _hook_drift_present: status must
    never crash on another tool's file format."""
    try:
        return _plugin_drift(Path.home(), __version__)
    except Exception:
        return None


def _hook_drift_present() -> bool:
    """Cheap yes/no for the `daimon status` pointer — hashes a handful of small
    files. Swallows every error: status must never crash on a weird hooks tree,
    and a probe that cannot read the packaged copies simply reports no drift."""
    try:
        return any(h["drift"] for h in _cli._hooks_status_report(Path.home()))
    except Exception:
        return False


def _skill_drift_present() -> bool:
    """Cheap yes/no for the `daimon status` pointer (#1006). Same
    swallow-everything contract as _hook_drift_present: a stale skill teaches
    an old protocol, but a status command that crashes teaches nothing at
    all. Project-scope rows resolve against the repo root the same way
    `skill install --project` writes."""
    try:
        from .. import config, skill_install

        cwd = Path(config.resolve_project_root(str(Path.cwd())))
        return any(r["drift"] for r in skill_install.audit(home=Path.home(),
                                                           cwd=cwd))
    except Exception:  # noqa: BLE001
        return False
