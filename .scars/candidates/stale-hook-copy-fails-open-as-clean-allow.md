---
id: 0
type: landmine
title: A stale hook copy fails open and is byte-identical to a clean allow
severity: high
confidence: 0.9
created: 2026-09-06
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/checks_host.py
  - path: hook/
  - pattern: "spec_from_file_location"
evidence:
  - note: "#943 slice 3, one debugging cycle lost to it"
status: candidate
expires:
  condition: "the end-to-end hook tests load the canonical file, or the sync runs automatically before pytest"
  review_after: 2027-03-06
---

The pre-action hook loads `checks_host.py` from its own directory by file
location, so `hook/checks_host.py` is what the end-to-end tests actually
execute. Edit the canonical `plugin/daimon_briefing/checks_host.py` and run
those tests without `scripts/sync_hooks.py` first, and the script loads the
stale copy, a missing attribute raises, and the top-level guard swallows it.
The result is exit 0, empty stdout, and no firing row.

That is byte-identical to a clean allow. Every assertion about the host
contract passes, and the test that should have caught the new behavior simply
does not see it. The pre-commit drift check catches this before a commit but
not before a test run, which is where the time goes.

Run the sync before any end-to-end hook run, and point unit tests at the
canonical file (`test_checks_host.py` loads it by path for exactly this
reason). The fail-open guard is correct and must stay: it is what keeps a
broken hook from blocking a session. It just means a missed sync looks like a
decision instead of a failure.
