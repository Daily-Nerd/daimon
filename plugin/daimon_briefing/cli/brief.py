"""`daimon brief` and `daimon anchor` (moved out of cli/__init__.py, #1132 PR 5).

The briefing renderer (one body for the plain and rich paths, the team
briefings and the worldcheck/receipt probes it triggers) and the anchor
resolver. Names tests patch (`_write_worldcheck_ledger`,
`_note_receipt_probe_usage`) are reached as `_cli.<name>`. Every name is
re-exported from `cli`.
"""

import json
import sys
import time

import daimon_briefing.cli as _cli

from .. import (
    anchor,
    briefing,
    config,
    ledger,
    recall,
    render,
    requests,
    store,
    trust as trust_lib,
)
from ..ledger import _format_age


def _cmd_anchor(args) -> int:
    project = _cli._resolve_project(args.project)
    a = anchor.resolve(project, args.file, args.symbol)
    if a is None:
        print(f"error: could not resolve {args.file}::{args.symbol} under {project}",
              file=sys.stderr)
        return 1
    if not args.attach:
        print(json.dumps(a, indent=2))
        return 0
    # --attach (#102): patch the anchor into the latest checkpoint's single
    # matching cognitive item and re-write through the NORMAL store path, so
    # rotation + stamping apply — the attached state becomes latest, the
    # pre-attach state is retained as prev-1.
    # #789: this caller PERSISTS what it reads, so it takes Route.OWN, the
    # route named for exactly that class (#94). With the global
    # fallback left on, a project with no bucket of its own re-wrote ANOTHER
    # project's checkpoint into its bucket under that project's session_id, and
    # the project that owns the item never received the anchor while the command
    # reported success. Refusing is the correct outcome: there is nothing here to
    # attach to, and the message below already says so.
    checkpoint = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                        admit=store.Admit.ANY)
    if checkpoint is None:
        print(f"error: no checkpoint found for {project} — nothing to attach to",
              file=sys.stderr)
        return 1
    needle = args.attach.lower()
    matches = [
        item for item in anchor._all_items(checkpoint)
        if isinstance(item, dict) and needle in str(item.get("text", "")).lower()
    ]
    if not matches:
        print(f"error: no cognitive item text contains {args.attach!r} "
              "in the latest checkpoint", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: {len(matches)} items match {args.attach!r} — "
              "narrow the match:", file=sys.stderr)
        for item in matches:
            print(f"  - {item.get('text')}", file=sys.stderr)
        return 1
    session_id = str(checkpoint.get("session_id", "")).strip()
    if not session_id:
        print("error: latest checkpoint has no session_id — cannot re-write",
              file=sys.stderr)
        return 1
    item = matches[0]
    item["anchored_to"] = a
    if store.write_checkpoint(session_id, checkpoint, project_dir=project) is None:
        # #421: write boundary refused (kill switch) — nothing was attached
        print("error: daimon disabled (DAIMON_DISABLE) — checkpoint not written",
              file=sys.stderr)
        return 1
    render.render_anchor_attach([f"attached {a['qualified_name']} to: {item.get('text')}"])
    recall.warm()  # #246: the re-write staled the index; freshen off the read path
    return 0


