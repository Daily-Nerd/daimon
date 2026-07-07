"""Dogfood CLI — works WITHOUT hermes, on a plain text/markdown transcript.

    daimon serialize <transcript-file>   transcript -> checkpoint (+latest)
    daimon brief                          latest checkpoint -> briefing on stdout
    daimon recall <query...>              FTS5 search over local + team
                                         checkpoint history (derived index)
    daimon status [--project DIR] [--json]
                                         checkpoint presence/age + last
                                         serialize outcome from the log
    daimon heal                          re-serialize the most recent
                                         FAILED session if safe (#26)
    daimon configure [--backend ...]     detect the resolved LLM backend
                                         and fill gaps in ~/.daimon/env
    daimon write-checkpoint [--project DIR] [--source S]
                                         store a checkpoint read as JSON on
                                         stdin (the #23 introspection path)
"""

import argparse
import functools
import getpass
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import anchor, briefing, carry, config, configure, harvest, llm, recall, render, serializer, store, teamsync, transcript
from . import __version__

# Module-level seam so tests can inject a fake LLM client.
_chat = llm.chat


def _formatter_class():
    """argparse help formatter: RichHelpFormatter-family when rich-argparse
    (daimon[pretty]) is importable, else the stock formatter everywhere
    already used it. Unlike render.supports_rich(), this needs no TTY gate of
    its own — rich's Console auto-detects a non-terminal stream, so `--help`
    degrades to plain text automatically when piped or redirected. It DOES
    need to honor the same DAIMON_PLAIN/NO_COLOR opt-outs supports_rich checks
    (same truthiness semantics), because rich-argparse's own Console has no
    idea what DAIMON_PLAIN means — left ungated, `--help` would ignore a
    user's explicit plain-mode request while every other command honors it."""
    if os.environ.get("DAIMON_PLAIN", "").strip().lower() in render._TRUTHY:
        return argparse.RawDescriptionHelpFormatter
    if os.environ.get("NO_COLOR") is not None:
        return argparse.RawDescriptionHelpFormatter
    try:
        from rich_argparse import RawDescriptionRichHelpFormatter
        return RawDescriptionRichHelpFormatter
    except ImportError:
        return argparse.RawDescriptionHelpFormatter


def _prompt(question: str) -> str:
    """Raw interactive prompt — a tiny seam so tests can monkeypatch input."""
    return input(question).strip()


def _resolve_project(arg) -> str:
    """Project dir for routing: explicit --project, else DAIMON_PROJECT_DIR, else cwd.

    Resolved to an absolute path BEFORE the store slugs it: the store derives
    slugs from absolute paths, so a relative "." (or a bare manual re-run) would
    otherwise never match a written checkpoint's slug.

    Then normalized to the git toplevel (#74) so a subdir session shares the ONE
    repo bucket; resolve_project_root returns the input unchanged when it is not a
    git repo, so the absolute-path fallback above still holds.
    """
    project = arg or config.project_dir() or os.getcwd()
    resolved = str(Path(project).expanduser().resolve())
    return config.resolve_project_root(resolved)


def _append_serialize_log(line: str) -> None:
    """Append a result line to serialize.log so manual/CLI serializes are
    visible to `status`, not only hook-spawned ones (FR #27). Best-effort:
    logging must never break a serialize."""
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _append_retry_log(session_id: str, prior: str) -> None:
    """Mark a #26 heal retry in serialize.log BEFORE re-serializing. The line is
    a TIMESTAMPED spawn-style marker (matching the hook spawn-line stamp format)
    so `status` surfaces it AND the dedup check can find it later — one retry per
    session, ever. Best-effort: never break a heal."""
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} session-start: retry serialize for {session_id} (prior: {prior})\n")
    except OSError:
        pass


def _note_usage(command: str) -> None:
    """One LOCAL line per deliberate read command (#54): `<iso> <command>` to
    usage.log. Never transmitted anywhere — `daimon stats` aggregates it so a
    user can answer "do I actually re-read briefings?" (and choose to share
    the answer). Best-effort, and silent under the kill switch: disabled
    means daimon writes nothing."""
    if config.is_disabled():
        return
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "usage.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} {command}\n")
    except OSError:
        pass


def _preflight_error(path: Path) -> str | None:
    """Credential pre-flight, mirroring llm.chat's routing (#52): an API key
    and model are required only when the resolved transport is llm-bound.
    The command / claude-cli backends need neither — pre-flight used to demand
    them anyway, so a command-backend user could never serialize (and the
    zero-config claude path only worked when a stray gateway key happened to
    be in env). Error lines carry the transcript suffix (#49) so the ledger
    attributes the failure to its session and heal can retry once fixed."""
    backend = config.llm_backend()
    if backend in ("command", "claude-cli"):
        return None
    if backend == "auto" and llm._resolve_command() is not None:
        return None  # llm.chat will route to the command CLI, key-free
    if not config.llm_api_key():
        return ("error: no LLM API key — set DAIMON_LLM_API_KEY "
                f"(env or ~/.daimon/env) (transcript: {path})")
    if not config.llm_model():
        return ("error: no LLM model — set DAIMON_LLM_MODEL "
                f"(env or ~/.daimon/env) (transcript: {path})")
    return None


def _run_serialize(transcript_path: Path, project: str | None) -> int:
    """Serialize one transcript to a checkpoint routed to `project` (used AS-IS;
    None => global pointer only, NO cwd fallback). The caller decides routing —
    this never calls _resolve_project, so `heal` can route to the FAILED
    session's project rather than the heal-time cwd.

    Every result line is built once into `msg`, printed, AND logged via
    _append_serialize_log — the logged string is byte-identical to the printed
    one so _RESULT_OK_RE / _RESULT_ERR_RE (raw, no timestamp) still match it.
    (No "(superseded by newer checkpoint)" hint here: result lines carry no
    timestamp to compare against a checkpoint mtime — out of scope, FR #27.)
    Returns the rc."""
    path = transcript_path
    try:
        messages = transcript.from_file(path)
    except FileNotFoundError:
        msg = f"error: transcript not found: {path}"
        print(msg, file=sys.stderr)
        _append_serialize_log(msg)
        return 2

    # Pre-flight missing credentials so the error names them before any LLM work
    # (a conflated message cost a live debugging round-trip — see PR #12 fallout).
    if _chat is llm.chat:
        preflight = _preflight_error(path)
        if preflight is not None:
            print(preflight, file=sys.stderr)
            _append_serialize_log(preflight)
            return 1

    session_id = path.stem
    # Elapsed time lands in serialize.log — checkpoint generation runs 4-25 min
    # in production and was invisible before this.
    llm.reset_fallback()  # #28: detect a silent backend downgrade during THIS run
    start = time.monotonic()
    try:
        checkpoint = serializer.serialize_strict(session_id, messages, chat=_chat)
    except serializer.TooShortError as exc:
        msg = f"skipped serialize for {session_id}: {exc}"
        print(msg)
        _append_serialize_log(msg)
        return 0
    except serializer.SerializeError as exc:
        elapsed = int(time.monotonic() - start)
        msg = f"error: {exc} (transcript: {path}) after {elapsed}s"
        print(msg, file=sys.stderr)
        _append_serialize_log(msg)
        return 1
    # `created` = when the SESSION ended, not when this write happens (#123).
    # Stamped here — not left to store's setdefault-now — so a heal/re-serialize
    # of an old transcript carries its true age and store's pointer guard can
    # keep it from stealing `latest` from a newer session.
    checkpoint["created"] = _session_end_stamp(path)
    if config.carry_enabled():
        # Deterministic carry (#33 Phase 2): fold the previous checkpoint's
        # unresolved items in BEFORE the write rotates it away. Clock = this
        # checkpoint's own stamp (scar: never default to wall clock when a
        # stamp exists), wall time only as fallback for stampless paths.
        # Advisory feature — a raise here must never cost us the checkpoint
        # itself (a briefing missing carried items is strictly better than
        # no briefing at all; same idiom as harvest.run's swallow below).
        try:
            # fallback=False (#94): on a project's first serialize there is no
            # per-project pointer, and the global pointer is another project's
            # checkpoint — carrying from it would write foreign items into
            # this project's bucket permanently. No prev -> no carry.
            prev = store.read_latest(project, fallback=False)
            now = store._created_epoch(checkpoint.get("created")) or time.time()
            events = store.resolutions(project_dir=project)
            resolved = frozenset(ref for ref, evt in events.items()
                                 if store.is_resolved(evt))
            checkpoint = carry.merge(checkpoint, prev, now,
                                     floor=config.carry_floor(),
                                     cap=config.carry_max(),
                                     resolved=resolved)
            # #14: text-target supersession links bound to prev-item ids ->
            # candidate events, gated so a human verdict is never overridden
            # (see _emit_supersede_candidates). Same fail-open try as merge
            # itself — a broken emission must never cost the checkpoint.
            # Stamp ids BEFORE binding: fresh natives are only stamped inside
            # write_checkpoint (after this block), so without this every new
            # item binds with new_id="" and the event carries no target.
            # Setdefault-idempotent — write_checkpoint's re-stamp no-ops.
            store._stamp_item_ids(checkpoint)
            pairs = carry.bind_links(checkpoint, prev)
            _emit_supersede_candidates(pairs, events, project)
        except Exception:  # keep the unmerged checkpoint, proceed to write
            pass
    out = store.write_checkpoint(session_id, checkpoint, project_dir=project)
    elapsed = int(time.monotonic() - start)
    msg = f"wrote checkpoint: {out} (took {elapsed}s)"
    if llm.fallback_used():
        # Trailing marker (#28): the configured backend failed and the weaker
        # command fallback produced this checkpoint — success, but downgraded.
        # Suffix-safe: _RESULT_OK_RE/_LEDGER_OK_RE are prefix-anchored.
        msg += " [fallback backend]"
    print(msg)
    _append_serialize_log(msg)
    # Opt-in scar-candidate harvest (#100), mirroring the hermes host wiring
    # (hooks.on_session_end). It runs AFTER the result line is printed AND logged,
    # and ANY failure is swallowed here — the harvest must never change this
    # function's rc nor disturb the byte-identical print/log result contract above.
    # harvest.run itself no-ops on project=None and on repos with no .scars/, so the
    # call site stays a thin gate; cli has no logger, so best-effort is silent (the
    # same idiom as _append_serialize_log's swallow).
    if config.scar_harvest_enabled():
        try:
            harvest.run(messages, project_root=project, session_id=session_id)
        except Exception:  # a broken harvest must not fail the serialize
            pass
    return 0


