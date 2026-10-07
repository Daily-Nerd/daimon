"""`daimon status`, `verify-receipt` and `mcp serve` (moved out of cli/__init__.py, #1132 PR 5).

The health, world and ledger sections of the status payload, the suppressed
listing and the receipt verifier. `datetime` is imported here, so a test that
freezes the clock patches `daimon_briefing.cli.status.datetime`. Every name is
re-exported from `cli`.
"""

import functools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import (
    buckets,
    config,
    display,
    ledger_census,
    llm,
    recall,
    receipts,
    redact,
    refutations,
    render,
    requests,
    schema,
    serializer,
    store,
    teamsync,
    view,
    worldcheck,
)
from ..ledger import (
    _compute_outstanding,
    _format_age,
    _parse_serialize_log,
    _session_ledger,
    _spawns_in_window_count,
    _stats_capture,
)


# ---- status: "did my ending checkpoint get generated?" without grepping logs ----

# Shared with store (single copy; hook/daimon-session-brief.py keeps its own
# stdlib-only twin — see the docstring in store._created_epoch).
_created_epoch = store._created_epoch


def _checkpoint_info(path, now) -> dict:
    """Existence/identity/age of a latest-pointer file. Never raises. Age prefers
    the written `created` stamp (which survives pointer rotation) and falls back to
    file mtime for legacy checkpoints (#93)."""
    if path is None or not path.exists():
        return {"exists": False, "path": str(path) if path else None}
    created = format_version = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        session_id = data.get("session_id")
        created = data.get("created")
        format_version = data.get("format_version")
    except (OSError, json.JSONDecodeError):
        session_id = None  # torn/foreign file: still report presence + age
    epoch = _created_epoch(created)
    age = int(now - (epoch if epoch is not None else path.stat().st_mtime))
    return {
        "exists": True,
        "session_id": session_id,
        "format_version": format_version,
        "age_seconds": age,
        "age": _format_age(age),
        "path": str(path),
    }


def _write_worldcheck_ledger(rows, route) -> None:
    """Append worldcheck's reserved ledger rows at the write boundary (#439,
    #839). A POINTER and a REASON CODE, never the item's text (#376).

    Cure rows take the gated path. worldcheck emits them the same way it emits
    contradictions, because it writes nothing to disk by contract, so this is
    the first place that knows where the item currently stands. A cure for an
    item nothing contradicted changes nothing, and writing it anyway would
    turn a ledger of problems found into a ledger of work done."""
    # World rows additionally carry a hash binding target and assertion.
    # `check_name`, not `check`: the #943 `daimon check` verb family is
    # imported into this module under that name, and a loop variable shadowing
    # it is the kind of collision that is silent until the shadowed name is
    # needed in the same scope.
    for row in rows:
        item_ref, check_name, reason = row[:3]
        if check_name == worldcheck.LEDGER_CONFIRM_CHECK:
            store.append_receipt_cure(item_ref, project_dir=route)
        elif check_name in store.WORLD_CURE_CHECKS:
            store.append_world_cure(item_ref, check_name, row[3], project_dir=route)
        elif check_name in store.WORLD_CHECKS:
            store.append_verification(item_ref, check_name, reason,
                                      project_dir=route, claim_key=row[3])
        else:
            store.append_verification(item_ref, check_name, reason,
                                      project_dir=route)


