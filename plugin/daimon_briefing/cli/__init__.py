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

from .. import amendments, anchor, briefing, buckets, capture, carry, config, configure, harvest, inspector, jsonl, ledger, ledger_census, llm, normalize, privacy, provenance, recall, recall_telemetry, receipts, redact, refutations, relations, render, requests, schema, serializer, store, teamsync, transcript, worldcheck  # noqa: F401 — several are re-exported for compat only (#708): `cli.<name>` is a stable seam
# Aliased: `trust` below (from . import (..., trust)) already binds the
# `cli.trust` VERB submodule at this scope — this is the LIBRARY ledger
# module (daimon_briefing.trust), needed here only to read
# active_value_keys() for withhold's quarantine pool.
from .. import trust as trust_lib
from .. import __version__
from ..display import one_line

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
    _format_age,
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
    # #789: this caller PERSISTS what it reads, so it takes Route.OWN, the
    # route named for exactly that class (#94). With the global
    # fallback left on, a project with no bucket of its own re-wrote ANOTHER
    # project's checkpoint into its bucket under that project's session_id, and
    # the project that owns the item never received the anchor while the command
    # reported success. Refusing is the correct outcome: there is nothing here to
    # attach to, and the message below already says so.
    checkpoint = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                        admit=store.Admit.ANY)
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


def _team_briefings(project, withheld: list | None = None) -> list:
    """Per-teammate briefing sections for `brief --team`, EXCLUDING the current
    author. Returns [(author, sections), ...] newest-first, or [] when the team dir
    is empty (nothing was ever mirrored). Reuses briefing.build so the #77 decision
    cap applies to teammates identically. Self is matched by slug — the same dir
    identity read_team fans in on.

    #981: each teammate checkpoint is folded through `briefing.withhold` BEFORE
    it is built, so a resolved item neither prints under Teammates nor takes a
    capped slot — the same fold every other briefing surface applies. The
    ledger that governs is the READER's own (`store.resolutions(project)`):
    what the reader resolved is what the reader stops seeing, on every
    surface including this one; a teammate's own ledger is theirs and is not
    read here. Fail-open like the main path: an unreadable ledger withholds
    nothing rather than dropping the section. `withheld` is an optional
    out-list the caller can hand in to fold the count into its note.
    """
    # project_slug munging, matching _dual_write_team's dir identity — _safe_name
    # would re-introduce the "a/b" == "a_b" collision on the self-match.
    self_slug = store.project_slug(config.author())
    # Read once, before the fan-in: one ledger read for every teammate, and
    # a failure here is this function's own to swallow (the fan-in's own
    # tombstone read of the same ledger keeps its own contract).
    try:
        resolutions = store.resolutions(project_dir=project)
    except Exception:
        resolutions = {}
    try:
        # #1109: the READER's own quarantine ledger governs here too, same
        # posture as `resolutions` above — a teammate's checkpoint is folded
        # through what THIS project's human has quarantined, never theirs.
        quarantine = trust_lib.active_value_keys(project_dir=project)
    except Exception:
        quarantine = set()
    out = []
    for author, checkpoint in store.read_team(project_dir=project):
        if store.project_slug(author) == self_slug:
            continue  # never surface your own state as a teammate
        try:
            checkpoint, dropped, _candidates = briefing.withhold(
                checkpoint, resolutions, quarantine=quarantine)
        except Exception:
            dropped = []
        if withheld is not None:
            withheld.extend(dropped)
        b = briefing.build(checkpoint)
        if b is None:
            continue  # nothing worth surfacing for this teammate
        out.append((author, b))
    return out


