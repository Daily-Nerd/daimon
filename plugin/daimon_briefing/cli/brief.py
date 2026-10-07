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


class _TeamCounts:
    """What the Teammates section withheld from the reader, for the brief's
    trailer note: loops the reader's own ledger resolved, and items the
    reader's own quarantine removed. A forgotten value is counted nowhere:
    it has to read as absent."""

    def __init__(self):
        self.resolved = 0
        self.quarantined = 0


def _team_briefings(project, counts: "_TeamCounts | None" = None) -> list:
    """Per-teammate briefing sections for `brief --team`, EXCLUDING the current
    author. Returns [(author, sections), ...] newest-first, or [] when the team dir
    is empty (nothing was ever mirrored). Reuses briefing.build so the #77 decision
    cap applies to teammates identically. Self is matched by slug — the same dir
    identity read_team fans in on.

    #981: each teammate checkpoint goes through the view BEFORE it is built
    (`view.team`), so a resolved item neither prints under Teammates nor takes
    a capped slot, the same fold every other briefing surface applies. The
    ledgers that govern are the READER's own: what the reader resolved or
    quarantined is what the reader stops seeing, on every surface including
    this one; a teammate's own ledger is theirs and is not read here. An
    unreadable trust ledger closes the view: no teammate item shows. The
    blocks carry no stamps (`stamps=False`), as they never did. `counts` is an
    optional collector the caller can hand in to fold the totals into its
    trailer."""
    from .. import view
    # project_slug munging, matching _dual_write_team's dir identity — _safe_name
    # would re-introduce the "a/b" == "a_b" collision on the self-match.
    self_slug = store.project_slug(config.author())
    now = time.time()
    out = []
    for author, opened in view.team(project, live=True):
        if store.project_slug(author) == self_slug:
            continue  # never surface your own state as a teammate
        prepared = briefing.prepare(project, now, opened=opened, stamps=False)
        if counts is not None:
            counts.resolved += prepared.suppressed
            counts.quarantined += prepared.quarantined
        b = briefing.build(prepared.checkpoint)
        if b is None:
            continue  # nothing worth surfacing for this teammate
        out.append((author, b))
    return out


def _withheld_trailer(own, team) -> list:
    """The advisory lines about what the briefing withheld. Resolved loops
    keep their count and wording; a quarantined item gets its own line, only
    when there is one. `own` is the `Annotated` of the reader's briefing (or
    None), `team` a `_TeamCounts` (or None)."""
    resolved = (own.suppressed if own else 0) + (team.resolved if team else 0)
    quarantined = ((own.quarantined if own else 0)
                   + (team.quarantined if team else 0))
    trailer = []
    if resolved:
        # #981: the count covers the Teammates section too, and says how
        # many were a teammate's, since `status --suppressed` lists only the
        # reader's own checkpoint.
        note = f"{resolved} resolved item(s) withheld"
        if team and team.resolved:
            note += f" ({team.resolved} a teammate's)"
        trailer.append(note + " — `daimon status --suppressed` to list")
    if quarantined:
        note = f"{quarantined} quarantined item(s) withheld"
        if team and team.quarantined:
            note += f" ({team.quarantined} a teammate's)"
        trailer.append(note + " — `daimon trust list` shows them")
    return trailer


