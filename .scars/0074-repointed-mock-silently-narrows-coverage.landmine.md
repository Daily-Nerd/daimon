---
id: 74
type: landmine
title: "Moving a function off a call graph silently shrinks any monkeypatch-based failure test aimed at the old call site; the test keeps passing while covering strictly less"
severity: high
confidence: 0.95
created: 2026-09-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/tests/test_ruling_briefing.py
  - path: plugin/daimon_briefing/briefing.py
  - pattern: "monkeypatch\.setattr\([^,]*refutations"
evidence:
  - commit: 8d66b44
  - pr: 971
  - note: #962: test_ruling_loader_fails_open patched refutations.listing, the OUTERMOST call, so its guard covered everything active_rulings touched including config-layer path resolution. The refactor moved active_rulings onto events()+fold(), the patch was repointed to refutations.events, and events sits INSIDE the new try. Path resolution moved outside the guard in the same change. active_rulings began raising UnicodeDecodeError and RuntimeError on config faults and briefing.render went from degrading to crashing, while 5888 tests passed. Caught only by an adversarial pass that ran the repro against both branches.
expires:
  condition: "every failure-simulating monkeypatch in plugin/tests asserts it was actually invoked (issue #968), so a bypassed patch fails loudly instead of narrowing silently"
  review_after: 2027-03-01
status: active
---

A monkeypatch that simulates a failure only tests what the patched function still
sits upstream of. Move the code onto a different call and the patch goes inert:
nothing raises, nothing goes red, and the test now proves a strictly smaller
claim than its name says.

This is not hypothetical. `test_ruling_loader_fails_open` patched
`refutations.listing`, which was the outermost call `active_rulings` made, so the
guard it exercised covered the whole body including `_path` and the config layer
underneath it. #962 rewired `active_rulings` onto `events()` + `fold()`. The
patch was repointed to `refutations.events` to keep the test running, and
`events` sits inside the new `try`. In the same change `_path` moved outside that
`try`. Result: `active_rulings` could raise on a bad byte in `~/.daimon/env`,
`briefing.render` went from degrading to crashing, and the suite stayed green at
5888 passing. The mock was repointed to follow the implementation rather than the
contract, and the coverage narrowed with it.

What a future editor must do instead. When you move a function off a call graph,
find every `monkeypatch.setattr` aimed at the old call site and ask what the patch
was standing in for, not merely how to make it run again. If the answer is "any
failure anywhere below this point", the replacement must sit at least as far out,
or the contract needs its own local guard. Prefer breaking the real dependency
over mocking it where that is cheap: `plugin/tests/test_rulings.py` now also
replaces the ledger with a directory, and that test cannot be bypassed by a
refactor at all. And make the fake count its own invocations so a bypassed patch
fails loudly, which is issue #968.

Same family as the hand-shaped fixtures in #963: in both cases a test described
the code instead of checking it, and a green suite proved nothing. There are 77
files under `plugin/tests/` using `monkeypatch.setattr`; not all are failure
simulations, but every one of them is a place this can happen again.
