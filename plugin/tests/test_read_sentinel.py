"""The read census: no read surface hands back a value it must withhold
(#1132 PR 6b), modeled on `test_write_audit_guard.py`.

A fixture store (`tests/_sentinel_world.py`, real writers only) holds one
sentinel per `schema.ITEM_FIELDS` entry: three quarantined by a human, three
forgotten through the real `daimon forget`. Every read surface is driven
against it: each CLI leaf (the iterator the write guard uses), every MCP tool,
every viewer route (found by an AST walk of `do_GET`) and the Hermes hooks.
A surface leaks when a sentinel token appears in anything it returned or in
any byte its run wrote.

Registries are two-way: a surface (or dest, mode, exemption) that exists and
is not classified fails, and a classification of something that no longer
exists fails. `KNOWN_LEAKS` is the allowlist of leaks the drive finds today;
it only shrinks: each listed entry must still leak and nothing else may.
`UNCONVERTED` lists the surfaces that do not yet go through `view`; PR 7
onward deletes them.

Not captured: output printed from a thread or an atexit handler (no drive
starts either). The sentinel detector is a token substring test, deliberately
stronger than the view's whole-value matching: a surface that echoes a
withheld value inside longer prose still leaks.
"""

import ast
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

import daimon_briefing
import daimon_ui
from daimon_briefing import cli, mcp_tools, schema, store
from tests import _leaves, _sentinel_drive as drive, _sentinel_world as sw
from tests._sentinel_dests import DESTS

SERVER = Path(daimon_ui.__file__).parent / "server.py"
PYTHON_PLAIN = "DAIMON_PLAIN"


@dataclass(frozen=True)
class Case:
    """One invocation of a surface. `args(world)` builds argv (cli), the
    arguments dict (mcp), the path and query (http) or the hook kwargs.
    `dests` are the OUTPUT dests it exercises, `axes` the OUTPUT_MODES axes it
    is crossed with, `shows` a visible control string that must appear (so a
    case that errors out cannot pass by saying nothing)."""

    args: Callable
    stdin: str = ""
    tty: bool = False
    dests: tuple = ()
    axes: tuple = ()
    shows: str | None = None
    rc: tuple = (0,)


def I(kind):                       # noqa: E743 — a short id accessor
    return lambda w: w.ids[kind]


CASES: dict = {}                   # (surface, tag) -> Case, filled below
NO_ITEMS: dict = {}                # surface -> reason it never shows item content


def case(surface, tag, args, **kw):
    assert (surface, tag) not in CASES
    CASES[(surface, tag)] = Case(args, **kw)


def no_items(reason, *surfaces):
    for surface in surfaces:
        assert surface not in NO_ITEMS
        NO_ITEMS[surface] = reason


# ===========================================================================
# Registry of surfaces
# ===========================================================================

def cli_surfaces():
    return {"cli:" + " ".join(leaf)
            for leaf in _leaves.iter_commands(cli.build_parser())}


def mcp_surfaces():
    return {"mcp:" + name for name in mcp_tools.HANDLERS}


def viewer_routes():
    """Every route `do_GET` branches on, by AST: a string compared with
    `path ==` or passed to `path.startswith(...)`."""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    do_get = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "do_GET")
    routes = []
    for node in ast.walk(do_get):
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)
                and node.left.id == "path" and isinstance(node.ops[0], ast.Eq)
                and isinstance(node.comparators[0], ast.Constant)):
            routes.append(node.comparators[0].value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "startswith"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "path" and node.args
                and isinstance(node.args[0], ast.Constant)):
            routes.append(node.args[0].value)
    return routes


def http_surfaces():
    return {"http:" + route for route in viewer_routes()}


HOOKS = ("pre_llm_call", "on_session_end")


def hook_surfaces():
    return {"hook:" + name for name in HOOKS}


def all_surfaces():
    return (cli_surfaces() | mcp_surfaces() | http_surfaces()
            | hook_surfaces())


# ===========================================================================
# OUTPUT_MODES: the environment axes a read can depend on
# ===========================================================================
# axis -> (values a case is crossed with, modules that read the env var).
# The module set is two-way against a source scan of every string constant
# naming the variable, so a new reader of an axis fails until it is listed.
OUTPUT_MODES = {
    "DAIMON_LLM_BRIEFING": (("off",), {"config.py"}),
    "DAIMON_PLAIN": (("plain", "rich"), {"render.py", "cli/__init__.py"}),
    "STDIN_TTY": (("agent", "human"), set()),
    "DAIMON_BRIEF_GLOBAL_FALLBACK": (("", "full"), {"config.py"}),
    "DAIMON_EXTRA_READ_SLUGS": (("", "set"), {"config.py"}),
    "DAIMON_TENANT_SCOPED": (("", "1"), {"config.py"}),
    "DAIMON_LIVE_DELIVERY": (("", "1"), {
        "config.py", "_hooks/daimon-codex-user-prompt-submit.py",
        "_hooks/daimon-kimi-user-prompt-submit.py"}),
}
# Modules that call `.isatty()`: the human-channel and rich-renderer gates.
# Two-way against a scan; STDIN_TTY and DAIMON_PLAIN x stdout TTY ride on it.
TTY_MODULES = {
    "cli/amend.py", "cli/_ledger.py", "cli/configure_cmd.py",
    "cli/ledger_cmd.py", "cli/lifecycle.py", "cli/relations_cmd.py",
    "cli/request.py", "cli/trust.py", "host_detect.py", "render.py",
}
# LLM briefing is an egress path: the text it sends is the kept, post-view
# text, so the axis is listed (and its accessor pinned) but never driven.
EGRESS_EXEMPT = {"DAIMON_LLM_BRIEFING":
                 "the model sees only the kept text, which is post-view"}