def _render_briefing_body(checkpoint, route, *, drift_project, teammates,
                          worldcheck_project=None, team_withheld=(),
                          loops_pointer=True) -> int:
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
    withheld: list = []

    if checkpoint:
        # #1128: withhold (#103), corroboration (#268), stale stamping (#977)
        # and the optional worldcheck spot-check (#365/#397/#439) all live in
        # briefing.annotate now, shared with the MCP tool and the Hermes hook.
        # Each step is fail-open inside it. Worldcheck is opt-in, budget-
        # bounded and read-only; it only RETURNS its counters and ledger rows,
        # and the writes below stay here, where the project route is already
        # resolved (worldcheck writes nothing to disk by contract).
        annotated = briefing.annotate(
            checkpoint,
            briefing.AnnotateContext(route=route,
                                     worldcheck_project=worldcheck_project),
            time.time())
        checkpoint = annotated.checkpoint
        withheld = annotated.withheld

        if annotated.worldcheck is not None:
            try:
                wc_stats = annotated.worldcheck
                # #397: the dict carries the aggregate outcomes AND a
                # "<class>:<outcome>" key per class, so one pass emits both the
                # slice-1 counters (unchanged meaning) and the per-class
                # fires-true rate the next expansion gate reads.
                for counter, count in sorted(wc_stats.items()):
                    for _ in range(int(count)):
                        _note_usage(f"worldcheck:{counter}")
                # #919: the receipt-probe axis, project-scoped (see the
                # helper's own docstring for why this one axis needs project
                # scope where the loop above deliberately stays machine-wide).
                _note_receipt_probe_usage(worldcheck_project, wc_stats)
                # A POINTER and a REASON CODE, never the item's text (#376) —
                # the same second stream capture writes, for the same reason:
                # folded into events.jsonl a rejection would HIDE the item it
                # describes.
                _write_worldcheck_ledger(annotated.ledger_rows, route)
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
    trailer = []
    if withheld or team_withheld:
        # #981: the count covers the Teammates section too, and says how
        # many were a teammate's, since `status --suppressed` lists only the
        # reader's own checkpoint.
        note = f"{len(withheld) + len(team_withheld)} resolved item(s) withheld"
        if team_withheld:
            note += f" ({len(team_withheld)} a teammate's)"
        trailer.append(note + " — `daimon status --suppressed` to list")
    # #1128: the note rides INTO render_brief so it is charged to the same
    # byte budget as the body, HANDOFF and teammates. `printed` is what the
    # budgeted brief actually showed of each panel (None: everything).
    printed = render.render_brief(checkpoint, drift=drift, teammates=teammates,
                                  handoff=handoff, project_dir=route,
                                  worldcheck_project=worldcheck_project,
                                  trailer=trailer,
                                  loops_pointer=loops_pointer)

    def _shown(panel, row) -> bool:
        # #1128: `printed` is the manifest of card ids render_brief printed in
        # full. A row the budget cut (its panel collapsed to a count line)
        # never reached the reader, so it is not stamped as surfaced; no
        # manifest means nothing is known to have been shown.
        return row["request_id"] in ((printed or {}).get(panel) or ())
    # #694 PR 2 (D1): the surfaced stamp, AFTER the render+print pipeline
    # above completes — the card has already reached the terminal, so a
    # crash between here and the write below just re-renders it next brief
    # (the safe direction) rather than a false "surfaced". Gated on the same
    # `worldcheck_project` parameter as the panel itself (D2) — never on
    # `route`, which is set on every path including --slug. Fail-open, same
    # posture as every other best-effort block in this function: a broken
    # composer must never take the briefing down.
    if worldcheck_project is not None:
        try:
            # #961 slice 3 review item 1: `decision_renderable`, not
            # `inbox_renderable` — the panel this stamp records as "shown"
            # is the one `request_panel_lines` actually reads
            # (briefing.py), and that one excludes `kind == "info"`. An
            # `info` ask stamped `surfaced` here would give `is_stale` an
            # anchor for a card that was never printed, decaying the ask
            # before anyone saw it (`is_stale`'s own `kind == "info"`
            # branch anchors on `delivered` instead, precisely because this
            # loop no longer stamps `surfaced` for one).
            for row in requests.decision_renderable(
                    project_dir=worldcheck_project).get("rows") or []:
                if requests.needs_surfaced_stamp(row) and _shown("request", row):
                    requests.stamp_surfaced(row["request_id"],
                                            project_dir=worldcheck_project)
        except Exception:
            pass
        # #694 PR 3 (D1, sender side): same posture, same gate, same
        # post-print timing — a crash before this line just re-renders the
        # verdict card next brief instead of a false "verdict_surfaced".
        try:
            for row in requests.verdict_renderable(
                    project_dir=worldcheck_project).get("rows") or []:
                # #1117: one stamp row carries whichever of the epoch and the
                # late reply the brief just showed.
                if not _shown("verdict", row):
                    continue
                reply_id = requests.unseen_reply_id(row)
                if requests.needs_verdict_surfaced_stamp(row) or reply_id:
                    requests.stamp_verdict_surfaced(
                        row["request_id"], project_dir=worldcheck_project,
                        reply_event_id=reply_id)
        except Exception:
            pass
    # #1128: the standing ">N days unverified" footer is gone. The stale
    # items carry [? unverified] marks in the body, and a section that hid
    # stale carried items says so in its own note, so the footer repeated
    # (and sometimes contradicted) what the sections already state.
    return 0


