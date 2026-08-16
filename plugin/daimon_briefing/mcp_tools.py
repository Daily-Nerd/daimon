"""#261: MCP tool handlers — thin shims over the existing library.

Each handler takes the tools/call `arguments` dict and returns the payload
TEXT for content[0]. Tool-level failures raise ToolError (rendered as
isError content, never a protocol error). Handlers own nothing semantic:
recall/brief/status/projects logic lives where it always lived (#255) —
this module owns argument validation and JSON/text serialization only.

Usage counters: every call notes `mcp:<tool>` through the same local ledger
as the CLI (#54) — the #257 demand counters must see MCP reads
distinguishably or the gate they measure goes blind.
"""
import json

from . import amendments, briefing, recall, requests, store


class ToolError(Exception):
    """A tool-level failure the calling agent should read, not a crash."""


def _note(tool: str) -> None:
    from . import cli
    cli._note_usage(f"mcp:{tool}")


def _recall(arguments: dict) -> str:
    _note("recall")
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    slug = arguments.get("slug") or None
    all_projects = bool(arguments.get("all_projects"))
    if slug and all_projects:
        # Same guard as the CLI: slug scopes to ONE project.
        raise ToolError("slug scopes to one project; drop it or drop "
                        "all_projects")
    limit = arguments.get("limit")
    limit = 20 if not isinstance(limit, int) or limit < 1 else limit
    from . import cli
    project = cli._resolve_project(None)
    try:
        rows = recall.search(query, project_dir=project, slug=slug,
                             all_projects=all_projects, limit=limit)
    except recall.RecallError as e:
        raise ToolError(str(e))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _brief(arguments: dict) -> str:
    _note("brief")
    slug = arguments.get("slug") or None
    project_arg = arguments.get("project") or None
    if slug and project_arg:
        raise ToolError('slug and project are two answers to "which bucket" '
                        "— pass one")
    from . import cli
    # Strictly scoped read (#94): never the global pointer. A named slug is
    # passed straight through; otherwise the resolved project's own bucket.
    target = slug if slug else cli._resolve_project(project_arg)
    # #693: one strictly-scoped ledger read serves every return below —
    # standing rulings exist before the first checkpoint does, so both
    # no-content returns carry them too.
    rulings = briefing.ruling_lines(target)
    ruling_text = ("\n".join(rulings) + "\n\n") if rulings else ""
    checkpoint = store.read_latest(project_dir=target, fallback=False)
    if checkpoint is None:
        # Orientation without content: name the explicit path, leak nothing
        # (#96, machine edition — an agent tool result carrying another
        # project's briefing is contamination, not convenience).
        others = len(store.list_buckets())
        hint = (f"daimon knows {others} project(s) — call daimon_projects "
                "and pass a slug to read one explicitly."
                if others else
                "no projects have checkpoints yet — the first serialized "
                "session creates one.")
        return f"{ruling_text}no checkpoint for this project. {hint}"
    filtered, _withheld, _candidates = briefing.withhold(
        checkpoint, store.resolutions(project_dir=target),
        amendments=amendments.renderable(project_dir=target))
    # #268: the witness count rides the same strictly-scoped target as the
    # withhold fold — an agent consumer reads the corroboration axis the human
    # brief shows, from this project's ledger and no other.
    filtered = briefing.mark_corroborated(
        filtered, store.corroborations(project_dir=target))
    b = briefing.build(filtered)
    if b is None:
        return f"{ruling_text}checkpoint exists but has nothing worth surfacing."
    # Deterministic render only over MCP — the opt-in LLM re-render is a
    # human-display affordance, and a machine consumer wants stable bytes.
    return briefing.render_plain(b, briefing.receipt_degraded(filtered),
                                 rulings)


def _projects(arguments: dict) -> str:
    _note("projects")
    from . import cli
    return json.dumps(cli.projects_rows(None), ensure_ascii=False, indent=2)


def _status(arguments: dict) -> str:
    _note("status")
    from . import cli
    payload, _rc = cli.status_payload(arguments.get("project") or None)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _requests_inbox(arguments: dict) -> str:
    """Read-only pull (#694 PR 2): requests other projects have addressed to
    this one. Deliberate — daimon_brief does NOT carry this content (D2's
    CLI-only gate); an MCP client that wants it calls this tool explicitly.
    Every write verb (open/revise/accept/reject/needs-info/suppress/done)
    stays CLI-only — no tool here mutates the ledger."""
    _note("requests_inbox")
    from . import cli
    project = cli._resolve_project(arguments.get("project") or None)
    rows = requests.inbox_listing(project_dir=project)
    return json.dumps(rows, ensure_ascii=False, indent=2)


HANDLERS = {
    "daimon_recall": _recall,
    "daimon_brief": _brief,
    "daimon_projects": _projects,
    "daimon_status": _status,
    "requests_inbox": _requests_inbox,
}
