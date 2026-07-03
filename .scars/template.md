---
# COPY THIS FILE — do not edit the template itself.
# New scars: write to .scars/candidates/<slug>.md with status: candidate.
# A human reviewer promotes to .scars/NNNN-<slug>.<type>.md with status: active.
id: 0                      # assigned at promotion (next free NNNN)
type: deadend              # deadend = tried+failed | fence = looks wrong, intentional | landmine = touching A breaks B
title: One line, searchable, says the constraint
severity: medium           # low | medium | high | critical
confidence: 0.7            # 0..1 — how sure are we this still holds
created: 1970-01-01
authors: ["claude-code"]   # add the human reviewer at promotion
anchors:
  - path: src/module/      # file or directory this protects
  - pattern: "regex"       # optional: fires when matching code appears in ANY new/edited file
evidence:
  - commit: abc1234        # at least one receipt: commit, pr, incident, or note
expires:
  condition: "what change would make this scar obsolete"
  review_after: 1971-01-01 # force a freshness look even if condition never triggers
status: template           # candidate | active | challenged | archived  (template = never parsed)
---

Body: 5-15 lines of prose. What was tried/observed, why it failed or why the
weirdness is intentional, and what a future editor must do instead. Write it
for someone (human or agent) with zero context. Cite the evidence inline.