def _cmd_brief(args) -> int:
    _note_usage("brief:auto" if getattr(args, "auto", False) else "brief")
    slug = getattr(args, "slug", None)
    if _refuses_caller_scope(slug):
        return 2
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
        # `slug` is a bare slug string, not a path — this survives because
        # project_slug is idempotent on slugs (pinned by its own test).
        checkpoint = store.read_latest_body(project_dir=slug, route=store.Route.OWN,
                                            admit=store.Admit.ANY)
        if not isinstance(checkpoint, dict):
            render.render_brief_note([
                f"no checkpoint bucket for slug {slug} — "
                "`daimon projects` lists what exists"])
            return 1
        render.render_brief_note([f"cross-project briefing — project: {slug}"])
        # A named bucket is somebody else's listing: no `daimon loops` pointer.
        return _render_briefing_body(checkpoint, slug,
                                     drift_project=None, teammates=None,
                                     loops_pointer=False)
    # Route like status/serialize: --project, else DAIMON_PROJECT_DIR, else cwd.
    # read_latest still falls back to the global pointer if the project has none.
    project = _resolve_project(args.project)
    # #787/#795: whether the fallback fired is what the read DID, not what the
    # filesystem shows — and the route fact is now READ off the result, never
    # reconstructed from a second look (scar 0058's class). Two invariants the
    # diff does not show: under Admit.ANY nothing is ever refused, so
    # fell_back=True implies checkpoint is not None (the old second conjunct
    # is implied, not dropped); and brief cannot be identity-less, because
    # _resolve_project returns str(Path(...).resolve()) — never empty — and
    # resolve_project_root ends `return top or raw`, so the no-slug rows of
    # the read contract are unreachable on this path.
    got = store.read_latest_result(project_dir=project,
                                   route=store.Route.OWN_ELSE_GLOBAL,
                                   admit=store.Admit.ANY)
    checkpoint = got.checkpoint
    fallback_used = got.fell_back
    if fallback_used and not (getattr(args, "global_fallback", False)
                              or config.brief_global_fallback()):
        # Header-only fallback (#96): the foreign body is suppressed — one
        # warning line above a hundred foreign lines does not read as a
        # warning. Orient (where the activity actually is) and exit clean;
        # `daimon status` still shows the full pointer table.
        # `checkpoint` is not None here: see the Admit.ANY reasoning on the
        # read above. mypy cannot carry that across the ReadResult, and the
        # conjunct that would re-narrow it was removed there as implied.
        slug = str(checkpoint.get("project_slug") or "").strip() or "another project"  # type: ignore[union-attr]
        epoch = store._created_epoch(checkpoint.get("created"))  # type: ignore[union-attr]
        age = f"{_format_age(time.time() - epoch)} ago" if epoch else "age unknown"
        # #740: a baton left for a checkpoint-less project is the only
        # orientation it has — status says "waiting baton"; brief must not
        # swallow it on the header-only path. Read-only: consumption stays
        # serialize-count-based in store.active_handoff.
        render.render_handoff(store.active_handoff(project))
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
            team_withheld: list = []
            render.render_teammates(_team_briefings(project, team_withheld))
            if team_withheld:
                render.render_brief_note([
                    f"{len(team_withheld)} resolved item(s) withheld "
                    "(a teammate's)"])
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
    team_withheld = []
    teammates = (_team_briefings(project, team_withheld)
                 if getattr(args, "team", False) else None)
    # #365: never worldcheck a fallback body — the global pointer may belong
    # to ANOTHER project, and probing this cwd's repo against that
    # checkpoint's claims answers for the wrong repo.
    return _render_briefing_body(checkpoint, project,
                                 drift_project=project, teammates=teammates,
                                 worldcheck_project=None if fallback_used
                                 else project, team_withheld=team_withheld,
                                 loops_pointer=(not fallback_used
                                                and loops_lists_project(project)))


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
    if _refuses_caller_scope(slug, args.all_projects):
        return 2
    project = _resolve_project(args.project)
    try:
        results = recall.search(query, project_dir=project, slug=slug,
                                all_projects=args.all_projects, limit=args.limit)
    except recall.RecallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    recall_telemetry.record(
        results,
        query_terms=recall.salient_terms(query),
        surface="recall-search",
        via="cli",
    )
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
        # never mean a silent dead end. #899: except on a tenant-scoped
        # home, where per-slug counts are enumeration and the remedy the
        # signpost names is the refused flag.
        if not args.all_projects and not slug and not config.tenant_scoped():
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
        # #865: name the WRITER, not just the value. A model-authored
        # supersedes link and a human `daimon resolve` both land here and
        # both can write a bare id, so the marker rendered a claim and an
        # action identically. `resolved` is the one value that was already
        # unambiguous, and only by accident of its spelling.
        # #1079: the phrase itself lives in recall.describe_supersession —
        # the MCP `daimon_recall` tool shares this exact wording.
        sup_phrase = recall.describe_supersession(r)
        superseded = f" [{sup_phrase}]" if sup_phrase else ""
        # #837: an independent axis gets an independent marker — a row can
        # carry both, and collapsing them would hide one fact behind the
        # other. recall owns the phrasing so this marker can never describe a
        # different view than the fold recorded.
        inv = recall.describe_invalidation(r.get("invalidated_by"))
        contradicted = f" [{inv}]" if inv else ""
        # #866: the release from burial is a fact too. A cured item read
        # exactly like one nothing ever questioned, which is the same
        # collapse the contradiction marker exists to prevent, inverted.
        cured = recall.describe_cure(r.get("cured_by"))
        contradicted += f" [{cured}]" if cured else ""
        trust = r.get("trust") or "untagged"
        item_id = f" [{r['item_id']}]" if r.get("item_id") else ""
        # #889: [author] reads like attribution and is not — config.author()
        # is a machine identity, one constant across every project here. The
        # row has always known its origin project; only --json ever showed it,
        # so a foreign hit and a local one rendered identically.
        scope = recall.describe_scope(r, store.project_slug(project))
        scope_mark = f" ({scope})" if scope else ""
        # #890: whose STATEMENT this is, when the record names one. Absent
        # means unknown and renders as nothing — never a guess and never a
        # placeholder, because a default would read as the reader's own claim.
        stated = str(r.get("stated_by") or "").strip()
        stated_mark = f" (stated by {stated})" if stated else ""
        lines.append(f"[{r['author']}] [{trust}] [{r['kind']}]{item_id} {r['text']} "
                     f"({r['session_id']}, {age} ago){stated_mark}{scope_mark}"
                     f"{superseded}{contradicted}")
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
    if _refuses_caller_scope(args.slug):
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