# ===========================================================================
# The case table
# ===========================================================================

def A(*parts):
    """argv builder: callables take the world, anything else is literal."""
    return lambda w: [p(w) if callable(p) else p for p in parts]


def slug(w):
    return store.project_slug(w.project)


def slug_opt(w):
    return f"--slug={slug(w)}"


def proj_opt(w):
    return f"--project={w.project}"


def qid(w):
    return w.quarantine_ids["question"]


VISIBLE = "an unrelated decision stays visible"
OTHER = "an unrelated open question stays visible"
CROSS = ("DAIMON_PLAIN",)

# ---- briefing and recall --------------------------------------------------
case("cli:brief", "default", A("brief"), shows=VISIBLE,
     axes=("DAIMON_PLAIN", "DAIMON_BRIEF_GLOBAL_FALLBACK",
           "DAIMON_TENANT_SCOPED"), dests=("project",))
case("cli:brief", "team", A("brief", "--team"), shows=VISIBLE,
     dests=("team",))
case("cli:brief", "slug", A("brief", slug_opt), shows=VISIBLE, dests=("slug",))
case("cli:brief", "global", A("brief", "--global-fallback"),
     dests=("global_fallback",))
case("cli:brief", "auto", A("brief", "--auto"), dests=("auto",))
case("cli:anchor", "print", A("anchor", "mod.py", "fn"),
     dests=("file", "symbol", "project"), rc=(0, 1))
case("cli:anchor", "attach",
     A("anchor", "mod.py", "fn", "--attach", sw.TOKENS["question"]),
     dests=("attach",), rc=(0, 1))
case("cli:recall", "query", A("recall", "sentinel"), dests=("query",),
     axes=("DAIMON_PLAIN", "DAIMON_EXTRA_READ_SLUGS", "DAIMON_TENANT_SCOPED"))
case("cli:recall", "control", A("recall", "unrelated", "decision"),
     shows="unrelated", dests=("query",))
case("cli:recall", "json", A("recall", "sentinel", "--json"),
     dests=("json",))
case("cli:recall", "limit", A("recall", "sentinel", "--limit", "1"),
     dests=("limit",))
case("cli:recall", "all", A("recall", "sentinel", "--all-projects"),
     dests=("all_projects",))
case("cli:recall", "project", A("recall", "sentinel", proj_opt),
     dests=("project",))
case("cli:recall", "slug", A("recall", "sentinel", slug_opt), dests=("slug",))
for kind in ("question", "decision", "belief", "uncertainty"):
    case("cli:why", kind, A("why", I(kind)), rc=(0, 1), dests=("item_id",),
         axes=("DAIMON_PLAIN",) if kind == "question" else ())
case("cli:why", "control", A("why", I("other_question")), shows=OTHER)
case("cli:why", "json", A("why", I("question"), "--json"), rc=(0, 1),
     dests=("json",))
case("cli:why", "source", A("why", I("question"), "--source"), rc=(0, 1),
     dests=("source",))
case("cli:why", "project", A("why", I("question"), proj_opt), rc=(0, 1),
     dests=("project",))
case("cli:why", "slug", A("why", I("question"), slug_opt), rc=(0, 1),
     dests=("slug",))
case("cli:diff", "default", A("diff"), dests=("project",),
     axes=("DAIMON_PLAIN",))
case("cli:diff", "json", A("diff", "--json"), dests=("json",))
case("cli:diff", "range", A("diff", "--from", "prev-1", "--to", "latest"),
     dests=("frm", "to"), rc=(0, 1, 2))
case("cli:diff", "slug", A("diff", slug_opt), dests=("slug",))
for kind in ("question", "decision", "belief", "uncertainty"):
    case("cli:blame", kind, A("blame", I(kind)), rc=(0, 1),
         dests=("item_id",))
case("cli:blame", "json", A("blame", I("question"), "--json"), rc=(0, 1),
     dests=("json",))
case("cli:blame", "project", A("blame", I("question"), proj_opt), rc=(0, 1),
     dests=("project",))
case("cli:blame", "slug", A("blame", I("question"), slug_opt), rc=(0, 1),
     dests=("slug",))
case("cli:projects", "default", A("projects"), dests=("project",),
     axes=("DAIMON_PLAIN", "DAIMON_TENANT_SCOPED"))
