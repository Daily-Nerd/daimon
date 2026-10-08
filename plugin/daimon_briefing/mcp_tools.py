"""#261: MCP tool handlers — thin shims over the existing library.

Each handler takes the tools/call `arguments` dict and returns the payload
TEXT for content[0]. Tool-level failures raise ToolError (rendered as
isError content, never a protocol error). Handlers own nothing semantic:
recall/brief/status/projects logic lives where it always lived (#255) —
this module owns argument validation and JSON/text serialization only.

Usage counters: every call notes `mcp:<tool>` through the same local ledger
as the CLI (#54) — the #257 demand counters must see MCP reads
distinguishably or the gate they measure goes blind. The line is an effect,
committed by `effects_commit` once the response is built, also when the
handler raises a ToolError (every attempt counts); a handler's own effects
(the recall telemetry row) commit the same way.
"""
import functools
import json
import time

from . import (briefing, config, display, effects_commit, recall,
               recall_telemetry, requests, store)
from .effects import Effects, Telemetry
from .terms import salient_terms


class ToolError(Exception):
    """A tool-level failure the calling agent should read, not a crash."""


def _tool(name: str):
    """A handler `fn(arguments, fx)` as `fn(arguments)`: `mcp:<name>` usage is
    recorded up front and committed after the payload is built or the
    handler raised; whatever the handler adds to `fx` commits with it."""
    def deco(fn):
        @functools.wraps(fn)
        def run(arguments: dict) -> str:
            fx = effects_commit.Pending(f"mcp:{name}")
            try:
                return fn(arguments, fx)
            finally:
                effects_commit.commit(fx.effects)
        return run
    return deco


@_tool("recall")
def _recall(arguments: dict, fx) -> str:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    slug = arguments.get("slug") or None
    all_projects = bool(arguments.get("all_projects"))
    if slug and all_projects:
        # Same guard as the CLI: slug scopes to ONE project.
        raise ToolError("slug scopes to one project; drop it or drop "
                        "all_projects")
    if config.tenant_scoped() and (slug or all_projects):
        # #899: refused out loud, never narrowed in silence.
        raise ToolError(config.TENANT_SCOPE_REFUSAL)
    limit = arguments.get("limit")
    limit = 20 if not isinstance(limit, int) or limit < 1 else limit
    # #1053: the live session this pull should be attributed to, if the
    # caller (agent) names one — paired against recall-inject's own
    # `injected_into` so tool-form follow-through becomes measurable.
    # `clean_session` is the SAME validator the recall hint's session
    # clause goes through (cli._suggest_line): whatever the hint offers,
    # this accepts back.
    session = recall_telemetry.clean_session(arguments.get("session"))
    from . import cli
    project = cli._resolve_project(None)
    try:
        recalled = recall.query(query, project_dir=project, slug=slug,
                                all_projects=all_projects, limit=limit)
    except recall.RecallError as e:
        raise ToolError(str(e))
    rows = recalled.rows
    # Best-effort (#1053): the row is committed after the payload is built,
    # and a telemetry failure must never take the tool call down with it —
    # the agent still gets its rows back. The rows are copied now, before
    # `status` is added below, so the recall-delivery ledger's row shape is
    # untouched.
    try:
        fx.add(Effects(telemetry=(Telemetry([dict(r) for r in rows], {
            "query_terms": salient_terms(query),
            "surface": "recall-search", "via": "mcp",
            "injected_into": session}),)))
    except Exception:  # noqa: BLE001 — see comment above
        pass
    # #1079: `status` in the SAME words `daimon recall` (text mode) prints
    # inside its `[...]` markers — None for a live row. A display field for
    # the agent reading this result, not a measurement.
    for row in rows:
        row["status"] = recall.describe_status(row)
    out = json.dumps(rows, ensure_ascii=False, indent=2)
    # A degraded read says so on a line of its own ahead of the rows, the way
    # a briefing leads with its warnings; a clean read is the bare list.
    note = display.recall_note(recalled.notes)
    return f"{note}\n{out}" if note else out


