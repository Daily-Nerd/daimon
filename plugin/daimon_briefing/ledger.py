"""The serialize.log ledger: append result/retry lines, parse them back, and
classify per-session outcomes.

Extracted verbatim from cli.py (#147) — this is the single subsystem behind
`daimon status` and `daimon heal`: writers (_append_serialize_log,
_append_retry_log), the line-format regexes, the last-of-each-kind tail view
(_parse_serialize_log), the per-session fold (_session_ledger), the lost-session
classifier (_outstanding_failures / _compute_outstanding), and the heal decision
(_heal_plan). Every regex here is a load-bearing contract with the lines the
hooks and _run_serialize write; change them together or not at all.

The stats fold over the same log lives here too (#162, second pure-move
slice): the every-line tally (_stats_capture) and the in-window spawn probe
(_spawns_in_window) with its stamp parser (_parse_stamp), so the
"new prefix -> update the parser" rule has a single home.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from . import config, store


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
    session, ever, BY DEFAULT. That cap deliberately survived the cache-buster
    era (#15): retries used to be pointless byte-identical replays against a
    caching gateway, so capping at one was strictly correct; the serializer now
    cache-busts both failure layers, so a second heal is no longer guaranteed
    to reproduce the first failure. The cap stays anyway — it bounds token burn
    on a permanently-bad transcript — but `daimon heal --force` is the explicit
    operator override past it (`_outstanding_failures`'s `force` param). This
    writer's format is unchanged either way: a forced retry appends the SAME
    marker shape, so a forced heal re-classifies as retry-exhausted again until
    the next --force. Best-effort: never break a heal."""
    try:
        log_dir = config.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with (log_dir / "serialize.log").open("a", encoding="utf-8") as f:
            f.write(f"{stamp} session-start: retry serialize for {session_id} (prior: {prior})\n")
    except OSError:
        pass


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


def _outstanding_failures(ledger, now, has_checkpoint, ceiling, transcript_exists, force=False) -> list:
    """Sessions still LOST — no checkpoint AND latest state != success.
    `has_checkpoint(sid)` and `transcript_exists(path)` are injected so this
    stays pure/testable. error+spawn+transcript-on-disk+not-retried -> healable
    (exactly what heal will repair); error but retried -> retry-exhausted; error
    but no spawn record or transcript gone -> unrecoverable (lost, heal can't
    retry it); spawn with no result older than `ceiling` -> hung.

    `force` (#15) is the `daimon heal --force` escape hatch: it ignores the
    `retried` gate on both the error and hung paths, so a retry-exhausted (or
    retried-hung) session reclassifies as `healable` again PROVIDED its
    transcript still exists — force can't repair what's genuinely gone, so
    those stay `unrecoverable`/`hung`. Callers that don't pass `force` (e.g.
    `status`, and default `heal`) see classification exactly as before."""
    out = []
    for sid, e in ledger.items():
        if e["result_kind"] in ("success", "skipped"):
            continue
        if has_checkpoint(sid):
            continue
        age = e["spawn_age"]
        if e["result_kind"] == "error":
            if e["retried"] and not force:
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
            # one-retry-ever policy (#26) applies unchanged via `retried`,
            # unless `force` (#15) overrides it.
            t = e["transcript"]
            cls = ("healable"
                   if t and transcript_exists(t) and (not e["retried"] or force)
                   else "hung")
            out.append({"sid": sid, "kind": "hung", "class": cls, "age": age,
                        "age_str": _format_age(age), "transcript": t,
                        "project": e["project"], "spawned": True, "line": None})
    out.sort(key=lambda f: (f["age"] is None, f["age"] or 0))
    return out


def _compute_outstanding(text: str, now: float, force: bool = False) -> list:
    """Wire the pure ledger/classifier to the live store + filesystem. Single
    source for both `status` (display) and `heal` (repair) so their notion of
    'outstanding' can never drift. `force` (#15) is forwarded to
    `_outstanding_failures`; callers that don't pass it get unchanged default
    classification."""
    return _outstanding_failures(
        _session_ledger(text, now), now,
        lambda sid: store.read_checkpoint(sid) is not None,
        config.hung_after_seconds(),
        lambda p: bool(p) and Path(p).exists(),
        force=force,
    )


_HEAL_SKIP_REASON = {
    "retry-exhausted": "retry already attempted, still failing (re-run with --force)",
    "unrecoverable": "no spawn record or transcript gone — cannot auto-heal",
    "hung": "spawned, no result (hung/killed) — transcript unavailable",
}


def _heal_plan(text, now, force=False) -> dict:
    """Decide what `heal` will repair and why. Pure — `now` injected. Reuses the
    SAME _compute_outstanding source as status, so their notion of healable agrees.
    target = the newest `healable` (already gauntlet-vetted); every other outstanding
    failure lands in `skipped` with a reason; `note` is the headline when there is no
    target. `force` (#15) is forwarded to _compute_outstanding — the classifier does
    the actual retry-exhausted-to-healable promotion, so this layer needs no
    special-casing beyond passing the flag through."""
    outstanding = _compute_outstanding(text, now, force=force)
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


# Host prefix on a spawn line, for per-host capture counts. Deliberately the
# same alternation as _SPAWN_RE (a new host adapter updates both).
_STATS_HOST_RE = re.compile(
    r"^\S+ (gemini-session-end|session-end|codex-stop|windsurf-cascade): "
    r"spawned serialize for "
)


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


_USAGE_STAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


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
