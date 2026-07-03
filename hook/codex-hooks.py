#!/usr/bin/env python3
"""Daimon Codex hook lifecycle manager.

Installs two Codex command hooks:
  - SessionStart -> inject latest daimon briefing as developer context
  - Stop         -> serialize Codex transcript opportunistically

Codex discovers hooks from ~/.codex/hooks.json or inline config.toml. This
manager uses hooks.json because it is easy to update idempotently without a TOML
writer dependency.
"""

import argparse
import json
import shutil
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
CODEX_DIR = Path.home() / ".codex"
HOOKS_DIR = CODEX_DIR / "hooks"
HOOKS_JSON = CODEX_DIR / "hooks.json"

# Shared helper module the hook scripts import by same-dir lookup. Copied
# alongside them on install; removed on uninstall once no daimon hook remains.
LIB = "_daimon_hook_lib.py"

HOOKS = [
    {
        "script": "daimon-codex-session-start.py",
        "event": "SessionStart",
        "entry": {
            "matcher": "startup|resume",
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-session-start.py",
                "timeout": 10,
                "statusMessage": "Reading daimon briefing...",
            }],
        },
    },
    {
        "script": "daimon-codex-stop.py",
        "event": "Stop",
        "entry": {
            "hooks": [{
                "type": "command",
                "command": "python3 ~/.codex/hooks/daimon-codex-stop.py",
                "timeout": 10,
                "statusMessage": "Writing daimon checkpoint...",
            }],
        },
    },
]


def load_hooks():
    if not HOOKS_JSON.exists():
        return {}
    return json.loads(HOOKS_JSON.read_text(encoding="utf-8"))


def save_hooks(settings, dry):
    if dry:
        return
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    if HOOKS_JSON.exists():
        backup = HOOKS_JSON.with_name(f"hooks.json.daimon-backup-{int(time.time())}")
        shutil.copy2(HOOKS_JSON, backup)
        print(f"  hooks.json backup: {backup.name}")
    HOOKS_JSON.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    print("  hooks.json written")


def is_ours(group, script):
    return any(script in h.get("command", "")
               for h in group.get("hooks", []) if isinstance(h, dict))


def install_lib(dry):
    src, dst = SRC_DIR / LIB, HOOKS_DIR / LIB
    same = dst.exists() and src.read_bytes() == dst.read_bytes()
    action = "up-to-date" if same else ("update" if dst.exists() else "copy")
    print(f"[{LIB}] library: {action}")
    if not same and not dry:
        shutil.copy2(src, dst)


def uninstall_lib(dry):
    # Remove the shared library only once no daimon hook script remains in the
    # dir. `remaining` excludes THIS manager's scripts (removed above), so it
    # counts only foreign daimon-*.py — correct under --dry-run too.
    dst = HOOKS_DIR / LIB
    if not dst.exists():
        return
    ours = {spec["script"] for spec in HOOKS}
    remaining = [p.name for p in HOOKS_DIR.glob("daimon-*.py") if p.name not in ours]
    if remaining:
        print(f"[{LIB}] library: kept ({len(remaining)} other daimon hook(s) present)")
        return
    print(f"[{LIB}] library: remove {dst}")
    if not dry:
        dst.unlink()


def install(dry):
    settings = load_hooks()
    hooks_cfg = settings.setdefault("hooks", {})
    changed = False
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for spec in HOOKS:
        src, dst = SRC_DIR / spec["script"], HOOKS_DIR / spec["script"]
        action = "update" if dst.exists() else "copy"
        same = dst.exists() and src.read_bytes() == dst.read_bytes()
        print(f"[{spec['script']}] script: {'up-to-date' if same else action}")
        if not same and not dry:
            shutil.copy2(src, dst)
            dst.chmod(0o755)
        groups = hooks_cfg.setdefault(spec["event"], [])
        if any(is_ours(g, spec["script"]) for g in groups):
            print(f"[{spec['script']}] hooks.json: already registered ({spec['event']})")
        else:
            print(f"[{spec['script']}] hooks.json: register under {spec['event']}")
            groups.append(spec["entry"])
            changed = True
    install_lib(dry)
    if changed:
        save_hooks(settings, dry)
    print("install: done" + (" (dry-run, nothing written)" if dry else
          ". Open /hooks in Codex to review and trust the hook definitions."))


def uninstall(dry):
    settings = load_hooks()
    hooks_cfg = settings.get("hooks", {})
    changed = False
    for spec in HOOKS:
        groups = hooks_cfg.get(spec["event"], [])
        keep = [g for g in groups if not is_ours(g, spec["script"])]
        if len(keep) != len(groups):
            print(f"[{spec['script']}] hooks.json: removing from {spec['event']}")
            hooks_cfg[spec["event"]] = keep
            if not keep:
                del hooks_cfg[spec["event"]]
            changed = True
        dst = HOOKS_DIR / spec["script"]
        if dst.exists():
            print(f"[{spec['script']}] script: remove {dst}")
            if not dry:
                dst.unlink()
    uninstall_lib(dry)
    if changed:
        save_hooks(settings, dry)
    print("uninstall: done" + (" (dry-run, nothing written)" if dry else
          ". Checkpoints (~/.daimon/) are untouched."))


def status():
    settings = load_hooks()
    hooks_cfg = settings.get("hooks", {})
    for spec in HOOKS:
        script_ok = (HOOKS_DIR / spec["script"]).exists()
        reg = any(is_ours(g, spec["script"])
                  for g in hooks_cfg.get(spec["event"], []))
        src_same = script_ok and \
            (SRC_DIR / spec["script"]).read_bytes() == (HOOKS_DIR / spec["script"]).read_bytes()
        state = ("installed" if script_ok and reg else
                 "partial" if script_ok or reg else "not installed")
        extra = "" if not script_ok else (" (current)" if src_same else " (outdated copy)")
        print(f"{spec['script']:32} {spec['event']:13} {state}{extra}")
    lib_ok = (HOOKS_DIR / LIB).exists()
    lib_same = lib_ok and \
        (SRC_DIR / LIB).read_bytes() == (HOOKS_DIR / LIB).read_bytes()
    lib_extra = "" if not lib_ok else (" (current)" if lib_same else " (outdated copy)")
    print(f"{LIB:32} {'(shared)':13} {'installed' if lib_ok else 'not installed'}{lib_extra}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daimon Codex hook lifecycle manager.")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["install", "uninstall", "status"])
    parser.add_argument("--dry-run", action="store_true",
                        help="preview changes without writing (install/uninstall)")
    ns = parser.parse_args()
    if ns.command == "install":
        install(ns.dry_run)
    elif ns.command == "uninstall":
        uninstall(ns.dry_run)
    else:
        status()
