---
id: 121
type: fence
title: A test that needs distinct or pinned ledger seconds uses clock.use, not a monkeypatch of module.time.time_ns
severity: medium
confidence: 0.8
created: 2026-10-07
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/clock.py
evidence:
  - note: tests/test_clock.py pins the seam; tests/test_clock_census.py fails on a new time.time_ns, time.time, datetime.now or uuid4 call in a ledger or write module outside its allowlist
expires:
  condition: "ledger stamps stop drawing order and event_id from clock.py"
  review_after: 2027-04-07
status: active
---

Every ledger row now takes `order` and `event_id` from `clock.now_ns()` and
`clock.new_id()`. The ledger modules no longer import `time` or `uuid`, so
`monkeypatch.setattr(requests.time, "time_ns", ...)` raises AttributeError.
Use `with clock.use(clock.StepClock(start_ns, step_ns))` for distinct seconds,
or a fixed clock (a class with `now_ns` and `new_id`) to pin one instant.
`StepClock` refuses a step under one second, because `open_request` refuses
two identical asks opened in the same second.

Scar 0064 still prescribes patching `<module>.time.time_ns`. Its reasoning
(a same-second test cannot tell a preserved stamp from a reset one) holds,
and so does its "run the fix backwards" rule, but the recipe is out of date
and its anchors should be re-anchored by a human. This candidate does not
edit 0064.

The clock lives in a ContextVar: it follows `asyncio.to_thread` but not
`threading.Thread` or `loop.run_in_executor`. Requests and rulings must keep
drawing from the same clock (scar 0083), so do not give either ledger a
private source of time.