def _render_briefing_body(annotated, route, *, drift_project, teammates,
                          worldcheck_project=None, team_counts=None,
                          loops_pointer=True) -> int:
    """Shared tail of `brief` and `brief --slug`: worldcheck bookkeeping,
    drift, render, footnotes. `annotated` is `briefing.prepare`'s result (the
    checkpoint is already through the view and stamped). `route` is whatever
    the events ledger should be keyed by — a project dir on the normal path, a
    bare slug on the --slug path (the store's slug munging is idempotent, so a
    slug rides through project_dir-shaped APIs unchanged; guarded by
    test_project_slug_is_idempotent_on_slugs).
    `drift_project=None` skips the anchor drift check: anchor paths are
    relative to the origin project's root, which a slug cannot recover.
    `worldcheck_project=None` skips the #365 external-state spot-check for the
    same reason drift skips: --slug and global-fallback briefs render ANOTHER
    project's checkpoint, and `gh` probes resolve against THIS cwd's repo —
    the wrong repo context for those claims."""
    checkpoint = annotated.checkpoint if annotated else None

    if checkpoint:
        # #1128: worldcheck (#365/#397/#439) runs inside briefing.prepare,
        # shared with the MCP tool and the Hermes hook. It is opt-in, budget-
        # bounded and read-only; it only RETURNS its counters and ledger rows,
        # and the writes below stay here, where the project route is already
        # resolved (worldcheck writes nothing to disk by contract).
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
    # #523: the baton leads the briefing. Fail-open like the view's reads — a broken
    # events file must never take the briefing down.
    try:
        handoff = store.active_handoff(route)
    except Exception:
        handoff = None
    trailer = _withheld_trailer(annotated, team_counts)
    # #1128: the note rides INTO render_brief so it is charged to the same
    # byte budget as the body, HANDOFF and teammates. `printed` is what the
    # budgeted brief actually showed of each panel (None: everything).
    printed = render.render_brief(checkpoint, drift=drift, teammates=teammates,
                                  handoff=handoff, project_dir=route,
                                  worldcheck_project=worldcheck_project,
                                  trailer=trailer,
                                  loops_pointer=loops_pointer,
                                  snap=annotated.snapshot if annotated else None,
                                  notes=annotated.notes if annotated else ())

    # #694 PR 2 (D1): the surfaced stamp, AFTER the render+print pipeline
    # above completes — the card has already reached the terminal, so a
    # crash between here and the write below just re-renders it next brief
    # (the safe direction) rather than a false "surfaced". `printed` is the
    # manifest of cards `render_brief` printed in full, taken from the one
    # read that built the panels: a row the budget cut (its panel collapsed
    # to a count line) never reached the reader, so it is not stamped, and
    # no manifest means nothing is known to have been shown. Gated on the
    # same `worldcheck_project` parameter as the panel itself (D2) — never
    # on `route`, which is set on every path including --slug. Fail-open,
    # same posture as every other best-effort block in this function: a
    # broken composer must never take the briefing down.
    if worldcheck_project is not None:
        # #961 slice 3 review item 1: the request cards are the panel's own
        # (`decision_renderable`, which excludes `kind == "info"`), so an
        # `info` ask is never stamped `surfaced` for a card that was never
        # printed.
        for card in (printed or {}).get("request") or ():
            if not card.stamp:
                continue
            try:
                requests.stamp_surfaced(card.request_id,
                                        project_dir=worldcheck_project)
            except Exception:
                pass
        # #694 PR 3 (D1, sender side): same posture, same gate, same
        # post-print timing. #1117: one stamp row carries whichever of the
        # epoch and the late reply the brief just showed.
        for card in (printed or {}).get("verdict") or ():
            if not card.stamp:
                continue
            try:
                requests.stamp_verdict_surfaced(
                    card.request_id, project_dir=worldcheck_project,
                    reply_event_id=card.reply_event_id)
            except Exception:
                pass
    # #1128: the standing ">N days unverified" footer is gone. The stale
    # items carry [? unverified] marks in the body, and a section that hid
    # stale carried items says so in its own note, so the footer repeated
    # (and sometimes contradicted) what the sections already state.
    return 0