def _team_briefings(project, withheld: list | None = None) -> list:
    """Per-teammate briefing sections for `brief --team`, EXCLUDING the current
    author. Returns [(author, sections), ...] newest-first, or [] when the team dir
    is empty (nothing was ever mirrored). Reuses briefing.build so the #77 decision
    cap applies to teammates identically. Self is matched by slug — the same dir
    identity read_team fans in on.

    #981: each teammate checkpoint is folded through `briefing.withhold` BEFORE
    it is built, so a resolved item neither prints under Teammates nor takes a
    capped slot — the same fold every other briefing surface applies. The
    ledger that governs is the READER's own (`store.resolutions(project)`):
    what the reader resolved is what the reader stops seeing, on every
    surface including this one; a teammate's own ledger is theirs and is not
    read here. Fail-open like the main path: an unreadable ledger withholds
    nothing rather than dropping the section. `withheld` is an optional
    out-list the caller can hand in to fold the count into its note.
    """
    # project_slug munging, matching _dual_write_team's dir identity — _safe_name
    # would re-introduce the "a/b" == "a_b" collision on the self-match.
    self_slug = store.project_slug(config.author())
    # Read once, before the fan-in: one ledger read for every teammate, and
    # a failure here is this function's own to swallow (the fan-in's own
    # tombstone read of the same ledger keeps its own contract).
    try:
        resolutions = store.resolutions(project_dir=project)
    except Exception:
        resolutions = {}
    try:
        # #1109: the READER's own quarantine ledger governs here too, same
        # posture as `resolutions` above — a teammate's checkpoint is folded
        # through what THIS project's human has quarantined, never theirs.
        quarantine = trust_lib.active_value_keys(project_dir=project)
    except Exception:
        quarantine = set()
    out = []
    for author, checkpoint in store.read_team(project_dir=project):
        if store.project_slug(author) == self_slug:
            continue  # never surface your own state as a teammate
        try:
            checkpoint, dropped, _candidates = briefing.withhold(
                checkpoint, resolutions, quarantine=quarantine)
        except Exception:
            dropped = []
        if withheld is not None:
            withheld.extend(dropped)
        b = briefing.build(checkpoint)
        if b is None:
            continue  # nothing worth surfacing for this teammate
        out.append((author, b))
    return out


