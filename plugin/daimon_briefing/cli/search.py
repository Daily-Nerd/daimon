"""`daimon recall`, `why` and `serve` (moved out of cli/__init__.py, #1132 PR 5).

Named `search` because `cli.recall` is the library module the CLI reaches
through its namespace (tests patch `cli.recall.search`). Every name is
re-exported from `cli`.
"""

import json
import sys
import time

import daimon_briefing.cli as _cli

from .. import config, inspector, recall, recall_telemetry, render, store
from ..ledger import _format_age


def _cmd_recall(args) -> int:
    """Lexical search over the derived recall index. The index is disposable —
    recall.search auto-(re)builds it — so the only hard failure surfaced here is
    an FTS5-less sqlite3 (rc 1, named); everything else degrades to no matches."""
    _cli._note_usage("recall")
    query = " ".join(args.query)
    if args.limit < 1:
        print(f"error: --limit must be >= 1 (got {args.limit})", file=sys.stderr)
        return 2
    slug = getattr(args, "slug", None)
    if slug and args.project:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return 2
    if slug and args.all_projects:
        print("error: --slug scopes to one project; drop it or drop "
              "--all-projects", file=sys.stderr)
        return 2
    if _cli._refuses_caller_scope(slug, args.all_projects):
        return 2
    project = _cli._resolve_project(args.project)
    try:
        results = recall.search(query, project_dir=project, slug=slug,
                                all_projects=args.all_projects, limit=args.limit)
    except recall.RecallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    recall_telemetry.record(
        results,
        query_terms=recall.salient_terms(query),
        surface="recall-search",
        via="cli",
    )
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        # #259: a zero-match SCOPED search is a signpost, not a dead end —
        # rerun the same query unscoped (one extra FTS query, same index)
        # and report COUNTS by project, never content: crossing projects
        # stays user-invoked (#94/#95), the system just stops hiding that
        # crossing would pay. Explicit scopes (--all-projects already
        # searched everything; --slug named its target) get no second-guess.
        # Same doctrine as the AND->OR retry (#25): a narrower scope must
        # never mean a silent dead end. #899: except on a tenant-scoped
        # home, where per-slug counts are enumeration and the remedy the
        # signpost names is the refused flag.
        if not args.all_projects and not slug and not config.tenant_scoped():
            try:
                wide = recall.search(query, all_projects=True, limit=50)
            except recall.RecallError:
                wide = []
            counts: dict = {}
            here = store.project_slug(project)
            for r in wide:
                s = r.get("project_slug")
                if s and s != here:
                    counts[s] = counts.get(s, 0) + 1
            if counts:
                lines = [f"no matches in this project — "
                         f"{sum(counts.values())} match(es) elsewhere:"]
                lines += [f"  {s} ({n})" for s, n in
                          sorted(counts.items(), key=lambda kv: -kv[1])]
                lines.append("rerun with --all-projects, or --slug <slug> "
                             "for one project")
                render.render_recall_lines(lines)
                return 0
        render.render_recall_lines(["no matches"])
        return 0
    now = time.time()
    lines = []
    for r in results:
        age = _format_age(now - r["created"]) if r.get("created") else "?"
        # #865: name the WRITER, not just the value. A model-authored
        # supersedes link and a human `daimon resolve` both land here and
        # both can write a bare id, so the marker rendered a claim and an
        # action identically. `resolved` is the one value that was already
        # unambiguous, and only by accident of its spelling.
        # #1079: the phrase itself lives in recall.describe_supersession —
        # the MCP `daimon_recall` tool shares this exact wording.
        sup_phrase = recall.describe_supersession(r)
        superseded = f" [{sup_phrase}]" if sup_phrase else ""
        # #837: an independent axis gets an independent marker — a row can
        # carry both, and collapsing them would hide one fact behind the
        # other. recall owns the phrasing so this marker can never describe a
        # different view than the fold recorded.
        inv = recall.describe_invalidation(r.get("invalidated_by"))
        contradicted = f" [{inv}]" if inv else ""
        # #866: the release from burial is a fact too. A cured item read
        # exactly like one nothing ever questioned, which is the same
        # collapse the contradiction marker exists to prevent, inverted.
        cured = recall.describe_cure(r.get("cured_by"))
        contradicted += f" [{cured}]" if cured else ""
        trust = r.get("trust") or "untagged"
        item_id = f" [{r['item_id']}]" if r.get("item_id") else ""
        # #889: [author] reads like attribution and is not — config.author()
        # is a machine identity, one constant across every project here. The
        # row has always known its origin project; only --json ever showed it,
        # so a foreign hit and a local one rendered identically.
        scope = recall.describe_scope(r, store.project_slug(project))
        scope_mark = f" ({scope})" if scope else ""
        # #890: whose STATEMENT this is, when the record names one. Absent
        # means unknown and renders as nothing — never a guess and never a
        # placeholder, because a default would read as the reader's own claim.
        stated = str(r.get("stated_by") or "").strip()
        stated_mark = f" (stated by {stated})" if stated else ""
        lines.append(f"[{r['author']}] [{trust}] [{r['kind']}]{item_id} {r['text']} "
                     f"({r['session_id']}, {age} ago){stated_mark}{scope_mark}"
                     f"{superseded}{contradicted}")
    render.render_recall_lines(lines)
    return 0


