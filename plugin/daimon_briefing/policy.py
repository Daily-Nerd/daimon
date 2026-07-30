"""Checkpoint admission policy (#421) — the ordered write-boundary pipeline.

Pure by the same contract carry.py documents: no file I/O, no env, no clock —
the caller (store.write_checkpoint) reads the forgotten-keys set off disk and
injects it, so this module owns the ORDER of the gates and nothing else. The
order is load-bearing and must not change:

1. redact_checkpoint — capture-time secret redaction (#104) runs FIRST, so
2. drop_forgotten — the value-keyed forget gate (#402) compares against the
   STORED (post-redaction) text the forget command keyed its tombstone on —
   a raw re-extraction of a redacted-then-forgotten sentence only folds to
   the tombstoned key because redaction already ran; and
3. stamp_item_ids — ids (#102) are stamped LAST, so they hash redacted text
   and a dropped item is never minted an id at all.

Like the helpers it absorbed, every function mutates its checkpoint argument
IN PLACE (the established store.py contract — callers rely on it).

#423 adds the INBOUND twin: admit_foreign runs where a teammate's synced
checkpoint enters local surfaces (read_team, the recall scan) — scope, local
re-redaction, the local forget gate, and the foreign verbatim->inferred
trust clamp. Same purity contract: membership, the forgotten set, and the
redact function are all injected by the caller.
"""

import hashlib

from . import normalize, redact, schema

# The five list sections that hold checkpoint items, from the shared schema
# (#146 — one definition; serializer/recall/carry derive theirs from the same
# table). active_topic is a single per-session dict and never needs an id (it
# does not carry, #33).
_ITEM_LISTS = schema.ITEM_LISTS


def redact_checkpoint(checkpoint: dict, redact_fn=None) -> None:
    """Capture-time secret redaction (#104): runs before this module's own
    stamp_item_ids call below, so ids stamped HERE hash redacted text. On
    the serialize path the cli stamps ids earlier (before bind_links, #14),
    so ids there hash pre-redaction text — no leak (sha1 slices are not
    reversible) and no consumer recomputes ids from text, but identity for
    secret-bearing items differs between the two paths.
    Covers text AND quote on every list item plus active_topic — verbatim
    quotes are the likeliest secret carriers. Stamps a visible
    checkpoint["redactions"] counter only when something was scrubbed.
    `redact_fn` (#423) lets the inbound gate inject the scrub function;
    the write path keeps the module-level default."""
    counts: dict = {}
    if redact_fn is None:
        redact_fn = redact.redact_text

    def _scrub(d: dict, field: str) -> None:
        val = d.get(field)
        red, c = redact_fn(val)
        if c:
            d[field] = red
            for k, n in c.items():
                counts[k] = counts.get(k, 0) + n

    for section, key in _ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                _scrub(item, "text")
                _scrub(item, "quote")
                _scrub(item, "scene")
                links = item.get("links")
                if isinstance(links, list):
                    for link in links:
                        if isinstance(link, dict) and isinstance(link.get("target"), str):
                            _scrub(link, "target")
    topic = (checkpoint.get("working_context") or {}).get("active_topic")
    if isinstance(topic, dict):
        _scrub(topic, "text")
        _scrub(topic, "quote")
        _scrub(topic, "scene")
    if counts:
        # MERGE, never overwrite: a re-write (anchor --attach reads, mutates,
        # writes the same dict) only re-matches NEW secrets — old markers don't
        # match the patterns again, so overwriting would drop kinds still
        # physically present in the checkpoint.
        merged = dict(checkpoint.get("redactions") or {})
        for k, n in counts.items():
            merged[k] = merged.get(k, 0) + n
        checkpoint["redactions"] = merged


def drop_forgotten(checkpoint: dict, forgotten_keys: set) -> list:
    """Value-keyed re-capture gate (#402): drop every item whose canonicalized
    text hashes into the injected forgotten set BEFORE it reaches the
    checkpoint on disk. This is what makes "a forgotten value stays gone" hold
    across a fresh re-extraction of the same sentence — not merely a
    render-time withhold, which leaves the value sitting on disk. Returns the
    dropped items so the caller can account the suppression hit (#404).

    The set comes from the CALLER (store.forgotten_content_keys reads the
    ledger — the one I/O dependency this module refuses to own). Fail-safe:
    over-suppresses on a hash collision (via the bounded content key) — a
    forgotten value re-appearing is the worse failure. A no-op with zero cost
    when nothing was ever forgotten here."""
    if not forgotten_keys:
        return []
    dropped: list = []
    for section, key in _ITEM_LISTS:
        block = checkpoint.get(section)
        if not isinstance(block, dict):
            continue
        lst = block.get(key)
        if not isinstance(lst, list):
            continue
        kept = []
        for item in lst:
            if (isinstance(item, dict)
                    and normalize.content_key(item.get("text") or "") in forgotten_keys):
                dropped.append(item)
            else:
                kept.append(item)
        if len(kept) != len(lst):
            block[key] = kept
    return dropped