@_tool("brief")
def _brief(arguments: dict, fx) -> str:
    slug = arguments.get("slug") or None
    project_arg = arguments.get("project") or None
    if slug and project_arg:
        raise ToolError('slug and project are two answers to "which bucket" '
                        "— pass one")
    if config.tenant_scoped() and slug:
        raise ToolError(config.TENANT_SCOPE_REFUSAL)
    from . import cli
    # Strictly scoped read (#94): never the global pointer. A named slug is
    # passed straight through; otherwise the resolved project's own bucket.
    target = slug if slug else cli._resolve_project(project_arg)
    # Route.OWN is a DECISION here, not a leftover: an agent tool result
    # carrying another project's briefing is contamination, and a pre-routing
    # store stays read-only for project-scoped surfaces — an un-routed body
    # would hand the agent handles no scoped write could act on.
    # #1132 PR 7a: the same preparation the CLI and Hermes run (the view, the
    # #268 witness count on the same strictly-scoped target, stale marks), so
    # an agent consumer reads the world the human brief states. No
    # worldcheck here: only the CLI same-project path sets that gate. A view
    # that cannot be built is a tool error, never an unfiltered briefing.
    now = time.time()
    try:
        annotated = briefing.prepare(target, now, route=store.Route.OWN)
    except Exception as exc:
        raise ToolError("the briefing could not be prepared "
                        f"({type(exc).__name__})") from exc
    notes = annotated.notes
    snap = annotated.snapshot
    # #693: one strictly-scoped ledger read serves every return below —
    # standing rulings exist before the first checkpoint does, so both
    # no-content returns carry them too.
    rulings = briefing.ruling_lines(target, snap=snap)
    ruling_text = "\n".join([*notes, *rulings])
    ruling_text = ruling_text + "\n\n" if ruling_text else ""
    filtered = annotated.checkpoint
    if filtered is None:
        # Orientation without content: name the explicit path, leak nothing
        # (#96, machine edition — an agent tool result carrying another
        # project's briefing is contamination, not convenience).
        # #899: on a tenant-scoped home even the count is enumeration, and
        # the remedy the hint names is the refused argument.
        others = 0 if config.tenant_scoped() else len(store.list_buckets())
        hint = (f"daimon knows {others} project(s) — call daimon_projects "
                "and pass a slug to read one explicitly."
                if others else
                "no projects have checkpoints yet — the first serialized "
                "session creates one.")
        return f"{ruling_text}no checkpoint for this project. {hint}"
    b = briefing.build(filtered)
    if b is None:
        if snap.closed:
            return (f"{briefing.GREETING}\n\n{ruling_text}"
                    f"{briefing.CLOSED_LINE}")
        return f"{ruling_text}checkpoint exists but has nothing worth surfacing."
    # Deterministic render only over MCP — the opt-in LLM re-render is a
    # human-display affordance, and a machine consumer wants stable bytes.
    # The note's `daimon loops` pointer only when that command would list
    # this same project: never for a named slug or a foreign --project.
    return briefing.render_plain(
        b, briefing.receipt_degraded(filtered), rulings,
        loops_pointer=not slug and cli.loops_lists_project(target),
        notes=notes)


@_tool("projects")
def _projects(arguments: dict, fx) -> str:
    from . import cli
    try:
        rows = cli.projects_rows(None)
    except Exception as exc:
        raise ToolError("the projects could not be listed "
                        f"({type(exc).__name__})") from exc
    return json.dumps(rows, ensure_ascii=False, indent=2)


@_tool("status")
def _status(arguments: dict, fx) -> str:
    from . import cli
    payload, _rc = cli.status_payload(arguments.get("project") or None)
    return json.dumps(payload, ensure_ascii=False, indent=2)


@_tool("requests_inbox")
def _requests_inbox(arguments: dict, fx) -> str:
    """Read-only pull (#694 PR 2): requests other projects have addressed to
    this one. Deliberate — daimon_brief does NOT carry this content (D2's
    CLI-only gate); an MCP client that wants it calls this tool explicitly.
    Every write verb (open/revise/accept/reject/needs-info/suppress/reply/done)
    stays CLI-only — no tool here mutates the ledger."""
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
