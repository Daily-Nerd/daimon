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
onward deletes them. The recall surfaces read through the judged core of
`recall` (`CONVERTED_RECALL`): the index they query is built and queried with
`view.judge`, the view's one verdict per bucket.

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
from daimon_briefing import cli, mcp_tools, schema, store
from daimon_ui import server
from tests import _leaves, _sentinel_drive as drive, _sentinel_world as sw
from tests._sentinel_dests import DESTS

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
    """Every route the viewer serves: the keys of `server.ROUTES`, the one
    dispatch table (#1132 PR 8a)."""
    return list(server.ROUTES)


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
# a needle that matches ONLY the quarantined question: a withheld match is
# ignored entirely, so this reads as no match (the needle is the caller's own
# words, not the sentinel token)
case("cli:anchor", "attach",
     A("anchor", "mod.py", "fn", "--attach", "the question sentinel"),
     dests=("attach",), rc=(1,))
# a needle that matches a quarantined question AND a visible one: the visible
# one takes the anchor, the withheld one is neither named nor counted
case("cli:anchor", "attach-withheld",
     A("anchor", "mod.py", "fn", "--attach", "question"),
     dests=("attach",), rc=(0,))
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
case("cli:why", "idforgot", A("why", I("idforgot")),
     shows="[withheld: forgotten]")
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
case("cli:diff", "range", A("diff", "--from", "1", "--to", "0"),
     dests=("frm", "to"), rc=(0, 1, 2))
case("cli:diff", "slug", A("diff", slug_opt), dests=("slug",))
for kind in ("question", "decision", "belief", "uncertainty"):
    case("cli:blame", kind, A("blame", I(kind)), rc=(0, 1),
         dests=("item_id",))
case("cli:blame", "json", A("blame", I("question"), "--json"), rc=(0, 1),
     dests=("json",))
case("cli:blame", "idforgot", A("blame", I("idforgot")),
     shows="[withheld: forgotten]")
case("cli:blame", "tombstone", A("blame", I("decision")),
     shows="[withheld: forgotten]")
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

# the folded prose of 11c: an anchor the guard matches, a state the default
# listing leaves out, and the --json form of every prose verb that has one (a
# printer that masks the text lines and dumps the record raw leaks here)
case("cli:refute guard", "anchor", A("refute", "guard", "zzzz", "--anchor",
     lambda w: sw.TEXTS["topic"]), dests=("anchor",))
case("cli:refute list", "overturned", A("refute", "list", "--state",
     "overturned"), dests=("state",))
case("cli:ruling list", "overturned", A("ruling", "list", "--state",
     "overturned"), dests=("state",))


def _json_variant(surface, tag="default"):
    base = CASES[(surface, tag)]
    CASES[(surface, "json" if tag == "default" else tag + "-json")] = Case(
        (lambda b: lambda w: [*b.args(w), "--json"])(base), stdin=base.stdin,
        tty=base.tty, dests=("json",), rc=base.rc, shows=None,
        axes=tuple(a for a in base.axes if a == "STDIN_TTY"))


for _surface, _tag in (
        ("cli:refute list", "default"), ("cli:refute list", "overturned"),
        ("cli:refute show", "id"), ("cli:refute search", "default"),
        ("cli:refute guard", "default"), ("cli:refute guard", "anchor"),
        ("cli:refute ratify", "id"), ("cli:refute revise", "id"),
        ("cli:refute overturn", "id"), ("cli:ruling list", "default"),
        ("cli:ruling list", "inherited"), ("cli:ruling list", "overturned"),
        ("cli:ruling show", "id"), ("cli:ruling ratify", "id"),
        ("cli:ruling revise", "id"), ("cli:ruling retire", "id"),
        ("cli:amend list", "default"), ("cli:trust list", "default"),
        ("cli:trust show", "id"), ("cli:request list", "default"),
        ("cli:request inbox", "default")):
    _json_variant(_surface, _tag)

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
     rc=(0, 1, 2), dests=("dry_run", "project"), axes=("STDIN_TTY",))
