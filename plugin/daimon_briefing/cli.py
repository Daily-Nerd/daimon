"""Dogfood CLI — works WITHOUT hermes, on a plain text/markdown transcript.

    daimon serialize <transcript-file>   transcript -> checkpoint (+latest)
    daimon brief                          latest checkpoint -> briefing on stdout
    daimon recall <query...>              FTS5 search over local + team
                                         checkpoint history (derived index)
    daimon status [--project DIR] [--json]
                                         checkpoint presence/age + last
                                         serialize outcome from the log
    daimon heal [--force]                 re-serialize the most recent
                                         FAILED session if safe (#26);
                                         --force ignores a prior retry
                                         marker (#15)
    daimon configure [--backend ...]     detect the resolved LLM backend
                                         and fill gaps in ~/.daimon/env
    daimon write-checkpoint [--project DIR] [--source S]
                                         store a checkpoint read as JSON on
                                         stdin (the #23 introspection path)
"""

import argparse
import functools
import getpass
import hashlib
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import amendments, anchor, briefing, capture, carry, config, configure, harvest, inspector, ledger, llm, normalize, privacy, provenance, recall, receipts, redact, refutations, relations, render, schema, serializer, store, teamsync, transcript, worldcheck
from . import __version__

# The serialize.log ledger subsystem lives in ledger.py (#147 + #162, pure
# moves). EVERY moved name is re-imported here — including the ones cli.py no
# longer calls itself — because `cli.<name>` is a stable seam: tests and host
# hooks resolve the ledger through this module.
from .ledger import (
    AUTO_BRIEF_HOSTS,
    _HEAL_SKIP_REASON,  # noqa: F401 — re-exported for compat
    _HEAL_TRANSCRIPT_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_OK_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_PROJECT_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_SKIP_RE,  # noqa: F401 — re-exported for compat
    _LEDGER_SPAWN_TRANSCRIPT_RE,  # noqa: F401 — re-exported for compat
    _RESULT_ERR_RE,  # noqa: F401 — re-exported for compat
    _RESULT_OK_RE,  # noqa: F401 — re-exported for compat
    _SPAWN_RE,  # noqa: F401 — re-exported for compat
    _STATS_HOST_RE,  # noqa: F401 — re-exported for compat
    _USAGE_STAMP_FMT,  # noqa: F401 — re-exported for compat
    _append_retry_log,
    _append_serialize_log,
    _compute_outstanding,
    _format_age,
    _heal_plan,
    _outstanding_failures,  # noqa: F401 — re-exported for compat
    _parse_serialize_log,
    _parse_stamp,
    _session_ledger,
    _spawns_in_window,  # noqa: F401 — re-exported for compat
    _spawns_in_window_count,
    _stats_capture,
)

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
    # #383: an explicit litellm backend missing its key/model still has a
    # rescue when fallback is enabled (default) and a command backend
    # resolves — _chat_litellm raises ChatError and llm.chat routes to the
    # command CLI. Hard-failing here killed the entire no-key error class
    # (28% of one field install's capture errors, zero rescues attempted)
    # before the rescue machinery could ever run. Fallback off, or nothing
    # resolving, keeps the early named error — strictly better than an LLM
    # failure minutes later inside a detached hook child.
    if (not (config.llm_api_key() and config.llm_model())
            and config.llm_fallback() and llm._resolve_command() is not None):
        return None
    if not config.llm_api_key():
        return ("error: no LLM API key — set DAIMON_LLM_API_KEY "
                f"(env or ~/.daimon/env) (transcript: {path})")
    if not config.llm_model():
        return ("error: no LLM model — set DAIMON_LLM_MODEL "
                f"(env or ~/.daimon/env) (transcript: {path})")
    return None


def _attach_serialize_log_handler() -> None:
    """Route `daimon_briefing` logger records (INFO+) into serialize.log for
    the serialize path only (#194). Without any handler, logging.lastResort
    dumps WARNING+ to stderr — which spawn_serialize points at
    serialize-crash.log — so an ordinary quote-verification downgrade read as
    a crash in `status`. First-class instead: the record lands in
    serialize.log, timestamped (`<iso> LEVEL logger: message`) so no ledger
    regex (_SPAWN_RE / _RESULT_*_RE / _LEDGER_*_RE) can ever match it, and a
    handler on the package logger suppresses lastResort. Keyed by target path
    (a DAIMON_LOG_DIR repoint replaces the handler; a repeat in-process
    serialize does not stack a second one). Fail-open: an unwritable log dir
    must never fail the serialize. brief/status never call this — they keep
    writing nothing."""
    pkg_log = logging.getLogger("daimon_briefing")
    try:
        target = config.log_dir() / "serialize.log"
    except Exception:
        return
    for h in pkg_log.handlers:
        if getattr(h, "_daimon_serialize_log", None) == str(target):
            return
    for h in list(pkg_log.handlers):  # stale target (log dir repointed)
        if getattr(h, "_daimon_serialize_log", None):
            pkg_log.removeHandler(h)
            h.close()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(target, encoding="utf-8", delay=True)
    except OSError:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%SZ")
    fmt.converter = time.gmtime  # ledger stamps are UTC "Z"; match them
    handler.setFormatter(fmt)
    handler.setLevel(logging.INFO)
    handler._daimon_serialize_log = str(target)
    pkg_log.addHandler(handler)
    # INFO must pass the logger-level gate too (default effective WARNING) —
    # the chunked-serialize heartbeat is the reason this log exists at all.
    pkg_log.setLevel(logging.INFO)


def _run_serialize(transcript_path: Path, project: str | None,
                   escalate: bool = False) -> int:
    """Serialize one transcript to a checkpoint routed to `project` (used AS-IS;
    None => global pointer only, NO cwd fallback). The caller decides routing —
    this never calls _resolve_project, so `heal` can route to the FAILED
    session's project rather than the heal-time cwd.

    `escalate` (#360): forwarded to serialize_strict — perspective-diverse
    extraction instead of the default single shape. Only `heal` may pass True
    (behind DAIMON_HEAL_ESCALATION); the hook/`serialize` command paths never
    do, so escalation cost scales with failure, not usage.

    Every result line is built once into `msg`, printed, AND logged via
    _append_serialize_log — the logged string is byte-identical to the printed
    one so _RESULT_OK_RE / _RESULT_ERR_RE (raw, no timestamp) still match it.
    (No "(superseded by newer checkpoint)" hint here: result lines carry no
    timestamp to compare against a checkpoint mtime — out of scope, FR #27.)
    Returns the rc."""
    # #194: serializer/llm diagnostics belong in serialize.log, not lastResort
    # stderr (which lands in serialize-crash.log and misreads as a crash).
    _attach_serialize_log_handler()
    path = transcript_path
    # Hash the raw transcript bytes at read time (#125), before any LLM work, so
    # the checkpoint can bind to its exact source content. None when unreadable —
    # stamped only when present; readers tolerate its absence (old checkpoints).
    transcript_sha = transcript.file_sha256(path)
    session_id = path.stem

    # Identical-bytes guard (#185): a `claude --resume` fork leaves the ORIGINAL
    # session's transcript on disk unchanged, but a SessionEnd can still fire for
    # it later (host quirk, retry, manual re-run) — without this, that re-run
    # burns a full LLM call reproducing a byte-identical checkpoint and reports
    # a misleading fresh "success" while the real (forked) session's work never
    # gets captured. Checked BEFORE parsing/preflight/LLM so a hash match short-
    # circuits all of it, even with no LLM backend configured at all.
    if store.transcript_unchanged(session_id, transcript_sha):
        msg = f"skipped serialize for {session_id}: transcript unchanged since checkpoint (hash match)"
        print(msg)
        _append_serialize_log(msg)
        return 0

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

    # Elapsed time lands in serialize.log — checkpoint generation runs 4-25 min
    # in production and was invisible before this.
    llm.reset_fallback()  # #28: detect a silent backend downgrade during THIS run
    # #458: same unit-of-work contract for the served-model collector — the
    # provenance stamp must report what the wire said during THIS serialize,
    # not receipts left over from an earlier run in a long-lived process.
    llm.reset_served_models()
    start = time.monotonic()
    # Same total budget as the hook path (hooks.py:73) — this entry point had
    # none at all, so a manual `daimon serialize` had no bound even in
    # principle while the SessionEnd hook did (#298).
    deadline = start + config.timeout_seconds()
    # #534: one slug-stamped entry touch attributes this run's whole
    # heartbeat trail to its project (step touches preserve the content), so
    # a brief in another shell can say "a serialize for THIS project is in
    # flight". Before the pipeline so the stamp exists for the entire run;
    # a too-short skip writes its result line immediately, which ends the
    # session's classification exactly as before.
    ledger.touch_heartbeat(session_id, project_slug=store.project_slug(project))
    try:
        # THE shared pipeline (#432): serialize -> stamps -> carry+fold ->
        # bind_links -> supersede emission -> write -> rejection ledger.
        # Identical for both capture doors — hooks.on_session_end calls the
        # same function; tests/test_capture_parity.py guards the parity.
        # Error POSTURE stays here: capture raises, this door prints/exits.
        out = capture.run(session_id, messages, project=project, chat=_chat,
                          deadline=deadline, transcript_path=path,
                          transcript_sha=transcript_sha, escalate=escalate)
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
    finally:
        # #564: the pipeline is over on every path out of this try — success
        # (checkpoint already on disk), skip, error, or an unexpected raise —
        # so the liveness stamp must go with it. Leaving it made every brief
        # inside the hung ceiling claim a finished serialize was in flight.
        ledger.clear_heartbeat(session_id)
    if out is None:
        # #421: the write boundary refused (kill switch). Same "skipped" shape
        # as the hash-match short-circuit above — matches neither _RESULT_OK_RE
        # nor _RESULT_ERR_RE, so the ledger never reads it as a lying success.
        msg = (f"skipped serialize for {session_id}: daimon disabled "
               "(DAIMON_DISABLE) — checkpoint not written")
        print(msg)
        _append_serialize_log(msg)
        return 0
    elapsed = int(time.monotonic() - start)
    msg = f"wrote checkpoint: {out} (took {elapsed}s)"
    if llm.fallback_used():
        # Trailing marker (#28): the configured backend failed and the weaker
        # command fallback produced this checkpoint — success, but downgraded.
        # Suffix-safe: _RESULT_OK_RE/_LEDGER_OK_RE are prefix-anchored.
        msg += " [fallback backend]"
    print(msg)
    _append_serialize_log(msg)
    # #246: the write above staled the recall index; freshen it HERE, where
    # nobody is waiting, instead of on the next session's first-prompt
    # recall-inject. After the print/log so the byte-identical result
    # contract above is untouched; warm() itself never raises.
    recall.warm()
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


# Moved to capture.py (#432) with the rest of the shared pipeline; re-exported
# because `cli.<name>` is a stable seam — tests and callers resolve them here.
_emit_supersede_candidates = capture._emit_supersede_candidates
_session_end_stamp = capture._session_end_stamp


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
    # #292: this dict was just authored by a model on the other end of the
    # pipe (the docstring's "live session emits its own cognitive state") —
    # same spoofing risk serialize_strict guards against, since validate()
    # never inspects top-level keys.
    serializer.strip_code_owned_keys(checkpoint)
    # #358, same discipline one level down: with no transcript here, no
    # model-claimed source_message_ids binding is validatable — drop them all
    # (empty id map = nothing the code can vouch for).
    serializer.sanitize_source_ids(checkpoint, {})
    # #359: `grounded` is a code-derived attestation; with no transcript
    # there are no signals, so a model-claimed verdict is stripped (empty
    # signal set = strip-only, no downgrade).
    serializer.ground_outcomes(checkpoint, set())
    # #511, the last field of the same discipline: with no transcript,
    # verify_quotes never runs here — a model-claimed `verbatim` is a
    # byte-check this path cannot perform, and carry's #22 freeze would
    # prefer it over genuinely extracted content. Unconditional (not keyed
    # to --source): the path never has a transcript regardless of what the
    # caller claims the checkpoint is.
    serializer.downgrade_unverifiable_verbatim(checkpoint)
    checkpoint["source"] = args.source  # provenance: introspection vs reconstruction
    session_id = str(checkpoint["session_id"])
    out = store.write_checkpoint(session_id, checkpoint, project_dir=_resolve_project(args.project))
    if out is None:  # #421: write boundary refused (kill switch)
        print("error: daimon disabled (DAIMON_DISABLE) — checkpoint not written",
              file=sys.stderr)
        return 1
    render.render_write_checkpoint([f"wrote checkpoint: {out} (source: {args.source})"])
    recall.warm()  # #246: freshen off the read path; never raises
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
    if store.write_checkpoint(session_id, checkpoint, project_dir=project) is None:
        # #421: write boundary refused (kill switch) — nothing was attached
        print("error: daimon disabled (DAIMON_DISABLE) — checkpoint not written",
              file=sys.stderr)
        return 1
    render.render_anchor_attach([f"attached {a['qualified_name']} to: {item.get('text')}"])
    recall.warm()  # #246: the re-write staled the index; freshen off the read path
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


def _render_briefing_body(checkpoint, route, *, drift_project, teammates,
                          worldcheck_project=None) -> int:
    """Shared tail of `brief` and `brief --slug`: withhold, worldcheck, drift,
    render, footnotes. `route` is whatever the events ledger should be keyed
    by — a project dir on the normal path, a bare slug on the --slug path (the
    store's slug munging is idempotent, so a slug rides through
    project_dir-shaped APIs unchanged; guarded by
    test_project_slug_is_idempotent_on_slugs).
    `drift_project=None` skips the anchor drift check: anchor paths are
    relative to the origin project's root, which a slug cannot recover.
    `worldcheck_project=None` skips the #365 external-state spot-check for the
    same reason drift skips: --slug and global-fallback briefs render ANOTHER
    project's checkpoint, and `gh` probes resolve against THIS cwd's repo —
    the wrong repo context for those claims."""
    withheld = []
    events = {}
    if checkpoint:
        # Withhold (#103): render-time derivation, fail-open — a briefing
        # must never die over suppression machinery. #14: candidates ride
        # along stamped on `checkpoint` itself — the deterministic path
        # (render_plain via briefing._line) picks up the annotation; the
        # opt-in LLM briefing path does not surface it (same pre-existing
        # scope as [carried]), so nothing further is done with them here.
        try:
            events = store.resolutions(project_dir=route)
            # #691: verified/ratified amendments annotate their items —
            # renderable() refuses candidates, so nothing unverified can
            # reach the render through this argument. Same fail-open.
            checkpoint, withheld, _candidates = briefing.withhold(
                checkpoint, events,
                amendments=amendments.renderable(project_dir=route))
        except Exception:
            withheld = []
            events = {}
        # Corroboration (#268): a SEPARATE axis from the trust class — how many
        # independent sessions have witnessed the claim, never what kind of
        # evidence it is. Its own try: a failure here must cost the badge
        # only, not the withheld list `events` still owes to stale_carried
        # below. Transient stamps on the in-memory checkpoint, same posture as
        # the candidate flags above and the worldcheck flags below.
        try:
            checkpoint = briefing.mark_corroborated(
                checkpoint, store.corroborations(project_dir=route))
        except Exception:
            pass
    # Worldcheck (#365/#397/#439): opt-in, budget-bounded, read-only
    # spot-check of carried claims — repo state via `gh`, on-disk state via
    # the filesystem, and the origin checkpoint's signed receipt via the vitni
    # CLI. Stamps contradicted items on the
    # IN-MEMORY checkpoint before render (transient, like withhold's
    # candidate stamps; never persisted). Fail-open like withhold above: a
    # briefing must never block or die on the network. Counters ride the
    # same usage.log every other counter uses, so `daimon stats` surfaces
    # the fires-true rate with zero extra machinery.
    if checkpoint and worldcheck_project and config.worldcheck_enabled():
        try:
            wc_stats = dict(worldcheck.check(checkpoint, worldcheck_project))
            # #439: the receipt-validity class returns its FAILURES alongside
            # the counters, under a reserved key. worldcheck writes nothing to
            # disk by contract, so the rejection-ledger append happens HERE,
            # where the project route is already resolved. Popped first: the
            # counter loop below must see an all-ints dict.
            ledger_rows = wc_stats.pop(worldcheck.LEDGER_KEY, ())
            # #397: the dict carries the aggregate outcomes AND a
            # "<class>:<outcome>" key per class, so one pass emits both the
            # slice-1 counters (unchanged meaning) and the per-class
            # fires-true rate the next expansion gate reads.
            for counter, count in sorted(wc_stats.items()):
                for _ in range(int(count)):
                    _note_usage(f"worldcheck:{counter}")
            # A POINTER and a REASON CODE, never the item's text (#376) — the
            # same second stream capture writes, for the same reason: folded
            # into events.jsonl a rejection would HIDE the item it describes.
            for item_ref, check, reason in ledger_rows:
                store.append_verification(item_ref, check, reason,
                                          project_dir=route)
        except Exception:
            pass
    # NOTE: drift is checked against the resolved project root. If read_latest fell
    # back to the GLOBAL pointer (another project's checkpoint), its anchor file paths
    # are relative to a different root and may report spurious "hard" drift. Acceptable
    # for v1 (degrades safely); origin-project gating is future work (#60 follow-up).
    drift = (anchor.drifted(checkpoint, drift_project)
             if checkpoint and drift_project else [])
    # #523: the baton leads the briefing. Fail-open like withhold — a broken
    # events file must never take the briefing down.
    try:
        handoff = store.active_handoff(route)
    except Exception:
        handoff = None
    render.render_brief(checkpoint, drift=drift, teammates=teammates,
                        handoff=handoff)
    if withheld:
        render.render_brief_note([
            f"{len(withheld)} resolved item(s) withheld — "
            "`daimon status --suppressed` to list"])
    # Staleness budget (#215): reuses the SAME resolutions fold `withhold`
    # already did above — no re-read of events.jsonl. Fail-open, same shape
    # as the withhold try/except; a broken stale_carried must never take the
    # briefing down with it. House rule: zero stale items -> NO line at all,
    # never a false alarm.
    if checkpoint:
        try:
            stale_items = briefing.stale_carried(checkpoint, events, time.time())
        except Exception:
            stale_items = []
        if stale_items:
            render.render_brief_note([
                f"⚠ {len(stale_items)} carried item(s) unverified for "
                f">{config.stale_days():g} days — world-check before "
                "repeating as true"])
    return 0


