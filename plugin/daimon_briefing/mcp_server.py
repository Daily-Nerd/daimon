"""#261: opt-in, read-only MCP server over stdio — pure stdlib.

Newline-delimited JSON-RPC 2.0 on stdin/stdout (one message per line, no
embedded newlines); logging goes to stderr via the package logger, never to
stdout — a stray print would corrupt the protocol stream.

Deliberately NOT the official MCP SDK: daimon's zero-runtime-dependency
contract is a product claim, and a tools-only stdio server needs exactly five
methods of the spec. The tool registry (TOOLS/_HANDLERS) is decoupled from the
transport loop so a future migration to the SDK — if daimon ever needs
resources/prompts/HTTP — replaces serve() mechanically.

Every tool is a thin shim over the existing library (#255: one owner per
semantic rule — this module owns serialization to MCP shapes, nothing else).
"""
import json
import logging

from . import config

log = logging.getLogger(__name__)

# Newest first. Unknown client versions are answered with our latest: the
# client may then disconnect if it cannot speak it (spec-sanctioned behavior).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2024-11-05")

_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602


def _tool_descriptors():
    """Static tools/list payload. Input schemas are hand-written dicts —
    they describe the shim's arguments, not the library's internals."""
    return [
        {
            "name": "daimon_recall",
            "description": (
                "Search daimon's cross-session memory. Rows carry provenance: "
                "trust class (verbatim = exact quote, inferred = model "
                "conclusion), author, supersession state, and origin project "
                "slug. Cross-project search only via all_projects/slug — "
                "never implicit."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "search terms"},
                    "all_projects": {"type": "boolean",
                                     "description": "search every project"},
                    "slug": {"type": "string",
                             "description": "search one other project by slug"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "daimon_brief",
            "description": (
                "The latest briefing for this project: what the last session "
                "was doing, decisions, open loops — trust-tagged. Strictly "
                "project-scoped: no checkpoint here answers 'none', never "
                "another project's content (pass slug to read one "
                "explicitly)."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": {"type": "string",
                             "description": "read another project by slug"},
                    "project": {"type": "string",
                                "description": "project directory path"},
                },
            },
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "daimon_projects",
            "description": ("Every project daimon has memory for: slug, age, "
                            "last topic. Use a slug with daimon_brief/"
                            "daimon_recall for explicit cross-project reads."),
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "daimon_status",
            "description": ("Capture health for this project: checkpoint "
                            "freshness, last serialize result, outstanding "
                            "failures, alarms."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project": {"type": "string",
                                "description": "project directory path"},
                },
            },
            "annotations": {"readOnlyHint": True},
        },
    ]


def _response(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def _handle_initialize(params):
    client_version = str((params or {}).get("protocolVersion") or "")
    version = (client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS
               else SUPPORTED_PROTOCOL_VERSIONS[0])
    from . import __version__
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "daimon", "version": __version__},
    }


def _handle_tools_call(params):
    """Dispatch one tool call. Tool-level failures come back as isError
    content (the agent can read them); only an unknown tool is a protocol
    error — that's a client bug, not a daimon state."""
    from . import mcp_tools
    name = str((params or {}).get("name") or "")
    arguments = (params or {}).get("arguments") or {}
    handler = mcp_tools.HANDLERS.get(name)
    if handler is None:
        raise _UnknownTool(name)
    try:
        payload = handler(arguments)
    except mcp_tools.ToolError as e:
        return {"content": [{"type": "text", "text": str(e)}],
                "isError": True}
    return {"content": [{"type": "text", "text": payload}], "isError": False}


class _UnknownTool(Exception):
    pass


def serve(in_stream=None, out_stream=None) -> int:
    """Blocking request loop. Returns 0 on clean EOF.

    Rules the loop lives by:
    - never respond to a message without an `id` (notifications);
    - never write anything but protocol JSON to out_stream;
    - one bad line never kills the loop (parse errors get -32700, id null).
    """
    import sys
    in_stream = in_stream if in_stream is not None else sys.stdin
    out_stream = out_stream if out_stream is not None else sys.stdout

    if config.is_disabled():
        # Kill switch: refuse to serve, but exit clean — a disabled daimon
        # must never break a host's MCP startup (printed to stderr via log).
        log.warning("daimon disabled (DAIMON_DISABLE) — mcp serve exiting")
        return 0

    def emit(msg):
        out_stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
        out_stream.flush()

    for line in in_stream:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            emit(_response(None, error={"code": _PARSE_ERROR,
                                        "message": "parse error"}))
            continue
        if not isinstance(req, dict):
            emit(_response(None, error={"code": _PARSE_ERROR,
                                        "message": "parse error"}))
            continue
        method = str(req.get("method") or "")
        has_id = "id" in req
        id_ = req.get("id")
        try:
            if method == "initialize":
                result = _handle_initialize(req.get("params"))
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": _tool_descriptors()}
            elif method == "tools/call":
                result = _handle_tools_call(req.get("params"))
            else:
                if has_id:
                    emit(_response(id_, error={
                        "code": _METHOD_NOT_FOUND,
                        "message": f"method not found: {method}"}))
                continue  # notifications (known or unknown): silence
        except _UnknownTool as e:
            if has_id:
                emit(_response(id_, error={
                    "code": _INVALID_PARAMS,
                    "message": f"unknown tool: {e}"}))
            continue
        if has_id:
            emit(_response(id_, result=result))
    return 0