def _prepared(project, route, worldcheck_project=None):
    """`briefing.prepare` for a CLI verb, or None after printing the one error
    line (rc 2): when the view cannot be built no briefing renders at all,
    never an unfiltered one."""
    try:
        return briefing.prepare(project, time.time(), route=route,
                                worldcheck_project=worldcheck_project)
    except Exception as exc:  # noqa: BLE001 — reported, never rendered around
        print("error: the briefing could not be prepared "
              f"({type(exc).__name__}); nothing was rendered", file=sys.stderr)
        return None


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
        annotated = _prepared(slug, store.Route.OWN)
        if annotated is None:
            return 2
        if not isinstance(annotated.checkpoint, dict):
            render.render_brief_line([
                f"no checkpoint bucket for slug {slug} — "
                "`daimon projects` lists what exists"])
            return 1
        render.render_brief_note([f"cross-project briefing — project: {slug}"])
        # A named bucket is somebody else's listing: no `daimon loops` pointer.
        return _render_briefing_body(annotated, slug,
                                     drift_project=None, teammates=None,
                                     loops_pointer=False)
    # Route like status/serialize: --project, else DAIMON_PROJECT_DIR, else cwd.
    # read_latest still falls back to the global pointer if the project has none.
    project = _cli._resolve_project(args.project)
    # #787/#795: whether the fallback fired is what the read DID, not what the
    # filesystem shows — the route fact is READ off the result (`fell_back`,
    # scar 0058's class), never reconstructed from a second look. Under
    # Admit.ANY nothing is ever refused, so fell_back=True implies a
    # checkpoint exists; and brief cannot be identity-less, because
    # _resolve_project returns str(Path(...).resolve()) — never empty — and
    # resolve_project_root ends `return top or raw`.
    # #365: worldcheck never probes a fallback body (the global pointer may
    # belong to ANOTHER project and `gh` resolves against THIS cwd's repo);
    # `prepare` enforces that from `fell_back`.
    annotated = _prepared(project, store.Route.OWN_ELSE_GLOBAL,
                          worldcheck_project=project)
    if annotated is None:
        return 2
    checkpoint = annotated.checkpoint
    fallback_used = annotated.fell_back
    if fallback_used and not (getattr(args, "global_fallback", False)
                              or config.brief_global_fallback()):
        # Header-only fallback (#96): the foreign body is suppressed — one
        # warning line above a hundred foreign lines does not read as a
        # warning. Orient (where the activity actually is) and exit clean;
        # `daimon status` still shows the full pointer table.
        # `checkpoint` is a dict here: see the Admit.ANY reasoning above.
        slug = str(checkpoint.get("project_slug") or "").strip() or "another project"  # type: ignore[union-attr]
        epoch = store._created_epoch(checkpoint.get("created"))  # type: ignore[union-attr]
        age = f"{_format_age(time.time() - epoch)} ago" if epoch else "age unknown"
        # #740: a baton left for a checkpoint-less project is the only
        # orientation it has — status says "waiting baton"; brief must not
        # swallow it on the header-only path. Read-only: consumption stays
        # serialize-count-based in store.active_handoff.
        render.render_handoff(store.active_handoff(project))
        render.render_brief_note(list(annotated.notes))
        render.render_brief_line([
            "No briefing for this project yet — the first serialized session "
            "will create one.",
            f"(Most recent activity elsewhere: {slug}, {age}.)",
            "Use --global-fallback or DAIMON_BRIEF_GLOBAL_FALLBACK=full to "
            "view that checkpoint here.",
        ])
        # #223: the foreign body is suppressed above, but --team still means
        # --team — a fresh project with no checkpoint of its own is exactly
        # the new-teammate case where reading the team's briefings matters
        # most. Empty team -> render_teammates no-ops, so a team-less
        # machine's output stays byte-identical to today.
        if getattr(args, "team", False):
            counts = _TeamCounts()
            render.render_teammates(_team_briefings(project, counts))
            if counts.resolved:
                render.render_brief_line([
                    f"{counts.resolved} resolved item(s) withheld "
                    "(a teammate's)"])
            if counts.quarantined:
                render.render_brief_line([
                    f"{counts.quarantined} quarantined item(s) withheld "
                    "(a teammate's)"])
        return 0
    # Label the global-pointer fallback (#29): status calls the same situation
    # "global checkpoint (fallback)"; brief must not present another project's
    # state as this project's without saying so.
    if fallback_used:
        render.render_brief_note(["no checkpoint for this project — showing the global "
                                  "checkpoint (fallback), possibly another project's."])
    # #534: a LIVE serialize for this project means a fresher checkpoint is
    # being written right now — say so instead of silently briefing one
    # session behind (measured at 10% of runs on one field machine). Keyed on
    # the ledger's liveness bar, never heartbeat existence: a stuck or
    # crashed serialize is heal's case, and a permanent false staleness line
    # would be worse than the silence this fixes.
    if ledger.serialize_in_flight(store.project_slug(project) or ""):
        render.render_brief_note([
            "a serialize is in flight — this briefing may be one session "
            "behind; re-run `daimon brief` in a few minutes for the fresh one."])
    # --team (#111): fan in teammates for THIS project. Empty team → None → the
    # renderer emits no Teammates section, byte-identical to a non-team briefing.
    team_counts = _TeamCounts()
    teammates = (_team_briefings(project, team_counts)
                 if getattr(args, "team", False) else None)
    return _render_briefing_body(annotated, project,
                                 drift_project=project, teammates=teammates,
                                 worldcheck_project=None if fallback_used
                                 else project, team_counts=team_counts,
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
