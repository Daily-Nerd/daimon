"""`daimon stats`: local usage and capture aggregates (moved out of cli/__init__.py, #1132 PR 5).

Nothing here is transmitted anywhere; sharing the output is a deliberate
paste. Every name is re-exported from `cli`, which stays the seam.
"""

import json
import time
from datetime import datetime, timedelta, timezone

import daimon_briefing.cli as _cli

from .. import (
    capture,
    config,
    jsonl,
    llm,
    recall_telemetry,
    refutations,
    render,
    schema,
    serializer,
    store,
)
from ..ledger import (
    AUTO_BRIEF_HOSTS,
    _parse_stamp,
    _spawns_in_window_count,
    _stats_capture,
)


# ---- stats: local usage + capture aggregates (#54) — zero phone-home ----


def _stats_usage() -> dict:
    """usage.log -> {command: count}. Counts every line — the file only holds
    `<iso> <command>` entries."""
    counts: dict = {}
    try:
        text = (config.log_dir() / "usage.log").read_text(encoding="utf-8")
    except OSError:
        return counts
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    return counts


def _stats_store() -> dict:
    """Checkpoint store -> counts by kind and trust class + carried items.
    Reuses recall's section map so a new cognitive kind shows up here for free."""
    out: dict = {"checkpoints": 0, "project_buckets": 0, "items_by_kind": {},
                 "items_verbatim": 0, "items_inferred": 0,
                 "items_untagged": 0, "items_carried": 0,
                 "format_versions": {}, "extraction_versions": {}}
    d = config.checkpoint_dir()
    try:
        out["project_buckets"] = sum(1 for p in d.iterdir() if p.is_dir())
        files = store._session_files(d)
    except OSError:
        return out
    for p in files:
        try:
            cp = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cp, dict):
            continue
        out["checkpoints"] += 1
        # #514: corpus generation composition — absent stamps count as
        # "unknown" (pre-stamp checkpoints cannot be retroactively dated),
        # so a mixed-generation corpus is visible instead of assumed uniform.
        for field, bucket in (("format_version", "format_versions"),
                              ("extraction_version", "extraction_versions")):
            key = str(cp.get(field)) if cp.get(field) is not None else "unknown"
            out[bucket][key] = out[bucket].get(key, 0) + 1
        for section, key, kind in schema.KIND_SOURCES:
            block = cp.get(section)
            raw = block.get(key) if isinstance(block, dict) else None
            if key == "active_topic":
                raw = [raw]
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                    continue
                out["items_by_kind"][kind] = out["items_by_kind"].get(kind, 0) + 1
                trust = item.get("trust")
                if trust == "verbatim":
                    out["items_verbatim"] += 1
                elif trust:
                    out["items_inferred"] += 1
                else:
                    out["items_untagged"] += 1
                if item.get("carried_from"):
                    out["items_carried"] += 1
    return out


_RETENTION_WINDOW_DAYS = 14