def _emit_supersede_candidates(pairs, events: dict, project) -> int:
    """Turn `carry.bind_links` triples into `supersede-candidate:*` events,
    gated so a machine SUGGESTION never overrides a human verdict (#14,
    human-speaks-once).

    `events` is the SAME `store.resolutions` fold the serialize block already
    fetched for `resolved` — reused, not re-read, so this stays consistent
    with the resolved set computed moments earlier in the same call.

    Gate per (old_id, new_id, old_text) triple:
    (a) prior = events.get(old_id) — the latest lifecycle fact for old_id.
    (b) prior exists and its source isn't "serializer" -> a HUMAN spoke
        (confirmed via superseded-by, rejected via reopened, or anything
        else typed by a person) -> skip, forever. The gate itself is why a
        latest-event check is enough for permanence: machine events only
        ever land through this function, and this function refuses to
        write over a non-serializer prior, so once a human event is latest
        no future serialize run can dethrone it — there is no path back to
        a machine-authored latest.
    (c) prior exists, is a serializer-authored candidate, and already points
        at this same new_id -> idempotent, skip (re-running serialize on an
        unchanged pair must not spam the log).
    (d) otherwise -> append. Covers both the fresh case (no prior) and the
        candidate-changed case (prior candidate names a DIFFERENT new_id —
        the carry target moved, so a fresh candidate replaces it as latest).

    Returns the number of events actually appended."""
    appended = 0
    for old_id, new_id, old_text in pairs:
        if not new_id:
            continue  # defense-in-depth: never write a candidate with no
                      # target ("supersede-candidate:") — the wiring stamps
                      # ids before binding, but a caller that skips that
                      # step must not corrupt the event log
        prior = events.get(old_id)
        if prior and str(prior.get("source") or "") != "serializer":
            continue  # human spoke — machine stays silent forever
        if prior and prior.get("status") == f"supersede-candidate:{new_id}":
            continue  # idempotent — same candidate already latest
        if store.append_event(old_id, f"supersede-candidate:{new_id}",
                              source="serializer", item_text=old_text,
                              project_dir=project):
            appended += 1
    return appended


def _session_end_stamp(path) -> str:
    """When the session in `path` ended, in checkpoint `created` format (#123):
    the transcript's last message timestamp, falling back to the file mtime
    (markdown/plain transcripts carry no per-row stamps), then to now."""
    stamp = transcript.last_timestamp(path)
    if stamp:
        return stamp
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        mtime = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))


def _cmd_serialize(args) -> int:
    return _run_serialize(Path(args.transcript), _resolve_project(args.project))


def _cmd_write_checkpoint(args) -> int:
    """Write a checkpoint supplied as JSON on stdin (the #23 introspection path).

    The live session emits its own cognitive state per the schema and pipes it
    here; we validate (reusing serializer.validate — the same bar the hook's
    reconstruction must clear), stamp `source`, and route through the normal
    store (project + global + per-session, with rotation). Provisional by design:
    a later SessionEnd reconstruction supersedes it and rotation keeps this as a
    prev pointer — so it never has to be verbatim-perfect to be useful."""
    raw = sys.stdin.read()
    try:
        checkpoint = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: invalid checkpoint JSON on stdin: {exc}", file=sys.stderr)
        return 1
    if not isinstance(checkpoint, dict) or not str(checkpoint.get("session_id", "")).strip():
        print("error: checkpoint must be a JSON object with a non-empty session_id", file=sys.stderr)
        return 1
    if not serializer.validate(checkpoint):
        print(
            "error: checkpoint failed schema validation — need session_id, "
            "working_context (active_topic + open_questions/recent_decisions lists) "
            "and epistemic_snapshot (strong_beliefs/uncertainties lists), each item "
            "trust-tagged",
            file=sys.stderr,
        )
        return 1
    checkpoint["source"] = args.source  # provenance: introspection vs reconstruction
    session_id = str(checkpoint["session_id"])
    out = store.write_checkpoint(session_id, checkpoint, project_dir=_resolve_project(args.project))
    render.render_write_checkpoint([f"wrote checkpoint: {out} (source: {args.source})"])
    return 0


def _cmd_anchor(args) -> int:
    project = _resolve_project(args.project)
    a = anchor.resolve(project, args.file, args.symbol)
    if a is None:
        print(f"error: could not resolve {args.file}::{args.symbol} under {project}",
              file=sys.stderr)
        return 1
    if not args.attach:
        print(json.dumps(a, indent=2))
        return 0
    # --attach (#102): patch the anchor into the latest checkpoint's single
    # matching cognitive item and re-write through the NORMAL store path, so
    # rotation + stamping apply — the attached state becomes latest, the
    # pre-attach state is retained as prev-1.
    checkpoint = store.read_latest(project_dir=project)
    if checkpoint is None:
        print(f"error: no checkpoint found for {project} — nothing to attach to",
              file=sys.stderr)
        return 1
    needle = args.attach.lower()
    matches = [
        item for item in anchor._all_items(checkpoint)
        if isinstance(item, dict) and needle in str(item.get("text", "")).lower()
    ]
    if not matches:
        print(f"error: no cognitive item text contains {args.attach!r} "
              "in the latest checkpoint", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: {len(matches)} items match {args.attach!r} — "
              "narrow the match:", file=sys.stderr)
        for item in matches:
            print(f"  - {item.get('text')}", file=sys.stderr)
        return 1
    session_id = str(checkpoint.get("session_id", "")).strip()
    if not session_id:
        print("error: latest checkpoint has no session_id — cannot re-write",
              file=sys.stderr)
        return 1
    item = matches[0]
    item["anchored_to"] = a
    store.write_checkpoint(session_id, checkpoint, project_dir=project)
    render.render_anchor_attach([f"attached {a['qualified_name']} to: {item.get('text')}"])
    return 0


def _team_briefings(project) -> list:
    """Per-teammate briefing sections for `brief --team`, EXCLUDING the current
    author. Returns [(author, sections), ...] newest-first, or [] when the team dir
    is empty (nothing was ever mirrored). Reuses briefing.build so the #77 decision
    cap applies to teammates identically. Self is matched by slug — the same dir
    identity read_team fans in on."""
    # project_slug munging, matching _dual_write_team's dir identity — _safe_name
    # would re-introduce the "a/b" == "a_b" collision on the self-match.
    self_slug = store.project_slug(config.author())
    out = []
    for author, checkpoint in store.read_team(project_dir=project):
        if store.project_slug(author) == self_slug:
            continue  # never surface your own state as a teammate
        b = briefing.build(checkpoint)
        if b is None:
            continue  # nothing worth surfacing for this teammate
        out.append((author, b))
    return out


