---
id: 0
type: landmine
title: Local patch-coverage green does NOT mean codecov green when a branch depends on LibreSSL-vs-OpenSSL behavior
severity: medium
confidence: 0.9
created: 2026-07-10
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/receipts.py
evidence:
  - pr: 205
  - pr: 207
  - note: "three catch-up commits across two PRs: 71feff7/efa57aa (#205), post-review sweep (#207) — each time local intersect said 0 missing, codecov said otherwise"
expires:
  condition: "openssl subprocess path removed from receipts.py (vitni keygen becomes the only derivation), or CI adds a macOS/LibreSSL coverage job"
  review_after: 2026-10-01
status: candidate
---

receipts.py's key derivation branches on what the host crypto CLI can do:
macOS LibreSSL fails Ed25519 (`openssl pkey` → rc≠0), CI's OpenSSL 3.x
succeeds. Any test that exercises the REAL subprocess therefore covers
DIFFERENT lines locally than on CI — the failure branch is green on the dev
Mac and missing on ubuntu, and vice versa for the success body. Verifying
patch coverage locally before push (coverage-json ∩ diff-hunks) reads 0
missing while codecov still flags lines; it took three catch-up commits
across PR #205 and PR #207 to learn this. Rule: for any change touching a
platform-conditional subprocess branch here, either (a) add a
monkeypatched-subprocess twin that walks BOTH branches deterministically on
any box, or (b) pull codecov's per-line report for the pushed sha
(api.codecov.io /report/?path=...&sha=...) instead of trusting the local
intersect. Local green is necessary, never sufficient, on this file.