case("cli:forget", "republish", A("forget", "--republish"), tty=True,
     rc=(0, 1, 4), dests=("project", "republish"))
# a real forget of the quarantined sentinel by its exact id, from a person's
# terminal: the receipt prints the content hash of the value just forgotten
case("cli:forget", "receipt", A("forget", I("question"), "--reason", "r"),
     tty=True, rc=(0, 4), shows="forgot ")
# a tombstoned id whose value is still on disk is no match on either channel
case("cli:forget", "idforgot", A("forget", I("idforgot"), "--dry-run"),
     tty=True, rc=(1,), shows="no item matches", dests=("dry_run",))
case("cli:forget", "idforgot-agent", A("forget", I("idforgot"), "--dry-run"),
     tty=False, rc=(1,), shows="no item matches", dests=("dry_run",))
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
no_items("serves the fixed page or an allowlisted static asset; no store read",
         "http:/", "http:/static/")

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
case("http:/api/why", "idforgot",
     lambda w: f"/api/why?id={w.ids['idforgot']}&source=1", shows="forgotten")
case("http:/api/grid", "default", lambda w: "/api/grid")
case("http:/api/refutations", "default", lambda w: "/api/refutations")
case("http:/api/relations", "default", lambda w: "/api/relations")
case("http:/api/relations", "item",
     lambda w: f"/api/relations?id={w.ids['question']}")
case("http:/api/ledger", "item", lambda w: f"/api/ledger?id={w.ids['question']}")
case("http:/api/session", "sid", lambda w: "/api/session?sid=S-2")
case("http:/api/activity", "default", lambda w: "/api/activity")

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
        lines_before = (drive.snapshot_lines(world.root)
                        if (surface, tag) in WRITE_CARRY else None)
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
        # a declared write-carry case keeps withheld bytes in the checkpoint
        # files it rewrites on purpose: those files are not read output. The
        # ledger bytes it writes (events, trust sidecar, team sidecar, usage
        # log) are scanned like any other run's.
        carry = (surface, tag) in WRITE_CARRY
        res.written = drive.written_since(
            world.root, before,
            skip=(lambda p: p.suffix == ".json") if carry else None,
            lines=lines_before)
        blob = res.text() + "\n" + b"\n".join(res.written).decode(
            "utf-8", errors="replace")
        leaks |= {(surface, tag, k) for k in sw.leaked_kinds(blob)}
        if (surface, tag) not in KEY_EXEMPT:
            leaks |= {(surface, tag, k) for k in sw.leaked_keys(blob)}
        results.append((label, res))
    return leaks, results


