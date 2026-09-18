"""Local telemetry for recall items that were actually delivered.

The recall index is disposable and its scores change when the corpus changes.
This append-only ledger records the score at the moment a row crossed a
delivery surface, so later measurement does not re-run history against today's
index. It contains no item text or query text, only the number of retrieval
terms used for that delivery.
"""

import json
import re
import statistics
from datetime import datetime, timedelta, timezone

from . import config

WINDOW_DAYS = 7

# #1053: the ONE session-id validator both the MCP tool argument
# (mcp_tools._recall) and the recall hint's session clause (cli._suggest_line)
# go through — a session the hint ever renders must be exactly one the tool
# will accept back, and a hostile id (a newline or a quote, either of which
# would break the hint's one-line echo-strip contract, #512) must be rejected
# before it ever reaches either place. Charset covers every real session id
# shape measured in this repo: Kimi's `session_<uuid>` (underscore AND
# hyphens, tests/test_kimi_hook_scripts.py SESSION) and Codex's bare
# `<uuid>` (tests/test_cli.py _CODEX_SID) both fit inside it already.
_SESSION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def clean_session(value) -> str | None:
    """Normalize an externally-sourced session id, or drop it silently.

    `value` is UNTRUSTED text — an agent-supplied MCP tool argument, or a
    host payload field threaded through a hook and a CLI flag. Anything
    that is not a non-empty string of at most 128 characters from
    `_SESSION_RE`'s charset (letters, digits, `.`, `_`, `:`, `-`) comes back
    `None`: too long, wrong type, empty/whitespace-only, or carrying a
    character (a newline, a double quote, a space, `;`) that could either
    break a one-line rendering or fail to round-trip through a shell/JSON
    boundary. The caller drops the field, never raises or fails the call
    that carries it."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not _SESSION_RE.fullmatch(value):
        return None
    return value


def _stamp(now=None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(rows, *, query_terms, surface, hint_form=None, injected_into=None,
          via=None, now=None) -> None:
    """Append one bounded record for every row actually delivered.

    Telemetry is best-effort. A read or prompt path must never fail because a
    local measurement file is unavailable or malformed.

    `hint_form` (#1036) is which "More: ..." rendering the delivery actually
    carried: "tool" when the host lists the read-only MCP server and the hint
    named `daimon_recall`, "shell" for the `daimon recall "..."` command
    string. One value per call, same as `surface` — a delivery renders one
    hint form, never a mix. Omitted (every caller that predates #1036)
    records `None` rather than guessing; rows written before this field
    existed carry no key at all, and a reader uses `.get` rather than assume
    one.

    `injected_into` (#1043) is the id of the LIVE session that received this
    delivery, read from the host payload by the caller. It is deliberately
    NOT `row["session_id"]`: that field is the CAPTURING session of the
    matched item (provenance) and stays unchanged. Omitted (every caller
    that predates #1043, and the plain `daimon recall` search, which is a
    pull rather than an injection) records `None`; a reader treats a missing
    value as unknown, never as the provenance id.

    `via` (#1053) is which SURFACE wrote this row: "cli" for the shell
    `daimon recall` command, "mcp" for the `daimon_recall` tool. Distinct
    from `hint_form` (which hint a delivery RENDERED) — `via` is where a
    PULL actually came from, and only a pull (`recall-search`) ever sets it.
    An out-of-vocabulary or omitted value (every caller that predates
    #1053) records `None` rather than guessing.

    #1057: a `recall-search` call (a PULL, from `_cmd_recall` or the
    `daimon_recall` MCP tool) that matched ZERO rows still writes exactly
    ONE row — `_follow_through` keys one event per `record()` call, so a
    pull that found nothing must still register as one, or an agent that
    followed the hint and got nothing back reads as an agent that never
    asked. That row carries the SAME `at`, `surface`, `via` and
    `injected_into` a matching call would have used; `item_id`,
    `session_id`, `project_slug`, `term_hits` and `hint_form` are all
    `None` (there is no item to read any of them from), `match_score` is
    `None` too (the same "unscoreable" posture every reader already gives a
    non-numeric score). `rendered_chars` is `0` and `truncated` is `False`
    — both are KNOWN facts here rather than absent measurements: zero
    characters were rendered, and zero characters can never have been cut.
    Only a PULL gets this row: `recall-inject` and `action-recall` deliver
    nothing by printing nothing, and an empty call there still writes
    nothing, same as before #1057.
    """
    entries = []
    stamp = _stamp(now)
    term_count = sum(1 for term in query_terms if str(term).strip())
    injected_into = (str(injected_into)
                     if isinstance(injected_into, str) and injected_into.strip()
                     else None)
    for row in rows:
        score = row.get("match_score")
        try:
            score = float(score)
        except (TypeError, ValueError):
            score = None
        hits = row.get("term_hits")
        if not isinstance(hits, int) or isinstance(hits, bool):
            hits = None
        # #1030: how much text the delivery actually rendered, and whether the
        # slot's width cut it. Only surfaces that render a width supply these,
        # so a missing or wrong-typed value records as absent rather than
        # invented — the same posture as an unscoreable match_score.
        rendered = row.get("rendered_chars")
        if not isinstance(rendered, int) or isinstance(rendered, bool):
            rendered = None
        truncated = row.get("truncated")
        if not isinstance(truncated, bool):
            truncated = None
        entries.append(json.dumps({
            "at": stamp,
            "surface": str(surface),
            "item_id": row.get("item_id"),
            "session_id": row.get("session_id"),
            "injected_into": injected_into,
            "project_slug": row.get("project_slug"),
            "match_score": score,
            "term_hits": hits,
            "query_term_count": term_count,
            "rendered_chars": rendered,
            "truncated": truncated,
            "hint_form": hint_form if hint_form in ("tool", "shell") else None,
            "via": via if via in ("cli", "mcp") else None,
        }, ensure_ascii=False, separators=(",", ":")))
    if not entries and surface == "recall-search":
        # #1057: the placeholder row for a pull that matched nothing — see
        # the docstring above for the exact shape and why each field lands
        # the way it does.
        entries.append(json.dumps({
            "at": stamp,
            "surface": str(surface),
            "item_id": None,
            "session_id": None,
            "injected_into": injected_into,
            "project_slug": None,
            "match_score": None,
            "term_hits": None,
            "query_term_count": term_count,
            "rendered_chars": 0,
            "truncated": False,
            "hint_form": None,
            "via": via if via in ("cli", "mcp") else None,
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


def _is_empty_pull(row: dict) -> bool:
    """True for the #1057 placeholder row a `recall-search` call writes when
    it matched nothing. `item_id` alone cannot tell this apart from a
    genuine delivered row — a pre-D-011 item can carry a real (scored) row
    with no minted id — so this checks the full combination the writer
    above actually produces for the empty case: no item id, no score, and
    `rendered_chars` pinned at exactly 0 (a genuine `recall-search` row
    never sets `rendered_chars` at all — that field only ever comes from
    `recall-inject`/`action-recall`'s own width fit — so it is otherwise
    always `None`, never `0`)."""
    return (row.get("surface") == "recall-search"
            and row.get("item_id") is None
            and row.get("match_score") is None
            and row.get("rendered_chars") == 0)


def _event_stamp(row: dict):
    """The row's `at` value when present and parseable, else `None` — a
    shared sentinel every stamp-less or malformed-stamp row for one session
    folds into, rather than a value that could collide with a real stamp."""
    at = row.get("at")
    if isinstance(at, str) and at.strip() and _parse_stamp(at) is not None:
        return at
    return None


def _follow_through(rows: list[dict]) -> dict:
    """Per-injecting-session pairing (#1053): for every session that received
    at least one recall-inject hint, how many recall-search PULLS (CLI or
    MCP) landed attributed to that SAME live session, and through which
    surface (`via`) they arrived.

    Counts EVENTS, not rows. One `record()` call is one event — every row it
    writes shares the same `at` stamp — and a single call routinely writes
    more than one row (measured against the real delivery log: recall-search
    calls wrote 20, 20, 20, and 3 rows respectively). Counting rows as pulls
    would report that one 20-row tool call as 20 pulls against maybe 2
    injections, a >100% follow-through rate. So `injections`/`pulls` here
    are event counts — for pulls, distinct (`at` stamp, `via`) pairs, since
    `via` is part of a pull's identity the way it is not for an injection —
    and `injection_rows`/`pull_rows` report the underlying row volume
    alongside them, so neither number hides the other.

    A row with a missing or unparseable `at` (every row from before this
    file recorded stamps reliably, or any future write bug) still counts
    toward its `*_rows` total, but folds into ONE shared 'unknown stamp'
    event per session (per `via`, for pulls) rather than being dropped or
    minting one phantom event per row.

    A pull's `injected_into` is agent-supplied text on the MCP path — a
    made-up session id, or one that simply never received a hint, must pair
    with nothing rather than mint a phantom bucket. So the bucket set is
    seeded from recall-inject rows ONLY, and a pull naming a session absent
    from that set is dropped, never counted and never added.

    #1057: the placeholder row a `recall-search` call writes when it
    matched nothing (`_is_empty_pull`) still counts as ONE pull EVENT — the
    event key is the same (`at`, `via`) pair a matching call would have
    used — but never adds to `pull_rows`, since it carries no delivered
    item. An agent that asked and got nothing back must still show up as a
    pull, just with zero rows behind it."""
    sessions: dict[str, dict] = {}
    inject_events: dict[str, set] = {}
    for row in rows:
        if row.get("surface") != "recall-inject":
            continue
        session = row.get("injected_into")
        if not isinstance(session, str) or not session.strip():
            continue
        bucket = sessions.setdefault(session, {
            "injections": 0, "injection_rows": 0,
            "pulls": 0, "pull_rows": 0, "by_via": {}})
        bucket["injection_rows"] += 1
        seen = inject_events.setdefault(session, set())
        stamp = _event_stamp(row)
        if stamp not in seen:
            seen.add(stamp)
            bucket["injections"] += 1
    pull_events: dict[str, set] = {}
    for row in rows:
        if row.get("surface") != "recall-search":
            continue
        session = row.get("injected_into")
        if not isinstance(session, str) or not session.strip():
            continue
        existing = sessions.get(session)
        if existing is None:
            continue
        if not _is_empty_pull(row):
            existing["pull_rows"] += 1
        via = row.get("via")
        via_key = via if via in ("cli", "mcp") else "unknown"
        stamp = _event_stamp(row)
        seen = pull_events.setdefault(session, set())
        key = (stamp, via_key)
        if key not in seen:
            seen.add(key)
            existing["pulls"] += 1
            existing["by_via"][via_key] = existing["by_via"].get(via_key, 0) + 1
    return sessions


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
    # #1057: the placeholder row for a zero-match pull delivered no item, so
    # every ITEM/ROW count below is built from this filtered list instead of
    # `rows` directly — it still counts as one pull EVENT, but `_follow_through`
    # (below, over the unfiltered `rows`) is the only reader that sees that.
    item_rows = [r for r in rows if not _is_empty_pull(r)]
    # #1043: per-injecting-session breakdown, with a hint_form split inside
    # each bucket, so the tool-vs-shell follow-through comparison has a
    # denominator without reading the raw file. A row with no `injected_into`
    # (every row written before this field existed, plus the plain
    # `daimon recall` search, which injects into nothing) buckets as
    # "unknown" rather than being dropped or mistaken for a real session.
    by_injected_into: dict[str, dict] = {}
    for row in item_rows:
        session = row.get("injected_into")
        key = session if isinstance(session, str) and session.strip() else "unknown"
        bucket = by_injected_into.setdefault(
            key, {"deliveries": 0, "by_hint_form": {}})
        bucket["deliveries"] += 1
        hint = row.get("hint_form")
        hint_key = hint if hint in ("tool", "shell") else "unknown"
        bucket["by_hint_form"][hint_key] = bucket["by_hint_form"].get(hint_key, 0) + 1
    return {
        "deliveries": len(item_rows),
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
            surface: sum(1 for r in item_rows if r.get("surface") == surface)
            for surface in surfaces
        },
        "by_injected_into": by_injected_into,
        "follow_through": _follow_through(rows),
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
