# Decisions (Why We Chose What We Chose)

ADR-lite. Each decision: what, alternatives, rationale, reversal cost, date, status. Reversal cost = how long/painful to undo (low/medium/high).

| ID | Decision | Reversal cost | Status | Date |
|---|---|---|---|---|
| D-001 | License = Apache-2.0 (over AGPL-3.0) | low | Active | 2026-06-09 |
| D-002 | Validate-before-build gate (no Phase-1 code until VALIDATION.md passes) | low | Active | 2026-06-09 |
| D-003 | File-based research logbook mirroring Daimon's memory layers | low | Active | 2026-06-09 |
| D-004 | Prefer edit-style incremental memory over batch end-of-session summarization | medium | Active (evidence-backed) | 2026-06-09 |
| D-005 | Contradiction detection via temporal-KG validity intervals, NOT raw NLI | high | ⚠️ Novelty retracted — Graphiti ships it verbatim (Track B); reframe to depend-on-Graphiti | 2026-06-09 |
| D-006 | Pin load-bearing facts extractively (verbatim) in the checkpoint; generate only narrative | medium | Active (evidence-backed) | 2026-06-09 |
| D-007 | Extend serialization to capture assistant-side fixes + impl decisions + open end-questions | medium | ✅ Resolved — probe: prompt helps, chunking REQUIRED (armC RR 89.7%/FMR 6.7% clears bars) | 2026-06-09 |
| D-008 | Pivot: standalone epistemic-graph product → dream-briefing layer on Honcho + upstream contributions to Graphiti | high | ⛔ Superseded by D-009 (Honcho/Graphiti runtime dependency never shipped) | 2026-06-09 |
| D-009 | Self-contained, host-agnostic core — Honcho/Graphiti evaluated, NOT adopted as runtime deps (supersedes D-008) | high | ✅ Active | 2026-06-27 |

---

## D-001 — License: Apache-2.0 over AGPL-3.0

**What:** The Daimon OSS layer is licensed Apache-2.0.

**Alternatives:** AGPL-3.0 (strong copyleft, network-use trigger), BSL (delayed open).

**Rationale:** Hermes — the dependency Daimon builds on — is MIT, compatible both ways. Apache-2.0 removes adoption friction (AGPL appears on enterprise blocklists), matches Letta's successful posture, and cleanly separates the OSS core from any future commercial layer with no copyleft obligation on self-hosters. The real moat is accumulated user data, not a hostile license.

**Reversal cost:** Low — pre-1.0, nothing published.

---

## D-002 — Validate the load-bearing bet before building

**What:** No Phase-1 implementation until the 10-day validation gate (`docs/VALIDATION.md`) returns a Build verdict.

**Alternatives:** Start building Phase 1 immediately per the RFC milestones.

**Rationale:** Three independent reviews converged: the CRP is the load-bearing bet and its confabulation risk is unflagged and untested. Building a persistence layer on an unvalidated reconstruction mechanism is laying foundation on sand. Two weeks of validation beats six weeks of misdirected building.

**Reversal cost:** Low — it's a gate, not a build.

---

## D-003 — File-based research logbook, shaped like Daimon's memory

**What:** Maintain project memory in `research/` with files mapped to Daimon's own memory layers (episodic/semantic/decisions/open-loops).

**Alternatives:** Single flat notes file; external tool (Notion); engram only.

**Rationale:** Daimon doesn't exist yet, so we hand-build its memory. Mirroring its target schema means the manual process is itself a usability test of the schema. Files are git-versioned, diffable, and committable alongside the docs they inform. (engram is also used in parallel for cross-session recall during development; the files are the canonical, shareable record.)

**Reversal cost:** Low — restructure anytime.

---

## D-004 — Edit-style incremental memory over batch summarization (TENTATIVE)

**What:** Lean toward incremental insert/update/delete memory operations (à la Mem0 / A-MEM) rather than the RFC's "summarize the whole session at end."

**Alternatives:** RFC's batch recursive summarization; pure extractive; raw-transcript-and-retrieve.

**Rationale:** 2026 literature shows batch summarization accumulates drift; edit-style operations reduce compounding loss — directly relevant to the confabulation risk. **Tentative** because it is not yet validated for Daimon's specific use; Track A may inform it.

**Reversal cost:** Medium — touches the core checkpoint format.

**Status:** **Active.** Firmed by evidence (`findings/02`, `06 §A`): Mem0's ADD/UPDATE/DELETE/NOOP gives +26% rel over OpenAI memory at 91% lower latency and resists silent fact-loss (the dominant confabulation mechanism). **Caveat:** full-context still leads *raw accuracy* (72.9 vs 66.9) when history fits — edit-style wins on drift/cost/scale, which is what matters over a long relationship.

---

## D-005 — Contradiction detection via temporal-KG validity intervals, not raw NLI