def _render_briefing_body(checkpoint, route, *, drift_project, teammates,
                          worldcheck_project=None, team_withheld=(),
                          loops_pointer=True) -> int:
    """Shared tail of `brief` and `brief --slug`: withhold, worldcheck, drift,
    render, footnotes. `route` is whatever the events ledger should be keyed
    by — a project dir on the normal path, a bare slug on the --slug path (the
    store's slug munging is idempotent, so a slug rides through
    project_dir-shaped APIs unchanged; guarded by
    test_project_slug_is_idempotent_on_slugs).
    `drift_project=None` skips the anchor drift check: anchor paths are
    relative to the origin project's root, which a slug cannot recover.
    `worldcheck_project=None` skips the #365 external-state spot-check for the
    same reason drift skips: --slug and global-fallback briefs render ANOTHER
    project's checkpoint, and `gh` probes resolve against THIS cwd's repo —
    the wrong repo context for those claims."""
    withheld: list = []

    if checkpoint:
        # #1128: withhold (#103), corroboration (#268), stale stamping (#977)
        # and the optional worldcheck spot-check (#365/#397/#439) all live in
        # briefing.annotate now, shared with the MCP tool and the Hermes hook.
        # Each step is fail-open inside it. Worldcheck is opt-in, budget-
        # bounded and read-only; it only RETURNS its counters and ledger rows,
        # and the writes below stay here, where the project route is already
        # resolved (worldcheck writes nothing to disk by contract).
        annotated = briefing.annotate(
            checkpoint,
            briefing.AnnotateContext(route=route,
                                     worldcheck_project=worldcheck_project),
            time.time())
        checkpoint = annotated.checkpoint
        withheld = annotated.withheld

        if annotated.worldcheck is not None:
            try:
                wc_stats = annotated.worldcheck
                # #397: the dict carries the aggregate outcomes AND a
                # "<class>:<outcome>" key per class, so one pass emits both the
                # slice-1 counters (unchanged meaning) and the per-class
                # fires-true rate the next expansion gate reads.
                for counter, count in sorted(wc_stats.items()):
                    for _ in range(int(count)):
                        _cli._note_usage(f"worldcheck:{counter}")
                # #919: the receipt-probe axis, project-scoped (see the
                # helper's own docstring for why this one axis needs project
                # scope where the loop above deliberately stays machine-wide).
                _cli._note_receipt_probe_usage(worldcheck_project, wc_stats)
                # A POINTER and a REASON CODE, never the item's text (#376) —
                # the same second stream capture writes, for the same reason:
                # folded into events.jsonl a rejection would HIDE the item it
                # describes.
                _cli._write_worldcheck_ledger(annotated.ledger_rows, route)
            except Exception:
                pass
    # NOTE: drift is checked against the resolved project root. If read_latest fell
    # back to the GLOBAL pointer (another project's checkpoint), its anchor file paths
    # are relative to a different root and may report spurious "hard" drift. Acceptable
    # for v1 (degrades safely); origin-project gating is future work (#60 follow-up).
    drift = (anchor.drifted(checkpoint, drift_project)
             if checkpoint and drift_project else [])
    # #523: the baton leads the briefing. Fail-open like withhold — a broken
    # events file must never take the briefing down.
    try:
        handoff = store.active_handoff(route)
    except Exception:
        handoff = None
    trailer = []
    if withheld or team_withheld:
        # #981: the count covers the Teammates section too, and says how
        # many were a teammate's, since `status --suppressed` lists only the
        # reader's own checkpoint.
        note = f"{len(withheld) + len(team_withheld)} resolved item(s) withheld"
        if team_withheld:
            note += f" ({len(team_withheld)} a teammate's)"
        trailer.append(note + " — `daimon status --suppressed` to list")
    # #1128: the note rides INTO render_brief so it is charged to the same
    # byte budget as the body, HANDOFF and teammates. `printed` is what the
    # budgeted brief actually showed of each panel (None: everything).
    printed = render.render_brief(checkpoint, drift=drift, teammates=teammates,
                                  handoff=handoff, project_dir=route,
                                  worldcheck_project=worldcheck_project,
                                  trailer=trailer,
                                  loops_pointer=loops_pointer)

    def _shown(panel, row) -> bool:
        # #1128: `printed` is the manifest of card ids render_brief printed in
        # full. A row the budget cut (its panel collapsed to a count line)
        # never reached the reader, so it is not stamped as surfaced; no
        # manifest means nothing is known to have been shown.
        return row["request_id"] in ((printed or {}).get(panel) or ())
    # #694 PR 2 (D1): the surfaced stamp, AFTER the render+print pipeline
    # above completes — the card has already reached the terminal, so a
    # crash between here and the write below just re-renders it next brief
    # (the safe direction) rather than a false "surfaced". Gated on the same
    # `worldcheck_project` parameter as the panel itself (D2) — never on
    # `route`, which is set on every path including --slug. Fail-open, same
    # posture as every other best-effort block in this function: a broken
    # composer must never take the briefing down.
    if worldcheck_project is not None:
        try:
            # #961 slice 3 review item 1: `decision_renderable`, not
            # `inbox_renderable` — the panel this stamp records as "shown"
            # is the one `request_panel_lines` actually reads
            # (briefing.py), and that one excludes `kind == "info"`. An
            # `info` ask stamped `surfaced` here would give `is_stale` an
            # anchor for a card that was never printed, decaying the ask
            # before anyone saw it (`is_stale`'s own `kind == "info"`
            # branch anchors on `delivered` instead, precisely because this
            # loop no longer stamps `surfaced` for one).
            for row in requests.decision_renderable(
                    project_dir=worldcheck_project).get("rows") or []:
                if requests.needs_surfaced_stamp(row) and _shown("request", row):
                    requests.stamp_surfaced(row["request_id"],
                                            project_dir=worldcheck_project)
        except Exception:
            pass
        # #694 PR 3 (D1, sender side): same posture, same gate, same
        # post-print timing — a crash before this line just re-renders the
        # verdict card next brief instead of a false "verdict_surfaced".
        try:
            for row in requests.verdict_renderable(
                    project_dir=worldcheck_project).get("rows") or []:
                # #1117: one stamp row carries whichever of the epoch and the
                # late reply the brief just showed.
                if not _shown("verdict", row):
                    continue
                reply_id = requests.unseen_reply_id(row)
                if requests.needs_verdict_surfaced_stamp(row) or reply_id:
                    requests.stamp_verdict_surfaced(
                        row["request_id"], project_dir=worldcheck_project,
                        reply_event_id=reply_id)
        except Exception:
            pass
    # #1128: the standing ">N days unverified" footer is gone. The stale
    # items carry [? unverified] marks in the body, and a section that hid
    # stale carried items says so in its own note, so the footer repeated
    # (and sometimes contradicted) what the sections already state.
    return 0


