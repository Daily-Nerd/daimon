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

import enum
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


class ReadPosture(str, enum.Enum):
    """What a reader does with a ledger in one health state (#1132 PR 10).

    OPEN reads the rows as if nothing happened. NOTE reads the rows and says
    so with a code. CLOSED withholds everything the ledger decides about.
    SKIP_SOURCE leaves one foreign source out and says so. `str` so a table
    prints as the words it holds."""
    OPEN = "open"
    NOTE = "note"
    CLOSED = "closed"
    SKIP_SOURCE = "skip-source"


# The four states a ledger can be in and not be OK, in the order a `read`
# column lists them. OK is OPEN on every row and is not a column.
READ_STATES = ("absent", "degraded", "transient", "unreadable")


class Writer(str, enum.Enum):
    """Who is writing: the class that picks a row of a `write` column.

    HUMAN is a verb a person ran. ADMISSION is new cognitive content entering
    through capture or `write-checkpoint`. EMITTER is a machine row written
    beside a capture (a candidate, a counter, a log line). CURE is a repair or
    a forget: it PROCEEDs on every row by construction, so it is never a
    column. No caller has a default: a writer that does not say which it is
    does not compile."""
    HUMAN = "human"
    ADMISSION = "admission"
    EMITTER = "emitter"
    CURE = "cure"


class WritePosture(str, enum.Enum):
    """What a write does with a ledger in one health state (#1132 PR 10b).

    PROCEED appends (a torn tail is healed first). REFUSE raises, so nothing
    is written and the caller says why. SKIP writes nothing and returns, for a
    machine row that must never cost the capture beside it."""
    PROCEED = "proceed"
    REFUSE = "refuse"
    SKIP = "skip"


# The three states a write column lists, in order. ABSENT and OK always
# PROCEED, and are not columns.
WRITE_STATES = ("degraded", "transient", "unreadable")


class FieldPath(NamedTuple):
    """One prose field of a ledger row: a key path into the row dict.
    `is_list` marks a top-level key holding a list of strings."""
    path: tuple[str, ...]
    is_list: bool = False


class Surface(NamedTuple):
    shape: str          # path pattern relative to ~/.daimon (syntax above)
    owner: str          # writer, as module.function
    plaintext: bool     # can item text/quote/scene/note ever land in it?
    delete: str         # one of DELETE_STRATEGIES
    walker: str         # who walks it: forget | audit | recall | reaper | none
    issue: str = ""     # required when delete == "known-gap"
    audit_exempt: bool = False  # feeds privacy's name/suffix exemption sets
    # -- ledger columns (#1132), jsonl shapes under checkpoints/{slug}/ only.
    #    A column is filled only where its consumer already reads it here;
    #    read, write, deleter and phase stay empty until theirs do. --
    fold: str = ""                    # dotted pure fold, e.g. "trust.fold"
    prose: tuple[FieldPath, ...] = ()  # plaintext row fields
    # Read posture per state, in READ_STATES order (absent, degraded,
    # transient, unreadable); `foreign_read` is the same for a ledger read
    # across buckets or authors. `write` is the same per writer class.
    read: tuple[ReadPosture, ...] = ()
    # Write posture per writer class, each in WRITE_STATES order (degraded,
    # transient, unreadable). Immutable: a tuple of (Writer, postures).
    write: tuple[tuple[Writer, tuple[WritePosture, ...]], ...] = ()
    foreign_read: tuple[ReadPosture, ...] = ()
    index_content: bool = False       # recall._fingerprint input (scar 0107)
    mergeable: bool = False           # a legacy-bucket migration moves it
    # Bytes of an append-only log judged for a write posture (0 = the whole
    # file). A log that grows without bound must not be parsed per write.
    tail_bytes: int = 0
    deleter: str = ""                 # forget registry (later PR)
    phase: str = ""                   # forget registry (later PR)


# A repair moves the lines a ledger cannot parse out of it and into
# `<stem>.quarantined-lines` beside it (#1132 2c-2): one envelope row per line.
QUARANTINE_SIDECAR_SUFFIX = ".quarantined-lines"


def quarantine_sidecar(ledger_name: str) -> str:
    """The sidecar file name for a bucket ledger: "events.jsonl" ->
    "events.quarantined-lines"."""
    return ledger_name.removesuffix(".jsonl") + QUARANTINE_SIDECAR_SUFFIX


