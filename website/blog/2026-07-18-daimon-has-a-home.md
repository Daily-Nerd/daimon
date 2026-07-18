---
slug: daimon-has-a-home
title: "daimon has a home: bilingual docs, and what shipped this week"
authors: [daimon]
tags: [announcement, release]
---

daimon now has a documentation site — the one you are reading — in English
and Spanish, with a quickstart that takes you from install to your first
briefing, and concept pages for the ideas that make daimon different: trust
classes, carry, receipts, and the item lifecycle. This blog is the new
canonical home for releases, feature explainers, and field incidents; every
announcement you see from us elsewhere will link back here.

{/* truncate */}

## What shipped this week

**daimon 0.18.0 is on PyPI** (`uv tool install 'daimon-briefing[pretty]'`).
The headline experiment: opt-in per-item **scene traces**, indexed for
recall. It ships behind a flag while we A/B it against our own benchmark —
if the numbers don't earn it, it doesn't go default-on. That's the deal we
make with every feature.

**`daimon forget` is merged** and ships in the next release: item removal
with a tombstone event. The item leaves the live checkpoint, the recall
index, and the audit trail's *content* — but the event stream keeps a
content-hash tombstone, and with receipts enabled the post-removal
checkpoint is re-signed. Deletion you can prove happened, without keeping
what was deleted. The [item lifecycle](/docs/concepts/lifecycle) page covers
the mechanics.

**The docs went bilingual.** Every page — quickstart, concepts, hosts,
configuration, team memory — is available in Spanish. Not machine-dumped:
written for Spanish-reading developers, because the es-speaking agent-dev
community deserves first-class docs, not an afterthought.

**Windsurf is live-validated.** The capture loop (native-transcript
serialize) has now been tested end-to-end in real Windsurf use, joining
Claude Code. Codex ships next; Gemini waits on an upstream fix.

## Why this project exists, in one paragraph

Your agent forgets everything between sessions, and most memory systems
"fix" that by storing text a model wrote about what happened — with no way
to tell which parts are quotes and which parts are guesses. daimon marks
every remembered item as **verbatim** (an exact quote, mechanically verified
against the transcript by a deterministic checker — no LLM grading its own
homework) or **inferred** (allowed to evolve, flagged for verification). A
recent survey of agent-memory research calls claim-level provenance an open
problem; we think the answer is to make memory *provable*, and that is the
axis everything here is built on.

More soon — releases, war stories from the field, and deep dives into how
the verification machinery works. Subscribe via [RSS](https://daily-nerd.github.io/daimon/blog/rss.xml) or
follow the repo on [GitHub](https://github.com/Daily-Nerd/daimon).
