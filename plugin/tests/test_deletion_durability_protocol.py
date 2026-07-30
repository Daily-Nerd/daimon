"""#407: the full deletion-durability protocol as a committed, executable test.

"A forgotten memory stays forgotten" is the load-bearing claim behind
`daimon forget`. #400/#402/#404 shipped the value-keyed tombstone and proved
non-resurrection for a single item across briefing, carry, recall and an index
rebuild. This file commits the WHOLE protocol end to end — including the two
steps that lineage does not yet cover:

  * step 3 re-feeds the ORIGINAL SOURCE TRANSCRIPT through the real serializer
    (re-ingesting raw material is how systems quietly resurrect deleted
    content), not a hand-built checkpoint dict; and
  * step 9 probes the signed provenance RECEIPT — the deleted-content version
    is what gets bound, never a stale pre-deletion blob; and
  * step 10 asserts the append-only audit trail records the deletion while
    holding NONE of the forgotten text.

Eleven steps, each re-asserting on the accumulated state, each paired with a
never-forgotten twin (T) that MUST stay retrievable so no negative assertion
can pass vacuously:

  1. write a distinctive fact through the serializer; assert retrievable
     (and seed the #48 chunk cache exactly as a chunked capture would)
  2. forget it; confirm removal from briefing, carry, recall — and that the
     serializer chunk cache (PRE-redaction by #125 necessity) is purged
     WHOLESALE (#422): entries are keyed by chunk text, not searchable by
     value, so selective removal is impossible and every entry goes
  3. re-feed the SAME source transcript + re-serialize; assert non-resurrection
  4. background job — recall index rebuild; re-assert absence
  5. background job — a subsequent carry; re-assert absence
  6. background job — team dual-write; re-assert absence in the remote copy
  7. derived artifact — the rendered brief STRING
  8. derived artifact — recall's SQLite rows
  9. derived artifact — the signed receipt binds the POST-deletion bytes
 10. audit trail — the deletion is recorded, with no forgotten text on disk
 11. chunk-cache sink on the ACCUMULATED state — after every background job
     above, no file under .chunk-cache holds the forgotten value

Deterministic and ZERO model quota: the serializer's LLM is a canned
`fake_chat` and the vitni signer is a monkeypatched stub — this is a
compliance test, not a benchmark run. Fixed clock threaded into every
now-consumer (scar 0016). No lifecycle event is ever written against T's id
(scar 0025: any event kind resolves its item) — T's liveness is untouched.
"""

import base64
import copy
import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from daimon_briefing import (
    briefing,
    carry,
    cli,
    config,
    llm,
    normalize,
    receipts,
    recall,
    serializer,
    store,
)

# Fixed clock (scar 0016): the checkpoints below carry a simulated calendar, so
# every now-consumer must read THIS clock, never wall time.
_NOW = datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp()

_P = "/repo/deletion-durability-407"

# S is forgotten; T is the never-forgotten twin. "adopt" is the hot keyword —
# it matches BOTH, so it is the tombstone, never the query, that separates them.
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"
_HOT = "adopt"


# ---- deterministic, zero-quota serializer front-end ----------------------


def _transcript():
    """The raw source material: a real-shaped transcript that STATES both S and
    T. Re-feeding THIS (step 3) is the resurrection vector under test."""
    return [
        {"role": "user", "content": f"Let's settle the storage question: {_S}."},
        {"role": "assistant", "content": "Noted. Any second store?"},
        {"role": "user", "content": f"Yes — separately, {_T}."},
        {"role": "assistant", "content": "Two distinct stores, understood."},
        {"role": "user", "content": "Recap the two decisions for the checkpoint."},
        {"role": "assistant", "content": f"1) {_S}. 2) {_T}."},
        {"role": "user", "content": "Good. Anything else open?"},
        {"role": "assistant", "content": "No open questions remain."},
        {"role": "user", "content": "Then wrap the session."},
        {"role": "assistant", "content": "Serializing the cognitive checkpoint now."},
        {"role": "user", "content": "Confirmed, end of session."},
        {"role": "assistant", "content": "Done."},
    ]