def _status_health(proj, glob, outstanding, siblings, *, now,
                   disabled: bool = False,
                   global_fallback: bool = False,
                   legacy: tuple | None = None,
                   incomplete: list | None = None) -> dict:
    """Objective health verdict for `status`. Pure — `now` is injected. Warns only
    on data-driven signals: a NEWER phantom-child bucket (the #74 split), a missing
    project checkpoint, outstanding serialize failures, or the kill switch being
    set. No age thresholds.

    `global_fallback` is the opt-in's state (#793), injected for the same
    reason `now` and `disabled` are: it is environment, and a verdict that
    reads its own environment cannot be tested at the verdict level."""
    warnings: list[str] = []

    # #28: a stuck DAIMON_DISABLE=1 silently stops all capture — the single
    # most important thing status can say, so it leads the verdict.
    if disabled:
        warnings.append(
            "DAIMON_DISABLE is set — capture is OFF (no checkpoints are "
            "being written)"
        )

    # #963: a bucket written before 0.42.0 from this same path, sitting
    # unread beside the one daimon uses now. The sibling `split:` warning
    # below is the same shape and the same class of fact — history this
    # project produced that this project is not reading — so it is stated the
    # same way, next to it. Injected, like `now` and `disabled`: a verdict
    # that reads its own filesystem cannot be tested at the verdict level.
    if legacy:
        legacy_slug, legacy_path, holds = legacy
        held = (f"; it still holds {', '.join(holds)}" if holds else "")
        warnings.append(
            f"legacy: bucket {legacy_slug} was written before 0.42.0 from "
            f"this path and is not read{held}; "
            f"{_cli._migrate_command(legacy_path)}")
    for from_slug in (incomplete or []):
        warnings.append(
            f"partial: the migration from {from_slug} did not finish, so "
            f"that bucket is not read as part of this project's history")

    proj_mtime = (now - proj["age_seconds"]) if proj.get("exists") else None
    newer = [
        s for s in siblings
        if proj_mtime is None or s["mtime"] > proj_mtime
    ]
    for s in sorted(newer, key=lambda s: s["mtime"], reverse=True):
        sid = s["session_id"] or "unknown"
        age = _format_age(int(now - s["mtime"]))
        warnings.append(
            f"split: related bucket '{s['slug']}' has newer work "
            f"(session {sid}, {age} ago) — a subdir session may have split your history"
        )

    if not proj.get("exists"):
        # #793: this said the briefing falls back to the global pointer,
        # possibly another project's. That behavior is gone by default —
        # `brief` has suppressed the foreign body since #96 and SessionStart
        # injection stopped falling back for a known project in #785, both
        # now behind an explicit opt-in. status is the surface an operator
        # reads to learn what the system WILL do, so a warning naming a risk
        # the system no longer takes costs twice: it sends someone hunting a
        # closed leak, and it teaches them to discount the warnings beside it,
        # which are load bearing. The old sentence is kept for the case where
        # it is true, which is the case an operator most needs to hear.
        warnings.append(
            "no checkpoint for this project — briefing falls back to the "
            "global pointer (possibly another project), because "
            "DAIMON_BRIEF_GLOBAL_FALLBACK is set"
            if global_fallback else
            "no checkpoint for this project — its briefing will be empty "
            "(the global pointer is not used unless "
            "DAIMON_BRIEF_GLOBAL_FALLBACK=full is set)"
        )

    # Format drift on the checkpoint that would back a briefing (proj, else the
    # global fallback): a stored format_version that differs from the current one
    # means the schema changed under it, so the briefing may render partially.
    # Legacy checkpoints (no format_version) are silent — nothing to compare (#93).
    #
    # #294: the two directions are different events. Older-than-code is routine
    # drift (#93) — expected after a version bump, cleared by re-serializing.
    # Newer-than-code is impossible by construction (PROMPT_VERSION is a source
    # constant; code that stamps it must contain it) — a second install writing
    # to the same checkpoint dir, a downgraded install, or a corrupted/forged
    # stamp (#292), never a schema change. Unparseable versions fail soft into
    # the older-style wording rather than raising.
    active = proj if proj.get("exists") else glob
    fv = active.get("format_version")
    # `is not None`, not truthy: an absent key (legacy checkpoint, #93) stays
    # silent, but an explicitly stamped "" is a garbage value that still
    # deserves the fail-soft fallback wording below (#294).
    if fv is not None and fv != serializer.PROMPT_VERSION:
        order = schema.compare_format_versions(fv, serializer.PROMPT_VERSION)
        if order is not None and order > 0:
            warnings.append(
                f"checkpoint format {fv} claims a version newer than this "
                f"daimon's {serializer.PROMPT_VERSION} — a checkpoint cannot be "
                f"newer than the code that wrote it, so the stamp is unreliable "
                f"(check for a second daimon install writing to this checkpoint "
                f"dir, or a downgraded install)"
            )
        else:
            warnings.append(
                f"checkpoint format {fv} != current {serializer.PROMPT_VERSION} — "
                f"schema changed; briefing may render partially (re-serialize to refresh)"
            )

    # #936: a repair in flight is listed in the outstanding block but is not
    # a failure; counting it would warn about the thing heal is fixing.
    failed = [f for f in outstanding if f.get("kind") != "in-flight"]
    if failed:
        n = len(failed)
        msg = f"{n} session{'s' if n != 1 else ''} failed to serialize"
        # Only point at heal when it can actually repair something (#29) —
        # "run 'daimon heal'" followed by "nothing to heal" is a contradiction.
        if any(f.get("class") == "healable" for f in failed):
            msg += " — run 'daimon heal'"
        else:
            msg += " (not auto-repairable)"
        warnings.append(msg)

    if not warnings:
        verdict = "✓ fresh"
        if glob.get("same_session_as_project"):
            verdict += " — this project produced the most recent checkpoint"
        return {"ok": True, "verdict": verdict, "warnings": []}
    return {"ok": False, "verdict": "⚠ " + warnings[0], "warnings": warnings}


