"""`daimon team` verbs — sidecar init, sync, and status (#708 move).

Shared helpers that remain in the package `__init__` are reached through the
module object (`_cli.<name>`).
"""

import functools
import json
import sys
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import config, recall, render, store, surfaces, teamsync


def _cmd_team_init(args) -> int:
    try:
        # #279: a fresh team is born scoped to the project init ran from.
        dest = teamsync.init(args.remote_url, project_dir=Path.cwd())
    except teamsync.TeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    render.render_team_init([
        f"initialized team sidecar: {dest}",
        "checkpoints now sync there — `daimon team sync` runs opportunistically "
        "at session start",
    ])
    return 0

def _cmd_team_sync(args) -> int:
    """rc 0 for every sync-nothing-to-do shape (no git, no remotes, offline);
    warnings go to stderr but never change the rc — a degraded sync is not a
    user error. rc 2 refuses `--apply-forget` without the standing consent
    (#620 item 1) — the same code every other precondition refusal on this
    CLI uses (`bucket migrate`'s tenant-scope refusal, `hooks install`'s
    unknown host; 2 is reserved for argparse and this house convention,
    documented at privacy.exit_code)."""
    apply_forget = getattr(args, "apply_forget", False)
    # #620 item 1: checked BEFORE anything below runs a sync side effect.
    # This used to sit after `teamsync.sync()`, so a refusal still let sync
    # commit and push this machine's own pending files first, then printed a
    # stderr warning and returned 0 — indistinguishable from "applied,
    # nothing new" to any caller checking the exit code alone.
    if apply_forget and not config.team_apply_forget():
        print("daimon team: --apply-forget needs DAIMON_TEAM_APPLY_FORGET=1"
              " — a teammate's forget rewriting your own checkpoints is"
              " opt-in, and there is no undo", file=sys.stderr)
        return 2
    if getattr(args, "project", None):
        # Accepted for CLI symmetry only — say so instead of silently running
        # a global sync the user thought was scoped (#29).
        print("daimon team: --project is ignored — sync is project-agnostic "
              "(all own checkpoints sync)", file=sys.stderr)
    if not teamsync.git_available():
        render.render_team_sync(["daimon team: git not found on PATH — sync skipped"])
        return 0
    reports = teamsync.sync()
    # #600 slice B, opt-in: apply teammates' tombstones to THIS machine's own
    # checkpoints. The flag alone is not enough to reach here — the gate
    # above already refused an unconsented --apply-forget before sync ran —
    # so this branch only runs with standing consent given. Bare `daimon team
    # sync` (no flag) is spawned DETACHED at SessionStart by
    # lib.spawn_team_sync with stdout to DEVNULL, exactly like heal, and the
    # hook never passes --apply-forget, so only a typed command reaches here
    # at all. Machine-wide, matching sync's own project-agnostic contract.
    if apply_forget:
        applied = store.apply_foreign_tombstones(all_projects=True)
        # #620: this walk reaches the *.json checkpoint shapes and no
        # others, so an unqualified success line claims a sweep it did
        # not perform. The operation is irreversible and machine-wide:
        # the user spends a one-way consent, and being told it worked
        # when the value survives elsewhere is worse than a refusal.
        # The gap is derived from the surface registry, so a plaintext
        # class added later is reported as unreached rather than
        # silently absorbed into the count.
        gap = surfaces.foreign_apply_gap()
        print(f"applied teammates' forget tombstones to {len(applied)} "
              "local surface(s) across all projects")
        print(f"  not reached ({len(gap)} plaintext surface class(es)): "
              + ", ".join(gap))
        print("  the value may survive there; full coverage lands with"
              " the read-through model (#752)")
    # #246: fetched teammate files are fingerprint input — freshen here (the
    # SessionStart hook spawns sync detached, off the prompt path) so the
    # first recall after a fetch doesn't pay the rebuild. Unconditional on
    # purpose: a no-op sync warms in ~ms, and any staleness left by other
    # writers gets healed opportunistically.
    recall.warm()
    if not reports:
        render.render_team_sync([
            "daimon team: no team remote configured — nothing to sync "
            "(run `daimon team init <remote-url>`)",
        ])
        return 0
    for r in reports:
        parts = [f"{r['committed']} committed", "pushed" if r["pushed"] else "no push"]
        if r["fetched"]:
            parts.append("fetched teammates' updates")
        line = f"{r['slug']}: " + ", ".join(parts)
        if r["notes"]:
            line += " (" + "; ".join(r["notes"]) + ")"
        # Rendered per-report (not collected and rendered once after the loop):
        # this keeps a report's stdout line interleaved with ITS OWN stderr
        # warnings below, matching the ordering the pre-#68 print()-per-report
        # loop had.
        render.render_team_sync([line])
        for w in r["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
    return 0

def _cmd_team_status(args) -> int:
    # #620 item 3: DAIMON_TEAM_APPLY_FORGET is standing consent for an
    # irreversible, machine-wide rewrite of this machine's own checkpoints
    # under a teammate's forget tombstone, and it appeared in no status
    # surface before this. Machine-wide, like the setting itself — shown
    # regardless of whether git is on PATH or a remote is configured yet,
    # because the env var stays armed either way.
    armed_lines = (
        ["DAIMON_TEAM_APPLY_FORGET is armed — the next `daimon team sync "
         "--apply-forget` rewrites this machine's own checkpoints under "
         "teammates' forget tombstones, machine-wide, with no undo"]
        if config.team_apply_forget() else [])
    if not teamsync.git_available():
        render.render_team_status(
            ["daimon team: git not found on PATH"] + armed_lines)
        return 0
    rows = teamsync.team_status()
    if not rows:
        render.render_team_status([
            "no team remote configured — run `daimon team init <remote-url>`",
        ] + armed_lines)
        return 0
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    lines = []
    for row in rows:
        authors = ", ".join(row["authors"]) or "none yet"
        # #217: uncommitted checkpoints (dual-written to disk, staged+committed
        # only on the next sync) are invisible to `unpushed` alone — surface
        # them and nudge a sync, but only when there's something to nudge
        # about, so the common case's wording stays byte-identical.
        pending = row.get("pending", 0)
        pending_part = (f", {pending} pending checkpoint(s) — run "
                        "`daimon team sync`") if pending > 0 else ""
        lines.append(f"{row['slug']}: {row['freshness']} — "
                     f"{row['unpushed']} unpushed checkpoint(s)"
                     f"{pending_part}, authors: {authors}")
        # #200: a broken daimon-team.toml fails open (mapping ignored) on the
        # write path — status is the one place the parse error surfaces.
        if row.get("config_warning"):
            lines.append(f"  warning: {row['config_warning']}")
        # #279: scope is default-closed — an empty allowlist means the remote
        # receives nothing, which must be visible, not a silent surprise.
        scope = row.get("scope") or []
        if scope:
            lines.append(f"  scope: {', '.join(scope)}")
        elif config.team_project():
            lines.append("  scope: none configured — DAIMON_TEAM_PROJECT "
                         "grants this machine's sessions")
        else:
            lines.append("  scope: none — this remote receives no checkpoints "
                         "(add [scope] repos to daimon-team.toml)")
    # #387: where the CURRENT project's checkpoints route — the one place a
    # misconfigured toml is visible BEFORE a session's checkpoint quietly
    # stays in the local mirror.
    dests = store._team_write_slugs(_cli._resolve_project(getattr(args, "project", None)))
    line = "this project writes to: " + ", ".join(dests)
    if dests == [store._TEAM_LOCAL_REMOTE]:
        line += ("  (no remote grants it membership — add its repo URL to a "
                 "sidecar's daimon-team.toml [scope] repos)")
    lines.append(line)
    lines.extend(armed_lines)
    render.render_team_status(lines)
    return 0


def register(sub, fmt) -> None:
    """Register the `team` parser family on the top-level subparsers."""
    p_team = sub.add_parser(
        "team", help="shared team memory: sidecar repo init/sync/status (#113)",
        epilog="Examples:\n"
               "  daimon team init git@github.com:org/team-memory.git\n"
               "  daimon team sync\n"
               "  daimon team status\n",
    )
    team_sub = p_team.add_subparsers(dest="team_cmd", required=True)
    team_sub.add_parser = functools.partial(team_sub.add_parser, formatter_class=fmt)
    pt_init = team_sub.add_parser(
        "init", help="clone the private team sidecar repo (empty remote OK)"
    )
    pt_init.add_argument("remote_url", help="git remote URL of the PRIVATE team repo")
    pt_init.set_defaults(func=_cli._cmd_team_init)
    pt_sync = team_sub.add_parser(
        "sync", help="commit+push own checkpoints; fetch teammates' only on "
                     "remote change (ls-remote gate)"
    )
    pt_sync.add_argument(
        "--project",
        help="accepted for CLI symmetry; sync is currently project-agnostic "
             "(all own checkpoints sync regardless of project)",
    )
    pt_sync.add_argument(
        "--apply-forget", action="store_true", dest="apply_forget",
        help="also rewrite THIS machine's checkpoints under teammates' forget "
             "tombstones (#600). Requires DAIMON_TEAM_APPLY_FORGET=1; typed "
             "only — the SessionStart hook spawns a bare sync, so this can "
             "never delete your belief state unattended. Without the "
             "standing consent, refuses at rc 2 before syncing anything",
    )
    pt_sync.set_defaults(func=_cli._cmd_team_sync)
    pt_status = team_sub.add_parser(
        "status", help="per-remote freshness, own unpushed count, authors seen"
    )
    pt_status.add_argument("--json", action="store_true", help="machine-readable output")
    pt_status.set_defaults(func=_cli._cmd_team_status)