case("cli:projects", "json", A("projects", "--json"), dests=("json",))
case("cli:loops", "default", A("loops"), shows="unrelated open", dests=("project",),
     axes=("DAIMON_PLAIN",))
case("cli:loops", "stale", A("loops", "--stale"), dests=("stale",))
case("cli:decide", "default", A("decide"), dests=("project",),
     axes=("DAIMON_PLAIN",))
case("cli:decide", "all", A("decide", "--all-projects"),
     dests=("all_projects",))
case("cli:status", "default", A("status"), dests=("project",),
     axes=("DAIMON_PLAIN", "DAIMON_BRIEF_GLOBAL_FALLBACK"))
case("cli:status", "json", A("status", "--json"), dests=("json",))
case("cli:status", "suppressed", A("status", "--suppressed"),
     dests=("suppressed",))
case("cli:verify-receipt", "session", A("verify-receipt", "S-2"),
     rc=(0, 1, 2), dests=("session_id", "project"))
case("cli:stats", "default", A("stats"), axes=("DAIMON_PLAIN",))
case("cli:stats", "json", A("stats", "--json"), dests=("json",))
case("cli:audit privacy", "default", A("audit", "privacy"), rc=(0, 1, 3),
     dests=("project",))
case("cli:audit privacy", "all", A("audit", "privacy", "--all"),
     rc=(0, 1, 3), dests=("all_projects",))
case("cli:audit quotes", "default", A("audit", "quotes"), rc=(0, 1, 3),
     dests=("project", "all", "top", "json"))
case("cli:audit-quotes", "default", A("audit-quotes"), rc=(0, 1, 3),
     dests=("project", "all", "top"))
case("cli:check sync", "default", A("check", "sync"), rc=(0, 1),
     dests=("check", "json", "project"))
case("cli:team status", "default", A("team", "status"), rc=(0, 1),
     dests=("json",))
case("cli:heal", "dry", A("heal", "--dry-run"), dests=("dry_run",))
case("cli:recall-inject", "prompt", A("recall-inject", "--session", "S-x"),
     stdin="sentinel decision question belief", dests=("project", "session"),
     axes=("DAIMON_BRIEF_GLOBAL_FALLBACK", "DAIMON_EXTRA_READ_SLUGS"))
case("cli:action-recall", "action",
     A("action-recall", "--session", "S-x"),
     stdin="git push origin sentinel decision question",
     dests=("project", "session", "record_only"),
     axes=("DAIMON_BRIEF_GLOBAL_FALLBACK", "DAIMON_EXTRA_READ_SLUGS"))
case("cli:request-inject", "prompt", A("request-inject", "--session", "S-x"),
     dests=("project", "session"), axes=("DAIMON_LIVE_DELIVERY",))

# ---- ledgers: reads and the verbs that act on a named record --------------
case("cli:refute list", "default", A("refute", "list"), dests=("state",
     "project", "json"))
case("cli:refute show", "id", A("refute", "show", lambda w: w.refutation_id),
     rc=(0, 1), dests=("refutation_id", "project", "json"))
case("cli:refute search", "default", A("refute", "search", "sentinel"),
     dests=("query", "state", "project", "json"))
case("cli:refute guard", "default", A("refute", "guard", "sentinel"),
     dests=("query", "anchor", "project", "json", "quiet"))
case("cli:refute ratify", "id", A("refute", "ratify",
     lambda w: w.refutation_id), tty=True, rc=(0, 1, 2),
     dests=("refutation_id",))
case("cli:refute revise", "id", A("refute", "revise",
     lambda w: w.refutation_id, "--verdict", "changed", "--evidence",
     "measurement:x"), tty=True, rc=(0, 1, 2), dests=("refutation_id",))
case("cli:refute overturn", "id", A("refute", "overturn",
     lambda w: w.refutation_id, "--evidence", "measurement:x"), tty=True,
     rc=(0, 1, 2), dests=("refutation_id",))
case("cli:ruling list", "default", A("ruling", "list"),
     dests=("state", "project", "json"), axes=("DAIMON_PLAIN",
     "DAIMON_TENANT_SCOPED"))
case("cli:ruling list", "inherited", A("ruling", "list", "--inherited"),
     dests=("inherited",))
case("cli:ruling show", "id", A("ruling", "show", lambda w: w.ruling_id),
     rc=(0, 1), dests=("ruling_id", "project", "json"))
case("cli:ruling checks", "default", A("ruling", "checks"),
     dests=("project", "json"))
case("cli:ruling ratify", "id", A("ruling", "ratify", lambda w: w.ruling_id),
     tty=True, rc=(0, 1, 2), dests=("ruling_id",))
case("cli:ruling revise", "id", A("ruling", "revise", lambda w: w.ruling_id,
     "--verdict", "changed", "--evidence", "issue:1"), tty=True,
     rc=(0, 1, 2), dests=("ruling_id",))
