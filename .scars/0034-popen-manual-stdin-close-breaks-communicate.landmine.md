---
id: 34
type: landmine
title: Manually closing a Popen stdin pipe makes communicate() raise — and fail-open reapers turn that into silent skips
severity: medium
confidence: 0.9
created: 2026-07-31
authors: ["claude-code", "kibukx"]
anchors:
  - path: plugin/daimon_briefing/worldcheck.py
evidence:
  - note: #439 implementation session 2026-07-31: every stdin-bearing vitni probe skipped silently; answer was on stdout the whole time
expires:
  condition: "worldcheck._run_probes stops writing probe stdin manually (e.g. moves to communicate(input=...) with its own timeout discipline)"
  review_after: 2026-10-31
status: active
---

`subprocess.Popen(stdin=PIPE)` + a manual `proc.stdin.write(...); proc.stdin.close()`
(needed when you poll under a shared deadline instead of blocking in
`communicate(input=...)`) leaves a closed pipe object on the Popen. A later
`proc.communicate()` flushes `self.stdin` UNCONDITIONALLY and raises
`ValueError: I/O operation on closed file`. Inside a fail-open reaper
(`except Exception: continue`, the worldcheck contract) that ValueError reads
as "probe failed" — every stdin-bearing probe becomes a silent skip while the
correct answer sits on stdout. The failure mode is silence, not an error;
gauges read green. Fix that must be preserved: set `proc.stdin = None`
immediately after the manual close (worldcheck._run_probes does this — do not
"clean it up"). Sibling trap: do not swap the runner for `receipts._run_cli`;
its blocking `subprocess.run(timeout=10)` is 12× worldcheck's whole 0.8s
budget.
