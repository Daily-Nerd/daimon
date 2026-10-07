"""Commits the `Effects` a read verb decided on, after its output (#1132 PR 7b).

`effects.Effects` is the pure record; this entry-layer module is the one place
that turns it into writes: usage lines, the worldcheck ledger rows, the
`surfaced` stamps, the recall telemetry row. It lives outside `effects.py`
because that module is the view layer (stdlib only) and these writers are not.

The ordering contract every host keeps: build the output, write it, THEN
commit. `commit` flushes stdout before its first write, so a line a host
printed has left the process before any usage line, stamp or ledger row is
written, and a crash in between leaves the card to be shown again rather than
recorded as shown. Hosts call it from a `finally`, so `usage` is recorded when
the verb exits 1 or 2 too; they only add `verification`, `surfaced` and
`telemetry` after the output was written, so those commit on the success path
alone.

Each write is best-effort on its own: one failing write never blocks the
others or changes the command's exit code. Writers are reached through the
modules they live in (`cli._note_usage`, `requests.stamp_surfaced`), never a
copy of them."""

import functools
import sys

from . import requests
from .effects import Effects, merge


class Pending:
    """The effects a host has accumulated so far; `add` merges more in."""

    def __init__(self, *usage: str):
        self.effects = Effects(usage=tuple(usage))

    def add(self, more: Effects) -> None:
        self.effects = merge(self.effects, more)


def committing(fn):
    """A CLI verb `fn(args, fx)` as `fn(args)`: the verb records into `fx`,
    and whatever it recorded is committed once it returns or raises."""
    @functools.wraps(fn)
    def run(args):
        fx = Pending()
        try:
            return fn(args, fx)
        finally:
            commit(fx.effects)
    return run


def worldcheck_effects(worldcheck_project, route, stats, rows) -> Effects:
    """The worldcheck bookkeeping of one brief: its counters, the receipt
    probe usage and the ledger rows, as one `verification` record."""
    return Effects(verification=(("worldcheck", worldcheck_project, route,
                                  dict(stats), tuple(rows)),))


def surfaced_effects(worldcheck_project, printed) -> Effects:
    """`surfaced` records for the cards `render_brief` printed in full.
    `printed` is its manifest (None: nothing is known to have been shown)."""
    if worldcheck_project is None or not printed:
        return Effects.none()
    out = [("request", worldcheck_project, c.request_id, None)
           for c in printed.get("request") or () if c.stamp]
    out += [("verdict", worldcheck_project, c.request_id, c.reply_event_id)
            for c in printed.get("verdict") or () if c.stamp]
    return Effects(surfaced=tuple(out))


def _attempt(write, *args, **kwargs) -> None:
    try:
        write(*args, **kwargs)
    except Exception:  # noqa: BLE001 — best-effort, see the module docstring
        pass


def commit(fx: Effects) -> None:
    """Flush stdout, then perform every write `fx` records, in the order
    usage, verification, surfaced, telemetry. Never raises."""
    if fx == Effects.none():
        return
    _attempt(sys.stdout.flush)
    from . import cli, recall_telemetry
    for tag in fx.usage:
        _attempt(cli._note_usage, tag)
    for record in fx.verification:
        _commit_worldcheck(cli, *record)
    for kind, project, request_id, reply_id in fx.surfaced:
        if kind == "request":
            _attempt(requests.stamp_surfaced, request_id,
                     project_dir=project)
        else:
            _attempt(requests.stamp_verdict_surfaced, request_id,
                     project_dir=project, reply_event_id=reply_id)
    for rows, kwargs in fx.telemetry:
        _attempt(recall_telemetry.record, rows, **kwargs)


def _commit_worldcheck(cli, kind, project, route, stats, rows) -> None:
    # #397: the dict carries the aggregate outcomes AND a "<class>:<outcome>"
    # key per class, so one pass emits both the slice-1 counters and the
    # per-class fires-true rate the next expansion gate reads.
    for counter, count in sorted(stats.items()):
        for _ in range(int(count)):
            _attempt(cli._note_usage, f"worldcheck:{counter}")
    # #919: the receipt-probe axis, project-scoped.
    _attempt(cli._note_receipt_probe_usage, project, stats)
    # A POINTER and a REASON CODE, never the item's text (#376).
    _attempt(cli._write_worldcheck_ledger, rows, route)