case("cli:ruling retire", "id", A("ruling", "retire", lambda w: w.ruling_id,
     "--evidence", "issue:1"), tty=True, rc=(0, 1, 2), dests=("ruling_id",))
case("cli:amend list", "default", A("amend", "list"),
     dests=("project", "json"))
case("cli:amend propose", "item", A("amend", I("question"), "--change",
     "changed", "--evidence", "the PR merged"), rc=(0, 1, 2),
     dests=("item_id",))
case("cli:amend ratify", "id", A("amend", "ratify",
     lambda w: w.amendment_id), tty=True, rc=(0, 1, 2),
     dests=("amendment_id",))
case("cli:amend reject", "id", A("amend", "reject",
     lambda w: w.amendment_id), tty=True, rc=(0, 1, 2),
     dests=("amendment_id",))
case("cli:trust list", "default", A("trust", "list"),
     dests=("project", "json"))
case("cli:trust show", "id", A("trust", "show", qid), rc=(0, 1),
     dests=("quarantine_id", "project", "json"), axes=("STDIN_TTY",))
case("cli:trust propose", "text", A("trust", "propose", "--text",
     "an unrelated claim", "--kind", "decision", "--reason", "r",
     "--evidence", "issue:1"), rc=(0, 1, 2), dests=("item_id",),
     axes=("STDIN_TTY",))
for verb in ("confirm", "dismiss", "release"):
    case(f"cli:trust {verb}", "id", A("trust", verb, qid), tty=True,
         rc=(0, 1, 2), dests=("quarantine_id",))
case("cli:trust repair", "dry", A("trust", "repair", "--dry-run"),
     dests=("dry_run",))
case("cli:ledger repair", "dry", A("ledger", "repair", "trust", "--dry-run"),
     dests=("name", "dry_run"))
case("cli:request list", "default", A("request", "list"),
     dests=("project", "json"), axes=("DAIMON_PLAIN",))
case("cli:request inbox", "default", A("request", "inbox"),
     dests=("project", "json"), axes=("DAIMON_LIVE_DELIVERY",))
for verb in ("accept", "reject", "needs-info", "suppress"):
    case(f"cli:request {verb}", "id", A("request", verb,
         lambda w: w.request_id), tty=True, rc=(0, 1, 2),
         dests=("request_id",))
case("cli:request done", "id", A("request", "done", lambda w: w.request_id,
     "--evidence", "it shipped"), tty=True, rc=(0, 1, 2),
     dests=("request_id",))
case("cli:request reply", "id", A("request", "reply",
     lambda w: w.request_id, "--note", "progress"), tty=True, rc=(0, 1, 2),
     dests=("request_id",))
case("cli:request revise", "id", A("request", "revise",
     lambda w: w.request_id, "--ask", "revised"), tty=True, rc=(0, 1, 2),
     dests=("request_id",))
case("cli:request open", "default", A("request", "open", "--to", slug,
     "--ask", "an unrelated ask", "--why", "because"), tty=True,
     rc=(0, 1, 2))
case("cli:relations list", "default", A("relations", "list"),
     dests=("state", "project", "json"), axes=("DAIMON_PLAIN",))
case("cli:relations show", "id", A("relations", "show",
     lambda w: w.relation_id), dests=("relation_id", "project", "json"))
for verb in ("confirm", "reject", "retract"):
    case(f"cli:relations {verb}", "id", A("relations", verb,
         lambda w: w.relation_id), tty=True, rc=(0, 1, 2),
         dests=("relation_id",))

# ---- verbs that bind a target in the live checkpoint ----------------------
case("cli:resolve", "no-match", A("resolve", "zzzqqq"), tty=True, rc=(0, 1),
     dests=("target",))
case("cli:resolve", "id", A("resolve", I("question")), tty=True, rc=(0, 1),
     dests=("target", "project"))
case("cli:resolve", "dry", A("resolve", I("question"), "--dry-run"),
     tty=True, rc=(0, 1), dests=("dry_run",))
case("cli:forget", "no-match", A("forget", "zzzqqq"), tty=True, rc=(0, 1),
     dests=("target",))
case("cli:forget", "dry", A("forget", I("question"), "--dry-run"), tty=True,
     rc=(0, 1), dests=("dry_run", "project"), axes=("STDIN_TTY",))
case("cli:reverify", "id", A("reverify", I("question"), "--evidence",
     "checked"), tty=True, rc=(0, 1), dests=("target", "project"))

# ---- R1 dests (which bucket is read) on the verbs that act on a record ----
def _bucket_variants():
    seen = {}
    for (surface, tag), c in list(CASES.items()):
        seen.setdefault(surface, (tag, c))
    for leaf, row in DESTS.items():
        surface = "cli:" + leaf
        if surface not in seen:
            continue
        base_tag, base = seen[surface]
        for name, (cls, code) in row.items():
            if cls != "OUT" or code != "R1" or name in base.dests:
                continue
            if any(name in c.dests for (sf, _t), c in CASES.items()
                   if sf == surface):
                continue
            value = ((lambda w: w.project) if name == "project"
                     else (lambda w: slug(w)))
            CASES[(surface, "bucket-" + name)] = Case(
                (lambda b, n, v: lambda w: [*b.args(w), f"--{n}", v(w)])(
                    base, name, value),
                stdin=base.stdin, tty=base.tty, dests=(name,), rc=base.rc + (1, 2))