def is_quarantine_sidecar(name: str) -> bool:
    return name.endswith(QUARANTINE_SIDECAR_SUFFIX)


def quarantine_prose() -> tuple["FieldPath", ...]:
    """The sidecar's `prose` column (the envelope `text`), from its row."""
    for s in SURFACES:
        if s.shape.endswith("*" + QUARANTINE_SIDECAR_SUFFIX):
            return s.prose
    raise LookupError("quarantine sidecar surface is not declared")


def _scalars(*names: str) -> tuple[FieldPath, ...]:
    return tuple(FieldPath((n,)) for n in names)


LOG_TAIL_BYTES = 64 * 1024   # the window a write judges an append-only log by

_RP = ReadPosture
# R2.3, the human copy lives in tests/test_read_posture_registry.py.
_READ_NOTED = (_RP.OPEN, _RP.NOTE, _RP.NOTE, _RP.NOTE)
_READ_TRUST = (_RP.OPEN, _RP.NOTE, _RP.CLOSED, _RP.CLOSED)
_READ_COUNTERS = (_RP.OPEN, _RP.OPEN, _RP.OPEN, _RP.NOTE)
_READ_FOREIGN = (_RP.OPEN, _RP.NOTE, _RP.SKIP_SOURCE, _RP.SKIP_SOURCE)

_WP = WritePosture
# R2.3 write column, the human copy lives in tests/test_write_posture_registry.py.
# An unproven ledger (TRANSIENT or any UNREADABLE) is REFUSE for a person and
# for admission, SKIP for a machine row, never a silent drop; DEGRADED is
# proven, so every writer PROCEEDs.
_WRITE_REFUSED = (_WP.PROCEED, _WP.REFUSE, _WP.REFUSE)
_WRITE_SKIPPED = (_WP.PROCEED, _WP.SKIP, _WP.SKIP)
_WRITE_OPEN = (_WP.PROCEED, _WP.PROCEED, _WP.PROCEED)
_WR = Writer
_W_HUMAN = ((_WR.HUMAN, _WRITE_REFUSED),)
_W_HUMAN_EMITTER = ((_WR.HUMAN, _WRITE_REFUSED), (_WR.EMITTER, _WRITE_SKIPPED))
_W_EMITTER = ((_WR.EMITTER, _WRITE_SKIPPED),)

