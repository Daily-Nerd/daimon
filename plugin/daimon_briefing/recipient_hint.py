"""#1424: the note `request open` prints when the recipient bucket is quiet and
a similarly named bucket next to it was active since.

The invariant behind it: an ask addressed to a slug reaches whoever works on
that slug. Two buckets that are really one project of work break it, and the
only declaration daimon has for "these are one" is `bucket merge` (the alias
row request routing follows). This module never routes and never decides that
two buckets are one project; it states facts at the one moment the sender can
still act, and points at the merge.

Definitions, all from data daimon already stores:

  last seen   the later of the bucket's latest checkpoint `created` and its
              latest MECHANICAL attention stamp in its own `requests.jsonl`
              (the brief or prompt hook showed it a request). Never a file
              mtime, never an accepted/done row: those happen from whatever
              folder a person stands in and say nothing about a session being
              shown the inbox.
  quiet       the sibling's last seen is at least `GAP_DAYS` later than the
              recipient's. A gap between the two, so no clock assumption.
  sibling     both buckets have a recorded root, the roots share a parent
              folder and the folder names are prefix-related. Skipped when
              one is already an alias of the other, and candidates come only
              from `view.buckets`, the one tenant rule.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Mapping, NamedTuple

from . import buckets, clock, config, requests, store, view

GAP_DAYS = 14
_GAP_SECONDS = GAP_DAYS * 86_400
MIN_NAME_LEN = 4

class Hint(NamedTuple):
    recipient: str
    recipient_seen: float
    sibling: str
    sibling_seen: float
    more: int       # further related buckets that were also active since


def names_related(a: str, b: str) -> bool:
    """Whether two folder names are prefix-related: case-insensitive, distinct,
    the shorter at least `MIN_NAME_LEN` characters."""
    a, b = a.lower(), b.lower()
    short, long_ = sorted((a, b), key=len)
    return (len(short) >= MIN_NAME_LEN and short != long_
            and long_.startswith(short))


def related(to: str, roots: Mapping[str, str],
            aliases: Mapping[str, str]) -> list[str]:
    """Slugs in `roots` that sit next to `to` under a related name and are not
    already one project with it. `aliases` is `buckets.alias_map()`, whose
    chains already end at the FINAL bucket, so two names are one project when
    they end at the same bucket or either ends at the other. Pure; sorted."""
    base = roots.get(to)
    if not base:
        return []

    def final(slug: str) -> str:
        return aliases.get(slug, slug)

    parent, name = os.path.split(base.rstrip("/\\"))
    out = []
    for slug, root in roots.items():
        if slug == to or store.root_eq(root, base):
            continue
        if final(to) == final(slug) or final(to) == slug or final(slug) == to:
            continue
        other_parent, other = os.path.split(root.rstrip("/\\"))
        if store.root_eq(other_parent, parent) and names_related(name, other):
            out.append(slug)
    return sorted(out)


def _sane(epoch: float | None) -> float | None:
    """`epoch` when it is a time a session could have written: after 1970 and
    no later than a day past the clock. Anything else (a hand-edited row, an
    absurd `order`, NaN, a clock gone wild) is no stamp. The one place a time
    read off disk is accepted, and the bound is what keeps `_day` from ever
    seeing a value it cannot render."""
    if epoch is None or not 0 < epoch <= clock.now_ns() / 1e9 + 86_400:
        return None
    return float(epoch)


def last_seen(slug: str) -> float | None:
    """Epoch seconds a session last saved or was shown anything in `slug`, or
    None when nothing readable says so."""
    seen: list[float | None] = []
    for entry in store.list_buckets(only=frozenset({slug})):
        cp = entry["checkpoint"]
        seen.append(_sane(store._created_epoch(
            cp.get("created") if isinstance(cp, dict) else None)))
    for row in requests.events(slug):
        if (row.get("channel") == "mechanical"
                and row.get("event") in requests.ATTENTION_STAMPS):
            order = requests._row_order(row)
            seen.append(_sane(order / 1_000_000_000) if order
                        else _sane(store._created_epoch(row.get("ts"))))
    valid = [t for t in seen if t is not None]
    return max(valid) if valid else None


def hint(to: str, own_slug: str) -> Hint | None:
    """The note's facts for an ask addressed to `to`, or None. Only buckets
    `view.buckets` lets the caller enumerate are ever read, and a
    tenant-scoped home returns before reading anything: the only bucket it may
    enumerate is its own, so no sibling can exist."""
    if config.tenant_scoped():
        return None
    candidates = set(view.buckets(own_slug)) | {to}
    roots = {slug: root for slug in sorted(candidates)
             if (root := store.bucket_root(slug))}
    siblings = related(to, roots, buckets.alias_map())
    if not siblings:
        return None
    recipient_seen = last_seen(to)
    if recipient_seen is None:
        return None
    active = []
    for slug in siblings:
        seen = last_seen(slug)
        if seen is not None and seen - recipient_seen >= _GAP_SECONDS:
            active.append((seen, slug))
    if not active:
        return None
    seen, slug = max(active)
    return Hint(to, recipient_seen, slug, seen, len(active) - 1)


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d")


def note_lines(found: Hint, now_ns: int | None = None) -> list[str]:
    """The advisory note: facts, one question, one remedy."""
    now = (clock.now_ns() if now_ns is None else now_ns) / 1_000_000_000
    days = max(0, int((now - found.recipient_seen) // 86_400))
    more = (f" (and {found.more} more with similar names)"
            if found.more else "")
    return [
        f"note: nothing has been saved or shown in {found.recipient} since "
        f"{_day(found.recipient_seen)} ({days} days ago).",
        f"  {found.sibling} sits next to it with a similar name and was "
        f"active on {_day(found.sibling_seen)}{more}.",
        "  If they are one project, ask the owner to merge them "
        "(`daimon bucket merge`), so asks reach one inbox. If not, ignore "
        "this.",
    ]
