"""#420: adversarial write-boundary test — carry cannot resurrect a forgotten value.

`carry.merge`'s only forget suppression is ID-keyed (`item.get("id") in
resolved`, carry.py). When a prev checkpoint still holds a forgotten VALUE under
an id the ledger never tombstoned, carry forwards the item in memory; the only
thing between it and disk is the value-keyed forget gate —
`policy.drop_forgotten`, run by `store.write_checkpoint` via
`policy.admit_checkpoint` (#421). The lookalike tests
(test_forget_reassertion_e2e's carry section,
test_deletion_durability_protocol's carry step) feed carry a prev that the
forget rewrite ALREADY scrubbed — delete the gate and they all
stay green. This file pins the boundary, architecture-guard style: the first
test proves the resurrection path does not exist; the second is the committed
MUTATION CHECK — it disables the gate and asserts the value DOES reach disk,
proving the first test's assertions are sensitive to exactly this gate.

The adversarial state is built legitimately with store primitives, in the one
ordering that produces it: the prev checkpoint is written BEFORE the tombstone
is recorded. Post-#424 the forget CLI cannot mint this state itself (it
tombstones first, then splices by value) — but a tombstone recorded against a
DIFFERENT id never rewrites this file: e.g. the cross-section sibling id (the
same sentence captured as an open question hashes to `o-<digest>` while the
decision copy holds `d-<digest>`), a widened hash, or a teammate's copy. The
fixture mints exactly that: a `forgotten:<content_key>` event whose ref is the
open_questions-section sibling id, while the prev holds the value as a decision.

Fixed clocks throughout (scar 0016): the prev carries a simulated calendar, the
serialize transcript's mtime is pinned with os.utime (markdown transcripts stamp
`created` from file mtime), and briefing.build reads `now=_NOW` — never wall
time, no flakes.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

from daimon_briefing import (briefing, carry, cli, config, normalize, policy,
                             recall, store)
from tests.conftest import FakeChat

_P = "/repo/carry-forget-adversarial-420"

# S is forgotten; T is the never-forgotten twin riding the identical carry path,
# so no negative assertion can pass vacuously (carry off / prev unread would
# lose T too). "adopt" is the hot recall keyword matching both.
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"
_HOT = "adopt"
_PROBE = "carry probe decision only"

_PREV_SID = "P1"
_NEW_SID = "S2-carry-adversarial"

_PREV_CREATED = "2026-07-01T00:00:00Z"
# The new session's `created` comes from the transcript file's mtime
# (cli._session_end_stamp falls back to mtime for markdown transcripts) —
# pinned below with os.utime so the whole calendar is simulated.
_NEW_EPOCH = datetime(2026, 7, 2, tzinfo=timezone.utc).timestamp()
_NOW = datetime(2026, 7, 3, tzinfo=timezone.utc).timestamp()


# ---- fixture builders -----------------------------------------------------


def _prev_checkpoint():
    return {
        "session_id": _PREV_SID,
        "created": _PREV_CREATED,
        "working_context": {
            "active_topic": {"text": "storage decisions", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": _S, "trust": "inferred"},
                {"text": _T, "trust": "inferred"},
            ],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def _sibling_ref():
    """The cross-section sibling id for _S: what store._stamp_item_ids would
    mint had the SAME sentence been captured as an open question. A tombstone
    keyed to this ref is producible by the real CLI on a copy where the value
    lived in that section — and it never matches the decision-section id the
    prev actually holds, so carry's id-keyed check cannot see it."""
    digest = hashlib.sha1(f"open_questions:{_S}".encode("utf-8")).hexdigest()
    return f"o-{digest[:6]}"


def _arm_adversarial_state():
    """Write the prev (still clean — no tombstone exists yet), THEN record the
    value-keyed tombstone against the never-present sibling id. Returns the
    prev item id under which the forgotten value survives on disk."""
    store.write_checkpoint(_PREV_SID, _prev_checkpoint(), project_dir=_P)
    prev = store.read_latest(project_dir=_P, fallback=False)
    x_id = next(d["id"] for d in prev["working_context"]["recent_decisions"]
                if d["text"] == _S)

    ref = _sibling_ref()
    assert ref != x_id
    assert store.append_event(ref, f"forgotten:{normalize.content_key(_S)}",
                              kind="tombstone", project_dir=_P)

    # Preconditions that make this ADVERSARIAL, not a re-run of #400/#407:
    # the ledger holds the value key, but the id the prev stores S under was
    # never tombstoned, and the prev on disk still contains S verbatim.
    assert normalize.content_key(_S) in store.forgotten_content_keys(_P)
    assert x_id not in store.resolutions(project_dir=_P)
    assert _S in _latest_raw()
    return x_id


def _transcript_file(tmp_path):
    """A real-shaped markdown transcript for the NEW session. It never states S
    or T — carry from the armed prev must be the ONLY vector that could put
    them into the new checkpoint."""
    turns = [
        ("user", "Quick status sync on the memory work."),
        ("assistant", "Nothing new on the storage front to restate."),
        ("user", "Log one probe decision for this session."),
        ("assistant", f"Noted the decision: {_PROBE}."),
        ("user", "That is all for today, wrap it up."),
        ("assistant", "Serializing the cognitive checkpoint now."),
    ]
    body = "# Session: carry adversarial probe\n\n" + "\n\n".join(
        f"**{role}**: {text}" for role, text in turns)
    path = tmp_path / f"{_NEW_SID}.md"
    path.write_text(body, encoding="utf-8")
    os.utime(path, (_NEW_EPOCH, _NEW_EPOCH))  # pins `created` (scar 0016)
    return path


