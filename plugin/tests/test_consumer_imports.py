"""The names the one external consumer imports from daimon_briefing (#1239).

`daimon_briefing.api` is the declared contract for other programs. The one
consumer I run imports `api` and also reaches into seven internal
modules: `store`, `config`, `requests`, `recall`, `normalize`, `refutations`
and `pending`. Nothing pinned that boundary, so every daimon bump meant
re-reading those modules to find what moved. This table is the pin.

One row per (module, attribute) the consumer uses, with a verdict:

    contract   the name is exported by `api`; the consumer may rely on it.
               For a row reached through an internal module (`pending.queue`),
               the api export must be the SAME object, or the consumer is
               silently calling something the contract does not cover.
    tolerated  internal; the consumer accepts breakage. The row still exists so
               a rename fails HERE, naming what the consumer does with it,
               instead of in the consumer's next bump.
    promote    should move into `api`. Listed only. An api row is added for a
               `promote` name only when an import has already broken a bump;
               no speculative rows. Once promoted, flip the row to `contract`
               (`test_promote_rows_are_not_already_exported` enforces that).

The `use` column is a short tag for WHAT the consumer does with the name. The
consumer is a private repository and daimon is public, so I keep its file
paths and line numbers out of this tree; the tag is what a failure prints, and
it is enough to find the call in the consumer. The test never reads the
consumer tree in CI, which does not have it.

Rebuilding the table after a consumer change is the opt-in test at the bottom:
`DAIMON_CONSUMER_SRC=<consumer src dir> pytest tests/test_consumer_imports.py`
walks that tree with `ast` only and compares what it finds with `PINS`. The
walk is scope-aware (a function-local `from daimon_briefing import config`
does not alias a module-level `config` the consumer owns), counts a
`from daimon_briefing.x import y` as a use of `x.y`, and records
`getattr(alias, "literal")` as a use. A `getattr(alias, variable)` is dynamic
dispatch: the walk cannot name the attribute, so it marks the module and the
comparison lets pinned rows on that module stand. Docstring and comment
mentions are not uses. Private names (a leading underscore) are never pinned.
"""

import ast
import importlib
import os
from pathlib import Path
from typing import NamedTuple

import pytest

from daimon_briefing import api

CONTRACT = "contract"
TOLERATED = "tolerated"
PROMOTE = "promote"
VERDICTS = (CONTRACT, TOLERATED, PROMOTE)


class Pin(NamedTuple):
    module: str   # `daimon_briefing.<module>`
    attr: str
    verdict: str
    use: str      # what the consumer does with it, never where it lives


