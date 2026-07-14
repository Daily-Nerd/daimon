"""Serialized-checkpoint cache for the LongMemEval harness (#267).

Serializing a haystack session is the one expensive step (an LLM call, minutes
each). The cache keys the produced checkpoint by the exact things that determine
it — the session's message content, the backend, the model, and the serializer
prompt version — so a re-run pays the LLM only for sessions it has never seen
under the current config. A backend, model, or prompt-version change misses on
purpose: a cached checkpoint from a different pipeline is a different measurement.

The cache stores the RAW `serialize_strict` output (pre-store mutation). Callers
deep-copy on write, so a cached checkpoint is never mutated in place by the store.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def cache_key(messages: list[dict], *, backend: str, model: str,
              prompt_version: str, carry: str = "off") -> str:
    """Stable content hash over (turns, backend, model, prompt_version, carry).

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
    """
    h = hashlib.sha256()
    h.update(f"v1\x00{backend}\x00{model}\x00{prompt_version}\x00".encode())
    if carry != "off":
        h.update(f"carry\x00{carry}\x00".encode())
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
    """

    def __init__(self, cache_dir: Path):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        # keys are hex digests (or test literals) — no separators to contain.
        return self.dir / f"{key}.json"

    def get(self, key: str) -> dict | None:
        p = self._path(key)
        try:
            checkpoint = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.misses += 1
            return None
        if not isinstance(checkpoint, dict):
            self.misses += 1
            return None
        self.hits += 1
        # Copy out: the store mutates checkpoints in place (redaction, id stamps).
        return copy.deepcopy(checkpoint)

    def put(self, key: str, checkpoint: dict) -> None:
        try:
            self._path(key).write_text(
                json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass  # an uncacheable checkpoint just re-serializes next run