_bucket_variants()

# ---- surfaces that never show item content --------------------------------
no_items("installs or lists host integration files; no store read",
         "cli:hooks install", "cli:hooks list", "cli:hooks remove",
         "cli:hooks status", "cli:skill install", "cli:skill list",
         "cli:skill show", "cli:skill status", "cli:skill uninstall",
         "cli:configure")
no_items("prints the project slug of a path",  "cli:slug")
no_items("writes or migrates; reads names of buckets, never item text",
         "cli:bucket migrate", "cli:team init", "cli:team sync",
         "cli:write-checkpoint", "cli:serialize", "cli:handoff",
         "cli:refute add", "cli:ruling propose", "cli:ruling check try")
no_items("prints only usage counters", "cli:log")
no_items("blocking server loop, skipped like the write guard skips it",
         "cli:serve", "cli:mcp serve")

# ---- MCP -------------------------------------------------------------------
case("mcp:daimon_recall", "query", lambda w: {"query": "sentinel"})
case("mcp:daimon_recall", "all", lambda w: {"query": "sentinel",
     "all_projects": True})
case("mcp:daimon_recall", "slug", lambda w: {"query": "sentinel",
     "slug": slug(w)})
case("mcp:daimon_brief", "default", lambda w: {"project": w.project},
     shows=VISIBLE)
case("mcp:daimon_projects", "default", lambda w: {})
case("mcp:daimon_status", "default", lambda w: {"project": w.project})
case("mcp:requests_inbox", "default", lambda w: {"project": w.project})

# ---- viewer routes ---------------------------------------------------------
case("http:/", "page", lambda w: "/")
case("http:/api/projects", "default", lambda w: "/api/projects")
case("http:/api/checkpoints", "default", lambda w: "/api/checkpoints")
case("http:/api/checkpoint/", "latest", lambda w: "/api/checkpoint/latest")
case("http:/api/checkpoint/", "prev", lambda w: "/api/checkpoint/prev-1")
case("http:/api/checkpoint/", "session", lambda w: "/api/checkpoint/S-1")
case("http:/api/history", "default", lambda w: "/api/history")
case("http:/api/diff", "default", lambda w: "/api/diff")
case("http:/api/diff", "ab", lambda w: "/api/diff?a=S-1&b=S-2")
case("http:/api/biography", "item", lambda w: f"/api/biography?id={w.ids['question']}")
case("http:/api/recall", "query", lambda w: "/api/recall?q=sentinel")
case("http:/api/why", "item", lambda w: f"/api/why?id={w.ids['question']}&source=1")
case("http:/api/grid", "default", lambda w: "/api/grid")
case("http:/api/refutations", "default", lambda w: "/api/refutations")
case("http:/api/relations", "default", lambda w: "/api/relations")
case("http:/api/ledger", "item", lambda w: f"/api/ledger?id={w.ids['question']}")
case("http:/api/session", "sid", lambda w: "/api/session?sid=S-2")
case("http:/api/activity", "default", lambda w: "/api/activity")
case("http:/static/", "asset", lambda w: "/static/app.js")

# ---- hooks -----------------------------------------------------------------
case("hook:pre_llm_call", "first", lambda w: {
    "session_id": "S-new", "user_message": "hello", "is_first_turn": True},
    axes=("DAIMON_BRIEF_GLOBAL_FALLBACK",))
case("hook:on_session_end", "end", lambda w: {"session_id": "S-end",
     "conversation_history": []})


# ===========================================================================
# Running a case
# ===========================================================================

def axis_envs(axes):
    """Env/stdin variants for the axes a case is crossed with (full product
    of the axes the case lists, never the others)."""
    values = [OUTPUT_MODES[a][0] for a in axes]
    for combo in itertools.product(*values):
        env, tty, plain = [], None, None
        for axis, val in zip(axes, combo):
            if axis == "DAIMON_PLAIN":
                plain = val == "plain"
                env.append(("DAIMON_PLAIN", "1" if plain else None))
            elif axis == "STDIN_TTY":
                tty = val == "human"
            elif axis == "DAIMON_BRIEF_GLOBAL_FALLBACK":
                env.append((axis, val or None))
            elif axis == "DAIMON_EXTRA_READ_SLUGS":
                env.append((axis, "other-project" if val else None))
            elif axis == "DAIMON_TENANT_SCOPED":
                env.append((axis, val or None))
            elif axis == "DAIMON_LIVE_DELIVERY":
                env.append((axis, val or None))
        yield tuple(env), tty, ("-".join(map(str, combo)) or "base")


