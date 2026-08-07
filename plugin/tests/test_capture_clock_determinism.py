"""#604: quote verification stamps must come from the threaded clock.

`verify_quotes` read `datetime.now()` directly for `last_verified` and, since
#595, for the quote receipt's `checked_at` too. That is the unthreaded
now-consumer scar 0016 exists to forbid: every other stamp that lands on
disk is threaded from the transcript's own clock (`created` from the session
end, carry's `now` from that stamp), so two captures of the same transcript
are byte-identical — except these, which differ whenever the two runs
straddle a second boundary.

The symptom was `test_capture_parity` failing 1-3 runs in 40. That test had
already noticed half the problem and worked around it, avoiding a verbatim
item whose quote WOULD verify because "a hit stamps wall-clock
`last_verified`". #595 then put wall clock on the MISS path as well, which
is the case the workaround had chosen as safe.

Determinism here is not only about the test: a re-serialize of the same
transcript should produce the same checkpoint, which is what makes a
receipt re-checkable at all.
"""
import json
import os

from daimon_briefing import capture, provenance, serializer, store

PROJECT = "/p/clock-determinism"
SESSION = "S-clock"

_QUOTED = "the retry budget is shared across attempts"

_EXTRACTION = json.dumps({
    "session_id": SESSION,
    "working_context": {
        "active_topic": {"text": "t", "trust": "inferred"},
        "open_questions": [
            # verifies: the quote is really in the transcript below
            {"text": "retry budget semantics", "trust": "verbatim",
             "quote": _QUOTED},
            # misses: stamps a not-verified receipt, the #595 path
            {"text": "unrelated claim", "trust": "verbatim",
             "quote": "this sentence appears nowhere"},
        ],
        "recent_decisions": [],
    },
    "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
})

_STAMPS = ["2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z",
           "2026-07-01T10:02:00Z"]


def _write_transcript(tmp_path):
    rows = []
    for i, ts in enumerate(_STAMPS):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"turn {i}: {_QUOTED}" if i == 0 else f"turn {i}"
        rows.append({"type": role,
                     "message": {"role": role, "content": content},
                     "timestamp": ts})
    p = tmp_path / f"{SESSION}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (1782000000, 1782000000))   # scar 0016: pin the mtime
    return p


def _stamps(checkpoint):
    """Every verification stamp the checkpoint carries."""
    out = []
    for section, key in store._ITEM_LISTS:
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if not isinstance(item, dict):
                continue
            receipt = item.get("quote_provenance") or {}
            out.append((item.get("text"), item.get("last_verified"),
                        receipt.get("checked_at")))
    return sorted(out, key=lambda r: r[0] or "")


def _serialize(tmp_path, monkeypatch, fake_chat_factory, home_name):
    home = tmp_path / home_name
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(home / "checkpoints"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(home / "logs"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    tpath = _write_transcript(tmp_path)
    from daimon_briefing import transcript as tmod
    messages = tmod.from_file(tpath)
    capture.run(SESSION, messages, project=PROJECT,
                chat=fake_chat_factory(_EXTRACTION), deadline=None,
                transcript_path=tpath)
    return json.loads((home / "checkpoints" / f"{SESSION}.json")
                      .read_text(encoding="utf-8"))


class _AdvancingClock:
    """Wall clock that moves one second per reading.

    The bug only shows when two runs straddle a second boundary, which is
    why the parity guard failed 1-3 times in 40 and passed the rest. A test
    that reproduces it by luck proves nothing, so the boundary is forced:
    under this clock a wall-clock implementation CANNOT produce equal
    stamps, and a threaded one is unaffected by it entirely.
    """

    def __init__(self):
        self.calls = 0

    def now(self, tz=None):
        from datetime import datetime as _dt, timedelta, timezone as _tz
        self.calls += 1
        return (_dt(2026, 7, 2, 9, 0, 0, tzinfo=tz or _tz.utc)
                + timedelta(seconds=self.calls))


def test_two_captures_of_one_transcript_stamp_identically(
        tmp_path, fake_chat_factory, monkeypatch):
    """The whole bug in one assertion: same transcript, two runs, same
    stamps — including the verifying item the parity guard had to avoid."""
    clock = _AdvancingClock()
    monkeypatch.setattr(serializer, "datetime", clock)
    first = _serialize(tmp_path, monkeypatch, fake_chat_factory, "home-a")
    second = _serialize(tmp_path, monkeypatch, fake_chat_factory, "home-b")
    assert _stamps(first) == _stamps(second)
    assert first == second


def test_stamps_come_from_the_transcript_not_the_wall(
        tmp_path, fake_chat_factory, monkeypatch):
    """Not merely equal to each other — equal to the session's own clock.
    Two runs could agree by both being fast; this pins the SOURCE."""
    cp = _serialize(tmp_path, monkeypatch, fake_chat_factory, "home-c")
    created = cp.get("created")
    assert created == _STAMPS[-1], created
    for text, last_verified, checked_at in _stamps(cp):
        if last_verified is not None:
            assert last_verified == created, text
        if checked_at is not None:
            assert checked_at == created, text


def test_both_receipt_outcomes_are_stamped_and_valid(
        tmp_path, fake_chat_factory, monkeypatch):
    """The threaded stamp must still satisfy the receipt validator — a
    malformed checked_at would silently drop the receipt entirely."""
    cp = _serialize(tmp_path, monkeypatch, fake_chat_factory, "home-d")
    receipts = [i.get("quote_provenance")
                for i in (cp["working_context"]["open_questions"] or [])]
    assert len(receipts) == 2 and all(r is not None for r in receipts)
    assert {r["outcome"] for r in receipts} == {"verified", "not-verified"}
    assert all(provenance.valid_quote_receipt(r) for r in receipts)


def test_verify_quotes_falls_back_to_wall_clock_without_a_clock(monkeypatch):
    """A caller with no transcript stamp (a hook host that provides no file)
    must still stamp — the fallback is wall clock, never an empty string,
    which valid_quote_receipt would reject."""
    checkpoint = {
        "session_id": "S1",
        "working_context": {"open_questions": [
            {"text": "x", "trust": "verbatim", "quote": "hello world"}]},
    }
    source_ref = provenance.capture_source_ref("S1", None)
    serializer.verify_quotes(checkpoint, "hello world", None,
                             source_ref=source_ref)
    item = checkpoint["working_context"]["open_questions"][0]
    assert item["quote_verified"] is True
    assert item["last_verified"]
    assert provenance.valid_quote_receipt(item["quote_provenance"])