def _extraction_json():
    """What the canned extractor returns for the new session: the probe only —
    faithful to a transcript that never mentions S or T."""
    return json.dumps({
        "session_id": _NEW_SID,
        "working_context": {
            "active_topic": {"text": "carry probe", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": _PROBE, "trust": "inferred", "importance": 6},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
        "worker_queue": [],
    })


def _serialize_new_session(tmp_path, monkeypatch):
    """The REAL production path: cli serialize -> carry.merge from the on-disk
    prev -> store.write_checkpoint. Zero quota (canned chat)."""
    monkeypatch.setattr(cli, "_chat", FakeChat(_extraction_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    assert cli.main(["serialize", str(_transcript_file(tmp_path))]) == 0
    return store.read_latest(project_dir=_P, fallback=False)


# ---- read-side probes (mirror #400/#407 helpers, fixed clock) -------------


def _latest_raw():
    slug = store.project_slug(_P)
    return (config.checkpoint_dir() / slug / "latest.json").read_text(encoding="utf-8")


def _decisions(cp):
    return {d["text"]: d for d in
            ((cp or {}).get("working_context") or {}).get("recent_decisions") or []}


def _brief_decisions(cp):
    b = briefing.build(cp, now=_NOW)
    return [d["text"] for d in (b["decisions"] if b else [])]


def _recall_texts():
    recall.rebuild()
    return [h["text"] for h in recall.search(_HOT, project_dir=_P)]


# ---- the tests ------------------------------------------------------------


def test_ungated_prev_cannot_resurrect_forgotten_value_through_carry(
        tmp_checkpoint_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    _arm_adversarial_state()

    # Control: the vector is REAL. Fed this prev, carry.merge itself forwards
    # the forgotten value in memory — its id-keyed suppression cannot see a
    # value-keyed tombstone recorded under a sibling id. Only the write
    # boundary stands between this merged dict and disk.
    prev = store.read_latest(project_dir=_P, fallback=False)
    resolved = frozenset(ref for ref, evt in
                         store.resolutions(project_dir=_P).items()
                         if store.is_resolved(evt))
    probe_cp = {
        "session_id": "in-memory-probe",
        "created": "2026-07-02T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": _PROBE, "trust": "inferred"}]},
    }
    merged = carry.merge(probe_cp, prev, now=_NEW_EPOCH, resolved=resolved)
    assert _S in _decisions(merged) and _T in _decisions(merged)

    # The real serialize -> carry -> write path.
    stored = _serialize_new_session(tmp_path, monkeypatch)

    # Liveness controls: carry genuinely merged from THAT prev — the twin was
    # carried (stamped with the prev's sid) and the fresh probe landed. Without
    # these, every negative below could pass vacuously on a carry that never ran.
    decisions = _decisions(stored)
    assert _PROBE in decisions
    assert _T in decisions
    assert decisions[_T].get("carried_from") == _PREV_SID

    # 1) The written checkpoint on disk: dropped at the boundary, not withheld.
    raw = _latest_raw()
    assert _S not in raw
    assert _T in raw
    session_file = (config.checkpoint_dir() / f"{_NEW_SID}.json").read_text(
        encoding="utf-8")
    assert _S not in session_file and _T in session_file

    # 2) briefing.build output.
    bd = _brief_decisions(stored)
    assert _S not in bd
    assert _T in bd and _PROBE in bd

    # 3) Recall after a full index rebuild. The armed prev's remnants (flat
    # session file, rotated prev-N pointers) still contain S on disk BY
    # CONSTRUCTION — the tombstone here never rewrote any file. Pre-#427 the
    # index's forgotten-scrub was id-keyed and this test had to unlink those
    # remnants to isolate the write boundary; post-#427 the scrub is
    # value-keyed, so the assertion holds against the FULL on-disk history:
    # neither the new write nor any historical sibling-id row may surface S.
    texts = _recall_texts()
    assert _S not in texts
    assert _T in texts


def test_gate_removed_carried_value_reaches_disk(tmp_checkpoint_dir, tmp_path,
                                                 monkeypatch):
    """The committed MUTATION CHECK (#420 acceptance): with the value-keyed
    gate no-opped, the SAME fixture drives the forgotten value through carry
    onto disk. This proves the sibling test is sensitive to exactly this gate —
    delete `policy.drop_forgotten` and that test goes red, because this one
    shows the resurrection genuinely happens without it."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    _arm_adversarial_state()
    # The mutation: the write boundary loses its value-keyed scrub. Patched at
    # the gate's home (#421) — policy.admit_checkpoint dispatches through the
    # module global `drop_forgotten`, so this name is what the write path
    # resolves at call time. The resurrection assertions below double as the
    # patch's own liveness check: they only pass if the no-op really bit.
    monkeypatch.setattr(policy, "drop_forgotten",
                        lambda checkpoint, forgotten_keys: [])

    stored = _serialize_new_session(tmp_path, monkeypatch)

    decisions = _decisions(stored)
    assert _S in decisions                                   # resurrected
    assert decisions[_S].get("carried_from") == _PREV_SID    # by carry, from P1
    assert _T in decisions and _PROBE in decisions
    raw = _latest_raw()
    assert _S in raw                                         # ... and on disk