def _topic_teaser(topic) -> str:
    """The topic on one line, at most `_TOPIC_TEASER_CHARS` wide. Kept off
    `display.shorten`: this cut is `[:CHARS - 1]` with NO rstrip and fires
    only above CHARS, a contract `shorten(hard=True)` cannot reproduce byte
    for byte."""
    flat = one_line(topic)
    if len(flat) > _TOPIC_TEASER_CHARS:
        flat = flat[:_TOPIC_TEASER_CHARS - 1] + "…"
    return flat


def projects_rows(project_arg=None) -> list:
    """One JSON-ready row per checkpoint bucket, newest first. Single
    assembler for `daimon projects --json` AND the MCP projects tool (#261) —
    two consumers, one shape. Torn buckets show with unknown fields rather
    than vanish: hiding one would read as "no such project"."""
    cur_slug = store.project_slug(_resolve_project(project_arg))
    rows = []
    for b in store.list_buckets():
        # #899: enumeration is the other half of the exfiltration primitive;
        # a tenant-scoped home lists the caller's own bucket and no other.
        if config.tenant_scoped() and b["slug"] != cur_slug:
            continue
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


def _cmd_slug(args) -> int:
    """Print the checkpoint bucket name daimon derives from a project path
    (#913). No store, config, or ledger access — deliberately: a host that
    wants this name before the first write (to lay out a fresh volume, or
    start a watcher on a directory daimon has not touched yet) has no bucket
    to list, so this must answer without one, unlike `projects` below."""
    slug = store.project_slug(args.path)
    if not slug:
        print("error: path must not be empty or whitespace-only", file=sys.stderr)
        return 2
    print(slug)
    return 0