# ===========================================================================
# Allowlists (seeded from the run; shrink-only)
# ===========================================================================
# {(surface, kind)}: seeded from the run, each must still leak, nothing else may
KNOWN_LEAKS: set = {
    *{("cli:amend list", k) for k in ("contradiction", "question",)},
    *{("cli:decide", k) for k in ("peerforgot", "question", "topic",)},
    *{("cli:request accept", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request done", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request inbox", k) for k in ("contradiction", "peerforgot", "question", "topic",)},
    *{("cli:request list", k) for k in ("contradiction", "peerforgot", "question", "topic",)},
    *{("cli:request needs-info", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request reject", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request reply", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request suppress", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:request-inject", k) for k in ("contradiction", "peerforgot", "question", "topic",)},
    *{("cli:ruling list", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling ratify", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling retire", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling revise", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:ruling show", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:trust list", k) for k in ("contradiction", "question", "topic",)},
    *{("cli:trust show", k) for k in ("contradiction", "topic",)},
    *{("http:/api/refutations", k) for k in ("contradiction", "peerforgot", "question", "topic",)},
    *{("mcp:requests_inbox", k) for k in ("contradiction", "peerforgot", "question", "topic",)},
}


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


def test_viewer_routes_are_the_table_and_the_owner_less_ones_show_no_items():
    assert len(viewer_routes()) == 16 == len(set(viewer_routes()))
    assert {"http:" + k for k, r in server.ROUTES.items()
            if r.owner is None} == {s for s in NO_ITEMS if s.startswith("http:")}


def test_dests_match_the_parser_and_are_classified():
    import argparse
    for leaf, parser in _leaves.leaf_parsers(cli.build_parser()).items():
        dests = {a.dest for a in parser._actions
                 if not isinstance(a, argparse._HelpAction)}
        assert dests == set(DESTS[" ".join(leaf)]), leaf
    assert sum(len(v) for v in DESTS.values()) == 339
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
    seen = {(s, kind) for (s, kind) in leaks if not kind.startswith("key:")}
    print("LEAKS", sorted(seen))
    assert seen == KNOWN_LEAKS


# A forgotten value's content key names the value it hashes (scar 0119): a
# raw tombstone status or scrub marker printed by a reader brings it back
# without the value sentinel noticing. Every case is scanned for the keys too.
# `KNOWN_KEY_LEAKS` is shrink-only like KNOWN_LEAKS. `KEY_EXEMPT` declares the
# cases whose job is to name a key: a person-run proof or publish tool.
KNOWN_KEY_LEAKS: set = set()
KEY_EXEMPT = {
    ("cli:audit privacy", "default"):
        "the residue audit names the content hash of each residue it proves",
    ("cli:audit privacy", "all"):
        "the residue audit names the content hash of each residue it proves",
    ("cli:forget", "receipt"):
        "the receipt prints the content hash of the value just forgotten, "
        "which is the tombstone key the team sidecar carries too; it is a "
        "person's command (the agent run is refused)",
    ("cli:forget", "republish"):
        "re-publishing a tombstone writes its key into the team sidecar",
}


def test_no_surface_hands_back_a_forgotten_key(world_run):
    _world, leaks, _details = world_run
    seen = {(s, kind) for (s, kind) in leaks if kind.startswith("key:")}
    print("KEY LEAKS", sorted(seen))
    assert seen == KNOWN_KEY_LEAKS


def test_the_prose_columns_are_planted_in_the_world(world_run, monkeypatch):
    """Anti-vacuity for 11c: every declared folded prose path of a refutation
    or request in the world carries a sentinel (so a printer that skips it
    leaks), except `revision_proposed.note`, which no writer can set."""
    from daimon_briefing import refutations, requests, surfaces
    from tests.test_folded_prose_census import _leaves
    world, _leaks, _details = world_run
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(world.bucket.parent))
    seen = {name: set() for name in ("refutations.jsonl", "requests.jsonl")}
    folded = {
        "refutations.jsonl": list(refutations.records(
            project_dir=world.project).values()) + refutations.guard(
                "zzzz", anchors=[sw.TEXTS["topic"]],
                project_dir=world.project),
        "requests.jsonl": list(requests.records(
            project_dir=world.project).values())}
    for name, records in folded.items():
        for record in records:
            for path, value in _leaves(record):
                if any(tok.lower() in value.lower()
                       for tok in [*sw.TOKENS.values(), sw.PEER_TOKEN]):
                    seen[name].add(path)
    for name, planted in seen.items():
        s = surfaces.bucket_ledger(name)
        declared = {surfaces.path_string(fp)
                    for fp in s.prose + s.folded_prose}
        missing = declared - planted
        # `note` and `act_author` are row fields a fold renames or does not
        # keep, `revision_proposed.note` has no writer and `from_label` would
        # name the bucket itself
        assert missing <= {"revision_proposed.note", "note", "act_author",
                           "from_label"}, (
            name, sorted(missing))


def test_the_forgotten_keys_are_stored_in_the_world(world_run):
    """Anti-vacuity: the tombstones the scan looks for are on disk."""
    world, _leaks, _details = world_run
    raw = b"\n".join(p.read_bytes() for p in world.bucket.rglob("events.jsonl"))
    assert sw.leaked_keys(raw) == {f"key:{n}" for n in sw.KEY_TOKENS}


def test_the_key_scan_finds_what_it_bans():
    assert sw.leaked_keys("x " + sw.KEY_TOKENS["decision"].upper()) == {
        "key:decision"}
    assert sw.leaked_keys(b"nothing here") == set()
    assert set(sw.KEY_TOKENS) == set(sw.FORGOTTEN) | {"idforgot"}


def test_the_key_exemptions_name_real_cases():
    for key in KEY_EXEMPT:
        assert key in CASES, key


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
    ("cli:forget", "receipt"):
        "forgets a quarantined value by exact id; an agent is refused",
}
# Bytes that carry a checkpoint forward on purpose: not read output.
WRITE_CARRY = {
    ("cli:serialize", "carry"):
        "carries the previous checkpoint into the new one (scar 0096 plants)",
    ("store._dual_write_team", "mirror"):
        "copies the checkpoint into the author's team mirror",
    ("cli:team sync", "publish"):
        "publishes own author dirs to the sidecar remote",
    ("cli:forget", "receipt"):
        "rewrites the live checkpoint without the forgotten value; every other "
        "byte, the withheld ones included, stays (tests/test_forget_*.py)",
    ("cli:anchor", "attach-withheld"):
        "rewrites the checkpoint whole; the bytes the view withholds stay in "
        "place, which is the point (tests/test_attach_anchor.py)",
}


