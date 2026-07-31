"""Serialized-checkpoint cache for the LongMemEval harness (#267).

Serializing a haystack session is the one expensive step (an LLM call, minutes
each). The cache keys the produced checkpoint by the exact things that determine
it — the session's message content, the backend, the model, and the serializer
prompt version — so a re-run pays the LLM only for sessions it has never seen
under the current config. A backend, model, or prompt-version change misses on
purpose: a cached checkpoint from a different pipeline is a different measurement.

The cache stores the RAW `serialize_strict` output (pre-store mutation). Callers
deep-copy on write, so a cached checkpoint is never mutated in place by the store.

#343: entries additionally record the SERVED model that produced them (the
wire's `response.model`, #458) and are verified against the run's pinned served
model on read — see CheckpointCache. The served model is deliberately NOT part
of `cache_key`: it is unknowable before the call is made (chicken-egg), so the
verify-on-read envelope is the mechanism, never the key.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


class ServedModelMismatch(RuntimeError):
    """#343: a wire receipt named a served model other than the run's pin.

    Raised by the adapter when a question's serialize was (or may have been)
    produced by a model different from the one this run is pinned to — a
    gateway silently substituting under quota/error (scar 0032). The question
    carrying it must be recorded as an error row and never scored; nothing it
    produced may enter the cache (scar 0015: caches replay whatever they were
    fed, so the gate sits at write time)."""

    def __init__(self, pinned: str | None, observed: list):
        self.pinned = pinned
        self.observed = sorted(observed)
        super().__init__(
            f"served model(s) {', '.join(self.observed) or '<none>'} != run pin "
            f"'{pinned}' — a gateway substituted the model mid-run (#343); this "
            "question fails loudly (error row, not scored) and its checkpoints "
            "are not cached")


def cache_key(messages: list[dict], *, backend: str, model: str,
              prompt_version: str, carry: str = "off",
              scene: str = "off") -> str:
    """Stable content hash over (turns, backend, model, prompt_version, carry,
    scene).

    Turns are hashed in order and by role — a reordered transcript is a different
    session. Only role/content are hashed (the fields the serializer reads), so an
    incidental metadata field on a turn does not bust the cache.

    `carry` (#274) namespaces the run mode: a carry-on run must never read a
    carry-off entry, or vice versa. The separation is deliberately defensive —
    today the cached blob is the raw pre-carry `serialize_strict` output (the
    fold happens downstream, at store-write time), but the cache must stay
    correct even if serialization ever becomes mode-sensitive. Carry-off keys
    are byte-identical to pre-#274 keys, so the existing cache (minutes of LLM
    per entry) stays valid for carry-off runs.

    `scene` (#319) namespaces the scene-traces flag (#317): unlike carry, the
    flag changes the serialize prompt itself, so a scene-on run reusing a
    scene-off entry would measure the cache, not the flag. Scene-off keys are
    byte-identical to pre-#319 keys, same preservation rule as carry.

    `model` here is the CONFIGURED gateway alias — routing config, not
    provenance (scar 0032). The model that actually served is unknowable
    until after the call, so it can never be part of the key; #343 records
    it in the entry envelope instead and verifies it on read.
    """
    h = hashlib.sha256()
    h.update(f"v1\x00{backend}\x00{model}\x00{prompt_version}\x00".encode())
    if carry != "off":
        h.update(f"carry\x00{carry}\x00".encode())
    if scene != "off":
        h.update(f"scene\x00{scene}\x00".encode())
    for m in messages:
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        h.update(role.encode("utf-8"))
        h.update(b"\x1f")
        h.update(content.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


class CheckpointCache:
    """A directory of `<key>.json` checkpoint files. One instance per run.

    Best-effort by design: a corrupt or unreadable entry is a miss, never a
    crash — a re-serialize is always safe, only slower.

    #343 provenance contract. The run holds ONE served-model pin
    (`pinned_served`): the explicit expectation when given (`expected_served`,
    from --expect-served / BENCH_EXPECT_SERVED), otherwise the first served
    receipt observed — from a live serialize (`check_served`) or from a
    replayed entry's recorded producer (`get`), whichever comes first; a
    replayed checkpoint's content joins this run's scores exactly like a live
    one, so its receipt is an observation too, and adopting it makes a warm
    cache holding MIXED producers fail loudly instead of replaying both.
    On disk each entry is an envelope `{"served_model": ..., "checkpoint":
    {...}}` — `served_model` is the wire's `response.model` receipt, or null
    when no receipt existed (command backend: honest absence, never the
    configured alias copied in — scar 0032). Reads verify the envelope against
    the pin; any disagreement, either direction, is a counted miss.
    """

    def __init__(self, cache_dir: Path, expected_served: str | None = None):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self.expected_served = expected_served
        self.pinned_served = expected_served
        # #343 miss reasons, surfaced in the run report's cost block so a
        # producer-verification miss is never silently indistinguishable
        # from a cold cache.
        self.model_mismatch_misses = 0
        self.legacy_misses = 0

    def check_served(self, served: list) -> str | None:
        """Fold live wire receipts into the pin; return the first offender.

        First receipt with no pin set → becomes the pin. Any receipt that
        disagrees with the pin → returned (the caller must fail the question
        loudly and skip the cache write). None → all receipts consistent."""
        for name in served:
            if self.pinned_served is None:
                self.pinned_served = name
            elif name != self.pinned_served:
                return name
        return None

    def _path(self, key: str) -> Path:
        # keys are hex digests (or test literals) — no separators to contain.
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        try:
            entry = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.misses += 1
            return None
        if not isinstance(entry, dict):
            self.misses += 1
            return None
        if "checkpoint" not in entry:
            # Pre-#343 entry: a raw checkpoint dict with no producer receipt —
            # exactly the unattributable shape the poisoning incident left
            # behind (~250 entries purgeable only by mtime), so it can never
            # be replayed. Poisoning-safe default; the cost is a one-time
            # cache re-warm after upgrading (each entry re-pays its LLM call
            # once, then carries a receipt forever).
            self.legacy_misses += 1
            self.misses += 1
            return None
        checkpoint = entry.get("checkpoint")
        if not isinstance(checkpoint, dict):
            self.misses += 1
            return None
        recorded = entry.get("served_model")
        if recorded is None:
            # Receiptless entry (command backend). Replayable only into a run
            # that is itself receiptless — a pinned run must not score
            # unattributable content (#343).
            if self.pinned_served is not None:
                self.model_mismatch_misses += 1
                self.misses += 1
                return None
        elif self.pinned_served is None:
            self.pinned_served = recorded  # first observation of the run
        elif recorded != self.pinned_served:
            # Producer disagrees with the run's pin: the entry was made by a
            # different served model — a MISS, never replayed (#343).
            self.model_mismatch_misses += 1
            self.misses += 1
            return None
        self.hits += 1
        # Copy out: the store mutates checkpoints in place (redaction, id stamps).
        return copy.deepcopy(checkpoint)

    def put(self, key: str, checkpoint: dict,
            served_model: str | None = None) -> None:
        # #343 envelope: record the producer alongside the payload. None is
        # honest absence (no wire receipt), never a default to the alias.
        entry = {"served_model": served_model, "checkpoint": checkpoint}
        try:
            self._path(key).write_text(
                json.dumps(entry, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # an uncacheable checkpoint just re-serializes next run