def _raw_project(arg) -> str:
    """The project value BEFORE resolution — the exact string a pre-0.42.0
    write would have slugged (#963).

    `_resolve_project` above collapses symlinks and walks to the git toplevel,
    which is precisely the information the legacy bucket rule needs and the
    resolved rule discards. Every surface that reports on a legacy bucket
    reads this, and every surface that ROUTES still reads `_resolve_project`:
    the two are deliberately not interchangeable. The fallback chain is the
    same one `_resolve_project` uses, so both halves answer for one project.
    """
    return arg or config.project_dir() or os.getcwd()


def _migrate_command(project_path) -> str:
    """The runnable form of "migrate this bucket", for whichever mode the
    home is in. Under DAIMON_TENANT_SCOPED an explicit --project is refused at
    rc 2, so printing it hands the reader a command that cannot work."""
    if config.tenant_scoped():
        return ("run daimon bucket migrate with DAIMON_PROJECT_DIR set to "
                f"{project_path}")
    return f"run daimon bucket migrate --project {project_path}"


def _cmd_bucket_migrate(args) -> int:
    """Move this project's pre-0.42.0 bucket into the one daimon reads (#963).

    `--project` is a PATH and is absolutized, never treated as a bucket name,
    so it can never be a bare bucket slug (scar 0071).

    That alone is NOT enough, and the earlier version of this docstring said
    it was. The two slug rules differ in WHEN they resolve: the legacy rule
    collapses `..` lexically before touching a symlink, the current one
    resolves the symlink first. A path combining both therefore names two
    different DIRECTORIES, and a migration would move a bucket the caller has
    no claim on. `buckets.migrate` refuses any `..` component outright, which
    is what makes the sentence above true; the refusal arrives here as a
    MigrationError and leaves as rc 2, the same code every other refusal on
    this surface uses.

    rc 1 is a PARTIAL merge: something was left behind, unreadable or too big
    for the pointer chain. The caller learns that from the exit code without
    parsing the receipt.

    On a TENANT-SCOPED home an explicit `--project` is refused outright, the
    way `--slug` and `--all-projects` already are (#899). The path reach is
    not what is new: `--project` could always name another directory. What
    this verb adds is a PERMANENT row in the global migrations file, which
    `recall.rebuild` and `requests.recipient_join` then honor for whichever
    bucket it names. A caller who may not choose a read scope must not be
    able to mint a durable alias between two of them, so the project comes
    from the host (DAIMON_PROJECT_DIR, else cwd) and from nowhere else."""
    if config.tenant_scoped() and args.project:
        message = (
            "this daimon home is tenant-scoped (DAIMON_TENANT_SCOPED): the "
            "project is host-set, so `bucket migrate` takes it from "
            "DAIMON_PROJECT_DIR (else the working directory) and refuses an "
            "explicit --project. A migration writes a lasting alias between "
            "two buckets, which is a scope choice.")
        if args.json:
            print(json.dumps({"refused": message}, indent=2,
                             ensure_ascii=False))
        else:
            print(f"error: {message}", file=sys.stderr)
        return 2
    raw = _raw_project(args.project)
    try:
        record = buckets.migrate(raw, dry_run=args.dry_run)
    except buckets.MigrationError as exc:
        # Machine callers get the refusal in the format they asked for; they
        # must never have to parse stderr to learn the verb said no.
        if args.json:
            print(json.dumps({"refused": str(exc)}, indent=2,
                             ensure_ascii=False))
        else:
            print(f"bucket not migrated: {exc}", file=sys.stderr)
        return 2
    rc = 0 if record.get("complete", True) else 1
    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
        return rc
    lines = _bucket_migrate_lines(record, raw, dry_run=args.dry_run)
    render.render_ledger_lines(lines)
    return rc