def _extraction_json(session_id):
    """What a faithful extractor returns for `_transcript()`: both decisions,
    verbatim-free (inferred) so no quote/outcome gate mutates them — the
    durability guarantee is value-keyed and trust-class independent."""
    return json.dumps({
        "session_id": session_id,
        "working_context": {
            "active_topic": {"text": "storage decisions", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": _S, "trust": "inferred", "importance": 8},
                {"text": _T, "trust": "inferred", "importance": 8},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
        "worker_queue": [],
    })


def _serialize_and_write(session_id, created, fake_chat_factory):
    """Full production capture path, zero quota: transcript -> serializer (canned
    LLM) -> store.write_checkpoint. Returns the serializer's OWN output (pre-write)
    so a caller can prove what the extractor produced vs what reached disk.

    Stamps `created` (cli #123) and `transcript_hash` (cli #125) exactly as the
    real CLI serialize path does, so the receipt mint has a binding to work
    with (step 9)."""
    messages = _transcript()
    chat = fake_chat_factory(_extraction_json(session_id))
    cp = serializer.serialize(session_id, messages, chat=chat)
    assert cp is not None, "the canned extraction must serialize cleanly"
    cp["created"] = created
    cp["transcript_hash"] = hashlib.sha256(
        serializer._render_transcript(messages).encode("utf-8")).hexdigest()
    # The extractor's output is a separate artifact from what reaches disk:
    # write_checkpoint mutates `cp` in place (the value-keyed gate drops
    # forgotten items), so snapshot the pre-write extraction to prove the
    # resurrection vector is real BEFORE the boundary closes it.
    pre_write = copy.deepcopy(cp)
    store.write_checkpoint(session_id, cp, project_dir=_P)
    return pre_write


# ---- read-side probes (mirror #400's helpers, on the fixed clock) --------


def _brief_decisions(cp):
    b = briefing.build(cp, now=_NOW)
    return [d["text"] for d in (b["decisions"] if b else [])]


def _recall_texts():
    recall.rebuild()
    return [h["text"] for h in recall.search(_HOT, project_dir=_P)]


def _resolved():
    return frozenset(
        ref for ref, evt in store.resolutions(project_dir=_P).items()
        if store.is_resolved(evt))


def _carry_decisions(new_created):
    prev = store.read_latest(project_dir=_P, fallback=False)
    new_cp = {
        "session_id": "carry-probe",
        "created": new_created,
        "working_context": {
            "active_topic": {"text": "", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": "carry probe decision only", "trust": "inferred"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    merged = carry.merge(new_cp, prev, now=_NOW, resolved=_resolved())
    return [d["text"] for d in
            merged["working_context"]["recent_decisions"]]


def _latest_raw():
    slug = store.project_slug(_P)
    return (config.checkpoint_dir() / slug / "latest.json").read_text(encoding="utf-8")


# ---- receipts: a zero-quota vitni signer stub ----------------------------


@pytest.fixture
def receipts_on(tmp_path, monkeypatch):
    """Turn signed receipts ON with a deterministic signer — no vitni CLI, no
    openssl, no real subprocess (avoids the LibreSSL-vs-OpenSSL coverage trap,
    scar candidate 0). Pre-seeds the key material and stubs the signer so every
    write mints a real sidecar over the exact on-disk bytes."""
    kdir = tmp_path / "keys"
    kdir.mkdir()
    (kdir / "signing.seed").write_text(base64.b64encode(bytes(range(32))).decode())
    (kdir / "signing.seed").chmod(0o600)
    (kdir / "signing.pub.json").write_text(json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": "stub-public-x",
         "alg": "EdDSA", "status": "active"}))
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(kdir))
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("DAIMON_VITNI_CLI", "fake-vitni")
    monkeypatch.setattr(receipts, "_resolve_cli", lambda: "fake-vitni")

    def _fake_run_cli(cli_path, command, stdin_json):
        # Only `sign` is exercised on the mint path; a well-formed JWS lets the
        # sidecar write proceed. Everything else is unused here.
        if command == "sign":
            return {"signed_receipt": "aaa.bbb.ccc"}
        return None

    monkeypatch.setattr(receipts, "_run_cli", _fake_run_cli)


def _sidecar_for(session_id):
    # The receipt binds the flat per-session checkpoint file (not a bucket copy).
    cp_file = config.checkpoint_dir() / f"{store._safe_name(session_id)}.json"
    return cp_file, receipts._sidecar_path(cp_file)


# =========================================================================
#  THE PROTOCOL
# =========================================================================


def test_deletion_durability_protocol(tmp_checkpoint_dir, monkeypatch,
                                      fake_chat_factory, receipts_on):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    # --- Step 1: write a distinctive fact through the serializer -----------
    _serialize_and_write("S1", "2026-07-01T00:00:00Z", fake_chat_factory)
    stored1 = store.read_latest(project_dir=_P, fallback=False)
    x_id = next(d["id"] for d in stored1["working_context"]["recent_decisions"]
                if d["text"] == _S)

    assert _S in _brief_decisions(stored1) and _T in _brief_decisions(stored1)
    assert _S in _carry_decisions("2026-07-02T00:00:00Z")
    assert _T in _carry_decisions("2026-07-02T00:00:00Z")
    r1 = _recall_texts()
    assert _S in r1 and _T in r1

    # Seed the #48 chunk cache through the PRODUCTION writer, exactly as a
    # chunked capture of this transcript would have: cached output is
    # PRE-redaction extraction (forced by #125 quote verification), so the
    # forgotten value's bytes land here verbatim. This transcript is too
    # small to trigger chunking, so the sink is seeded explicitly.
    llm.reset_fallback()  # _save_chunk_cache refuses after a fallback fired
    cache_dir = serializer._chunk_cache_dir()
    seed_key = serializer._chunk_cache_key(
        serializer._render_transcript(_transcript()))
    serializer._save_chunk_cache(seed_key, json.loads(_extraction_json("S1")))
    seeded = cache_dir / f"{seed_key}.json"
    # Control precondition: the sink genuinely holds the value, so the
    # negative assertions below cannot pass vacuously.
    assert _S in seeded.read_text(encoding="utf-8")

    # --- Step 2: forget it; confirm removal from briefing/carry/recall -----
    assert cli.main(["forget", x_id]) == 0
    res = store.resolutions(project_dir=_P)
    assert x_id in res and res[x_id]["status"].startswith("forgotten:")

    stored2 = store.read_latest(project_dir=_P, fallback=False)
    assert _S not in _brief_decisions(stored2)          # gone
    assert _T in _brief_decisions(stored2)              # liveness
    c2 = _carry_decisions("2026-07-03T00:00:00Z")
    assert _S not in c2 and _T in c2
    r2 = _recall_texts()
    assert _S not in r2 and _T in r2

    # Chunk-cache sink (#422): forget purges the cache WHOLESALE — entries
    # are keyed by chunk text plus config dimensions, never by contained
    # value, so selective removal is impossible. The directory survives (the
    # next serialize re-populates in place); every entry is gone. Accepted
    # cost: re-paying extraction for chunks younger than chunk_cache_days.
    assert cache_dir.is_dir()
    assert list(cache_dir.glob("*.json")) == []
    for leftover in cache_dir.iterdir():
        assert _S not in leftover.read_text(encoding="utf-8")

    # --- Step 3: re-feed the ORIGINAL SOURCE TRANSCRIPT + re-serialize -----
    # The resurrection vector: the same raw material is re-ingested. The
    # extractor faithfully RE-PRODUCES S (proving the vector is real); the
    # value-keyed gate drops it at the write boundary (proving it is closed).
    refed = _serialize_and_write("S3", "2026-07-04T00:00:00Z", fake_chat_factory)
    refed_texts = [d["text"] for d in refed["working_context"]["recent_decisions"]]
    assert _S in refed_texts and _T in refed_texts      # extractor re-produced both

    stored3 = store.read_latest(project_dir=_P, fallback=False)
    assert _S not in _latest_raw()                       # dropped at the boundary
    assert _T in _latest_raw()                           # twin persisted to disk
    assert _S not in _brief_decisions(stored3) and _T in _brief_decisions(stored3)

    # --- Step 4: background job — recall index rebuild --------------------
    recall.rebuild()
    r4 = [h["text"] for h in recall.search(_HOT, project_dir=_P)]
    assert _S not in r4 and _T in r4

    # --- Step 5: background job — a subsequent carry ---------------------
    c5 = _carry_decisions("2026-07-05T00:00:00Z")
    assert _S not in c5 and _T in c5

    # --- Step 6: background job — team dual-write ------------------------
    monkeypatch.setenv("DAIMON_TEAM", "1")
    _serialize_and_write("S6", "2026-07-06T00:00:00Z", fake_chat_factory)
    team_blobs = [p.read_text(encoding="utf-8")
                  for p in config.team_dir().rglob("*.json")]
    joined = "\n".join(team_blobs)
    assert joined                       # the mirror is live (control precondition)
    assert _T in joined                 # twin reached the remote copy
    assert _S not in joined             # forgotten value never reached the mirror
    monkeypatch.delenv("DAIMON_TEAM", raising=False)

    # --- Step 7: derived artifact — the rendered brief STRING ------------
    stored7 = store.read_latest(project_dir=_P, fallback=False)
    rendered = briefing.render(stored7)
    assert rendered is not None
    assert _S not in rendered and _T in rendered

    # --- Step 8: derived artifact — recall's SQLite rows ----------------
    recall.rebuild()
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = [r[0] for r in conn.execute("SELECT text FROM items").fetchall()]
    finally:
        conn.close()
    assert _S not in rows and _T in rows

    # --- Step 9: derived artifact — the signed receipt ------------------
    # The receipt for the re-fed session (S3) must bind the POST-deletion bytes:
    # its outputs_hash matches the on-disk checkpoint, which carries T, not S.
    cp_file, sidecar = _sidecar_for("S3")
    assert sidecar.exists(), "receipts on -> a sidecar must have been minted"
    sidecar_doc = json.loads(sidecar.read_text(encoding="utf-8"))
    on_disk = cp_file.read_bytes()
    assert _S.encode() not in on_disk and _T.encode() in on_disk
    assert sidecar_doc["receipt"]["outputs_hash"] == \
        receipts._multibase_sha256(on_disk)             # binds the deleted-content blob
    assert receipts.verbatim_degraded(
        json.loads(cp_file.read_text(encoding="utf-8"))) is False
    assert _S not in sidecar.read_text(encoding="utf-8")  # never leaks into the receipt

    # --- Step 10: audit trail records the deletion (no forgotten text) ---
    events = store._events_path(_P).read_text(encoding="utf-8")
    tomb = [json.loads(ln) for ln in events.splitlines()
            if json.loads(ln).get("item_ref") == x_id]
    assert tomb, "the forget must leave an append-only audit record"
    latest = tomb[-1]
    assert latest["kind"] == "tombstone"
    assert latest["status"] == f"forgotten:{normalize.content_key(_S)}"
    # removal means the content leaves the audit trail too (#321): the trail
    # proves THAT something was removed, never re-persists WHAT. The status
    # carries a HASH ("forgotten:<key>"), so short stop-words ("for", "the")
    # appear as substrings of the scaffolding — the leak signal is the
    # DISTINCTIVE content words, none of which may reach disk.
    assert _S not in events
    for token in (t for t in _S.split() if len(t) >= 4):
        assert token not in events

    # --- Step 11: chunk-cache sink on the ACCUMULATED state ---------------
    # Steps 3-6 re-ran the serializer after the forget; whatever they left
    # behind, no file under .chunk-cache may hold the forgotten value. (A
    # post-forget re-ingest MAY re-cache raw material until the age reaper
    # or the next forget fires — this transcript serializes single-pass, so
    # here the cache must simply hold nothing containing S.)
    assert cache_dir.is_dir()
    for cached in cache_dir.iterdir():
        assert _S not in cached.read_text(encoding="utf-8")


# =========================================================================
#  Focused twins for the two genuinely-new steps (independent diagnosis)
# =========================================================================


def test_step3_transcript_refeed_never_resurrects(tmp_checkpoint_dir, monkeypatch,
                                                  fake_chat_factory):
    """Step 3 in isolation: forget, then re-ingest the SAME raw transcript. The
    serializer re-extracts S every time (its job); the write boundary is where
    S dies. Twin T rides the identical path and survives."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)

    _serialize_and_write("A1", "2026-07-01T00:00:00Z", fake_chat_factory)
    stored = store.read_latest(project_dir=_P, fallback=False)
    x_id = next(d["id"] for d in stored["working_context"]["recent_decisions"]
                if d["text"] == _S)
    assert cli.main(["forget", x_id]) == 0

    for i, created in enumerate(["2026-07-02T00:00:00Z", "2026-07-03T00:00:00Z",
                                 "2026-07-04T00:00:00Z"]):
        cp = _serialize_and_write(f"A-refeed-{i}", created, fake_chat_factory)
        # every re-ingest re-produces S in the extractor output ...
        assert _S in [d["text"] for d in cp["working_context"]["recent_decisions"]]
        # ... and never lands it back on disk, while T persists each time.
        assert _S not in _latest_raw()
        assert _T in _latest_raw()


def test_step9_receipt_binds_post_deletion_bytes(tmp_checkpoint_dir, monkeypatch,
                                                 fake_chat_factory, receipts_on):
    """Step 9 in isolation: with receipts on, the forget command's re-mint binds
    the post-removal checkpoint. The signed outputs_hash equals the hash of the
    bytes on disk, which no longer contain the forgotten value."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _P)

    _serialize_and_write("R1", "2026-07-01T00:00:00Z", fake_chat_factory)
    stored = store.read_latest(project_dir=_P, fallback=False)
    x_id = next(d["id"] for d in stored["working_context"]["recent_decisions"]
                if d["text"] == _S)

    cp_file, sidecar = _sidecar_for("R1")
    assert sidecar.exists()                              # step 1 already signed
    pre_hash = json.loads(sidecar.read_text())["receipt"]["outputs_hash"]

    assert cli.main(["forget", x_id]) == 0               # rewrites + re-mints R1

    assert sidecar.exists()
    post = json.loads(sidecar.read_text(encoding="utf-8"))["receipt"]["outputs_hash"]
    on_disk = cp_file.read_bytes()
    assert post != pre_hash                              # a new blob was signed
    assert post == receipts._multibase_sha256(on_disk)  # binds the post-removal bytes
    assert _S.encode() not in on_disk and _T.encode() in on_disk
    assert receipts.verbatim_degraded(
        json.loads(cp_file.read_text(encoding="utf-8"))) is False