# Census of the consumer at 2026-10-26, daimon at ade5738.
PINS = (
    # api: the declared contract; the consumer imports these directly.
    Pin("api", "Briefing", CONTRACT, "parsed briefing handed to the channel poster"),
    Pin("api", "parse_briefing", CONTRACT, "parsing the brief text for the channel"),
    Pin("api", "masked", CONTRACT, "ruling anchor count check against the raw ledger"),
    Pin("api", "why", CONTRACT, "item receipt in the office"),
    Pin("api", "why_lines", CONTRACT, "item receipt lines in the office"),
    # pending: `queue` is `api.queue`; `MaskFailed` is what `api.queue` raises
    # when the judge fails, and the consumer must catch it, yet api does not
    # export it. The exception of a contract call belongs beside the call.
    Pin("pending", "queue", CONTRACT, "office pending queue"),
    Pin("pending", "MaskFailed", PROMOTE, "office pending queue failure, per room"),
    # requests: the two listings are api re-exports. The record reads
    # (`get`, `records`, `recipient_join`) sit beside `api.join` and
    # `api.inbox` but are not them. The verdict verbs and `open_request`
    # are writes, and writes are not part of the api surface by design.
    Pin("requests", "listing", CONTRACT, "office request listing"),
    Pin("requests", "inbox_listing", CONTRACT, "office inbox listing"),
    Pin("requests", "get", TOLERATED, "office request read by id"),
    Pin("requests", "records", TOLERATED, "office request revisions, one fold per bucket"),
    Pin("requests", "recipient_join", TOLERATED, "office read of requests addressed here"),
    Pin("requests", "open_request", TOLERATED, "office opening an ask to the assistant"),
    # The four verbs are dispatched by name, `getattr(requests, verb)`, so no
    # `.accept` appears in the consumer's code and the census marks the
    # module as dynamic instead.
    Pin("requests", "accept", TOLERATED, "office verdict verb, dispatched by name"),
    Pin("requests", "reject", TOLERATED, "office verdict verb, dispatched by name"),
    Pin("requests", "needs_info", TOLERATED, "office verdict verb, dispatched by name"),
    Pin("requests", "suppress", TOLERATED, "office verdict verb, dispatched by name"),
    Pin("requests", "RequestError", TOLERATED, "office verdict refusal, re-exported"),
    # refutations: `CHANNEL_AUTHORITY` is the constant the consumer's human
    # gate is pinned to, and api already exports constants of this kind
    # (`LIVE_STATES`, `EXCLUDED_ROWS`). `listing` is the raw rulings read
    # that `api.masked(project, "refutations.jsonl", raw)` is fed with;
    # without it a consumer cannot build the argument of a contract call.
    # The rest are the ruling write path and its refusal: tolerated.
    Pin("refutations", "CHANNEL_AUTHORITY", PROMOTE, "human gate on the ui channel"),
    Pin("refutations", "listing", PROMOTE, "raw rulings read for the gate and the ratify path"),
    Pin("refutations", "get", TOLERATED, "ruling read before a verdict write"),
    Pin("refutations", "RefutationError", TOLERATED, "ruling write refusal"),
    Pin("refutations", "assert_ruling", TOLERATED, "ruling written in process on the signed channel"),
    Pin("refutations", "ratify", TOLERATED, "ruling ratify from the gate"),
    Pin("refutations", "revise", TOLERATED, "ruling revise from the gate"),
    Pin("refutations", "make_id", TOLERATED, "ruling id derived without the ledger"),
    # normalize: a pure transform the consumer uses to derive content keys.
    Pin("normalize", "content_key", TOLERATED, "content key derived for a ruling"),
    # store: `project_bucket` is documented as the host-facing way to check a
    # bucket before writing (#954); api exports its sibling `project_slug`
    # but not it, and three consumer sites call it.
    Pin("store", "project_bucket", PROMOTE, "bucket name check before the policy store is used"),
    # config: path and identity resolution. api's own docstring points callers
    # at `config.resolve_project_dir`, so config is a neighbour the contract
    # already leans on, but these three are not in api: tolerated.
    Pin("config", "checkpoint_dir", TOLERATED, "policy bucket path and office recall path"),
    Pin("config", "recall_db", TOLERATED, "recall database path in the office"),
    Pin("config", "author", TOLERATED, "author check before a ui verdict"),
    # recall: a different store (recall_db) from the ledgers api reads.
    Pin("recall", "search", TOLERATED, "recall search in the office"),
    Pin("recall", "RecallError", TOLERATED, "recall search failure in the office"),
)


def _pin_id(pin: Pin) -> str:
    return f"{pin.module}.{pin.attr}"


def _assert_pinned(pin: Pin) -> None:
    """The one check: the name exists, and a contract row reached through an
    internal module is the very object api exports."""
    module = importlib.import_module(f"daimon_briefing.{pin.module}")
    assert hasattr(module, pin.attr), (
        f"daimon_briefing.{pin.module}.{pin.attr} is gone ({pin.verdict}); "
        f"the consumer uses it for {pin.use}"
    )
    if pin.verdict == CONTRACT and pin.module != "api":
        assert getattr(api, pin.attr) is getattr(module, pin.attr), (
            f"api.{pin.attr} is not daimon_briefing.{pin.module}.{pin.attr}; "
            f"the consumer calls the module one for {pin.use}"
        )


@pytest.mark.parametrize("pin", PINS, ids=[_pin_id(p) for p in PINS])
def test_consumer_import_is_pinned(pin: Pin) -> None:
    _assert_pinned(pin)


def _pin(module: str, attr: str) -> Pin:
    return next(p for p in PINS if (p.module, p.attr) == (module, attr))


def test_pinned_name_gone_fails_naming_the_use(monkeypatch) -> None:
    """The failure a bump produces: the message says what the consumer does."""
    from daimon_briefing import pending

    monkeypatch.delattr(pending, "MaskFailed")
    with pytest.raises(AssertionError, match=r"office pending queue failure"):
        _assert_pinned(_pin("pending", "MaskFailed"))


def test_contract_row_that_is_not_the_api_object_fails(monkeypatch) -> None:
    """Only the api side is replaced; the module name the consumer calls stays."""
    monkeypatch.setattr(api, "queue", object())
    with pytest.raises(AssertionError, match=r"api\.queue is not daimon_briefing\.pending\.queue"):
        _assert_pinned(_pin("pending", "queue"))


