"""`daimon serialize`, `write-checkpoint` and `heal` (moved out of cli/__init__.py, #1132 PR 5).

The serialize run, its preflight and log handler, the introspection
checkpoint writer and the heal verb that re-runs a failed serialize. Names
tests patch (`_chat`, `_run_serialize`, `_heal_plan`, `_append_retry_log`) are
reached as `_cli.<name>` at call time. Every name is re-exported from `cli`.
"""

import json
import logging
import sys
import time
import uuid
from pathlib import Path

import daimon_briefing.cli as _cli

from .. import (
    capture,
    config,
    harvest,
    jsonl,
    ledger,
    llm,
    recall,
    render,
    serializer,
    store,
    transcript,
)
from ..ledger import _append_serialize_log
from ..surfaces import Writer


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
    # Marker attribute, deliberately hung on the handler so a later lookup
    # can recognise OUR file handler among any the host installed.
    handler._daimon_serialize_log = str(target)  # type: ignore[attr-defined]
    pkg_log.addHandler(handler)
    # INFO must pass the logger-level gate too (default effective WARNING) —
    # the chunked-serialize heartbeat is the reason this log exists at all.
    pkg_log.setLevel(logging.INFO)


def _print_error(msg: str) -> None:
    """Print one FAILED serialize result line, mirroring it onto stdout when a
    host opted in (#939).

    Success and skip lines already print to stdout unaided, so the error lines
    were the only ones missing from the surface a container runtime collects,
    which made a failed capture the one outcome an operator could not see.
    There is deliberately no success branch here: a wrapper with a path no
    caller takes is dead code that reads as covered behavior.

    The mirrored string is the same `msg` that reaches _append_serialize_log,
    byte for byte, so the raw timestamp-free result regexes keep matching it
    and the line still counts in `daimon stats` and `daimon status`.
    """
    print(msg, file=sys.stderr)
    if config.log_stdout():
        print(msg)


