"""`daimon ruling` verbs — standing rulings, the ledger's positive polarity
(#693).

Pure move out of the cli monolith (#708). Shared helpers that remain in the
package `__init__` are reached through the module object (`_cli.<name>`) so
the `cli.<name>` seam tests and hosts patch keeps working on moved code.
"""

import argparse
import json
import sys

import daimon_briefing.cli as _cli

from .. import (briefing, buckets, checks, config, normalize, refutations,
                render, store)
from ._ledger import (
    _check_args,
    _check_ceremony_lines,
    _policy_args,
    _policy_ceremony_lines,
    _warn_check_sync,
    _print_ruling,
    _refusal_message,
    _refutation_json,
    _report_vanished_write,
    _refute_channel,
    _ruling_lines,
)


def _cmd_ruling_propose(args) -> int:
    project = _cli._resolve_project(args.project)
    try:
        check = _check_args(args)
        request_policy = _policy_args(args)
        ruling_id = refutations.assert_ruling(
            subject=args.subject, verdict=args.verdict, scope=args.scope,
            evidence=args.evidence, channel=_refute_channel(args),
            anchors=args.anchor,
            revisit_when=args.revisit_when or "", ratified=args.ratify,
            check=check, request_policy=request_policy, project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not recorded", exc))
        return 1
    record = refutations.get(ruling_id, project_dir=project)
    _cli._note_usage("ruling:propose")
    if record is None:
        _report_vanished_write(ruling_id, "propose", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record["state"] == "candidate":
            if getattr(args, "by", None) != "agent":
                render.render_ledger_lines(
                    [f"  Next: daimon ruling ratify {ruling_id} "
                     f"--project {project}"])
            else:
                render.render_ledger_lines(
                    ["  Candidate recorded. Activation requires an explicit "
                     "human decision."])
    return 0


def _cmd_ruling_ratify(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not ratified", exc))
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute ratify`")
        return 1
    if channel != "cli-tty":
        # Never invoke a writer to harvest its error string.
        print("ruling not ratified: ratification requires a human channel; "
              f"this call arrived through {channel!r}")
        return 1
    # Ratification is a signature, not an id-typing exercise: print the FULL
    # text, disclose the render consequence, and bind the append to the key
    # of the text DISPLAYED — the fold refuses to activate any other text.
    # Under --json the ceremony goes to stderr so stdout stays parseable.
    ceremony = sys.stderr if args.json else sys.stdout
    print("About to ratify this ruling:", file=ceremony)
    if not args.json:
        _print_ruling(record, detailed=True)
    else:
        print(f"  {record.get('verdict', '')}", file=ceremony)
    print("  This text will render into every future session for this "
          "project.", file=ceremony)
    check = record.get("check") if isinstance(record.get("check"), dict) else None
    displayed_check_sha = ""
    if check:
        displayed_check_sha = str(check.get("sha256") or "")
        for line in _check_ceremony_lines(check, label="Check",
                                          verb="Ratifying"):
            print(line, file=ceremony)
    # #961 slice 4: same pin discipline as `check` just above — the ceremony
    # discloses the grant it is about to ratify and pins the append to the
    # hash it displayed.
    request_policy = (record.get("request_policy")
                      if isinstance(record.get("request_policy"), dict)
                      else None)
    displayed_policy_sha = ""
    if request_policy:
        displayed_policy_sha = str(request_policy.get("sha256") or "")
        for line in _policy_ceremony_lines(request_policy, verb="Ratifying",
                                           label="Policy"):
            print(line, file=ceremony)
    displayed_key = normalize.content_key(record.get("verdict") or "")
    answer = input("Ratify? [y/N]: ").strip().casefold()
    if answer not in ("y", "yes"):
        print("not ratified")
        return 1
    try:
        refutations.ratify(args.ruling_id, channel=channel,
                           note=args.note or "", verdict_key=displayed_key,
                           check_sha256=displayed_check_sha,
                           policy_sha256=displayed_policy_sha,
                           project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not ratified", exc))
        return 1
    _warn_check_sync()
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:ratify")
    if record is None:
        _report_vanished_write(args.ruling_id, "ratify", as_json=args.json)
        return 0
    if record["state"] != "active":
        print("not activated: the text changed during confirmation; "
              "re-run to review the current text")
        return 1
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
    return 0


def _cmd_ruling_revise(args) -> int:
    project = _cli._resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute revise`")
        return 1
    if args.ratify:
        # The ratification ceremony (full text, disclosure, confirm, key
        # binding) lives in ONE place; a revise flag walking around it would
        # activate text the human was never shown.
        print("ruling revise does not activate; revise the candidate, then "
              f"run `daimon ruling ratify {args.ruling_id}`")
        return 1
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not revised", exc))
        return 1
    try:
        check = _check_args(args)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not revised", exc))
        return 1
    try:
        request_policy = _policy_args(args)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not revised", exc))
        return 1
    clear_request_policy = bool(getattr(args, "no_request_policy", False))
    if request_policy is not None and clear_request_policy:
        print(_refusal_message(
            "ruling not revised", refutations.RefutationError(
                "--request-policy and --no-request-policy are mutually "
                "exclusive")))
        return 1
    if (record["state"] == "active" and channel == "cli-tty"
            and (args.verdict is not None or args.subject is not None
                 or check is not None or request_policy is not None
                 or clear_request_policy)):
        # Rewriting what renders is the same power ratification has, and it
        # earns trust the same way: show the change, disclose, confirm.
        print("About to change the ACTIVE text of this ruling:")
        _print_ruling(record, detailed=True)
        if args.verdict is not None:
            print(f"  New text: {args.verdict}")
        if args.subject is not None:
            print(f"  New governs: {args.subject}")
        if check is not None:
            for line in _check_ceremony_lines(check, label="New check",
                                              verb="Applying"):
                print(line)
        if request_policy is not None:
            # #961 slice 4 review round 2 (M5b): validated (never written)
            # here so the ceremony can disclose the SAME sha the write, if
            # confirmed, will pin — the pin discipline ratify's own
            # ceremony already applies to an already-active policy, now
            # applied before the fact too, on the same terms
            # `_check_ceremony_lines` already hashes a check's raw body for
            # display before a check has ever been stored. A value that
            # fails validation falls back to the raw KEY=VALUE terms it was
            # given in — `revise` itself refuses it at the write boundary
            # with the real reason, and a hash for a shape that will never
            # be stored would disclose a fact that never comes to exist.
            try:
                validated_policy = refutations._policy(request_policy)
            except refutations.RefutationError:
                validated_policy = None
            if validated_policy is None:
                # `request_policy` is not None here, and `_policy` returns
                # None only for a None input — so this is either the
                # RefutationError fallback above (a value that will not be
                # stored) or, in principle, unreachable; either way the raw
                # KEY=VALUE terms are the honest thing to show.
                print(f"  New policy: {request_policy}")
            else:
                for line in _policy_ceremony_lines(
                        validated_policy, verb="Applying",
                        label="New policy"):
                    print(line)
        if clear_request_policy:
            print("  Policy will be CLEARED — no ruling will cover an "
                  "agent accept for this project after this change.")
        print("  This text will render into every future session for this "
              "project.")
        answer = input("Apply? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("not revised")
            return 1
    anchors = args.anchor if args.anchor is not None else None
    try:
        refutations.revise(
            args.ruling_id, channel=channel,
            evidence=args.evidence, subject=args.subject,
            verdict=args.verdict, scope=args.scope,
            anchors=anchors, revisit_when=args.revisit_when,
            ratified=False, check=check, request_policy=request_policy,
            clear_request_policy=clear_request_policy, project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not revised", exc))
        return 1
    _warn_check_sync()
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:revise")
    if record is None:
        _report_vanished_write(args.ruling_id, "revise", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record.get("revision_proposed"):
            render.render_ledger_lines(
                ["  Proposal recorded; the active ruling is untouched "
                 "until a human verdict."])
        elif record["state"] == "candidate":
            render.render_ledger_lines(
                ["  Revision is not load-bearing until explicit human "
                 "ratification."])
    return 0


def _cmd_ruling_retire(args) -> int:
    project, rc = _cli._slug_route(args)
    if rc:
        return rc
    try:
        event = refutations.retire(
            args.ruling_id, channel=_refute_channel(args),
            evidence=args.evidence or (),
            note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(_refusal_message("ruling not retired", exc))
        return 1
    _warn_check_sync()
    record = refutations.get(args.ruling_id, project_dir=project)
    _cli._note_usage("ruling:retire")
    if record is None:
        _report_vanished_write(args.ruling_id, "retire", as_json=args.json)
        return 0
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if event == "overturn-proposed":
            render.render_ledger_lines(
                ["  Retirement proposed; the ruling stands until a human "
                 "verdict."])
    return 0


def _cmd_ruling_list(args) -> int:
    project = _cli._resolve_project(args.project)
    # #969: ask the shared reader for its STATE before asking `listing` for
    # rows. `listing` reaches `_path` through `records` and `fold` with no
    # guard of its own, so on a config fault (a bad byte in ~/.daimon/env,
    # an unexpandable DAIMON_CHECKPOINT_DIR) it raised several statements
    # before the `unresolved` branch below could name the fault. The library
    # keeps raising for in-process callers, who have `rulings_read` for the
    # honest answer; the CLI, which shares the reader's vocabulary, consults
    # it first. One probe, reused by every branch below. `unresolved` returns
    # HERE, before the usage counter or anything else that reads config: the
    # fault is in config itself (a corrupt env file raises on every read), so
    # every later config touch would die the same way. stdout keeps its
    # empty-ledger shape, the way `no-bucket` and `unreadable` keep theirs.
    read = briefing.rulings_read(project)
    if read.state == "unresolved":
        # No `path` to name — resolution never got that far, so this line
        # never interpolates one.
        print(f"cannot resolve a ledger path for {project}: check "
              f"DAIMON_CHECKPOINT_DIR and ~/.daimon/env for a bad value",
              file=sys.stderr)
        if args.json:
            print(_refutation_json([]))
        else:
            render.render_ledger_lines(["no rulings for this project"])
        return 1
    rows = refutations.listing(states=set(args.state or refutations.STATES),
                               polarity="ruling", project_dir=project)
    _cli._note_usage("ruling:list")
    # #948: an empty answer for a project that has never been written from is
    # not the same fact as an empty ledger, and it is what a path routed to
    # the wrong bucket looks like. stdout keeps its exact shape so parsers are
    # unaffected; the distinction rides on stderr and the exit code, the way
    # `daimon status` already reports "no checkpoint here".
    #
    # #962: two more causes read the same as an empty ledger. The bucket can
    # be there with refutations.jsonl unreadable (permissions, a symlink
    # loop, or replaced by a directory), or the ledger path itself can never
    # get resolved at all (a config problem, not a project problem — see
    # `briefing.rulings_read`). `rulings_read` is the shared reader that
    # already tells all four states apart, so it settles every branch here
    # from one probe instead of this command reaching for `bucket_exists` on
    # its own (a second, independent read of the same fact is exactly the
    # shape a TOCTOU takes).
    rc = 0
    if not rows:
        if read.state == "unreadable":
            print(f"cannot read the ledger for {store.project_slug(project)} "
                  f"(resolved {project}): {read.path} exists but could not "
                  f"be read", file=sys.stderr)
            rc = 1
        elif read.state == "no-bucket":
            # #963: the same empty answer has a second cause worth naming — a
            # bucket written from this path before 0.42.0, under the
            # literal-path slug, still holding the rulings. Appended to the
            # SAME stderr line; stdout and the exit code are untouched (scar
            # 0057, the #948 decision), so a parser sees exactly what it saw
            # before.
            raw = _cli._raw_project(args.project)
            legacy = buckets.legacy_bucket(raw)
            hint = (f"; a legacy bucket {legacy} exists, "
                    f"{_cli._migrate_command(raw)}") if legacy else ""
            print(f"no bucket for {store.project_slug(project)} yet (resolved "
                  f"{project}): nothing has been written from this project{hint}",
                  file=sys.stderr)
            rc = 1
    if args.json:
        print(_refutation_json(rows))
        active_j = sum(1 for r in rows if r.get("state") == "active")
        cap_j = config.ruling_cap()
        if active_j > cap_j:
            # stderr so the JSON stays parseable and machines still learn it.
            print(f"over cap: {active_j} active vs cap {cap_j}",
                  file=sys.stderr)
        return rc
    if not rows:
        render.render_ledger_lines(["no rulings for this project"])
        return rc
    render.render_ledger_records([_ruling_lines(row) for row in rows])
    # The cap binds ACTIVATION; a lowered DAIMON_RULING_CAP leaves the
    # excess active, so the over-cap state must be visible somewhere.
    active = sum(1 for r in rows if r.get("state") == "active")
    cap = config.ruling_cap()
    if active > cap:
        render.render_ledger_lines(
            [f"over cap: {active} active vs cap {cap} — retire one, or "
             "raise DAIMON_RULING_CAP deliberately"])
    return 0


def _cmd_ruling_show(args) -> int:
    project = _cli._resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute show`")
        return 1
    _cli._note_usage("ruling:show")
    if args.json:
        print(_refutation_json(record))
    else:
        # #943 slice 5: the record is what `show` owes; liveness is an extra,
        # so a firing log that cannot be folded drops the line rather than
        # the answer.
        try:
            firing = checks.firing_summary(project)
        except Exception:  # noqa: BLE001
            firing = None
        _print_ruling(record, detailed=True, firing=firing)
    return 0


def _checks_payload(project) -> dict:
    """The `ruling checks` answer, once, for both the table and `--json`.

    One row per (ruling that carries a check) x (host profile). The CLI has
    no notion of which host it is running on — there is no `config.host()` —
    so the hosts are ENUMERATED from `checks_host.PROFILES` rather than
    guessed. A host left out is a column an author would never see.

    Scoping is by this project's ledger, and the ids that come out of it are
    the only ids the firing summary and the audit are allowed to contribute
    (scar 0055: the manifest and the log are both global, and rendering
    another bucket's ids writes them into this project's checkpoint).

    Reading `check_lifecycle` here makes a fourth consumer of the field
    (scar 0053). The other three are `_ruling_lines`, `checks._sync` and the
    viewer payload; none of them changes behavior for this one, and this
    reader adds no new derivation — it renders the value the fold already
    computed."""
    from .. import checks_host

    summary = checks.firing_summary(project)
    audit = checks.audit(project)
    rows = []
    for record in refutations.listing(polarity="ruling", project_dir=project):
        check = record.get("check")
        lifecycle = record.get("check_lifecycle")
        if not isinstance(check, dict) or not lifecycle:
            continue
        ruling_id = record["refutation_id"]
        intent = str(check.get("intent") or "warn")
        for host, profile in checks_host.PROFILES.items():
            mode = checks_host.mode_for(profile, intent)
            # No liveness cell where nothing could have fired. A `never
            # fired` on a disarmed check or an unsupported host reports a
            # wiring that does not exist, which is the opposite of what this
            # table is for.
            # A log daimon could not read leaves every cell blank too: the
            # header says why, and a `never fired` here would be a claim
            # this read cannot support.
            live = (lifecycle == "armed" and mode != "unsupported"
                    and summary.log_state != "unreadable")
            fold = summary.rulings.get((ruling_id, host)) if live else None
            # Counts are null wherever the table prints none, including the
            # never-fired row: a consumer reading `clean` without checking
            # `last_fired` would get the "0 clean" reading constraint 2
            # exists to prevent. `lifecycle` and `mode` still tell a
            # never-fired row from one with no liveness cell at all.
            # Bound once, so the "has it fired" test and the four values
            # that depend on it cannot answer differently.
            seen = fold if fold and fold["last_ts"] else None
            rows.append({
                "ruling_id": ruling_id, "lifecycle": lifecycle,
                "intent": intent, "host": host, "mode": mode,
                # Tri-state, and the renderer's only input: None means the
                # row has no liveness cell at all, False means it has one
                # and nothing has fired, True means the counts are real.
                "fired": (seen is not None) if live else None,
                "last_fired": seen["last_ts"] if seen else None,
                "clean": seen["clean"] if seen else None,
                "violation": seen["violation"] if seen else None,
                "unresolved": seen["unresolved"] if seen else None,
            })
    return {"rows": rows, "manifest": audit._asdict(),
            "hosts": summary.hook_seen,
            "log": {"state": summary.log_state, "path": summary.path},
            # #955: where the counts start. Appended, because key order is
            # part of the --json contract.
            "window_since": summary.window_since}


def _cmd_ruling_checks(args) -> int:
    """#943 slice 5: what is armed, what mode each host gives it, and whether
    it ever fired. Read-only, agent-callable, exit 0 even when empty — a
    reporting read never refuses to signal a state."""
    project = _cli._resolve_project(args.project)
    payload = _checks_payload(project)
    _cli._note_usage("ruling:checks")
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    render.render_ledger_lines(render.checks_table_lines(payload))
    return 0


def _cmd_ruling_check_try(args) -> int:
    """#943: run a ruling's check against a command, arming nothing.

    Exit codes are the auditors' three: 0 clean, 1 violation, 3 could not be
    proven either way. Unresolved never collapses into 0, which would read as
    clean, or into 1, which would report a violation the check never found.
    """
    project = _cli._resolve_project(args.project)
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(_refusal_message("check not run", exc))
        return 1
    if channel != "cli-tty":
        # Never invoke the runner to harvest its error string, and never
        # execute a body to discover the caller was not allowed to ask.
        print("check not run: a dry run executes the check body, so it "
              f"requires a human channel; this call arrived through {channel!r}")
        return 1
    try:
        outcome = checks.try_run(
            args.ruling_id, args.command, channel=channel, cwd=args.cwd,
            project_dir=project, proposed=args.proposed)
    except refutations.RefutationError as exc:
        print(_refusal_message("check not run", exc))
        return 1
    lines = [f"  outcome: {outcome.outcome}"]
    if outcome.cause:
        lines.append(f"  cause: {outcome.cause}")
    if outcome.reason:
        lines.append(f"  reason: {outcome.reason}")
    lines.append(f"  duration: {outcome.duration_ms} ms")
    lines.append("  Nothing was armed and nothing was logged; this was a "
                 "rehearsal.")
    render.render_ledger_lines(lines)
    _cli._note_usage("ruling:check-try")
    return {"clean": 0, "violation": 1}.get(outcome.outcome, 3)


def register(sub, fmt) -> None:
    """Register the `ruling` parser family on the top-level subparsers."""
    p_ruling = sub.add_parser(
        "ruling",
        help="standing rulings: human-ratified records that never decay (#693)",
        epilog="Examples:\n"
               "  daimon ruling propose --subject 'public posts' "
               "--verdict 'internal numbers never appear in public posts' "
               "--scope publishing --evidence issue:693 --by agent\n"
               "  daimon ruling ratify r-1a2b3c4d5e6f\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ruling_sub = p_ruling.add_subparsers(dest="ruling_cmd", required=True)

    rl_propose = ruling_sub.add_parser(
        "propose", help="propose a standing ruling; agent proposals stay "
                        "candidates until a human ratifies")
    rl_propose.add_argument("--subject", required=True,
                            help="what the ruling governs")
    rl_propose.add_argument("--verdict", required=True,
                            help="the ruling text itself (max 280 chars)")
    rl_propose.add_argument("--scope", required=True,
                            help="where it applies")
    rl_propose.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="cited source behind the ruling; recorded verbatim, not "
             "resolved or verified; repeatable")
    rl_propose.add_argument(
        "--anchor", action="append", default=[], metavar="ANCHOR",
        help="stable exact-match key such as issue:693; repeatable")
    rl_propose.add_argument(
        "--revisit-when", help="condition that makes reconsideration legitimate")
    rl_propose.add_argument("--by", choices=["agent"], default=None,
                            help="declare yourself an agent; omit it only "
                                 "from an interactive terminal")
    rl_propose.add_argument(
        "--ratify", action="store_true",
        help="activate immediately; valid only on the human path")
    rl_propose.add_argument(
        "--check-body-file", metavar="PATH",
        help="file whose contents become the ruling's check script; the "
             "bytes are stored, the path is not (#943)")
    rl_propose.add_argument(
        "--check-match", metavar="REGEX",
        help="regex on the command string that selects the actions the "
             "check runs before")
    rl_propose.add_argument(
        "--check-intent", choices=sorted(refutations.CHECK_INTENTS),
        default=None,
        help="what the check asks each host for; the host delivers the "
             "strongest it supports (default warn)")
    rl_propose.add_argument(
        "--request-policy", action="append", default=None,
        metavar="KEY=VALUE",
        help="grant this ruling authorizes: sender=<slug> kind=work|info "
             "verb=accept by=agent; repeatable, all four required (#961)")
    rl_propose.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_propose.add_argument("--json", action="store_true", help="machine-readable output")
    rl_propose.set_defaults(func=_cli._cmd_ruling_propose)

    rl_ratify = ruling_sub.add_parser(
        "ratify", help="activate a candidate ruling; prints the full text "
                       "and confirms before the append")
    rl_ratify.add_argument("ruling_id", help="exact r-… id")
    rl_ratify.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; ratification "
                                "then refuses")
    rl_ratify.add_argument("--note", help="optional ratification rationale")
    rl_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_ratify.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    rl_ratify.add_argument("--json", action="store_true", help="machine-readable output")
    rl_ratify.set_defaults(func=_cli._cmd_ruling_ratify)

    rl_revise = ruling_sub.add_parser(
        "revise", help="revise a ruling; on an active ruling an agent call "
                       "records a proposal and the text stands")
    rl_revise.add_argument("ruling_id", help="exact r-… id")
    rl_revise.add_argument("--subject", help="replacement subject")
    rl_revise.add_argument("--verdict", help="replacement ruling text")
    rl_revise.add_argument("--scope", help="replacement scope")
    rl_revise.add_argument(
        "--anchor", action="append", default=None, metavar="ANCHOR",
        help="replacement anchor set; repeatable")
    rl_revise.add_argument("--revisit-when", help="replacement revisit condition")
    rl_revise.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="source cited for the revision; recorded, not verified; repeatable")
    rl_revise.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; on an active ruling the call then "
             "records a proposal and the text stands")
    rl_revise.add_argument(
        "--ratify", action="store_true",
        help="refused: activation goes through `daimon ruling ratify`, "
             "which shows the text before the write")
    rl_revise.add_argument(
        "--check-body-file", metavar="PATH",
        help="file whose contents become the ruling's check script; the "
             "bytes are stored, the path is not (#943)")
    rl_revise.add_argument(
        "--check-match", metavar="REGEX",
        help="regex on the command string that selects the actions the "
             "check runs before")
    rl_revise.add_argument(
        "--check-intent", choices=sorted(refutations.CHECK_INTENTS),
        default=None,
        help="what the check asks each host for; the host delivers the "
             "strongest it supports (default warn)")
    rl_revise.add_argument(
        "--request-policy", action="append", default=None,
        metavar="KEY=VALUE",
        help="replacement grant: sender=<slug> kind=work|info verb=accept "
             "by=agent; repeatable, all four required (#961)")
    rl_revise.add_argument(
        "--no-request-policy", action="store_true",
        help="clear this ruling's request_policy; mutually exclusive with "
             "--request-policy")
    rl_revise.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_revise.add_argument("--json", action="store_true", help="machine-readable output")
    rl_revise.set_defaults(func=_cli._cmd_ruling_revise)

    rl_retire = ruling_sub.add_parser(
        "retire", help="end a ruling; agent calls propose, humans retire "
                       "directly; evidence optional")
    rl_retire.add_argument("ruling_id", help="exact r-… id")
    rl_retire.add_argument(
        "--evidence", action="append", default=None, metavar="SOURCE",
        help="optional source for why the ruling stopped applying; repeatable")
    rl_retire.add_argument("--note", help="optional explanation")
    rl_retire.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; the call then records a retirement "
             "proposal and the ruling stands until a human verdict")
    rl_retire.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_retire.add_argument("--slug", metavar="SLUG", help=_cli.SLUG_ROUTE_HELP)
    rl_retire.add_argument("--json", action="store_true", help="machine-readable output")
    rl_retire.set_defaults(func=_cli._cmd_ruling_retire)

    rl_check = ruling_sub.add_parser(
        "check", help="the check a ruling carries (#943): try it before a "
                      "human arms it")
    check_sub = rl_check.add_subparsers(dest="ruling_check_cmd", required=True)
    rc_try = check_sub.add_parser(
        "try", help="run this ruling's check against a command and print the "
                    "outcome; arms nothing, logs nothing")
    rc_try.add_argument("ruling_id", help="exact r-... id")
    rc_try.add_argument("--command", required=True, metavar="CMD",
                        help="the command string to check, as a host would "
                             "hand it to the hook")
    rc_try.add_argument("--cwd", metavar="DIR",
                        help="working directory the command would run in; "
                             "relative file arguments resolve against it "
                             "(default: the current directory)")
    rc_try.add_argument(
        "--proposed", action="store_true",
        help="run the pending revision's check instead of the armed one")
    rc_try.add_argument("--by", choices=["agent"], default=None,
                        help="declare yourself an agent; a dry run then "
                             "refuses, because it executes the body")
    rc_try.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rc_try.set_defaults(func=_cli._cmd_ruling_check_try)

    rl_checks = ruling_sub.add_parser(
        "checks", help="what this project has armed, the mode each host "
                       "gives it, and whether it ever fired (#943)")
    rl_checks.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_checks.add_argument("--json", action="store_true", help="machine-readable output")
    # No `--slug`: this is a read, but it prints ruling ids, and routing a
    # read to a bucket the caller only names would put another project's ids
    # into this session's transcript (scar 0055, #899, #948).
    rl_checks.set_defaults(func=_cli._cmd_ruling_checks)

    rl_list = ruling_sub.add_parser("list", help="list project rulings")
    rl_list.add_argument("--state", action="append",
                         choices=sorted(refutations.STATES),
                         help="filter by state; repeatable")
    rl_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_list.add_argument("--json", action="store_true", help="machine-readable output")
    rl_list.set_defaults(func=_cli._cmd_ruling_list)

    rl_show = ruling_sub.add_parser("show", help="show one ruling, including "
                                                 "pending proposals")
    rl_show.add_argument("ruling_id", help="exact r-… id")
    rl_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_show.add_argument("--json", action="store_true", help="machine-readable output")
    rl_show.set_defaults(func=_cli._cmd_ruling_show)