def stamp_item_ids(checkpoint: dict) -> None:
    """Stable per-item ids (#102): sha1 of kind:text, 6 hex chars, prefixed
    with the kind's initial. setdefault semantics — an item that already
    carries an id (a carried twin, a re-write) is never re-stamped, so
    identity survives rotation and re-serialization. Collisions within one
    checkpoint widen the slice; identical-text twins fall through to a
    counter suffix (same text, same kind, still two loops)."""
    seen: set = set()
    for section, key in _ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                continue
            if item.get("id"):
                seen.add(item["id"])
                continue
            digest = hashlib.sha1(
                f"{key}:{item['text']}".encode("utf-8")).hexdigest()
            cand = ""
            for width in (6, 8, 12, 40):
                cand = f"{key[0]}-{digest[:width]}"
                if cand not in seen:
                    break
            n = 2
            while cand in seen:
                cand = f"{key[0]}-{digest[:6]}-{n}"
                n += 1
            item["id"] = cand
            seen.add(cand)


def admit_checkpoint(checkpoint: dict, forgotten_keys: set) -> list:
    """Run the full admission pipeline, in the only valid order (module
    docstring): redact, then the forget gate, then id-stamping. Mutates
    `checkpoint` in place; returns the forget-dropped items for the caller's
    hit accounting (#404)."""
    redact_checkpoint(checkpoint)
    dropped = drop_forgotten(checkpoint, forgotten_keys)
    stamp_item_ids(checkpoint)
    return dropped


def clamp_foreign_trust(checkpoint: dict) -> None:
    """#423: a foreign `verbatim` claim is structurally unverifiable on this
    machine — receipt verification resolves against the LOCAL checkpoint dir —
    so repeated foreign assertion must never read as verified capture (the
    manufactured-corroboration failure mode). Clamp the trust class to
    `inferred` (also the #413 authority ceiling the recall index scores by)
    and stamp `foreign_verbatim_claim` so the Teammates render can state both
    facts visibly: claimed verbatim, unverifiable here. Items that never
    claimed verbatim are left untouched — no marker they never earned."""
    def _clamp(item) -> None:
        if isinstance(item, dict) and item.get("trust") == "verbatim":
            item["trust"] = "inferred"
            item["foreign_verbatim_claim"] = True

    for section, key in _ITEM_LISTS:
        items = (checkpoint.get(section) or {}).get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            _clamp(item)
    _clamp((checkpoint.get("working_context") or {}).get("active_topic"))


def admit_foreign(checkpoint, *, member: bool, forgotten_keys: set,
                  redact_fn):
    """#423 inbound gate: the read/index-time twin of admit_checkpoint, run
    where FOREIGN content (a synced teammate's checkpoint) enters local
    surfaces. In-memory only — sidecar files and the git layer are never
    rewritten. Order mirrors the write pipeline where it overlaps:

    1. scope    — `member` is the caller's teamproject.in_scope answer for
                  the sidecar this checkpoint came from; not a member -> None
                  (caller skips the checkpoint entirely, default closed);
    2. redact   — the injected local redact_fn re-scrubs text/quote/scene
                  (+ active_topic): a teammate on an older daimon with fewer
                  secret patterns must not seed a durable cleartext copy here;
    3. forget   — items whose canonicalized text hashes into the injected
                  LOCAL forgotten set are dropped, so a teammate's checkpoint
                  cannot re-assert a value forgotten on this machine
                  (redaction first, same load-bearing order as the write side);
    4. trust    — clamp_foreign_trust above.

    Pure by the module contract: the caller injects membership, the forgotten
    set, and the redact function. Mutates `checkpoint` in place and returns
    it, or None when the checkpoint must not be admitted. Tolerant of
    legacy/malformed foreign blobs (missing sections, junk items) — the only
    hard rejection is a non-dict, which fails CLOSED rather than admit what
    the gate cannot inspect."""
    if not member:
        return None
    if not isinstance(checkpoint, dict):
        return None  # cannot inspect it -> cannot vouch for it
    redact_checkpoint(checkpoint, redact_fn)
    drop_forgotten(checkpoint, forgotten_keys)
    clamp_foreign_trust(checkpoint)
    return checkpoint
