"""Local telemetry for recall items that were actually delivered.

The recall index is disposable and its scores change when the corpus changes.
This append-only ledger records the score at the moment a row crossed a
delivery surface, so later measurement does not re-run history against today's
index. It contains no item text or query text, only the number of retrieval
terms used for that delivery.
"""

import json
import statistics
from datetime import datetime, timedelta, timezone

from . import config

WINDOW_DAYS = 7


def _stamp(now=None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(rows, *, query_terms, surface, now=None) -> None:
    """Append one bounded record for every row actually delivered.

    Telemetry is best-effort. A read or prompt path must never fail because a
    local measurement file is unavailable or malformed.
    """
    entries = []
    stamp = _stamp(now)
    term_count = sum(1 for term in query_terms if str(term).strip())
    for row in rows:
        score = row.get("match_score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
        hits = row.get("term_hits")
        if not isinstance(hits, int) or isinstance(hits, bool):
            hits = None
        entries.append(json.dumps({
            "at": stamp,
            "surface": str(surface),
            "item_id": row.get("item_id"),
            "session_id": row.get("session_id"),
            "project_slug": row.get("project_slug"),
            "match_score": score,
            "term_hits": hits,
            "query_term_count": term_count,
        }, ensure_ascii=False, separators=(",", ":")))
    if not entries:
        return
    try:
        path = config.recall_delivery_log()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n".join(entries) + "\n")
    except OSError:
        pass


def _parse_stamp(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _summary(rows: list[dict]) -> dict:
    scores = [r["match_score"] for r in rows
              if isinstance(r.get("match_score"), (int, float))]
    hits = [r["term_hits"] for r in rows
            if isinstance(r.get("term_hits"), int)
            and not isinstance(r.get("term_hits"), bool)]
    surface_names: set[str] = set()
    for row in rows:
        surface = row.get("surface")
        if isinstance(surface, str) and surface:
            surface_names.add(surface)
    surfaces = sorted(surface_names)
    return {
        "deliveries": len(rows),
        "scored": len(scores),
        "match_score": {
            "min": min(scores) if scores else None,
            "median": statistics.median(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
        "term_hits": {
            "min": min(hits) if hits else None,
            "median": statistics.median(hits) if hits else None,
            "max": max(hits) if hits else None,
        },
        "by_surface": {
            surface: sum(1 for r in rows if r.get("surface") == surface)
            for surface in surfaces
        },
    }


def stats(*, now=None) -> dict:
    """Read lifetime and recent delivery distributions without rebuilding recall."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS)
    lifetime, recent = [], []
    try:
        lines = config.recall_delivery_log().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        lifetime.append(row)
        stamp = _parse_stamp(row.get("at"))
        if stamp is not None and stamp >= cutoff:
            recent.append(row)
    return {
        "window_days": WINDOW_DAYS,
        "lifetime": _summary(lifetime),
        "window": _summary(recent),
    }
