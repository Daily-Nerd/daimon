---
id: 0
type: deadend
title: top-level docs/ is not a mirror of website/docs/ — it has no reference/ section, so relative .md links assuming parity point at nothing
severity: low
confidence: 0.8
created: 2026-09-02
authors: ["claude-code"]
anchors:
  - path: docs/
  - path: website/docs/
evidence:
  - note: "#913: docs/hosts/claude-code.md and website/docs/hosts/claude-code.md carry the SAME stale 'Claude Code style' slug claim and were fixed in the same PR, which reads as if the two trees mirror each other page-for-page. They do not: website/docs/reference/cli.md exists, docs/reference/ does not exist at all (docs/ only has ARCHITECTURE.md, PITCH.md, hosts/, team.md, trust-inspector.md, etc. — internal working notes and validation records, confirmed by test_reader_facing_vocabulary.py's own comment: 'docs/ is deliberately absent — it holds working notes and validation records, not published prose'). A first attempt linked docs/hosts/claude-code.md to ../reference/cli.md#status, which resolves to nothing on GitHub."
expires:
  condition: "docs/ grows a reference/ section, or the two trees are formally declared independent (e.g. a README note) so no editor assumes parity again"
  review_after: 2027-03-01
status: candidate
---

`docs/` and `website/docs/` both describe hosts and both got edited in #913 for
the same stale-claim fix, which looks like two synced copies of one doc set.
They are not synced: `website/docs/` is the full Docusaurus site (getting-started,
reference, concepts, hosts, viewer, blog) with an ES mirror under
`website/i18n/`; `docs/` is a much smaller, GitHub-only set of internal/founder
docs (architecture, pitch, problem, RFC, validation, hosts/, team, trust
inspector) with no `reference/` section and no i18n mirror at all.

Before adding a cross-link from a page under `docs/` to another doc, check the
target actually exists in that tree (`fd -t f <name> docs/`) rather than
assuming the `website/docs/` layout carries over. A `.md`-relative link into a
nonexistent path fails silently on GitHub (renders as a dead link, no build
error — unlike the `website/` es-locale hazard in scar #24, which at least
throws at build time). Prefer describing the command in plain prose in
`docs/`, or point at a URL if a page there truly needs an inbound link.