def drive_case(surface, tag, world, pristine, *, tty_override=None,
               env_extra=()):
    c = CASES[(surface, tag)]
    kind = surface.split(":", 1)[0]
    name = surface.split(":", 1)[1]
    leaks, results = set(), []
    variants = (list(axis_envs(c.axes)) if c.axes
                else [((), None, "base")])
    for env, tty, label in variants:
        pristine.restore()
        before = drive.snapshot_sizes(world.root)
        tty_now = c.tty if tty is None else tty
        if tty_override is not None:
            tty_now = tty_override
        envs = tuple(env) + tuple(env_extra)
        stdout_tty = any(k == "DAIMON_PLAIN" and v is None for k, v in env)
        if kind == "cli":
            res = drive.run_cli(c.args(world), stdin=c.stdin,
                                stdin_tty=tty_now, stdout_tty=stdout_tty,
                                env=envs)
        elif kind == "mcp":
            res = drive.run_mcp(name, c.args(world), env=envs)
        elif kind == "http":
            res = drive.run_http(c.args(world), world.bucket.parent,
                                 world.bucket.name, env=envs)
        else:
            res = drive.run_hook(name, c.args(world), env=envs)
        res.written = drive.written_since(world.root, before)
        blob = res.text() + "\n" + b"\n".join(res.written).decode(
            "utf-8", errors="replace")
        leaks |= {(surface, tag, k) for k in sw.leaked_kinds(blob)}
        results.append((label, res))
    return leaks, results


