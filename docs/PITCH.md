# Positioning

> **This doc replaced an earlier pitch** that sold Daimon as a standalone, category-defining "persistent AI companion" with a 9/10 moat and market sizing. That framing was retired per **[D-008](../research/DECISIONS.md)** (user-approved 2026-06-09). Track B (`research/findings/07`) found ~7/9 of the proposed epistemic-graph features already shipped by Honcho + Graphiti — the "differentiator" was a dependency. No market sizing, no empty-quadrant claim, no moat language below. The success metric is post-ship adoption, not a category slide.
>
> **Superseded again by [D-009](../research/DECISIONS.md)** (2026-06-27): D-008's own "build on Honcho + Graphiti" framing was never implemented. The shipped runtime is **self-contained and host-agnostic** — its own lean stack (checkpoint store + prose serializer + deterministic render + pluggable LLM backend), no server, no external memory backend. Honcho/Graphiti remain evaluated-and-rejected alternatives (see "Why it doesn't compete with Honcho or Graphiti" below), not a dependency. This doc is preserved as the historical pitch; the current authoritative architecture is [MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md).

---

## What it is

Daimon is a **dream-briefing skill** for coding agents. At session end it writes a small cognitive checkpoint; at the next session's start it reconstructs that into a short, skimmable briefing — "while you were away / here's where we left off." The agent resumes from a faithful prior state instead of confidently guessing.

The primary integration is native host hooks (Claude Code, Codex, Gemini, Windsurf) shelling out to the `daimon` CLI; a hermes-agent plugin is an optional secondary surface. See [MVP-DREAM-BRIEFING.md](./MVP-DREAM-BRIEFING.md) for the authoritative architecture.

---

## Who it's for

Users of hermes-agent / Claude-Code-style agents who work across multiple sessions and lose carried-over context when they resume — especially when state changed *outside the AI ecosystem* between sessions (a PR merged in the GitHub UI, a deploy that failed overnight). The briefing surfaces exactly those carried-over open loops.

---

## Why it doesn't compete with Honcho or Graphiti

It depends on them. The memory substrate is already shipped, production, and inside the hermes ecosystem (`research/findings/07`):

- **Honcho** (~5k★, AGPL-3.0) — LLM-driven deriver, per-peer representation, belief reconciliation, Dialectic query API.
- **Graphiti** (~27k★, Apache-2.0) — bi-temporal knowledge graph, validity intervals, overlap-gated contradiction resolution shipped verbatim in code.

Of the ~9 epistemic-graph / memory features the old pitch claimed as a differentiator, ~7 are covered by one or both. Rebuilding that store would be reimplementing a funded, shipped product. Daimon *consumes* their output; it does not replace it. (D-005's temporal-KG mechanism is retracted as novel — Graphiti ships it verbatim.)

---

## What's genuinely net-new

In descending order of defensibility (`research/findings/07`):

1. **The briefing UX** — a session-*start* "while you were away" artifact. Neither Honcho (which answers *queries*) nor Graphiti (a store) ships this packaged, skimmable, proactive briefing. Demonstrated valuable live. Highest-value piece.
2. **The initiative taxonomy** — attention-gated proactive interruption (confidence × relevance × attention). MVP ships **Level 0 only** (pull at session start; nothing pings you); Levels 1–3 are deferred (`research/findings/05`).
3. **A Claimify-style extraction gate** — a high-confidence, decontextualized extraction pass in front of fact creation, **absent in Graphiti** (`node_operations.py`). Its natural home is an **upstream PR to Graphiti**, not a standalone build. *Verify the gap against current Graphiti before claiming it (`findings/07` quotes were @ v0.29.1).*

A fourth piece — semantic evolution-vs-contradiction classification — is hardest, narrowest, and unproven; it stays a research note, not a deliverable.

---

## What's honest about the bet

The reconstruction step was the load-bearing risk, and it was measured (Track A, n=5, `research/findings/03`):

- **Confabulation was not the problem** in single-cycle reconstruction — FMR ~1.0%, max 4.8%. (Multi-cycle drift remains **untested**; not yet claimable.)
- **Recall is the weakness** — RR 67.3%, and it tracks transcript *length* (sharp cliff ~1,400 lines). The fix lives in the serialize step (D-007); whether it's a prompt change or chunked extraction is open.

The briefing's job is **recall fidelity, not fluency**. The discipline is honest measurement against held answer keys, not vibes — see `research/findings/03` and the open VERIFIED/ASSUMED list in [MVP-DREAM-BRIEFING.md §9](./MVP-DREAM-BRIEFING.md).

---

## Success metric

Post-ship adoption of the skill — installs / active users among hermes / Claude-Code users — not market size, not a category. If it doesn't improve a real workflow, it doesn't matter how the slide reads.
