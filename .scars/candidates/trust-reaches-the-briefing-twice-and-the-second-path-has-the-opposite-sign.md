---
id: 0
type: landmine
title: Trust reaches the rendered briefing through TWO paths, and the truncation exemption pushes the opposite way from the weight lid
severity: medium
confidence: 0.9
created: 2026-09-12
authors: ["claude-code"]
anchors:
  - path: plugin/daimon_briefing/briefing.py
  - path: plugin/daimon_briefing/scoring.py
evidence:
  - note: "#976 replay — 320 of 6600 briefing comparisons differ between a gated and an ungated arm; one of them has zero lid-biting flips and is caused entirely by the stage-1 verbatim truncation exemption"
expires:
  condition: "render_plain stage 1 stops exempting verbatim items, or the exemption stops depending on the trust field"
  review_after: 2027-03-01
status: candidate
---

Everyone reasons about the #408 trust ceiling as THE way a trust class reaches
a read surface: `scoring.effective_weight` applies the lid last, so a lower
class ranks lower. That is true and it is not the whole mechanism for the
briefing.

`render_plain` stage 1 shortens oversized items in place and exempts verbatim
ones, because #23 froze verbatim text and a render that rewrites it breaks that
guarantee. So an item's trust class also decides whether its text costs the
budget its full length or `_ITEM_TRUNCATE_CHARS`. Downgrading an item to
inferred makes it truncatable, which FREES budget, which lets stage 2 keep an
item it would otherwise have dropped. The weight lid pushes a downgraded item
down; the truncation exemption pushes other items up. Same field, opposite
sign, different sections.

Measured, not reasoned: in the #976 replay one local checkpoint at the 30-day
horizon rendered identical section ORDER in both arms and still dropped a
different number of uncertainties (4 gated, 5 ungated). No flipped item in it
cleared the 0.63 soft-clip knee, so the weight path explains nothing. One
610-character inferred decision did it alone: truncated to 400 while inferred,
exempt once flipped to verbatim, 52 tokens heavier, one more item evicted.

What a future editor must do: when you measure or reason about "what the trust
gate costs the briefing", do not stop at `effective_weight`. Any change to
`_ITEM_TRUNCATE_CHARS`, to `truncate_preserving_sections`, or to the stage-1
exemption test `i.get("trust") == "verbatim"` is a change to how trust affects
which items survive the budget, and it will not show up in any ordering test.
