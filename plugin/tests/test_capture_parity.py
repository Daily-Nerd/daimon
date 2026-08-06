"""#432 standing parity guard: the hook and CLI capture entry points must
produce IDENTICAL checkpoints — and emit the same ledger events — from
identical input.

The two doors (`hooks.on_session_end`, `cli._run_serialize`) had drifted:
the hook skipped the CLI-layer pipeline (created stamp from the transcript
end, transcript_hash, carry.merge + resolved fold, bind_links, supersede
emission with its #425 forgotten-filter, and the #376 rejection ledger).
Both must call the ONE shared pipeline; this test fails CI on any future
re-divergence.

Fixed-clock conventions (scar 0016): all time that lands on disk is threaded
from the transcript's own row stamps (`created` -> carry `now` -> first_seen);
the transcript file's mtime is pinned via os.utime so the mtime fallback can
never smuggle wall clock in. The ONE nondeterministic field per ledger row is
the append-time `ts` wall stamp — popped before comparison and listed as the
intended difference.
"""

import copy
import json
import os

from daimon_briefing import cli, hooks, provenance, store, transcript

PROJECT = "/p/parity"
SESSION = "S-parity"

# Previous checkpoint, seeded IDENTICALLY into both stores: one open question
# for carry to fold, one decision for bind_links to target.
_PREV = {
    "session_id": "S-prev",
    "created": "2026-06-25T08:00:00Z",
    "working_context": {
        "active_topic": {"text": "prior topic", "trust": "inferred"},
        "open_questions": [
            {"text": "quorint reconciliation loop unresolved", "trust": "inferred",
             "importance": 7, "first_seen": "2026-06-20T00:00:00Z"},
        ],
        "recent_decisions": [
            {"text": "use gateway A for serialize", "trust": "inferred"},
        ],
    },
    "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                           "contradictions_flagged": []},
}

# The extraction both fake LLMs return. Exercises every drifted step:
# - a supersedes link with a free-text target that uniquely matches the prev
#   decision -> bind_links pair -> supersede-candidate event in events.jsonl;
# - a verbatim item whose quote is NOT in the transcript -> quote_verified
#   False -> a #376 rejection row in verification.jsonl. (Deliberately no
#   verbatim item whose quote WOULD verify: a hit stamps wall-clock
#   `last_verified`, which would be flaky noise here, not signal.)
_NEW_CP = json.dumps({
    "session_id": SESSION,
    "working_context": {
        "active_topic": {"text": "t", "trust": "inferred"},
        "open_questions": [
            {"text": "PR #6 state", "trust": "verbatim",
             "quote": "this quote appears nowhere in the transcript"},
        ],
        "recent_decisions": [
            {"text": "postgres replaces the old lookup store",
             "trust": "inferred",
             "links": [{"type": "supersedes",
                        "target": "gateway A serialize choice"}]},
        ],
    },
    "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
})

_STAMPS = ["2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z"]


def _write_transcript(tmp_path):
    rows = []
    for i, ts in enumerate(_STAMPS):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append({"type": role,
                     "message": {"role": role, "content": f"turn {i}"},
                     "timestamp": ts})
    p = tmp_path / f"{SESSION}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    # Pin mtime (scar 0016): if either door ever falls back to mtime for the
    # session-end stamp, it still reads a fixed clock, not test wall time.
    os.utime(p, (1782000000, 1782000000))
    return p


def _seed_home(monkeypatch, home):
    """Point the store at `home` and seed the identical prev checkpoint."""
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(home / "checkpoints"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(home / "logs"))
    # deepcopy: write_checkpoint mutates its argument in place (scar 0027) —
    # both homes must be seeded from the same pristine dict.
    store.write_checkpoint("S-prev", copy.deepcopy(_PREV), project_dir=PROJECT)


def _rows_minus_ts(path):
    """Ledger rows with the append-time wall stamp popped — `ts` is the ONE
    intended per-run difference (append happens at two different instants)."""
    if not path.exists():
        return []
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows:
        r.pop("ts", None)
    return rows


def test_hook_and_cli_capture_produce_identical_checkpoints(
        tmp_path, fake_chat_factory, monkeypatch):
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    tpath = _write_transcript(tmp_path)
    slug = store.project_slug(PROJECT)

    # ---- door 1: the CLI (`daimon serialize`) ----
    home_cli = tmp_path / "home-cli"
    _seed_home(monkeypatch, home_cli)
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_NEW_CP))
    assert cli.main(["serialize", str(tpath)]) == 0

    # ---- door 2: the in-process SessionEnd hook ----
    home_hook = tmp_path / "home-hook"
    _seed_home(monkeypatch, home_hook)
    monkeypatch.setattr(hooks, "_chat", fake_chat_factory(_NEW_CP))
    monkeypatch.setattr(transcript, "from_session",
                        lambda sid: transcript.from_file(tpath))
    hooks.on_session_end(session_id=SESSION, completed=True, interrupted=False,
                         model="m", platform="cli",
                         transcript_path=str(tpath))

    cli_cp = json.loads(
        (home_cli / "checkpoints" / f"{SESSION}.json").read_text(encoding="utf-8"))
    hook_path = home_hook / "checkpoints" / f"{SESSION}.json"
    assert hook_path.exists(), "hook door wrote no checkpoint at all"
    hook_cp = json.loads(hook_path.read_text(encoding="utf-8"))

    # THE contract: byte-equal checkpoint content. No excluded fields — every
    # stamp is threaded from the transcript's fixed clock, so any diff here is
    # real pipeline drift.
    assert hook_cp == cli_cp

    # Liveness: the scenario actually exercised the drifted steps on the CLI
    # side — a vacuously-equal empty run would guard nothing.
    carried = [q for q in cli_cp["working_context"]["open_questions"]
               if q.get("text") == "quorint reconciliation loop unresolved"]
    assert carried and carried[0]["carried_from"] == "S-prev"
    assert cli_cp["transcript_hash"] == transcript.file_sha256(tpath)
    assert cli_cp["created"] == _STAMPS[-1]
    assert provenance.valid_source_ref(cli_cp["source_ref"])
    rejected = next(
        q for q in cli_cp["working_context"]["open_questions"]
        if q.get("text") == "PR #6 state")
    assert provenance.valid_quote_receipt(rejected["quote_provenance"])
    assert rejected["quote_provenance"]["outcome"] == "not-verified"

    # Same events emitted through both doors (supersede-candidate emission),
    # minus the append-time `ts` wall stamp.
    cli_events = _rows_minus_ts(home_cli / "checkpoints" / slug / "events.jsonl")
    hook_events = _rows_minus_ts(home_hook / "checkpoints" / slug / "events.jsonl")
    assert cli_events, "scenario failed to produce a supersede-candidate event"
    assert any(str(e.get("status") or "").startswith("supersede-candidate:")
               for e in cli_events)
    assert hook_events == cli_events

    # Same rejection-ledger rows (#376), minus `ts`.
    cli_rej = _rows_minus_ts(home_cli / "checkpoints" / slug / "verification.jsonl")
    hook_rej = _rows_minus_ts(home_hook / "checkpoints" / slug / "verification.jsonl")
    assert cli_rej, "scenario failed to produce a quote-rejection row"
    assert hook_rej == cli_rej