def test_table_has_one_row_per_name_and_known_verdicts() -> None:
    keys = [(p.module, p.attr) for p in PINS]
    assert len(keys) == len(set(keys)), "duplicate (module, attr) rows"
    assert all(p.verdict in VERDICTS for p in PINS)
    assert all(not p.attr.startswith("_") for p in PINS), "private names are never pinned"


def test_contract_rows_are_exported_by_api() -> None:
    """A `contract` verdict is a claim about api.__all__, not about the module."""
    missing = [_pin_id(p) for p in PINS if p.verdict == CONTRACT and p.attr not in api.__all__]
    assert not missing, f"marked contract but not in api.__all__: {missing}"


def test_promote_rows_are_not_already_exported() -> None:
    """When a `promote` name lands in api, the row flips to `contract` or the
    table lies about what the consumer may rely on."""
    stale = [
        _pin_id(p) for p in PINS
        if p.verdict == PROMOTE
        and p.attr in api.__all__
        and getattr(api, p.attr) is getattr(importlib.import_module(f"daimon_briefing.{p.module}"), p.attr)
    ]
    assert not stale, f"already in api.__all__, flip to contract: {stale}"


# The census. Opt-in: it needs the consumer tree, which CI never has.

PACKAGE = "daimon_briefing"


class _Census(ast.NodeVisitor):
    """Collect (module, attr) uses of daimon_briefing in one file, by scope.

    Each function body is its own scope: an alias bound there does not leak to
    the rest of the file, and a parameter with the same name shadows an alias
    from an enclosing scope."""

    def __init__(self) -> None:
        self.found: set[tuple[str, str]] = set()
        self.dynamic: set[str] = set()
        self._scopes: list[dict[str, str | None]] = [{}]

    def _resolve(self, name: str) -> str | None:
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    def _in_scope(self, node: ast.AST, shadow: list[str]) -> None:
        self._scopes.append({name: None for name in shadow})
        self.generic_visit(node)
        self._scopes.pop()

    def _params(self, args: ast.arguments) -> list[str]:
        names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
        for extra in (args.vararg, args.kwarg):
            if extra is not None:
                names.append(extra.arg)
        return names

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._in_scope(node, self._params(node.args))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._in_scope(node, self._params(node.args))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._in_scope(node, self._params(node.args))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._in_scope(node, [])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == PACKAGE:
            for alias in node.names:
                self._scopes[-1][alias.asname or alias.name] = alias.name
        elif node.module and node.module.startswith(PACKAGE + "."):
            module = node.module.split(".", 1)[1]
            for alias in node.names:
                self.found.add((module, alias.name))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            module = self._resolve(node.value.id)
            if module is not None:
                self.found.add((module, node.attr))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                and len(node.args) >= 2 and isinstance(node.args[0], ast.Name)):
            module = self._resolve(node.args[0].id)
            if module is not None:
                key = node.args[1]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.found.add((module, key.value))
                else:
                    self.dynamic.add(module)
        self.generic_visit(node)


def _census(src: Path) -> tuple[set[tuple[str, str]], set[str]]:
    found: set[tuple[str, str]] = set()
    dynamic: set[str] = set()
    for path in sorted(src.rglob("*.py")):
        if any(part.startswith("test") for part in path.relative_to(src).parts):
            continue
        visitor = _Census()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found |= visitor.found
        dynamic |= visitor.dynamic
    return found, dynamic


@pytest.mark.skipif(
    "DAIMON_CONSUMER_SRC" not in os.environ,
    reason="set DAIMON_CONSUMER_SRC to the consumer's src directory to rebuild the census",
)
def test_census_matches_the_consumer_tree() -> None:
    found, dynamic = _census(Path(os.environ["DAIMON_CONSUMER_SRC"]))
    pinned = {(p.module, p.attr) for p in PINS}
    unpinned = sorted(found - pinned)
    gone = sorted(key for key in pinned - found if key[0] not in dynamic)
    dispatched = sorted(key for key in pinned - found if key[0] in dynamic)
    assert not unpinned and not gone, (
        f"consumer uses but PINS lacks: {unpinned}; "
        f"PINS has but consumer no longer uses: {gone}; "
        f"reached by dynamic dispatch, not checked: {dispatched}"
    )
