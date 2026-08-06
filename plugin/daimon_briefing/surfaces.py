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
    # store.append_verification: "a POINTER and a REASON CODE, never the
    # rejected text" (store.py docstring).
    Surface("checkpoints/{slug}/verification.jsonl",
            "store.append_verification", False, "exempt-no-plaintext",
            "none", audit_exempt=True),
    # store.record_forget_hits: {ts, key} — "NEVER the text or any prefix".
    Surface("checkpoints/{slug}/forget-hits.jsonl",
            "store.record_forget_hits", False, "exempt-no-plaintext",
            "none", audit_exempt=True),
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
    # -- team mirror: forward plaintext copies, no tombstone propagation
    #    yet — THE declared gap. --
    Surface("team/{remote}/README.md", "teamsync.init",
            False, "exempt-no-plaintext", "none"),
    Surface("team/{remote}/daimon-team.toml", "teamsync.init",
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
    # -- logs: no item text by construction, except the crash sink, which
    #    captures raw serializer-child stderr — tracebacks can embed item
    #    text and nothing deletes it. Declared debt. --
    Surface("logs/serialize-crash.log", "cli serialize child stderr",
            True, "known-gap", "none", issue="#605"),
    Surface("logs/heartbeats/*", "ledger.touch_heartbeat",
            False, "exempt-no-plaintext", "none"),
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
    #    turn, plus unparsed event payloads (secret-scrubbed at write, never
    #    item-text scrubbed). The largest plaintext store daimon writes, and
    #    nothing in forget or the audit reaches it — THE declared gap after
    #    the team mirror. provenance.py reads the transcripts as a
    #    first-class source, so this is load-bearing, not vestigial. --
    Surface("windsurf/transcripts/*.md", "_hooks/daimon-windsurf-hooks.py",
            True, "known-gap", "none", issue="#607"),
    Surface("windsurf/unparsed-*.json", "_hooks/daimon-windsurf-hooks.py",
            True, "known-gap", "none", issue="#607"),
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