SURFACES: tuple[Surface, ...] = (
    # -- per-project bucket ledgers (specific before the *.json generics) --
    Surface("checkpoints/{slug}/events.jsonl", "store.append_event",
            True, "append-tombstone", "forget",
            prose=_scalars("note", "item_text", "status"),
            index_content=True, mergeable=True, read=_READ_NOTED,
            write=((_WR.HUMAN, _WRITE_REFUSED), (_WR.ADMISSION, _WRITE_REFUSED),
                   (_WR.EMITTER, _WRITE_SKIPPED))),
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
    #    `subject`..`note` are scalar prose; `anchors`/`evidence` are lists;
    #    `check.match`/`check.body` (#943) are nested. `author` is absent on
    #    purpose: a person's name, not item text. --
    Surface("checkpoints/{slug}/refutations.jsonl", "refutations.append",
            True, "rewrite", "forget", fold="refutations.fold",
            prose=_scalars("subject", "verdict", "scope", "revisit_when",
                           "note") + (
                FieldPath(("anchors",), True), FieldPath(("evidence",), True),
                FieldPath(("check", "match")), FieldPath(("check", "body"))),
            mergeable=True, read=_READ_NOTED, foreign_read=_READ_FOREIGN,
            write=_W_HUMAN),
    # -- the amendment ledger (#691): the fourth bucket ledger — evidence
    #    quotes and human-channel notes, both length-capped, both plaintext
    #    by design, so it sits in the checkpoint's deletion category with
    #    refutations. `rewrite` covers its two deleters:
    #    amendments.forget_content_key (by value, whole-value canonical
    #    match) and amendments.forget_item_id (rows about a forgotten item
    #    go with it — unlike relations, these rows carry prose that can
    #    paraphrase the removed content). Never audit_exempt: the audit
    #    hashes the ledger's own `prose` column and checks
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
            True, "rewrite", "forget", fold="amendments.fold",
            prose=_scalars("evidence", "note"), mergeable=True,
            read=_READ_NOTED, foreign_read=_READ_FOREIGN,
            write=_W_HUMAN_EMITTER),
    # -- the request ledger (#694): the fifth bucket ledger — one project's
    #    ask of another, so its rows carry the ask, its rationale, a human
    #    verdict note, and a completion quote: plaintext by design, in the
    #    checkpoint's deletion category with refutations and amendments.
    #    `rewrite` is requests.forget_content_key, matching the same
    #    whole-value canonical key the checkpoint splice uses.
    #
    #    The cross-project shape is what makes deletion LOCAL and complete:
    #    the recipient never holds a copy (it reads through to this file at
    #    brief time), so one rewrite here removes the ask from every reader
    #    at once — no tombstone propagation, nothing to chase. Rows this
    #    bucket wrote ABOUT a foreign request are this bucket's own prose
    #    and go by the same path; the foreign origin rows are the other
    #    side's to delete, which is the whole point of mutual read-through.
    #
    #    Never audit_exempt: the audit hashes the module's own
    #    `prose` column (privacy.audit_project) and prints
    #    record/row/byte counts, so growth is measured, never silent. Same
    #    honest limit as the amendment ledger — forget matches a WHOLE
    #    value, so an ask merely CONTAINING a forgotten value is beyond the
    #    hash scan. No age reaper: attention decay (#694 D3) is DERIVED at
    #    render time and deliberately never deletes a record — a request
    #    that stopped mattering still happened. --
    Surface("checkpoints/{slug}/requests.jsonl", "requests.append",
            True, "rewrite", "forget", fold="requests.fold",
            prose=_scalars("ask", "why", "note", "evidence", "from_label",
                           "act_author"), mergeable=True, read=_READ_NOTED,
            foreign_read=_READ_FOREIGN, write=_W_HUMAN_EMITTER),
    # store.append_verification: "a POINTER and a REASON CODE, never the
    # rejected text" (store.py docstring).
    Surface("checkpoints/{slug}/verification.jsonl",
            "store.append_verification", False, "exempt-no-plaintext",
            "none", audit_exempt=True, index_content=True, mergeable=True,
            read=_READ_COUNTERS, write=_W_EMITTER),
    # store.record_forget_hits: {ts, key, reason?} — "NEVER the text or any
    # prefix"; reason (#693) is a closed-vocabulary code ("ruling-echo").
    Surface("checkpoints/{slug}/forget-hits.jsonl",
            "store.record_forget_hits", False, "exempt-no-plaintext",
            "none", audit_exempt=True, mergeable=True,
            read=_READ_COUNTERS, write=_W_EMITTER),
    # -- the bucket root record (#1092): one line, the absolute resolved
    #    directory that FIRST wrote to this bucket. store.record_bucket_root
    #    stamps it once, on the first ledger/checkpoint write, and never
    #    overwrites it after. Holds a filesystem path, never item text, quote,
    #    scene, or note: no field of it is user-authored content, so it
    #    carries nothing forget is ever asked to reach and no rewrite path
    #    exists for it. `config.layer_scopes` is the one reader. --
    Surface("checkpoints/{slug}/root", "store.record_bucket_root",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # -- the ledger census marker (#1132 PR 2b): written once per bucket, on
    #    its first checkpoint write, by ledger_census.record_marker. A version,
    #    a UTC stamp, and per bucket-ledger FILE NAME -> {state, torn, split,
    #    garbage, tombstoned_present}: closed-vocabulary states and integer
    #    counts. `record_marker` builds the whole document from the census,
    #    which returns names, states and counts and no row content, so there is
    #    no path from item text into it and nothing for forget to reach. --
    Surface("checkpoints/{slug}/.ledger-census", "ledger_census.record_marker",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # -- the quarantine sidecar (#1132 2c-2): `daimon ledger repair` moves
    #    every torn or garbage line out of a ledger into
    #    `<stem>.quarantined-lines`, one JSONL envelope row per line,
    #    {ledger, quarantined_at, kind, text}. `text` is the original line
    #    VERBATIM (undecodable bytes as \xNN escapes), so it holds whatever
    #    plaintext the torn row held and sits inside the deletion contract.
    #    `rewrite` is ledger_repair.forget_quarantined_lines: forget purges
    #    the envelope rows holding the value (by text when it has the text,
    #    else by canonical key over the JSON strings in the line), and every
    #    other row stays. Mergeable: a legacy-bucket merge by concatenation
    #    is safe, the rows are independent. Not a `bucket_ledger` (the shape
    #    is a glob, one file per ledger), so census and privacy list the
    #    files by suffix. --
    Surface("checkpoints/{slug}/*" + QUARANTINE_SIDECAR_SUFFIX,
            "ledger_repair.quarantine_lines", True, "rewrite", "forget",
            prose=_scalars("text"), mergeable=True, read=_READ_NOTED, write=_W_HUMAN,
            deleter="ledger_repair.forget_quarantined_lines"),
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
            True, "rewrite", "forget", fold="relations.fold",
            mergeable=True, read=_READ_NOTED, write=_W_HUMAN),
    # -- the trust ledger (#1109 Slice 1): a human-only quarantine verdict on
    #    a checkpoint value, append-only like refutations.jsonl, and in the
    #    same category — it carries item PLAINTEXT by design (`reason`,
    #    `evidence`), so `rewrite`/`forget` must be able to reach it exactly
    #    the way they reach refutations.jsonl today. `value_key` is a hash,
    #    never plaintext, and is not in the deletion contract (it names no
    #    item text on its own). `rewrite` is trust.redact_content_key: it
    #    REDACTS prose in place and never drops a record (a drop would lift
    #    the quarantine), matching the same whole-value canonical key
    #    refutations.py uses.
    #    Never audit_exempt: growth must be measured, never silent, the same
    #    posture every other plaintext ledger here holds. Readers: the briefing,
    #    recall's rebuild (hence `index_content`) and the cli quarantine reads
    #    read the ACTIVE quarantines through trust.active_value_keys, and
    #    pending.queue lists the PROPOSED ones for a human. Registered so it
    #    can never repeat #645's unknown->unscannable->exit-3 arc. --
    Surface("checkpoints/{slug}/trust.jsonl", "trust.append",
            True, "rewrite", "forget", fold="trust.fold",
            prose=(FieldPath(("reason",)), FieldPath(("evidence",), True)),
            index_content=True, mergeable=True, read=_READ_TRUST, write=_W_HUMAN),
    # -- the request-policy tombstones (#961 slice 5): one row per activation
    #    interval of a ruling that forget has since removed, so a forgotten
    #    ruling's `info` asks do not silently flip to `work` (refutations.
    #    _write_policy_tombstones writes, _read_policy_tombstones reads).
    #    A row is {sender, to, kind, verb, by, ruling_id, policy_sha256,
    #    active_from, active_until}: two bucket slugs, three closed-enum
    #    strings, an opaque ruling id, a hash and two integer stamps. The
    #    writer states that none of it is ruling prose, so nothing here is
    #    reachable by value and exempt-no-plaintext is the true class.
    #    Mergeable because the reader folds the rows into a SET of tuples:
    #    order never matters and a duplicated line adds nothing, so a
    #    legacy-bucket merge by concatenation is safe. Left undeclared until
    #    #1132, so a migration stranded it and the audit called it unknown. --
    Surface("checkpoints/{slug}/request_policy_tombstones.jsonl",
            "refutations._write_policy_tombstones", False,
            "exempt-no-plaintext", "none", audit_exempt=True,
            mergeable=True, read=_READ_NOTED, write=_W_HUMAN),
    # -- the bucket-migration receipt (#963): one line per move that actually
    #    moved something, {version, ts, from_slug, to_slug, mode, ledgers,
    #    pointers, leftovers, unreadable, stranded_pointers,
    #    target_unreadable, target_slots, complete, observed, by}.
    #    Global rather than per-bucket on purpose — it names a bucket that no
    #    longer exists, so it cannot live inside one.
    #
    #    `complete` is the one field with reach beyond reporting: only a
    #    complete row mints an alias, because an alias claims the old
    #    bucket's history now lives here and a partial move has not made that
    #    true. `unreadable`, `target_unreadable` and `stranded_pointers` name
    #    what stayed behind (a ledger that would not decode, a target pointer
    #    that could not be parsed, a pointer with no free slot under
    #    DAIMON_CHECKPOINT_HISTORY); all are file names or session ids.
    #    `target_slots` is how many pointer slots the target holds after the
    #    run, recorded so the remedy for a stranded pointer is arithmetic on
    #    what is there rather than on the current knob.
    #    `observed` is free text for a state the fields cannot carry, and
    #    today holds one value: a legacy bucket a person cleared themselves.
    #
    #    exempt-no-plaintext, and the guarantee is `buckets._record`, which
    #    builds the whole row: two slugs the caller's own path already
    #    derives, a UTC stamp, a mode from a closed set, per-ledger LINE
    #    COUNTS (never lines), the FILE NAMES a merge did not understand, and
    #    a literal channel. No item text, quote, scene or note has a path
    #    into it, and no ledger content is copied through it. The slugs are
    #    the bucket directory names the store already shows the auditor.
    #
    #    Deletion is `none` for the same reason the shape exists: this file
    #    is what makes rows moved out of a legacy bucket reachable again
    #    (recall's alias mapping, the requests recipient join). Dropping a
    #    row would re-orphan exactly the history the migration rescued, and
    #    it holds nothing forget is asked to reach. --
    Surface("checkpoints/migrations.jsonl", "buckets._append_record",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # -- serializer chunk cache: PRE-redaction by design (#125), so it can
    #    only be purged wholesale (#422); age reaper bounds survivors. --
    Surface("checkpoints/.chunk-cache/*", "serializer._save_chunk_cache",
            True, "wholesale-purge", "forget"),
    # -- receipts sidecars: hashes, method, nonce — bind to bytes, never
    #    copy them (receipts._sidecar_path). The suffix exemption. --
    Surface("checkpoints/**/*.receipt", "receipts._atomic_write_text",
            False, "exempt-no-plaintext", "none", audit_exempt=True),
    # jsonl.dir_lock (store._pointer_lock delegates): empty flock sidecar,
    # opened a+, never written; every bucket ledger append and rewrite takes it.
    Surface("checkpoints/**/.pointer.lock", "jsonl.dir_lock",
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
            False, "exempt-no-plaintext", "none",
            foreign_read=_READ_FOREIGN,
            # Append-only and read per line, the reader skips a bad row: the
            # own sidecar is a write target in every state (R2.3).
            write=((_WR.HUMAN, _WRITE_OPEN),)),
    Surface("team/{remote}/**/*.json", "store._dual_write_team",
            True, "known-gap", "audit", issue="#600"),
    Surface("team/{remote}/.git/**", "git (teamsync subprocess)",
            True, "known-gap", "none", issue="#600"),
    # -- recall index: derived cache. A withheld row is never inserted (every
    #    row is judged before it is written), and a rebuild stages into
    #    `recall.db.<pid>.tmp.<token>` (mode 0600), so the shape below covers
    #    both that name and the bare `recall.db.<pid>.tmp` an older build left.
    #    Dead snapshots from crashed rebuilds are reaped on sight. --
    Surface("recall.db.{pid}.tmp*", "recall.rebuild",
            True, "reap", "reaper"),
    Surface("recall.db", "recall.rebuild", True, "lazy-rebuild", "recall"),
    # -- per-store indexes (D9.6): any store but the default keeps its index
    #    at `recall/<hash>.db`, and its staging twins follow the same shapes.
    #    The audit scans every cache whose meta.store is the audited store;
    #    heal reaps the ones whose store is gone or untouched for 30 days. --
    Surface("recall/{hash}.db.{pid}.tmp*", "recall.rebuild",
            True, "reap", "reaper"),
    Surface("recall/*.db", "recall.rebuild", True, "lazy-rebuild", "recall"),
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
    # -- #943 check firing log: one row per runner invocation, carrying ids,
    #    outcomes, causes and durations only. No command text, no paths, no
    #    subject — and that is a property of the WRITER, which projects every
    #    row onto a fixed key list rather than trusting its callers, so the
    #    exemption rests on code and not on a convention. Before the *.log
    #    glob, which would not catch a .jsonl anyway, so the shape sits with
    #    the other log declarations instead of after the catch-all.
    #
    #    RETENTION (#955): bounded, and by the WRITER. log_firing caps the
    #    file at 256 KiB and keeps the last 64 KiB, the crash log's numbers,
    #    so the walker stays `none` and no reaper owns this shape. The bound
    #    is a size and not an age: a busy host writes a row per shell action
    #    and a quiet one writes almost none, so a day-based rotation would
    #    keep a quiet machine's rows forever and lose a loud one's in
    #    batches. Every surface that folds this log names the window it has
    #    left rather than calling its counts lifetime. --
    Surface("logs/checks.jsonl", "checks_runtime.log_firing",
            False, "exempt-no-plaintext", "none"),
    # Recall delivery telemetry carries ids, scores, counts and a fixed
    # surface label only. Query text is deliberately not persisted, so this
    # is an auditable machine-local measurement surface without item prose.
    Surface("logs/recall-delivery.jsonl", "recall_telemetry.record",
            False, "exempt-no-plaintext", "none", write=_W_EMITTER,
            tail_bytes=LOG_TAIL_BYTES),
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
    # -- #943 armed checks. Both shapes carry AUTHORED text: the manifest
    #    holds `check.match` and each body IS `check.body`, and refutations
    #    already declares that pair plaintext (its `prose` column) so forget
    #    reaches it in the ledger. Declaring these exempt would be the same
    #    claim contradicting itself one directory over.
    #
    #    `rewrite` is what checks.sync already does: it rebuilds the manifest
    #    from the ledger and removes the bodies no active ruling wants, and
    #    every writer that can disarm a ruling calls it — forget included. So
    #    deletion reaches here by re-deriving from a ledger the forget
    #    already rewrote, rather than by a second walk that could disagree
    #    with the first. --
    Surface("checks/manifest.json", "checks.sync", True, "rewrite", "forget"),
    Surface("checks/*.sh", "checks.sync", True, "rewrite", "forget"),
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


# #620: what a FOREIGN tombstone apply actually reaches.
#
# store.apply_foreign_tombstones calls scrub_content_key and nothing else,
# and that walk is *.json under the checkpoint dir. These two shapes are the
# whole of its coverage. A local `daimon forget` reaches far more.
#
# Declared here rather than inferred from the walk, deliberately. Inferring
# it would make the covered set and the walk the same statement, and a test
# over it could not fail for a class the walk misses -- the tautology already
# recorded on #620 and generalised as #944. Adding a plaintext surface to
# SURFACES therefore widens the reported GAP on its own, which is the safe
# direction: a new class is assumed unreached until someone says otherwise.
FOREIGN_APPLY_SHAPES: frozenset = frozenset({
    "checkpoints/{slug}/*.json",
    "checkpoints/*.json",
})


def foreign_apply_gap() -> tuple[str, ...]:
    """Plaintext surface shapes a foreign tombstone apply does NOT reach.

    The caller prints these instead of an unqualified success line. An
    irreversible, machine-wide operation that reports a clean sweep it did
    not perform is worse than one that refuses: the user spends a one-way,
    no-undo consent and is told it worked.

    Never empty in practice, and the test says so: an empty return would
    assert full coverage, which is the claim this exists to prevent anyone
    making by accident."""
    return tuple(sorted(
        s.shape for s in SURFACES
        if s.plaintext and s.shape not in FOREIGN_APPLY_SHAPES))


def read_posture(row: Surface, state: str, *,
                 foreign: bool = False) -> ReadPosture:
    """The read posture of `row` in `state` (a `jsonl.Health` value, as a
    word). OK is OPEN on every row and is not a column; `foreign` asks the
    column for a ledger read across buckets or authors."""
    if state == "ok":
        return ReadPosture.OPEN
    column = row.foreign_read if foreign else row.read
    return column[READ_STATES.index(state)]


def write_posture(row: Surface, writer: Writer, state: str) -> WritePosture:
    """The write posture of `row` for `writer` in `state` (a `jsonl.Health`
    value, as a word). OK and ABSENT always PROCEED, and so does CURE on any
    row: a repair must be able to write the ledger it is repairing. A writer
    class the row never declared is a bug in the caller, not a PROCEED, so
    it raises LookupError whatever the ledger's state (a bug must not wait
    for the day the ledger breaks to show itself)."""
    if writer is Writer.CURE:
        return WritePosture.PROCEED
    for declared, column in row.write:
        if declared is writer:
            if state in ("ok", "absent"):
                return WritePosture.PROCEED
            return column[WRITE_STATES.index(state)]
    raise LookupError(f"{row.shape} declares no {writer.value} write posture")


def ledger_hint(name: str, state: str, detail: str = "",
                unscannable: str = "", *, on_status: bool = False) -> str:
    """What to do about a ledger in `state`: retry a transient failure, check
    permissions after an OS error (the errno is in `unscannable`), repair a
    degraded or garbage ledger. `state` is a `jsonl.Health` value. On the
    `status` verb itself ("run: daimon status" would send the reader in a
    circle) the pointer back to it is dropped and the path is printed
    instead."""
    if state == "transient":
        return "retry"
    if str(detail).startswith("fold raised"):
        return "check the ledger file" if on_status else "run: daimon status"
    if state == "unreadable" and unscannable and unscannable != "undecodable":
        hint = f"check permissions ({unscannable})"
        return hint if on_status else hint + "; run: daimon status"
    if name == "trust.jsonl":
        return "run: daimon trust repair"
    return f"run: daimon ledger repair {name.removesuffix('.jsonl')}"


def write_row(name: str) -> Surface:
    """The registry row that declares a write column for the ledger file
    `name` ("events.jsonl", "events.quarantined-lines", "tombstones.jsonl").
    Raises LookupError for a name no row declares a write column for: a
    writer asking about a ledger the registry never governed is a bug."""
    for s in SURFACES:
        if s.write and _part_matches(s.shape.split("/")[-1], name):
            return s
    raise LookupError(f"no declared write column for ledger {name!r}")


def match(pattern: str) -> Surface | None:
    """Classify a path (or a write-audit-normalized pattern) against the
    registry. First declaration wins; None means UNDECLARED — the caller's
    cue to fail loudly, never to guess."""
    parts = tuple(p for p in pattern.split("/") if p)
    for s in SURFACES:
        if _parts_match(tuple(s.shape.split("/")), parts):
            return s
    return None


_BUCKET_LEDGER_PREFIX = "checkpoints/{slug}/"


def _bucket_ledger_rows() -> list[tuple[str, Surface]]:
    """(file name, row) for every fixed-name jsonl ledger in a bucket."""
    out = []
    for s in SURFACES:
        if not (s.shape.startswith(_BUCKET_LEDGER_PREFIX)
                and s.shape.endswith(".jsonl")):
            continue
        name = s.shape[len(_BUCKET_LEDGER_PREFIX):]
        if "/" in name or any(c in name for c in "*?[{"):
            continue
        out.append((name, s))
    return out


def bucket_ledger(name: str) -> Surface:
    """The registry row for a bucket ledger file name, e.g. "trust.jsonl".
    Raises LookupError on an undeclared name: a consumer asking about a
    ledger the registry never declared is a bug, not an empty answer."""
    for ledger_name, s in _bucket_ledger_rows():
        if ledger_name == name:
            return s
    raise LookupError(f"undeclared bucket ledger: {name!r}")


def bucket_ledger_names(*, plaintext: bool | None = None) -> tuple[str, ...]:
    """Every declared bucket ledger file name, in registry order; with
    `plaintext` given, only the ledgers whose declaration matches."""
    return tuple(n for n, s in _bucket_ledger_rows()
                 if plaintext is None or s.plaintext == plaintext)


def scalar_prose_fields(name: str) -> tuple[str, ...]:
    """Top-level non-list prose keys of a ledger, in declaration order."""
    return tuple(fp.path[0] for fp in bucket_ledger(name).prose
                 if len(fp.path) == 1 and not fp.is_list)


def prose_values(prose: tuple[FieldPath, ...], row: dict, *,
                 scalars_only: bool = False) -> list[str]:
    """The non-blank string values of `row` at the declared prose paths, in
    declaration order, unstripped. `scalars_only` keeps top-level non-list
    paths: the by-value forget MENU offers those and nothing shared across
    records (anchors, check bodies)."""
    out: list[str] = []
    for fp in prose:
        if scalars_only and (fp.is_list or len(fp.path) != 1):
            continue
        holder: object = row
        for key in fp.path:
            holder = holder.get(key) if isinstance(holder, dict) else None
        if fp.is_list:
            values = holder if isinstance(holder, list) else []
        else:
            values = [holder]
        out.extend(v for v in values if isinstance(v, str) and v.strip())
    return out


def mergeable_ledgers() -> tuple[str, ...]:
    """Ledger names a legacy-bucket migration moves, in registry order."""
    return tuple(n for n, s in _bucket_ledger_rows() if s.mergeable)


def mergeable_files() -> tuple[str, ...]:
    """Every file a legacy-bucket migration moves: the mergeable ledgers,
    each followed by its quarantine sidecar."""
    return tuple(f for n in mergeable_ledgers()
                 for f in (n, quarantine_sidecar(n)))


def index_content_ledgers() -> frozenset:
    """Bucket file NAMES whose mtime/size feed recall's index fingerprint:
    the ledgers rebuild() folds into index columns or row drops (#245,
    scar 0107). Names, never a glob."""
    return frozenset(n for n, s in _bucket_ledger_rows() if s.index_content)


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