def _run_serialize(transcript_path: Path, project: str | None,
                   escalate: bool = False, session: str | None = None) -> int:
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
    # #988: the filename names the session on every host adapted before Kimi
    # Code, and on Kimi it does not — the session id is a DIRECTORY and the
    # file inside it is always `wire.jsonl`. Without the override every Kimi
    # session on the machine would serialize under the id "wire": one
    # per-session checkpoint overwritten by each new session, and a heartbeat
    # under a constant name that makes any live serialize look like every
    # other session's (scar 0061). Same rule #983 set for `write-checkpoint`:
    # when the host can supply a real session id, that id wins over an
    # inferred one.
    session_id = (session or "").strip() or path.stem

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
        detailed = transcript.from_file_detailed(path)
        messages = detailed["messages"]
        coverage = detailed["coverage"]
    except FileNotFoundError:
        msg = f"error: transcript not found: {path}"
        _print_error(msg)
        _append_serialize_log(msg)
        return 2

    # Pre-flight missing credentials so the error names them before any LLM work
    # (a conflated message cost a live debugging round-trip — see PR #12 fallout).
    if _cli._chat is llm.chat:
        preflight = _preflight_error(path)
        if preflight is not None:
            _print_error(preflight)
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
        out = capture.run(session_id, messages, project=project, chat=_cli._chat,
                          deadline=deadline, transcript_path=path,
                          transcript_sha=transcript_sha, escalate=escalate,
                          coverage=coverage)
    except serializer.TooShortError as exc:
        msg = f"skipped serialize for {session_id}: {exc}"
        print(msg)
        _append_serialize_log(msg)
        return 0
    except serializer.SerializeError as exc:
        elapsed = int(time.monotonic() - start)
        msg = f"error: {exc} (transcript: {path}) after {elapsed}s"
        _print_error(msg)
        _append_serialize_log(msg)
        return 1
    except jsonl.Refused as exc:
        # D10.4: a refused admission is an ORDINARY failed serialize: the same
        # result line every fold already parses, so `status` counts it, the
        # briefing names it and `heal` retries it. The session group sits
        # BEFORE the transcript group (the transcript regex swallows anything
        # after the path) and is printed only when the host named the session
        # (`--session`), which keys the line to the real session rather than
        # to the transcript stem (Kimi, #988).
        elapsed = int(time.monotonic() - start)
        named = (session or "").strip()
        group = f" (session: {named})" if named else ""
        msg = f"error: {exc}{group} (transcript: {path}) after {elapsed}s"
        _print_error(msg)
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
    return _cli._run_serialize(Path(args.transcript), _cli._resolve_project(args.project),
                          session=getattr(args, "session", None))


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
    # #983: the live session's REAL id, when the host can supply one, always
    # wins over whatever the model put in the JSON body — never trust a
    # model-authored session_id for identity, the same discipline
    # strip_code_owned_keys applies a few lines down to format_version/author/
    # created. Before this flag existed, the daimon-end skill instructed the
    # model to INVENT one (`introspection-<short-unique-id>`), so a
    # provisional and the SessionEnd reconstruction of the very same session
    # carried two different ids — the one thing that let carry.py's G2
    # same-session guard (origin_session == observing session) miss every
    # self-corroboration, since the guard never saw the two halves as one.
    session_override = str(getattr(args, "session", "") or "").strip()
    if isinstance(checkpoint, dict) and session_override:
        checkpoint["session_id"] = session_override
    elif isinstance(checkpoint, dict):
        # #983 B1: no --session (a host with no live-session-id concept at
        # all, e.g. Codex or Windsurf) leaves this branch as the ONLY guard
        # against a PLACEHOLDER-shaped session_id ever reaching disk. The
        # skill's own JSON template still shows "session_id":
        # "introspection-<short-unique-id>" so a model can see the shape —
        # but a model that pipes that literal text through unfilled (or
        # leaves session_id blank/absent) writes a CONSTANT id, verified
        # with the real CLI: it lands at .../introspection-<short-unique-
        # id>.json, and every subsequent /daimon-end on that host — any
        # project — overwrites that SAME file. That is strictly worse than
        # the pre-#983 behavior (a model-invented, at least locally unique,
        # id): it does not merely fail to link a provisional to its own
        # reconstruction, it destroys the previous provisional outright.
        # A body id that is present, non-blank, and free of angle brackets
        # is left untouched — this only replaces something that could never
        # have been a usable id.
        body_sid = str(checkpoint.get("session_id") or "").strip()
        if not body_sid or "<" in body_sid or ">" in body_sid:
            checkpoint["session_id"] = (
                f"{store.ORIGIN_SESSION_INTROSPECTION_PREFIX}{uuid.uuid4().hex}")
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
    project = _cli._resolve_project(args.project)
    # #811: this write rotates the pointer chain exactly like a serialize, and
    # `brief` reads `latest` alone (never prev-N). Without carry it therefore
    # ENDS the reachable history of whatever it displaces. The docstring above
    # reasons that is safe because a later reconstruction supersedes it — true
    # only while `latest` holds this session's own earlier state. Two sessions
    # in one bucket break that assumption and the earlier one is orphaned.
    #
    # Placement is load-bearing: strictly AFTER the four provenance strips, and
    # #511 in particular. Carry's #22 freeze prefers `verbatim`, so carrying
    # before downgrade_unverifiable_verbatim would let a model-claimed verbatim
    # this path can never check beat genuinely extracted prev content.
    checkpoint = capture.carry_forward(checkpoint, project)
    # admit=True (#693): the stdin path is the second admission caller — a
    # model-authored checkpoint passes the ruling echo filter like capture's.
    out = store.write_checkpoint(session_id, checkpoint,
                                 project_dir=project,
                                 admit=True, writer=Writer.ADMISSION)
    if out is None:  # #421: write boundary refused (kill switch)
        print("error: daimon disabled (DAIMON_DISABLE) — checkpoint not written",
              file=sys.stderr)
        return 1
    render.render_write_checkpoint([f"wrote checkpoint: {out} (source: {args.source})"])
    recall.warm()  # #246: freshen off the read path; never raises
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
    # D9.6: a per-store index whose store is gone, or that nobody opened in
    # 30 days, is dead weight holding a plaintext copy.
    for p in recall.reap_stale_caches(apply=not dry_run):
        print(f"{'would reap' if dry_run else 'reaped'} "
              f"stale recall cache: {p.name}")
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
    plan = _cli._heal_plan(text, now, force=force)
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
    _cli._append_retry_log(t["sid"], prior)
    # #360: the default retry re-runs the SAME extraction shape that already
    # failed. Opt-in escalation (DAIMON_HEAL_ESCALATION) re-serializes from
    # multiple perspectives instead — heal-path only, so the extra token cost
    # scales with failure, never with usage.
    escalate = config.heal_escalation_enabled()
    # The session id goes along only when it is not what the transcript's own
    # name says: Kimi (every file is `wire.jsonl`, #988) yes, Codex (its id is
    # the rollout stem's `_session_key`) and Claude no, so a healed Codex
    # checkpoint keeps its `rollout-...` name and the sweep and the identical-
    # bytes guard still find it.
    session = (t["sid"] if t["sid"] != ledger._session_key(transcript_path.stem)
               else None)
    with render.working(f"healing {t['sid']} — re-serializing transcript"):
        if session is None:
            return _cli._run_serialize(transcript_path, t["project"],
                                       escalate=escalate)
        return _cli._run_serialize(transcript_path, t["project"],
                                   escalate=escalate, session=session)


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
    p_ser = sub.add_parser("serialize", help="serialize a transcript file into a checkpoint")
    p_ser.add_argument("transcript", help="path to a text/markdown transcript")
    p_ser.add_argument(
        "--project",
        help="project directory to route the checkpoint to "
        "(default: DAIMON_PROJECT_DIR, then cwd)",
    )
    p_ser.add_argument(
        "--session",
        help="session id for this transcript, overriding the filename (#988: "
             "a host whose transcript is not named for its session, such as "
             "Kimi Code, where every file is `wire.jsonl`)",
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
    p_wc.add_argument(
        "--session",
        help="the live session's REAL id (e.g. $CLAUDE_CODE_SESSION_ID), "
        "stamped onto the checkpoint in place of whatever session_id the "
        "JSON body carries (#983). This is what lets carry.py's same-session "
        "guard recognize a later reconstruction of THIS session as its own "
        "predecessor instead of an independent witness, so a provisional "
        "can never corroborate itself. Omit only when the host has no way "
        "to tell the live session its own id.",
    )
    p_wc.set_defaults(func=_cmd_write_checkpoint)


def register_heal(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
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
    p_heal.set_defaults(func=_cli._cmd_heal)