def _cmd_brief(args) -> int:
    _note_usage("brief:auto" if getattr(args, "auto", False) else "brief")
    slug = getattr(args, "slug", None)
    if slug:
        # Deliberate cross-project read (#243). Explicit-never-automatic is
        # the #94/#95 lesson, so: no global-pointer fallback (the target was
        # named — somebody else's checkpoint is never an answer), no --team
        # (fan-in routes by path), and a provenance header so this can never
        # masquerade as the current project's briefing.
        if args.project:
            print("error: --slug and --project are two answers to \"which "
                  "bucket\" — pass one", file=sys.stderr)
            return 2
        if getattr(args, "team", False):
            print("error: --team routes by project path and cannot combine "
                  "with --slug", file=sys.stderr)
            return 2
        checkpoint = store.read_latest(project_dir=slug, fallback=False)
        if not isinstance(checkpoint, dict):
            render.render_brief_note([
                f"no checkpoint bucket for slug {slug} — "
                "`daimon projects` lists what exists"])
            return 1
        render.render_brief_note([f"cross-project briefing — project: {slug}"])
        return _render_briefing_body(checkpoint, slug,
                                     drift_project=None, teammates=None)
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
        # #223: the foreign body is suppressed above, but --team still means
        # --team — a fresh project with no checkpoint of its own is exactly
        # the new-teammate case where reading the team's briefings matters
        # most. Same unprotected exposure as the main :546 call site below
        # (no new armor here); empty team -> render_teammates no-ops, so a
        # team-less machine's output stays byte-identical to today.
        if getattr(args, "team", False):
            render.render_teammates(_team_briefings(project))
        return 0
    # Label the global-pointer fallback (#29): status calls the same situation
    # "global checkpoint (fallback)"; brief must not present another project's
    # state as this project's without saying so.
    if fallback_used:
        render.render_brief_note(["⚠ no checkpoint for this project — showing the global "
                                  "checkpoint (fallback), possibly another project's."])
    # #534: a LIVE serialize for this project means a fresher checkpoint is
    # being written right now — say so instead of silently briefing one
    # session behind (measured at 10% of runs on one field machine). Keyed on
    # the ledger's liveness bar, never heartbeat existence: a stuck or
    # crashed serialize is heal's case, and a permanent false staleness line
    # would be worse than the silence this fixes.
    if ledger.serialize_in_flight(store.project_slug(project) or ""):
        render.render_brief_note([
            "⏳ a serialize is in flight — this briefing may be one session "
            "behind; re-run `daimon brief` in a few minutes for the fresh one."])
    # --team (#111): fan in teammates for THIS project. Empty team → None → the
    # renderer emits no Teammates section, byte-identical to a non-team briefing.
    teammates = _team_briefings(project) if getattr(args, "team", False) else None
    # #365: never worldcheck a fallback body — the global pointer may belong
    # to ANOTHER project, and probing this cwd's repo against that
    # checkpoint's claims answers for the wrong repo.
    return _render_briefing_body(checkpoint, project,
                                 drift_project=project, teammates=teammates,
                                 worldcheck_project=None if fallback_used
                                 else project)


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
    slug = getattr(args, "slug", None)
    if slug and args.project:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return 2
    if slug and args.all_projects:
        print("error: --slug scopes to one project; drop it or drop "
              "--all-projects", file=sys.stderr)
        return 2
    project = _resolve_project(args.project)
    try:
        results = recall.search(query, project_dir=project, slug=slug,
                                all_projects=args.all_projects, limit=args.limit)
    except recall.RecallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    if not results:
        # #259: a zero-match SCOPED search is a signpost, not a dead end —
        # rerun the same query unscoped (one extra FTS query, same index)
        # and report COUNTS by project, never content: crossing projects
        # stays user-invoked (#94/#95), the system just stops hiding that
        # crossing would pay. Explicit scopes (--all-projects already
        # searched everything; --slug named its target) get no second-guess.
        # Same doctrine as the AND->OR retry (#25): a narrower scope must
        # never mean a silent dead end.
        if not args.all_projects and not slug:
            try:
                wide = recall.search(query, all_projects=True, limit=50)
            except recall.RecallError:
                wide = []
            counts: dict = {}
            here = store.project_slug(project)
            for r in wide:
                s = r.get("project_slug")
                if s and s != here:
                    counts[s] = counts.get(s, 0) + 1
            if counts:
                lines = [f"no matches in this project — "
                         f"{sum(counts.values())} match(es) elsewhere:"]
                lines += [f"  {s} ({n})" for s, n in
                          sorted(counts.items(), key=lambda kv: -kv[1])]
                lines.append("rerun with --all-projects, or --slug <slug> "
                             "for one project")
                render.render_recall_lines(lines)
                return 0
        render.render_recall_lines(["no matches"])
        return 0
    now = time.time()
    lines = []
    for r in results:
        age = _format_age(now - r["created"]) if r.get("created") else "?"
        sup = r.get("superseded_by")
        superseded = ("" if not sup
                      else " [resolved]" if sup == "resolved"
                      else f" [superseded by {sup}]")
        trust = r.get("trust") or "untagged"
        item_id = f" [{r['item_id']}]" if r.get("item_id") else ""
        lines.append(f"[{r['author']}] [{trust}] [{r['kind']}]{item_id} {r['text']} "
                     f"({r['session_id']}, {age} ago){superseded}")
    render.render_recall_lines(lines)
    return 0