def _cmd_brief(args) -> int:
    _note_usage("brief:auto" if getattr(args, "auto", False) else "brief")
    # Route like status/serialize: --project, else DAIMON_PROJECT_DIR, else cwd.
    # read_latest still falls back to the global pointer if the project has none.
    project = _resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project)
    proj_path = store.project_latest_path(project)
    fallback_used = (checkpoint is not None and proj_path is not None
                     and not proj_path.exists())
    if fallback_used and not (getattr(args, "global_fallback", False)
                              or config.brief_global_fallback()):
        # Header-only fallback (#96): the foreign body is suppressed — one
        # warning line above a hundred foreign lines does not read as a
        # warning. Orient (where the activity actually is) and exit clean;
        # `daimon status` still shows the full pointer table.
        slug = str(checkpoint.get("project_slug") or "").strip() or "another project"
        epoch = store._created_epoch(checkpoint.get("created"))
        age = f"{_format_age(time.time() - epoch)} ago" if epoch else "age unknown"
        render.render_brief_note([
            "No briefing for this project yet — the first serialized session "
            "will create one.",
            f"(Most recent activity elsewhere: {slug}, {age}.)",
            "Use --global-fallback or DAIMON_BRIEF_GLOBAL_FALLBACK=full to "
            "view that checkpoint here.",
        ])
        return 0
    # Label the global-pointer fallback (#29): status calls the same situation
    # "global checkpoint (fallback)"; brief must not present another project's
    # state as this project's without saying so.
    if fallback_used:
        render.render_brief_note(["⚠ no checkpoint for this project — showing the global "
                                  "checkpoint (fallback), possibly another project's."])
    withheld = []
    if checkpoint:
        # Withhold (#103): render-time derivation, fail-open — a briefing
        # must never die over suppression machinery. #14: candidates ride
        # along stamped on `checkpoint` itself — the deterministic path
        # (render_plain via briefing._line) picks up the annotation; the
        # opt-in LLM briefing path does not surface it (same pre-existing
        # scope as [carried]), so nothing further is done with them here.
        try:
            events = store.resolutions(project_dir=project)
            checkpoint, withheld, _candidates = briefing.withhold(checkpoint, events)
        except Exception:
            withheld = []
    # NOTE: drift is checked against the resolved project root. If read_latest fell
    # back to the GLOBAL pointer (another project's checkpoint), its anchor file paths
    # are relative to a different root and may report spurious "hard" drift. Acceptable
    # for v1 (degrades safely); origin-project gating is future work (#60 follow-up).
    drift = anchor.drifted(checkpoint, project) if checkpoint else []
    # --team (#111): fan in teammates for THIS project. Empty team → None → the
    # renderer emits no Teammates section, byte-identical to a non-team briefing.
    teammates = _team_briefings(project) if getattr(args, "team", False) else None
    render.render_brief(checkpoint, drift=drift, teammates=teammates)
    if withheld:
        render.render_brief_note([
            f"{len(withheld)} resolved item(s) withheld — "
            "`daimon status --suppressed` to list"])
    return 0


# ---- recall: FTS search over local + team checkpoint history (#112) ----


def _cmd_recall(args) -> int:
    """Lexical search over the derived recall index. The index is disposable —
    recall.search auto-(re)builds it — so the only hard failure surfaced here is
    an FTS5-less sqlite3 (rc 1, named); everything else degrades to no matches."""
    _note_usage("recall")
    query = " ".join(args.query)
    if args.limit < 1:
        print(f"error: --limit must be >= 1 (got {args.limit})", file=sys.stderr)
        return 2
    project = _resolve_project(args.project)
    try:
        results = recall.search(query, project_dir=project,
                                all_projects=args.all_projects, limit=args.limit)
    except recall.RecallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        render.render_recall_lines(["no matches"])
        return 0
    now = time.time()
    lines = []
    for r in results:
        age = _format_age(now - r["created"]) if r.get("created") else "?"
        superseded = f" [superseded by {r['superseded_by']}]" if r.get("superseded_by") else ""
        trust = r.get("trust") or "untagged"
        lines.append(f"[{r['author']}] [{trust}] [{r['kind']}] {r['text']} "
                     f"({r['session_id']}, {age} ago){superseded}")
    render.render_recall_lines(lines)
    return 0


# ---- resolve/log: zero-LLM append-only event writers (#102) ----


def _cmd_resolve(args) -> int:
    """Append a resolution event for ONE checkpoint item (#102). Exact id
    first; else a fuzzy query that must match uniquely — an ambiguous bind
    is refused with candidates listed, because a wrong bind silently
    suppresses a live memory (the false-merge lesson, #13)."""
    project = _resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to resolve")
        return 1
    items = []
    for section, key in store._ITEM_LISTS:
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(item, dict) and item.get("id"):
                items.append((key, item))
    target = next((it for _, it in items if it["id"] == args.target), None)
    if target is None:
        texts = [str(it.get("text") or "") for _, it in items]
        generic = carry._generic_terms(texts)
        hits = [(key, it) for key, it in items
                if carry._same_item(args.target, str(it.get("text") or ""), generic)]
        if len(hits) == 1:
            target = hits[0][1]
        else:
            label = "no item matches" if not hits else "ambiguous — matches"
            print(f"{label} {args.target!r}; candidates:")
            listing = hits or items
            for key, it in listing:
                print(f"  {it['id']}  [{key}] {it.get('text', '')}")
            print("resolve by exact id: daimon resolve <id>")
            return 1
    ok = store.append_event(target["id"], args.status, note=args.note or "",
                            project_dir=project, item_text=str(target.get("text") or ""))
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    print(f"resolved {target['id']}: {target.get('text', '')} [{args.status}]")
    return 0