# ===========================================================================
# Allowlists (seeded from the run; shrink-only)
# ===========================================================================
# {(surface, kind)}: seeded from the run, each must still leak, nothing else may
KNOWN_LEAKS: set = {
    *{("cli:action-recall", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:amend list", k) for k in ("question",)},
    *{("cli:blame", k) for k in ("contradiction", "question",)},
    *{("cli:decide", k) for k in ("question", "topic",)},
    *{("cli:forget", k) for k in ("question", "topic",)},
    *{("cli:recall", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:recall-inject", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:refute list", k) for k in ("topic",)},
    *{("cli:refute overturn", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:refute ratify", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:refute revise", k) for k in ("question", "topic",)},
    *{("cli:refute search", k) for k in ("question", "topic",)},
    *{("cli:refute show", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:relations list", k) for k in ("question",)},
    *{("cli:relations show", k) for k in ("question",)},
    *{("cli:request accept", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request done", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request inbox", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request list", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request needs-info", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request reject", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request reply", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request suppress", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request-inject", k) for k in ("contradiction", "topic",)},
    *{("cli:resolve", k) for k in ("question",)},
    *{("cli:reverify", k) for k in ("question",)},
    *{("cli:ruling list", k) for k in ("question",)},
    *{("cli:ruling retire", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling revise", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling show", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:trust list", k) for k in ("contradiction", "question",)},
    *{("cli:trust show", k) for k in ("contradiction",)},
    *{("cli:why", k) for k in ("question",)},
    *{("http:/api/activity", k) for k in ("contradiction", "question", "topic",)},
    *{("http:/api/checkpoint/", k) for k in ("topic",)},
    *{("http:/api/checkpoints", k) for k in ("topic",)},
    *{("http:/api/diff", k) for k in ("topic",)},
    *{("http:/api/history", k) for k in ("topic",)},
    *{("http:/api/ledger", k) for k in ("topic",)},
    *{("http:/api/projects", k) for k in ("topic",)},
    *{("http:/api/recall", k) for k in ("contradiction", "question", "topic",)},
    *{("http:/api/refutations", k) for k in ("contradiction", "question", "topic",)},
    *{("http:/api/session", k) for k in ("topic",)},
    *{("http:/api/why", k) for k in ("question",)},
    *{("mcp:daimon_recall", k) for k in ("contradiction", "question", "topic",)},
    *{("mcp:requests_inbox", k) for k in ("contradiction", "question", "topic",)},
}
UNCONVERTED: set = set()      # surfaces that do not yet go through `view`


@pytest.fixture(scope="module")
def world_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sentinel")
    with pytest.MonkeyPatch.context() as m:
        world = sw.build_world(tmp, m)
        pristine = drive.Pristine(tmp, tmp.parent / (tmp.name + "-keep"))
        leaks, details = {}, {}
        for (surface, tag) in CASES:
            found, results = drive_case(surface, tag, world, pristine)
            details[(surface, tag)] = results
            for s, _t, kind in found:
                leaks.setdefault((s, kind), set()).add(tag)
        yield world, leaks, details


def test_surfaces_are_all_classified():
    have = {s for s, _ in CASES} | set(NO_ITEMS)
    assert all_surfaces() - have == set(), "surface without a case or reason"
    assert have - all_surfaces() == set(), "case for a surface that is gone"
    assert not ({s for s, _ in CASES} & set(NO_ITEMS))


def test_viewer_routes_are_the_sixteen():
    assert len(viewer_routes()) == 16 == len(set(viewer_routes()))


def test_dests_match_the_parser_and_are_classified():
    import argparse
    for leaf, parser in _leaves.leaf_parsers(cli.build_parser()).items():
        dests = {a.dest for a in parser._actions
                 if not isinstance(a, argparse._HelpAction)}
        assert dests == set(DESTS[" ".join(leaf)]), leaf
    assert sum(len(v) for v in DESTS.values()) == 338
    for leaf, row in DESTS.items():
        for name, (cls, code) in row.items():
            assert cls in ("OUT", "INE"), (leaf, name)


def test_every_output_dest_has_a_case_or_a_reason():
    exercised = {}
    for (surface, _tag), c in CASES.items():
        exercised.setdefault(surface, set()).update(c.dests)
    missing = []
    for leaf, row in DESTS.items():
        surface = "cli:" + leaf
        if surface in NO_ITEMS:
            continue
        for name, (cls, _code) in row.items():
            if cls == "OUT" and name not in exercised.get(surface, set()):
                missing.append((surface, name))
    assert missing == [], missing


def _pkg_sources():
    pkg = Path(daimon_briefing.__file__).parent
    for path in sorted(pkg.rglob("*.py")):
        yield path.relative_to(pkg).as_posix(), path.read_text(encoding="utf-8")


def test_output_mode_readers_match_a_source_scan():
    for axis, (_vals, modules) in OUTPUT_MODES.items():
        if axis == "STDIN_TTY":
            continue
        found = set()
        for rel, text in _pkg_sources():
            tree = ast.parse(text)
            if any(isinstance(n, ast.Constant) and n.value == axis
                   for n in ast.walk(tree)):
                found.add(rel)
        assert found == modules, (axis, found ^ modules)


def test_tty_gates_match_a_source_scan():
    found = {rel for rel, text in _pkg_sources()
             if any(isinstance(n, ast.Attribute) and n.attr == "isatty"
                    for n in ast.walk(ast.parse(text)))}
    assert found == TTY_MODULES, found ^ TTY_MODULES


def test_a_cased_axis_is_a_known_axis():
    for c in CASES.values():
        assert set(c.axes) <= set(OUTPUT_MODES), c.axes


def test_the_drive_runs_and_nothing_unexpected_leaks(world_run):
    _world, leaks, _details = world_run
    seen = {(s, kind) for (s, kind) in leaks}
    print("LEAKS", sorted(seen))
    assert seen == KNOWN_LEAKS


def test_cases_ran_and_said_what_they_should(world_run):
    _world, _leaks, details = world_run
    bad = []
    for key, results in details.items():
        c = CASES[key]
        for label, res in results:
            if res.rc not in c.rc and not (key[0].startswith(("http", "mcp",
                                                              "hook"))):
                bad.append((key, label, "rc", res.rc, res.error[:80]))
            if c.shows and c.shows not in res.text():
                bad.append((key, label, "shows"))
            if res.rc == "raised":
                bad.append((key, label, "raised", res.error[:100]))
    assert bad == [], bad


# ===========================================================================
# Exemptions
# ===========================================================================
# Output that exists to show a human the value they are about to act on.
# Each is run on BOTH channels; the agent channel must show no sentinel.
HUMAN_CHANNEL = {
    ("cli:trust show", "id"):
        "a human reads the quarantined evidence they are about to confirm",
    ("cli:trust propose", "text"):
        "echoes the text the human just typed; the agent channel is refused",
    ("cli:forget", "dry"):
        "dry run names the target the human is about to forget",
}
# Bytes that carry a checkpoint forward on purpose: not read output.
WRITE_CARRY = {
    ("cli:serialize", "carry"):
        "carries the previous checkpoint into the new one (scar 0096 plants)",
    ("store._dual_write_team", "mirror"):
        "copies the checkpoint into the author's team mirror",
    ("cli:team sync", "publish"):
        "publishes own author dirs to the sidecar remote",
}


def test_exemptions_name_real_cases():
    for key in HUMAN_CHANNEL:
        assert key in CASES, key
    for surface, _tag in WRITE_CARRY:
        assert surface in NO_ITEMS or surface.startswith("store."), surface


def test_the_human_channel_exemption_is_closed_to_the_agent(world_run):
    world, _leaks, _details = world_run
    tmp = world.root
    pristine = drive.Pristine(tmp, tmp.parent / (tmp.name + "-keep2"))
    for key in HUMAN_CHANNEL:
        for label, tty in (("human", True), ("agent", False)):
            found, results = drive_case(key[0], key[1], world, pristine,
                                        tty_override=tty)
            if not tty:
                assert found == set(), (key, label, found)


def test_llm_briefing_is_an_exemption_not_an_axis_we_drive():
    assert "DAIMON_LLM_BRIEFING" in EGRESS_EXEMPT
    assert OUTPUT_MODES["DAIMON_LLM_BRIEFING"][0] == ("off",)


# ===========================================================================
# Uniform failure
# ===========================================================================
# Surfaces that read through the view: the module whose code makes the call,
# and the names that call may take. `prepare` opens the checkpoint through
# `view.open`; `open`, `suppressed` and `visible_topic` are the view's own
# projections. The MCP projects tool reaches `visible_topic` through
# `cli.projects_rows`, so the module that calls it is cli/projects.py.
PREPARE = frozenset({"prepare", "_prepared"})
PEEK = frozenset({"visible_topic"})
CONVERTED = {
    "cli:brief": ("cli/brief.py", PREPARE),
    "cli:loops": ("cli/lifecycle.py", PREPARE),
    "cli:projects": ("cli/projects.py", PEEK),
    "cli:status": ("cli/status.py", frozenset({"suppressed"})),
    "mcp:daimon_brief": ("mcp_tools.py", PREPARE),
    "mcp:daimon_projects": ("cli/projects.py", PEEK),
    "hook:pre_llm_call": ("hooks.py", PREPARE),
}

# Shrink-only: surfaces that do not yet read through `view.open`. Each PR from
# 7a onward deletes entries as readers convert; an empty set is the goal.
UNCONVERTED = {s for s, _ in CASES} - set(CONVERTED)


# Cases of a converted surface that never read a checkpoint item: the status
# payload carries counts and health, never an item's text (only the
# `--suppressed` listing reads items), so there is no view call to fail.
NO_ITEM_READ = {("cli:status", "default"), ("cli:status", "json")}


def _calls_view(rel, names):
    pkg = Path(daimon_briefing.__file__).parent
    tree = ast.parse((pkg / rel).read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Call)
               and (getattr(n.func, "attr", None) in names
                    or getattr(n.func, "id", None) in names)
               for n in ast.walk(tree))


def test_unconverted_is_a_subset_of_the_registry():
    assert UNCONVERTED <= all_surfaces()
    assert not UNCONVERTED & set(CONVERTED)
    assert UNCONVERTED | set(CONVERTED) == {s for s, _ in CASES}


def test_a_converted_surface_reaches_the_view():
    for surface, (rel, names) in CONVERTED.items():
        assert _calls_view(rel, names), (surface, rel)


def test_a_converted_surface_renders_nothing_when_view_open_raises(
        world_run, monkeypatch):
    from daimon_briefing import view
    world, _l, _d = world_run
    converted = {s for s, _ in CASES} - UNCONVERTED
    assert converted == set(CONVERTED)

    def boom(*_a, **_k):
        raise RuntimeError("view.open failed")

    monkeypatch.setattr(view, "open", boom)
    monkeypatch.setattr(view, "visible_topic", boom)
    # the autouse isolation points this test at a fresh empty home; the world
    # lives where the fixture built it, and a surface that finds no bucket
    # never reaches its raise point
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(world.bucket.parent))
    tmp = world.root
    # the fixture's own clean copy: earlier drives have dirtied `tmp`, and a
    # raise point that is never reached (no buckets) would pass vacuously
    pristine = drive.Pristine.adopt(tmp, tmp.parent / (tmp.name + "-keep"))
    for surface, tag in CASES:
        if surface not in converted or (surface, tag) in NO_ITEM_READ:
            continue
        _found, results = drive_case(surface, tag, world, pristine)
        for label, res in results:
            text = res.text()
            for shown in (VISIBLE, OTHER, "stays visible", "SENTINEL"):
                assert shown not in text, (surface, tag, label)
            if surface.startswith("cli:"):
                assert res.rc == 2, (surface, tag, label, res.rc)
                assert text.strip().count("\n") == 0, (surface, tag, label)
            elif surface.startswith("mcp:"):
                assert res.rc == "raised", (surface, tag, label)
            else:
                assert res.chunks == ["None"], (surface, tag, label)


# ===========================================================================
# C: the hook scripts never read an item key
# ===========================================================================
HOOKS_DIR = Path(daimon_briefing.__file__).parent / "_hooks"
ITEM_KEYS = {f.key for f in schema.ITEM_FIELDS}


def _item_key_reads(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in ITEM_KEYS):
            hits.append((node.lineno, node.slice.value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in ITEM_KEYS):
            hits.append((node.lineno, node.args[0].value))
    return hits


def test_hook_scripts_never_read_an_item_key():
    scripts = sorted(HOOKS_DIR.glob("*.py"))
    assert len(scripts) >= 10
    found = {p.name: _item_key_reads(p) for p in scripts}
    assert {k: v for k, v in found.items() if v} == {}


def test_the_item_key_scan_finds_what_it_bans(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("cp = {}\na = cp['open_questions']\n"
                     "b = cp.get('recent_decisions')\nc = cp['session_id']\n")
    assert [k for _l, k in _item_key_reads(probe)] == [
        "open_questions", "recent_decisions"]


def test_hook_lib_has_no_read_meta_yet():
    """Recorded gap: pointer metadata should reach the scripts through one
    `_daimon_hook_lib.read_meta`; it does not exist, so the scripts parse
    pointers themselves (checked above to touch no item key)."""
    lib = (HOOKS_DIR / "_daimon_hook_lib.py").read_text(encoding="utf-8")
    assert "def read_meta" not in lib