def _cmd_why(args) -> int:
    """Render one project-scoped, read-side trust receipt (#502)."""
    _note_usage("why")
    if args.slug and args.project:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return 2
    if not inspector.valid_item_id(args.item_id):
        print("error: invalid item id — expected "
              "[a-z]-[0-9a-f]{6,40}(-N)?", file=sys.stderr)
        return 2
    project = args.slug or _resolve_project(args.project)
    result = inspector.inspect_item(
        project, args.item_id, include_source=args.source)
    if result is None:
        print(f"no item {args.item_id!r} in this project", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        render.render_recall_lines(inspector.human_lines(result))
    return 0


def _cmd_serve(args) -> int:
    """Front door to the read-only viewer (#670). Delegates to daimon_ui's own
    argv path so there is exactly one config surface — the CLI never grows its
    own copy of the flag handling."""
    _note_usage("serve")
    import daimon_ui.__main__ as ui_main
    argv = []
    if args.data_dir:
        argv += ["--data-dir", args.data_dir]
    if args.project_dir:
        argv += ["--project-dir", args.project_dir]
    if args.port is not None:
        argv += ["--port", str(args.port)]
    if args.no_browser:
        argv.append("--no-browser")
    return ui_main.main(argv) or 0


# ---- projects: cross-project bucket list (#243) ----


_TOPIC_TEASER_CHARS = 60


def projects_rows(project_arg=None) -> list:
    """One JSON-ready row per checkpoint bucket, newest first. Single
    assembler for `daimon projects --json` AND the MCP projects tool (#261) —
    two consumers, one shape. Torn buckets show with unknown fields rather
    than vanish: hiding one would read as "no such project"."""
    cur_slug = store.project_slug(_resolve_project(project_arg))
    rows = []
    for b in store.list_buckets():
        cp = b["checkpoint"] or {}
        created = cp.get("created")
        topic = ((cp.get("working_context") or {}).get("active_topic") or {})
        name = cp.get("project_name")
        rows.append({
            "slug": b["slug"],
            # #672 write-time stamp; None when the bucket predates it — never
            # a slug-derived guess, the flattening is not invertible.
            "name": name if isinstance(name, str) and name else None,
            "session_id": cp.get("session_id"),
            "created": created if isinstance(created, str) else None,
            "git_branch": cp.get("git_branch"),
            "topic": topic.get("text") if isinstance(topic, dict) else None,
            "current": b["slug"] == cur_slug,
            # display sort key only, never emitted: created stamp when the
            # pointer has one, pointer mtime for torn/stampless buckets
            "_epoch": store._created_epoch(created) or b["mtime"],
        })
    rows.sort(key=lambda r: r["_epoch"], reverse=True)
    for r in rows:
        del r["_epoch"]
    return rows


def _cmd_projects(args) -> int:
    """Read-only orientation for context switching — the crossing itself
    stays explicit (`brief --slug` / `recall --slug`), the #94/#95 lesson."""
    _note_usage("projects")
    rows = projects_rows(getattr(args, "project", None))
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        render.render_recall_lines(
            ["no project buckets yet — the first serialized session creates one"])
        return 0
    now = time.time()
    display = []
    for r in rows:
        epoch = store._created_epoch(r["created"])
        age = f"{_format_age(now - epoch)} ago" if epoch else "?"
        topic = (r["topic"] or "").strip()
        if len(topic) > _TOPIC_TEASER_CHARS:
            topic = topic[:_TOPIC_TEASER_CHARS - 1] + "…"
        display.append({
            "mark": "*" if r["current"] else " ",
            "name": r["name"] or "—",
            "slug": r["slug"], "age": age,
            "branch": r["git_branch"] or "—", "topic": topic or "?",
        })
    render.render_projects(display)
    return 0


# ---- resolve/log: zero-LLM append-only event writers (#102) ----


def _cmd_resolve(args) -> int:
    """Append a resolution event for ONE checkpoint item (#102). Exact id
    first; else a fuzzy query that must match uniquely — an ambiguous bind
    is refused with candidates listed, because a wrong bind silently
    suppresses a live memory (the false-merge lesson, #13).

    `--dry-run` runs this SAME bind and stops right before the event write
    (#304) — the matcher a preview would use is otherwise inherently
    different from the one doing the write (recall: FTS5 over history
    across checkpoints; resolve: carry._same_item over this checkpoint
    only), and a preview that disagrees with the writer is worse than none.
    Ambiguous/no-match refusals return before this point either way, so
    their output is identical with or without the flag.

    #480 slice 2 — the agent write path: `--by agent --evidence "<quote>"`
    appends a `resolving-candidate` event, `source="agent"`, instead of the
    human path's immediate `resolved`/`source="cli"`. Evidence is mandatory
    with `--by agent` (mirrors #103 reverify's evidence gate on the reopen
    side) — a call without it is refused before any checkpoint I/O, nothing
    written, logged as `resolve:no-evidence` (extends #303). `--evidence`
    without `--by agent` is rejected too: it is not a `--note` synonym on
    the human path. The bind (exact id, unique fuzzy match, ambiguous/
    no-match refusal, --dry-run) is identical on both paths — only what gets
    written at the end differs. `resolving-candidate` is deliberately NOT
    'resolved': store.is_resolved/_tie_rank exempt it exactly as they exempt
    #14's supersede-candidate, so an agent's claim never withholds the item
    on its own (the #13 false-merge lesson) until slice 3's serializer
    verifies the quote or a human confirms."""
    by_agent = getattr(args, "by", None) == "agent"
    raw_evidence = getattr(args, "evidence", None)
    if raw_evidence is not None and not by_agent:
        print("--evidence requires --by agent — the human path vouches by "
              "calling resolve directly, no evidence needed")
        return 1
    evidence = (raw_evidence or "").strip()
    if by_agent and not evidence:
        # Same #303 stance as the ambiguous/no-match refusals below: a
        # refused attempt must leave its own trace, so "no agent ever tried"
        # and "an agent tried and was refused for lacking evidence" stay
        # distinguishable in `daimon stats`.
        _note_usage("resolve:no-evidence")
        print("--by agent requires --evidence \"<verbatim transcript quote>\" "
              "— refused, nothing written")
        return 1
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
            # #303: a refused attempt must leave its own trace — otherwise
            # "no agent ever tried resolve" and "an agent tried and was
            # refused" are indistinguishable in `daimon stats`, and the two
            # have opposite fixes (teaching vs UX).
            _note_usage("resolve:no-match" if not hits else "resolve:ambiguous")
            label = "no item matches" if not hits else "ambiguous — matches"
            print(f"{label} {args.target!r}; candidates:")
            listing = hits or items
            for key, it in listing:
                print(f"  {it['id']}  [{key}] {it.get('text', '')}")
            print("resolve by exact id: daimon resolve <id>")
            return 1
    # #480 slice 2: the agent path writes a candidate status, credited to
    # source="agent" — NOT args.status/"cli", which stay exactly what the
    # human path has always written (byte-identical human behavior).
    effective_status = "resolving-candidate" if by_agent else args.status
    if getattr(args, "dry_run", False):
        # A distinct tag, not "resolve": nothing was written, so folding this
        # into the success counter would inflate it with attempts that never
        # touched the ledger — corrupting the exact refusal-rate signal #303
        # exists to expose. Not silent either: the tag still shows up in
        # `daimon stats` usage counts, just apart from resolve/resolve:*.
        _note_usage("resolve:dry-run")
        print(f"would resolve {target['id']}: {target.get('text', '')} [{effective_status}]")
        return 0
    ok = store.append_event(
        target["id"], effective_status,
        note=(evidence if by_agent else (args.note or "")),
        source=("agent" if by_agent else "cli"),
        project_dir=project, item_text=str(target.get("text") or ""))
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    if by_agent:
        _note_usage("resolve:agent")
        print(f"claim recorded {target['id']}: {target.get('text', '')} "
              "— pending verification at session end")
        return 0
    _note_usage("resolve")
    print(f"resolved {target['id']}: {target.get('text', '')} [{args.status}]")
    return 0


# ---- refute: project-level negative-knowledge ledger (#573) ----


def _refutation_json(record) -> str:
    """JSON for one folded record or a list of them.

    #576: every record carries `evidence_status: "cited"`.  `evidence` holds
    typed source strings that were shape-checked and redacted on the way in and
    never resolved — daimon does not open them, does not confirm the referent
    exists, and does not judge whether it entails the verdict.  The text
    renderer says so in a parenthetical; without this key a machine consumer
    read sourced-looking strings under a key called `evidence` with nothing to
    contradict the obvious reading.  The value is a constant today because
    `cited` is the only status the ledger can currently earn (#581 would add a
    resolved one).
    """
    def stamped(row: dict) -> dict:
        return {**row, "evidence_status": "cited"}
    payload = ([stamped(row) for row in record]
               if isinstance(record, list) else stamped(record))
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _print_refutation(record: dict, *, detailed: bool = False,
                      tag: bool = False) -> None:
    state = record.get("state") or "candidate"
    activation = record.get("activation") or f"{record.get('asserted_by', '?')}-proposed"
    mark = "✗" if state == "active" else ("×" if state == "overturned" else "?")
    word = "refutation " if tag else ""
    print(f"[{word}{mark} {state} · {activation}] {record['refutation_id']}  "
          f"{record.get('subject', '')}")
    if not detailed:
        return
    print(f"  Verdict: {record.get('verdict', '')}")
    print(f"  Scope: {record.get('scope', '')}")
    anchors = record.get("anchors") or []
    if anchors:
        print(f"  Anchors: {', '.join(anchors)}")
    revisit = record.get("revisit_when") or ""
    if revisit:
        print(f"  Revisit when: {revisit}")
    evidence = record.get("evidence") or []
    for item in evidence:
        print(f"  Evidence: {item}")
    if evidence:
        # #576: Evidence sits in the same Label: value register as Provenance
        # and Authority, which ARE derived from recorded lifecycle facts.  The
        # source string is shape-checked and never resolved, so say so here —
        # this is the only surface a reader of `refute show` actually reads.
        print("  (evidence sources are recorded as cited; "
              "daimon does not verify them)")
    print(f"  Provenance: asserted by {record.get('asserted_by', '?')} "
          f"({record.get('asserted_author') or 'unknown'})")
    if record.get("activation"):
        print(f"  Authority: {record['activation']} "
              f"({record.get('activation_author') or 'unknown'})")
    pending = record.get("overturn_proposed")
    if isinstance(pending, dict):
        print(f"  Overturn proposed by {pending.get('by', '?')} — still active")


def _refute_channel(args) -> str:
    """The channel this invocation actually arrived through.

    `--by agent` is a self-declaration of the NARROWER authority, mirroring
    `resolve`, where the human path is likewise the ABSENCE of the flag. A
    human path has to show an interactive terminal, and the CLI can mint
    nothing stronger: `ui` and `signed` are in-process-only, because a channel
    an agent can reach by shelling out is the deleted `--by human` renamed.
    """
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise refutations.RefutationError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record a candidate, or run it from a terminal")
    return "cli-tty"


def _print_ruling(record: dict, *, detailed: bool = False,
                  tag: bool = False) -> None:
    """#693: a ruling renders its VERDICT (the rule text) and never the
    refutation's ✗ glyph; an overturned ruling reads "retired" (label only,
    the state vocabulary is unchanged); and text authored by a non-human
    channel is labeled as such even after human ratification."""
    state = record.get("state") or "candidate"
    shown_state = "retired" if state == "overturned" else state
    activation = (record.get("activation")
                  or f"{record.get('asserted_by', '?')}-proposed")
    authored = record.get("text_authored_by")
    if state == "active" and authored and authored != "human":
        activation = f"{authored}-written, {activation}"
    mark = "§" if state == "active" else ("×" if state == "overturned" else "?")
    word = "ruling " if tag else ""
    print(f"[{word}{mark} {shown_state} · {activation}] "
          f"{record['refutation_id']}  {record.get('verdict', '')}")
    if not detailed:
        return
    print(f"  Governs: {record.get('subject', '')}")
    print(f"  Scope: {record.get('scope', '')}")
    anchors = record.get("anchors") or []
    if anchors:
        print(f"  Anchors: {', '.join(anchors)}")
    revisit = record.get("revisit_when") or ""
    if revisit:
        print(f"  Revisit when: {revisit}")
    for item in record.get("evidence") or []:
        print(f"  Evidence: {item}")
    proposal = record.get("revision_proposed")
    if proposal:
        print(f"  Pending revision proposal ({proposal.get('by', '?')}): "
              f"{proposal.get('verdict') or proposal.get('subject') or ''}")
    retirement = record.get("overturn_proposed")
    if retirement:
        print(f"  Pending retirement proposal ({retirement.get('by', '?')})")


def _refuse_ruling_id(record, verb: str) -> bool:
    """#693: one conversation per record — `refute` verbs refuse ruling ids
    with a pointer, and vice versa."""
    if record is not None and record.get("polarity") == "ruling":
        print(f"{record['refutation_id']} is a ruling; use "
              f"`daimon ruling {verb}`")
        return True
    return False


def _cmd_ruling_propose(args) -> int:
    project = _resolve_project(args.project)
    try:
        ruling_id = refutations.assert_ruling(
            subject=args.subject, verdict=args.verdict, scope=args.scope,
            evidence=args.evidence, channel=_refute_channel(args),
            anchors=args.anchor,
            revisit_when=args.revisit_when or "", ratified=args.ratify,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not recorded: {exc}")
        return 1
    record = refutations.get(ruling_id, project_dir=project)
    _note_usage("ruling:propose")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record["state"] == "candidate":
            if getattr(args, "by", None) != "agent":
                print(f"  Next: daimon ruling ratify {ruling_id} "
                      f"--project {project}")
            else:
                print("  Candidate recorded. Activation requires an explicit "
                      "human decision.")
    return 0


def _cmd_ruling_ratify(args) -> int:
    project = _resolve_project(args.project)
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(f"ruling not ratified: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute ratify`")
        return 1
    if channel != "cli-tty":
        # Never invoke a writer to harvest its error string.
        print("ruling not ratified: ratification requires a human channel; "
              f"this call arrived through {channel!r}")
        return 1
    # Ratification is a signature, not an id-typing exercise: print the FULL
    # text, disclose the render consequence, and bind the append to the key
    # of the text DISPLAYED — the fold refuses to activate any other text.
    # Under --json the ceremony goes to stderr so stdout stays parseable.
    ceremony = sys.stderr if args.json else sys.stdout
    print("About to ratify this ruling:", file=ceremony)
    if not args.json:
        _print_ruling(record, detailed=True)
    else:
        print(f"  {record.get('verdict', '')}", file=ceremony)
    print("  This text will render into every future session for this "
          "project.", file=ceremony)
    displayed_key = normalize.content_key(record.get("verdict") or "")
    answer = input("Ratify? [y/N]: ").strip().casefold()
    if answer not in ("y", "yes"):
        print("not ratified")
        return 1
    try:
        refutations.ratify(args.ruling_id, channel=channel,
                           note=args.note or "", verdict_key=displayed_key,
                           project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not ratified: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _note_usage("ruling:ratify")
    if record["state"] != "active":
        print("not activated: the text changed during confirmation; "
              "re-run to review the current text")
        return 1
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
    return 0


def _cmd_ruling_revise(args) -> int:
    project = _resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute revise`")
        return 1
    if args.ratify:
        # The ratification ceremony (full text, disclosure, confirm, key
        # binding) lives in ONE place; a revise flag walking around it would
        # activate text the human was never shown.
        print("ruling revise does not activate; revise the candidate, then "
              f"run `daimon ruling ratify {args.ruling_id}`")
        return 1
    try:
        channel = _refute_channel(args)
    except refutations.RefutationError as exc:
        print(f"ruling not revised: {exc}")
        return 1
    if (record["state"] == "active" and channel == "cli-tty"
            and (args.verdict is not None or args.subject is not None)):
        # Rewriting what renders is the same power ratification has, and it
        # earns trust the same way: show the change, disclose, confirm.
        print("About to change the ACTIVE text of this ruling:")
        _print_ruling(record, detailed=True)
        if args.verdict is not None:
            print(f"  New text: {args.verdict}")
        if args.subject is not None:
            print(f"  New governs: {args.subject}")
        print("  This text will render into every future session for this "
              "project.")
        answer = input("Apply? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("not revised")
            return 1
    anchors = args.anchor if args.anchor is not None else None
    try:
        refutations.revise(
            args.ruling_id, channel=channel,
            evidence=args.evidence, subject=args.subject,
            verdict=args.verdict, scope=args.scope,
            anchors=anchors, revisit_when=args.revisit_when,
            ratified=False, project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not revised: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _note_usage("ruling:revise")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if record.get("revision_proposed"):
            print("  Proposal recorded; the active ruling is untouched "
                  "until a human verdict.")
        elif record["state"] == "candidate":
            print("  Revision is not load-bearing until explicit human "
                  "ratification.")
    return 0


def _cmd_ruling_retire(args) -> int:
    project = _resolve_project(args.project)
    try:
        event = refutations.retire(
            args.ruling_id, channel=_refute_channel(args),
            evidence=args.evidence or (),
            note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(f"ruling not retired: {exc}")
        return 1
    record = refutations.get(args.ruling_id, project_dir=project)
    _note_usage("ruling:retire")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
        if event == "overturn-proposed":
            print("  Retirement proposed; the ruling stands until a human "
                  "verdict.")
    return 0


def _cmd_ruling_list(args) -> int:
    project = _resolve_project(args.project)
    rows = refutations.listing(states=set(args.state or refutations.STATES),
                               polarity="ruling", project_dir=project)
    _note_usage("ruling:list")
    if args.json:
        print(_refutation_json(rows))
        active_j = sum(1 for r in rows if r.get("state") == "active")
        cap_j = config.ruling_cap()
        if active_j > cap_j:
            # stderr so the JSON stays parseable and machines still learn it.
            print(f"over cap: {active_j} active vs cap {cap_j}",
                  file=sys.stderr)
        return 0
    if not rows:
        print("no rulings for this project")
        return 0
    for row in rows:
        _print_ruling(row)
    # The cap binds ACTIVATION; a lowered DAIMON_RULING_CAP leaves the
    # excess active, so the over-cap state must be visible somewhere.
    active = sum(1 for r in rows if r.get("state") == "active")
    cap = config.ruling_cap()
    if active > cap:
        print(f"over cap: {active} active vs cap {cap} — retire one, or "
              "raise DAIMON_RULING_CAP deliberately")
    return 0


def _cmd_ruling_show(args) -> int:
    project = _resolve_project(args.project)
    record = refutations.get(args.ruling_id, project_dir=project)
    if record is None:
        print(f"unknown ruling: {args.ruling_id}")
        return 1
    if record.get("polarity") != "ruling":
        print(f"{args.ruling_id} is a refutation; use `daimon refute show`")
        return 1
    _note_usage("ruling:show")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_ruling(record, detailed=True)
    return 0


def _cmd_refute_add(args) -> int:
    project = _resolve_project(args.project)
    try:
        ref_id = refutations.assert_refutation(
            subject=args.subject, verdict=args.verdict, scope=args.scope,
            evidence=args.evidence, channel=_refute_channel(args),
            anchors=args.anchor,
            revisit_when=args.revisit_when or "", ratified=args.ratify,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(f"refutation not recorded: {exc}")
        return 1
    record = refutations.get(ref_id, project_dir=project)
    _note_usage("refute:add")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if record["state"] == "candidate":
            # An agent-authored candidate never gets handed its own escalation
            # command; activation is a decision the human has to reach.
            if getattr(args, "by", None) != "agent":
                print(f"  Next: daimon refute ratify {ref_id} "
                      f"--project {project}")
            else:
                print("  Candidate recorded. Activation requires an explicit "
                      "human decision.")
    return 0


def _cmd_refute_ratify(args) -> int:
    project = _resolve_project(args.project)
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "ratify"):
        return 1
    try:
        refutations.ratify(args.refutation_id, channel=_refute_channel(args),
                           note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(f"refutation not ratified: {exc}")
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _note_usage("refute:ratify")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
    return 0


def _cmd_refute_revise(args) -> int:
    project = _resolve_project(args.project)
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "revise"):
        return 1
    anchors = args.anchor if args.anchor is not None else None
    try:
        refutations.revise(
            args.refutation_id, channel=_refute_channel(args),
            evidence=args.evidence, subject=args.subject, verdict=args.verdict, scope=args.scope,
            anchors=anchors, revisit_when=args.revisit_when,
            ratified=args.ratify, project_dir=project)
    except refutations.RefutationError as exc:
        print(f"refutation not revised: {exc}")
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _note_usage("refute:revise")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if record["state"] == "candidate":
            print("  Revision is not load-bearing until explicit human ratification.")
    return 0


def _cmd_refute_overturn(args) -> int:
    project = _resolve_project(args.project)
    if _refuse_ruling_id(refutations.get(args.refutation_id,
                                         project_dir=project), "retire"):
        return 1
    try:
        event = refutations.overturn(
            args.refutation_id, channel=_refute_channel(args),
            evidence=args.evidence,
            note=args.note or "", project_dir=project)
    except refutations.RefutationError as exc:
        print(f"refutation not overturned: {exc}")
        return 1
    record = refutations.get(args.refutation_id, project_dir=project)
    _note_usage("refute:overturn")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
        if event == "overturn-proposed":
            print("  Agent evidence recorded; the active guard remains until human ratification.")
    return 0


def _cmd_refute_show(args) -> int:
    project = _resolve_project(args.project)
    record = refutations.get(args.refutation_id, project_dir=project)
    if record is None:
        print(f"unknown refutation: {args.refutation_id}")
        return 1
    if _refuse_ruling_id(record, "show"):
        return 1
    _note_usage("refute:show")
    if args.json:
        print(_refutation_json(record))
    else:
        _print_refutation(record, detailed=True)
    return 0


def _cmd_refute_list(args) -> int:
    project = _resolve_project(args.project)
    rows = refutations.listing(states=set(args.state or refutations.STATES),
                               polarity="refutation", project_dir=project)
    _note_usage("refute:list")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        print("no refutations for this project")
    else:
        for row in rows:
            _print_refutation(row)
    return 0


def _cmd_refute_search(args) -> int:
    project = _resolve_project(args.project)
    try:
        rows = refutations.search(
            " ".join(args.query), project_dir=project,
            states=set(args.state or refutations.STATES))
    except refutations.RefutationError as exc:
        print(f"refutation search refused: {exc}")
        return 1
    _note_usage("refute:search")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        print("no matching refutations")
    else:
        # #693: search is the topic-addressable pull path, so it returns BOTH
        # polarities, labelled by their own printers — filtering rulings out
        # would leave the records that matter most with no pull surface
        # mid-session, after the briefing has scrolled away.
        for row in rows:
            if row.get("polarity") == "ruling":
                _print_ruling(row, tag=True)
            else:
                _print_refutation(row, tag=True)
    return 0


def _cmd_refute_guard(args) -> int:
    project = _resolve_project(args.project)
    try:
        rows = refutations.guard(
            " ".join(args.query), anchors=args.anchor,
            project_dir=project)
    except refutations.RefutationError as exc:
        print(f"refutation guard refused: {exc}")
        return 1
    # #581: split by outcome and rail, the way `resolve` splits its tags. One
    # aggregate count cannot separate a hit from a miss, so the false-veto rate
    # the design named as its own expansion gate is uncomputable from field
    # data. The rails also differ in precision, so the hit tag names which one
    # fired: an aggregate cannot say which rail generates the noise.
    _note_usage(f"refute:guard:hit:{rows[0]['guard_match']['rail']}"
                if rows else "refute:guard:miss")
    if args.json:
        print(_refutation_json(rows))
    elif not rows:
        if not args.quiet:
            print("no active refutation matched exact anchors or subject")
    else:
        # One warning is the v1 attention budget. JSON retains every exact hit
        # for deliberation integrations that can reconcile them in batch.
        _print_refutation(rows[0], detailed=True)
        if len(rows) > 1:
            print(f"  + {len(rows) - 1} more exact match(es); use --json to inspect")
    return 0


# ---- #691: amendment verbs — evidence-carrying state transitions ----


def _amend_channel(args) -> str:
    """The channel this invocation actually arrived through — the
    `_refute_channel` contract restated for the amendment ledger (same
    doctrine, its own error type so refusals stay per-surface)."""
    if getattr(args, "by", None) == "agent":
        return "cli-agent"
    if not sys.stdin.isatty():
        raise amendments.AmendmentError(
            "this is the human path and there is no interactive terminal; "
            "pass --by agent to record a candidate, or run it from a terminal")
    return "cli-tty"


def _cmd_amend_propose(args) -> int:
    project = _resolve_project(args.project)
    item_id = str(args.item_id or "").strip()
    # Exact-id binding against the LIVE checkpoint only — an amendment
    # describes an open item's state, so unlike forget it never reaches into
    # prev-N surfaces, and unlike resolve it never fuzzy-matches: the id came
    # off a rendered ` [id]` handle or it does not exist. Loop-shaped items
    # only (#480's scope rule, restated): amending a settled decision or
    # belief would invite exactly the state-rewriting on settled facts that
    # BRIEFABLE_ITEM_KEYS exists to fence off, and `daimon loops` — the
    # discovery surface this command's errors point at — lists only these.
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    live = {
        str(item.get("id") or "")
        for section, key in store._ITEM_LISTS
        if key in briefing.BRIEFABLE_ITEM_KEYS
        for item in ((checkpoint or {}).get(section) or {}).get(key) or []
        if isinstance(item, dict)
    }
    live.discard("")
    if item_id not in live:
        _note_usage("amend:no-match")
        print(f"no open-loop item with id {item_id!r} — amend targets open "
              "questions and uncertainties; `daimon loops` lists them")
        return 1
    if store.is_resolved(store.resolutions(project_dir=project).get(item_id)):
        _note_usage("amend:resolved")
        print(f"{item_id} is already resolved — an amendment describes an "
              "OPEN item; `daimon reverify` reopens one first")
        return 1
    try:
        a_id = amendments.propose(
            item_id=item_id, change=args.change, evidence=args.evidence,
            channel=_amend_channel(args), note=args.note or "",
            project_dir=project)
    except amendments.AmendmentError as exc:
        _note_usage("amend:refused")
        print(f"amendment not recorded: {exc}")
        return 1
    record = amendments.get(a_id, project_dir=project)
    state = record["state"] if record else "candidate"
    _note_usage("amend:agent" if getattr(args, "by", None) == "agent"
                else "amend")
    print(f"amendment {a_id} recorded on {item_id}: {args.change} ({state})")
    if state == "candidate":
        # Same posture as refute add: an agent-authored candidate is never
        # handed its own escalation command — verification is the transcript
        # byte-check at session end, settlement is a human's.
        print("  evidence is byte-checked against the transcript at session "
              "end; a human settles it earlier with `daimon amend ratify` "
              "or `daimon amend reject`")
    return 0


def _cmd_amend_verdict(args) -> int:
    project = _resolve_project(args.project)
    verb = args.amend_cmd
    try:
        channel = _amend_channel(args)
        if verb == "ratify":
            amendments.ratify(args.amendment_id, channel=channel,
                              project_dir=project)
        else:
            amendments.reject(args.amendment_id, channel=channel,
                              note=getattr(args, "note", None) or "",
                              project_dir=project)
    except amendments.AmendmentError as exc:
        _note_usage(f"amend:{verb}:refused")
        print(f"amendment {verb} refused: {exc}")
        return 1
    _note_usage(f"amend:{verb}")
    record = amendments.get(args.amendment_id, project_dir=project)
    print(f"{args.amendment_id}: {record['state'] if record else 'unknown'}")
    return 0


def _cmd_amend_list(args) -> int:
    project = _resolve_project(args.project)
    _note_usage("amend:list")
    rows = sorted(
        amendments.records(project_dir=project).values(),
        key=lambda r: (r["state"] != "candidate",
                       r.get("updated_at") or "", r["amendment_id"]))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("no amendments recorded for this project")
        return 0
    for r in rows:
        quote = briefing._truncate_agent_claim(r.get("evidence"))
        print(f'{r["amendment_id"]}  {r["state"]:<9} {r["item_id"]}  '
              f'{r["change"]}: "{quote}"')
    return 0


def _relations_channel() -> str:
    """The observed write channel for a relation verdict.

    Narrower than `_refute_channel` on purpose: there is no `--by agent`
    here because agents cannot verdict relations AT ALL — the fold ignores
    non-human channels and the module refuses them, so offering the flag
    would only advertise a path that always fails. A verdict has to show an
    interactive terminal; anything else is refused, not downgraded.
    """
    if not sys.stdin.isatty():
        raise relations.RelationError(
            "relation verdicts are human-only and need an interactive "
            "terminal; there is no agent path to confirm, reject, or retract")
    return "cli-tty"


def _relations_endpoint_texts(project_dir) -> dict:
    """Stable cli seam over the engine's read-time id→text join."""
    return relations.endpoint_texts(project_dir)


def _cmd_relations_list(args) -> int:
    project = _resolve_project(args.project)
    # Sort, state filter, and erased-edge withholding all live in
    # relations.listing — the presentation contract shared with the viewer
    # lane, so the two surfaces cannot drift. argparse `choices` already
    # gates unknown states.
    rows, withheld = relations.listing(
        states=set(args.state or relations.STATES), project_dir=project)
    _note_usage("relations:list")
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        if withheld:
            print(f"{withheld} edge(s) withheld (erased endpoint)")
    else:
        texts = _relations_endpoint_texts(project) if rows else {}
        render.render_relations_list(rows, texts, withheld)
    return 0


def _cmd_relations_show(args) -> int:
    project = _resolve_project(args.project)
    record = relations.get(args.relation_id, project_dir=project)
    if record is None:
        print(f"unknown relation: {args.relation_id}")
        return 1
    _note_usage("relations:show")
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        render.render_relation(record, _relations_endpoint_texts(project))
    return 0


def _cmd_relations_verdict(args) -> int:
    project = _resolve_project(args.project)
    move = {"confirm": relations.confirm, "reject": relations.reject,
            "retract": relations.retract}[args.verdict]
    try:
        move(args.relation_id, channel=_relations_channel(),
             project_dir=project)
    except relations.RelationError as exc:
        print(f"relation {args.verdict} refused: {exc}")
        return 1
    _note_usage(f"relations:{args.verdict}")
    state = relations.records(project_dir=project)[args.relation_id]["state"]
    print(f"{args.relation_id} -> {state}")
    return 0


def _cmd_forget(args) -> int:
    """Deliberate item removal (#321): append a tombstone event whose status
    carries a content HASH, never the text — removal means the content leaves
    the audit trail too — then rewrite the live checkpoint without the value.
    Tombstone first (#418): the rewrite's _drop_forgotten reads the ledger, so
    the key must land before the write scrubs sibling ids of the same value.
    Binding is _cmd_resolve's never-guess contract verbatim: exact id
    first, else a fuzzy query that must match exactly one item. The tombstone
    rides the resolutions fold, so withhold, carry suppression, and the
    recall index deletion all inherit it with no new plumbing. The rewritten
    checkpoint re-mints its receipt, so the post-removal state is signed."""
    project = _resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    # #578: the refutation ledger is a second plaintext store, so a value can
    # live there with no checkpoint at all. Bailing on a missing checkpoint
    # would leave that value permanently unreachable.
    # Every plaintext value the record has EVER carried, not only the folded
    # ones: a revision rewrites fields, so the forgotten value can sit in an
    # earlier row that nothing renders. #698: the field walk is the module's
    # OWN declaration (plaintext_values, scalars only — a shared anchor or
    # evidence token must never become a by-value target), never a hand-read
    # `subject`. One entry per record, so a record whose old and new values
    # both match is one hit; `text` stays the latest subject for display.
    ledger_texts: dict[str, list[str]] = {}
    ledger_display: dict[str, str] = {}
    for row in refutations.events(project_dir=project):
        ref_id = str(row.get("refutation_id") or "")
        if not ref_id:
            continue
        for value in refutations.plaintext_values(row):
            ledger_texts.setdefault(ref_id, [])
            if value not in ledger_texts[ref_id]:
                ledger_texts[ref_id].append(value)
        subject = str(row.get("subject") or "")
        if subject:
            ledger_display[ref_id] = subject
    # #693: the ledger holds BOTH polarities; every forget surface (pool
    # label, dry-run, gate, receipt) names a ruling as a ruling. Snapshot
    # taken BEFORE any deletion.
    ledger_meta = {
        rid: (rec.get("polarity") or "refutation", rec.get("state"))
        for rid, rec in refutations.records(project_dir=project).items()}
    ledger = [
        (None, ledger_meta.get(ref_id, ("refutation", None))[0],
         {"id": ref_id,
          "text": ledger_display.get(ref_id, texts[0]),
          "_texts": texts})
        for ref_id, texts in ledger_texts.items()
    ]
    # #691: the amendment ledger is another plaintext store — evidence quotes
    # and human notes. Same every-row posture as the refutation subjects
    # above: a value can sit in any historical row of a record. The field
    # walk is the module's OWN declaration (plaintext_values), never a
    # hand-copied tuple, and `text` is the FIRST value (evidence before
    # note) — the content hash downstream must never key on a note when the
    # user named the evidence. `_target` carries the amended item's id so
    # the fuzzy branch can drop amendment hits that merely orbit an item
    # the query already matched.
    amend_texts: dict[str, list[str]] = {}
    amend_targets: dict[str, str] = {}
    for row in amendments.events(project_dir=project):
        # events() admits only rows whose amendment_id matched the id shape,
        # so the key is present and non-empty by contract.
        a_id = str(row["amendment_id"])
        target_id = str(row.get("item_id") or "")
        if target_id:
            amend_targets.setdefault(a_id, target_id)
        for value in amendments.plaintext_values(row):
            amend_texts.setdefault(a_id, [])
            if value not in amend_texts[a_id]:
                amend_texts[a_id].append(value)
    amend_pool = [
        (None, "amendment", {"id": a_id, "text": texts[0], "_texts": texts,
                             "_target": amend_targets.get(a_id, "")})
        for a_id, texts in amend_texts.items()
    ]
    if not isinstance(checkpoint, dict) and not ledger and not amend_pool:
        print("no checkpoint for this project yet — nothing to forget")
        return 1
    # Every surface this project holds, not just the live checkpoint (#419
    # scope): a value that had been superseded still sits in prev-N and in its
    # session file, and resolving only against `latest` made forget answer "no
    # item matches" about plaintext that was demonstrably on disk.
    seen_ids: set[str] = set()
    items = []
    for _path, section, key, item in store.items_for_project(project):
        if item["id"] in seen_ids:
            continue          # one item, several surfaces — not several items
        seen_ids.add(item["id"])
        items.append((section, key, item))
    # Refutation ids and checkpoint `recent_decisions` ids SHARE the namespace
    # `r-<12 hex>`, so an exact-id lookup can legitimately hit both surfaces.
    # forget's never-guess contract decides it: an ambiguous id is refused, not
    # resolved by preferring a store.
    candidates = items + ledger + amend_pool
    exact = [it for _, _, it in candidates if it["id"] == args.target]
    target = exact[0] if len(exact) == 1 else None
    if target is None:
        # `_generic_terms` is a DOCUMENT-FREQUENCY statistic over texts "of one
        # kind" (carry.py's own wording): terms shared by >= _GENERIC_DF of them
        # are that kind's boilerplate and are subtracted from the matcher.
        # Counting the checkpoint and the ledger together mixes two corpora, so
        # recording refutations inflated the frequency until the query's own
        # terms read as generic — and `forget "<text>"` stopped reaching a
        # checkpoint item it had deleted a moment earlier, reporting "no item
        # matches" about plaintext on disk. Each store is counted on its own
        # and each candidate matched against its own store's vocabulary.
        def _texts_of(it):
            return it.get("_texts") or [str(it.get("text") or "")]

        # #698 review: document frequency is per CANDIDATE, never per field.
        # A record's subject/verdict/scope restate each other by construction,
        # so counting them as separate documents lets one record push its own
        # terms over the generic threshold and hide itself from its exact
        # subject. One concatenated document per candidate keeps the statistic
        # meaning what carry.py says it means: terms shared by >= k ITEMS.
        pools = [(pool, carry._generic_terms(
            [" ".join(_texts_of(it)) for _, _, it in pool]))
            for pool in (items, ledger, amend_pool)]
        query_key = normalize.content_key(args.target)

        def _matched_value(it, generic):
            # The value the QUERY named. The exact canonical match is a rail
            # the GENERIC FILTER can never subtract, no matter how common the
            # value's terms have become (#698 review) — the never-guess
            # ambiguity gate below may still refuse it when several values
            # match. The fuzzy fallback matches each value against the
            # candidate's own store vocabulary.
            for t in _texts_of(it):
                if normalize.content_key(t) == query_key:
                    return t
            for t in _texts_of(it):
                if carry._same_item(args.target, t, generic):
                    return t
            return None

        # Hits carry the value they matched IN the tuple — a side table keyed
        # on object identity would silently degrade to display text if a
        # refactor ever copied a candidate dict (#698 review).
        if len(exact) > 1:
            hits = [(s, k, it, None) for s, k, it in candidates
                    if it["id"] == args.target]
        else:
            hits = [(s, k, it, value)
                    for pool, generic in pools
                    for s, k, it in pool
                    for value in [_matched_value(it, generic)]
                    if value is not None]
        # #691: an amendment's evidence is by construction ABOUT its item, so
        # a query matching the item routinely fuzzy-matches the amendment too
        # — a false-ambiguity surface, not a real second value. When an item
        # hit exists, amendment hits that merely target one of the hit items
        # are dropped: forgetting the item removes its amendments anyway
        # (forget_item_id below), so nothing becomes unreachable.
        item_hit_ids = {it["id"] for _, k, it, _ in hits
                        if k not in ("refutation", "amendment")}
        if item_hit_ids:
            hits = [(s, k, it, m) for s, k, it, m in hits
                    if not (k == "amendment"
                            and it.get("_target") in item_hit_ids)]
        # Ambiguity is about distinct MATCHED values, not hit count and never
        # the display text. The same sentence carried by sibling ids, held on
        # several surfaces, or held in both the checkpoint and the refutation
        # ledger is one thing to forget; #418 already splices sibling ids from
        # a single key. But two records matched on DIFFERENT values behind one
        # shared display subject are two things — collapsing them over the
        # display text silently under-deleted (#698 review). Only genuinely
        # different matched values leave the user a choice, and there
        # never-guess still refuses.
        distinct = {normalize.content_key(
            m if m is not None else str(it.get("text") or ""))
            for _, _, it, m in hits}
        if len(distinct) == 1:
            _, _, target, matched = hits[0]
            # #691: a record can hold several values (evidence, note,
            # historical rows). The tombstone must key on the value the QUERY
            # named, never on an arbitrary field — rebind `text` to the
            # matched value before the hash below derives from it.
            if matched is not None and matched != target.get("text"):
                target = dict(target, text=matched)
        else:
            _note_usage("forget:no-match" if not hits else "forget:ambiguous")
            label = "no item matches" if not hits else "ambiguous — matches"
            print(f"{label} {args.target!r}; candidates:")
            # A never-guess refusal is only useful if the user can make the
            # choice: when the matched value differs from the display text,
            # show it, or two candidates separated by their verdicts render
            # as identical lines (#698 review).
            rows = hits or [(s, k, it, None) for s, k, it in candidates]
            for _, key, it, m in rows:
                line = f"  {it['id']}  [{key}] {it.get('text', '')}"
                if m is not None and m != it.get("text"):
                    line += f" — matched: {m}"
                print(line)
            print("forget by exact id: daimon forget <id>")
            return 1
    if getattr(args, "dry_run", False):
        _note_usage("forget:dry-run")
        # #698 review: forget is irreversible and this preview is the only
        # pre-deletion check. The deleters reach EVERY record whose any
        # declared field folds to the value's key — including list fields the
        # selector deliberately never offers — so the preview enumerates that
        # reach non-destructively instead of naming only the selected target.
        value_key = normalize.content_key(str(target.get("text") or ""))
        # The checkpoint splice takes every item holding the value, whatever
        # its id — and amendments die with their item BY ID, carrying prose
        # that is not the forgotten value, so both must be previewed too
        # (#698 review). Same computation as the destructive path below,
        # against the in-memory checkpoint, writing nothing — INCLUDING the
        # seed: a target superseded out of the live checkpoint (or ledger-
        # only) never appears in the walk, but its own id still reaches the
        # amendments keyed on it.
        spliced = {str(target["id"] or "")}
        if isinstance(checkpoint, dict):
            for section, key in store._ITEM_LISTS:
                for i in (checkpoint.get(section) or {}).get(key) or []:
                    if isinstance(i, dict) and (
                            i.get("id") == target["id"]
                            or normalize.content_key(i.get("text") or "")
                            == value_key):
                        spliced.add(str(i.get("id") or ""))
        spliced.discard("")
        ref_reach = sorted({str(row.get("refutation_id") or "")
                            for row in refutations.events(project_dir=project)
                            if value_key in refutations.row_content_keys(row)})
        amend_reach = sorted({
            str(row.get("amendment_id") or "")
            for row in amendments.events(project_dir=project)
            if value_key in amendments.row_content_keys(row)
            or str(row.get("item_id") or "") in spliced})
        print(f"would forget {target['id']}: {target.get('text', '')}")
        active_rulings = sorted(
            rid for rid in set(ref_reach) | {str(target["id"])}
            if ledger_meta.get(rid) == ("ruling", "active"))
        if active_rulings:
            print("WARNING — this removes ACTIVE ruling(s): "
                  + ", ".join(active_rulings))
        sibling_ids = sorted(spliced - {str(target["id"])})
        if sibling_ids:
            print("also removed — checkpoint siblings holding the value: "
                  + ", ".join(sibling_ids))
        also = [rid for rid in ref_reach + amend_reach
                if rid and rid != target["id"]]
        if also:
            print("also removed — ledger records reached by value or by "
                  "doomed item id: " + ", ".join(also))
        return 0
    # #402: key the tombstone on the CANONICAL value (normalize.content_key),
    # not the raw bytes — so a later re-extraction of the same claim (different
    # case, invisible chars, a look-alike glyph) folds to the same key and is
    # suppressed at capture. Still a hash, never the text: removal means the
    # content leaves the audit trail too (#321).
    content_hash = normalize.content_key(target.get("text") or "")
    # #693: forget takes no --by and asks no confirmation, so without this
    # gate it is an agent path that un-renders a human-ratified standing
    # constraint — the one thing no agent path may do. A human at a terminal
    # proceeds (deletion stays the user's call, #421 posture unchanged); a
    # non-interactive caller is pointed at the human.
    doomed_rulings = sorted(
        rid for rid in ({
            str(row.get("refutation_id") or "")
            for row in refutations.events(project_dir=project)
            if content_hash in refutations.row_content_keys(row)}
            | {str(target["id"])})
        if ledger_meta.get(rid) == ("ruling", "active"))
    if doomed_rulings:
        if not sys.stdin.isatty():
            print("refused: this would remove ACTIVE ruling(s) "
                  + ", ".join(doomed_rulings)
                  + " — a human decision. Run it from a terminal, or ask the "
                  "user; `daimon ruling retire` records the verdict instead.")
            return 1
        # Deleting what renders is strictly more power than rewriting it,
        # and rewriting confirms. A human can still say y (#421 unchanged).
        print("WARNING — this removes ACTIVE ruling(s): "
              + ", ".join(doomed_rulings))
        answer = input("Remove? [y/N]: ").strip().casefold()
        if answer not in ("y", "yes"):
            print("not removed")
            return 1
    sid = str((checkpoint or {}).get("session_id") or "")
    # A checkpoint-bearing project still needs its session id to rewrite. A
    # ledger-only value has no checkpoint to rewrite, so the missing id is not
    # an error there.
    if isinstance(checkpoint, dict) and not sid:
        print("checkpoint has no session_id — cannot rewrite")
        return 1
    # Tombstone BEFORE the rewrite (#418): write_checkpoint's forget gate
    # consults the ledger during the write — appended after, the new key is
    # invisible to that scrub and sibling ids carrying the same value survive.
    # Failing here leaves the checkpoint untouched: no half-removal without an
    # audit-trail record. allow_disabled (#421): forget is the ratified
    # deletion exemption to the kill switch — the tombstone (and the rewrite
    # below, which #418 chains to it) must land even while daimon is disabled.
    ok = store.append_event(target["id"], f"forgotten:{content_hash}",
                            note=args.reason or "", kind="tombstone",
                            project_dir=project, allow_disabled=True)
    if not ok:
        print("tombstone event not written (project unknown or ledger unwritable)")
        return 1
    # Splice by VALUE, not only id (#418): one value can hold sibling ids —
    # the same sentence in two sections, or a widened hash within one
    # (store._stamp_item_ids). Removal is content removal, so every item
    # folding to the tombstoned key goes. Id kept in the predicate as a belt
    # for non-string text, which content_key canonicalizes to "".
    spliced_ids = {str(target["id"] or "")}
    if isinstance(checkpoint, dict):
        for section, key in store._ITEM_LISTS:
            lst = (checkpoint.get(section) or {}).get(key)
            if isinstance(lst, list):
                doomed = [i for i in lst
                          if isinstance(i, dict)
                          and (i.get("id") == target["id"]
                               or normalize.content_key(i.get("text") or "")
                               == content_hash)]
                # #691: every spliced sibling id, not only the named target —
                # amendments are keyed by item id, and an amendment about a
                # sibling holding the same value is plaintext ABOUT the
                # forgotten content (the #418 sibling rule, extended to the
                # ledger that references the siblings).
                spliced_ids.update(str(i.get("id") or "") for i in doomed)
                lst[:] = [i for i in lst if i not in doomed]
        # allow_disabled (#421): the ONE write_checkpoint call that may run under
        # the kill switch — the rewrite that makes the deletion real on disk.
        # rotate=False: rotation copies the CURRENT latest into prev-1 before
        # writing, and the current latest is the PRE-forget bytes. Rotating here
        # made the deletion manufacture a fresh copy of the value it was asked to
        # remove, in a file that did not exist when the user ran the command.
        store.write_checkpoint(sid, checkpoint, project_dir=project,
                               allow_disabled=True, rotate=False)
    # The live checkpoint is one surface of several. prev-N and superseded
    # session files hold the same plaintext and were never in the contract
    # (#419: plaintext is what puts a file inside it, not its role). Runs even
    # with no live checkpoint: a ledger-only project still has these surfaces.
    store.scrub_content_key(content_hash, project_dir=project)
    # #600 slice A: the author's own team-mirror copies are plaintext this
    # machine owns (#419) — scrubbed here; teammates' copies and upstream
    # git history are the sync protocol's to converge (tombstone
    # propagation), not a local rewrite's.
    team_scrubbed = store.scrub_team_copies(content_hash,
                                            project_dir=project)
    # #600 slice B: publish the deletion itself (hash only) so teammates can
    # suppress the value without waiting to pull the scrubbed file — and so
    # a copy THEY extracted independently can be acted on at all.
    store.publish_tombstone(content_hash, project_dir=project)
    # #599: rows appended BEFORE this forget can carry the value in
    # `item_text`/`status`/`note` — redacted in place, rows never dropped
    # (the one ratified rewrite of the append-only ledger).
    events_scrubbed = store.scrub_event_fields(content_hash,
                                               project_dir=project)
    # #578: same value, second plaintext store. The ledger splices on the SAME
    # canonical key for the same reason the checkpoint splices on it rather than
    # on the id — removal is content removal, so a refutation asserting the
    # forgotten value goes with it whether or not it was the named target.
    forgotten_refutations = refutations.forget_content_key(
        content_hash, project_dir=project)
    # #678 fork A: relation rows hold no text, but an edge touching this item
    # is an equivalence CLAIM about its content (an `exact-text` rail against
    # a surviving twin re-derives what the value was), and post-forget the
    # relations ledger would be the only surface binding the forgotten id to
    # its sessions, kind, and revision-chain length. The scrub is id-keyed —
    # the tombstone above landed on this exact id — and the audit's
    # relations-ledger scan is what proves it reached the edges.
    forgotten_relations = relations.forget_item_id(
        target["id"], project_dir=project)
    # #691: same value, another plaintext store — and unlike relations, amend
    # rows DO carry prose, so records targeting a forgotten item (or any of
    # its spliced siblings) go with it — their evidence may paraphrase the
    # removed content — and records holding the value in any plaintext field
    # go regardless of target.
    spliced_ids.discard("")
    forgotten_amendment_set = set(
        amendments.forget_content_key(content_hash, project_dir=project))
    for doomed_id in sorted(spliced_ids):
        forgotten_amendment_set.update(
            amendments.forget_item_id(doomed_id, project_dir=project))
    forgotten_amendments = sorted(forgotten_amendment_set)
    # #422: the serializer chunk cache holds PRE-redaction extraction output
    # (quote verification forbids redacting before caching, #125), keyed by
    # chunk text — the forgotten value cannot be located selectively, so the
    # purge is WHOLESALE and default-on. Never fatal: the belief-state
    # deletion above is the primary contract; a failed purge is reported
    # honestly below, and the age reaper still bounds any survivor at
    # chunk_cache_days.
    try:
        purged, purge_err = serializer.purge_chunk_cache()
    except Exception as e:  # belt: purge_chunk_cache itself never raises
        purged, purge_err = 0, str(e)
    # #607: the Windsurf adapter writes its own transcripts when Cascade
    # gives it none — daimon-authored plaintext, so inside the contract.
    # Wholesale for the same reason as the chunk cache: the tombstone is a
    # hash, so a value inside prose cannot be located to remove selectively.
    try:
        ws_purged, ws_err = store.purge_windsurf_state()
    except Exception as e:  # belt: purge_windsurf_state never raises
        ws_purged, ws_err = 0, str(e)
    # #605: serialize-crash.log is the detached child's RAW stderr — an
    # uncaught traceback carries whatever the crashing frame held, and the
    # bytes were never scrubbed (status redacted its tail on READ, #513, over
    # a file nothing deleted). Wholesale like the two purges above: the
    # tombstone is a hash, so a value inside a traceback cannot be located.
    try:
        crash_purged, crash_err = store.purge_crash_log()
    except Exception as e:  # belt: purge_crash_log never raises
        crash_purged, crash_err = 0, str(e)
    # #616: backend-stderr.log holds backend stderr/stdout, which CLI
    # backends can seed with transcript text (#141). Wholesale like the
    # crash sink: prose diagnostics, hash tombstone, value unlocatable.
    try:
        backend_purged, backend_err = store.purge_backend_stderr_log()
    except Exception as e:  # belt: purge_backend_stderr_log never raises
        backend_purged, backend_err = 0, str(e)
    # #616: pre-fix downgrade lines logged item text into serialize.log.
    # Shape-targeted scrub, NOT a purge — serialize.log is also the ledger
    # `status` parses, and the capture record must survive a forget.
    try:
        scrubbed_lines, scrub_err = store.scrub_serialize_log()
    except Exception as e:  # belt: scrub_serialize_log never raises
        scrubbed_lines, scrub_err = 0, str(e)
    _note_usage("forget")
    surfaces = []
    if isinstance(checkpoint, dict):
        surfaces.append("the live checkpoint")
    if forgotten_refutations:
        gone_rulings = [r for r in forgotten_refutations
                        if ledger_meta.get(r, ("refutation", None))[0]
                        == "ruling"]
        gone_refuts = [r for r in forgotten_refutations
                       if r not in gone_rulings]
        if gone_refuts:
            surfaces.append(f"{len(gone_refuts)} refutation(s) "
                            f"({', '.join(gone_refuts)})")
        if gone_rulings:
            surfaces.append(f"{len(gone_rulings)} ruling(s) "
                            f"({', '.join(gone_rulings)})")
    if forgotten_relations:
        surfaces.append(f"{len(forgotten_relations)} relation(s) "
                        f"({', '.join(forgotten_relations)})")
    if forgotten_amendments:
        surfaces.append(f"{len(forgotten_amendments)} amendment(s) "
                        f"({', '.join(forgotten_amendments)})")
    print(f"forgot {target['id']} (content hash {content_hash}) — "
          f"removed from {' and '.join(surfaces) or 'no store'}; "
          "tombstone recorded")
    if team_scrubbed:
        print(f"scrubbed {len(team_scrubbed)} own team-mirror cop(y/ies) — "
              "run `daimon team sync` to publish; teammates' copies and "
              "upstream git history remain until tombstone propagation")
    if events_scrubbed:
        print(f"redacted {events_scrubbed} event-ledger field(s) "
              "carrying the value (rows kept, field replaced)")
    if purge_err is not None:
        print(f"warning: chunk cache purge failed: {purge_err} — "
              "cached pre-redaction chunks may persist up to "
              f"{config.chunk_cache_days()} day(s) (age reaper)")
    else:
        print(f"purged {purged} cached chunk extraction(s) "
              "(pre-redaction serializer cache)")
    if ws_err is not None:
        print(f"warning: windsurf transcript purge failed: {ws_err} — "
              "daimon-authored conversation text may persist up to "
              f"{config.windsurf_state_days()} day(s) (age reaper)")
    else:
        # Always printed, like the chunk-cache line: a silent zero is how a
        # misconfigured store (writer and deleter disagreeing about the
        # directory) stays invisible. The scope note is not decoration —
        # the store is keyed by trajectory and carries no project
        # attribution, so this purge is machine-wide by construction.
        print(f"purged {ws_purged} daimon-authored windsurf transcript "
              "file(s) across all projects (machine-wide: the store is keyed "
              "by trajectory, not project); host-authored transcripts are "
              "untouched")
    if crash_err is not None:
        print(f"warning: crash log purge failed: {crash_err} — serializer "
              "tracebacks may persist (bounded to a trimmed tail at the next "
              "spawn, never removed)")
    else:
        # Printed even at zero, like the two lines above: a silent zero is
        # how a writer and a deleter disagreeing about DAIMON_LOG_DIR stays
        # invisible. One crash log per machine, so the scope note is the
        # same honesty the windsurf line owes.
        print(f"purged {crash_purged} serializer crash log(s) across all "
              "projects (machine-wide: the log is raw child stderr, not "
              "keyed by project)")
    if backend_err is not None:
        print(f"warning: backend log purge failed: {backend_err} — backend "
              "echo of transcript text may persist (byte-bounded at the "
              "write seam, never removed)")
    else:
        # Same silent-zero rule as the three lines above.
        print(f"purged {backend_purged} backend stderr log(s) across all "
              "projects (machine-wide: backend diagnostics are not keyed "
              "by project)")
    if scrub_err is not None:
        print(f"warning: serialize.log scrub failed: {scrub_err} — legacy "
              "downgrade lines may still carry item text")
    else:
        # Printed even at zero, same rule again: a scrubber resolving a
        # different log dir than the writer must not read as "nothing left".
        print(f"scrubbed {scrubbed_lines} legacy downgrade line(s) in "
              "serialize.log (payload replaced; ledger lines kept)")
    return 0


def _is_supersede_candidate(item_id: str, project) -> bool:
    """True when the item's LATEST lifecycle event is a serializer-authored
    supersede-candidate — an unconfirmed machine SUGGESTION (#14) that has
    never withheld anything. This is the one reopen target that needs no
    evidence: rejecting a guess is not vouching for a claim (#111). The
    source gate mirrors `_emit_supersede_candidates` — only the serializer
    ever writes candidates, so a human verdict already latest reads as
    not-a-candidate and keeps the evidence gate. Fails closed: a broken
    events fold means 'not a candidate', so the evidence gate still applies."""
    try:
        prior = store.resolutions(project_dir=project).get(item_id)
    except Exception:
        return False
    if not isinstance(prior, dict):
        return False
    status = str(prior.get("status") or "").lower()
    source = str(prior.get("source") or "")
    return status.startswith("supersede-candidate") and source == "serializer"


def _cmd_reverify(args) -> int:
    """Evidence-gated reopen (#103): re-stamping a resolved item without
    evidence would mark an unchecked claim verified — the one thing this
    tool must never do to its own audit trail. Reopen is allowed only when
    the original anchor still checks out live (the code proved itself) or
    the caller supplies --evidence (a human vouches for it); otherwise the
    refusal appends nothing. Exact id only — no fuzzy match, because
    reopening is a deliberate act; find ids via `daimon status --suppressed`.

    #111 exception: when the target is an unconfirmed supersede CANDIDATE
    (a machine guess that has never withheld anything), reopen is allowed
    with no evidence — rejecting a suggestion is not vouching for a claim.
    The reopened event is a human verdict, so re-detection stays silent."""
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
    elif _is_supersede_candidate(item["id"], project):
        # #111: the target is an unconfirmed machine SUGGESTION, never a
        # withheld/suppressed item — rejecting a guess needs no proof. The
        # reopened event below is a human verdict, so the human-speaks-once
        # gate (#14) silences re-detection of this candidate permanently.
        note = "candidate rejected"
    else:
        print("re-stamping without evidence would mark an unchecked claim "
              "verified — supply --evidence, or fix the anchored code and retry")
        return 1
    ok = store.append_event(item["id"], "reopened", note=note,
                            item_text=item.get("text", ""), project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    # A typed baseline for this verb. Without it reverify's count is
    # structurally zero, and a zero that measures missing instrumentation
    # cannot be compared against a UI channel's count later.
    _note_usage("reverify")
    print(f"reopened {item['id']}: {item.get('text', '')}")
    return 0


def _cmd_loops(args) -> int:
    """`daimon loops` (#480 slice 1): list open, briefable loop items with
    ids for this project — the read-only counterpart to the id handles
    briefing._line now renders inline (an agent, or a human, needs something
    to pass to `daimon resolve`).

    Reuses the SAME item walk `_cmd_resolve` builds (store._ITEM_LISTS over
    the latest checkpoint) and the SAME withhold classification `daimon
    status --suppressed` uses (briefing.withhold over store.resolutions) —
    the resolved/live split must stay in exactly one place, or this listing
    could show an item the briefing itself would withhold (an agent would
    then "resolve" a ghost).

    Briefable = briefing.BRIEFABLE_ITEM_KEYS (open_questions — external and
    non-external both — plus uncertainties). Decisions/beliefs/
    contradictions are valid `daimon resolve` targets too, but are not
    loop-shaped; listing them here would invite resolving settled facts
    (#480 scope guard, mirrors briefing._line's new suffix)."""
    _note_usage("loops")
    project = _resolve_project(args.project)
    checkpoint = store.read_latest(project_dir=project, fallback=False)
    if not isinstance(checkpoint, dict):
        print("no checkpoint for this project yet — nothing to list")
        return 0
    try:
        events = store.resolutions(project_dir=project)
        # #691: amendments ride along so the listing agrees with the
        # briefing — an agent discovering targets here must see that an
        # item is already amended, or its cheapest probe is a duplicate
        # proposal.
        checkpoint, _, _ = briefing.withhold(
            checkpoint, events,
            amendments=amendments.renderable(project_dir=project))
    except Exception:
        pass  # fail-open, same stance as _print_suppressed
    rows = []
    for section, key in store._ITEM_LISTS:
        if key not in briefing.BRIEFABLE_ITEM_KEYS:
            continue
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            if item.get("_agent_claim"):
                # #480 slice 4: the listing agrees with the briefing — an
                # item carrying a still-pending, unverified agent claim
                # (briefing.withhold's transient stamp) is marked here too.
                text += " (agent claim pending)"
            amend_stamp = item.get("_amend")
            if isinstance(amend_stamp, dict):
                n = (len(amend_stamp.get("rows") or [])
                     + (amend_stamp.get("overflow") or 0))
                if n:
                    text += f" (amended ×{n})"
            rows.append((item["id"], key, text, briefing._mark(item)))
    if not rows:
        print("no open loops")
        return 0
    for item_id, key, text, mark in rows:
        print(f"  {item_id}  [{key}] [{mark}] {text}")
    return 0


# #523: a baton is small on purpose — "do X first, beware Y", not a second
# checkpoint. Over-cap input is REFUSED, never silently truncated: it is an
# authored artifact and the author trims it.
_HANDOFF_MAX_CHARS = 2000


def _cmd_handoff(args) -> int:
    """Leave (or retract) the project's baton (#523). Ref-less by contract —
    scar 0025: an event kind carrying an item_ref silently resolves that
    item, so a handoff must never name one. The resolutions fold ignores
    ref-less lines (guarded by test_handoff_event_never_resolves_an_item)."""
    _note_usage("handoff")
    project = _resolve_project(args.project)
    if args.clear:
        if args.text:
            print("error: --clear takes no text", file=sys.stderr)
            return 1
        if not store.append_event("", "cleared", note="", kind="handoff",
                                  project_dir=project):
            print("error: handoff not recorded (daimon disabled or project "
                  "unknown)", file=sys.stderr)
            return 1
        print("handoff cleared")
        return 0
    text = (args.text or "").strip()
    if not text:
        print("error: nothing to hand off — pass the baton text or --clear",
              file=sys.stderr)
        return 1
    if len(text) > _HANDOFF_MAX_CHARS:
        print(f"error: baton exceeds {_HANDOFF_MAX_CHARS} chars "
              f"({len(text)}) — a handoff is \"do X first, beware Y\", not a "
              "second checkpoint; trim it", file=sys.stderr)
        return 1
    # #571: latest-wins stays the contract, but replacing a baton no session
    # has consumed yet must not be silent — the superseded text never
    # surfaces again (ref-less events sit outside ranking/recall/carry).
    # active_handoff already encodes "unconsumed" (None after two distinct
    # non-introspection serializes) and is fail-open, so a broken read warns
    # about nothing rather than blocking the write.
    prior = store.active_handoff(project)
    if prior:
        print("warning: superseding an unconsumed baton — its text below "
              "never surfaces again; fold anything still relevant into the "
              f"new baton:\n  {prior['note']}", file=sys.stderr)
    if not store.append_event("", "active", note=text, kind="handoff",
                              project_dir=project):
        print("error: handoff not recorded (daimon disabled or project "
              "unknown)", file=sys.stderr)
        return 1
    print("handoff recorded — will lead the next briefing for this project.")
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

_INJECT_BUDGET = 2   # slots per prompt (#125 noise budget)
_INJECT_FETCH = 8    # candidates asked of `suggest`, i.e. budget + headroom:
                     # content dedup below must be able to PROMOTE the next
                     # distinct candidate, and it can only promote from
                     # candidates it was given (#451)

# #452: age dominates injection precision. Measured per-slot relevance by item
# age on the maintainer's corpus: <=1d = 78%, 2-7d ~ 24%, >7d = 6-10% — a
# week-old item riding an ordinary 2-term match is near-certain noise, so past
# the knee it must EARN its slot with a substantially stronger match. The
# thresholds are the measured knee and one term above suggest's session floor
# (_MIN_OVERLAP = 2): 3 distinct hits on one ITEM is specific prior work, not
# vocabulary coincidence.
_AGE_GATE_DAYS = 7   # past this, a candidate needs _STALE_MIN_HITS
_STALE_MIN_HITS = 3  # distinct salient-term hits that buy a stale slot back


def _inject_age_bucket(age_days: float | None) -> str:
    """Stats bucket for a CHOSEN candidate's age — the bands of #452's
    measured relevance table, so the before/after read by age is a `daimon
    stats` query. `unknown` = no usable first_seen stamp."""
    if age_days is None:
        return "unknown"
    if age_days <= 1:
        return "<=1d"
    if age_days <= 3:
        return "2-3d"
    if age_days <= 7:
        return "4-7d"
    if age_days <= 14:
        return "8-14d"
    return ">14d"


def _seen_path(session: str):
    """Cooldown-state file for one session, or None when the id is unusable
    (empty, or path-hostile — the id becomes a filename). Origin ids are not
    all uuids: a Codex session id is `rollout-<timestamp>-<hex>`, which is a
    fine filename and must keep working."""
    if not session or "/" in session or "\\" in session or ".." in session:
        return None
    return config.recall_seen_dir() / f"{session}.json"


# Sentinel for "argument not supplied", where None is itself a meaningful
# value: an unknown age (never gate) and a disabled origin cooldown both use
# None deliberately, so neither can double as "use the default".
_UNSET = object()

# Rows ONE prior session may supply across a host session (not per prompt —
# _INJECT_BUDGET already caps that at 2). Was effectively 1: injecting a row
# retired its whole origin.
#
# Measured on the replay corpus, the rows that ban was suppressing are the
# GOOD ones: lifting it to 3 restores 138 injections of which 40 are <=1d and
# only 3 are older than a week — an age mix whose calibrated relevance is ~39%
# against a ~20% baseline. The ban was spending its suppression on the freshest
# material in the store.
#
# 3 is a judgement bounded by evidence, not a measured optimum: 2 and "no cap"
# are both defensible (they recover 116 and 160 rows at 37% and 43%). The cap
# exists for the concern the ban encoded — one dense prior session must not
# crowd out everything else — and unlimited lets a single origin supply 7 rows
# in one working session. Crowding harm itself is UNMEASURED, so the cap is
# deliberately conservative rather than tuned.
_ORIGIN_BUDGET = 3


def cooled_origins(origin_counts: dict, budget=_UNSET) -> set:
    """Origins that have spent their budget and must not be offered again.

    ONE definition, called by the injection path and by the replay harness —
    the same reason #491 extracted the age gate. A hand-copied cooldown drifts,
    and a harness enforcing last week's cooldown reports confident numbers
    about a system that no longer exists.

    #500: this used to be a session-wide BAN. Injecting a single row retired
    its whole origin, so a decision about one row silently cost every other row
    that session could supply — and any change upstream reshuffled which
    sessions got burned. Measured while shipping #491, 12 injections <=7d old
    vanished that way, including a 1-day-old exact-match decision that appeared
    nowhere else in the run. The age gate cannot have touched them.

    A budget keeps the intent the ban encoded — one dense prior session must
    not crowd out everything else — at the granularity the decision is actually
    made at. `budget=None` disables origin cooldown entirely (the measurement
    arm); `budget=1` is the pre-#500 ban, kept exactly reachable so the change
    is provably behaviour-preserving at the old default.
    """
    # Resolved at CALL time, not bound as a def-time default: a default
    # argument freezes the module constant at import and silently ignores any
    # later change, which makes the knob untunable and every sweep over it a
    # measurement of the same arm.
    if budget is _UNSET:
        budget = _ORIGIN_BUDGET
    if budget is None:
        return set()
    return {sid for sid, n in origin_counts.items() if n >= budget}


def _load_seen(path) -> tuple[dict, set]:
    """(origin session id -> injected count, content keys) for this host
    session.

    Three on-disk shapes are read. The pre-#451 file is a flat JSON list of
    origin ids; the pre-#500 file is {"origins": [...], "content_keys": [...]}.
    Neither records a per-origin COUNT, so both load as EXHAUSTED (budget
    reached) rather than as one: guessing low would hand an in-flight session
    extra slots it may already have spent, and an upgrade must never loosen
    suppression a session already earned. The current shape carries
    {"origins": {sid: count}, ...}.

    Anything else — corrupt, truncated, a bare scalar — is state we cannot
    trust, and cooldown is best-effort: fall open and pay one extra
    suggestion."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return {str(s): _ORIGIN_BUDGET for s in raw}, set()
        origins = raw["origins"]
        keys = {str(k) for k in raw["content_keys"]}
        if isinstance(origins, dict):
            return ({str(s): int(n) for s, n in origins.items()}, keys)
        return ({str(s): _ORIGIN_BUDGET for s in origins}, keys)
    except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        return {}, set()


def _save_seen(path, origin_counts: dict, content_keys: set) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"origins": {s: origin_counts[s] for s in sorted(origin_counts)},
             "content_keys": sorted(content_keys)}), encoding="utf-8")
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


def _row_age_days(m, now: float):
    """Age of a suggest() row in days, or None when the stamp is missing,
    malformed, or in the future. Same parser and same tolerance philosophy
    scoring trusts (store._created_epoch): unknown age is never evidence of
    staleness, so it fails toward suggesting (#450 direction)."""
    epoch = store._created_epoch(m.get("first_seen"))
    return ((now - epoch) / 86400.0
            if epoch is not None and epoch <= now else None)


def age_gate_blocks(m, now: float, age_days=_UNSET) -> bool:
    """Does #452's age gate silence this candidate?

    ONE definition, called by the injection path and by the replay harness's
    post-filter replica. It used to be duplicated, and a duplicated gate drifts
    the moment either side changes — a harness measuring last week's policy
    reports confident numbers about a system that no longer exists.

    #491 removed the question half of the exemption. It read "no logged
    resolution" as "still open", and the premise was measured false: 1280 of
    1343 question rows (95.3%) carry no resolution, so survival is the DEFAULT,
    not evidence. Blind-graded, the injections it admitted came back 3/30 = 10%
    relevant (95% CI [3.5%, 25.6%]) — inside the 6-10% band this gate already
    blocks, which is the whole argument. On the replay corpus it admitted 68 of
    340 injections, every one a question and not one of them pinned.

    Two narrower liveness signals were measured first and BOTH separate the
    wrong way, which is why this is a removal rather than a refinement:

      * carry depth: exempt-admitted rows are MORE carried than the ones
        earning their slot honestly (33% appear in a single checkpoint vs
        54%), so being re-asserted does not predict being useful.
      * frontier-by-content (is this item's text still in the newest
        checkpoint at prompt time): 1 of 68 exempt vs 15 of 272 earned. It
        would gate 67 of 68 — indistinguishable from removal, and pointing the
        same wrong way. NOTE it is not readable here anyway: `suggest`'s SELECT
        does not carry `frontier` (only `search`'s does), so that measurement
        described a hypothetical column, never shipped behaviour.

    Questions are not banned — a stale question matching _STALE_MIN_HITS
    distinct terms still injects, like any other stale row. It just stops being
    waved through on age alone. Pinned survives untouched: a standing rule is
    age-independent by construction and is a small, curated set — though note
    the pinned branch fires zero times on the replay corpus, so it rests on
    #452's original observation, not on anything #491 measured.

    Cost, recorded because the instrument cannot see it: this is purely
    subtractive at the prompt level. 38 of 189 prompts that previously carried
    an injection now carry none, and NO prompt gains one. Precision of what is
    injected is measurable; the value of what is now withheld is not.
    """
    if age_days is _UNSET:
        age_days = _row_age_days(m, now)
    if age_days is None or age_days <= _AGE_GATE_DAYS:
        return False
    if m.get("pinned"):
        return False
    hits = m.get("term_hits")
    # isinstance, not `or 0`: a row with no term_hits offers no match-strength
    # evidence, and absent evidence never gates (same fail-open direction as
    # unknown age).
    return isinstance(hits, int) and hits < _STALE_MIN_HITS


def _suggest_line(r: dict, terms, now: float) -> str:
    """One compact, attributed, trust-preserving injection line (#125).

    ONE line is a contract, not a hope (#512): the echo strip that removes
    this line from the verification haystack is line-scoped, so item text
    carrying a newline would leave its tail behind as a fake witness.
    Internal whitespace collapses here, at the emitter."""
    age = _format_age(now - r["created"]) if r.get("created") else "?"
    trust = r.get("trust") or "untagged"
    text = " ".join(str(r["text"]).split())
    text = text if len(text) <= 160 else text[:157] + "..."
    # v3 (#234): the flag is item-level evidence — a typed supersedes link
    # or a logged resolution — not the old whole-checkpoint recency.
    sup = r.get("superseded_by")
    superseded = ("" if not sup
                  else " (resolved)" if sup == "resolved"
                  else " (superseded by later work)")
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
        # #450: host-emitted blocks (task notifications, teammate/agent
        # messages, command output) arrive here as prompts but are nobody
        # asking for anything — 37.9% of measured injections landed on them.
        # Its own try: a classifier failure must cost nothing, so it falls back
        # to today's behavior (suggest) rather than to the outer silent return.
        try:
            machine = recall.is_machine_prompt(prompt)
        except Exception:  # noqa: BLE001 — fail toward suggesting, never skip on a bug
            machine = False
        if machine:
            # Counted apart from `recall-inject`, which still counts every fire:
            # the pair is the before/after measure of the noise removed (#450).
            _note_usage("recall-inject:skip-machine")
            return 0
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
        origin_counts, seen_keys = (_load_seen(seen_file) if seen_file
                                    else ({}, set()))
        matches = recall.suggest(prompt, project_dir=project,
                                 current_session=session,
                                 exclude_sessions=(
                                     exclude | cooled_origins(origin_counts)),
                                 limit=_INJECT_FETCH)
        # #451: an origin id is not a content identity. The same claim carried
        # by two checkpoints (sibling-id copies — the read-side twin of the
        # value-keyed forget arc, #424/#435) passes the origin cooldown and
        # re-injects as if it were new: 15.5% of measured injections repeated
        # text the session had already seen, every repeated group cross-origin.
        # So the budget is spent on distinct content keys, within one injection
        # AND across the session, and a suppressed candidate yields its slot to
        # the next distinct one instead of shrinking the injection.
        now = time.time()
        chosen: list[dict] = []
        chosen_keys: set[str] = set()
        suppressed = False
        age_gated = False
        for m in matches:
            key = normalize.content_key(m.get("text") or "")
            if key in seen_keys or key in chosen_keys:
                suppressed = True
                continue
            # #452: stale items must show a stronger match. Age comes from the
            # row's first_seen through the same parser scoring trusts
            # (store._created_epoch) with the same tolerance philosophy:
            # missing, malformed, or future stamps mean age UNKNOWN, and
            # unknown is never gated — a missing stamp is not evidence of
            # staleness (fail toward suggesting, the #450 direction). Like the
            # #451 dedup, a gated candidate is a `continue`, so its slot
            # promotes the next one.
            # Age is computed ONCE and shared with the stats bucket below:
            # if the gate and the #452 re-measurement ever read different
            # clocks, the counters stop describing the gate that produced
            # them — the same duplication this predicate exists to remove.
            age_days = _row_age_days(m, now)
            if age_gate_blocks(m, now, age_days=age_days):
                age_gated = True
                continue
            chosen_keys.add(key)
            chosen.append(m)
            # #452 re-measurement: every CHOSEN row records its age bucket, so
            # the before/after precision read by age stays a stats query.
            _note_usage(f"recall-inject:age:{_inject_age_bucket(age_days)}")
            if len(chosen) >= _INJECT_BUDGET:
                break
        if suppressed:
            # Counted apart from `recall-inject`, which still counts every fire:
            # the issue's claim is a RATE, so the pair has to be readable from
            # `daimon stats` the way #450's machine skip is.
            _note_usage("recall-inject:dedup-content")
        if age_gated:
            # Same convention as dedup-content above: once per injection run
            # where >=1 candidate was age-gated (#452) — a rate, not a tally.
            _note_usage("recall-inject:age-gate")
        if not chosen:
            return 0
        terms = recall.salient_terms(prompt)
        for m in chosen:
            print(_suggest_line(m, terms, now))
        if seen_file:
            # #500: count what each origin supplied instead of retiring it
            # outright, so a later, stronger row from the same session stays
            # reachable until that session has had its share.
            spent = dict(origin_counts)
            for m in chosen:
                sid = str(m["session_id"])
                spent[sid] = spent.get(sid, 0) + 1
            _save_seen(seen_file, spent, seen_keys | chosen_keys)
    except Exception:  # noqa: BLE001 — see docstring: fail-open, always rc 0
        pass
    return 0


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
            # #111: both a confirm and a reject path — a human who disagrees
            # with the guess must not have to reach for evidence machinery.
            print(f"    confirm: daimon resolve {item_id} --status superseded-by:{new_id}")
            print(f"    reject: daimon reverify {item_id}")
    return 0


def _cmd_verify_receipt(args) -> int:
    """Verify a checkpoint's signed provenance receipt (#204). Default target is
    the current project's latest checkpoint; a session id can be passed
    explicitly. rc 0 verified / 1 failed / 2 unable (see receipts.verify_receipt)."""
    _note_usage("verify-receipt")
    session_id = getattr(args, "session_id", None)
    if not session_id:
        project = _resolve_project(args.project)
        checkpoint = store.read_latest(project_dir=project)
        if not isinstance(checkpoint, dict) or not checkpoint.get("session_id"):
            print("no checkpoint for this project yet — nothing to verify")
            return 2
        session_id = checkpoint["session_id"]
    rc, lines = receipts.verify_receipt(str(session_id))
    for line in lines:
        print(line)
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
    cutoff_epoch = now - _RETENTION_WINDOW_DAYS * 86400
    cutoff_dt = datetime.fromtimestamp(cutoff_epoch, tz=timezone.utc)
    spawns = _spawns_in_window_count(cutoff_dt)
    if spawns < _CAPTURE_MIN_SESSIONS:
        return None
    checkpoints = store.checkpoints_written_since(cutoff_epoch)
    if checkpoints > 0:
        return None
    return {"verdict": "fail", "spawns": spawns, "checkpoints": checkpoints,
            "window_days": _RETENTION_WINDOW_DAYS}


def _status_world(project_arg=None) -> dict:
    """Every status fact, computed once — the single source for the plain
    render, `status --json`, and the MCP status tool (#261)."""
    project = _resolve_project(project_arg)
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
    hook_drift = _hook_drift_present()
    # #554: the same pointer for the one host `hooks status` cannot audit —
    # Claude Code's hooks ship inside the plugin, which updates on its own
    # schedule. None on a machine with no plugin, so non-plugin users stay
    # silent.
    plugin_drift = _plugin_drift_present()
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
    health = _status_health(proj, glob, outstanding, siblings, now=now,
                            disabled=disabled)
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
    identity = {
        "cwd": str(Path(project_arg or ".").expanduser().resolve()),
        "git_root": project,
        "slug": store.project_slug(project),
    }
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
        "rescue_gap": rescue_gap,
        "rescue_posture": rescue_posture, "rescue_window_errors": rescue_window_errors,
        "forget_hits": forget_hits, "rc": rc,
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
        "rescue_gap": w["rescue_gap"],
        "rescue_posture": w["rescue_posture"],
        "forget_hits": w["forget_hits"],
    }
    return payload, w["rc"]


def _cmd_status(args) -> int:
    _note_usage("status")
    if getattr(args, "suppressed", False):
        return _print_suppressed(_resolve_project(args.project))
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
        "rescue_gap": w["rescue_gap"],
        "rescue_posture": w["rescue_posture"],
        "rescue_window_errors": w["rescue_window_errors"],
        "forget_hits": w["forget_hits"],
    })
    return w["rc"]


def _cmd_mcp_serve(args) -> int:
    """#261: blocking stdio MCP server. No usage note here — each tool call
    notes `mcp:<tool>` itself; serving is not reading."""
    from . import mcp_server
    return mcp_server.serve()


def _load_audit_transcript(tpath):
    """Parse one transcript file into the (haystack, texts_by_id) pair
    audit-quotes checks quotes against — the one place #503's per-session
    cache calls into transcript.from_file, so it is charged once per session
    no matter how many items or checkpoints resolve to it."""
    msgs = transcript.from_file(tpath)
    # #440: the same stripped haystacks verify_quotes used at serialize time.
    # Audit re-blesses stored items, so reading the raw render here would
    # certify exactly the echo verification rejected — and do it with the
    # CLI's authority. #512: daimon-output tool rows blank here too, for the
    # same reason and by the same rule.
    haystack = serializer.stripped_transcript(msgs)
    daimon_ids = serializer.daimon_output_ids(msgs)
    texts_by_id = {
        mid: "" if mid in daimon_ids else serializer.strip_injected(text)
        for mid, text in serializer.message_texts_by_id(msgs).items()}
    return haystack, texts_by_id


def _legacy_audit_source(session_id, author=None):
    """Explicitly inferred pre-#594 Claude candidate, never a bound receipt."""
    if not provenance.valid_session_id(session_id):
        return None
    source = {
        "version": provenance.SOURCE_REF_VERSION,
        "host": "claude-code",
        "session_id": session_id,
        "locator": "managed",
    }
    if isinstance(author, str) and author.strip():
        source["author"] = author.strip()
    return source


def _audit_item_source(item):
    """Return (source, receipt) without containing-checkpoint fallback."""
    receipt = item.get("quote_provenance")
    if provenance.valid_quote_receipt(receipt):
        return receipt["source"], receipt
    origin = item.get("origin_session")
    if not provenance.valid_session_id(origin):
        return None, None
    origin_cp = store.read_checkpoint(origin)
    if isinstance(origin_cp, dict):
        source = origin_cp.get("source_ref")
        if provenance.valid_source_ref(source):
            return source, None
    return _legacy_audit_source(origin, item.get("origin_author")), None


def _resolve_audit_source(source, resolver, cache: dict):
    """Resolve and parse one strict source once per complete source identity."""
    if not provenance.valid_source_ref(source):
        return None
    key = json.dumps(source, sort_keys=True, separators=(",", ":"))
    if key in cache:
        return cache[key]
    result = None
    resolved = resolver.resolve(source)
    if resolved.state == "resolved" and resolved.path is not None:
        try:
            result = _load_audit_transcript(resolved.path)
        except (OSError, FileNotFoundError):
            result = None
    cache[key] = result
    return result


def _cmd_audit_privacy(args) -> int:
    """Read-only tombstone residue audit — proves forget's contract instead
    of trusting it (#583: a passing test once asserted the residue). Exit 0
    proven clean / 1 residue / 3 cannot-prove; 3 exists because "could not
    check" must never look like "all clean"."""
    if getattr(args, "all_projects", False):
        results = privacy.audit_all()
    else:
        results = [privacy.audit_project(_resolve_project(args.project))]
    render.render_privacy_audit(results)
    code = privacy.exit_code(results)
    # One tag per OUTCOME: "the auditor ran" and "the auditor found residue"
    # answer different questions, and folding them loses the only number that
    # says whether the deletion contract holds in the field.
    _note_usage({1: "audit-privacy:residue",
                 3: "audit-privacy:unproven"}.get(code, "audit-privacy"))
    return code


def _cmd_audit_quotes_deprecated(args) -> int:
    print("note: 'daimon audit-quotes' is deprecated — use 'daimon audit quotes'")
    return _cmd_audit_quotes(args)


def _cmd_audit_quotes(args) -> int:
    """Read-only audit (#125): re-check every stored verbatim quote against its
    source transcript with the SAME tier-f matcher serialize uses, and REPORT.

    #594: a valid item receipt is authoritative for source identity and message
    binding. Legacy origin_session may form an explicitly inferred Claude
    candidate, but an absent/unresolved source NEVER falls back to the
    containing checkpoint: exact-text carry proves origin, quote evidence, and
    containing session can be three different facts.

    Never rewrites a trust tag — a blind backfill would flip a large share of
    the historical corpus. Measured (#503): the overwhelming majority of
    failures on an unpatched checker were exactly this resolution bug, not
    quotes crossing content the current renderer no longer emits — that class
    is a small minority. Default scope is the current project; --all spans
    the whole corpus."""
    project = _resolve_project(args.project)
    want_slug = store.project_slug(project)
    d = config.checkpoint_dir()
    try:
        files = store._session_files(d)
    except OSError:
        files = []
    scanned = paired = unpaired = items = verified = failed = id_resolved = 0
    origin_resolved = 0
    transcripts: dict = {}
    resolver = provenance.SourceResolver(
        claude_projects=config.claude_projects_dir(),
        current_author=config.author())
    failures: list[tuple[str, str]] = []
    for f in sorted(files):
        try:
            cp = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(cp, dict):
            continue
        slug = cp.get("project_slug")
        if not args.all and want_slug is not None and slug != want_slug:
            continue
        scanned += 1
        session_id = str(cp.get("session_id") or f.stem)
        own_source = cp.get("source_ref")
        if not provenance.valid_source_ref(own_source):
            own_source = _legacy_audit_source(session_id, cp.get("author"))
        own = _resolve_audit_source(own_source, resolver, transcripts)
        if own is not None:
            paired += 1
        else:
            unpaired += 1
        for item in serializer.iter_items(cp):
            if item.get("trust") != "verbatim":
                continue
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                continue
            source, receipt = _audit_item_source(item)
            resolved = _resolve_audit_source(source, resolver, transcripts)
            if resolved is None:
                continue
            haystack, texts_by_id = resolved
            items += 1
            if source.get("session_id") != session_id:
                origin_resolved += 1
            # #358: an item bound to source message id(s) resolves the id and
            # compares bytes against just that message. Missing/invalid ids
            # (old checkpoints, moved/truncated transcripts) fall back to the
            # whole-transcript scan — the pre-#358 verdict, byte-identical.
            checked_item = item
            if receipt is not None:
                checked_item = dict(item)
                ids = provenance.binding_message_ids(receipt)
                if ids:
                    checked_item[serializer.SOURCE_IDS_KEY] = ids
                else:
                    checked_item.pop(serializer.SOURCE_IDS_KEY, None)
            scoped = serializer.scoped_haystack(checked_item, texts_by_id)
            if scoped is not None and serializer.quote_matches(quote, scoped):
                verified += 1
                id_resolved += 1
            elif serializer.quote_matches(quote, haystack):
                verified += 1
            else:
                failed += 1
                failures.append((session_id, str(item.get("text") or "")))
    rate = (verified / items) if items else 0.0
    scope = "all projects" if args.all else project
    lines = [
        f"audit-quotes ({scope})",
        f"  checkpoints scanned: {scanned}  paired: {paired}  unpaired: {unpaired}",
        f"  verbatim quotes checked: {items}  verified: {verified}  "
        f"failed: {failed}  id-resolved: {id_resolved}  "
        f"origin-resolved: {origin_resolved}  rate: {rate:.1%}",
    ]
    if failures:
        top = max(0, args.top)
        lines.append(f"  top {min(top, len(failures))} failures (item text prefix):")
        for sid, text in failures[:top]:
            lines.append(f"    [{sid}] {text[:80]}")
    print("\n".join(lines))
    # #504: the only read-side verification verb recorded nothing, so there was
    # no evidence either way about whether anyone reaches for it. The unpaired
    # variant is a distinct event, not a detail of this one: a run that resolved
    # no transcript verified nothing, and it is also how a host whose
    # transcripts live outside the registered resolver's reach shows up at all.
    # #503: keyed on ANY resolved transcript, not on `paired`. Once resolution
    # is per item, a checkpoint whose own transcript is gone still verifies its
    # carried items through origin_session — `paired` counts containing
    # checkpoints and would report that run as silence.
    resolved_any = any(v is not None for v in transcripts.values())
    _note_usage("audit-quotes" if resolved_any else "audit-quotes:unpaired")
    return 0


def _cmd_heal(args) -> int:
    """Explain the heal decision, then repair the newest healable session if safe.
    Every no-op returns 0 (a no-op heal is never an error). `--dry-run` explains
    without serializing. `--force` (#15) ignores a prior retry marker so a
    retry-exhausted session becomes healable again — the default one-retry-ever
    policy is unchanged when --force is absent.

    #219: a real target means `_run_serialize` runs next — the same ~15s-2min
    silent LLM roundtrip `configure --test` already covers with the house
    `render.working()` spinner (#182/#183). Unlike that call site, which wraps
    only the LLM call and prints its verdict AFTER the `with` exits,
    `_run_serialize` prints its own byte-identical result line (the
    `_RESULT_OK_RE`/`_RESULT_ERR_RE` contract, see its docstring) from deep
    inside its own body — hoisting that print out would touch the layering
    shared with `_run_serialize`'s other, non-interactive callers (the hook
    path and the `serialize` command), which must stay untouched. A manual
    check (rich `Console().status(...)` wrapping a body that calls plain
    `print()`) confirmed Rich's Live-backed Status intercepts stdout writes
    cleanly during the spinner: the printed line lands undisturbed between
    spinner frames and the spinner's own line is cleared before the `with`
    exits, both on the rich/TTY path and the plain path (which never touches
    Live at all). So the whole `_run_serialize` call — print included — is
    wrapped directly; no restructuring of `_run_serialize` needed."""
    dry_run = getattr(args, "dry_run", False)
    force = getattr(args, "force", False)
    # #601: heal owns repair, so the dead-index-snapshot reap lives here (the
    # audit group is read-only by charter). Runs even when nothing is
    # healable — the strands are what pin `audit privacy` at cannot-prove.
    for p in recall.reap_dead_snapshots(apply=not dry_run):
        print(f"{'would reap' if dry_run else 'reaped'} "
              f"dead index snapshot: {p.name}")
    # #607: same repair charter — bound how long daimon-authored Windsurf
    # conversation text lingers between forgets.
    for p in store.reap_windsurf_state(apply=not dry_run):
        print(f"{'would reap' if dry_run else 'reaped'} "
              f"aged windsurf transcript: {p.name}")
    try:
        text = (config.log_dir() / "serialize.log").read_text(encoding="utf-8")
    except OSError:
        text = ""
    now = time.time()
    plan = _heal_plan(text, now, force=force)
    render.render_heal(plan, dry_run=dry_run, force=force)
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
    # #360: the default retry re-runs the SAME extraction shape that already
    # failed. Opt-in escalation (DAIMON_HEAL_ESCALATION) re-serializes from
    # multiple perspectives instead — heal-path only, so the extra token cost
    # scales with failure, never with usage.
    escalate = config.heal_escalation_enabled()
    with render.working(f"healing {t['sid']} — re-serializing transcript"):
        return _run_serialize(transcript_path, t["project"], escalate=escalate)


# ---- team: sidecar private-repo sync (#113) ----


def _cmd_team_init(args) -> int:
    try:
        # #279: a fresh team is born scoped to the project init ran from.
        dest = teamsync.init(args.remote_url, project_dir=Path.cwd())
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
    # #600 slice B, opt-in: apply teammates' tombstones to THIS machine's own
    # checkpoints. TWO gates, and the flag is the load-bearing one: bare
    # `daimon team sync` is spawned DETACHED at SessionStart by
    # lib.spawn_team_sync with stdout to DEVNULL, exactly like heal — so a
    # setting alone would delete local belief state unattended and silently,
    # which is the failure this design exists to prevent. The hook never
    # passes --apply-forget, so only a typed command can reach this.
    # Machine-wide, matching sync's own project-agnostic contract.
    if getattr(args, "apply_forget", False):
        if not config.team_apply_forget():
            print("daimon team: --apply-forget needs DAIMON_TEAM_APPLY_FORGET=1"
                  " — a teammate's forget rewriting your own checkpoints is"
                  " opt-in, and there is no undo", file=sys.stderr)
        else:
            applied = store.apply_foreign_tombstones(all_projects=True)
            print(f"applied teammates' forget tombstones to {len(applied)} "
                  "local surface(s) across all projects")
    # #246: fetched teammate files are fingerprint input — freshen here (the
    # SessionStart hook spawns sync detached, off the prompt path) so the
    # first recall after a fetch doesn't pay the rebuild. Unconditional on
    # purpose: a no-op sync warms in ~ms, and any staleness left by other
    # writers gets healed opportunistically.
    recall.warm()
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
        # #217: uncommitted checkpoints (dual-written to disk, staged+committed
        # only on the next sync) are invisible to `unpushed` alone — surface
        # them and nudge a sync, but only when there's something to nudge
        # about, so the common case's wording stays byte-identical.
        pending = row.get("pending", 0)
        pending_part = (f", {pending} pending checkpoint(s) — run "
                        "`daimon team sync`") if pending > 0 else ""
        lines.append(f"{row['slug']}: {row['freshness']} — "
                     f"{row['unpushed']} unpushed checkpoint(s)"
                     f"{pending_part}, authors: {authors}")
        # #200: a broken daimon-team.toml fails open (mapping ignored) on the
        # write path — status is the one place the parse error surfaces.
        if row.get("config_warning"):
            lines.append(f"  warning: {row['config_warning']}")
        # #279: scope is default-closed — an empty allowlist means the remote
        # receives nothing, which must be visible, not a silent surprise.
        scope = row.get("scope") or []
        if scope:
            lines.append(f"  scope: {', '.join(scope)}")
        elif config.team_project():
            lines.append("  scope: none configured — DAIMON_TEAM_PROJECT "
                         "grants this machine's sessions")
        else:
            lines.append("  scope: none — this remote receives no checkpoints "
                         "(add [scope] repos to daimon-team.toml)")
    # #387: where the CURRENT project's checkpoints route — the one place a
    # misconfigured toml is visible BEFORE a session's checkpoint quietly
    # stays in the local mirror.
    dests = store._team_write_slugs(_resolve_project(getattr(args, "project", None)))
    line = "this project writes to: " + ", ".join(dests)
    if dests == [store._TEAM_LOCAL_REMOTE]:
        line += ("  (no remote grants it membership — add its repo URL to a "
                 "sidecar's daimon-team.toml [scope] repos)")
    lines.append(line)
    render.render_team_status(lines)
    return 0


# ---- configure: detect/report the resolved backend + fill gaps in ~/.daimon/env ----


def _run_backend_test() -> int:
    """--test (#56): prove the RESOLVED backend works, interactively, at setup
    time — the alternative is a real serialize failing minutes later inside
    a detached hook child. One tiny prompt through the same llm.chat path
    serialization uses; failure prints the cause and where stderr landed."""
    start = time.monotonic()
    try:
        # working() (#182): the roundtrip is ~15s of otherwise-dead
        # terminal at the exact moment a new user decides whether the
        # tool works — spinner on rich/TTY, one plain line elsewhere.
        with render.working("testing backend — one tiny prompt through "
                            "the resolved backend"):
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


def _configure_flag_updates(args) -> dict:
    """Backend flags -> env updates (the non-interactive write path)."""
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
        if args.input:
            updates["DAIMON_LLM_COMMAND_INPUT"] = args.input
    # claude-cli: just pin the backend, no credentials needed.
    return updates


def _ask_backend_updates() -> dict:
    """Interactive backend Q&A -> env updates. Question order and wording are
    a stable contract with the tests' answer iterators — extend at the END."""
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
        input_spec = _prompt(
            "input spec [stdin/arg/file:<flag>] (blank=stdin): "
        ).strip()
        if input_spec:
            updates["DAIMON_LLM_COMMAND_INPUT"] = input_spec
    # claude-cli: nothing more to ask.
    return updates


def _configure_wizard(args) -> int:
    """#368: `daimon configure --init` — the guided path the two
    highest-friction onboarding moments never had. Backend -> timeout ->
    probe offer -> optional team walk -> the same `daimon status` summary
    the docs reference. Every prompt has a flag escape hatch so scripts/CI
    can run the whole thing non-interactively."""
    interactive = sys.stdin.isatty() and not args.backend
    updates: dict = {}
    if args.backend:
        updates = _configure_flag_updates(args)
    elif interactive:
        updates = _ask_backend_updates()
        timeout = _prompt(
            "serialize timeout seconds (blank = default "
            f"{config.timeout_seconds()}): ").strip()
        if timeout.isdigit():
            updates["DAIMON_TIMEOUT"] = timeout
    else:
        render.render_configure_lines(
            ["--init needs a terminal or --backend plus value flags "
             "(and optionally --timeout/--author/--team-remote)."])
        return 0
    if getattr(args, "timeout", None):
        updates["DAIMON_TIMEOUT"] = str(args.timeout)
    # Scar fence: DAIMON_TIMEOUT is a TOTAL budget shared across retries, and
    # real serialize/merge calls run 80-250s each — a sub-420 budget cannot
    # fit even one slow call. The wizard must not help a user write one.
    if updates.get("DAIMON_TIMEOUT") and int(updates["DAIMON_TIMEOUT"]) < 420:
        render.render_configure_lines(
            [f"timeout {updates['DAIMON_TIMEOUT']}s is below the 420s floor — "
             "ignored (real serialize/merge calls run 80-250s each; the "
             "budget must fit at least one slow call plus a retry)"])
        del updates["DAIMON_TIMEOUT"]
    if updates:
        path = configure.write_env(updates)
        render.render_configure_lines([f"wrote {path}"])

    # Probe right after writing (#368 item 2) — the alternative is the first
    # real serialize failing inside a detached hook child. Default YES on the
    # interactive path: a wizard that skips its own verification teaches the
    # user nothing about whether setup worked.
    rc = 0
    run_probe = getattr(args, "test", False)
    if interactive and not run_probe:
        run_probe = _prompt("run backend test now? [Y/n]: ").strip().lower() \
            not in ("n", "no")
    if run_probe:
        rc = _run_backend_test()

    # Team walk (#368 item 3): env vars + `team init` in one place, instead
    # of spread across the reference page and a separate command.
    author = getattr(args, "author", None)
    remote = getattr(args, "team_remote", None)
    if interactive and not (author or remote):
        if _prompt("set up team memory? [y/N]: ").strip().lower() in ("y", "yes"):
            author = _prompt("author name (namespaces your checkpoints): ").strip()
            remote = _prompt("team remote URL (git): ").strip()
    if author or remote:
        team_updates = {"DAIMON_TEAM": "1"}
        if author:
            team_updates["DAIMON_AUTHOR"] = author
        configure.write_env(team_updates)
        if remote:
            try:
                dest = teamsync.init(remote, project_dir=Path.cwd())
                render.render_team_init([
                    f"initialized team sidecar: {dest}",
                    "checkpoints now sync there — `daimon team sync` runs "
                    "opportunistically at session start",
                ])
            except teamsync.TeamError as exc:
                print(f"error: {exc}", file=sys.stderr)
                rc = rc or 1

    # End on the exact status view the docs reference (#368 item 4), so the
    # user leaves the wizard seeing the same health verdict every other
    # surface will show them.
    _cmd_status(argparse.Namespace(project=None, json=False, suppressed=False))
    return rc


def _cmd_configure(args) -> int:
    """Detect + report the resolved LLM backend; fill gaps in ~/.daimon/env.

    Always prints a doctor view. With backend flags, writes non-interactively.
    With no flags it is SAFE everywhere: it only prompts on a TTY when daimon is
    not ready, and otherwise just prints guidance — it never blocks.
    `--init` (#368) runs the full guided wizard instead.
    """
    if getattr(args, "init", False):
        return _configure_wizard(args)
    if getattr(args, "test", False):
        return _run_backend_test()

    st = configure.status()
    render.render_configure(st)

    if args.backend:
        path = configure.write_env(_configure_flag_updates(args))
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
    path = configure.write_env(_ask_backend_updates())
    render.render_configure_lines([f"wrote {path}"])
    render.render_configure(configure.status())
    return 0


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
    out = {"checkpoints": 0, "project_buckets": 0, "items_by_kind": {},
           "items_verbatim": 0, "items_inferred": 0, "items_untagged": 0,
           "items_carried": 0, "format_versions": {}, "extraction_versions": {}}
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
    out = {"window_days": _RETENTION_WINDOW_DAYS, "hook_briefs": 0,
           "skill_briefs": 0, "ambiguous_briefs": 0, "briefings_total": 0,
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


def _earlier(current: str | None, ts: str) -> str | None:
    """Earliest of two event stamps, ignoring empties. Stamps are written by
    one helper in a single UTC format, so lexicographic order is chronological
    (#562)."""
    if not ts:
        return current
    return ts if current is None or ts < current else current


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
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        for line in lines:
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if not isinstance(evt, dict) or not evt.get("item_ref"):
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
    project = _resolve_project(None)
    data = {"usage": usage, "capture": _stats_capture(),
            "store": _stats_store(), "retention": _stats_retention(),
            "events": _stats_events(project),
            "verification": _stats_verification(project),
            "resolutions": _stats_resolutions(project, usage),
            # #475 part 2: current-configuration posture, rendered next to
            # (never merged into) the historical fallback counts above.
            "rescue_posture": llm.rescue_posture()}
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
        # redact.py ships alongside the scripts so the standalone hooks can
        # scrub secrets at their write sites (#109) without importing the
        # venv-only package. A test keeps it byte-identical to the canonical
        # daimon_briefing/redact.py.
        "files": ("daimon-windsurf-hooks.py", "_daimon_hook_lib.py", "redact.py"),
        "entry": "daimon-windsurf-hooks.py",
        "events": ("pre_user_prompt", "post_cascade_response",
                   "post_cascade_response_with_transcript"),
    },
    # Codex needs two distinct scripts under two events plus a real hooks.json
    # registration, so it carries `register: "codex"` and the install command
    # delegates the whole flow to codex_hooks.install (#262). `files`/`events`
    # here drive `hooks list` only; codex_hooks owns the copy + registration.
    "codex": {
        "files": ("daimon-codex-session-start.py", "daimon-codex-stop.py",
                  "_daimon_hook_lib.py"),
        "events": ("SessionStart", "Stop"),
        "register": "codex",
    },
}


def _hooks_target_dir() -> Path:
    return Path.home() / ".daimon" / "hooks"


def _host_scripts(spec) -> str:
    """Display label of a host's hook script(s): the single `entry` when the
    host has one, else the registered scripts (everything but shared helpers)."""
    if spec.get("entry"):
        return spec["entry"]
    return ", ".join(n for n in spec["files"]
                     if n not in ("_daimon_hook_lib.py", "redact.py"))


def _cmd_hooks_list(args) -> int:
    lines = [f"{host}  ({_host_scripts(spec)}; events: {', '.join(spec['events'])})"
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
    pkg = resources.files("daimon_briefing._hooks")
    if spec.get("register") == "codex":
        # Codex owns its own install path: two scripts registered under two
        # events straight into ~/.codex/hooks.json (#262), not a printed snippet.
        from . import codex_hooks

        render.render_hooks_install(codex_hooks.install(pkg, Path.home()))
        return 0
    target = _hooks_target_dir()
    target.mkdir(parents=True, exist_ok=True)
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


# ---- hooks status: audit installed copies against the packaged bytes (#266) ----
#
# A stale hook copy keeps *working* on old behavior after an upgrade, so drift is
# invisible until a briefing turns up wrong. `daimon hooks status` hashes the
# packaged bytes against what is installed and reports it, per host, per file.


def _host_install_dir(spec, home: Path) -> Path:
    """Where a host's hook files live. Codex owns ~/.codex/hooks/ (it registers
    the scripts there itself); everyone else shares the stable ~/.daimon/hooks/
    that `hooks install` writes to."""
    if spec.get("register") == "codex":
        return home / ".codex" / "hooks"
    return home / ".daimon" / "hooks"


def _hook_file_status(pkg, install_dir: Path, name: str) -> str:
    """CURRENT / STALE / MISSING for one installed file vs its packaged copy.
    ``exists()`` and ``read_bytes()`` both follow symlinks, so a symlinked
    install is judged by the bytes it points at (a broken link → MISSING)."""
    dest = install_dir / name
    if not dest.exists():
        return "MISSING"
    try:
        installed = dest.read_bytes()
    except OSError:
        return "MISSING"
    packaged = (pkg / name).read_bytes()
    match = hashlib.sha256(installed).digest() == hashlib.sha256(packaged).digest()
    return "CURRENT" if match else "STALE"


def _codex_registration_status(home: Path) -> str:
    """REGISTERED / PARTIAL / UNREGISTERED for the ~/.codex/hooks.json entries.
    Reuses codex_hooks' own loader and ownership check so this verdict can never
    drift from what `hooks install codex` writes."""
    from . import codex_hooks

    settings = codex_hooks._load(home / ".codex" / "hooks.json")
    cfg = settings.get("hooks", {})
    if not isinstance(cfg, dict):
        cfg = {}
    found = sum(
        1 for hspec in codex_hooks.HOOKS
        if any(codex_hooks._is_ours(g, hspec["script"])
               for g in cfg.get(hspec["event"], []) if isinstance(g, dict))
    )
    if found == 0:
        return "UNREGISTERED"
    return "REGISTERED" if found == len(codex_hooks.HOOKS) else "PARTIAL"


def _host_status_entry(host: str, spec, pkg, home: Path) -> dict:
    install_dir = _host_install_dir(spec, home)
    reg = _codex_registration_status(home) if spec.get("register") == "codex" else None
    installed = any((install_dir / n).exists() for n in spec["files"])
    if spec.get("register") == "codex":
        # Codex counts as installed if its hooks dir OR any of our registration
        # entries exist — either alone is a setup we must audit, not ignore.
        installed = installed or install_dir.exists() or reg != "UNREGISTERED"
    entry = {"host": host, "dir": str(install_dir), "installed": installed,
             "registration": reg, "files": [], "drift": False}
    if not installed:
        return entry
    entry["files"] = [{"name": n, "status": _hook_file_status(pkg, install_dir, n)}
                      for n in spec["files"]]
    file_drift = any(f["status"] in ("STALE", "MISSING") for f in entry["files"])
    reg_drift = reg is not None and reg != "REGISTERED"
    entry["drift"] = file_drift or reg_drift
    return entry


def _hooks_status_report(home: Path) -> list[dict]:
    from importlib import resources

    pkg = resources.files("daimon_briefing._hooks")
    return [_host_status_entry(host, spec, pkg, home)
            for host, spec in sorted(_HOOK_HOSTS.items())]


def _version_tuple(v: str) -> tuple:
    """Loose numeric compare, stdlib only (no packaging dependency). Trailing
    non-digits in a component are ignored, so '1.0.0rc1' sorts as (1, 0, 0)."""
    out = []
    for chunk in str(v).split("."):
        digits = ""
        for ch in chunk:
            if not ch.isdigit():
                break
            digits += ch
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _plugin_drift(home: Path, cli_version: str) -> dict | None:
    """The installed Claude Code plugin's version vs this CLI's, or None when
    they agree or no plugin is installed (#554).

    Claude Code's hooks ship INSIDE the plugin rather than through
    `hooks install`, so #266's byte-hash audit never sees them: the host with
    the most users was the only one whose drift nothing reported. The two
    halves also move under different commands (`uv tool upgrade` for the CLI,
    the host's own plugin update for the hooks), and neither notices the other.

    The version comes from daimon's OWN manifest inside the installed tree.
    That file ships with the code that will actually execute; the host's record
    only says what it meant to install, and the two can disagree.
    """
    state = home / ".claude" / "plugins" / "installed_plugins.json"
    try:
        data = json.loads(state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None
    installed = None
    for key, entries in plugins.items():
        if key.split("@")[0] != "daimon" or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            installed = _plugin_manifest_version(entry.get("installPath")) \
                or entry.get("version")
            if installed:
                break
        if installed:
            break
    if not installed or installed == cli_version:
        return None
    return {"installed": installed, "cli": cli_version,
            "behind": _version_tuple(installed) < _version_tuple(cli_version)}


def _plugin_manifest_version(install_path) -> str | None:
    if not install_path:
        return None
    manifest = Path(install_path) / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def _plugin_drift_present() -> dict | None:
    """Same swallow-everything contract as _hook_drift_present: status must
    never crash on another tool's file format."""
    try:
        return _plugin_drift(Path.home(), __version__)
    except Exception:
        return None


def _hook_drift_present() -> bool:
    """Cheap yes/no for the `daimon status` pointer — hashes a handful of small
    files. Swallows every error: status must never crash on a weird hooks tree,
    and a probe that cannot read the packaged copies simply reports no drift."""
    try:
        return any(h["drift"] for h in _hooks_status_report(Path.home()))
    except Exception:
        return False


def _cmd_hooks_status(args) -> int:
    report = _hooks_status_report(Path.home())
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        render.render_hooks_status(report)
    return 1 if any(h["drift"] for h in report) else 0


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
    line, then the traceback. Covers uncaught Python exceptions (the
    dominant case); interpreter-level deaths still write nothing.

    #605: the traceback is formatted HERE rather than handed to
    sys.__excepthook__, so it can pass through redact_text on the way out.
    The crashing process is the only one that can scrub these bytes — for
    the same reason it is the only one that can stamp them — and #513
    redacted the tail on READ over a file nothing deleted. Item text still
    survives (redaction catches secrets, not beliefs), which is why the
    purge above it is wholesale.

    Fail-open, redact.py's own posture: anything that goes wrong formatting
    or redacting falls back to the stock hook, because a swallowed traceback
    is a crash nobody can diagnose."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), "?")
    print(f"--- crash {stamp} pid={os.getpid()} cmd={cmd} ---",
          file=sys.stderr, flush=True)
    try:
        formatted = "".join(traceback.format_exception(exc_type, exc, tb))
        redacted, _ = redact.redact_text(formatted)
        print(redacted, end="", file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — see fail-open above
        sys.__excepthook__(exc_type, exc, tb)


def build_parser() -> argparse.ArgumentParser:
    """The full daimon parser tree, extracted from main (#431) so tests can
    walk the subparser registry mechanically — the write-audit architecture
    guard enumerates every command argparse knows about, so a NEW subcommand
    is enumerated (and audited) automatically the moment it is registered."""
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
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
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
        "--slug", metavar="SLUG",
        help="brief another project's bucket by its slug (see `daimon "
             "projects`) — deliberate cross-project read, provenance-labeled, "
             "no fallback (#243)",
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
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`) — "
             "reaches buckets whose source path no longer exists (#243)",
    )
    p_recall.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_recall.add_argument(
        "--limit", type=int, default=20, help="max results (default: 20)"
    )
    p_recall.set_defaults(func=_cmd_recall)

    p_why = sub.add_parser(
        "why", help="inspect the evidence and lifecycle receipt for one item",
        epilog="Examples:\n"
               "  daimon recall retry policy\n"
               "  daimon why o-3f8a2c\n"
               "  daimon why o-3f8a2c --source --json\n",
    )
    p_why.add_argument(
        "item_id", help="exact item id shown by `daimon recall` or `daimon loops`")
    p_why.add_argument(
        "--source", action="store_true",
        help="show one bounded, redacted message-level source window")
    p_why.add_argument(
        "--json", action="store_true", help="machine-readable evidence axes")
    p_why.add_argument(
        "--project",
        help="project directory to scope to (default: DAIMON_PROJECT_DIR, then cwd)")
    p_why.add_argument(
        "--slug", metavar="SLUG",
        help="scope to a project bucket by its slug (see `daimon projects`)")
    p_why.set_defaults(func=_cmd_why)

    p_serve = sub.add_parser(
        "serve",
        help="serve the read-only local viewer (localhost only)",
        description="Serve the read-only local viewer on localhost. Every "
                    "surface renders an existing engine's output; nothing "
                    "writes.",
        epilog="Examples:\n"
               "  daimon serve\n"
               "  daimon serve --port 7800 --no-browser\n",
    )
    p_serve.add_argument(
        "--data-dir", default=None,
        help="checkpoint dir (default: DAIMON_CHECKPOINT_DIR, then ~/.daimon/checkpoints)")
    p_serve.add_argument(
        "--project-dir", default=None,
        help="project directory to scope to (default: cwd)")
    p_serve.add_argument(
        "--port", type=int, default=None, help="port to bind (default: 7717)")
    p_serve.add_argument(
        "--no-browser", action="store_true", help="don't open a browser tab")
    p_serve.set_defaults(func=_cmd_serve)

    p_projects = sub.add_parser(
        "projects", help="list every project daimon has a checkpoint for",
        epilog="Examples:\n  daimon projects\n  daimon projects --json\n",
    )
    p_projects.add_argument(
        "--project",
        help="project directory the current-project mark is computed against "
             "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_projects.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    p_projects.set_defaults(func=_cmd_projects)

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
    p_resolve.add_argument(
        "--by", choices=["agent"],
        help="declare an agent-initiated claim (requires --evidence); "
             "omit for the human path (default)")
    p_resolve.add_argument(
        "--evidence",
        help="verbatim transcript quote proving the claim — required with "
             "--by agent, rejected without it")
    p_resolve.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_resolve.add_argument(
        "--dry-run", action="store_true",
        help="show what would resolve without writing an event — look before the write (#304)")
    p_resolve.set_defaults(func=_cmd_resolve)

    p_forget = sub.add_parser(
        "forget", help="remove ONE item from the live checkpoint and record a "
        "signed-state tombstone — content leaves disk and index; the event "
        "carries only a hash (#321)",
        epilog="Examples:\n  daimon forget o-3f8a2c --reason \"contains client name\"\n"
               "  daimon forget \"wrong belief about retry nonce\" --dry-run\n",
    )
    p_forget.add_argument("target", help="item id (exact) or a query that must match exactly one item")
    p_forget.add_argument("--reason", help="recorded on the tombstone event (redacted like any note)")
    p_forget.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_forget.add_argument(
        "--dry-run", action="store_true",
        help="show what would be forgotten without writing — look before a destructive op")
    p_forget.set_defaults(func=_cmd_forget)

    p_reverify = sub.add_parser(
        "reverify", help="evidence-gated reopen of a resolved item (#103) — "
        "refuses without proof, so a claim can't get re-verified for free",
        epilog="Examples:\n"
               "  daimon reverify o-3f8a2c --evidence \"checked release page\"\n"
               "  daimon reverify o-3f8a2c   # reopens only if the anchor still checks live\n"
               "  daimon reverify o-3f8a2c   # rejects an unconfirmed supersede candidate — no evidence needed\n",
    )
    p_reverify.add_argument("target", help="item id (exact only — reverify is deliberate, no fuzzy match)")
    p_reverify.add_argument("--evidence", help="why this claim can be trusted again")
    p_reverify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_reverify.set_defaults(func=_cmd_reverify)

    p_loops = sub.add_parser(
        "loops", help="list open, briefable loop items with ids for this "
        "project (#480) — the read counterpart to daimon resolve's write path",
        epilog="Examples:\n  daimon loops\n  daimon loops --project .\n",
    )
    p_loops.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_loops.set_defaults(func=_cmd_loops)

    p_refute = sub.add_parser(
        "refute",
        help="author and query evidence-cited negative knowledge (#573)",
        epilog="Examples:\n"
               "  daimon refute list\n"
               "  daimon refute search receipt verification\n"
               "  daimon refute guard 'should we revisit #502?'\n",
    )
    refute_sub = p_refute.add_subparsers(dest="refute_cmd", required=True)
    refute_sub.add_parser = functools.partial(
        refute_sub.add_parser, formatter_class=fmt)

    pr_add = refute_sub.add_parser(
        "add", help="assert a scoped refutation; agent assertions stay candidates",
        epilog="Example:\n"
               "  daimon refute add --subject 'original #502 receipt design' "
               "--verdict 'whole-file hashes do not prove span claims' "
               "--scope 'carried-item receipt tiers' --anchor issue:502 "
               "--evidence 'measurement:566/623 origin misses' "
               "--ratify\n",
    )
    pr_add.add_argument("--subject", required=True, help="approach or claim rejected")
    pr_add.add_argument("--verdict", required=True, help="what no longer holds")
    pr_add.add_argument("--scope", required=True, help="where the verdict applies")
    pr_add.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="cited measurement, artifact, issue, or transcript source; recorded verbatim, not resolved or verified; repeatable")
    pr_add.add_argument(
        "--anchor", action="append", default=[], metavar="ANCHOR",
        help="stable exact-match key such as issue:502 or command:daimon-why; repeatable")
    pr_add.add_argument(
        "--revisit-when", help="condition that makes reconsideration legitimate")
    pr_add.add_argument("--by", choices=["agent"], default=None,
                        help="declare yourself an agent; omit it only from an "
                             "interactive terminal, which is the human path")
    pr_add.add_argument(
        "--ratify", action="store_true",
        help="activate immediately; valid only on the human path, which is "
             "an interactive terminal with no --by.")
    pr_add.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_add.add_argument("--json", action="store_true", help="machine-readable output")
    pr_add.set_defaults(func=_cmd_refute_add)

    pr_ratify = refute_sub.add_parser(
        "ratify", help="explicitly activate a candidate as a human decision")
    pr_ratify.add_argument("refutation_id", help="exact r-… id")
    pr_ratify.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; ratification then refuses, because "
             "activation requires a human channel")
    pr_ratify.add_argument("--note", help="optional ratification rationale")
    pr_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_ratify.add_argument("--json", action="store_true", help="machine-readable output")
    pr_ratify.set_defaults(func=_cmd_refute_ratify)

    pr_revise = refute_sub.add_parser(
        "revise", help="append a new evidence-cited version; inactive until ratified")
    pr_revise.add_argument("refutation_id", help="exact r-… id")
    pr_revise.add_argument("--subject", help="replacement subject")
    pr_revise.add_argument("--verdict", help="replacement verdict")
    pr_revise.add_argument("--scope", help="replacement scope")
    pr_revise.add_argument(
        "--anchor", action="append", default=None, metavar="ANCHOR",
        help="replacement anchor set; repeatable")
    pr_revise.add_argument("--revisit-when", help="replacement revisit condition")
    pr_revise.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="new evidence cited for the revision; recorded, not verified; repeatable")
    pr_revise.add_argument("--by", choices=["agent"], default=None)
    pr_revise.add_argument(
        "--ratify", action="store_true",
        help="activate the revision immediately; valid only on the human "
             "path, which is an interactive terminal with no --by.")
    pr_revise.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_revise.add_argument("--json", action="store_true", help="machine-readable output")
    pr_revise.set_defaults(func=_cmd_refute_revise)

    pr_overturn = refute_sub.add_parser(
        "overturn",
        help="cite evidence against the verdict; agent calls propose, humans deactivate")
    pr_overturn.add_argument("refutation_id", help="exact r-… id")
    pr_overturn.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="evidence cited against the active verdict; recorded, not "
             "verified; repeatable")
    pr_overturn.add_argument("--note", help="optional explanation")
    pr_overturn.add_argument("--by", choices=["agent"], default=None)
    pr_overturn.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_overturn.add_argument("--json", action="store_true", help="machine-readable output")
    pr_overturn.set_defaults(func=_cmd_refute_overturn)

    pr_show = refute_sub.add_parser("show", help="show one refutation and its trust signals")
    pr_show.add_argument("refutation_id", help="exact r-… id")
    pr_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_show.add_argument("--json", action="store_true", help="machine-readable output")
    pr_show.set_defaults(func=_cmd_refute_show)

    pr_list = refute_sub.add_parser("list", help="list project refutations")
    pr_list.add_argument("--state", action="append", choices=sorted(refutations.STATES),
                         help="filter by state; repeatable")
    pr_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_list.add_argument("--json", action="store_true", help="machine-readable output")
    pr_list.set_defaults(func=_cmd_refute_list)

    pr_search = refute_sub.add_parser(
        "search", help="search the complete ledger without age decay")
    pr_search.add_argument("query", nargs="+", help="subject, verdict, scope, or anchor terms")
    pr_search.add_argument("--state", action="append", choices=sorted(refutations.STATES),
                           help="filter by state; repeatable")
    pr_search.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_search.add_argument("--json", action="store_true", help="machine-readable output")
    pr_search.set_defaults(func=_cmd_refute_search)

    pr_guard = refute_sub.add_parser(
        "guard", help="check active refutations by exact anchor or subject phrase")
    pr_guard.add_argument("query", nargs="+", help="the proposed approach or user prompt")
    pr_guard.add_argument("--anchor", action="append", default=[], metavar="ANCHOR",
                          help="candidate action anchor; repeatable")
    pr_guard.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pr_guard.add_argument("--json", action="store_true", help="machine-readable output")
    pr_guard.add_argument("--quiet", action="store_true",
                          help="print nothing when no active refutation matches")
    pr_guard.set_defaults(func=_cmd_refute_guard)

    p_ruling = sub.add_parser(
        "ruling",
        help="standing rulings: human-ratified records that never decay (#693)",
        epilog="Examples:\n"
               "  daimon ruling propose --subject 'public posts' "
               "--verdict 'internal numbers never appear in public posts' "
               "--scope publishing --evidence issue:693 --by agent\n"
               "  daimon ruling ratify r-1a2b3c4d5e6f\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ruling_sub = p_ruling.add_subparsers(dest="ruling_cmd", required=True)

    rl_propose = ruling_sub.add_parser(
        "propose", help="propose a standing ruling; agent proposals stay "
                        "candidates until a human ratifies")
    rl_propose.add_argument("--subject", required=True,
                            help="what the ruling governs")
    rl_propose.add_argument("--verdict", required=True,
                            help="the ruling text itself (max 280 chars)")
    rl_propose.add_argument("--scope", required=True,
                            help="where it applies")
    rl_propose.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="cited source behind the ruling; recorded verbatim, not "
             "resolved or verified; repeatable")
    rl_propose.add_argument(
        "--anchor", action="append", default=[], metavar="ANCHOR",
        help="stable exact-match key such as issue:693; repeatable")
    rl_propose.add_argument(
        "--revisit-when", help="condition that makes reconsideration legitimate")
    rl_propose.add_argument("--by", choices=["agent"], default=None,
                            help="declare yourself an agent; omit it only "
                                 "from an interactive terminal")
    rl_propose.add_argument(
        "--ratify", action="store_true",
        help="activate immediately; valid only on the human path")
    rl_propose.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_propose.add_argument("--json", action="store_true", help="machine-readable output")
    rl_propose.set_defaults(func=_cmd_ruling_propose)

    rl_ratify = ruling_sub.add_parser(
        "ratify", help="activate a candidate ruling; prints the full text "
                       "and confirms before the append")
    rl_ratify.add_argument("ruling_id", help="exact r-… id")
    rl_ratify.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; ratification "
                                "then refuses")
    rl_ratify.add_argument("--note", help="optional ratification rationale")
    rl_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_ratify.add_argument("--json", action="store_true", help="machine-readable output")
    rl_ratify.set_defaults(func=_cmd_ruling_ratify)

    rl_revise = ruling_sub.add_parser(
        "revise", help="revise a ruling; on an active ruling an agent call "
                       "records a proposal and the text stands")
    rl_revise.add_argument("ruling_id", help="exact r-… id")
    rl_revise.add_argument("--subject", help="replacement subject")
    rl_revise.add_argument("--verdict", help="replacement ruling text")
    rl_revise.add_argument("--scope", help="replacement scope")
    rl_revise.add_argument(
        "--anchor", action="append", default=None, metavar="ANCHOR",
        help="replacement anchor set; repeatable")
    rl_revise.add_argument("--revisit-when", help="replacement revisit condition")
    rl_revise.add_argument(
        "--evidence", action="append", required=True, metavar="SOURCE",
        help="source cited for the revision; recorded, not verified; repeatable")
    rl_revise.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; on an active ruling the call then "
             "records a proposal and the text stands")
    rl_revise.add_argument(
        "--ratify", action="store_true",
        help="refused: activation goes through `daimon ruling ratify`, "
             "which shows the text before the write")
    rl_revise.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_revise.add_argument("--json", action="store_true", help="machine-readable output")
    rl_revise.set_defaults(func=_cmd_ruling_revise)

    rl_retire = ruling_sub.add_parser(
        "retire", help="end a ruling; agent calls propose, humans retire "
                       "directly; evidence optional")
    rl_retire.add_argument("ruling_id", help="exact r-… id")
    rl_retire.add_argument(
        "--evidence", action="append", default=None, metavar="SOURCE",
        help="optional source for why the ruling stopped applying; repeatable")
    rl_retire.add_argument("--note", help="optional explanation")
    rl_retire.add_argument(
        "--by", choices=["agent"], default=None,
        help="declare yourself an agent; the call then records a retirement "
             "proposal and the ruling stands until a human verdict")
    rl_retire.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_retire.add_argument("--json", action="store_true", help="machine-readable output")
    rl_retire.set_defaults(func=_cmd_ruling_retire)

    rl_list = ruling_sub.add_parser("list", help="list project rulings")
    rl_list.add_argument("--state", action="append",
                         choices=sorted(refutations.STATES),
                         help="filter by state; repeatable")
    rl_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_list.add_argument("--json", action="store_true", help="machine-readable output")
    rl_list.set_defaults(func=_cmd_ruling_list)

    rl_show = ruling_sub.add_parser("show", help="show one ruling, including "
                                                 "pending proposals")
    rl_show.add_argument("ruling_id", help="exact r-… id")
    rl_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    rl_show.add_argument("--json", action="store_true", help="machine-readable output")
    rl_show.set_defaults(func=_cmd_ruling_show)

    p_amend = sub.add_parser(
        "amend",
        help="record an evidence-carrying state transition on a briefed item (#691)",
        epilog="Examples:\n"
               "  daimon amend o-1a2b3c4d5e6f --change progressed "
               "--evidence 'the PR merged' --by agent\n"
               "  daimon amend ratify a-0f1e2d3c4b5a\n",
    )
    amend_sub = p_amend.add_subparsers(dest="amend_cmd", required=True)
    amend_sub.add_parser = functools.partial(
        amend_sub.add_parser, formatter_class=fmt)

    pa_prop = amend_sub.add_parser(
        "propose",
        help="propose an amendment; agent proposals stay candidates until "
             "the session-end byte-check or a human verdict")
    pa_prop.add_argument("item_id",
                         help="exact item id from a briefing/loops handle")
    pa_prop.add_argument(
        "--change", required=True, choices=sorted(amendments.CHANGES),
        help="the typed transition; the closed vocabulary is the render bound")
    pa_prop.add_argument(
        "--evidence", required=True,
        help="verbatim transcript quote backing the change; byte-checked "
             "against this session's transcript at session end")
    pa_prop.add_argument("--note",
                         help="short context; human channel only")
    pa_prop.add_argument("--by", choices=["agent"], default=None,
                         help="declare yourself an agent; omit it only from "
                              "an interactive terminal, which is the human path")
    pa_prop.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_prop.set_defaults(func=_cmd_amend_propose)

    pa_ratify = amend_sub.add_parser(
        "ratify", help="activate a candidate or verified amendment as a human decision")
    pa_ratify.add_argument("amendment_id", help="exact a-… id")
    pa_ratify.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; ratification then "
                                "refuses, because it requires a human channel")
    pa_ratify.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_ratify.set_defaults(func=_cmd_amend_verdict)

    pa_reject = amend_sub.add_parser(
        "reject", help="reject an amendment with a reason, as a human decision")
    pa_reject.add_argument("amendment_id", help="exact a-… id")
    pa_reject.add_argument("--note", help="why it is wrong; kept on the record")
    pa_reject.add_argument("--by", choices=["agent"], default=None,
                           help="declare yourself an agent; rejection then "
                                "refuses, because it requires a human channel")
    pa_reject.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_reject.set_defaults(func=_cmd_amend_verdict)

    pa_list = amend_sub.add_parser("list", help="list project amendments, candidates first")
    pa_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_list.add_argument("--json", action="store_true", help="machine-readable output")
    pa_list.set_defaults(func=_cmd_amend_list)

    p_relations = sub.add_parser(
        "relations",
        help="inspect and adjudicate typed item relations (#678, shadow mode)",
        epilog="Examples:\n"
               "  daimon relations list\n"
               "  daimon relations show rel-0123456789abcdef\n"
               "  daimon relations confirm rel-0123456789abcdef\n",
    )
    relations_sub = p_relations.add_subparsers(dest="relations_cmd",
                                               required=True)
    relations_sub.add_parser = functools.partial(
        relations_sub.add_parser, formatter_class=fmt)

    prl_list = relations_sub.add_parser(
        "list", help="candidates first; endpoint texts resolved at read time")
    prl_list.add_argument(
        "--state", action="append",
        choices=sorted(relations.STATES),
        help="filter by state; repeatable (default: all)")
    prl_list.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    prl_list.add_argument("--json", action="store_true", help="machine-readable output")
    prl_list.set_defaults(func=_cmd_relations_list)

    prl_show = relations_sub.add_parser(
        "show", help="one relation with its proposal history")
    prl_show.add_argument("relation_id", help="exact rel-… id")
    prl_show.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    prl_show.add_argument("--json", action="store_true", help="machine-readable output")
    prl_show.set_defaults(func=_cmd_relations_show)

    for verdict, blurb in (
            ("confirm", "record a human confirmation of a candidate edge"),
            ("reject", "record a human rejection; sticky against re-proposal"),
            ("retract", "undo a confirmation; a fresh proposal may revive it")):
        prl_verdict = relations_sub.add_parser(
            verdict,
            help=f"{blurb} (human-only: needs an interactive terminal)")
        prl_verdict.add_argument("relation_id", help="exact rel-… id")
        prl_verdict.add_argument("--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
        prl_verdict.set_defaults(func=_cmd_relations_verdict, verdict=verdict)

    p_handoff = sub.add_parser(
        "handoff",
        help="leave an authored baton for the next session — renders above "
             "everything in its next briefing (#523)",
    )
    p_handoff.add_argument("text", nargs="?", default=None,
                           help="the baton: imperative, small — what to do "
                                "first and what to beware")
    p_handoff.add_argument("--clear", action="store_true",
                           help="retract the active baton")
    p_handoff.add_argument("--project", help="project directory (default: "
                           "DAIMON_PROJECT_DIR, then cwd)")
    p_handoff.set_defaults(func=_cmd_handoff)

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

    p_audit = sub.add_parser(
        "audit",
        help="read-only auditors that verify stored guarantees",
    )
    audit_sub = p_audit.add_subparsers(dest="audit_cmd", required=True)
    pa_quotes = audit_sub.add_parser(
        "quotes",
        help="re-check stored verbatim quotes against their source transcripts "
             "and report mismatches (read-only, never rewrites tags, #125)",
        epilog="Examples:\n"
               "  daimon audit quotes\n"
               "  daimon audit quotes --all --top 20\n",
    )
    pa_quotes.add_argument(
        "--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_quotes.add_argument(
        "--all", action="store_true",
        help="audit every project's checkpoints, not just the current one")
    pa_quotes.add_argument(
        "--top", type=int, default=10,
        help="how many failing quotes to list (default: 10)")
    pa_quotes.set_defaults(func=_cmd_audit_quotes)
    pa_priv = audit_sub.add_parser(
        "privacy",
        help="prove no forgotten value's plaintext survives on any surface "
             "(read-only; exit 0 clean, 1 residue, 3 cannot-prove)",
        epilog="Examples:\n"
               "  daimon audit privacy\n"
               "  daimon audit privacy --all\n",
    )
    # Mutually exclusive: --project scopes to ONE bucket and --all audits every
    # bucket, so together one of them is silently ignored — and the flag that
    # loses decides which tombstone sets were checked. Fail loud instead.
    pa_priv_scope = pa_priv.add_mutually_exclusive_group()
    pa_priv_scope.add_argument(
        "--project", help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    pa_priv_scope.add_argument(
        "--all", action="store_true", dest="all_projects",
        help="audit every local project, each against its own tombstone set")
    pa_priv.set_defaults(func=_cmd_audit_privacy)
    # Deprecated flat alias (#504-era name). metavar on the top-level
    # subparsers (set below) keeps it out of the usage brace list.
    p_audit_old = sub.add_parser("audit-quotes")
    p_audit_old.add_argument("--project")
    p_audit_old.add_argument("--all", action="store_true")
    p_audit_old.add_argument("--top", type=int, default=10)
    p_audit_old.set_defaults(func=_cmd_audit_quotes_deprecated)

    p_heal = sub.add_parser(
        "heal",
        help="re-serialize the most recent FAILED session if it can be done safely",
    )
    p_heal.add_argument(
        "--dry-run", action="store_true",
        help="explain what heal would repair (and why not) without serializing",
    )
    p_heal.add_argument(
        "--force", action="store_true",
        help="ignore a prior retry marker and re-heal a retry-exhausted session (#15)",
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
    pt_sync.add_argument(
        "--apply-forget", action="store_true", dest="apply_forget",
        help="also rewrite THIS machine's checkpoints under teammates' forget "
             "tombstones (#600). Requires DAIMON_TEAM_APPLY_FORGET=1; typed "
             "only — the SessionStart hook spawns a bare sync, so this can "
             "never delete your belief state unattended",
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
        "--input",
        help="command: DAIMON_LLM_COMMAND_INPUT (stdin|arg|file:<flag>) — how the "
             "prompt reaches a CLI that doesn't read stdin, e.g. --input "
             "'file:--prompt-file' for the Devin CLI (#58)",
    )
    p_cfg.add_argument(
        "--init", action="store_true",
        help="guided setup wizard (#368): backend, timeout, immediate --test "
             "offer, optional team walk, ends with the status summary; every "
             "prompt has a flag escape hatch for scripts",
    )
    p_cfg.add_argument(
        "--timeout", type=int,
        help="--init: DAIMON_TIMEOUT (total serialize budget, floor 420s)",
    )
    p_cfg.add_argument("--author", help="--init: DAIMON_AUTHOR for team memory")
    p_cfg.add_argument(
        "--team-remote",
        help="--init: git remote URL — sets DAIMON_TEAM=1 and runs "
             "`daimon team init <url>`",
    )
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
        help="ship host hook scripts from the package (#43): list, install, status",
    )
    hooks_sub = p_hooks.add_subparsers(dest="hooks_cmd", required=True)
    hooks_sub.add_parser = functools.partial(hooks_sub.add_parser, formatter_class=fmt)
    ph_list = hooks_sub.add_parser("list", help="hosts with packaged hook scripts")
    ph_list.set_defaults(func=_cmd_hooks_list)
    ph_status = hooks_sub.add_parser(
        "status",
        help="audit installed hook copies against the packaged versions "
             "(CURRENT/STALE/MISSING/NOT INSTALLED); non-zero exit on drift",
    )
    ph_status.add_argument("--json", action="store_true", help="machine-readable output")
    ph_status.set_defaults(func=_cmd_hooks_status)
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

    p_mcp = sub.add_parser(
        "mcp",
        help="opt-in read-only MCP server (#261): recall/brief/projects/"
             "status as a self-describing tool surface for MCP-capable hosts")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser = functools.partial(mcp_sub.add_parser, formatter_class=fmt)
    pm_serve = mcp_sub.add_parser(
        "serve",
        help="serve MCP over stdio until EOF — reads only, never writes; "
             "register in your host's MCP config (see the docs site)")
    pm_serve.set_defaults(func=_cmd_mcp_serve)
    return parser


def main(argv=None) -> int:
    sys.excepthook = _crash_stamp_excepthook  # #92: stamp uncaught crashes
    parser = build_parser()

    # Slugs are munged absolute paths, so they START with "-" ("/Users/x" ->
    # "-Users-x") — argparse reads `--slug -Users-x` as a missing argument and
    # only accepts the `=` form. Fuse the pair pre-parse so both spellings
    # work; a trailing bare `--slug` is left for argparse to reject normally.
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    for i, tok in enumerate(argv[:-1]):
        if tok == "--slug":
            argv[i:i + 2] = [f"--slug={argv[i + 1]}"]
            break

    # #691: `daimon amend <item-id> …` is the documented propose spelling;
    # argparse subcommands need the verb word, so fuse it pre-parse. Only an
    # item-id-shaped second token is rewritten — verbs and ids cannot collide
    # (no verb matches the id shape).
    if (len(argv) > 1 and argv[0] == "amend"
            and amendments._ITEM_ID_RE.fullmatch(argv[1])):
        argv.insert(1, "propose")

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