def _cmd_reverify(args) -> int:
    """Evidence-gated reopen (#103): re-stamping a resolved item without
    evidence would mark an unchecked claim verified — the one thing this
    tool must never do to its own audit trail. Reopen is allowed only when
    the original anchor still checks out live (the code proved itself) or
    the caller supplies --evidence (a human vouches for it); otherwise the
    refusal appends nothing. Exact id only — no fuzzy match, because
    reopening is a deliberate act; find ids via `daimon status --suppressed`."""
    project = _resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to reverify")
        return 1
    item = None
    for section, key in store._ITEM_LISTS:
        for it in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(it, dict) and it.get("id") == args.target:
                item = it
                break
        if item is not None:
            break
    if item is None:
        print(f"no item found with id {args.target!r}")
        return 1
    evidence = args.evidence or ""
    a = item.get("anchored_to")
    if isinstance(a, dict) and anchor.check(a, project) == "live":
        note = "reverified: anchor live"
        if evidence:
            note += "; " + evidence
    elif evidence:
        note = f"evidence: {evidence}"
    else:
        print("re-stamping without evidence would mark an unchecked claim "
              "verified — supply --evidence, or fix the anchored code and retry")
        return 1
    ok = store.append_event(item["id"], "reopened", note=note,
                            item_text=item.get("text", ""), project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    print(f"reopened {item['id']}: {item.get('text', '')}")
    return 0


def _cmd_log(args) -> int:
    """Freeform zero-LLM event append (#102): a timeline fact worth keeping
    that is not tied to one item. The fold ignores ref-less lines; readers
    of the raw log get the audit trail."""
    project = _resolve_project(args.project)
    ok = store.append_event("", args.status, note=args.text,
                            kind=args.kind, project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    print(f"logged [{args.kind}] {args.text}")
    return 0


# ---- recall-inject: the UserPromptSubmit hook backend (#125) ----

_SEEN_PRUNE_SECONDS = 7 * 86400  # cooldown files for week-old sessions are dead


def _seen_path(session: str):
    """Cooldown-state file for one session, or None when the id is unusable
    (empty, or path-hostile — the id becomes a filename)."""
    if not session or "/" in session or "\\" in session or ".." in session:
        return None
    return config.recall_seen_dir() / f"{session}.json"


def _load_seen(path) -> set:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {str(s) for s in raw} if isinstance(raw, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _save_seen(path, seen: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen)), encoding="utf-8")
        # Opportunistic prune: cooldown state for long-dead sessions.
        cutoff = time.time() - _SEEN_PRUNE_SECONDS
        for p in path.parent.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass  # cooldown is best-effort; losing it means one extra suggestion


def _suggest_line(r: dict, terms, now: float) -> str:
    """One compact, attributed, trust-preserving injection line (#125)."""
    age = _format_age(now - r["created"]) if r.get("created") else "?"
    trust = r.get("trust") or "untagged"
    text = r["text"] if len(r["text"]) <= 160 else r["text"][:157] + "..."
    superseded = " (superseded — newer checkpoint exists)" if r.get("superseded_by") else ""
    more = " ".join(terms[:3])
    return (f"daimon recall: prior work — {r['kind']} from {r['session_id']} "
            f"({age} ago): \"{text}\" [{trust}]{superseded}. "
            f"More: daimon recall \"{more}\"")


def _cmd_recall_inject(args) -> int:
    """Print 0-2 'you worked on this before' lines for the prompt on stdin, or
    nothing. rc 0 ALWAYS — this sits on the user's per-prompt critical path and
    a suggestion is never worth blocking a prompt (fail-open, like the hooks)."""
    _note_usage("recall-inject")
    try:
        prompt = sys.stdin.read()
        project = _resolve_project(args.project)
        session = str(args.session or "")
        # Never re-suggest what the SessionStart briefing already carried: the
        # project's latest and the global latest are briefed by definition.
        exclude = set()
        for cp in (store.read_latest(project), store.read_latest()):
            sid = (cp or {}).get("session_id")
            if sid:
                exclude.add(str(sid))
        seen_file = _seen_path(session)
        seen = _load_seen(seen_file) if seen_file else set()
        matches = recall.suggest(prompt, project_dir=project,
                                 current_session=session,
                                 exclude_sessions=exclude | seen)
        if not matches:
            return 0
        now = time.time()
        terms = recall.salient_terms(prompt)
        for m in matches:
            print(_suggest_line(m, terms, now))
        if seen_file:
            _save_seen(seen_file, seen | {str(m["session_id"]) for m in matches})
    except Exception:  # noqa: BLE001 — see docstring: fail-open, always rc 0
        pass
    return 0


# ---- status: "did my ending checkpoint get generated?" without grepping logs ----

# Hook spawn line: `<iso-stamp> <hook>: spawned serialize for <id> (...)`,
# where <hook> is `session-end` (Claude), `codex-stop` (Codex), or
# `gemini-session-end` (Gemini — must be listed BEFORE a bare `session-end`
# would substring-match it; the alternation is exact so order only matters for
# readability). The #26 heal retry marker (`<iso> session-start: retry
# serialize for <id> (...)`) is also a spawn for status purposes, so both the
# host and the verb are alternations. A new host adapter MUST add its prefix
# here or its serializes are invisible to status/hung detection/heal.
_SPAWN_RE = re.compile(
    r"^(\S+) (?:gemini-session-end|session-end|codex-stop|windsurf-cascade|"
    r"session-start): "
    r"(?:spawned|retry) serialize for (\S+)"
)
# Child stdout/stderr land in the log RAW (no timestamp): the serialize
# success/error lines printed by _cmd_serialize above.
_RESULT_OK_RE = re.compile(r"^wrote checkpoint: .+ \(took (\d+)s\)")
_RESULT_ERR_RE = re.compile(r"^error: .*?(?: after (\d+)s)?$")


def _format_age(seconds) -> str:
    """Coarse human age: 59 -> '59s', 61 -> '1m', 7200 -> '2h', 432000 -> '5d'."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


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


def _status_health(proj, glob, outstanding, siblings, *, now,
                   disabled: bool = False) -> dict:
    """Objective health verdict for `status`. Pure — `now` is injected. Warns only
    on data-driven signals: a NEWER phantom-child bucket (the #74 split), a missing
    project checkpoint, outstanding serialize failures, or the kill switch being
    set. No age thresholds."""
    warnings: list[str] = []

    # #28: a stuck DAIMON_DISABLE=1 silently stops all capture — the single
    # most important thing status can say, so it leads the verdict.
    if disabled:
        warnings.append(
            "DAIMON_DISABLE is set — capture is OFF (no checkpoints are "
            "being written)"
        )

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
        warnings.append(
            "no checkpoint for this project — briefing falls back to the "
            "global pointer (possibly another project) or nothing"
        )

    # Format drift on the checkpoint that would back a briefing (proj, else the
    # global fallback): a stored format_version that differs from the current one
    # means the schema changed under it, so the briefing may render partially.
    # Legacy checkpoints (no format_version) are silent — nothing to compare (#93).
    active = proj if proj.get("exists") else glob
    fv = active.get("format_version")
    if fv and fv != serializer.PROMPT_VERSION:
        warnings.append(
            f"checkpoint format {fv} != current {serializer.PROMPT_VERSION} — "
            f"schema changed; briefing may render partially (re-serialize to refresh)"
        )

    if outstanding:
        n = len(outstanding)
        msg = f"{n} session{'s' if n != 1 else ''} failed to serialize"
        # Only point at heal when it can actually repair something (#29) —
        # "run 'daimon heal'" followed by "nothing to heal" is a contradiction.
        if any(f.get("class") == "healable" for f in outstanding):
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


def _parse_serialize_log(path, now) -> dict | None:
    """Tail of serialize.log -> {spawn, result}, or None when there's no log.

    Lines from overlapping sessions interleave, so spawn and result are
    reported INDEPENDENTLY (last of each kind) — no pairing is attempted.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    spawn = result = None
    for line in text.splitlines()[-200:]:  # tail is plenty; the log only appends
        line = line.strip()
        m = _SPAWN_RE.match(line)
        if m:
            spawn = {"session_id": m.group(2), "timestamp": m.group(1)}
            continue
        m = _RESULT_OK_RE.match(line)
        if m:
            result = {"outcome": "success", "duration_seconds": int(m.group(1)), "line": line}
            continue
        m = _RESULT_ERR_RE.match(line)
        if m:
            duration = int(m.group(1)) if m.group(1) else None
            result = {"outcome": "error", "duration_seconds": duration, "line": line}
    if spawn:
        try:
            ts = datetime.strptime(spawn["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
            age = int(now - ts.replace(tzinfo=timezone.utc).timestamp())
            spawn["age_seconds"] = age
            spawn["age"] = _format_age(age)
        except ValueError:
            pass  # unexpected stamp format: report the spawn without an age
    return {"spawn": spawn, "result": result}


def _crash_log_info(path: Path, now: float) -> dict | None:
    """Tail of serialize-crash.log — the file spawn_serialize points child
    stderr at. It was a write-only dead-drop: tracebacks landed there and no
    command ever read it (#28). Returns None when absent/empty/unreadable;
    else the last non-empty line (a traceback's final line names the
    exception) plus the file's age."""
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
    return {"last_line": lines[-1], "age_seconds": age,
            "age": _format_age(age), "path": str(path)}


def _print_suppressed(project) -> int:
    """`daimon status --suppressed` (#103): the visibility answer to brief's
    silent-suppression note ("N resolved item(s) withheld — `daimon status
    --suppressed` to list"). Reuses briefing.withhold for the classification
    rather than reimplementing it — the resolved/live split must stay in
    exactly one place. Reads ONLY this project's own latest checkpoint
    (fallback=False, same rule as carry #94): listing another project's
    withheld items under this project's status would be worse than listing
    none. Fails open like brief's withhold call — a broken events.jsonl
    must not crash `status`, it should just report nothing suppressed."""
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    withheld = []
    candidates = []
    if checkpoint:
        try:
            events = store.resolutions(project_dir=project)
            _, withheld, candidates = briefing.withhold(checkpoint, events)
        except Exception:
            withheld = []
            candidates = []
    if not withheld and not candidates:
        print("no suppressed items")
        return 0
    if withheld:
        print(f"suppressed items ({len(withheld)}):")
        for key, item, evt in withheld:
            item_id = item.get("id") or "-"
            text = str(item.get("text") or "").strip()
            status = str(evt.get("status") or "")
            ts = str(evt.get("ts") or "")
            note = str(evt.get("note") or "").strip()
            paren = f"{status} {ts}"
            if note:
                paren += f", {note}"
            print(f"  {item_id}  [{key}] {text}  ({paren})")
    if candidates:
        # #14: machine SUGGESTIONS, not resolutions — a separate subsection
        # so they never read as confirmed suppressions.
        print("likely superseded (unconfirmed):")
        for key, item, evt in candidates:
            item_id = item.get("id") or "-"
            text = str(item.get("text") or "").strip()
            new_id = item.get("_supersede_candidate") or "-"
            print(f"  {item_id}  [{key}] {text}  -> {new_id}")
    return 0


def _cmd_status(args) -> int:
    _note_usage("status")
    project = _resolve_project(args.project)
    if getattr(args, "suppressed", False):
        return _print_suppressed(project)
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
    recall_error = _crash_log_info(config.log_dir() / "recall-error.log", now)
    disabled = config.is_disabled()
    # Skips are terminal by design (too-short sessions), but invisible skips
    # read as captured sessions (#28) — count them for display.
    skipped_recent = sum(
        1 for e in _session_ledger(_ledger_text, now).values()
        if e["result_kind"] == "skipped"
    )
    siblings = store.sibling_buckets(project)
    health = _status_health(proj, glob, outstanding, siblings, now=now,
                            disabled=disabled)
    # ONE objective team line (#113), only when a team remote exists — the #84
    # health-line rule: no line, no false alarms when the team feature is unused.
    team = teamsync.status_line()
    identity = {
        "cwd": str(Path(args.project or ".").expanduser().resolve()),
        "git_root": project,
        "slug": store.project_slug(project),
    }
    # 0 = some checkpoint would back a briefing; 1 = neither pointer exists
    # (cheap existence test for scripts / the FR #23 hook guard).
    rc = 0 if (proj["exists"] or glob["exists"]) else 1

    if args.json:
        proj = {"dir": project, "slug": identity["slug"], **proj}
        print(json.dumps(
            {"project": proj, "global": glob, "last_serialize": last,
             "outstanding": outstanding, "siblings": siblings, "health": health,
             "team": team, "crash": crash, "disabled": disabled,
             "skipped_recent": skipped_recent, "recall_error": recall_error},
            indent=2,
        ))
        return rc

    render.render_status({
        "project": project, "proj": proj, "glob": glob, "same": same, "last": last,
        "outstanding": outstanding, "identity": identity, "health": health,
        "team": team, "crash": crash, "skipped_recent": skipped_recent,
        "recall_error": recall_error,
    })
    return rc


# ---- heal: opportunistic ONE-shot repair of the most recent FAILED serialize ----

# The transcript carried by an error result line (see _run_serialize):
# `error: <exc> (transcript: <path>) after <N>s` for serialize failures, or
# `error: <preflight msg> (transcript: <path>)` for pre-flight errors (#49) —
# the `after Ns` clause is optional so both attribute to their session. A
# pre-flight-failed session with its transcript on disk is healable: fixing
# the config (e.g. adding the API key) makes the retry succeed.
_HEAL_TRANSCRIPT_RE = re.compile(r"\(transcript: (.+?)\)(?: after \d+s|$)")

# Per-session ledger regexes (kept SEPARATE from _RESULT_OK_RE/_RESULT_ERR_RE,
# which _parse_serialize_log depends on). Success lines embed the session id in
# the checkpoint path: `wrote checkpoint: <dir>/<session>.json (took Ns)`.
_LEDGER_OK_RE = re.compile(r"^wrote checkpoint: (.+?) \(took \d+s\)")
_LEDGER_SKIP_RE = re.compile(r"^skipped serialize for (\S+):")
_LEDGER_PROJECT_RE = re.compile(r"project: (.*?)\)")
# #28: hooks stamp the transcript path on the spawn line as a TRAILING group —
# `... (reason: r, project: p) (transcript: <path>)` — so a child that crashes
# before writing any result line still leaves a healable trail. Trailing-only
# match keeps it disjoint from _HEAL_TRANSCRIPT_RE (error lines, `after Ns`).
_LEDGER_SPAWN_TRANSCRIPT_RE = re.compile(r"\(transcript: (.+?)\)\s*$")


def _session_ledger(text: str, now: float) -> dict:
    """Fold serialize.log into per-session terminal state. Unlike
    _parse_serialize_log (last-of-each-kind, no pairing), this attributes every
    line to its session_id — spawn regex group, success checkpoint-path stem, or
    error transcript stem — so a failure is never masked by a later session's
    success. Pre-flight errors (no transcript) carry no session and are dropped."""
    sessions: dict = {}

    def _entry(sid: str) -> dict:
        return sessions.setdefault(sid, {
            "spawned": False, "spawn_ts": None, "spawn_age": None, "project": None,
            "result_kind": None, "result_line": None, "transcript": None,
            "retried": False,
        })

    for line in text.splitlines()[-200:]:
        line = line.strip()
        m = _SPAWN_RE.match(line)
        if m:
            e = _entry(m.group(2))
            e["spawned"] = True
            try:
                ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ")
                e["spawn_ts"] = ts.replace(tzinfo=timezone.utc).timestamp()
                e["spawn_age"] = int(now - e["spawn_ts"])
            except ValueError:
                pass
            pm = _LEDGER_PROJECT_RE.search(line)
            if pm:
                raw = pm.group(1).strip()
                e["project"] = raw if (raw and raw != "?") else None
            tm = _LEDGER_SPAWN_TRANSCRIPT_RE.search(line)
            if tm:
                e["transcript"] = tm.group(1)
            if "retry serialize for" in line:
                e["retried"] = True
            continue
        m = _LEDGER_OK_RE.match(line)
        if m:
            e = _entry(Path(m.group(1)).stem)
            e["result_kind"] = "success"
            e["result_line"] = line
            e["transcript"] = None
            continue
        m = _LEDGER_SKIP_RE.match(line)
        if m:
            e = _entry(m.group(1))
            e["result_kind"] = "skipped"
            e["result_line"] = line
            continue
        if _RESULT_ERR_RE.match(line):
            tm = _HEAL_TRANSCRIPT_RE.search(line)
            if not tm:
                continue  # pre-flight error, no session to attribute
            e = _entry(Path(tm.group(1)).stem)
            e["result_kind"] = "error"
            e["result_line"] = line
            e["transcript"] = tm.group(1)
    return sessions


def _outstanding_failures(ledger, now, has_checkpoint, ceiling, transcript_exists) -> list:
    """Sessions still LOST — no checkpoint AND latest state != success.
    `has_checkpoint(sid)` and `transcript_exists(path)` are injected so this
    stays pure/testable. error+spawn+transcript-on-disk+not-retried -> healable
    (exactly what heal will repair); error but retried -> retry-exhausted; error
    but no spawn record or transcript gone -> unrecoverable (lost, heal can't
    retry it); spawn with no result older than `ceiling` -> hung."""
    out = []
    for sid, e in ledger.items():
        if e["result_kind"] in ("success", "skipped"):
            continue
        if has_checkpoint(sid):
            continue
        age = e["spawn_age"]
        if e["result_kind"] == "error":
            if e["retried"]:
                cls = "retry-exhausted"
            elif e["spawned"] and e["transcript"] and transcript_exists(e["transcript"]):
                cls = "healable"
            else:
                cls = "unrecoverable"
            out.append({"sid": sid, "kind": "error", "class": cls, "age": age,
                        "age_str": _format_age(age) if age is not None else "unknown",
                        "transcript": e["transcript"], "project": e["project"],
                        "spawned": e["spawned"], "line": e["result_line"]})
        elif e["result_kind"] is None and e["spawned"] and age is not None and age > ceiling:
            # #28: a spawn line that recorded its transcript makes a hung
            # (crashed/killed) serialize healable — the checkpoint is
            # recoverable as long as the transcript is still on disk. The
            # one-retry-ever policy (#26) applies unchanged via `retried`.
            t = e["transcript"]
            cls = ("healable"
                   if t and transcript_exists(t) and not e["retried"]
                   else "hung")
            out.append({"sid": sid, "kind": "hung", "class": cls, "age": age,
                        "age_str": _format_age(age), "transcript": t,
                        "project": e["project"], "spawned": True, "line": None})
    out.sort(key=lambda f: (f["age"] is None, f["age"] or 0))
    return out


def _compute_outstanding(text: str, now: float) -> list:
    """Wire the pure ledger/classifier to the live store + filesystem. Single
    source for both `status` (display) and `heal` (repair) so their notion of
    'outstanding' can never drift."""
    return _outstanding_failures(
        _session_ledger(text, now), now,
        lambda sid: store.read_checkpoint(sid) is not None,
        config.hung_after_seconds(),
        lambda p: bool(p) and Path(p).exists(),
    )


_HEAL_SKIP_REASON = {
    "retry-exhausted": "retry already attempted, still failing",
    "unrecoverable": "no spawn record or transcript gone — cannot auto-heal",
    "hung": "spawned, no result (hung/killed) — transcript unavailable",
}


def _heal_plan(text, now) -> dict:
    """Decide what `heal` will repair and why. Pure — `now` injected. Reuses the
    SAME _compute_outstanding source as status, so their notion of healable agrees.
    target = the newest `healable` (already gauntlet-vetted); every other outstanding
    failure lands in `skipped` with a reason; `note` is the headline when there is no
    target."""
    outstanding = _compute_outstanding(text, now)
    healable = [f for f in outstanding if f["class"] == "healable"]
    target = None
    if healable:
        t = healable[0]  # newest-first
        target = {"sid": t["sid"], "transcript": t["transcript"],
                  "project": t["project"], "age_str": t["age_str"], "line": t["line"]}

    skipped = []
    for f in outstanding:
        if target and f["sid"] == target["sid"]:
            continue
        if f["class"] == "healable":
            reason = "newer failure took this run — re-run 'daimon heal' to reach it"
        else:
            reason = _HEAL_SKIP_REASON.get(f["class"], "not auto-repairable")
        skipped.append({"sid": f["sid"], "age_str": f["age_str"], "reason": reason})

    if target is not None:
        note = ""
    elif not outstanding:
        note = ("nothing to heal — no serialize activity logged"
                if not text.strip() else "nothing to heal — no outstanding failures")
    else:
        n = len(skipped)
        note = f"nothing to heal — {n} failure{'s' if n != 1 else ''} can't be auto-repaired:"
    return {"target": target, "skipped": skipped, "note": note}


def _cmd_heal(args) -> int:
    """Explain the heal decision, then repair the newest healable session if safe.
    Every no-op returns 0 (a no-op heal is never an error). `--dry-run` explains
    without serializing."""
    dry_run = getattr(args, "dry_run", False)
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        text = ""
    now = time.time()
    plan = _heal_plan(text, now)
    render.render_heal(plan, dry_run=dry_run)
    if dry_run or plan["target"] is None:
        return 0
    t = plan["target"]
    transcript_path = Path(t["transcript"])
    if not transcript_path.exists():
        render.render_heal_abort([f"heal aborted: transcript for {t['sid']} vanished"])
        return 0
    # A hung target has no result line (#34 made spawn-with-transcript hung
    # sessions healable) — the retry marker still needs a prior (#49).
    prior = (t["line"] or "hung: spawned, no result").split(" (transcript:")[0]
    _append_retry_log(t["sid"], prior)
    return _run_serialize(transcript_path, t["project"])


# ---- team: sidecar private-repo sync (#113) ----


def _cmd_team_init(args) -> int:
    try:
        dest = teamsync.init(args.remote_url)
    except teamsync.TeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    render.render_team_init([
        f"initialized team sidecar: {dest}",
        "checkpoints now sync there — `daimon team sync` runs opportunistically "
        "at session start",
    ])
    return 0


def _cmd_team_sync(args) -> int:
    """rc 0 for every sync-nothing-to-do shape (no git, no remotes, offline);
    warnings go to stderr but never change the rc — a degraded sync is not a
    user error."""
    if getattr(args, "project", None):
        # Accepted for CLI symmetry only — say so instead of silently running
        # a global sync the user thought was scoped (#29).
        print("daimon team: --project is ignored — sync is project-agnostic "
              "(all own checkpoints sync)", file=sys.stderr)
    if not teamsync.git_available():
        render.render_team_sync(["daimon team: git not found on PATH — sync skipped"])
        return 0
    reports = teamsync.sync()
    if not reports:
        render.render_team_sync([
            "daimon team: no team remote configured — nothing to sync "
            "(run `daimon team init <remote-url>`)",
        ])
        return 0
    for r in reports:
        parts = [f"{r['committed']} committed", "pushed" if r["pushed"] else "no push"]
        if r["fetched"]:
            parts.append("fetched teammates' updates")
        line = f"{r['slug']}: " + ", ".join(parts)
        if r["notes"]:
            line += " (" + "; ".join(r["notes"]) + ")"
        # Rendered per-report (not collected and rendered once after the loop):
        # this keeps a report's stdout line interleaved with ITS OWN stderr
        # warnings below, matching the ordering the pre-#68 print()-per-report
        # loop had.
        render.render_team_sync([line])
        for w in r["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
    return 0


def _cmd_team_status(args) -> int:
    if not teamsync.git_available():
        render.render_team_status(["daimon team: git not found on PATH"])
        return 0
    rows = teamsync.team_status()
    if not rows:
        render.render_team_status([
            "no team remote configured — run `daimon team init <remote-url>`",
        ])
        return 0
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    lines = []
    for row in rows:
        authors = ", ".join(row["authors"]) or "none yet"
        lines.append(f"{row['slug']}: {row['freshness']} — "
                     f"{row['unpushed']} unpushed checkpoint(s), authors: {authors}")
    render.render_team_status(lines)
    return 0


# ---- configure: detect/report the resolved backend + fill gaps in ~/.daimon/env ----


def _cmd_configure(args) -> int:
    """Detect + report the resolved LLM backend; fill gaps in ~/.daimon/env.

    Always prints a doctor view. With backend flags, writes non-interactively.
    With no flags it is SAFE everywhere: it only prompts on a TTY when daimon is
    not ready, and otherwise just prints guidance — it never blocks.
    """
    # --test (#56): prove the RESOLVED backend works, interactively, at setup
    # time — the alternative is a real serialize failing minutes later inside
    # a detached hook child. One tiny prompt through the same llm.chat path
    # serialization uses; failure prints the cause and where stderr landed.
    if getattr(args, "test", False):
        start = time.monotonic()
        try:
            reply = llm.chat(
                [{"role": "user", "content":
                  'Reply with exactly this JSON and nothing else: {"ok": true}'}],
                retries=1)
        except llm.ChatError as exc:
            print(f"backend test: FAILED — {exc}", file=sys.stderr)
            return 1
        # Same extraction path serialization uses (#59): a transport that
        # answers but cannot return extractable JSON — agent-style CLIs often
        # can't — must fail HERE, not on the first real serialize.
        try:
            llm.extract_json(reply)
        except json.JSONDecodeError:
            print("backend test: FAILED — transport works, but the backend did "
                  "not return extractable JSON; serialization will fail. "
                  "Agent-style CLIs often can't do this — use an "
                  "OpenAI-compatible endpoint or a raw-completion CLI.",
                  file=sys.stderr)
            return 1
        elapsed = time.monotonic() - start
        render.render_configure_lines([f"backend test: ok ({elapsed:.1f}s round trip)"])
        return 0

    st = configure.status()
    render.render_configure(st)

    if args.backend:
        updates = {"DAIMON_LLM_BACKEND": args.backend}
        if args.backend == "litellm":
            if args.api_key:
                updates["DAIMON_LLM_API_KEY"] = args.api_key
            if args.model:
                updates["DAIMON_LLM_MODEL"] = args.model
            if args.base_url:
                updates["DAIMON_LLM_BASE_URL"] = args.base_url
        elif args.backend == "command":
            if args.command:
                updates["DAIMON_LLM_COMMAND"] = args.command
            if args.output:
                updates["DAIMON_LLM_COMMAND_OUTPUT"] = args.output
        # claude-cli: just pin the backend, no credentials needed.
        path = configure.write_env(updates)
        render.render_configure_lines([f"wrote {path}"])
        render.render_configure(configure.status())  # reprint the new resolved state
        return 0

    if st["ready"]:
        return 0  # nothing to do
    if not sys.stdin.isatty():
        # Non-interactive and not ready: guide, never block.
        render.render_configure_lines(["not ready — re-run with --backend {litellm,command,claude-cli} "
                                       "and the matching value flags, or run interactively in a terminal."])
        return 0

    # Interactive: prompt for a backend and its values.
    backend = _prompt("backend [litellm/command/claude-cli]: ").strip() or "litellm"
    updates = {"DAIMON_LLM_BACKEND": backend}
    if backend == "litellm":
        base_url = _prompt("base_url (blank for default): ").strip()
        if base_url:
            updates["DAIMON_LLM_BASE_URL"] = base_url
        # getpass, not _prompt (#29): the secret must not echo to the
        # terminal or land in scrollback.
        api_key = getpass.getpass("api_key: ").strip()
        if api_key:
            updates["DAIMON_LLM_API_KEY"] = api_key
        model = _prompt("model: ").strip()
        if model:
            updates["DAIMON_LLM_MODEL"] = model
    elif backend == "command":
        command = _prompt("command: ").strip()
        if command:
            updates["DAIMON_LLM_COMMAND"] = command
        output = _prompt("output spec [text/json:<key>] (blank=text): ").strip()
        if output:
            updates["DAIMON_LLM_COMMAND_OUTPUT"] = output
    # claude-cli: nothing more to ask.
    path = configure.write_env(updates)
    render.render_configure_lines([f"wrote {path}"])
    render.render_configure(configure.status())
    return 0


# ---- stats: local usage + capture aggregates (#54) — zero phone-home ----

# Host prefix on a spawn line, for per-host capture counts. Deliberately the
# same alternation as _SPAWN_RE (a new host adapter updates both).
_STATS_HOST_RE = re.compile(
    r"^\S+ (gemini-session-end|session-end|codex-stop|windsurf-cascade): "
    r"spawned serialize for "
)


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


def _stats_capture() -> dict:
    """serialize.log -> aggregate counters. Tallies EVERY line (scar #9: no
    last-of-kind collapse — a buried failure still counts)."""
    out = {"success": 0, "skipped": 0, "errors": 0, "fallback_serializes": 0,
           "hosts": {}, "max_serialize_seconds": 0, "total_serialize_seconds": 0}
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        m = _RESULT_OK_RE.match(line)
        if m:
            out["success"] += 1
            took = int(m.group(1))
            out["max_serialize_seconds"] = max(out["max_serialize_seconds"], took)
            out["total_serialize_seconds"] += took
            if "[fallback backend]" in line:
                out["fallback_serializes"] += 1
            continue
        if _LEDGER_SKIP_RE.match(line):
            out["skipped"] += 1
            continue
        if _RESULT_ERR_RE.match(line):
            out["errors"] += 1
            continue
        hm = _STATS_HOST_RE.match(line)
        if hm:
            out["hosts"][hm.group(1)] = out["hosts"].get(hm.group(1), 0) + 1
    return out


def _stats_store() -> dict:
    """Checkpoint store -> counts by kind and trust class + carried items.
    Reuses recall's section map so a new cognitive kind shows up here for free."""
    out = {"checkpoints": 0, "project_buckets": 0, "items_by_kind": {},
           "items_verbatim": 0, "items_inferred": 0, "items_untagged": 0,
           "items_carried": 0}
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
        for section, key, kind in recall._KIND_SOURCES:
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


_USAGE_STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"
_RETENTION_WINDOW_DAYS = 14


def _parse_stamp(token: str):
    try:
        return datetime.strptime(token, _USAGE_STAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _spawns_in_window(cutoff) -> bool:
    """True when serialize.log shows any hook-spawned capture inside the
    window — i.e. sessions ARE happening on this machine."""
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not _STATS_HOST_RE.match(line):
            continue
        stamp = _parse_stamp(line.split()[0])
        if stamp is not None and stamp >= cutoff:
            return True
    return False


def _stats_retention(now=None) -> dict:
    """usage.log -> hook-driven briefings (`brief:auto`) vs deliberate
    re-reads, over the last _RETENTION_WINDOW_DAYS. Plain `brief` lines
    stamped before the first `brief:auto` ever logged predate the flag and
    are reported as untagged — ambiguous, never guessed (#54 honesty rule).
    stale_hook_warning: sessions were captured in the window but zero
    auto-briefings were logged — the SessionStart hook likely predates
    --auto."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=_RETENTION_WINDOW_DAYS)
    out = {"window_days": _RETENTION_WINDOW_DAYS, "hook_briefs": 0,
           "rereads": {"brief": 0, "status": 0, "recall": 0},
           "rereads_total": 0, "rereads_per_hook_brief": None,
           "untagged_briefs": 0, "stale_hook_warning": False}
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
        if cmd == "brief" and (first_auto is None or stamp < first_auto):
            out["untagged_briefs"] += 1
            continue
        if stamp < cutoff:
            continue
        if cmd == "brief:auto":
            out["hook_briefs"] += 1
        elif cmd in out["rereads"]:
            out["rereads"][cmd] += 1
    out["rereads_total"] = sum(out["rereads"].values())
    if out["hook_briefs"]:
        out["rereads_per_hook_brief"] = round(
            out["rereads_total"] / out["hook_briefs"], 2)
    if out["hook_briefs"] == 0 and _spawns_in_window(cutoff):
        out["stale_hook_warning"] = True
    return out


def _cmd_stats(args) -> int:
    """Aggregate what is already on disk. Nothing is transmitted anywhere —
    sharing the output is a deliberate act (the user pastes it)."""
    data = {"usage": _stats_usage(), "capture": _stats_capture(),
            "store": _stats_store(), "retention": _stats_retention()}
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    render.render_stats(data)
    return 0


# ---- hooks: ship host hook scripts from the package (#43) ----

# host -> (files to install, entry script, events to register). The packaged
# copies live in daimon_briefing/_hooks/ and are drift-guarded against the
# repo's hook/ dir by tests/test_hooks_install.py. Claude Code is absent on
# purpose: the plugin marketplace owns that path.
_HOOK_HOSTS = {
    "windsurf": {
        "files": ("daimon-windsurf-hooks.py", "_daimon_hook_lib.py"),
        "entry": "daimon-windsurf-hooks.py",
        "events": ("pre_user_prompt", "post_cascade_response",
                   "post_cascade_response_with_transcript"),
    },
}


def _hooks_target_dir() -> Path:
    return Path.home() / ".daimon" / "hooks"


def _cmd_hooks_list(args) -> int:
    lines = [f"{host}  ({spec['entry']}; events: {', '.join(spec['events'])})"
             for host, spec in sorted(_HOOK_HOSTS.items())]
    render.render_hooks_list(lines)
    return 0


def _cmd_hooks_install(args) -> int:
    """Copy the host's packaged hook script(s) to ~/.daimon/hooks/ — a STABLE
    path the host's hooks config points at once. Idempotent: re-running after
    `uv tool upgrade daimon-briefing` refreshes the scripts to match the
    installed CLI, which is the whole point (#43: a curl'd script drifts)."""
    from importlib import resources

    spec = _HOOK_HOSTS.get(args.host)
    if spec is None:
        known = ", ".join(sorted(_HOOK_HOSTS))
        print(f"error: unknown host '{args.host}' (known: {known})", file=sys.stderr)
        return 2
    target = _hooks_target_dir()
    target.mkdir(parents=True, exist_ok=True)
    pkg = resources.files("daimon_briefing._hooks")
    for name in spec["files"]:
        data = (pkg / name).read_bytes()
        dest = target / name
        dest.write_bytes(data)
        dest.chmod(dest.stat().st_mode | 0o100)  # u+x
    entry = target / spec["entry"]
    lines = [
        f"installed {len(spec['files'])} file(s) to {target}",
        "",
        "Register this command for the events below "
        "(host hooks config — see the host's hooks documentation):",
        f"  command: python3 {entry}",
    ]
    for ev in spec["events"]:
        lines.append(f"  event:   {ev}")
    lines.append("")
    lines.append("Re-run `daimon hooks install " + args.host +
                 "` after every `uv tool upgrade daimon-briefing`.")
    render.render_hooks_install(lines)
    return 0


# ---- skill: ship the portable agent skill from the package (#66) ----

def _resolve_project_cwd() -> Path:
    """--project writes relative to the repo root, not whatever subdirectory
    the command was run from — same git-toplevel normalization _resolve_project
    uses for checkpoint routing (#74); falls back to plain cwd outside a repo
    or when git is unavailable (resolve_project_root's own contract)."""
    return Path(config.resolve_project_root(str(Path.cwd())))


def _cmd_skill_list(args) -> int:
    from . import skill_install
    rows = []
    for host in sorted(skill_install.HOSTS):
        scopes = [s for s in ("global", "project")
                  if skill_install.HOSTS[host].get(s) is not None]
        rows.append((host, scopes))
    render.render_skill_list(rows)
    return 0


def _cmd_skill_show(args) -> int:
    from . import skill_content
    print(skill_content.render_compact() if args.compact
          else skill_content.render_full(), end="")
    return 0


def _cmd_skill_install(args) -> int:
    from . import skill_install
    try:
        lines = skill_install.install(
            args.host, project=args.project, home=Path.home(),
            cwd=_resolve_project_cwd())
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines, footer=(
        f"Re-run `daimon skill install {args.host}` after every "
        "`uv tool upgrade daimon-briefing` to refresh the content.",
    ))
    return 0


def _cmd_skill_uninstall(args) -> int:
    from . import skill_install
    try:
        lines = skill_install.uninstall(
            args.host, project=args.project, home=Path.home(),
            cwd=_resolve_project_cwd())
    except skill_install.SkillInstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    render.render_skill_lines(lines)
    return 0


def _crash_stamp_excepthook(exc_type, exc, tb) -> None:
    """Uncaught-crash header (#92): serialize-crash.log is the detached
    child's RAW stderr fd — no logger sits in the write path, so the only
    process that can timestamp a crash is the crashing one. One ISO-stamped
    line, then the stock traceback. Covers uncaught Python exceptions (the
    dominant case); interpreter-level deaths still write nothing."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), "?")
    print(f"--- crash {stamp} pid={os.getpid()} cmd={cmd} ---",
          file=sys.stderr, flush=True)
    sys.__excepthook__(exc_type, exc, tb)


def main(argv=None) -> int:
    sys.excepthook = _crash_stamp_excepthook  # #92: stamp uncaught crashes
    # #68: one formatter selection for the WHOLE parser tree. argparse does not
    # propagate formatter_class from parent to subparser, so every add_parser
    # call below must receive it — done here by patching add_parser on each
    # subparsers action into a partial pre-bound with `fmt`, rather than
    # threading formatter_class= through 20+ individual call sites.
    fmt = _formatter_class()
    parser = argparse.ArgumentParser(
        prog="daimon",
        description="Cognitive checkpoints — serialize sessions, brief on resume.",
        epilog="Examples:\n"
               "  daimon brief                 render the latest briefing\n"
               "  daimon status                checkpoint presence + last serialize\n"
               "  daimon configure             detect/repair the LLM backend\n",
        formatter_class=fmt,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser = functools.partial(sub.add_parser, formatter_class=fmt)

    p_ser = sub.add_parser("serialize", help="serialize a transcript file into a checkpoint")
    p_ser.add_argument("transcript", help="path to a text/markdown transcript")
    p_ser.add_argument(
        "--project",
        help="project directory to route the checkpoint to "
        "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_ser.set_defaults(func=_cmd_serialize)

    p_wc = sub.add_parser(
        "write-checkpoint",
        help="store a checkpoint read as JSON on stdin (introspection path, #23)",
    )
    p_wc.add_argument(
        "--project",
        help="project directory to route the checkpoint to "
        "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_wc.add_argument(
        "--source", default="introspection",
        help="provenance stamp for the checkpoint (default: introspection)",
    )
    p_wc.set_defaults(func=_cmd_write_checkpoint)

    p_brief = sub.add_parser(
        "brief", help="render the briefing from the latest checkpoint",
        epilog="Examples:\n  daimon brief\n  daimon brief --project .\n  DAIMON_PLAIN=1 daimon brief\n",
    )
    p_brief.add_argument(
        "--project",
        help="project directory to brief (default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_brief.add_argument(
        "--team", action="store_true",
        help="also show a 'Teammates' section: each teammate's active topic + "
             "recent decisions from the shared team memory (#111)",
    )
    p_brief.add_argument(
        "--global-fallback", action="store_true",
        help="when this project has no checkpoint, render the full global "
             "checkpoint (possibly another project's) instead of the "
             "header-only note (#96)",
    )
    p_brief.add_argument(
        "--auto", action="store_true",
        help="mark this render as hook-driven (SessionStart) so `daimon stats` "
             "can separate automatic briefings from deliberate re-reads (#54)",
    )
    p_brief.set_defaults(func=_cmd_brief)

    p_anchor = sub.add_parser(
        "anchor", help="resolve a code symbol to an anchor block for a cognitive item",
        epilog="Examples:\n  daimon anchor daimon_briefing/cli.py _cmd_brief\n"
               "  daimon anchor pkg/mod.py MyClass.method --project .\n"
               "  daimon anchor pkg/mod.py fn --attach 'auth decision'\n",
    )
    p_anchor.add_argument("file", help="repo-relative path to the source file")
    p_anchor.add_argument("symbol", help="symbol name or Class.method")
    p_anchor.add_argument(
        "--project", help="project root the file is relative to (default: cwd)"
    )
    p_anchor.add_argument(
        "--attach", metavar="TEXT-MATCH",
        help="attach the anchor to the one checkpoint item whose text contains "
             "TEXT-MATCH (case-insensitive), re-writing the latest checkpoint",
    )
    p_anchor.set_defaults(func=_cmd_anchor)

    p_recall = sub.add_parser(
        "recall", help="search local + team checkpoint history (FTS5)",
        epilog="Examples:\n"
               "  daimon recall auth caching\n"
               "  daimon recall gateway --all-projects --json\n",
    )
    p_recall.add_argument(
        "query", nargs="+",
        help="search terms (matched as words against item text and quotes)",
    )
    p_recall.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_recall.add_argument(
        "--all-projects", action="store_true",
        help="search across every project (lifts the project scope)",
    )
    p_recall.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_recall.add_argument(
        "--limit", type=int, default=20, help="max results (default: 20)"
    )
    p_recall.set_defaults(func=_cmd_recall)

    p_resolve = sub.add_parser(
        "resolve", help="mark a checkpoint item resolved — append-only event, "
        "folds at read so the item stops carrying (#102)",
        epilog="Examples:\n  daimon resolve o-3f8a2c\n"
               "  daimon resolve \"release pipeline approval\" --note \"shipped in 0.9\"\n",
    )
    p_resolve.add_argument("target", help="item id (exact) or a query that must match exactly one item")
    p_resolve.add_argument("--status", default="resolved",
                           help="free-form lifecycle status (default: resolved; "
                                "a status starting with 'reopen' revives the item)")
    p_resolve.add_argument("--note", help="optional context recorded on the event")
    p_resolve.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_resolve.set_defaults(func=_cmd_resolve)

    p_reverify = sub.add_parser(
        "reverify", help="evidence-gated reopen of a resolved item (#103) — "
        "refuses without proof, so a claim can't get re-verified for free",
        epilog="Examples:\n"
               "  daimon reverify o-3f8a2c --evidence \"checked release page\"\n"
               "  daimon reverify o-3f8a2c   # reopens only if the anchor still checks live\n",
    )
    p_reverify.add_argument("target", help="item id (exact only — reverify is deliberate, no fuzzy match)")
    p_reverify.add_argument("--evidence", help="why this claim can be trusted again")
    p_reverify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_reverify.set_defaults(func=_cmd_reverify)

    p_log = sub.add_parser(
        "log", help="append a freeform timeline event (zero-LLM) to this project's event log (#102)",
    )
    p_log.add_argument("--text", required=True, help="what happened")
    p_log.add_argument("--kind", default="note", help="event kind (default: note)")
    p_log.add_argument("--status", default="", help="optional free-form status")
    p_log.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_log.set_defaults(func=_cmd_log)

    p_inject = sub.add_parser(
        "recall-inject",
        help="proactive-suggestion backend for the UserPromptSubmit hook (#125): "
             "prompt on stdin, prints 0-2 prior-work lines, rc 0 always",
    )
    p_inject.add_argument("--project", default=None,
                          help="project dir for scoping (defaults to cwd detection)")
    p_inject.add_argument("--session", default=None,
                          help="current session id (excluded from matches; keys the cooldown)")
    p_inject.set_defaults(func=_cmd_recall_inject)

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

    p_heal = sub.add_parser(
        "heal",
        help="re-serialize the most recent FAILED session if it can be done safely",
    )
    p_heal.add_argument(
        "--dry-run", action="store_true",
        help="explain what heal would repair (and why not) without serializing",
    )
    p_heal.set_defaults(func=_cmd_heal)

    p_team = sub.add_parser(
        "team", help="shared team memory: sidecar repo init/sync/status (#113)",
        epilog="Examples:\n"
               "  daimon team init git@github.com:org/team-memory.git\n"
               "  daimon team sync\n"
               "  daimon team status\n",
    )
    team_sub = p_team.add_subparsers(dest="team_cmd", required=True)
    team_sub.add_parser = functools.partial(team_sub.add_parser, formatter_class=fmt)
    pt_init = team_sub.add_parser(
        "init", help="clone the private team sidecar repo (empty remote OK)"
    )
    pt_init.add_argument("remote_url", help="git remote URL of the PRIVATE team repo")
    pt_init.set_defaults(func=_cmd_team_init)
    pt_sync = team_sub.add_parser(
        "sync", help="commit+push own checkpoints; fetch teammates' only on "
                     "remote change (ls-remote gate)"
    )
    pt_sync.add_argument(
        "--project",
        help="accepted for CLI symmetry; sync is currently project-agnostic "
             "(all own checkpoints sync regardless of project)",
    )
    pt_sync.set_defaults(func=_cmd_team_sync)
    pt_status = team_sub.add_parser(
        "status", help="per-remote freshness, own unpushed count, authors seen"
    )
    pt_status.add_argument("--json", action="store_true", help="machine-readable output")
    pt_status.set_defaults(func=_cmd_team_status)

    p_cfg = sub.add_parser(
        "configure",
        help="detect the resolved LLM backend and fill gaps in ~/.daimon/env",
    )
    p_cfg.add_argument(
        "--backend", choices=("litellm", "command", "claude-cli"),
        help="non-interactive: pin this backend and write the value flags below",
    )
    p_cfg.add_argument("--api-key", help="litellm: DAIMON_LLM_API_KEY")
    p_cfg.add_argument("--model", help="litellm: DAIMON_LLM_MODEL")
    p_cfg.add_argument("--base-url", help="litellm: DAIMON_LLM_BASE_URL")
    p_cfg.add_argument("--command", help="command: DAIMON_LLM_COMMAND")
    p_cfg.add_argument("--output", help="command: DAIMON_LLM_COMMAND_OUTPUT (text|json:<key>)")
    p_cfg.add_argument(
        "--test", action="store_true",
        help="send one tiny prompt through the resolved backend and report "
             "pass/fail — run this right after configuring (#56)",
    )
    p_cfg.set_defaults(func=_cmd_configure)

    p_stats = sub.add_parser(
        "stats",
        help="local usage + capture aggregates (#54) — nothing is transmitted; "
             "sharing the output is a deliberate paste",
    )
    p_stats.add_argument("--json", action="store_true", help="machine-readable output")
    p_stats.set_defaults(func=_cmd_stats)

    p_hooks = sub.add_parser(
        "hooks",
        help="ship host hook scripts from the package (#43): list, install",
    )
    hooks_sub = p_hooks.add_subparsers(dest="hooks_cmd", required=True)
    hooks_sub.add_parser = functools.partial(hooks_sub.add_parser, formatter_class=fmt)
    ph_list = hooks_sub.add_parser("list", help="hosts with packaged hook scripts")
    ph_list.set_defaults(func=_cmd_hooks_list)
    ph_install = hooks_sub.add_parser(
        "install",
        help="copy a host's hook script(s) to the stable path ~/.daimon/hooks/ "
             "and print the registration snippet — re-run after every upgrade",
    )
    ph_install.add_argument("host", help="host to install (see `daimon hooks list`)")
    ph_install.set_defaults(func=_cmd_hooks_install)

    p_skill = sub.add_parser(
        "skill",
        help="install the daimon agent skill into a host's rules/skills file (#66)")
    skill_sub = p_skill.add_subparsers(dest="skill_cmd", required=True)
    skill_sub.add_parser = functools.partial(skill_sub.add_parser, formatter_class=fmt)
    ps_list = skill_sub.add_parser("list", help="hosts with a skill renderer")
    ps_list.set_defaults(func=_cmd_skill_list)
    ps_show = skill_sub.add_parser(
        "show", help="print the canonical skill content")
    ps_show.add_argument("--compact", action="store_true",
                          help="print the rules-host variant instead of SKILL.md")
    ps_show.set_defaults(func=_cmd_skill_show)
    ps_install = skill_sub.add_parser(
        "install", help="write the skill for a host (global scope by default)")
    ps_install.add_argument("host", help="host to install (see `daimon skill list`)")
    ps_install.add_argument("--project", action="store_true",
                             help="write into the current repo instead of $HOME")
    ps_install.set_defaults(func=_cmd_skill_install)
    ps_uninstall = skill_sub.add_parser(
        "uninstall", help="remove exactly what install wrote")
    ps_uninstall.add_argument("host")
    ps_uninstall.add_argument("--project", action="store_true")
    ps_uninstall.set_defaults(func=_cmd_skill_uninstall)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