def _bucket_migrate_lines(record: dict, raw: str, *, dry_run: bool) -> list:
    """The human render of one migration record. Pure, so the wording is
    testable without a filesystem."""
    mode = record["mode"]
    if mode == "unknown":
        return [f"nothing to migrate: no project resolves from {raw}"]
    if mode == "stable":
        return [f"nothing to migrate: {record['to_slug']} is stable under "
                f"both rules"]
    if mode == "absent":
        return [f"nothing to migrate: no legacy bucket "
                f"{record['from_slug']} for {raw}"]
    verb = "would move" if dry_run else "moved"
    lines = [f"{verb} {record['from_slug']} into {record['to_slug']} "
             f"({mode})"]
    for name, count in sorted(record["ledgers"].items()):
        appended = "would append" if dry_run else "appended"
        lines.append(f"  {name}: {appended} {count} line(s)")
    if record["pointers"]:
        landed = "would move" if dry_run else "moved"
        lines.append(f"  pointers: {landed} {record['pointers']} into the "
                     f"chain")
    # Every remaining line names a CONCRETE remedy for one thing. The earlier
    # wording said "remove or fix by hand" for all of them, which invited
    # deleting a legacy bucket outright and left a migration nothing could
    # ever finish.
    stranded = record.get("stranded_pointers") or []
    if stranded:
        # Arithmetic on the slots the target ACTUALLY holds, which the record
        # carries. Deriving it from DAIMON_CHECKPOINT_HISTORY assumes the
        # target occupies exactly that many, and a bucket written while the
        # knob was higher holds more: the message then names a value that
        # strands the same pointer again.
        need = record.get("target_slots", 0) + len(stranded)
        lines.append(
            f"  {len(stranded)} pointer(s) found no free slot and are still "
            f"in {record['from_slug']}: {', '.join(stranded)}")
        lines.append(f"  raise DAIMON_CHECKPOINT_HISTORY to at least {need} "
                     f"and run again")
    for name in record.get("target_unreadable") or []:
        lines.append(f"  {name} in {record['to_slug']} could not be read, so "
                     f"no pointer was moved: fix or move that file, then run "
                     f"again")
    unreadable = record.get("unreadable") or []
    for name in unreadable:
        lines.append(f"  {name} could not be read, so it was not moved: fix "
                     f"or move that file, then run again")
    for name in record["leftovers"]:
        if name in unreadable:
            continue  # already named above, with its remedy
        if stranded and store._POINTER_RE.match(name):
            continue  # named on the stranded line above, by session
        # Never a claim about who wrote it: a dangling symlink named
        # `prev-2.json` carries daimon's own naming, and an authorship claim
        # here is one nobody can check. What is true is that this verb could
        # not read it as anything it knows how to move.
        lines.append(f"  {name} could not be read as anything this verb "
                     f"moves: move it out of {record['from_slug']} to finish")
    return lines


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
        topic = _topic_teaser(r["topic"])
        display.append({
            "mark": "*" if r["current"] else " ",
            "name": r["name"] or "—",
            "slug": r["slug"], "age": age,
            "branch": r["git_branch"] or "—", "topic": topic or "?",
        })
    render.render_projects(display)
    return 0


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
from . import (  # noqa: E402
    action_recall,
    amend,
    audit,
    check,
    configure_cmd,
    history,
    hooks,
    inject,
    ledger_cmd,
    lifecycle,
    refute,
    request,
    ruling,
    skill,
    stats,
    status,
    team,
    trust,
)


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
        render.render_lifecycle_lines(["handoff cleared"])
        return 0
    text = (args.text or "").strip()
    if not text:
        print("error: nothing to hand off — pass the baton text or --clear",
              file=sys.stderr)
        return 1
    if len(text) > _HANDOFF_MAX_CHARS:
        # #902: a refusal that names no destination sends the trimmed content
        # to whatever store is nearest, which for an agent is the harness's
        # own memory file, where daimon never sees it. Name the daimon-side
        # homes. NOT `daimon log`: nothing reads a ref-less note back, so it
        # would be a void with a command name.
        print(f"error: baton exceeds {_HANDOFF_MAX_CHARS} chars "
              f"({len(text)}) — a handoff is \"do X first, beware Y\", not a "
              "second checkpoint; trim it. The trimmed facts belong in a "
              "checkpoint (the /daimon-end skill, `daimon write-checkpoint`), "
              "and a rule that must never decay in "
              "`daimon ruling propose --by agent`", file=sys.stderr)
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
    render.render_lifecycle_lines(
        ["handoff recorded — will lead the next briefing for this project."])
    return 0