def test_exemptions_name_real_cases():
    for key in HUMAN_CHANNEL:
        assert key in CASES, key
    for surface, tag in WRITE_CARRY:
        assert (surface in NO_ITEMS or surface.startswith("store.")
                or (surface, tag) in CASES), (surface, tag)


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
# `view.open`; `open`, `suppressed`, `projects` (with `peek` under it),
# `pointers`, `sessions`, `open_sessions`, `events` and `verifications` are
# the view's own projections.
# The MCP projects tool reaches `projects` through `cli.projects_rows`, so the
# module that calls it is cli/projects.py; the viewer's routes reach theirs
# through `daimon_ui/reader.py`.
PREPARE = frozenset({"prepare", "_prepared"})
PEEK = frozenset({"projects", "peek"})
VIEWER = "../daimon_ui/reader.py"
CONVERTED = {
    "cli:brief": ("cli/brief.py", PREPARE),
    "cli:loops": ("cli/lifecycle.py", PREPARE),
    "cli:projects": ("cli/projects.py", PEEK),
    "cli:status": ("cli/status.py", frozenset({"suppressed"})),
    "mcp:daimon_brief": ("mcp_tools.py", PREPARE),
    "mcp:daimon_projects": ("cli/projects.py", PEEK),
    "hook:pre_llm_call": ("hooks.py", PREPARE),
    "http:/api/projects": (VIEWER, frozenset({"projects"})),
    "http:/api/checkpoints": (VIEWER, frozenset({"pointers", "sessions"})),
    "http:/api/checkpoint/": (VIEWER, frozenset({"pointers"})),
    "http:/api/history": (VIEWER, frozenset({"sessions"})),
    "http:/api/diff": (VIEWER, frozenset({"sessions", "open_sessions"})),
    "http:/api/biography": (VIEWER, frozenset({"sessions", "open_sessions"})),
    "http:/api/grid": (VIEWER, frozenset({"sessions", "open_sessions"})),
    "http:/api/ledger": (VIEWER, frozenset({"sessions", "open_sessions"})),
    "http:/api/session": (VIEWER, frozenset({"sessions", "open_sessions"})),
    "http:/api/activity": (VIEWER, frozenset({"sessions", "events"})),
    # 11a: the exact-id history verbs. `why` and `/api/why` compose
    # `inspector.inspect_item` over `view.lineage`; `blame` reads the lineage
    # itself; `diff` judges two pointer generations over one snapshot.
    "cli:why": ("inspector.py", frozenset({"lineage"})),
    "http:/api/why": ("inspector.py", frozenset({"lineage"})),
    "cli:blame": ("cli/history.py", frozenset({"lineage"})),
    "cli:diff": ("cli/history.py", frozenset({"snapshot", "pointers"})),
    # 11b: the binding verbs bind through `view.match` and nothing else; the
    # relations verbs and route read the edges through `view.relations`.
    "cli:resolve": ("cli/lifecycle.py", frozenset({"match"})),
    "cli:reverify": ("cli/lifecycle.py", frozenset({"match"})),
    "cli:amend propose": ("cli/amend.py", frozenset({"match"})),
    "cli:anchor": ("cli/brief.py", frozenset({"match"})),
    "cli:forget": ("cli/lifecycle.py", frozenset({"label"})),
    "cli:audit quotes": ("cli/audit.py", frozenset({"open_sessions"})),
    "cli:audit-quotes": ("cli/audit.py", frozenset({"open_sessions"})),
    "cli:relations list": ("cli/relations_cmd.py", frozenset({"relations"})),
    "cli:relations show": ("cli/relations_cmd.py", frozenset({"relations"})),
    "http:/api/relations": ("../daimon_ui/server.py", frozenset({"relations"})),
    # 11c: the prose verbs print what they read through `view.masked`. The
    # call sits in the helper the verb modules share, `cli/_ledger.py`.
    **{f"cli:refute {verb}": ("cli/_ledger.py", frozenset({"masked"}))
       for verb in ("list", "show", "search", "guard", "ratify", "revise",
                    "overturn")},
}

