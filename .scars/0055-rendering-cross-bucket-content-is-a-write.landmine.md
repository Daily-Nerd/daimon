---
id: 55
type: landmine
title: Printing another bucket's record text writes that text into THIS project's checkpoint, where forget can never reach it
severity: high
confidence: 0.9
created: 2026-08-27
authors: ["claude-code", "Kibukx"]
anchors:
  - path: plugin/daimon_briefing/cli/
  - pattern: "_bucket_slugs"
evidence:
  - note: transcript.py:138 _tool_result_of flattens tool_result payloads; transcript.py:302 gives them their own row shape (#359) — so CLI stdout in an agent session is checkpoint input
  - note: recall.py:760-766 already implements the safe pattern and states the doctrine: counts by project, never content, because crossing projects stays user-invoked (#94/#95)
  - note: daimon#766 design review 2026-08-27 — caught before any code was written
expires:
  condition: "capture excludes daimon's own CLI output from serialization, OR surfaces.py can map a deletion into checkpoint item text that originated as captured tool output"
  review_after: 2027-02-27
status: active
---

daimon serializes the host transcript INCLUDING tool_result payloads (`_tool_result_of`,
transcript.py:138; row shape at :302, #359). So inside an agent session **CLI stdout is
checkpoint input: printing is writing.**

A command that enumerates buckets and prints their record text therefore copies that
plaintext into the checkpoint of whichever project it was RUN in. `forget` in the origin
project can never reach the copy: surfaces.py maps deletion per file shape per bucket, and
there is no shape for "project B's plaintext inside project A's item text". Both projects
then audit clean while A holds text B deleted. This is the defect class surfaces.py:4-12
was built to end (#583, #599 twice, #600), reappearing as a CONTENT path rather than a
FILE path, which is why the write-audit registry cannot see it. It is recursive: A's
briefing can re-emit the copy. And it ships green, because that guard watches file writes,
not render content.

**The safe pattern already exists — copy it.** recall.py:760-766 auto-widens across
projects and deliberately reports counts by project and never content, because "crossing
projects stays user-invoked (#94/#95), the system just stops hiding that crossing would
pay". `recall --all-projects` does cross with content, but only when a human explicitly
asks for it. The bounded exposure comes from that user-invoked gate, not from luck.

So: render ids, counts and the owning slug by default, and make the operator open the
owning project to read the text. A surface that crosses with content by default removes
the only thing keeping this bounded. If foreign text must be rendered, the deletion story
has to exist BEFORE the render does. The shipped request panel is not a precedent for
skipping this: it shows only asks addressed to THIS project, capped at 3
(requests.py:102) and truncated to 160 chars (briefing.py:806).
