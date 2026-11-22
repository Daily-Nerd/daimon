---
id: 0
type: landmine
title: daimon counts the lines handsal will render for signed text, and must never wrap or change that text
severity: high
confidence: 0.85
created: 2026-11-21
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/authority.py
  - path: plugin/daimon_briefing/requests.py
  - path: plugin/daimon_briefing/refutations.py
evidence:
  - note: "handsal v0.4.2 spec/profile-interfaces.md RN-05 and RN-12; conformance vectors rnd.request-long-line-continuation, rnd.truncate-not-inside-escape"
expires:
  condition: "handsal offers a call that counts or checks the render of bare content, so the copy of the rule can be deleted"
  review_after: 2027-05-21
status: candidate
---

A person signs the stored text exactly as handsal's renderer shows it, and
the signature binds the hash of those bytes. The renderer wraps long lines
itself (continuation lines of at most 96 escaped bytes, an escape never split).
`authority.rendered_lines` only COUNTS that wrap, to refuse text over 32 lines
when it is written. If daimon wrapped, trimmed or normalised the text to make
it fit, the signed bytes would differ from the stored row and the recheck would
report a mismatch.

Rules: the count is greedy (an escape that does not fit the rest of a line
moves whole to the next one, so ceil(escaped / 96) undercounts); a final LF
starts no line and empty content has none; the ask and the ruling verdict are
checked on the string that will be stored (after whitespace collapse and
redaction), through `signable_problem`, at the one write boundary of each
ledger (`requests._ask`, `refutations._guard_ruling_text`). Existing rows over
the bound are left alone; `build_decision` refuses them. Any new copy of the
limit outside `authority.py` fails `tests/test_authority_census.py`.
