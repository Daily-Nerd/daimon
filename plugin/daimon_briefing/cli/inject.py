"""`daimon recall-inject`: the UserPromptSubmit recall backend (moved out of cli/__init__.py, #1132 PR 5).

The per-session seen/cooldown state, the age gate, the slot-width constants
and the rendering of one suggestion. `_ORIGIN_BUDGET` lives here, so a test
that changes it patches `daimon_briefing.cli.inject`. Every name is
re-exported from `cli`.
"""

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

import daimon_briefing.cli as _cli

from .. import briefing, config, normalize, recall, recall_telemetry, store
from ..terms import is_machine_prompt, salient_terms
from ..ledger import _format_age


# ---- recall-inject: the UserPromptSubmit hook backend (#125) ----

_SEEN_PRUNE_SECONDS = 7 * 86400  # cooldown files for week-old sessions are dead


_INJECT_BUDGET = 2   # slots per prompt (#125 noise budget)


# #1031: slots per SHELL ACTION. One, not two, and not for symmetry — a prompt
# arrives once per turn and an action several times within one, so the same
# budget would multiply the noise by however many commands a turn runs.
_ACTION_BUDGET = 1


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


# #1030: how much of an item's text each slot renders. Width is a property of
# the SLOT, not of the item — no score, term count or pin buys extra room, so
# there is no second ranking axis hiding in the renderer. The lead slot is the
# one a reader acts on, so it carries enough text to act on; every slot after
# it keeps the old width, and the noise budget (_INJECT_BUDGET), the ranking,
# the age gate and the cooldown are all unchanged.
_LEAD_WIDTH = 320


_SLOT_WIDTH = 160


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


def _seen_path(session: str, *, suffix: str = "json"):
    """Cooldown-state file for one session, or None when the id is unusable
    (empty, or path-hostile — the id becomes a filename). Origin ids are not
    all uuids: a Codex session id is `rollout-<timestamp>-<hex>`, which is a
    fine filename and must keep working.

    `suffix` names the SURFACE, and the default is the prompt surface's, which
    predates every other one (#1031). Two surfaces sharing one file would
    share a read-modify-write across two processes that fire on different
    events, and the loser of that race silently discards the winner's whole
    cooldown — not one line, all of it."""
    if not session or "/" in session or "\\" in session or ".." in session:
        return None
    return config.recall_seen_dir() / f"{session}.{suffix}"


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


