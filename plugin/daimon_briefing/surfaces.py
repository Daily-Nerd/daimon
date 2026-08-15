"""The declared surface registry (#601): every file shape daimon writes under
~/.daimon, with its deletion contract.

Four shipped defects of one class (#583, #599 twice over, the #600 team gap)
traced to the same hole: three hand-maintained lists — store's deletion walk,
privacy's audit exemptions, recall's fingerprint set — each answered "what
files exist and what may they hold" separately, so a new file shape silently
inherited a hole in whichever list its author forgot. This module is the
single declaration; consumers derive their views from it (privacy's
exemptions today; the write-audit guard refuses write shapes that were never
declared), and a plaintext shape with no reachable deletion must name the
tracking issue for its gap — visible debt, never silence.

Follows schema.py's pattern: one NamedTuple table, every consumer derived.
A shape added here propagates; a shape written but not declared fails the
guard in tests/test_write_audit_guard.py.

Shape syntax: path parts relative to ~/.daimon. `{slug}`/`{remote}`/`{pid}`/
`{hash}` are placeholders (they match anything, and match themselves
literally so write-audit-normalized patterns classify too); `*` is fnmatch
within one part; `**` spans zero or more parts. First declaration wins, so
specific shapes come before the generic ones they would otherwise shadow.
"""
from __future__ import annotations

import fnmatch
import re
from typing import NamedTuple

# rewrite            — forget reaches it by rewriting the file (atomic replace)
# append-tombstone   — append-only ledger; forget appends a tombstone and the
#                      ONE ratified carve-out (store.scrub_event_fields)
#                      redacts fields in place
# wholesale-purge    — cannot be selectively scrubbed; deletion drops the
#                      whole store (chunk cache, in-flight tmps)
# reap               — dead by construction; a reaper deletes on sight
#                      (recall.reap_dead_snapshots)
# exempt-no-plaintext— holds no item plaintext BY CONSTRUCTION; every such
#                      claim cites the owning module's own guarantee
# known-gap          — holds plaintext deletion cannot reach TODAY; must cite
#                      the tracking issue. The registry makes the debt
#                      visible; it never silences it.
# lazy-rebuild        — derived cache; forgotten rows leave at the NEXT
#                       fingerprint-triggered rebuild, not at forget time —
#                       if no recall command ever runs, plaintext persists
#                       (the audit's stale-index-pending-rebuild class)
DELETE_STRATEGIES = frozenset({
    "rewrite", "append-tombstone", "wholesale-purge", "reap",
    "lazy-rebuild", "exempt-no-plaintext", "known-gap",
})


class Surface(NamedTuple):
    shape: str          # path pattern relative to ~/.daimon (syntax above)
    owner: str          # writer, as module.function
    plaintext: bool     # can item text/quote/scene/note ever land in it?
    delete: str         # one of DELETE_STRATEGIES
    walker: str         # who walks it: forget | audit | recall | reaper | none
    issue: str = ""     # required when delete == "known-gap"
    audit_exempt: bool = False  # feeds privacy's name/suffix exemption sets


