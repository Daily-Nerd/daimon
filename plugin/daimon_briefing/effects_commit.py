"""Commits the `Effects` a read verb decided on, after its output (#1132 PR 7b).

`effects.Effects` is the pure record; this entry-layer module is the one place
that turns it into writes: usage lines, the worldcheck ledger rows, the
`surfaced` stamps, the recall telemetry row, the per-session cooldown state
(`seen`) and error breadcrumbs (`error_log`). It lives outside `effects.py`
because that module is the view layer (stdlib only) and these writers are not.

The ordering contract every host keeps: build the output, write it, THEN
commit. `commit` flushes stdout before its first write, so a line a host
printed has left the process before any usage line, stamp or ledger row is
written, and a crash in between leaves the card to be shown again rather than
recorded as shown. Hosts call it from a `finally`, so `usage` is recorded when
the verb exits 1 or 2 too. `verification`, `surfaced` and `telemetry` commit on
the success path alone for the briefing hosts. The recall verbs (`recall`,
`recall-inject`, `action-recall`) record their `usage`, `telemetry` and `seen`
as they go and commit them all from the `finally` of `committing`, after the
output.

A verb killed before its commit (a hook timeout, SIGKILL) loses its usage line,
its telemetry row and its cooldown state. That is accepted: all three are
measurements or best-effort hints, and writing them before the output instead
would record a delivery that never happened, which is the worse error.

Each write is best-effort on its own: one failing write never blocks the
others or changes the command's exit code. Writers are reached through the
modules they live in (`cli._note_usage`, `requests.stamp_surfaced`), never a
copy of them."""

import functools
import sys

from . import requests
from .effects import Effects, Surfaced, Verification, merge


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
    return Effects(verification=(Verification(
        worldcheck_project, route, dict(stats), tuple(rows)),))


def surfaced_effects(worldcheck_project, printed) -> Effects:
    """`surfaced` records for the cards `render_brief` printed in full.
    `printed` is its manifest (None: nothing is known to have been shown)."""
    if worldcheck_project is None or not printed:
        return Effects.none()
    out = [Surfaced("request", worldcheck_project, c.request_id)
           for c in printed.get("request") or () if c.stamp]
    out += [Surfaced("verdict", worldcheck_project, c.request_id,
                     c.reply_event_id)
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
        _commit_worldcheck(cli, record)
    for card in fx.surfaced:
        if card.kind == "request":
            _attempt(requests.stamp_surfaced, card.request_id,
                     project_dir=card.project)
        else:
            _attempt(requests.stamp_verdict_surfaced, card.request_id,
                     project_dir=card.project,
                     reply_event_id=card.reply_event_id)
    for sample in fx.telemetry:
        _attempt(_commit_telemetry, recall_telemetry, sample)
    for state in fx.seen:
        writer = cli._save_seen_atomic if state.atomic else cli._save_seen
        _attempt(writer, state.path, state.origin_counts, set(state.content_keys))
    for crumb in fx.error_log:
        _attempt(commit_error_log, crumb)


# One breadcrumb line is a pointer to what went wrong, not a place to keep a
# payload: whatever an exception message carried is cut here.
_ERROR_DETAIL_CAP = 400


def commit_error_log(crumb) -> None:
    """Append one redacted, capped line to `crumb.log` under the log dir. A
    line stays one line: the detail is folded onto a single row. Unlike
    `commit`, this neither flushes stdout nor imports `cli`: it is the narrow
    door for a library that swallows an error in the middle of someone else's
    output. `commit` uses this same writer for `fx.error_log`. May raise OSError;
    the callers decide how best-effort they are."""
    from . import config, redact
    detail, _ = redact.redact_text(" ".join(str(crumb.detail).split()))
    where = " ".join(str(crumb.where).split())[:64]
    d = config.log_dir()
    d.mkdir(parents=True, exist_ok=True)
    with (d / crumb.log).open("a", encoding="utf-8") as f:
        f.write(f"{crumb.at} {where}: {detail[:_ERROR_DETAIL_CAP]}\n")


def _commit_telemetry(recall_telemetry, sample) -> None:
    """Record one delivery. A query term whose key is forgotten is dropped
    first, using the set the view already holds, so a forgotten value never
    counts as something the reader asked about. An unreadable set costs
    nothing: the terms are kept."""
    # LIMITS, said plainly: the delivery ledger stores only
    # `query_term_count`, never the terms, so today this filter only lowers
    # that count. It compares ONE term's key with whole-value tombstones, so it
    # fires only when a forgotten value is itself a single term. It stays so
    # the shape is right if the ledger ever stores terms.
    kwargs = dict(sample.kwargs)
    try:
        from . import normalize, view
        forgotten = view.forgotten_keys()
        kwargs["query_terms"] = [
            t for t in kwargs.get("query_terms") or ()
            if normalize.content_key(str(t)) not in forgotten]
    except Exception:  # noqa: BLE001 - keep the terms, see the docstring
        pass
    recall_telemetry.record(sample.rows, **kwargs)


def _commit_worldcheck(cli, record: Verification) -> None:
    project, route = record.project, record.route
    stats, rows = record.stats, record.rows
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