def _save_seen_atomic(path, origin_counts: dict, content_keys: set) -> None:
    """Same state as `_save_seen`, written temp-then-rename (#1031).

    The action surface fires before a SHELL ACTION, and a host runs several of
    those at once. Two concurrent writers to one file can interleave a partial
    write with a read, and `_load_seen` reads a truncated file as empty state,
    which loses the whole session's cooldown rather than one line of it.
    `os.replace` is atomic on POSIX and on Windows, so a reader sees the old
    file or the new one and never half of either.

    What this does NOT fix, deliberately: a LOST UPDATE. Two actions that read
    the same state and both write will keep only the second, and the cost of
    that is at most one repeated suggestion. Paying for a lock in front of
    every shell action to save one line is the wrong trade.

    No opportunistic prune here, unlike `_save_seen`: the prune walks the whole
    directory and unlinks by mtime, and this writer runs concurrently with
    itself. The prompt surface still sweeps the shared directory.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(
            {"origins": {s: origin_counts[s] for s in sorted(origin_counts)},
             "content_keys": sorted(content_keys)})
        # In the SAME directory: os.replace is only atomic within a filesystem,
        # and a temp dir elsewhere can be a different one.
        fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                                   prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
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


def _fit_item_text(raw, width: int) -> tuple[str, bool]:
    """Item text as one slot renders it, plus whether the cut took anything.

    Collapse then cut, in that order: the collapse is what keeps the #512
    one-line contract, and it has to happen before the width is measured or a
    newline-bearing item would be cut to a width it never occupies.

    The second return value is not derivable after the fact — a rendered
    length equal to the width could be an item that exactly fits — and #1030's
    whole read-back rests on telling a short claim from a truncated one.

    Stays a local cut on purpose, not a `display.shorten` call (#1129): the
    rendered length, the `truncated` flag and the ASCII `...` all feed recall
    telemetry, and changing any of them would split the soak series."""
    text = " ".join(str(raw).split())
    if len(text) <= width:
        return text, False
    return text[:width - 3] + "...", True


# #1062: a conservative shape for a value that renders straight into
# model-visible text — anything else falls back to the bare tool name rather
# than being trusted verbatim. Alphanumerics and underscores cover every
# real tool name in this codebase's own hosts (a bare `daimon_recall`, a
# server-qualified `mcp__daimon__daimon_recall`, a plugin-prefixed
# `mcp__plugin_daimon_daimon__daimon_recall`); a length ceiling matches the
# one `recall_telemetry.clean_session` already applies to the neighboring
# session clause. Checked with `fullmatch`, never `match` against an
# anchored pattern: Python's `$` matches just before a trailing "\n", not
# only end of string, so `match()` here would accept "daimon_recall\n" and
# render the newline straight into model-visible text.
_MCP_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,128}")


_DEFAULT_MCP_TOOL_NAME = "daimon_recall"


def _suggest_line(r: dict, terms, now: float, own_slug=None, *,
                  width: int, mcp_tool_available: bool = False,
                  mcp_tool_name: str | None = None,
                  session: str | None = None) -> str:
    """One compact, attributed, trust-preserving injection line (#125).

    ONE line is a contract, not a hope (#512): the echo strip that removes
    this line from the verification haystack is line-scoped, so item text
    carrying a newline would leave its tail behind as a fake witness.
    Internal whitespace collapses here, at the emitter.

    `width` is keyword-only and has no default on purpose (#1030): it is the
    caller's slot decision, and a default would quietly make one slot's width
    the invisible truth again.

    `mcp_tool_available` (#1036) is the caller's own resolved
    `config.mcp_tool_available()` reading, passed in rather than read here:
    the emitter stays a pure formatter, and every existing call site keeps
    the shell-command hint it always rendered by leaving the default alone.
    True names the exact MCP tool instead of the shell string, so an agent
    that already lists `daimon_recall` can act on the hint without leaving
    its tool list.

    `session` (#1053) is the live session this delivery is printing into,
    when the caller knows it — the same value already threaded through to
    `recall_telemetry.record`'s `injected_into`. It rides the TOOL form of
    the hint only: the shell string has no `--session` flag to receive it
    back, so a follow-up `daimon recall "..."` cannot be paired to this
    injection the way a `daimon_recall` tool call can.

    `mcp_tool_name` (#1062) is the caller's own resolved
    `config.mcp_tool_name()` reading, same posture as `mcp_tool_available`:
    the host-visible name the AGENT's own tool list carries for the
    read-only MCP server — Claude Code's plugin-prefixed
    `mcp__plugin_daimon_daimon__daimon_recall`, Kimi's server-qualified
    `mcp__daimon__daimon_recall`, Codex's bare `daimon_recall` — instead of
    a generic default on every host. It only ever changes the TOOL form of
    the hint; the shell string never names a tool at all. Falls back to the
    bare `daimon_recall` when unset (an older hook that predates this flag)
    or when it fails `_MCP_TOOL_NAME_RE`'s conservative shape check: this
    string renders straight into model-visible text, so a value this codebase
    did not itself produce is never trusted verbatim."""
    age = _format_age(now - r["created"]) if r.get("created") else "?"
    trust = r.get("trust") or "untagged"
    text, _truncated = _fit_item_text(r["text"], width)
    # v3 (#234): the flag is item-level evidence — a typed supersedes link
    # or a logged resolution — not the old whole-checkpoint recency.
    # #1079: name_id=False keeps this surface's deliberately vaguer wording
    # (no id, no writer) — recall.describe_supersession is the shared parse,
    # not a shared phrase, across every surface.
    sup_phrase = recall.describe_supersession(r, name_id=False)
    superseded = f" ({sup_phrase})" if sup_phrase else ""
    # #837: suggest() ranks a contradicted item DOWN, and a demotion alone is
    # silent burial — one that still clears the gate has to arrive flagged.
    # The evidence is named in full here, unlike the supersession marker's
    # vaguer wording, because there is no `daimon why` follow-up that would
    # surface it and no cure path that would retract it.
    inv = recall.describe_invalidation(r.get("invalidated_by"))
    contradicted = f" ({inv})" if inv else ""
    # #866: an item that survived a challenge arrives saying so, rather than
    # as though nothing had happened.
    cured = recall.describe_cure(r.get("cured_by"))
    contradicted += f" ({cured})" if cured else ""
    # #889: name the origin project when it is not this reader's own. Same
    # phrasing function as the recall surface, so a live nudge and a hand-run
    # search can never describe a different origin for one row.
    scope = recall.describe_scope(r, own_slug)
    scope_mark = f" ({scope})" if scope else ""
    # #890: same posture as the recall surface, same phrasing.
    stated = str(r.get("stated_by") or "").strip()
    stated_mark = f" (stated by {stated})" if stated else ""
    more = " ".join(terms[:3])
    if mcp_tool_available:
        tool_name = (mcp_tool_name if mcp_tool_name
                    and _MCP_TOOL_NAME_RE.fullmatch(mcp_tool_name)
                    else _DEFAULT_MCP_TOOL_NAME)
        more_hint = f'call the {tool_name} tool with query "{more}"'
        # #1053 fix B: the SAME validator the tool argument goes through
        # (mcp_tools._recall) — a session the hint renders is always one
        # the tool will accept back, and a hostile id (a newline or a
        # quote, either of which would break this one-line hint, #512)
        # never reaches the rendered text at all.
        clean = recall_telemetry.clean_session(session)
        if clean:
            more_hint += f' and session "{clean}"'
    else:
        more_hint = f'daimon recall "{more}"'
    return (f"daimon recall: prior work — {r['kind']} from {r['session_id']} "
            f"({age} ago): \"{text}\" [{trust}]{stated_mark}{scope_mark}"
            f"{superseded}"
            f"{contradicted}. "
            f"More: {more_hint}")


def _choose_recall_rows(matches, seen_keys: set, now: float, *, budget: int,
                        usage_prefix: str) -> tuple[list[dict], set]:
    """Rows from `suggest` that actually earn a slot, plus their content keys.

    ONE definition, shared by every injection surface (#1031 added the second).
    Two copies of this loop would drift the moment either gate changed, and the
    drift is invisible: both surfaces stay green while one of them quietly
    enforces last month's cooldown.

    #451: an origin id is not a content identity. The same claim carried by two
    checkpoints (sibling-id copies — the read-side twin of the value-keyed
    forget arc, #424/#435) passes the origin cooldown and re-injects as if it
    were new: 15.5% of measured injections repeated text the session had
    already seen, every repeated group cross-origin. So the budget is spent on
    distinct content keys, within one injection AND across the session, and a
    suppressed candidate yields its slot to the next distinct one instead of
    shrinking the injection.

    `usage_prefix` names the SURFACE in every counter this writes, so the rates
    stay separable: the action surface fires per shell action and the prompt
    surface per prompt, and pooling them would make either denominator a
    fiction.
    """
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
        _cli._note_usage(f"{usage_prefix}:age:{_inject_age_bucket(age_days)}")
        if len(chosen) >= budget:
            break
    if suppressed:
        # Counted apart from the surface's own key, which still counts every
        # fire: the issue's claim is a RATE, so the pair has to be readable
        # from `daimon stats` the way #450's machine skip is.
        _cli._note_usage(f"{usage_prefix}:dedup-content")
    if age_gated:
        # Same convention as dedup-content above: once per injection run
        # where >=1 candidate was age-gated (#452) — a rate, not a tally.
        _cli._note_usage(f"{usage_prefix}:age-gate")
    return chosen, chosen_keys


def _cmd_recall_inject(args) -> int:
    """Print 0-2 'you worked on this before' lines for the prompt on stdin, or
    nothing. rc 0 ALWAYS — this sits on the user's per-prompt critical path and
    a suggestion is never worth blocking a prompt (fail-open, like the hooks)."""
    _cli._note_usage("recall-inject")
    try:
        prompt = sys.stdin.read()
        # #450: host-emitted blocks (task notifications, teammate/agent
        # messages, command output) arrive here as prompts but are nobody
        # asking for anything — 37.9% of measured injections landed on them.
        # Its own try: a classifier failure must cost nothing, so it falls back
        # to today's behavior (suggest) rather than to the outer silent return.
        try:
            machine = is_machine_prompt(prompt)
        except Exception:  # noqa: BLE001 — fail toward suggesting, never skip on a bug
            machine = False
        if machine:
            # Counted apart from `recall-inject`, which still counts every fire:
            # the pair is the before/after measure of the noise removed (#450).
            _cli._note_usage("recall-inject:skip-machine")
            return 0
        project = _cli._resolve_project(args.project)
        session = str(args.session or "")
        # Never re-suggest what the SessionStart briefing already carried.
        # #784: that is ONE checkpoint, and which one depends on the same gate the
        # injection hook reads — the project's own, and the global pointer only when
        # the operator opted into the foreign body. Excluding the global latest
        # unconditionally suppressed recall of a session that was never briefed.
        exclude = set()
        briefed = store.read_latest_body(
            project_dir=project,
            route=briefing.injection_read_route(project),
            admit=store.Admit.ANY)
        sid = (briefed or {}).get("session_id")
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
        now = time.time()
        chosen, chosen_keys = _choose_recall_rows(
            matches, seen_keys, now, budget=_INJECT_BUDGET,
            usage_prefix="recall-inject")
        terms = salient_terms(prompt)
        # #1036: resolved once per injection, not per line — one delivery
        # renders one hint form, and the flag is the plugin's own hook
        # telling the truth about itself, never inferred here. Read before
        # the empty-pull exit below too: the placeholder row's hint_form
        # describes the delivery that WOULD have rendered.
        mcp_available = config.mcp_tool_available()
        if not chosen:
            # #1073: an empty pull is still an event — record the
            # honest-empty placeholder so a 7-day distribution can see
            # refusals, not just admissions. `best_refused` is the strongest
            # candidate suggest() found that the age gate then turned away;
            # None when suggest() itself matched nothing.
            numeric = [m["match_score"] for m in matches
                      if isinstance(m.get("match_score"), (int, float))]
            recall_telemetry.record(
                [],
                query_terms=terms,
                surface="recall-inject",
                hint_form="tool" if mcp_available else "shell",
                injected_into=session or None,
                now=datetime.fromtimestamp(now, tz=timezone.utc),
                best_refused=max(numeric) if numeric else None,
            )
            return 0
        own_slug = store.project_slug(project)
        # #1062: same posture, same call site, the sibling value the hook
        # exports next to the flag above.
        mcp_name = config.mcp_tool_name()
        delivered = []
        for slot, m in enumerate(chosen):
            # #1030: the slot table lives here and nowhere else. Lead gets
            # _LEAD_WIDTH, every later slot _SLOT_WIDTH.
            width = _LEAD_WIDTH if slot == 0 else _SLOT_WIDTH
            print(_suggest_line(m, terms, now, own_slug=own_slug, width=width,
                                mcp_tool_available=mcp_available,
                                mcp_tool_name=mcp_name,
                                session=session or None))
            # Same pure fit the emitter just used, so the ledger cannot
            # describe a rendering the host never received.
            rendered, truncated = _fit_item_text(m["text"], width)
            delivered.append({**m, "rendered_chars": len(rendered),
                              "truncated": truncated})
        recall_telemetry.record(
            delivered,
            query_terms=terms,
            surface="recall-inject",
            hint_form="tool" if mcp_available else "shell",
            # #1043: the live session this suggestion is printing into, not
            # the (possibly different) session that captured each item.
            injected_into=session or None,
            now=datetime.fromtimestamp(now, tz=timezone.utc),
        )
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


def register(sub, fmt) -> None:
    """Register this family's parsers on the top-level subparsers."""
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