SURFACES: tuple[Surface, ...] = (
    # -- per-project bucket ledgers (specific before the *.json generics) --
    Surface("checkpoints/{slug}/events.jsonl", "store.append_event",
            True, "append-tombstone", "audit"),
    # -- the refutation ledger (#575): append-only like events.jsonl, but it
    #    carries item PLAINTEXT by design (subject, verdict, scope, note,
    #    revisit_when, anchors, evidence), so it sits in the checkpoint's
    #    category rather than the hash-only ledger's. `rewrite` is what
    #    refutations.forget_content_key already does: the one path that
    #    rewrites this file, atomically, dropping every row of a matched
    #    record. Never audit_exempt (#645) — an exemption here would silence
    #    exit 3 in one line while the file holds the very text the registry
    #    exists to declare.
    #
    #    RETENTION (#648): there is none, and that is the posture rather than
    #    an oversight. `delete` holds one value and `rewrite` is the true one —
    #    forget reaches this file by VALUE — so the growth story cannot live in
    #    this field and is recorded here instead.
    #
    #    Nothing reaps it by age, deliberately. `daimon refute` records
    #    rejected approaches "outside checkpoint decay" (README, and the CLI
    #    reference in both locales), and a refutation is worth MORE with age:
    #    it exists so a lesson outlives the temptation to retry the approach.
    #    Every other reaped store here — chunk cache, windsurf state, crash
    #    log, stale tmps — holds derived or diagnostic data that is worthless
    #    when old. Reaping this one would delete the lesson exactly when it
    #    finally becomes useful.
    #
    #    Growth is bounded by deliberate action instead: the writers are the
    #    four `refute` CLI verbs plus the four `ruling` write verbs (#693 —
    #    the file now holds BOTH polarities; rulings share the no-reaper
    #    rationale: an active ruling is live state, and retirement is a human
    #    verdict, not an age), the `mechanical` channel is a socket nothing
    #    plugs into, the two agent-writable proposal channels on a ruling
    #    (`revision-proposed`, `overturn-proposed`) refuse past 3 open rows
    #    PER CHANNEL since the last human verdict, and the audit reports record/row/byte
    #    counts every run (privacy.audit_project) so this is measured, never
    #    silent. Ruling CANDIDATES are agent proposals and reaper-eligible
    #    under the note below, but with the #693 lifecycle no human-ratified
    #    ruling can ever BE a candidate again — demotion is never a side
    #    effect and no agent path demotes — so a candidate reaper cannot
    #    delete a human constraint at agent initiative.
    #
    #    Two things reopen it, and neither is a clock. If #581 ships mechanical
    #    activation, something writes without a human asking. If the reported
    #    counts actually climb, the sanctioned fix is a CANDIDATE-scoped
    #    reaper: the design of record scopes no-decay to ACTIVE records and
    #    already allows candidates to expire (research/experiments/
    #    refutation-573/README.md). An active-record reaper would falsify a
    #    published claim and needs the contract amended first. --
    Surface("checkpoints/{slug}/refutations.jsonl", "refutations.append",
            True, "rewrite", "forget"),
    # -- the amendment ledger (#691): the fourth bucket ledger — evidence
    #    quotes and human-channel notes, both length-capped, both plaintext
    #    by design, so it sits in the checkpoint's deletion category with
    #    refutations. `rewrite` covers its two deleters:
    #    amendments.forget_content_key (by value, whole-value canonical
    #    match) and amendments.forget_item_id (rows about a forgotten item
    #    go with it — unlike relations, these rows carry prose that can
    #    paraphrase the removed content). Never audit_exempt: the audit
    #    hashes the module's own _PLAINTEXT_FIELDS declaration and checks
    #    target ids against tombstones (privacy.audit_project). Honest
    #    limit, stated because this ledger's defining field is a VERBATIM
    #    QUOTE: forget and the audit match whole values, so a quote merely
    #    CONTAINING a forgotten value is beyond the hash scan — the same
    #    stated limitation as event notes, but it is this surface's normal
    #    shape rather than its edge case (the render note says so). No age
    #    reaper: an amendment is lifecycle evidence for a LIVE item and
    #    falls out of every render surface the moment its item resolves or
    #    is forgotten; the audit computes AND prints record/row/byte counts
    #    (render_privacy_audit) so growth is measured, never silent. --
    Surface("checkpoints/{slug}/amendments.jsonl", "amendments.append",
            True, "rewrite", "forget"),
    # store.append_verification: "a POINTER and a REASON CODE, never the
    # rejected text" (store.py docstring).
    Surface("checkpoints/{slug}/verification.jsonl",
            "store.append_verification", False, "exempt-no-plaintext",
            "none", audit_exempt=True),
    # store.record_forget_hits: {ts, key} — "NEVER the text or any prefix".
    Surface("checkpoints/{slug}/forget-hits.jsonl",
            "store.record_forget_hits", False, "exempt-no-plaintext",
            "none", audit_exempt=True),
    # -- the relations ledger (#678 fork A): ids and closed-vocabulary codes
    #    only — no field can carry item text (relations.py refuses at the
    #    seam). plaintext=True anyway, deliberately: an edge is an
    #    equivalence CLAIM about content (`exact-text` against a forgotten
    #    endpoint asserts the forgotten value equaled a surviving item's
    #    text), and #419's rule is that holding the sensitive relation to
    #    content — not the file format — is what puts a surface inside the
    #    deletion contract. `rewrite` is relations.forget_item_id, the one
    #    path that rewrites this file, dropping every row of a record whose
    #    edge touches a tombstoned item id. Never audit_exempt: the audit
    #    scans endpoint ids against tombstones (privacy.audit_project) and
    #    reports records/rows/bytes/by_state, so residue is a finding and
    #    growth is measured, never silent. --
    Surface("checkpoints/{slug}/relations.jsonl", "relations._append",
            True, "rewrite", "forget"),
    # -- serializer chunk cache: PRE-redaction by design (#125), so it can
    #    only be purged wholesale (#422); age reaper bounds survivors. --
    Surface("checkpoints/.chunk-cache/*", "serializer._save_chunk_cache",
            True, "wholesale-purge", "forget"),
    # -- receipts sidecars: hashes, method, nonce — bind to bytes, never
    #    copy them (receipts._sidecar_path). The suffix exemption. --
    Surface("checkpoints/**/*.receipt", "receipts._atomic_write_text",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # store._pointer_lock: empty flock sidecar, opened a+, never written.
    Surface("checkpoints/**/.pointer.lock", "store._pointer_lock",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # macOS Finder metadata: listing positions, never file contents.
    Surface("**/.DS_Store", "macOS Finder", False, "exempt-no-plaintext",
            "none", audit_exempt=True),
    # -- checkpoint JSON: the core belief state, flat and bucketed. --
    Surface("checkpoints/{slug}/*.json", "store.write_checkpoint",
            True, "rewrite", "forget"),
    Surface("checkpoints/*.json",
            "store.write_checkpoint / store._rotate_pointers",
            True, "rewrite", "forget"),
    # store._atomic_write staging twins; reaped by store._reap_stale_tmps.
    # Honest bounds (adversarial-review finding): the reaper walks the flat
    # dir plus ONE level of bucket subdirs, so a depth-2+ .tmp has no live
    # deletion mechanism; and any future .tmp-suffixed store auto-classifies
    # here, blinding the declaration ratchet — the conservative inherited
    # contract (plaintext=True, never audit_exempt) keeps the auditor
    # scanning such files regardless.
    Surface("checkpoints/**/*.tmp", "store._atomic_write",
            True, "wholesale-purge", "reaper"),
    # -- team mirror: forward plaintext copies. #600 slice A: forget now
    #    rewrites the author's OWN copies (store.scrub_team_copies); the
    #    remaining gap — teammates' copies and every clone's git history —
    #    needs tombstone propagation through the sync protocol, so the
    #    json entry stays a known-gap until that lands. --
    Surface("team/{remote}/README.md", "teamsync.init",
            False, "exempt-no-plaintext", "none"),
    Surface("team/{remote}/daimon-team.toml", "teamsync.init",
            False, "exempt-no-plaintext", "none"),
    # #600 slice B: the published tombstone ledger — {ts, key, author}, the
    # canonical hash and never the text (#321), the same posture
    # forget-hits.jsonl takes locally. Deliberately BEFORE the json entry
    # and named `.jsonl` so no `*.json` walk claims it.
    Surface("team/{remote}/**/tombstones.jsonl", "store.publish_tombstone",
            False, "exempt-no-plaintext", "none"),
    Surface("team/{remote}/**/*.json", "store._dual_write_team",
            True, "known-gap", "audit", issue="#600"),
    Surface("team/{remote}/.git/**", "git (teamsync subprocess)",
            True, "known-gap", "none", issue="#600"),
    # -- recall index: derived cache; rows leave at rebuild, dead snapshots
    #    from crashed rebuilds are reaped on sight. --
    Surface("recall.db.{pid}.tmp*", "recall.rebuild",
            True, "reap", "reaper"),
    Surface("recall.db", "recall.rebuild", True, "lazy-rebuild", "recall"),
    Surface("recall_seen/*.json", "cli._save_seen",
            False, "exempt-no-plaintext", "none"),
    # -- the crash sink: RAW child stderr, so its contents are whatever the
    #    serialize child wrote to fd 2. An uncaught traceback, yes — but also
    #    logging.lastResort output from any logger OUTSIDE the
    #    `daimon_briefing` hierarchy the #194 handler attaches to
    #    (`daimon.recall` and `daimon.briefing` are not under it). Any of it
    #    can carry item text.
    #    The WHOLESALE PURGE at forget and the write-seam trim are therefore
    #    the whole contract (#605): a value inside a traceback cannot be
    #    located when the tombstone is a hash — the chunk-cache situation —
    #    and the trim bounds what accumulates between forgets without a
    #    second reaper. The #92 excepthook redacts secrets on the way out,
    #    but it sees ONLY uncaught top-level exceptions in the child: a
    #    narrowing of what lands here, never a claim about the file, and
    #    nothing in this entry rests on it. --
    Surface("logs/serialize-crash.log", "cli serialize child stderr",
            True, "wholesale-purge", "forget"),
    Surface("logs/heartbeats/*", "ledger.touch_heartbeat",
            False, "exempt-no-plaintext", "none"),
    # -- backend diagnostics: stderr AND stdout of the LLM CLI child, which
    #    can echo prompt fragments — transcript text (#141). Secret-redacted
    #    and byte-bounded at the write seam (llm._log_backend_stderr), but
    #    item text is not a secret shape, so the honest declaration is the
    #    crash sink's (#616, same class #605 closed): plaintext, purged
    #    wholesale at forget — a value inside prose diagnostics cannot be
    #    located when the tombstone is a hash. BEFORE the *.log glob so the
    #    specific contract wins. --
    Surface("logs/backend-stderr.log", "llm._log_backend_stderr",
            True, "wholesale-purge", "forget"),
    # #616 restored the glob's claim instead of widening it: serializer's
    # downgrade lines — the one writer that put item text under this shape —
    # now log a content hash (normalize.content_key, the same key a forget
    # would tombstone), and forget scrubs the LEGACY payloads by line shape
    # (store.scrub_serialize_log) rather than purging serialize.log
    # wholesale, because that file is also the ledger `status` parses.
    Surface("logs/*.log", "ledger / cli._note_usage / recall._note_error",
            False, "exempt-no-plaintext", "none"),
    # -- key material and env: secrets, never item plaintext. --
    Surface("keys/signing.seed", "receipts._ensure_seed",
            False, "exempt-no-plaintext", "none"),
    Surface("keys/signing.pub.json", "receipts._ensure_pubkey",
            False, "exempt-no-plaintext", "none"),
    Surface("env", "configure.write_env", False, "exempt-no-plaintext",
            "none"),
    # codex host adapter stop stamps: an epoch float per session.
    Surface("codex/*.last-stop", "_hooks/daimon-codex-*.py",
            False, "exempt-no-plaintext", "none"),
    # -- windsurf host adapter state: FULL RAW TRANSCRIPTS appended turn by
    #    turn, plus unparsed event payloads. daimon-authored, so inside the
    #    deletion contract (#419) — unlike Codex rollouts or Claude Code
    #    JSONL, which daimon reads by path and never copies. #607: forget
    #    purges wholesale (a value inside prose cannot be located when the
    #    tombstone is a hash, the chunk-cache situation), heal reaps by age
    #    (config.windsurf_state_days), and the audit reports the store
    #    without claiming a verdict it cannot reach. --
    Surface("windsurf/transcripts/*.md", "_hooks/daimon-windsurf-hooks.py",
            True, "wholesale-purge", "forget"),
    Surface("windsurf/unparsed-*.json", "_hooks/daimon-windsurf-hooks.py",
            True, "wholesale-purge", "forget"),
    # trajectory activity/serialize stamps: epoch floats, no item text.
    Surface("windsurf/*.last-activity", "_hooks/daimon-windsurf-hooks.py",
            False, "exempt-no-plaintext", "none"),
    Surface("windsurf/*.last-serialize", "_hooks/daimon-windsurf-hooks.py",
            False, "exempt-no-plaintext", "none"),
    # installed hook copies under ~/.daimon/hooks — program text, not
    # belief bytes (cli._hooks_target_dir).
    Surface("hooks/*", "cli install-hooks", False, "exempt-no-plaintext",
            "none"),
)

_PLACEHOLDER_RE = re.compile(r"\{[a-z]+\}")

# {pid} is digits, not a bare wildcard: `recall.db.bak.tmp` beside a
# DAIMON_RECALL_DB override is a user's own backup and must stay UNDECLARED
# so the registry-derived reaper cannot touch it.
_PLACEHOLDER_GLOBS = {"{pid}": "[0-9]*"}


def _part_matches(shape_part: str, part: str) -> bool:
    if shape_part == part:
        return True
    pat = _PLACEHOLDER_RE.sub(
        lambda m: _PLACEHOLDER_GLOBS.get(m.group(0), "*"), shape_part)
    return fnmatch.fnmatchcase(part, pat)


def _parts_match(shape_parts: tuple, parts: tuple) -> bool:
    if not shape_parts:
        return not parts
    head, rest = shape_parts[0], shape_parts[1:]
    if head == "**":
        return any(_parts_match(rest, parts[i:])
                   for i in range(len(parts) + 1))
    return bool(parts) and _part_matches(head, parts[0]) \
        and _parts_match(rest, parts[1:])


def match(pattern: str) -> Surface | None:
    """Classify a path (or a write-audit-normalized pattern) against the
    registry. First declaration wins; None means UNDECLARED — the caller's
    cue to fail loudly, never to guess."""
    parts = tuple(p for p in pattern.split("/") if p)
    for s in SURFACES:
        if _parts_match(tuple(s.shape.split("/")), parts):
            return s
    return None


def exempt_names() -> frozenset:
    """Fixed-filename audit exemptions — privacy._EXEMPT_NAMES is this view."""
    out = set()
    for s in SURFACES:
        if not s.audit_exempt:
            continue
        name = s.shape.rsplit("/", 1)[-1]
        if not any(c in name for c in "*?[{"):
            out.add(name)
    return frozenset(out)


def exempt_suffix() -> str:
    """The one suffix-shaped audit exemption (receipts sidecars).

    Exactly one: privacy._is_plaintext_free compares a single suffix, so a
    second declaration would have declaration ORDER silently pick the
    winner — the guess this registry exists to forbid. Fail loudly both
    ways."""
    found = []
    for s in SURFACES:
        if s.audit_exempt:
            name = s.shape.rsplit("/", 1)[-1]
            if name.startswith("*."):
                found.append(name[1:])
    if len(found) != 1:
        raise LookupError(
            f"expected exactly one suffix-shaped exemption, got {found}")
    return found[0]
