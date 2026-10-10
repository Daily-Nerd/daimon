"""One module runs every phase of a forget and reports every step it could not
vouch for (#1132 PR 12).

Before this module a forget was a procedure in the command: the tombstone, the
splice, five hand-wrapped purges, a composite ledger scrub and a derived-cache
refresh, with a different failure rule for each. Now the surface registry
(`surfaces.SURFACES`) is the only list of what a forget does. `STEPS` is
derived from it and from nothing else, `run` walks it in phase order, and every
step is judged by what it reports about its own reach:

- a deleter that returns a `jsonl.Reached` is believed as judged (its ledger was
  read again after the step);
- a plain count, or a `(count, error)` pair, is a reach of "nothing went wrong";
- an exception becomes an unreached step that names the exception TYPE and never
  its message, because a message can carry the value being forgotten.

`daimon forget`, `daimon ledger repair` (ledger steps only) and the finishing
verbs all call `run`, so a deleter added to the registry cannot be missed by one
of them. The module stays a library: it prints nothing and asks nothing, so the
command keeps everything that talks to a person.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import NamedTuple

from . import jsonl, normalize, refutations, schema, store, surfaces
from .surfaces import Writer


class Unreached(NamedTuple):
    """A surface forget could not vouch for (#1132 PR 10b, D10.5): what it is
    called, what a read judged it, how many torn lines it holds, and whether it
    carries plaintext (a surface that does not never fails forget; it is only
    reported)."""
    name: str
    state: jsonl.Health
    torn: int
    plaintext: bool = True
    detail: str = ""
    unscannable: str = ""


class Target(NamedTuple):
    """What one forget is about."""
    content_key: str
    # The id the person named; "" when the forget is finished from a standing
    # tombstone and no id is to hand.
    item_id: str = ""
    # Every id that held the value, the named one included, sorted.
    item_ids: tuple = ()
    project: str | None = None
    # The value itself, when the caller has it (a forget does, a repair or a
    # resume does not: they match by key).
    text: str = ""
    # The live checkpoint a forget rewrites, and its session id.
    checkpoint: dict | None = None
    session_id: str = ""
    reason: str = ""


class Planned(NamedTuple):
    """One entry of `STEPS`: a registry step and the row it sits on."""
    step: surfaces.Step
    shape: str
    ledger: bool    # a bucket ledger or quarantine sidecar: `ledger repair` runs it


class Done(NamedTuple):
    """What one step reported."""
    step: surfaces.Step
    result: object = None
    removed: tuple = ()
    n: int = 0                 # the count a purge or a scrub reported
    unreached: tuple = ()      # `Unreached` entries, none when it was reached
    kept: int = 0

    @property
    def reached(self) -> bool:
        return not self.unreached


@dataclass
class Outcome:
    """What a run did, in the order it did it."""
    done: list
    # True when the tombstone phase ran and its row was not written; nothing
    # else was touched.
    refused: bool = False
    # Set when the active-ruling gate stopped the run before any write.
    refusal: str = ""

    def get(self, fn: str):
        """The `Done` of the step that called `fn`, or None."""
        for item in self.done:
            if item.step.fn == fn:
                return item
        return None

    @property
    def unreached(self) -> list:
        return [u for item in self.done for u in item.unreached]


def _is_ledger_row(shape: str) -> bool:
    return (shape.startswith("checkpoints/{slug}/")
            and (shape.endswith(".jsonl")
                 or shape.endswith(surfaces.QUARANTINE_SIDECAR_SUFFIX)))


def _derive_steps() -> tuple:
    """Every step of the registry, once, in the order a forget runs them: by
    phase, then by the order the registry declares its rows. A step several
    rows name (the checkpoint copies, the windsurf store) is run once."""
    seen: set = set()
    found: list = []
    for row in surfaces.SURFACES:
        for step in row.steps:
            if step.fn in seen:
                continue
            seen.add(step.fn)
            found.append(Planned(step, row.shape, _is_ledger_row(row.shape)))
    order = {phase: i for i, phase in enumerate(surfaces.PHASES)}
    return tuple(sorted(found, key=lambda p: order[p.step.phase]))


STEPS: tuple = _derive_steps()


# ---- the two steps a forget owns itself ------------------------------------

def siblings(project, content_key: str, item_id: str = "") -> tuple:
    """Every id that held the value, the named one included, from every surface
    the project holds (live, prev-N and session files), sorted. Computed before
    any scrub, because afterwards there is nothing left to find."""
    ids = {str(item_id or "")}
    for _path, _section, _key, item in store.items_for_project(project):
        if normalize.content_key(item.get("text") or "") == content_key:
            ids.add(str(item.get("id") or ""))
    ids.discard("")
    return tuple(sorted(ids))


def write_tombstones(target: Target) -> jsonl.Reached:
    """The tombstone rows, in ONE append: the id row first, then the key row
    on a ref of its own, then one row per sibling id.

    The id row keeps the person-facing record (`blame` and `why` read it) and
    names the key in its status, so even a torn tail that loses the rest still
    withholds the value and a `--resume` can write what is missing. The key
    row is what a reopen on the id cannot reach, so only `unforget` lifts the
    value. The sibling rows make every id that held the value a standing
    tombstone, which is how a repair finds them again. The rows are built by
    the writer of every other events row, so each is admitted on its own.

    Nothing is written twice: a ref that already stands for this key is left
    alone, so a second forget of a value adds no second key row. The
    deletion exemption to the kill switch applies (#421)."""
    key = target.content_key
    status = f"forgotten:{key}"
    standing = store.project_standing(target.project).get(key, store.Standing())
    specs: list = []
    if target.item_id and target.item_id not in standing.ids:
        specs.append({"item_ref": target.item_id, "status": status,
                      "note": target.reason, "kind": "tombstone"})
    if not standing.key_refs:
        specs.append({"item_ref": store.new_key_ref(), "status": status,
                      "kind": "tombstone"})
    for sibling in target.item_ids:
        if sibling and sibling != target.item_id \
                and sibling not in standing.ids:
            specs.append({"item_ref": sibling, "status": status,
                          "kind": "tombstone"})
    if not specs:
        return jsonl.Reached(())
    ok = store.append_events(
        specs, project_dir=target.project, allow_disabled=True,
        tombstone=True, writer=Writer.HUMAN)
    written = tuple(spec["item_ref"] for spec in specs
                    if not store.is_key_ref(spec["item_ref"]))
    return jsonl.Reached(written if ok else (), reached=bool(ok))


def splice_checkpoint(target: Target) -> jsonl.Reached:
    """Remove the value from the live checkpoint and write it back (#418).

    By VALUE, not only by id: one value can hold sibling ids (the same sentence
    in two sections, or a widened hash within one), and removal is content
    removal, so every item folding to the tombstoned key goes. The id stays in
    the predicate as a belt for non-string text, which `content_key`
    canonicalizes to "". A forget with no live checkpoint (a ledger-only value,
    a resume) has nothing to splice."""
    checkpoint = target.checkpoint
    if not isinstance(checkpoint, dict):
        return jsonl.Reached(())
    removed: list[str] = []
    for section, key in schema.ITEM_LISTS:
        lst = (checkpoint.get(section) or {}).get(key)
        if isinstance(lst, list):
            doomed = [i for i in lst
                      if isinstance(i, dict)
                      and (i.get("id") == target.item_id
                           or normalize.content_key(i.get("text") or "")
                           == target.content_key)]
            removed.extend(str(i.get("id") or "") for i in doomed)
            lst[:] = [i for i in lst if i not in doomed]
    # rotate=False: rotation copies the CURRENT latest into prev-1 before
    # writing, and the current latest is the PRE-forget bytes. Rotating here
    # made the deletion manufacture a fresh copy of the value it was asked to
    # remove, in a file that did not exist when the user ran the command.
    wrote = store.write_checkpoint(
        target.session_id, checkpoint, project_dir=target.project,
        allow_disabled=True, rotate=False, writer=Writer.CURE)
    return jsonl.Reached(sorted(set(removed) - {""}), reached=wrote is not None)


# ---- running the steps -----------------------------------------------------

def _resolve(fn: str):
    module, _, attr = fn.rpartition(".")
    return getattr(importlib.import_module(f"daimon_briefing.{module}"), attr)


def _call(step: surfaces.Step, target: Target):
    fn = _resolve(step.fn)
    project = target.project
    if step.call == "value":
        return fn(target.content_key, project_dir=project)
    if step.call == "value-text":
        return fn(target.content_key, text=target.text, project_dir=project)
    if step.call == "id":
        ids = target.item_ids or ((target.item_id,) if target.item_id else ())
        return [fn(i, project_dir=project) for i in sorted(set(ids))]
    if step.call == "project":
        return fn(project_dir=project)
    if step.call == "target":
        return fn(target)
    return fn()


def _failed(step: surfaces.Step, result=None, *, state=None, torn: int = 0,
            detail: str = "", unscannable: str = "") -> tuple:
    return (Unreached(step.label, jsonl.Health.OK if state is None else state,
                      torn, step.plaintext, detail, unscannable),)


def holds_prose(row, content_key: str, ledger: str) -> bool:
    """Does a row of `ledger` still carry the value whole in one of the prose
    fields the registry declares for it. The one judge for a ledger whose
    deleter reports only a count."""
    return any(
        isinstance(row.get(field), str) and row[field]
        and normalize.content_key(row[field]) == content_key
        for field in surfaces.scalar_prose_fields(ledger))


def _count_judge(target: Target, shape: str):
    """The read afterwards for a deleter that returns only a count: the ledger
    named by the row is read again and judged by the row's own prose columns."""
    name = shape.rsplit("/", 1)[-1]
    return jsonl.judge_reach(
        store.ledger_file(target.project, name), [],
        lambda row: holds_prose(row, target.content_key, name))


def _as_done(step: surfaces.Step, shape: str, target: Target, result) -> Done:
    """Turn what a step returned into a `Done`. Accepts exactly the shapes the
    deleters have: a `jsonl.Reached`, a `Purged` (quarantine lines), a
    `store.Published`, a `(count, error)` pair, an int count, a plain list and,
    for an id step, a list of any of those. Anything else is a defect and is
    reported as an unreached step, never as a clean one."""
    if step.call == "id" and type(result) is list:
        parts = [_as_done(step, shape, target, r) for r in result]
        bad = [p for p in parts if p.unreached]
        removed = tuple(r for p in parts for r in p.removed)
        return Done(step, result, removed, len(removed),
                    bad[0].unreached if bad else ())
    if isinstance(result, jsonl.Reached):
        if result.reached:
            return Done(step, result, tuple(result), len(result))
        return Done(step, result, tuple(result), len(result),
                    _failed(step, state=result.state, torn=result.torn,
                            detail=result.detail,
                            unscannable=result.unscannable))
    if hasattr(result, "purged") and hasattr(result, "unreached"):
        return Done(step, result, (), result.purged, tuple(result.unreached),
                    kept=getattr(result, "kept", 0))
    if hasattr(result, "failed") and hasattr(result, "keys"):
        # store.Published: a team publish that could not write a sidecar.
        failed = tuple(result.failed)
        unreached = _failed(step, detail="could not be written") if failed else ()
        return Done(step, result, tuple(result), len(result), unreached)
    if isinstance(result, tuple) and len(result) == 2 and (
            result[1] is None or isinstance(result[1], str)):
        count, err = result
        return Done(step, result, (), int(count),
                    _failed(step) if err is not None else ())
    if isinstance(result, int):
        done = Done(step, result, (), result)
        if step.call == "value" and _is_ledger_row(shape):
            judged = _count_judge(target, shape)
            if not judged.reached:
                return Done(step, result, (), result,
                            _failed(step, state=judged.state, torn=judged.torn,
                                    detail=judged.detail,
                                    unscannable=judged.unscannable))
        return done
    # A bare list or nothing at all says nothing about the surface: it is
    # never taken as reached (scar 0135). Every step returns a `Reached`.
    if isinstance(result, list) or result is None:
        return Done(step, result, (), 0, _failed(step, detail="unjudged"))
    raise TypeError(f"{step.fn} returned an unsupported {type(result).__name__}")


def _no_approval(rulings: list) -> str:
    return ("refused: this would remove ACTIVE ruling(s) "
            + ", ".join(rulings) + ", a human decision")


def run(target: Target, *, scope: str = "all", phases=None,
        approve_rulings=None) -> Outcome:
    """Run every step of the registry for one forgotten value, in phase order.

    `scope="ledgers"` runs only the bucket-ledger and sidecar steps (the part
    `daimon ledger repair` owns); `phases` restricts the run to the named
    phases. The tombstone phase is the one phase whose failure stops the run:
    a forget that cannot record the tombstone has touched nothing and returns
    `refused`. Every other step is isolated, so one that fails or raises is
    reported and the steps after it still run.

    The active-ruling gate lives here so no caller can skip it: when the value
    phase is in the run and it would remove an ACTIVE ruling, `approve_rulings`
    (ids -> None to proceed, else the refusal text) is asked BEFORE any write;
    with no callable the run refuses."""
    done: list = []
    if phases is None or "value" in phases:
        doomed = refutations.active_rulings_reached(
            {target.content_key},
            item_ids={i for i in (*target.item_ids, target.item_id) if i},
            project_dir=target.project)
        if doomed:
            refusal = (approve_rulings or _no_approval)(list(doomed))
            if refusal:
                return Outcome(done, refusal=refusal)
    for planned in STEPS:
        step = planned.step
        if scope == "ledgers" and not planned.ledger:
            continue
        if scope == "ledgers" and step.phase in ("tombstone", "publish"):
            continue
        if phases is not None and step.phase not in phases:
            continue
        if step.phase == "tombstone":
            # Not isolated: a refused ledger (jsonl.Refused) reaches the
            # command, and a row that was not written stops the run.
            reached = _as_done(step, planned.shape, target,
                               _call(step, target))
            done.append(reached)
            if not reached.reached:
                return Outcome(done, refused=True)
            continue
        try:
            done.append(_as_done(step, planned.shape, target,
                                 _call(step, target)))
        except Exception as exc:  # noqa: BLE001 - a report, never a raise
            got = jsonl.raised(exc)
            done.append(Done(step, got, (), 0,
                             _failed(step, detail=got.detail)))
    return Outcome(done)