def _stats_retention(now=None) -> dict:
    """usage.log -> briefings delivered vs deliberate re-reads, over the last
    _RETENTION_WINDOW_DAYS. `status` is ops polling and counts apart, outside
    the total and the ratio (#232 — a debugging session must not read as
    retention).

    A briefing reaches the agent by one of two paths, and which one is
    available is a permanent property of the host (#349): hosts with a
    session-start event log `brief:auto`, and hosts without one (Cascade,
    Codex) have the skill invoke `daimon brief` instead. usage.log carries no
    host, so a plain `brief` line is only readable against the hosts that
    actually spawned captures in the same window (#477):

    - `hook`/`none` — no hookless spawns contradict it, so `brief` is a
      deliberate re-read, as it always was.
    - `skill` — hookless spawns only. `brief` IS the briefing being delivered;
      counting it as a re-read made the only working delivery path on that
      host invisible. The pre-`--auto` untagged rule is off here too: a host
      that can never log `brief:auto` has no upgrade marker to sit before.
    - `mixed` — both. A plain `brief` is neither confidently delivery nor
      re-read, so it is reported as `ambiguous_briefs` and the ratio is
      withheld rather than guessed (#54). This is the defect #477 filed: one
      stray auto-capable session in the denominator against a fortnight of
      hookless reads in the numerator rendered as a confident headline number.

    Plain `brief` lines stamped before the first `brief:auto` ever logged
    predate the flag and are reported as untagged — ambiguous, never guessed.
    stale_hook_warning: sessions were captured in the window but zero
    auto-briefings were logged — the SessionStart hook likely predates
    --auto."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_RETENTION_WINDOW_DAYS)
    out: dict = {"window_days": _RETENTION_WINDOW_DAYS, "hook_briefs": 0,
                 "skill_briefs": 0, "ambiguous_briefs": 0,
                 "briefings_total": 0,
                 "delivery_mode": "none",
           # status is ops polling (serializer health, pending counts), not a
           # memory read (#232): counted apart, never in the total or ratio.
                 "rereads": {"brief": 0, "recall": 0}, "status_checks": 0,
                 "rereads_total": 0, "rereads_per_briefing": None,
                 "untagged_briefs": 0, "stale_hook_warning": False}
    # Host population is read from the SAME window as the counters, so a stale
    # spawn from a host retired months ago cannot reclassify this fortnight.
    auto_spawns = _spawns_in_window_count(cutoff, hosts=AUTO_BRIEF_HOSTS)
    total_spawns = _spawns_in_window_count(cutoff)
    hookless_spawns = total_spawns - auto_spawns
    if hookless_spawns and auto_spawns:
        mode = "mixed"
    elif hookless_spawns:
        mode = "skill"
    elif auto_spawns:
        mode = "hook"
    else:
        mode = "none"
    out["delivery_mode"] = mode
    try:
        lines = (config.log_dir() / "usage.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    events = []
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        stamp = _parse_stamp(parts[0])
        if stamp is not None:
            events.append((stamp, parts[1]))
    first_auto = min((s for s, cmd in events if cmd == "brief:auto"), default=None)
    for stamp, cmd in events:
        if (cmd == "brief" and mode != "skill"
                and (first_auto is None or stamp < first_auto)):
            out["untagged_briefs"] += 1
            continue
        if stamp < cutoff:
            continue
        if cmd == "brief:auto":
            out["hook_briefs"] += 1
        elif cmd == "status":
            out["status_checks"] += 1
        elif cmd == "brief":
            if mode == "skill":
                out["skill_briefs"] += 1
            elif mode == "mixed":
                out["ambiguous_briefs"] += 1
            else:
                out["rereads"]["brief"] += 1
        elif cmd in out["rereads"]:
            out["rereads"][cmd] += 1
    out["rereads_total"] = sum(out["rereads"].values())
    out["briefings_total"] = out["hook_briefs"] + out["skill_briefs"]
    # Withheld on `mixed`: the numerator spans hosts the denominator does not.
    if mode != "mixed" and out["briefings_total"]:
        out["rereads_per_briefing"] = round(
            out["rereads_total"] / out["briefings_total"], 2)
    # #349: only spawns from auto-brief-capable hosts count — a Windsurf- or
    # Codex-only machine can never log brief:auto, and warning it to re-run
    # `hooks install` is a permanent false positive.
    if out["hook_briefs"] == 0 and auto_spawns:
        out["stale_hook_warning"] = True
    return out


def _stats_events(project_dir) -> dict:
    """events.jsonl (current project) -> raw line count + fold cost. The
    measure-first instrument for #106: compaction of the append-only log stays
    deferred until these numbers show a real cost. `lines` counts EVERY
    appended line (the growth signal), `resolved_refs` the folded item count,
    and `fold_ms` times a full store.resolutions() — read + parse + latest-by-ts
    fold over the whole log, measured at stats time. Fails open to zeroes when
    the project is unknown or the log is missing/corrupt (same as the fold)."""
    out = {"lines": 0, "fold_ms": 0.0, "resolved_refs": 0}
    path = store._events_path(project_dir)
    if path is None:
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    # A RAW line count (blank, torn and garbage lines included): the growth
    # signal, which jsonl.read's rows cannot give.
    out["lines"] = len(text.splitlines())
    start = time.perf_counter()
    folded = store.resolutions(project_dir=project_dir)
    out["fold_ms"] = round((time.perf_counter() - start) * 1000, 2)
    out["resolved_refs"] = len(folded)
    return out


def _stats_verification(project_dir) -> dict:
    """Rejection ledger (#376) -> total + per-check breakdown. This is the
    number that turns "memory you can verify" from a claim into something the
    user can check on their own machine: how often the checkers actually
    caught something here. Zeroes when nothing was ever rejected, which is
    itself an answer."""
    by_check = store.verification_counts(project_dir=project_dir)
    return {"total": sum(by_check.values()), "by_check": by_check}


def _stats_stitching(project_dir) -> dict:
    """Quote-stitching rate over this project's stored receipts (#974).

    #829 records, per VERIFIED receipt, whether the matched fragments could
    have come from one message / one role
    (`quote_provenance.stitching.{cross_message, cross_role}`). Nothing read
    it back, so rule 17's no-stitching doctrine had a measurement nobody
    could see. This is that read.

    Three decisions, each of which changes what the number means:

    * DENOMINATOR = receipts that CARRY a stitching verdict, never all
      verified receipts. A pre-D-019 receipt, or one stamped by a
      transcript-less capture, is absent-means-unknown — folding it in as a
      clean verdict would report `0%` where the honest answer is "daimon
      cannot tell you". The unknowns are counted as `unmeasured` and
      reported beside the rate, the #477 two-populations rule.

    * ONE ITEM COUNTS ONCE, keyed on the stable item id. A carried item
      rides into every later checkpoint with the same id and the same frozen
      receipt, so counting occurrences would let a single stitched quote
      inflate the rate once per session it survived (#562's epoch-artifact
      class). An item with no id — `active_topic` never carries one — counts
      as its own occurrence, which is the conservative reading: it cannot be
      proven to be a duplicate of anything.

    * ITS OWN WALK, not a rider on `_stats_store`'s. That block is
      machine-wide by contract and this number is per project (audit.py's
      `project_slug` filter, reused). Stats is a diagnostic verb run by
      hand; a second pass over the checkpoint dir is cheaper than a
      per-project count living inside a machine-wide section.

    Never raises: an unreadable checkpoint is skipped, the same posture the
    other stats folds take."""
    want_slug = store.project_slug(project_dir)
    out: dict = {"verified": 0, "measured": 0, "stitched": 0,
                 "cross_message": 0, "cross_role": 0, "unmeasured": 0,
                 "rate_pct": None}
    try:
        files = store._session_files(config.checkpoint_dir())
    except OSError:
        return out
    seen: set = set()
    for f in sorted(files):
        try:
            cp = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cp, dict):
            continue
        if want_slug is not None and cp.get("project_slug") != want_slug:
            continue
        for item in serializer.iter_items(cp):
            receipt = item.get("quote_provenance")
            if (not isinstance(receipt, dict)
                    or receipt.get("outcome") != "verified"):
                continue
            item_id = item.get("id")
            if isinstance(item_id, str) and item_id:
                if item_id in seen:
                    continue
                seen.add(item_id)
            out["verified"] += 1
            stitching = receipt.get("stitching")
            if not isinstance(stitching, dict):
                out["unmeasured"] += 1
                continue
            out["measured"] += 1
            cross_message = stitching.get("cross_message") is True
            cross_role = stitching.get("cross_role") is True
            out["cross_message"] += int(cross_message)
            out["cross_role"] += int(cross_role)
            out["stitched"] += int(cross_message or cross_role)
    if out["measured"]:
        out["rate_pct"] = round(100.0 * out["stitched"] / out["measured"], 1)
    return out


def _earlier(current: str | None, ts: str) -> str | None:
    """Earliest of two event stamps, ignoring empties. Stamps are written by
    one helper in a single UTC format, so lexicographic order is chronological
    (#562)."""
    if not ts:
        return current
    return ts if current is None or ts < current else current


def _stats_receipts(project_dir, usage: dict) -> dict:
    """Receipt-probe telemetry (this project, #919). `attempted`/`eligible`/
    `confirmed`/`contradicted`/`skipped` are lifetime usage.log counts scoped
    to this project's slug via `_RECEIPT_PROBE_USAGE_PREFIX` — the population
    size the confirmed/contradicted/skipped rates are measured against is
    `eligible`, not the checkpoint's whole item count, since only carried
    items with an `origin_session` naming an in-project origin ever enter the
    pool. `skipped` is the silent case #919 exists to expose: a probe that
    fired and answered nothing (no CLI, no cached pubkey, a killed deadline,
    garbage output) used to read identically to "0 confirmed, 0
    contradicted" — indistinguishable from a probe that never ran at all.

    `cured` is NOT a usage.log counter: it is read live off the rejection
    ledger's latest-verdict fold (`store.latest_receipt_verdicts`), because a
    cure changes an EXISTING item's standing rather than adding a new
    occurrence — `store.append_receipt_cure`'s own docstring is why a
    lifetime tally would double- or under-count depending on how many times
    a cured item happened to get re-probed.

    `enabled` is CURRENT config, not history: an install can flip
    DAIMON_RECEIPTS off after accumulating counts, and the render needs to
    tell "no receipts configured" apart from "configured, nothing has fired
    yet" — the exact ambiguity #919 diagnosed in the first place."""
    slug = store.project_slug(project_dir)
    attempted = usage.get(f"{_cli._RECEIPT_PROBE_USAGE_PREFIX}attempted:{slug}", 0) if slug else 0
    eligible = usage.get(f"{_cli._RECEIPT_PROBE_USAGE_PREFIX}eligible:{slug}", 0) if slug else 0
    confirmed = usage.get(f"{_cli._RECEIPT_PROBE_USAGE_PREFIX}confirmed:{slug}", 0) if slug else 0
    contradicted = usage.get(
        f"{_cli._RECEIPT_PROBE_USAGE_PREFIX}contradicted:{slug}", 0) if slug else 0
    skipped = usage.get(f"{_cli._RECEIPT_PROBE_USAGE_PREFIX}skipped:{slug}", 0) if slug else 0
    cured = sum(1 for row in store.latest_receipt_verdicts(project_dir=project_dir).values()
               if row.get("verdict") == "confirmed")
    return {"enabled": config.receipts_enabled(), "attempted": attempted,
            "eligible": eligible, "confirmed": confirmed,
            "contradicted": contradicted, "skipped": skipped, "cured": cured}


def _stats_checks(project_dir) -> dict:
    """Armed checks and their firings over the retained window (this
    project, #943 slice 5).

    `armed` and `proposed` come from the LEDGER, because that is where
    whether a check may run is decided; the manifest is a derived view and
    an audit of it is a different question (`daimon check sync --check`).
    The five firing counters come from the log, folded across every host:
    this process cannot know which host it is running on, and `daimon ruling
    checks` is the surface that splits them.

    `fired` is separate from `clean + violation + unresolved` on purpose. It
    counts rows, so an outcome this build does not recognise still proves the
    check RAN — the one fact constraint 2 exists to make visible. Never
    raises: a broken log must not take `stats` down with it."""
    from .. import checks  # local, like cli.hooks: not every verb pays for it

    counts = {"armed": 0, "proposed": 0}
    try:
        for record in refutations.listing(polarity="ruling",
                                          project_dir=project_dir):
            if not isinstance(record.get("check"), dict):
                continue
            lifecycle = record.get("check_lifecycle")
            if lifecycle in counts:
                counts[lifecycle] += 1
    except Exception:  # noqa: BLE001
        pass
    # #1095: a layer's active check arms here too — see `_status_checks`'s
    # identical addition for the full reasoning. `inherited_active` never
    # raises on its own, so this needs no guard of its own.
    #
    # #1102: excludes only an ACTIVE own copy (`own_active_ruling_ids`,
    # itself fail-open) — see `_status_checks`'s identical change for why
    # the unfiltered own-id set above under-counted a promoted ruling.
    own_active_ids = refutations.own_active_ruling_ids(project_dir)
    for record in refutations.inherited_active(project_dir):
        if record["refutation_id"] in own_active_ids:
            continue
        if isinstance(record.get("check"), dict):
            counts["armed"] += 1
    summary = checks.firing_summary(project_dir)
    totals = summary.totals
    # `log_state` travels with the counts. Without it an unreadable log is
    # byte-identical to an empty one on this line, and the render layer has
    # no way to tell "nothing ran" from "daimon cannot tell you".
    #
    # And a read that FAILED reports null rather than zero, the way
    # `ruling checks --json` does: a consumer reading the counts without
    # reading the state would otherwise conclude nothing ran. Only the
    # failed read is unknown. An absent log and a log holding no matching
    # rows both know the answer is zero and say so, so a fresh install stays
    # distinguishable from a broken one.
    known = summary.log_state != "unreadable"
    return {**counts,
            **{key: (totals[key] if known else None)
               for key in ("fired", "clean", "violation", "unresolved",
                           "denied")},
            "log_state": summary.log_state,
            # #955: the log is capped, so the counts above are a window's and
            # a consumer reading them as a machine's whole history would be
            # wrong. At the tail: key order is part of the --json contract.
            "window_since": summary.window_since}


def _stats_resolutions(project_dir, usage: dict) -> dict:
    """Resolution credit, by source (#480 slice 5) — who is closing loops,
    and whether their receipts hold. Two populations, kept honestly apart
    (#477's lesson, #478's fix): `human`/`agent_verified`/`agent_pending`
    fold THIS PROJECT's events.jsonl (store._events_path keys per project),
    `refused` reads usage.log, which is per-MACHINE (every project's CLI
    invocations share one file, #54's own design) — the render layer labels
    the refused line apart from the other three; never summed together.

    - `human`: lifetime COUNT OF EVENTS (not refs — a ref resolved twice
      over its life, e.g. corrected later, is two human decisions), with
      source="cli", kind="resolution" (the human `resolve` path's default —
      excludes `forget`'s "tombstone" kind and `log`'s freeform rows, which
      also default to source="cli" but are not resolve decisions; scar
      0025's own lesson: kind never isolates a fold on its own, so this
      filters kind explicitly rather than trusting it to), whose status
      is_resolved (a real resolution, not a reopen/candidate/corroboration
      row).
    - `agent_verified`: lifetime count of events with source="serializer"
      and status==capture.AGENT_VERIFIED_STATUS — the one call site that
      ever writes it (capture._verify_agent_resolutions, #480 slice 3).
    - `agent_pending`: refs whose FOLDED latest event is still a pending
      agent candidate — reuses capture._pending_agent_candidates over
      store.resolutions()'s fold rather than re-deriving the same status/
      source filter a second time (the same reuse briefing.withhold's #480
      slice 4 stamp makes).
    - `refused`: the `resolve:no-evidence` usage-log tag (#303/#482) — an
      agent that tried `--by agent` with no evidence, refused before any
      event was written.

    Fails open to zeroes on a broken/missing/unknown-project log, same
    stance as every other stats instrument here."""
    # #562: the lifetime fold spans the arrival of agent credit, so a store
    # older than the agent write path reports its whole history as human
    # credit. Before the first agent-attributable event the absence of agent
    # credit is UNFALSIFIABLE — the path may simply not have existed — so the
    # counter reports where that line falls instead of implying a comparison
    # across it. Derived from the events themselves rather than a hardcoded
    # release, so it generalizes to the next credit source added.
    #
    # Counters stay plain locals and the result dict is built once at the end:
    # a literal mixing ints with a nullable stamp types the whole mapping as
    # optional, and every `+= 1` below then reads as arithmetic on None.
    human = 0
    agent_verified = 0
    agent_since: str | None = None
    human_stamps: list[str] = []
    path = store._events_path(project_dir)
    if path is not None:
        for evt in jsonl.read(path).rows:
            if not evt.get("item_ref"):
                continue
            source = str(evt.get("source") or "")
            status = str(evt.get("status") or "")
            kind = str(evt.get("kind") or "")
            ts = str(evt.get("ts") or "")
            if (source in store.HUMAN_EVENT_SOURCES and kind == "resolution"
                    and store.is_resolved(evt)):
                human += 1
                human_stamps.append(ts)
            elif source == "serializer" and status == capture.AGENT_VERIFIED_STATUS:
                agent_verified += 1
                agent_since = _earlier(agent_since, ts)
            elif source == "agent" and kind == "resolution":
                # A claim the serializer has not verified yet is still proof
                # the path existed, which is the only question here.
                agent_since = _earlier(agent_since, ts)
    # No agent event at all: the whole human population predates any agent
    # credit, because there is none to have predated.
    human_before_agent = (human if agent_since is None
                          else sum(1 for t in human_stamps
                                   if t and t < agent_since))
    agent_pending = 0
    try:
        agent_pending = len(capture._pending_agent_candidates(
            store.resolutions(project_dir=project_dir)))
    except Exception:
        pass
    return {"human": human, "agent_verified": agent_verified,
            "agent_pending": agent_pending,
            "refused": usage.get("resolve:no-evidence", 0),
            "agent_since": agent_since,
            "human_before_agent": human_before_agent}


def _cmd_stats(args) -> int:
    """Aggregate what is already on disk. Nothing is transmitted anywhere —
    sharing the output is a deliberate act (the user pastes it)."""
    usage = _stats_usage()
    project = _cli._resolve_project(None)
    data = {"usage": usage, "capture": _stats_capture(),
            "store": _stats_store(), "retention": _stats_retention(),
            "events": _stats_events(project),
            "verification": _stats_verification(project),
            "resolutions": _stats_resolutions(project, usage),
            "receipts": _stats_receipts(project, usage),
            "recall": recall_telemetry.stats(),
            # #475 part 2: current-configuration posture, rendered next to
            # (never merged into) the historical fallback counts above.
            "rescue_posture": llm.rescue_posture(),
            # #943 slice 5: appended at the tail — `stats --json` key order is
            # the same contract `status --json` documents.
            "checks": _stats_checks(project),
            # #974 step 1: same rule, same tail.
            "stitching": _stats_stitching(project)}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    render.render_stats(data)
    return 0


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_stats = sub.add_parser(
        "stats",
        help="local usage + capture aggregates (#54) — nothing is transmitted; "
             "sharing the output is a deliberate paste",
    )
    p_stats.add_argument("--json", action="store_true", help="machine-readable output")
    p_stats.set_defaults(func=_cmd_stats)
