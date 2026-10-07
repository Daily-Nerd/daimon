---
id: 0
type: landmine
title: A note printed above the greeting without the warning mark becomes the briefing's header for every machine reader
severity: medium
confidence: 0.85
created: 2026-10-07
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/cli/brief.py
  - path: plugin/daimon_briefing/marks.py
evidence:
  - note: "#1132 PR 7b: anamnesis' parse_briefing keeps every line holding the warning mark as a warning, then takes the first remaining non-empty line as the header. Before 7b, `brief --slug X` printed 'cross-project briefing - project: X' and a serialize-in-flight note unprefixed, so each took the header and the greeting was lost. tests/test_briefing_contract.py pins it over real output."
expires:
  condition: "the briefing text gains an explicit header marker that parsers read instead of 'first line that is not a warning', or the parser stops depending on line order"
  review_after: 2027-04-01
status: candidate
---

`daimon brief` is read by people and by programs: the session hooks of five
hosts capture it, and a chat bridge parses it with one rule: a line carrying
the warning mark (or the phrase VERIFY BEFORE TRUSTING) is a warning, the
first other non-empty line is the header, a section sign starts a ruling, a
dash or star starts an item, and every other line is dropped.

So a note printed before the greeting that does not carry the mark silently
replaces the header, and the greeting is parsed as a stray line and lost.
That was the cross-project and serialize-in-flight notes until 7b.

Do not print a note above the greeting through `render.render_brief_line`,
and do not pass the warning mark in by hand: `render.render_brief_note` adds
it once. A line that is not a warning (no briefing yet, no bucket for a slug)
goes through `render_brief_line` and is the header on purpose, because it is
the only line. The marks live in `marks.py`; `api.parse_briefing` reads from
the same constants.
