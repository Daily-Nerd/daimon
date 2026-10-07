---
id: 0
type: landmine
title: Patching sys.stdout in a fixture is undone when the test body starts under pytest capture
severity: low
confidence: 0.9
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/tests/test_effects_commit.py
evidence:
  - note: "an events fixture that replaced sys.stdout recorded no flush; the same test passed under -s"
expires:
  condition: "the suite stops using pytest default capture"
  review_after: 2027-04-07
status: candidate
---

A test that must observe the order of writes to stdout against other writes
cannot install its recording stdout from a fixture: pytest capture puts its
own stdout back when the call phase begins, so the recorder sees nothing and
the ordering assertion fails only when capture is on. Install the recorder
inside the test body (`events.tap(monkeypatch)` in tests/test_effects_commit.py)
and run the test once with and once without `-s`.
