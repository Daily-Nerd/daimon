---
id: 48
type: landmine
title: Verifying redaction with a prefix literal is doubly invalid — it matches its own instructions and assumes a prefix the local key never had
severity: medium
confidence: 0.9
created: 2026-08-04
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/_hooks/redact.py
  - path: plugin/daimon_briefing/store.py
evidence:
  - note: 2026-08-04. A handoff prescribed `rg -l 'sk-proj' ~/.daimon/checkpoints/`, documented as 'empty = held', to verify #513 shape-based redaction. It failed twice over. (1) The credential in use is a local litellm proxy key, 25 chars, with no `sk-proj` prefix, so the grep could not have found it even had redaction failed. (2) It returned 4 files anyway, all of them the handoff sentence itself, which had serialized into latest.json, the session checkpoint, and events.jsonl.
  - note: 2026-08-04, the check redone correctly against the live value: the exact key appears in 3 raw transcripts under ~/.claude/projects/ and 0 checkpoints, and `sk-[A-Za-z0-9_-]{20,}` matches 0 checkpoint files. Redaction held, and this is the first evidence that actually tests it.
expires:
  condition: "redaction verification moves to a scanner that reads the configured key from the environment and excludes daimon's own captured prose"
  review_after: 2027-02-04
status: active
---

Two independent defects, either one fatal to the verification.

First, never hardcode a vendor prefix. daimon's configured credential is
whatever `DAIMON_LLM_API_KEY` holds, which on a proxy install is a local
litellm key that resembles no cloud vendor's format. A check written around
`sk-proj` silently passes on every install that does not use OpenAI directly,
which is the quietest possible failure for a security check.

Second, daimon captures the session that discusses daimon. Any literal used to
hunt for secrets is copied into the checkpoints it will later search, so the
detector starts matching its own instructions and can no longer distinguish
"a key leaked" from "someone wrote down how to look for a key".

Verify by reading the key from the environment and grepping for that value,
plus a generic shape (`sk-[A-Za-z0-9_-]{20,}`) as a backstop. Confirm the key
is present in the source transcript before claiming redaction held, or the test
proves nothing about redaction. Classify every hit before calling it a breach.

The same trap applies beyond secrets: the pre-post rg gate that keeps private
identifiers out of public issues keys on literals that land in checkpoints the
same way. Expected and unchanged: redaction runs at capture, so raw keys stay
in the host transcript regardless. Only checkpoints are cleaned.