def _cmd_log(args) -> int:
    """Freeform zero-LLM event append (#102): a timeline fact worth keeping
    that is not tied to one item. The fold ignores ref-less lines; readers
    of the raw log get the audit trail."""
    if store.is_tombstone_status(args.status):
        print("a forgotten: status is a tombstone and only `daimon forget` "
              "writes one (it also removes the value) — refused, nothing "
              "written")
        return 1
    project = _resolve_project(args.project)
    ok = store.append_event("", args.status, note=args.text,
                            kind=args.kind, project_dir=project)
    if not ok:
        print("event not written (daimon disabled or project unknown)")
        return 1
    render.render_lifecycle_lines([f"logged [{args.kind}] {args.text}"])
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

    history.register(sub, fmt)

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

    p_bucket = sub.add_parser(
        "bucket",
        help="maintain the checkpoint bucket this project reads and writes",
    )
    bucket_sub = p_bucket.add_subparsers(dest="bucket_cmd", required=True)
    bucket_sub.add_parser = functools.partial(  # type: ignore[method-assign]
        bucket_sub.add_parser, formatter_class=fmt)

    p_bucket_migrate = bucket_sub.add_parser(
        "migrate",
        help="move a bucket written before 0.42.0 into the one daimon reads",
        description="Move the bucket a pre-0.42.0 daimon wrote for this "
                    "project into the bucket this daimon reads. Affects a "
                    "project whose path carries a symlink component, or one "
                    "below a git toplevel: before 0.42.0 the library slugged "
                    "the literal path. A pointer already in the current "
                    "bucket is never removed or displaced. Safe to run twice: "
                    "a second run over an unchanged state writes nothing new "
                    "and returns the code matching the state it finds.",
        epilog="Examples:\n"
               "  daimon bucket migrate\n"
               "  daimon bucket migrate --project /tmp/my-repo --dry-run\n",
    )
    p_bucket_migrate.add_argument(
        "--project",
        help="project directory (default: DAIMON_PROJECT_DIR, then cwd)")
    p_bucket_migrate.add_argument(
        "--dry-run", action="store_true",
        help="print the plan and write nothing")
    p_bucket_migrate.add_argument(
        "--json", action="store_true", help="machine-readable output")
    p_bucket_migrate.set_defaults(func=_cmd_bucket_migrate)

    p_slug = sub.add_parser(
        "slug",
        help="print the checkpoint directory name daimon derives from a project path",
        description="Print the checkpoint directory name daimon derives from "
                     "a project path. Read-only: no store, config, or ledger "
                     "access.",
        epilog="Examples:\n"
               "  daimon slug /Users/x/my.proj\n"
               "  daimon slug -- -Users-x        # '--' escapes a path starting with '-'\n",
    )
    p_slug.add_argument("path", help="project directory path to slug")
    p_slug.set_defaults(func=_cmd_slug)

    lifecycle.register(sub, fmt)

    refute.register(sub, fmt)

    ruling.register(sub, fmt)

    amend.register(sub, fmt)

    trust.register(sub, fmt)

    ledger_cmd.register(sub, fmt)

    request.register(sub, fmt)

    p_relations = sub.add_parser(
        "relations",
        help="inspect and decide typed item relations (#678, shadow mode)",
        epilog="Examples:\n"
               "  daimon relations list\n"
               "  daimon relations show rel-0123456789abcdef\n"
               "  daimon relations confirm rel-0123456789abcdef\n",
    )
    relations_sub = p_relations.add_subparsers(dest="relations_cmd",
                                               required=True)
    relations_sub.add_parser = functools.partial(  # type: ignore[method-assign]
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
