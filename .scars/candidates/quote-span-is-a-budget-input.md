---
id: 0
type: landmine
title: The quote cap is a budget input, so changing QUOTE_CHARS or quote_span's containment rule changes which items survive the briefing
severity: medium
confidence: 0.85
created: 2026-10-03
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/display.py
  - path: plugin/daimon_briefing/briefing.py
evidence:
  - note: "#1129 replay (research/experiments/quote-span-1129/measurements.json): at the default budget, 734 of 980 briefings over the local store keep a different set once the quote is a capped span instead of whole; 2857 items gained, none lost"
expires:
  condition: "the briefing stops charging the quote to the byte budget, or verbatim items stop carrying quotes"
  review_after: 2027-04-01
status: candidate
---

Evidence quotes sit almost only on verbatim items, and `briefing._line` puts
them on the item's line, so a quote is part of what that item costs the #1128
byte budget. `display.quote_span` caps it at `QUOTE_CHARS` (120) and hides it
when the item's text already contains it. That makes the cap, and the
containment rule, an input to `select`: it is scar 0095's mechanism with a
second field. Verbatim text is exempt from stage-1 shortening, so before the
cap a long quote was the one part of a verbatim item the budget could not
touch.

Measured, not reasoned: replaying the local checkpoints at the default budget,
the whole-quote arm and the span arm keep different sets in 734 of 980
briefings (364 at one day, 370 at thirty). The span arm only gains items
(1699 verbatim, 1158 inferred), because the cheaper verbatim items free
budget for others. Ordering tests do not see any of this: section order is
identical in both arms.

What a future editor must do: treat a change to `QUOTE_CHARS`, to the
`(?<!\w)`/`(?!\w)` edge handling in `quote_span`, or to the rule that
containment is decided against the ORIGINAL text (`_quote_in_text`, set in
`select` stage 1) as a change to which items survive, and re-run the replay.
The opt-in LLM path passes `full_quotes=True` to `select` on purpose, because
it must reproduce verbatim quotes whole; do not collapse the two sizing
models into one without changing `_validate_llm_render` too.