# The recall surfaces: they do not open a checkpoint, they query the derived
# index, which `view.judge` keeps free of withheld rows and re-judges per row.
# The names are the judged core they call (`recall.query`, `recall.suggest`).
CONVERTED_RECALL = {
    "cli:recall": ("cli/search.py", frozenset({"query"})),
    "cli:recall-inject": ("cli/inject.py", frozenset({"suggest"})),
    "cli:action-recall": ("cli/action_recall.py", frozenset({"suggest"})),
    "mcp:daimon_recall": ("mcp_tools.py", frozenset({"query"})),
    "http:/api/recall": ("../daimon_ui/server.py", frozenset({"query"})),
}

# Shrink-only: surfaces that do not yet read through `view.open`. Each PR from
# 7a onward deletes entries as readers convert; an empty set is the goal.
UNCONVERTED = {s for s, _ in CASES} - set(CONVERTED) - set(CONVERTED_RECALL)


# Cases of a converted surface that never read a checkpoint item: the status
# payload carries counts and health, never an item's text (only the
# `--suppressed` listing reads items), so there is no view call to fail.
NO_ITEM_READ = {("cli:status", "default"), ("cli:status", "json"),
                # `/api/checkpoint/S-1` is not a pointer ref: refused before
                # any read
                ("http:/api/checkpoint/", "session")}
# `anchor` without `--attach` prints the resolved anchor block and reads no item
NO_ITEM_READ |= {(s, t) for (s, t) in CASES
                 if s == "cli:anchor" and t != "attach"}
# `forget --republish` publishes tombstone keys already recorded; it binds no item
NO_ITEM_READ |= {("cli:forget", "republish")}


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
    assert not UNCONVERTED & set(CONVERTED_RECALL)
    assert not set(CONVERTED) & set(CONVERTED_RECALL)
    assert (UNCONVERTED | set(CONVERTED) | set(CONVERTED_RECALL)
            == {s for s, _ in CASES})


def test_a_converted_surface_reaches_the_view():
    for surface, (rel, names) in CONVERTED.items():
        assert _calls_view(rel, names), (surface, rel)


def test_a_recall_surface_reaches_the_judged_core():
    for surface, (rel, names) in CONVERTED_RECALL.items():
        assert _calls_view(rel, names), (surface, rel)


def test_a_converted_surface_renders_nothing_when_view_open_raises(
        world_run, monkeypatch):
    from daimon_briefing import view
    world, _l, _d = world_run
    converted = {s for s, _ in CASES} - UNCONVERTED - set(CONVERTED_RECALL)
    assert converted == set(CONVERTED)

    def boom(*_a, **_k):
        raise RuntimeError("view.open failed")

    for name in ("open", "peek", "projects", "pointers", "sessions",
                 "open_sessions", "events", "verifications", "snapshot",
                 "judge", "lookup_many", "lineage", "match", "relations", "label",
                 "masked"):
        monkeypatch.setattr(view, name, boom)
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
                # the deprecated `audit-quotes` alias prints its rename note first
                lines = [ln for ln in text.strip().splitlines()
                         if ln.strip() and not ln.startswith("note: ")]
                assert len(lines) == 1, (surface, tag, label)
            elif surface.startswith("mcp:"):
                assert res.rc == "raised", (surface, tag, label)
            elif surface.startswith("http:"):
                assert res.rc == 500, (surface, tag, label, res.rc)
            else:
                assert res.chunks == ["None"], (surface, tag, label)