def _cmd_why(args) -> int:
    """Render one project-scoped, read-side trust receipt (#502)."""
    _cli._note_usage("why")
    if args.slug and args.project:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return 2
    if not inspector.valid_item_id(args.item_id):
        print("error: invalid item id — expected "
              "[a-z]-[0-9a-f]{6,40}(-N)?", file=sys.stderr)
        return 2
    if _cli._refuses_caller_scope(args.slug):
        return 2
    project = args.slug or _cli._resolve_project(args.project)
    result = inspector.inspect_item(
        project, args.item_id, include_source=args.source)
    if result is None:
        print(f"no item {args.item_id!r} in this project", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        render.render_recall_lines(inspector.human_lines(result))
    return 0


def _cmd_serve(args) -> int:
    """Front door to the read-only viewer (#670). Delegates to daimon_ui's own
    argv path so there is exactly one config surface — the CLI never grows its
    own copy of the flag handling."""
    _cli._note_usage("serve")
    import daimon_ui.__main__ as ui_main
    argv = []
    if args.data_dir:
        argv += ["--data-dir", args.data_dir]
    if args.project_dir:
        argv += ["--project-dir", args.project_dir]
    if args.port is not None:
        argv += ["--port", str(args.port)]
    if args.no_browser:
        argv.append("--no-browser")
    return ui_main.main(argv) or 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_recall = sub.add_parser(
        "recall", help="search local + team checkpoint history (FTS5)",
        epilog="Examples:\n"
               "  daimon recall auth caching\n"
               "  daimon recall gateway --all-projects --json\n",
    )
    p_recall.add_argument(
        "query", nargs="+",
        help="search terms (matched as words against item text and quotes)",
    )
    p_recall.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_recall.add_argument(
        "--all-projects", action="store_true",
        help="search across every project (lifts the project scope)",
    )
    p_recall.add_argument(
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`) — "
             "reaches buckets whose source path no longer exists (#243)",
    )
    p_recall.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_recall.add_argument(
        "--limit", type=int, default=20, help="max results (default: 20)"
    )
    p_recall.set_defaults(func=_cmd_recall)

    p_why = sub.add_parser(
        "why", help="inspect the evidence and lifecycle receipt for one item",
        epilog="Examples:\n"
               "  daimon recall retry policy\n"
               "  daimon why o-3f8a2c\n"
               "  daimon why o-3f8a2c --source --json\n",
    )
    p_why.add_argument(
        "item_id", help="exact item id shown by `daimon recall` or `daimon loops`")
    p_why.add_argument(
        "--source", action="store_true",
        help="show one bounded, redacted message-level source window")
    p_why.add_argument(
        "--json", action="store_true", help="machine-readable evidence axes")
    p_why.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)")
    p_why.add_argument(
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`)")
    p_why.set_defaults(func=_cmd_why)


def register_serve(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_serve = sub.add_parser(
        "serve",
        help="serve the read-only local viewer (localhost only)",
        description="Serve the read-only local viewer on localhost. Every "
                    "surface renders an existing engine's output; nothing "
                    "writes.",
        epilog="Examples:\n"
               "  daimon serve\n"
               "  daimon serve --port 7800 --no-browser\n",
    )
    p_serve.add_argument(
        "--data-dir", default=None,
        help="checkpoint dir (default: DAIMON_CHECKPOINT_DIR, then ~/.daimon/checkpoints)")
    p_serve.add_argument(
        "--project-dir", default=None,
        help="project directory to scope to (default: cwd)")
    p_serve.add_argument(
        "--port", type=int, default=None, help="port to bind (default: 7717)")
    p_serve.add_argument(
        "--no-browser", action="store_true", help="don't open a browser tab")
    p_serve.set_defaults(func=_cmd_serve)