**What:** The epistemic graph detects contradictions by storing each belief as a temporal-KG edge with a validity interval (Zep/Graphiti pattern); a new belief about the same subject *supersedes* the old with a timestamp; a contradiction is flagged **only** when two beliefs have *overlapping* validity intervals. Belief extraction uses a Claimify-style disambiguation gate first.

**Alternatives:** Raw NLI (DeBERTa-MNLI) over conversation history; frontier-LLM judge over the full transcript.

**Rationale:** Raw NLI scores **23.94% precision on natural dialogue** ([DECODE 2012.13391](https://arxiv.org/abs/2012.13391)) — ~3 false alarms per true hit, product-lethal for a trust feature. The temporal-KG approach converts "is this a contradiction?" (semantically hard, ~24%) into "do these validity intervals overlap?" (mostly mechanical, high precision) and correctly treats belief *evolution* as supersession, not contradiction — which is the entire point of Daimon's "intellectual mirror." Evidence: `findings/04`, `06 §C`.

**Reversal cost:** High — defines the epistemic-graph engine architecture.

**Status:** ⚠️ **Novelty retracted (Track B, `findings/07`).** Graphiti ships this exact mechanism — temporal validity intervals + overlap-gated contradiction — verbatim in `resolve_edge_contradictions` (Apache-2.0, ~27k★). D-005 as written is a reimplementation of Graphiti. **Reframe:** *depend on Graphiti* for the temporal store; the only net-new surface is the Claimify extraction gate (absent in Graphiti — a contribution) and semantic evolution-vs-contradiction classification (hard, unproven). Track C as an original build is moot.

---

## D-008 — Pivot from standalone product to dream-briefing layer + upstream contributions

**What (user-approved 2026-06-09):** Stop positioning Daimon as a standalone "persistent AI companion" competing on an epistemic graph. Instead: (a) build the **CRP dream-briefing UX** as a hermes-agent skill / thin layer on Honcho; (b) **contribute** the Claimify extraction gate and evolution-classification upstream to Graphiti/Honcho; (c) drop the epistemic-graph differentiator and the market-sizing pitch; (d) answer Q-5NAMES — if it's a personal tool, build it as one without category slides.

**Alternatives:** Persevere as a standalone product (compete with Honcho 5k★ + Graphiti 27k★ + hermes); park; kill.

**Rationale:** Track B (`findings/07`) found ~7/9 epistemic-graph/memory features already shipped by Honcho + Graphiti, both production, both already in the Hermes ecosystem Daimon sits on. The stated differentiator is a dependency, not a moat. What genuinely survives is the **dream-briefing artifact** (neither incumbent ships it; demonstrated valuable live) and the **initiative taxonomy**. Riding existing distribution beats cold-starting against funded incumbents.

**Reversal cost:** High — it redefines what the project IS.

**Status:** ⛔ **Superseded by [D-009] (2026-06-27).** The "build on Honcho + Graphiti" half of this pivot was never implemented — the shipped runtime is self-contained (see D-009 for why and the evidence). What survives from D-008: the **dream-briefing UX** as the North Star, the **skill/community framing**, dropping the market-sizing pitch, and the Track A CRP work (D-006/D-007). What is reversed: Honcho/Graphiti as the runtime memory substrate.

---

## D-009 — Self-contained, host-agnostic core (supersedes D-008's Honcho/Graphiti dependency)

**What (2026-06-27):** Daimon's shipped runtime is **self-contained and host-agnostic**, NOT a layer on Honcho + Graphiti. The substrate is its own lean stack: a per-project JSON checkpoint store (`store.py`; rotation #33), a prose serializer with extractive trust/provenance/supersession (`serializer.py`; D-006/D-007), a deterministic briefing render, and a pluggable LLM backend (#42). It runs today on **Claude Code and Codex** via native hook scripts (`hook/daimon-session-end.py`, `hook/daimon-codex-stop.py`) plus a host-free dogfood CLI; hermes is one optional host, not a requirement (`transcript.py` guards the hermes import; `cli.py` "works WITHOUT hermes"). Honcho and Graphiti are **evaluated-and-not-adopted as runtime dependencies.**

**Alternatives:** Follow D-008 and build on Honcho + Graphiti at runtime; hybrid (optional Graphiti backend behind a flag).

**Rationale:** D-008's dependency framing never shipped, and later evidence inverted it. (1) **Hard constraints:** Honcho is a server; Graphiti needs a graph DB + embeddings + an LLM-extraction endpoint — each reintroduces the gateway/embedding fragility that caused **days of LiteLLM outages this cycle**, and breaks offline-first + lean/local-first. (2) **Own research:** memory-backend (PR #40/#41) killed CSL, found **prose/retrieval the efficient frontier** and **lexical SQLite/FTS5** the right substrate (embeddings only if proven necessary); Graphiti appears there only as a benchmark arm, never wired in. (3) Graphiti's one load-bearing idea — temporal-validity supersession — Daimon already does cheaply in the serializer schema (subsumes D-005's "depend-on-Graphiti" reframe). Net: a smaller, more portable, more robust core than the dependency path.

**Reversal cost:** High — it redefines what the project depends on and how it is positioned.

**Status:** ✅ **Active (2026-06-27).** Supersedes **D-008**; closes **D-005**'s depend-on-Graphiti reframe. Follow-ups: (a) correct README/docs to the self-contained, host-agnostic reality (this change); (b) formalize a host-adapter so Odysseus / openclawd / other agents are first-class targets; (c) the team-shared-memory research (vault) must honor the same lean/offline/no-gateway constraints.

---

## D-006 — Pin load-bearing facts extractively in the checkpoint

**What:** In the cognitive checkpoint, load-bearing facts (decisions, open questions) are stored **verbatim/extractively** with a trust-class marker; only the connective narrative is generated. Reconstruction trusts pinned facts and may only re-narrate around them.

**Alternatives:** Fully generative checkpoint (RFC default — everything summarized).

**Rationale:** The dominant measured confabulation mechanism is *silent information loss*, not invention (`findings/03`, `06 §B`). Extractive pinning makes the critical facts impossible to silently drop or fabricate, confining the generative risk to harmless narrative. Also lets reconstruction know what to trust under lost-in-the-middle conditions.

**Reversal cost:** Medium — changes the checkpoint schema (`RFC §5.1`) to carry trust classes.

**Status:** Active, to be validated within Track A (test batch vs edit vs extractive-pinned).

---

## D-007 — Extend serialization to capture the assistant-side execution layer

**What:** The cognitive-checkpoint serializer must extract not just user-stated decisions but also: assistant-side fixes/diagnoses, implementation-level decisions, and open end-of-session questions. The current `SERIALIZE_SYS` over-anchors on user utterances.

**Alternatives:** Leave serialization as-is and accept ~67% recall; attack recall at the reconstruction step instead.

**Rationale:** Track A (n=5 real sessions) returned **PIVOT — confabulation refuted (FMR 1.0%) but recall only 67.3%**, driven by the checkpoint dropping the assistant-side troubleshooting/fix layer on messy sessions (~54% vs ~86% on decision-dense ones). Reconstruction faithfully replays the checkpoint with ~0 confabulation, so the fix belongs in serialization: a richer checkpoint should lift recall without reintroducing fabrication. This is a prompt+schema change, not a fundamental limit.

**Reversal cost:** Medium — changes the serialize prompt and likely the checkpoint schema.

**Status:** ✅ **Resolved (probe, 2026-06-09 — `findings/03`).** Three-arm probe on S2 (the worst long session): D-007 prompt alone lifts RR 37.9%→58.6% (helps, insufficient); D-007 prompt + **chunked multi-pass extraction** (800-line chunks + merge) reaches **RR 89.7% / FMR 6.7%** — clears both bars. The richer prompt is adopted AND the architecture is chunked: serialization for long transcripts is chunk→extract→merge (`docs/MVP-DREAM-BRIEFING.md §4`). Merge pass showed no fabrication (FMR under bar). Caveats: n=1, LLM-judged. See also `.scars/0001` (judge grounding must reference transcript, not answer key).

## D-013 — Verbatim expiry stays; verbatim immutability extended to every render surface

**What (2026-07-03):** Two rules pinned after the four-lens audit (issue #30). (1) **Verbatim items are NOT exempt from carry's weight-floor expiry** — rational forgetting (Anderson & Schooler; the #78/#123 decay model) applies to every trust class. Verbatim means *exact wording*, not *immortal*: an unresolved verbatim item nobody has touched decays past the floor and drops from carry like any other item. (2) **Verbatim text is immutable on every surface, not just carry (#23):** render-time budget truncation (stage 1, `briefing.render_plain`) skips verbatim items' text — an oversized verbatim item may be *dropped whole* (announced by the trim note) but never rewritten in place — and the opt-in LLM briefing render is post-validated (`_validate_llm_render`): a render that loses or mutates any verbatim quote is rejected and the deterministic render takes over.

**Alternatives:** Full expiry exemption for verbatim (risk: the cap-8/kind budget fills with stale verbatim items and crowds out fresh carries); a longer decay horizon for verbatim (adds a tuning knob with no field data — violates don't-tune-on-n=1); leaving the LLM render unvalidated but documented as experimental (keeps the differentiator broken on an opt-in path).

**Rationale:** The verbatim/inferred distinction is the product's central guarantee; it must mean exactly one thing everywhere. Immutability governs *wording* (reconsolidation fix, #22/#23); expiry governs *retention* (rational forgetting). Keeping the two orthogonal preserves both cognitive-science groundings without inventing an unfounded knob.

**Reversal cost:** Low — an expiry exemption or horizon knob can be added in carry.merge if field data ever shows load-bearing verbatim items dying at the floor.

**Status:** ✅ **Active (2026-07-03).** Implemented with issue #30: truncation exemption + LLM-render post-validation + untagged trust rendering. Related: #22/#23 (freeze on re-discovery), D-006 (extractive pinning).