def _tail_log_info(path: Path, now: float) -> dict | None:
    """Tail of a breadcrumb log (recall-error.log): last non-empty line plus
    the file's age. Returns None when absent/empty/unreadable. Every line in
    that file IS an error by construction (recall._note_error), so no header
    anchoring — unlike serialize-crash.log, see _crash_log_info."""
    try:
        st = path.stat()
        if st.st_size == 0:
            return None
        with path.open("rb") as f:
            f.seek(max(0, st.st_size - 4096))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [ln.strip() for ln in tail.splitlines() if ln.strip()]
    if not lines:
        return None
    age = int(now - st.st_mtime)
    # #513: raw disk bytes reach stdout via status — the one display path
    # that never passed redact_text. Redacted at construction so the plain,
    # rich, and --json renders all inherit it.
    logged, _ = redact.redact_text(lines[-1])
    return {"last_line": logged, "age_seconds": age,
            "age": _format_age(age), "path": str(path)}


def _crash_log_info(path: Path, now: float) -> dict | None:
    """Tail of serialize-crash.log — the file spawn_serialize points child
    stderr at, read back by `status` (#28). A crash is ONLY what carries the
    #92 excepthook header (`--- crash <iso> pid=… cmd=… ---`): the file is
    raw child stderr, so stray lastResort warnings land there too and used to
    misreport as a crash (#194). Reports the LAST crash — age from the
    header's stamp (mtime only as fallback: a later stray write must not
    re-age an old crash), last_line the traceback's exception line."""
    try:
        st = path.stat()
        if st.st_size == 0:
            return None
        with path.open("rb") as f:
            f.seek(max(0, st.st_size - 4096))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = tail.splitlines()
    hdr = None
    for i, ln in enumerate(lines):
        if ln.startswith("--- crash "):
            hdr = i
    if hdr is None:
        return None  # warnings-only file (legacy lastResort strays): no crash
    block = lines[hdr + 1:]
    # The exception line: last unindented line directly following an indented
    # one (traceback frames are indented; the raising line is not). Falls back
    # to the block's last non-empty line, then to the header itself (a child
    # killed mid-write leaves a bare header).
    last_line = None
    for prev, ln in zip(block, block[1:]):
        if ln.strip() and not ln[:1].isspace() and prev[:1].isspace():
            last_line = ln.strip()
    if last_line is None:
        nonempty = [ln.strip() for ln in block if ln.strip()]
        last_line = nonempty[-1] if nonempty else lines[hdr].strip()
    age = None
    parts = lines[hdr].split()
    if len(parts) >= 3:
        try:
            ts = datetime.strptime(parts[2], "%Y-%m-%dT%H:%M:%SZ")
            age = int(now - ts.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass  # unstamped/foreign header: fall back to file mtime
    if age is None:
        age = int(now - st.st_mtime)
    # #513: same rule as _tail_log_info — the traceback's exception line is
    # raw child stderr and can embed a credential (requests errors echo URLs,
    # config errors echo the offending value).
    logged, _ = redact.redact_text(last_line)
    return {"last_line": logged, "age_seconds": age,
            "age": _format_age(age), "path": str(path)}


def _print_suppressed(project) -> int:
    """`daimon status --suppressed` (#103): the visibility answer to brief's
    silent-suppression note ("N resolved item(s) withheld — `daimon status
    --suppressed` to list"). Formatting only: `view.suppressed` decides what
    is resolved, quarantined or forgotten, so the resolved/live split stays in
    exactly one place. A quarantined row names the item and the record, never
    the text; a forgotten value is not here at all. Reads ONLY this project's
    own latest checkpoint (Route.OWN, same rule as carry #94): listing another
    project's withheld items under this project's status would be worse than
    listing none. Fails CLOSED like brief and loops (#1132 PR 7b): a view that
    raises is one error line and rc 2, never "no suppressed items"."""
    try:
        sup = view.suppressed(project, time.time())
    except Exception as exc:  # noqa: BLE001 — reported, never listed around
        print("error: the suppressed list could not be built "
              f"({type(exc).__name__}); nothing was rendered", file=sys.stderr)
        return 2
    for note in sup.notes:
        print(note)
    if not (sup.resolved or sup.withheld or sup.closed or sup.candidates):
        print("no suppressed items")
        return 0
    if sup.resolved or sup.withheld:
        print(f"suppressed items ({len(sup.resolved) + len(sup.withheld)}):")
    for row in sup.resolved:
        item, evt = row.item, row.event
        item_id = item.get("id") or "-"
        text = str(item.get("text") or "").strip()
        status = str(evt.get("status") or "")
        ts = str(evt.get("ts") or "")
        note = str(evt.get("note") or "").strip()
        paren = f"{status} {ts}"
        if note:
            paren += f", {note}"
        if item.get("restated_after_resolve") is True:
            # #980: carry matched this session's item to a closed one and
            # handed it the closed id; the wording shown is this
            # session's own, so a person can tell a match from a
            # resolution they recorded.
            paren += ("; identity inherited from a resolved item, "
                      "wording is this session's own")
        print(f"  {item_id}  [{row.field.key}] {text}  ({paren})")
    for w in sup.withheld:
        print(f"  {w.item_id or '-'}  [{w.kind}] {display.withheld_marker(w)}")
    if sup.closed:
        print(f"{sup.closed} item(s) withheld while the trust ledger "
              "cannot be read")
    if sup.candidates:
        # #14: machine SUGGESTIONS, not resolutions — a separate subsection
        # so they never read as confirmed suppressions.
        print("likely superseded (unconfirmed):")
        for key, item, evt in sup.candidates:
            item_id = item.get("id") or "-"
            text = str(item.get("text") or "").strip()
            new_id = item.get("_supersede_candidate") or "-"
            print(f"  {item_id}  [{key}] {text}  -> {new_id}")
            # #111: both a confirm and a reject path — a human who disagrees
            # with the guess must not have to reach for evidence machinery.
            print(f"    confirm: daimon resolve {item_id} --status superseded-by:{new_id}")
            print(f"    reject: daimon reverify {item_id}")
    return 0


def _cmd_verify_receipt(args) -> int:
    """Verify a checkpoint's signed provenance receipt (#204). Default target is
    the current project's latest checkpoint; a session id can be passed
    explicitly. rc 0 verified / 1 failed / 2 unable (see receipts.verify_receipt)."""
    _cli._note_usage("verify-receipt")
    session_id = getattr(args, "session_id", None)
    if not session_id:
        project = _cli._resolve_project(args.project)
        # #791: the docstring above says the default target is THIS project's
        # latest checkpoint, and the fallback let it be another project's. An
        # un-routed checkpoint is still this project's to verify.
        checkpoint = store.read_latest_body(project_dir=project,
                                            route=store.Route.OWN_ELSE_GLOBAL,
                                            admit=store.Admit.OWN_OR_UNROUTED)
        if not isinstance(checkpoint, dict) or not checkpoint.get("session_id"):
            print("no checkpoint for this project yet — nothing to verify")
            return 2
        session_id = checkpoint["session_id"]
    rc, lines = receipts.verify_receipt(str(session_id))
    render.render_lifecycle_lines(lines)
    return rc


# The silent-capture alarm (#265) ships the FAIL tier ONLY: sessions were
# observed but ZERO checkpoints landed. The issue also describes a WARN "capture
# ratio low" tier — deferred until the ratio threshold is calibrated against real
# capture distributions. An uncalibrated cutoff false-alarms on low-activity
# projects (this repo's overlap thresholds did exactly that), so below
# _CAPTURE_MIN_SESSIONS spawns the probe stays SILENT — too little signal to
# judge — rather than guessing OK. 3 is the smallest count that reads as a
# pattern rather than a one-off, and matches the retention-window scope.
_CAPTURE_MIN_SESSIONS = 3


def _capture_alarm(now: float) -> dict | None:
    """Silent-capture probe (#265): compare hook spawns OBSERVED against
    checkpoints WRITTEN over the retention window, machine-wide (serialize.log
    and the checkpoint store are per-machine, not per-project). Returns a FAIL
    payload only when >= _CAPTURE_MIN_SESSIONS sessions spawned but ZERO
    checkpoints landed in the window; otherwise None (no verdict — the WARN
    ratio tier is deferred, see _CAPTURE_MIN_SESSIONS). Reuses the same in-window
    spawn logic as the stats stale-hook warning. `now` is epoch seconds; both
    the ledger (tz-aware) and store (epoch) cutoffs derive from it for a single
    deterministic window edge."""
    cutoff_epoch = now - _cli._RETENTION_WINDOW_DAYS * 86400
    cutoff_dt = datetime.fromtimestamp(cutoff_epoch, tz=timezone.utc)
    spawns = _spawns_in_window_count(cutoff_dt)
    if spawns < _CAPTURE_MIN_SESSIONS:
        return None
    checkpoints = store.checkpoints_written_since(cutoff_epoch)
    if checkpoints > 0:
        return None
    return {"verdict": "fail", "spawns": spawns, "checkpoints": checkpoints,
            "window_days": _cli._RETENTION_WINDOW_DAYS}


def _status_checks(project_dir, now: float):
    """#943 slice 5: armed checks, their liveness and their manifest, or None.

    dict-or-None like `handoff` and `recall_index`, never a fabricated zero
    shape: a machine that has never used the feature gets no line at all,
    which is the quiet-by-default rule the team and receipts lines follow.

    `proposed` is counted beside `armed` because a candidate arms nothing but
    is still the thing a human has to act on, and a status that hid it would
    make an unratified check invisible until someone ran `ruling list`."""
    from .. import checks

    counts = {"armed": 0, "proposed": 0}
    for record in refutations.listing(polarity="ruling",
                                      project_dir=project_dir):
        if not isinstance(record.get("check"), dict):
            continue
        lifecycle = record.get("check_lifecycle")
        if lifecycle in counts:
            counts[lifecycle] += 1
    # #1095: a layer's active check arms here too (`checks.sync_layers`, and
    # `armed_for` matches this project by path prefix), so `status` must
    # count it or a fresh machine reads "0 armed" under a check that is, in
    # fact, gating every action. `inherited_active` is always `state ==
    # "active"`, so a check-carrying row is always lifecycle `armed`, and it
    # never raises on its own (fail-open to `[]`), so this needs no guard of
    # its own.
    #
    # #1102: the exclusion set is `own_active_ruling_ids`, not every own id
    # from the loop above — a ruling promoted to a layer and then retired,
    # overturned, or left a candidate in this project's own bucket must not
    # hide the layer's active copy from this count.
    own_active_ids = refutations.own_active_ruling_ids(project_dir)
    for record in refutations.inherited_active(project_dir):
        if record["refutation_id"] in own_active_ids:
            continue
        if isinstance(record.get("check"), dict):
            counts["armed"] += 1
    if not counts["armed"] and not counts["proposed"]:
        return None
    summary = checks.firing_summary(project_dir)
    last_ts = summary.totals["last_ts"]
    age = ""
    if last_ts:
        try:
            stamp = datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%SZ")
            age = _format_age(now - stamp.replace(
                tzinfo=timezone.utc).timestamp())
        except ValueError:
            age = ""  # an unexpected stamp reports the fact without an age
    return {**counts, "last_ts": last_ts, "age": age,
            "log_state": summary.log_state,
            "drift": checks.audit(project_dir).drift}


def _status_ledgers(slug) -> dict | None:
    """The ledger census for `status`: this bucket's ledgers plus the
    machine-level ledger files under "other". None when the project has no
    bucket name to census."""
    if not slug:
        return None
    return {**ledger_census.census_bucket(slug),
            "other": ledger_census.census_machine()}


def _status_world(project_arg=None) -> dict:
    """Every status fact, computed once — the single source for the plain
    render, `status --json`, and the MCP status tool (#261)."""
    project = _cli._resolve_project(project_arg)
    # #963: the value BEFORE resolution — resolution is exactly what collapses
    # the difference the pre-0.42.0 bucket rule turns on, so the legacy facts
    # below are derived from this and the routing facts from `project`.
    raw_project = _cli._raw_project(project_arg)
    identity: dict = {
        "cwd": str(Path(project_arg or ".").expanduser().resolve()),
        "git_root": project,
        "slug": store.project_slug(project),
    }
    now = time.time()
    proj = _checkpoint_info(store.project_latest_path(project), now)
    glob = _checkpoint_info(store.global_latest_path(), now)
    same = bool(
        proj["exists"] and glob["exists"] and proj["session_id"] == glob["session_id"]
    )
    glob["same_session_as_project"] = same
    last = _parse_serialize_log(config.log_dir() / "serialize.log", now)
    try:
        _ledger_text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        _ledger_text = ""
    outstanding = _compute_outstanding(_ledger_text, now)
    crash = _crash_log_info(config.log_dir() / "serialize-crash.log", now)
    recall_error = _tail_log_info(config.log_dir() / "recall-error.log", now)
    # #233: dark-matter visibility — read-only peek at the existing index;
    # None (absent/corrupt db) simply drops the line, never triggers a rebuild.
    recall_index = recall.index_attribution()
    disabled = config.is_disabled()
    # Skips are terminal by design (too-short sessions), but invisible skips
    # read as captured sessions (#28) — count them for display.
    skipped_recent = sum(
        1 for e in _session_ledger(_ledger_text, now).values()
        if e["result_kind"] == "skipped"
    )
    siblings = store.sibling_buckets(project)
    # Silent-capture alarm (#265): machine-wide spawns-vs-checkpoints over the
    # window. A FAIL payload (or None) renders at the very TOP of status — a
    # class of failure that otherwise hides until a briefing turns up empty.
    capture_alarm = _capture_alarm(now)
    # One-line pointer only when installed hook copies have drifted (#266);
    # silent on a clean machine. Cheap: hashes a handful of small files.
    hook_drift = _cli._hook_drift_present()
    # #554: the same pointer for the one host `hooks status` cannot audit —
    # Claude Code's hooks ship inside the plugin, which updates on its own
    # schedule. None on a machine with no plugin, so non-plugin users stay
    # silent.
    plugin_drift = _cli._plugin_drift_present()
    # #1006: the skill goes stale the same way the hooks do, and until now
    # nothing said so. An out-of-date skill is instructions, so it fails
    # silently and in the direction of the agent doing an older thing well.
    skill_drift = _cli._skill_drift_present()
    # #341/#475 part 2: whether a rescue path exists for the CURRENTLY
    # CONFIGURED primary. llm.rescue_posture() is the single resolver (it
    # calls resolve_backend(), the same decision chat() dispatches on) —
    # rescue_gap is re-expressed through it rather than re-implementing the
    # "auto" cascade inline a second time (two copies of one decision drift
    # the moment either changes). rescue_gap keeps its EXACT existing
    # meaning (posture == "gap") for JSON back-compat; rescue_posture is the
    # richer value new consumers get.
    rescue_posture = llm.rescue_posture()
    rescue_gap = rescue_posture == "gap"
    # #475 part 2: the "none" warning below is gated on real errors, not on
    # posture alone (the #349/#477 false-positive shape) — an operator who
    # pinned a `command` backend deliberately must not see a permanent
    # warning about a permanent property of their own choice. The 14-day
    # capture window _stats_capture() already computes is the same window
    # `daimon stats` reports, so "no errors yet" here means the same thing
    # it means there.
    rescue_window_errors = _stats_capture()["window"]["errors"]
    # #963: the pre-0.42.0 bucket for THIS path, and where this bucket came
    # from if it was migrated. Read from the raw project value, never the
    # resolved one: resolution is exactly what collapses the difference the
    # legacy rule turns on. Fail-open like every other best-effort status
    # fact — an unreadable checkpoint dir must not take `status` down.
    try:
        legacy_slug = buckets.legacy_bucket(raw_project)
        legacy_holds = list(buckets.legacy_leftovers(raw_project))
        aliases = buckets.alias_provenance(identity["slug"])
        incomplete = buckets.incomplete_for(identity["slug"])
    except Exception:
        legacy_slug, legacy_holds, aliases, incomplete = None, [], (), ()
    identity["legacy_slug"] = legacy_slug
    identity["aliases"] = [a["slug"] for a in aliases]
    # One line per (from, to) pair. A hand-edited receipt file, or an older
    # daimon that appended a row per re-run, otherwise prints the same
    # migration several times and reads as several migrations.
    seen_pairs: set = set()
    migrated: list[dict] = []
    for entry in aliases:
        pair = (entry["slug"], identity["slug"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        migrated.append(dict(entry))
    identity["migrated"] = migrated
    identity["incomplete"] = [str(r.get("from_slug") or "")
                              for r in incomplete]
    health = _status_health(proj, glob, outstanding, siblings, now=now,
                            disabled=disabled,
                            global_fallback=config.brief_global_fallback(),
                            legacy=((legacy_slug, raw_project, legacy_holds)
                                    if legacy_slug else None),
                            incomplete=identity["incomplete"])
    # ONE objective team line (#113), only when a team remote exists — the #84
    # health-line rule: no line, no false alarms when the team feature is unused.
    team = teamsync.status_line()
    # One receipts line, only when the feature is on (#204) — mirrors the team
    # line's "no line when unused" rule so status stays quiet by default.
    receipts_line = receipts.status_line(project)
    # #404: forget-suppression hit accounting — the count + most-recent stamp,
    # surfaced only when non-zero (same "quiet by default" rule). Claim
    # snapshots stay in the ledger; status shows only the number.
    forget_hits = store.forget_hit_stats(project)
    # #694 PR 3: the requests summary — {open_sent, awaiting_you}. Fail-open,
    # same posture as every other best-effort status fact: a broken composer
    # must never take `status` down with it.
    try:
        request_counts = requests.status_counts(project_dir=project)
    except Exception:
        request_counts = {"open_sent": 0, "awaiting_you": 0}
    # #662: a waiting handoff baton, or None once consumed/absent — the same
    # optional-fact convention as recall_index (dict-or-None, never a
    # fabricated zero-shape). store.active_handoff already encodes "waiting
    # only" (None once two sessions have serialized past it, #523); the
    # note text stays OUT of status by the issue's own instruction — it
    # belongs to `brief`, not here. Fail-open like every other best-effort
    # status fact: a broken reader must never take status down with it.
    try:
        _baton = store.active_handoff(project)
        handoff = {"written_at": _baton["ts"]} if _baton else None
    except Exception:
        handoff = None
    # #943 slice 5: armed checks and whether any ever fired. Fail-open like
    # every other best-effort status fact — a broken firing log or an
    # unreadable checks directory must never take `status` down with it.
    try:
        checks_fact = _status_checks(project, now)
    except Exception:
        checks_fact = None
    # #1132 PR 2b: the ledger census. Read-only and fail-open like every other
    # best-effort status fact; None drops the lines and nulls the payload field.
    try:
        ledgers = _status_ledgers(identity["slug"])
    except Exception:
        ledgers = None
    # 0 = some checkpoint would back a briefing; 1 = neither pointer exists
    # (cheap existence test for scripts / the FR #23 hook guard).
    rc = 0 if (proj["exists"] or glob["exists"]) else 1
    return {
        "project": project, "proj": proj, "glob": glob, "same": same,
        "last": last, "outstanding": outstanding, "siblings": siblings,
        "identity": identity, "health": health, "team": team, "crash": crash,
        "disabled": disabled, "skipped_recent": skipped_recent,
        "recall_error": recall_error, "recall_index": recall_index,
        "receipts": receipts_line, "capture_alarm": capture_alarm,
        "hook_drift": hook_drift, "plugin_drift": plugin_drift,
        "skill_drift": skill_drift,
        "rescue_gap": rescue_gap,
        "rescue_posture": rescue_posture, "rescue_window_errors": rescue_window_errors,
        "forget_hits": forget_hits, "requests": request_counts,
        "handoff": handoff, "checks": checks_fact, "ledgers": ledgers,
        "rc": rc,
    }


def status_payload(project_arg=None) -> tuple:
    """(json payload, rc) — byte-identical facts for `status --json` and the
    MCP status tool. Payload key order is part of the --json contract."""
    w = _status_world(project_arg)
    proj = {"dir": w["project"], "slug": w["identity"]["slug"], **w["proj"]}
    payload = {
        "project": proj, "global": w["glob"], "last_serialize": w["last"],
        "outstanding": w["outstanding"], "siblings": w["siblings"],
        "health": w["health"], "team": w["team"], "crash": w["crash"],
        "disabled": w["disabled"], "skipped_recent": w["skipped_recent"],
        "recall_error": w["recall_error"], "recall_index": w["recall_index"],
        "receipts": w["receipts"], "capture_alarm": w["capture_alarm"],
        "hook_drift": w["hook_drift"], "plugin_drift": w.get("plugin_drift"),
        "skill_drift": w.get("skill_drift"),
        "rescue_gap": w["rescue_gap"],
        "rescue_posture": w["rescue_posture"],
        "forget_hits": w["forget_hits"],
        "requests": w["requests"],
        "handoff": w["handoff"],
        # #943 slice 5: appended at the tail — payload key order is
        # part of the --json contract.
        "checks": w["checks"],
        # #963: the bucket identity, appended at the tail for the same
        # reason. `project.slug` above already names the bucket in use; this
        # adds the two facts a machine needs to act on a move — the
        # unmigrated legacy bucket (null when there is none) and the slugs
        # this bucket absorbed.
        "identity": w["identity"],
        # #1132 PR 2b: the ledger census, appended at the tail for the same
        # reason. Counts and states only; `null` when the census could not run.
        "ledgers": w["ledgers"],
    }
    return payload, w["rc"]


def _cmd_status(args) -> int:
    _cli._note_usage("status")
    if getattr(args, "suppressed", False):
        return _print_suppressed(_cli._resolve_project(args.project))
    if args.json:
        payload, rc = status_payload(args.project)
        print(json.dumps(payload, indent=2))
        return rc
    w = _status_world(args.project)
    render.render_status({
        "project": w["project"], "proj": w["proj"], "glob": w["glob"],
        "same": w["same"], "last": w["last"], "outstanding": w["outstanding"],
        "identity": w["identity"], "health": w["health"], "team": w["team"],
        "crash": w["crash"], "skipped_recent": w["skipped_recent"],
        "recall_error": w["recall_error"], "recall_index": w["recall_index"],
        "receipts": w["receipts"], "capture_alarm": w["capture_alarm"],
        "hook_drift": w["hook_drift"], "plugin_drift": w.get("plugin_drift"),
        "skill_drift": w.get("skill_drift"),
        "rescue_gap": w["rescue_gap"],
        "rescue_posture": w["rescue_posture"],
        "rescue_window_errors": w["rescue_window_errors"],
        "forget_hits": w["forget_hits"],
        "requests": w["requests"],
        "handoff": w["handoff"],
        "checks": w["checks"],
        "ledgers": w["ledgers"],
    })
    return w["rc"]


def _cmd_mcp_serve(args) -> int:
    """#261: blocking stdio MCP server. No usage note here — each tool call
    notes `mcp:<tool>` itself; serving is not reading."""
    from .. import mcp_server
    return mcp_server.serve()


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_status = sub.add_parser(
        "status", help="checkpoint presence/age + last serialize outcome",
        epilog="Examples:\n"
               "  daimon status\n"
               "  daimon status --project . --json\n",
    )
    p_status.add_argument(
        "--project",
        help="project directory to check (default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_status.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_status.add_argument(
        "--suppressed", action="store_true",
        help="list items withheld from the briefing as resolved (#103)",
    )
    p_status.set_defaults(func=_cmd_status)

    p_vr = sub.add_parser(
        "verify-receipt",
        help="verify a checkpoint's signed provenance receipt via the vitni CLI (#204)",
        epilog="Examples:\n"
               "  daimon verify-receipt\n"
               "  daimon verify-receipt <session-id>\n",
    )
    p_vr.add_argument(
        "session_id", nargs="?",
        help="session id to verify (default: this project's latest checkpoint)")
    p_vr.add_argument(
        "--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_vr.set_defaults(func=_cmd_verify_receipt)


def register_mcp(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_mcp = sub.add_parser(
        "mcp",
        help="opt-in read-only MCP server (#261): recall/brief/projects/"
             "status as a self-describing tool surface for MCP-capable hosts")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser = functools.partial(  # type: ignore[method-assign]
        mcp_sub.add_parser, formatter_class=fmt)
    pm_serve = mcp_sub.add_parser(
        "serve",
        help="serve MCP over stdio until EOF — reads only, never writes; "
             "register in your host's MCP config (see the docs site)")
    pm_serve.set_defaults(func=_cmd_mcp_serve)
