---
id: 83
type: fence
title: refutations.request_policy_history checks order-aware intervals, not membership; a row that BACKDATES its order into an old window is still indistinguishable from a genuine accept made then
severity: low
confidence: 0.85
created: 2026-09-09
authors: ["claude-code"]
promoted_by: Kibukx
promoted_by_source: explicit
anchors:
  - path: plugin/daimon_briefing/refutations.py
  - path: plugin/daimon_briefing/requests.py
  - pattern: "request_policy_history|_covered_by_policy"
evidence:
  - note: #961 slice 4 build, 2026-09-09: the first implementation of request_policy_history was a MONOTONIC set (every grant ever active, no notion of when), which closed binding decision 3 (a past accept survives an overturn) but left a gap: a request row forged directly via requests.append (bypassing accept() entirely) citing a once-active, since-overturned ruling id/hash landed unconditionally, since a forged row's CURRENT order was never checked against anything. Fixed in the same session, before merge, per review: request_policy_history now returns (sender, kind, verb, by, ruling_id, sha256, active_from, active_until) INTERVALS in `order` units (both refutations._stamp and requests._stamp compute order = time.time_ns(), one clock, verified not assumed), and requests._covered_by_policy requires the accepted row's own order to fall inside the interval. A forged row with a CURRENT order citing an overturned hash is now inert (tests/test_requests.py::test_a_forged_row_with_a_current_order_citing_an_overturned_hash_is_inert). The narrower residual that remains: a row that ALSO backdates its own order (_stamp(..., now_ns=...)) into a window that WAS legitimately open for that exact grant is indistinguishable from a genuine accept made then, since order is the only ordering signal available and the forger controls it. Pinned by tests/test_requests.py::test_disclosed_gap_a_backdated_forged_row_inside_an_old_window_still_lands.
expires:
  condition: "the codebase gains a cross-ledger physical ordering primitive independent of the order field a writer supplies (e.g. a monotonic append-position counter neither ledger's caller can set), or accept() gains its own tamper-evident receipt distinct from a raw requests.append row"
  review_after: 2027-03-09
status: active
---

This is now a much narrower gap than the monotonic-set version it replaced.
Reaching it requires: (1) `requests.append` access, already inside the
trust boundary every other forged-row scenario in this codebase assumes;
(2) knowing a specific ruling id and its exact historical policy hash from
a window that genuinely was open at some point; (3) the ability to set
`order` on the forged row to a value inside that window rather than the
current time, which every write path but a direct `requests.append` call
denies.

Do not try to close this by making `order` unforgeable at the field level
(a caller with `requests.append` access can already write any well-formed
row) or by adding a monotonic counter that only `_stamp` increments (a
forger calling `_stamp` gets a fresh one same as anyone, so it does not
help unless the counter is bound to something outside the row itself,
like an append-position the file offset already encodes but nothing reads
back today). If this is ever worth closing, the shape is a receipt written
by `accept()` itself into a THIRD location neither ledger's `append`
reaches, not another field on the row a forger already controls.