def _drive_recall_surfaces(world, monkeypatch):
    """Every case of every recall surface, run on the world's own store."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(world.bucket.parent))
    tmp = world.root
    pristine = drive.Pristine.adopt(tmp, tmp.parent / (tmp.name + "-keep"))
    for surface, tag in CASES:
        if surface in CONVERTED_RECALL:
            _found, results = drive_case(surface, tag, world, pristine)
            for label, res in results:
                yield surface, tag, label, res


def test_a_recall_surface_fails_closed_and_uniformly_when_the_judge_raises(
        world_run, monkeypatch):
    """The recall surfaces ask `view.judge` for every bucket they touch. A
    judge that fails shows no sentinel and no visible control text, and each
    host fails the way the converted readers do: the CLI rc 2 and one line,
    the MCP tool a ToolError, the viewer a 500. The two prompt hooks print
    nothing."""
    from daimon_briefing import view
    world, _l, _d = world_run

    def boom(*_a, **_k):
        raise RuntimeError("view.judge failed")

    monkeypatch.setattr(view, "judge", boom)
    ran = 0
    for surface, tag, label, res in _drive_recall_surfaces(world, monkeypatch):
        ran += 1
        text = res.text()
        for shown in (VISIBLE, OTHER, "unrelated", "SENTINEL"):
            assert shown not in text, (surface, tag, label)
        where = (surface, tag, label, res.rc, text[:80])
        if surface == "cli:recall":
            assert res.rc == 2, where
            assert text.strip().count("\n") == 0, where
        elif surface.startswith("mcp:"):
            assert res.rc == "raised", where
            assert "ToolError" in res.error or "could not be read" in text, where
        elif surface.startswith("http:"):
            assert res.rc == 500, where
        else:   # recall-inject, action-recall: hook backends print nothing
            assert text.strip() == "", where
    assert ran >= 10


def test_a_recall_surface_shows_nothing_when_the_judge_withholds_everything(
        world_run, monkeypatch):
    """Behavior, not names: with a judge that withholds every row, no recall
    surface shows an item, the visible control included."""
    import dataclasses

    from daimon_briefing import view
    world, _l, _d = world_run
    closed = view.Judge(dataclasses.replace(view.Snapshot.empty(), closed=True))
    monkeypatch.setattr(view, "judge", lambda slug, **_k: closed)
    ran = 0
    for surface, tag, label, res in _drive_recall_surfaces(world, monkeypatch):
        ran += 1
        text = res.text()
        for shown in (VISIBLE, OTHER, "unrelated decision", "SENTINEL"):
            assert shown not in text, (surface, tag, label)
    assert ran >= 10


# A forgotten ID whose value no tombstone key names: only the id rule withholds
# it. It is asserted absent on every surface that reads through the view or the
# judged recall core. The surfaces that still read the store themselves are
# listed (shrink-only, like KNOWN_LEAKS): each converts in a later PR.
# The binding verbs (`resolve`, `reverify`, `amend propose`, `anchor`,
# `forget`) judge every candidate through the view, so no surface prints it.
def _id_leaking_surfaces(details):
    out = set()
    for (surface, tag), results in details.items():
        for _label, res in results:
            blob = res.text() + "\n" + b"\n".join(res.written).decode(
                "utf-8", errors="replace")
            if sw.leaked_id(blob):
                out.add(surface)
    return out


def test_the_id_forgotten_sentinel_exists_and_is_absent_where_judged(
        world_run):
    world, _leaks, details = world_run
    assert world.ids["idforgot"]
    raw = b"\n".join(p.read_bytes() for p in world.bucket.parent.rglob("*.json"))
    assert sw.ID_TOKEN.encode() in raw        # anti-vacuity: it is stored
    leaking = _id_leaking_surfaces(details)
    judged = set(CONVERTED) | set(CONVERTED_RECALL)
    assert leaking & judged == set(), sorted(leaking & judged)
    assert leaking == set(), sorted(leaking)


def test_the_built_index_holds_no_sentinel_byte_after_an_upgrade(
        world_run, monkeypatch, tmp_path):
    """The recall index file is a byte channel of its own. A schema-9 index
    holding a quarantined sentinel is replaced on first use, and the rebuilt
    file carries no sentinel of any kind, the id-forgotten one included."""
    import sqlite3

    from daimon_briefing import config, recall
    world, _l, _d = world_run
    pristine = drive.Pristine.adopt(world.root, world.root.parent
                                    / (world.root.name + "-keep"))
    pristine.restore()
    # scar 0114: pin the world's directories, or the surface finds no bucket
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(world.bucket.parent))
    monkeypatch.setenv("DAIMON_TEAM_DIR",
                       str(world.bucket.parent.parent / "team"))
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    db = tmp_path / "seeded.db"
    monkeypatch.setenv("DAIMON_RECALL_DB", str(db))
    conn = sqlite3.connect(str(db))
    recall._init_schema(conn)
    conn.execute("INSERT INTO items (text, kind, project_slug, session_id,"
                 " created) VALUES (?, 'question', ?, 'S-1', 1.0)",
                 (sw.TEXTS["question"], world.bucket.name))
    conn.execute("INSERT INTO items_fts(rowid, text, quote, scene)"
                 " VALUES (1, ?, '', '')", (sw.TEXTS["question"],))
    conn.execute("INSERT INTO meta VALUES ('schema_version', '9')")
    conn.execute("INSERT INTO meta VALUES ('fingerprint', ?)",
                 (recall._fingerprint(),))
    conn.commit()
    conn.close()
    assert sw.TOKENS["question"].encode() in db.read_bytes()
    rows = recall.search("unrelated decision", project_dir=world.project)
    assert {r["text"] for r in rows} == {"an unrelated decision stays visible"}
    blob = config.recall_db().read_bytes()
    assert sw.leaked_kinds(blob) == set()
    assert not sw.leaked_id(blob)
    assert b"an unrelated decision stays visible" in blob


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


# ===========================================================================
# diff and blame over a RESTATED item (11a)
# ===========================================================================
# The shared world's checkpoints repeat their texts, so a generation pair never
# changes an item's wording. These cases build the one writer that does (carry's
# twin path) in a store of their own and drive the verbs against it: one id,
# two wordings, each carrying a sentinel token. A reader leaks when the token
# of a wording it may not show appears in anything it printed or wrote.

OLD_WORDING = f"{sw.TOKENS['question']} the retry budget stays at six attempts per request"
NEW_WORDING = f"{sw.TOKENS['belief']} the retry budget stays six attempts per request overall"


@pytest.fixture
def restated(tmp_path, monkeypatch):
    from daimon_briefing import carry, config, normalize, trust
    from daimon_briefing.surfaces import Writer
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DAIMON_PROJECT_DIR", str(proj))
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    project = str(proj)

    def cp(sid, created, text):
        return {"session_id": sid, "created": created,
                "working_context": {
                    "active_topic": {"text": "restated", "trust": "inferred"},
                    "recent_decisions": [{"text": text, "trust": "inferred"}]},
                "epistemic_snapshot": {}}

    first = cp("S-1", "2026-09-01T10:00:00Z", OLD_WORDING)
    assert store.write_checkpoint("S-1", first, project_dir=project,
                                  writer=Writer.HUMAN)
    native = cp("S-2", "2026-09-01T11:00:00Z", NEW_WORDING)
    merged = carry.merge(native, first, now=1_800_000_000.0)
    assert store.write_checkpoint("S-2", merged, project_dir=project,
                                  writer=Writer.HUMAN)
    item_id = first["working_context"]["recent_decisions"][0]["id"]
    assert merged["working_context"]["recent_decisions"][0]["id"] == item_id

    class World:
        pass

    w = World()
    w.project, w.item_id = project, item_id
    w.root = tmp_path
    w.bucket = config.checkpoint_dir() / store.project_slug(project)

    def quarantine(text, kind):
        return trust.propose(text=text, kind=kind, reason="fabricated",
                             evidence=["issue:1"], channel="cli-tty",
                             project_dir=project)

    def forget_elsewhere(text):
        store.append_event("o-elsewhere", "forgotten:" + normalize.content_key(text),
                           kind="tombstone", tombstone=True,
                           project_dir=project, writer=Writer.HUMAN)

    w.quarantine, w.forget_elsewhere = quarantine, forget_elsewhere
    return w


def _drive(w, argv):
    before = drive.snapshot_sizes(w.root)
    res = drive.run_cli(argv, stdin_tty=True)
    res.written = drive.written_since(w.root, before)
    blob = res.text() + "\n" + b"\n".join(res.written).decode(
        "utf-8", errors="replace")
    return res, blob


VERBS = (("diff",), ("diff", "--json"))


@pytest.mark.parametrize("verb", VERBS, ids=lambda v: "-".join(v))
def test_a_quarantined_old_wording_never_prints_in_a_diff(restated, verb):
    restated.quarantine(OLD_WORDING, "decision")
    res, blob = _drive(restated, [*verb, f"--project={restated.project}"])
    assert res.rc == 0
    assert "question" not in sw.leaked_kinds(blob)
    assert NEW_WORDING.split(" ", 1)[1] in blob       # the visible side shows


@pytest.mark.parametrize("verb", VERBS, ids=lambda v: "-".join(v))
def test_a_quarantined_new_wording_never_prints_in_a_diff(restated, verb):
    restated.quarantine(NEW_WORDING, "decision")
    res, blob = _drive(restated, [*verb, f"--project={restated.project}"])
    assert res.rc == 0
    assert "belief" not in sw.leaked_kinds(blob)
    assert OLD_WORDING.split(" ", 1)[1] in blob


@pytest.mark.parametrize("verb", VERBS, ids=lambda v: "-".join(v))
def test_a_wording_quarantined_under_another_kind_is_masked_in_was(
        restated, verb):
    restated.quarantine(OLD_WORDING, "belief")
    res, blob = _drive(restated, [*verb, f"--project={restated.project}"])
    assert res.rc == 0
    assert "question" not in sw.leaked_kinds(blob)
    assert "was [withheld: quarantine" in blob


@pytest.mark.parametrize("verb", VERBS, ids=lambda v: "-".join(v))
def test_an_erased_old_wording_leaves_no_trace_in_a_diff(restated, verb):
    restated.forget_elsewhere(OLD_WORDING)
    res, blob = _drive(restated, [*verb, f"--project={restated.project}"])
    assert res.rc == 0
    assert sw.leaked_kinds(blob) - {"belief"} == set()
    assert "forgot" not in blob.lower()
    assert sw.leaked_keys(blob) == set()
    from daimon_briefing import normalize
    assert normalize.content_key(OLD_WORDING) not in blob


@pytest.mark.parametrize("flags", [(), ("--json",)], ids=["text", "json"])
def test_blame_of_a_restated_item_hides_the_quarantined_wording(
        restated, flags):
    restated.quarantine(OLD_WORDING, "decision")
    res, blob = _drive(restated, ["blame", restated.item_id, *flags,
                                  f"--project={restated.project}"])
    assert res.rc == 0
    assert "question" not in sw.leaked_kinds(blob)
    assert "withheld" in blob


@pytest.mark.parametrize("flags", [(), ("--json",)], ids=["text", "json"])
def test_blame_of_an_erased_restated_item_says_so_and_no_key(
        restated, flags):
    from daimon_briefing import normalize
    restated.forget_elsewhere(NEW_WORDING)
    res, blob = _drive(restated, ["blame", restated.item_id, *flags,
                                  f"--project={restated.project}"])
    assert res.rc == 0
    assert "belief" not in sw.leaked_kinds(blob)
    assert normalize.content_key(NEW_WORDING) not in blob
    assert "[withheld: forgotten]" in blob or '"reason": "forgotten"' in blob
