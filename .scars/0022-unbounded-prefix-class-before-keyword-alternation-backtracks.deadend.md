---
id: 22
type: deadend
title: Unbounded [\w-]* prefix before a keyword alternation backtracks quadratically — bound it ({0,32}?) or the write path hangs
severity: high
confidence: 0.9
created: 2026-07-07
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/redact.py
evidence:
  - pr: 108
  - note: adjudicated fix in 964dfbb widened api-key to [\\w-]*(?:api[_-]?key|...) so env-var names match; passed unit tests AND task review; final-review adversarial probe measured O(N²): 16KB no-separator run = 7s, 32KB ≈ 28s — a checkpoint write freeze fail-open cannot catch (slowness is not an exception). Fixed in aa966df: [\\w-]{0,32}? lazy bounded prefix -> 0.40ms at 32KB
expires:
  condition: "redaction moves off Python re (e.g. re2/hyperscan) or gains a timeout wrapper"
  review_after: 2027-07-07
violation: "\[\\w-\]\*\(\?:"
status: active
---

Tried in #104: widening the api-key pattern with an unbounded `[\w-]*` prefix
so env-var-style names (DAIMON_LLM_API_KEY=) match the keyword alternation.
Failure mode: the prefix class overlaps the keyword characters, so on a long
`[\w-]` run with NO separator (pasted base64url blob, concatenated hashes)
the engine retries every split point — quadratic time, and this regex runs on
every text/quote of every item on every checkpoint write. Unit tests and
per-task review both passed; only an adversarial LENGTH probe surfaced it.
Rule: any prefix class before a keyword alternation must be BOUNDED and lazy
(`[\w-]{0,32}?`), and every capture-path regex gets a long-input completion
test (50k chars, no timing assert — completion IS the signal). Python `re`
has no timeout; fail-open except-clauses do not protect against slow matches.