def _cmd_brief(args) -> int:
    _cli._note_usage("brief:auto" if getattr(args, "auto", False) else "brief")
    slug = getattr(args, "slug", None)
    if _cli._refuses_caller_scope(slug):
        return 2
    if slug:
        # Deliberate cross-project read (#243). Explicit-never-automatic is
        # the #94/#95 lesson, so: no global-pointer fallback (the target was
        # named — somebody else's checkpoint is never an answer), no --team
        # (fan-in routes by path), and a provenance header so this can never
        # masquerade as the current project's briefing.
        if args.project:
            print("error: --slug and --project are two answers to \"which "
                  "bucket\" — pass one", file=sys.stderr)
            return 2
        if getattr(args, "team", False):
            print("error: --team routes by project path and cannot combine "
                  "with --slug", file=sys.stderr)
            return 2
        # `slug` is a bare slug string, not a path — this survives because
        # project_slug is idempotent on slugs (pinned by its own test).
        checkpoint = store.read_latest_body(project_dir=slug, route=store.Route.OWN,
                                            admit=store.Admit.ANY)
        if not isinstance(checkpoint, dict):
            render.render_brief_note([
                f"no checkpoint bucket for slug {slug} — "
                "`daimon projects` lists what exists"])
            return 1
        render.render_brief_note([f"cross-project briefing — project: {slug}"])
        # A named bucket is somebody else's listing: no `daimon loops` pointer.
        return _render_briefing_body(checkpoint, slug,
                                     drift_project=None, teammates=None,
                                     loops_pointer=False)
    # Route like status/serialize: --project, else DAIMON_PROJECT_DIR, else cwd.
    # read_latest still falls back to the global pointer if the project has none.
    project = _cli._resolve_project(args.project)
    # #787/#795: whether the fallback fired is what the read DID, not what the
    # filesystem shows — and the route fact is now READ off the result, never
    # reconstructed from a second look (scar 0058's class). Two invariants the
    # diff does not show: under Admit.ANY nothing is ever refused, so
    # fell_back=True implies checkpoint is not None (the old second conjunct
    # is implied, not dropped); and brief cannot be identity-less, because
    # _resolve_project returns str(Path(...).resolve()) — never empty — and
    # resolve_project_root ends `return top or raw`, so the no-slug rows of
    # the read contract are unreachable on this path.
    got = store.read_latest_result(project_dir=project,
                                   route=store.Route.OWN_ELSE_GLOBAL,
                                   admit=store.Admit.ANY)
    checkpoint = got.checkpoint
    fallback_used = got.fell_back
    if fallback_used and not (getattr(args, "global_fallback", False)
                              or config.brief_global_fallback()):
        # Header-only fallback (#96): the foreign body is suppressed — one
        # warning line above a hundred foreign lines does not read as a
        # warning. Orient (where the activity actually is) and exit clean;
        # `daimon status` still shows the full pointer table.
        # `checkpoint` is not None here: see the Admit.ANY reasoning on the
        # read above. mypy cannot carry that across the ReadResult, and the
        # conjunct that would re-narrow it was removed there as implied.
        slug = str(checkpoint.get("project_slug") or "").strip() or "another project"  # type: ignore[union-attr]
        epoch = store._created_epoch(checkpoint.get("created"))  # type: ignore[union-attr]
        age = f"{_format_age(time.time() - epoch)} ago" if epoch else "age unknown"
        # #740: a baton left for a checkpoint-less project is the only
        # orientation it has — status says "waiting baton"; brief must not
        # swallow it on the header-only path. Read-only: consumption stays
        # serialize-count-based in store.active_handoff.
        render.render_handoff(store.active_handoff(project))
        render.render_brief_note([
            "No briefing for this project yet — the first serialized session "
            "will create one.",
            f"(Most recent activity elsewhere: {slug}, {age}.)",
            "Use --global-fallback or DAIMON_BRIEF_GLOBAL_FALLBACK=full to "
            "view that checkpoint here.",
        ])
        # #223: the foreign body is suppressed above, but --team still means
        # --team — a fresh project with no checkpoint of its own is exactly
        # the new-teammate case where reading the team's briefings matters
        # most. Same unprotected exposure as the main :546 call site below
        # (no new armor here); empty team -> render_teammates no-ops, so a
        # team-less machine's output stays byte-identical to today.
        if getattr(args, "team", False):
            team_withheld: list = []
            render.render_teammates(_team_briefings(project, team_withheld))
            if team_withheld:
                render.render_brief_note([
                    f"{len(team_withheld)} resolved item(s) withheld "
                    "(a teammate's)"])
        return 0
    # Label the global-pointer fallback (#29): status calls the same situation
    # "global checkpoint (fallback)"; brief must not present another project's
    # state as this project's without saying so.
    if fallback_used:
        render.render_brief_note(["⚠ no checkpoint for this project — showing the global "
                                  "checkpoint (fallback), possibly another project's."])
    # #534: a LIVE serialize for this project means a fresher checkpoint is
    # being written right now — say so instead of silently briefing one
    # session behind (measured at 10% of runs on one field machine). Keyed on
    # the ledger's liveness bar, never heartbeat existence: a stuck or
    # crashed serialize is heal's case, and a permanent false staleness line
    # would be worse than the silence this fixes.
    if ledger.serialize_in_flight(store.project_slug(project) or ""):
        render.render_brief_note([
            "⏳ a serialize is in flight — this briefing may be one session "
            "behind; re-run `daimon brief` in a few minutes for the fresh one."])
    # --team (#111): fan in teammates for THIS project. Empty team → None → the
    # renderer emits no Teammates section, byte-identical to a non-team briefing.
    team_withheld = []
    teammates = (_team_briefings(project, team_withheld)
                 if getattr(args, "team", False) else None)
    # #365: never worldcheck a fallback body — the global pointer may belong
    # to ANOTHER project, and probing this cwd's repo against that
    # checkpoint's claims answers for the wrong repo.
    return _render_briefing_body(checkpoint, project,
                                 drift_project=project, teammates=teammates,
                                 worldcheck_project=None if fallback_used
                                 else project, team_withheld=team_withheld,
                                 loops_pointer=(not fallback_used
                                                and _cli.loops_lists_project(project)))


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_brief = sub.add_parser(
        "brief", help="render the briefing from the latest checkpoint",
        epilog="Examples:\n  daimon brief\n  daimon brief --project .\n  DAIMON_PLAIN=1 daimon brief\n",
    )
    p_brief.add_argument(
        "--project",
        help="project directory to brief (default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_brief.add_argument(
        "--team", action="store_true",
        help="also show a 'Teammates' section: each teammate's active topic + "
             "recent decisions from the shared team memory (#111)",
    )
    p_brief.add_argument(
        "--slug", metavar="SLUG",
        help="brief another project's bucket by its slug (see `daimon "
             "projects`) — deliberate cross-project read, provenance-labeled, "
             "no fallback (#243)",
    )
    p_brief.add_argument(
        "--global-fallback", action="store_true",
        help="when this project has no checkpoint, render the full global "
             "checkpoint (possibly another project's) instead of the "
             "header-only note (#96)",
    )
    p_brief.add_argument(
        "--auto", action="store_true",
        help="mark this render as hook-driven (SessionStart) so `daimon stats` "
             "can separate automatic briefings from deliberate re-reads (#54)",
    )
    p_brief.set_defaults(func=_cmd_brief)

    p_anchor = sub.add_parser(
        "anchor", help="resolve a code symbol to an anchor block for a cognitive item",
        epilog="Examples:\n  daimon anchor daimon_briefing/cli.py _cmd_brief\n"
               "  daimon anchor pkg/mod.py MyClass.method --project .\n"
               "  daimon anchor pkg/mod.py fn --attach 'auth decision'\n",
    )
    p_anchor.add_argument("file", help="repo-relative path to the source file")
    p_anchor.add_argument("symbol", help="symbol name or Class.method")
    p_anchor.add_argument(
        "--project", help="project root the file is relative to (default: cwd)"
    )
    p_anchor.add_argument(
        "--attach", metavar="TEXT-MATCH",
        help="attach the anchor to the one checkpoint item whose text contains "
             "TEXT-MATCH (case-insensitive), re-writing the latest checkpoint",
    )
    p_anchor.set_defaults(func=_cmd_anchor)
