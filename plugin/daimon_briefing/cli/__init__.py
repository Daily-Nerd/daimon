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
import getpass  # noqa: F401 — tests patch cli.getpass.getpass
import json
import logging
import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import amendments, anchor, briefing, buckets, capture, carry, config, configure, harvest, inspector, jsonl, ledger, ledger_census, llm, normalize, privacy, provenance, recall, recall_telemetry, receipts, redact, refutations, render, requests, schema, serializer, store, teamsync, transcript, worldcheck  # noqa: F401 — several are re-exported for compat only (#708): `cli.<name>` is a stable seam
# Aliased: `trust` below (from . import (..., trust)) already binds the
# `cli.trust` VERB submodule at this scope — this is the LIBRARY ledger
# module (daimon_briefing.trust). cli/brief.py reads it directly now; the
# name stays here because tests reach it as `cli.trust_lib`.
from .. import trust as trust_lib  # noqa: F401
from .. import __version__

# The serialize.log ledger subsystem lives in ledger.py (#147 + #162, pure
# moves). EVERY moved name is re-imported here — including the ones cli.py no
# longer calls itself — because `cli.<name>` is a stable seam: tests and host
# hooks resolve the ledger through this module.
from ..ledger import (
    AUTO_BRIEF_HOSTS,  # noqa: F401 — re-exported for compat
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
    _compute_outstanding,  # noqa: F401 — re-exported for compat
    _format_age,  # noqa: F401 — re-exported for compat
    _heal_plan,
    _outstanding_failures,  # noqa: F401 — re-exported for compat
    _parse_serialize_log,  # noqa: F401 — re-exported for compat
    _parse_stamp,  # noqa: F401 — re-exported for compat
    _session_ledger,  # noqa: F401 — re-exported for compat
    _spawns_in_window,  # noqa: F401 — re-exported for compat
    _spawns_in_window_count,  # noqa: F401 — re-exported for compat
    _stats_capture,  # noqa: F401 — re-exported for compat
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


def _resolve_project(arg, *, for_write: bool = False) -> str:
    """Project dir for routing: explicit --project, else DAIMON_PROJECT_DIR, else cwd.

    Only the PRECEDENCE lives here. The resolution itself is
    `config.resolve_project_dir` — absolute, symlinks collapsed, then the git
    toplevel (#74) — which the library ledger entry points call on the same
    raw value. Holding one function means a write from a subdir and a read
    from the repo root cannot land in different buckets (#948); when this was
    two copies, only the CLI half existed.

    `allow_slug=False` because `--project` is a PATH. The library needs the
    slug-passthrough branch for its bucket-iterating readers; the CLI must not
    have it, or a slug-shaped `--project` would address that bucket on every
    verb, writes included, bypassing both the ten-verb limit `--slug` is held
    to and the tenant-scope refusal that guards it (#899).

    `for_write=True` (#1092) additionally refuses (`config.ProjectWriteRefused`,
    left for the caller to catch) a request that only resolved elsewhere
    because `Path.home()` is itself a git repository. See
    `config.resolve_project_dir_for_write`'s docstring for why this cannot be
    the default for every call site: it must never touch a read.
    """
    project = arg or config.project_dir() or os.getcwd()
    if for_write:
        return config.resolve_project_dir_for_write(project, allow_slug=False)
    return config.resolve_project_dir(project, allow_slug=False)


def loops_lists_project(project: str) -> bool:
    """True when a bare `daimon loops` run from here would list `project`'s
    items: the pointer in a briefing note is only honest then. An explicit
    --project (or --slug) that names a different project than the caller's own
    resolution means the reader would be sent to the wrong listing."""
    return project == _resolve_project(None)


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


# #919: usage.log key prefix for the per-project receipt-probe counters
# `daimon stats` renders. usage.log is otherwise a per-MACHINE log (#54's own
# design, and #477's own lesson about conflating populations) — every other
# worldcheck counter here rides it unscoped. These four fold the project
# slug into the command name itself so `_stats_receipts` can filter to just
# THIS project, the one place worldcheck's stats need to be per-project
# rather than per-machine (the issue this constant answers: a receipt probe
# fires and leaves zero trace ANYWHERE, #919's diagnosis).
_RECEIPT_PROBE_USAGE_PREFIX = "worldcheck:receipt-probe:"


def _note_receipt_probe_usage(project_dir, wc_stats: dict) -> None:
    """Fold this run's receipt-probe telemetry into usage.log, project-scoped
    (#919). `attempted`/`eligible` come from `worldcheck.last_receipt_probe()`
    — a module-level record, NOT a key in `wc_stats`, because that dict is
    asserted by exact equality across dozens of worldcheck tests and this is
    a run-level population fact, not a per-item outcome. `confirmed`/
    `contradicted`/`skipped` are read straight off `wc_stats`'s existing
    receipt-validity:<outcome> keys (already correct, just unscoped by
    project). `skipped` matters as much as the other two: it is the SILENT
    case #919 exists to expose — a probe that fired and answered nothing (no
    CLI, no pubkey, a killed deadline, garbage output), which used to read
    identically to "0 confirmed, 0 contradicted" and therefore identically to
    "did nothing". A count of zero writes nothing — `_note_usage` in a
    zero-length range is simply never called, so a disabled or silent run
    leaves the log untouched, same as every other counter here."""
    slug = store.project_slug(project_dir)
    if not slug:
        return
    probe = worldcheck.last_receipt_probe()
    counts = {
        "attempted": probe["attempted"],
        "eligible": probe["eligible"],
        "confirmed": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:confirmed", 0),
        "contradicted": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:contradicted", 0),
        "skipped": wc_stats.get(f"{worldcheck.RECEIPT_VALIDITY}:skipped", 0),
    }
    for label, count in counts.items():
        for _ in range(int(count)):
            _note_usage(f"{_RECEIPT_PROBE_USAGE_PREFIX}{label}:{slug}")


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
    if _chat is llm.chat:
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
        out = capture.run(session_id, messages, project=project, chat=_chat,
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
    return _run_serialize(Path(args.transcript), _resolve_project(args.project),
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
    project = _resolve_project(args.project)
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
                                 admit=True)
    if out is None:  # #421: write boundary refused (kill switch)
        print("error: daimon disabled (DAIMON_DISABLE) — checkpoint not written",
              file=sys.stderr)
        return 1
    render.render_write_checkpoint([f"wrote checkpoint: {out} (source: {args.source})"])
    recall.warm()  # #246: freshen off the read path; never raises
    return 0


# ---- recall: FTS search over local + team checkpoint history (#112) ----


def _refuses_caller_scope(slug=None, all_projects: bool = False) -> bool:
    """#899: on a tenant-scoped home a caller-chosen scope is refused out
    loud, rc 2, never narrowed in silence. One check for `recall`, `brief`
    and `why`, the three read verbs that take an address."""
    if config.tenant_scoped() and (slug or all_projects):
        print(f"error: {config.TENANT_SCOPE_REFUSAL}", file=sys.stderr)
        return True
    return False


SLUG_ROUTE_HELP = ("route to another project's bucket by its slug, written "
                   "--slug=<slug> (a slug starts with '-'); routing only, "
                   "the channel gate is unchanged (#766)")


def _slug_route(args) -> tuple:
    """(project, rc) for the ten human-only decision verbs (#766 slice 4).

    `--slug` is a ROUTING flag: `decide --all-projects` prints a command per
    foreign entry, and `--project` cannot carry it (it resolves a path, and
    the slug flattening is not invertible). The slug passes straight through
    as the project, which works because `store.project_slug` is idempotent on
    slugs, the same mechanism `brief --slug` relies on. It mints nothing and
    leaves every channel gate where it was; on a tenant-scoped home (#899)
    it is refused, since a caller choosing a bucket is the primitive that
    mode removes. rc is 0 on success, else the exit code to return."""
    slug = getattr(args, "slug", None)
    project_arg = getattr(args, "project", None)
    if slug and project_arg:
        print("error: --slug and --project are two answers to \"which bucket\" "
              "— pass one", file=sys.stderr)
        return None, 2
    if _refuses_caller_scope(slug):
        return None, 2
    return (slug or _resolve_project(project_arg)), 0


# ---- resolve/log: zero-LLM append-only event writers (#102) ----


# ---- #708: the refutation/ruling/amendment verb families live in their own
# modules. EVERY moved name is re-imported here — `cli.<name>` is a stable
# seam: tests and host hooks resolve these through this module, and each
# family's register() binds handlers via this namespace so a monkeypatch on
# `cli` lands on moved code too.
from ._ledger import (  # noqa: E402
    _print_refutation,  # noqa: F401 — re-exported for compat
    _print_ruling,  # noqa: F401 — re-exported for compat
    _refutation_json,  # noqa: F401 — re-exported for compat
    _refuse_ruling_id,  # noqa: F401 — re-exported for compat
    _refute_channel,  # noqa: F401 — re-exported for compat
)
from .refute import (  # noqa: E402
    _cmd_refute_add,  # noqa: F401 — re-exported for compat
    _cmd_refute_guard,  # noqa: F401 — re-exported for compat
    _cmd_refute_list,  # noqa: F401 — re-exported for compat
    _cmd_refute_overturn,  # noqa: F401 — re-exported for compat
    _cmd_refute_ratify,  # noqa: F401 — re-exported for compat
    _cmd_refute_revise,  # noqa: F401 — re-exported for compat
    _cmd_refute_search,  # noqa: F401 — re-exported for compat
    _cmd_refute_show,  # noqa: F401 — re-exported for compat
)
from .ruling import (  # noqa: E402
    _cmd_ruling_check_try,  # noqa: F401 — re-exported for compat
    _cmd_ruling_checks,  # noqa: F401 — re-exported for compat
    _cmd_ruling_list,  # noqa: F401 — re-exported for compat
    _cmd_ruling_propose,  # noqa: F401 — re-exported for compat
    _cmd_ruling_ratify,  # noqa: F401 — re-exported for compat
    _cmd_ruling_retire,  # noqa: F401 — re-exported for compat
    _cmd_ruling_revise,  # noqa: F401 — re-exported for compat
    _cmd_ruling_show,  # noqa: F401 — re-exported for compat
)
from .amend import (  # noqa: E402
    _amend_channel,  # noqa: F401 — re-exported for compat
    _cmd_amend_list,  # noqa: F401 — re-exported for compat
    _cmd_amend_propose,  # noqa: F401 — re-exported for compat
    _cmd_amend_verdict,  # noqa: F401 — re-exported for compat
)
from .ledger_cmd import (  # noqa: E402
    _cmd_ledger_repair,  # noqa: F401 — re-exported for compat
)
from .trust import (  # noqa: E402
    _cmd_trust_list,  # noqa: F401 — re-exported for compat
    _cmd_trust_propose,  # noqa: F401 — re-exported for compat
    _cmd_trust_repair,  # noqa: F401 — re-exported for compat
    _cmd_trust_show,  # noqa: F401 — re-exported for compat
    _cmd_trust_verdict,  # noqa: F401 — re-exported for compat
    _trust_channel,  # noqa: F401 — re-exported for compat
)
from .request import (  # noqa: E402
    _cmd_request_done,  # noqa: F401 — re-exported for compat
    _cmd_request_inbox,  # noqa: F401 — re-exported for compat
    _cmd_request_inject,  # noqa: F401 — re-exported for compat
    _cmd_request_list,  # noqa: F401 — re-exported for compat
    _cmd_request_open,  # noqa: F401 — re-exported for compat
    _cmd_request_reply,  # noqa: F401 — re-exported for compat
    _cmd_request_revise,  # noqa: F401 — re-exported for compat
    _cmd_request_verdict,  # noqa: F401 — re-exported for compat
    _request_channel,  # noqa: F401 — re-exported for compat
)
from .lifecycle import (  # noqa: E402
    _cmd_forget,  # noqa: F401 — re-exported for compat
    _cmd_decide,  # noqa: F401 — re-exported for compat
    _cmd_loops,  # noqa: F401 — re-exported for compat
    _cmd_resolve,  # noqa: F401 — re-exported for compat
    _cmd_reverify,  # noqa: F401 — re-exported for compat
    _is_supersede_candidate,  # noqa: F401 — re-exported for compat
)
from .audit import (  # noqa: E402
    _audit_item_source,  # noqa: F401 — re-exported for compat
    _cmd_audit_privacy,  # noqa: F401 — re-exported for compat
    _cmd_audit_quotes,  # noqa: F401 — re-exported for compat
    _cmd_audit_quotes_deprecated,  # noqa: F401 — re-exported for compat
    _legacy_audit_source,  # noqa: F401 — re-exported for compat
    _load_audit_transcript,  # noqa: F401 — re-exported for compat
    _resolve_audit_source,  # noqa: F401 — re-exported for compat
)
from .team import (  # noqa: E402
    _cmd_team_init,  # noqa: F401 — re-exported for compat
    _cmd_team_status,  # noqa: F401 — re-exported for compat
    _cmd_team_sync,  # noqa: F401 — re-exported for compat
)
from .check import (  # noqa: E402
    _cmd_check_sync,  # noqa: F401 — re-exported for compat
)
from .history import (  # noqa: E402
    _cmd_blame,  # noqa: F401 — re-exported for compat
    _cmd_diff,  # noqa: F401 — re-exported for compat
)
from .hooks import (  # noqa: E402
    _cmd_hooks_install,  # noqa: F401 — re-exported for compat
    _cmd_hooks_list,  # noqa: F401 — re-exported for compat
    _cmd_hooks_status,  # noqa: F401 — re-exported for compat
)
from .skill import (  # noqa: E402
    _cmd_skill_install,  # noqa: F401 — re-exported for compat
    _cmd_skill_list,  # noqa: F401 — re-exported for compat
    _cmd_skill_show,  # noqa: F401 — re-exported for compat
    _cmd_skill_uninstall,  # noqa: F401 — re-exported for compat
    _resolve_project_cwd,  # noqa: F401 — re-exported for compat
)
from ._hookhosts import (  # noqa: E402
    _HOOK_HOSTS,  # noqa: F401 — re-exported for compat
    _HookHostSpec,  # noqa: F401 — re-exported for compat
    _codex_registration_status,  # noqa: F401 — re-exported for compat
    _hook_drift_present,  # noqa: F401 — re-exported for compat
    _hook_file_status,  # noqa: F401 — re-exported for compat
    _hooks_status_report,  # noqa: F401 — re-exported for compat
    _hooks_target_dir,  # noqa: F401 — re-exported for compat
    _host_install_dir,  # noqa: F401 — re-exported for compat
    _host_scripts,  # noqa: F401 — re-exported for compat
    _host_status_entry,  # noqa: F401 — re-exported for compat
    _plugin_drift,  # noqa: F401 — re-exported for compat
    _plugin_drift_present,  # noqa: F401 — re-exported for compat
    _plugin_manifest_version,  # noqa: F401 — re-exported for compat
    _registration_status,  # noqa: F401 — re-exported for compat
    _skill_drift_present,  # noqa: F401 — re-exported for compat
    _version_tuple,  # noqa: F401 — re-exported for compat
)
from .stats import (  # noqa: E402
    _RETENTION_WINDOW_DAYS,  # noqa: F401 — re-exported for compat
    _cmd_stats,  # noqa: F401 — re-exported for compat
    _earlier,  # noqa: F401 — re-exported for compat
    _stats_checks,  # noqa: F401 — re-exported for compat
    _stats_events,  # noqa: F401 — re-exported for compat
    _stats_receipts,  # noqa: F401 — re-exported for compat
    _stats_resolutions,  # noqa: F401 — re-exported for compat
    _stats_retention,  # noqa: F401 — re-exported for compat
    _stats_stitching,  # noqa: F401 — re-exported for compat
    _stats_store,  # noqa: F401 — re-exported for compat
    _stats_usage,  # noqa: F401 — re-exported for compat
    _stats_verification,  # noqa: F401 — re-exported for compat
)
from .configure_cmd import (  # noqa: E402
    _ask_backend_updates,  # noqa: F401 — re-exported for compat
    _cmd_configure,  # noqa: F401 — re-exported for compat
    _configure_flag_updates,  # noqa: F401 — re-exported for compat
    _configure_wizard,  # noqa: F401 — re-exported for compat
    _configure_wizard_flags,  # noqa: F401 — re-exported for compat
    _configure_write_flags,  # noqa: F401 — re-exported for compat
    _run_backend_test,  # noqa: F401 — re-exported for compat
)
from .status import (  # noqa: E402
    _CAPTURE_MIN_SESSIONS,  # noqa: F401 — re-exported for compat
    _capture_alarm,  # noqa: F401 — re-exported for compat
    _checkpoint_info,  # noqa: F401 — re-exported for compat
    _cmd_mcp_serve,  # noqa: F401 — re-exported for compat
    _cmd_status,  # noqa: F401 — re-exported for compat
    _cmd_verify_receipt,  # noqa: F401 — re-exported for compat
    _crash_log_info,  # noqa: F401 — re-exported for compat
    _created_epoch,  # noqa: F401 — re-exported for compat
    _print_suppressed,  # noqa: F401 — re-exported for compat
    _status_checks,  # noqa: F401 — re-exported for compat
    _status_health,  # noqa: F401 — re-exported for compat
    _status_ledgers,  # noqa: F401 — re-exported for compat
    _status_world,  # noqa: F401 — re-exported for compat
    _tail_log_info,  # noqa: F401 — re-exported for compat
    _write_worldcheck_ledger,  # noqa: F401 — re-exported for compat
    status_payload,  # noqa: F401 — re-exported for compat
)
from .inject import (  # noqa: E402
    _ACTION_BUDGET,  # noqa: F401 — re-exported for compat
    _AGE_GATE_DAYS,  # noqa: F401 — re-exported for compat
    _DEFAULT_MCP_TOOL_NAME,  # noqa: F401 — re-exported for compat
    _INJECT_BUDGET,  # noqa: F401 — re-exported for compat
    _INJECT_FETCH,  # noqa: F401 — re-exported for compat
    _LEAD_WIDTH,  # noqa: F401 — re-exported for compat
    _MCP_TOOL_NAME_RE,  # noqa: F401 — re-exported for compat
    _ORIGIN_BUDGET,  # noqa: F401 — re-exported for compat
    _SEEN_PRUNE_SECONDS,  # noqa: F401 — re-exported for compat
    _SLOT_WIDTH,  # noqa: F401 — re-exported for compat
    _STALE_MIN_HITS,  # noqa: F401 — re-exported for compat
    _UNSET,  # noqa: F401 — re-exported for compat
    _choose_recall_rows,  # noqa: F401 — re-exported for compat
    _cmd_recall_inject,  # noqa: F401 — re-exported for compat
    _fit_item_text,  # noqa: F401 — re-exported for compat
    _inject_age_bucket,  # noqa: F401 — re-exported for compat
    _load_seen,  # noqa: F401 — re-exported for compat
    _row_age_days,  # noqa: F401 — re-exported for compat
    _save_seen,  # noqa: F401 — re-exported for compat
    _save_seen_atomic,  # noqa: F401 — re-exported for compat
    _seen_path,  # noqa: F401 — re-exported for compat
    _suggest_line,  # noqa: F401 — re-exported for compat
    age_gate_blocks,  # noqa: F401 — re-exported for compat
    cooled_origins,  # noqa: F401 — re-exported for compat
)
from .action_recall import (  # noqa: E402
    _ACTION_VERBS,  # noqa: F401 — re-exported for compat
    _ACTION_VERB_PAIRS,  # noqa: F401 — re-exported for compat
    _HEREDOC_MARK,  # noqa: F401 — re-exported for compat
    _action_query_text,  # noqa: F401 — re-exported for compat
    _action_verb,  # noqa: F401 — re-exported for compat
    _cmd_action_recall,  # noqa: F401 — re-exported for compat
    _is_env_assignment,  # noqa: F401 — re-exported for compat
)
from .handoff import (  # noqa: E402
    _HANDOFF_MAX_CHARS,  # noqa: F401 — re-exported for compat
    _cmd_handoff,  # noqa: F401 — re-exported for compat
    _cmd_log,  # noqa: F401 — re-exported for compat
)
from .relations_cmd import (  # noqa: E402
    _cmd_relations_list,  # noqa: F401 — re-exported for compat
    _cmd_relations_show,  # noqa: F401 — re-exported for compat
    _cmd_relations_verdict,  # noqa: F401 — re-exported for compat
    _relations_channel,  # noqa: F401 — re-exported for compat
    _relations_endpoint_texts,  # noqa: F401 — re-exported for compat
)
from .projects import (  # noqa: E402
    _TOPIC_TEASER_CHARS,  # noqa: F401 — re-exported for compat
    _bucket_migrate_lines,  # noqa: F401 — re-exported for compat
    _cmd_bucket_migrate,  # noqa: F401 — re-exported for compat
    _cmd_projects,  # noqa: F401 — re-exported for compat
    _cmd_slug,  # noqa: F401 — re-exported for compat
    _migrate_command,  # noqa: F401 — re-exported for compat
    _raw_project,  # noqa: F401 — re-exported for compat
    _topic_teaser,  # noqa: F401 — re-exported for compat
    projects_rows,  # noqa: F401 — re-exported for compat
)
from .search import (  # noqa: E402
    _cmd_recall,  # noqa: F401 — re-exported for compat
    _cmd_serve,  # noqa: F401 — re-exported for compat
    _cmd_why,  # noqa: F401 — re-exported for compat
)
from .brief import (  # noqa: E402
    _cmd_anchor,  # noqa: F401 — re-exported for compat
    _cmd_brief,  # noqa: F401 — re-exported for compat
    _render_briefing_body,  # noqa: F401 — re-exported for compat
    _team_briefings,  # noqa: F401 — re-exported for compat
)
from . import (  # noqa: E402
    action_recall,
    amend,
    audit,
    brief,
    check,
    configure_cmd,
    handoff,
    history,
    hooks,
    inject,
    ledger_cmd,
    lifecycle,
    projects,
    refute,
    relations_cmd,
    request,
    ruling,
    search,
    skill,
    stats,
    status,
    team,
    trust,
)


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
               "  daimon configure             detect/repair the LLM backend\n"
               "\n"
               "Docs:   https://daily-nerd.github.io/daimon/\n"
               "Issues: https://github.com/Daily-Nerd/daimon/issues\n",
        formatter_class=fmt,
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<command>")
    # Rebinding the bound method is the point: every subparser below then
    # gets `fmt` without repeating it at ~90 call sites.
    sub.add_parser = functools.partial(  # type: ignore[method-assign]
        sub.add_parser, formatter_class=fmt)

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

    brief.register(sub, fmt)


    search.register(sub, fmt)


    history.register(sub, fmt)

    search.register_serve(sub, fmt)


    projects.register(sub, fmt)


    lifecycle.register(sub, fmt)

    refute.register(sub, fmt)

    ruling.register(sub, fmt)

    amend.register(sub, fmt)

    trust.register(sub, fmt)

    ledger_cmd.register(sub, fmt)

    request.register(sub, fmt)

    relations_cmd.register(sub, fmt)


    handoff.register(sub, fmt)


    inject.register(sub, fmt)


    action_recall.register(sub, fmt)


    # #756: the second UserPromptSubmit backend, top-level beside
    # `recall-inject` rather than under `request` — it is a hook backend, not
    # one of the request object's verbs, and the command-catalogue guard
    # (#650) only partitions the TOP-LEVEL surface, so a subcommand here
    # would reach no skill and trip no test.
    p_rq_inject = sub.add_parser(
        "request-inject",
        help="live-delivery backend for the UserPromptSubmit hook (#756): "
             "prints undecided asks this session has not been shown, rc 0 always",
    )
    p_rq_inject.add_argument("--project", default=None,
                             help="project dir for scoping (defaults to cwd detection)")
    p_rq_inject.add_argument("--session", default=None,
                             help="current session id (half the delivery write-once key)")
    p_rq_inject.set_defaults(func=_cmd_request_inject)

    status.register(sub, fmt)


    audit.register(sub, fmt)

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

    team.register(sub, fmt)

    configure_cmd.register(sub, fmt)


    stats.register(sub, fmt)


    check.register(sub, fmt)

    hooks.register(sub, fmt)

    skill.register(sub, fmt)

    status.register_mcp(sub, fmt)

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
