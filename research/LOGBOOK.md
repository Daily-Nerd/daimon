# Logbook (Episodic Memory)

Append-only. Newest entries at the top. Each entry: date, what happened, why it matters, pointers.

---

## 2026-07-02 — Deterministic carry (#33 Phase 2) validated: run-02 kills the loss failure mode under load — same-day loop from finding to shipped fix

**What happened:** Built deterministic carry per the vault ADR (PR #135: `carry.py` pure merge — #78 weight-floor expiry, salient-term dedup with first_seen inheritance, per-kind cap 8, `carried_from` provenance, idempotency + anachronism guards; wired into the cli serialize path default-ON with `DAIMON_CARRY=0` kill switch; `[carried]` briefing marker; multicycle driver mirror). Then ran **multicycle run-02** (control + distractor arms × 20 cycles, carry ON, haiku, 114,595 est. tokens) against run-01 as baseline.

**Results vs run-01 (the acceptance table):**

| metric | run-01 (no carry) | run-02 (carry) |
| --- | --- | --- |
| FACT-EVOLVE (imp 8) | died @9 control / @4 distractor | **survives all 20 in BOTH arms; evolved V2 form alive at cycle 20** (stale 0 — evolution-noted form throughout) |
| OQ-STABLE first_seen resets | 8 control / 12 distractor | **0 in both arms**; seed stamp 2026-05-02 intact through 20 cycles — #128 overdue boost restored |
| growth | — | flat: 3-4 questions, 10-decision plateau from cycle 5; no accumulation flood; carried_from steady 0-3/cycle |
| FILLER-1 (imp 2, distractor) | died @5 | died @3 — cap pressure under 10 competing decisions squeezes the lightest carried item out (working as designed) |

**Mechanism, visible in provenance:** `carried_from` counts fluctuate (0→3→0 control; steady 1 distractor) — carry catches serializer drops, then once the briefing keeps an item in context the serializer re-emits it natively and dedup hands the old birth stamp to the new wording. Safety net, not crutch.

**Honest read:** one acceptance row was mis-premised — DEC-STABLE's verbatim-quote integrity still erodes (0.44 control / 0.24 distractor) because the serializer never DROPS it, it rewords it, and dedup's new-wins rule keeps the fresh text by design. Carry fixes LOSS and STAMP CHURN; rewording erosion of re-discussed items is scoped v2 (candidate: verbatim-trust items prefer carried text). Beliefs remain unprotected (v1 exclusion, resets 6). Same caveats as run-01: n=1 seed, synthetic sessions, haiku.

**Pointers:** PR #135, `plugin/daimon_briefing/carry.py`, `experiments/multicycle/results/run-02-carry/`, vault `Daimon/decisions/Deterministic Carry - 33 Phase 2`, run-01 entry below (the evidence base), issue #33.

---

## 2026-07-02 — Q-STALE multicycle run-01: cross-session failure mode is LOSS, not staleness — LLM-mediated carry loses whole items even from lossless input

**What happened:** Built and ran the multicycle degradation instrument (`experiments/multicycle/`, PR #134): one synthetic seed checkpoint with six nonce-graded items cycled through the REAL pipeline (briefing render → synthetic session → `serialize_strict` D-011 → `store.write_checkpoint`) for 20 cycles across three arms — `control` (briefing carry, quiet sessions), `distractor` (+ unrelated work per cycle, production 3000-token briefing budget), `carry` (raw checkpoint JSON as context — lossless-carry upper bound, the #33 proxy). Serializer model haiku via gateway; 147,860 estimated tokens total (25% of the 600K abort budget); grading fully deterministic (substring survival, difflib integrity, first_seen persistence), no LLM judges. One mid-run kill: the first launch ran decay scoring on wall clock against simulated future stamps (user-caught; fixed in 35758c8, scar candidate `simulated-clock-must-thread-into-every-now-consumer`, invalidated cycles deleted).

**Results (survival = last cycle alive of 20; integrity = final difflib vs seed text):**

| item (importance) | control | distractor | carry |
|---|---|---|---|
| OQ-STABLE (7) | alive, 1.0, **8 fs-resets** | alive, 0.85, **12 fs-resets** | alive, 1.0, **0 resets** |
| FACT-EVOLVE (8) | died @9 | died @4, resurrected @5 by re-mention, died @8 | died @13 |
| DEC-STABLE (7, verbatim+quote) | alive, eroded to 0.41 | alive, 0.53 | died @13, **1.0 until death** |
| BELIEF-STABLE (6) | alive, 0.99 | alive, 0.99 | died @20 |
| FILLER-1 (2) / FILLER-2 (3) | died @9 / alive 0.54 | died @5 / died @1 | died @5 / died @20 |

**Findings:**
1. **True staleness = 0/60 graded cycles, all arms.** The serializer never asserted an old value as current — it records the evolution-noted form ("revised from V1 to V2") or loses the item entirely. Q-STALE's original cross-session fear does not reproduce; D-011 needs no prefer-latest rule. (First grader marked the evolution-noted form stale — metric artifact, same class as the scale test's verbose-answer trap; fixed in c59905a, true-stale = V1 present WITHOUT V2.)
2. **Loss is the failure mode, and LLM re-emission is the telephone.** An importance-8 decision died in 4 cycles under realistic load, 9 cycles in quiet sessions — and still died (cycle 13) when the FULL checkpoint JSON was in context. Lossless input does not survive LLM-mediated carry.
3. **Carry mode determines failure shape.** Briefing carry = graceful erosion (items survive as reworded shadows; verbatim quote fidelity fell to 0.41). JSON carry = all-or-nothing (perfect 1.0 fidelity and zero first_seen churn while alive, then whole-item loss).
4. **first_seen exact-text carry-over breaks constantly under briefing carry** (8-12 resets/20 cycles on the star item) — every reset restarts #78 decay age, so the #128 overdue boost effectively never fires across real multi-session horizons. The carry arm's 0-1 resets prove structured input preserves stamps.
5. **The distractor arm's cycle-5 resurrection is pull-based memory measured:** conversation re-mention restored the dead fact; memory alone never did.

**Verdict mapping (the four decisions this run was built to inform):**
- **#33 merged history: PROMOTED, with deterministic-merge as a hard requirement.** LLM-mediated merge is disproven (finding 2); deterministic carry is validated as achievable (finding 3's fidelity behavior). Serializer handles new session content; unresolved open loops/decisions carry by code.
- **Serializer contract: needs a don't-lose rule, not a prefer-latest rule** (finding 1 vs finding 2) — moot if deterministic carry lands first.
- **#78 decay tuning: secondary.** Importance-ordering was roughly right when items died by decay (imp-2 first), but importance does NOT protect against carry loss (imp-8 died before imp-3 filler in control). No constant-tuning until carry is fixed.
- **#126 first_seen: exact-text carry is too fragile for briefing-mediated chains** (finding 4); rides the deterministic-carry fix.

**The honest read:** n=1 seed world, synthetic ~10-message sessions, haiku serializer — real sessions use a stronger model and richer re-mention patterns, so absolute cycle counts are pessimistic bounds; direction and mechanism are the findings. Loss stochasticity is real (different items died in different arms — survival is not a clean priority order). This reproduces the memory-backend scale test's FUTURE-HURT verdict (prose 0.955→0.364 under merge passes) on the real pipeline with the real serializer — the two instruments now agree from independent angles.

**Pointers:** `experiments/multicycle/` (instrument + README), `results/run-01/` (per-arm results.jsonl, summary.md, all 60 cached checkpoints), PR #134, vault `Daimon/progress/Q-STALE Multicycle Experiment Design`, `research/memory-backend/benchmark/results/scale-full/` (the prior), scar candidate `simulated-clock-must-thread-into-every-now-consumer`.

---

## 2026-06-13 — D-008 serialize-fidelity fix: rules work (checkpoint inspection); full RR/FMR re-judge deferred on gateway throughput

**What happened:** Shipped the D-008 serialize prompt (three fidelity rules — final-state resolution, distinct-items, exact-quantities — + a cross-chunk merge reconciliation rule + `PROMPT_VERSION` provenance; PR pending). Validated by **checkpoint inspection** rather than the full re-judge: re-serialized the corpus and compared D-008 checkpoints against the archived D-007 baselines (both kimi-gen → clean prompt-only delta, model held constant). Full RR/FMR/staleness re-judge was deferred — see the gateway finding below.

**The fidelity rules demonstrably fire, without overcorrecting:**
- **Distinct-items (rule 13):** H3 D-007→D-008 captured the previously-omitted gt7 monetization decision and four distinct naming-candidate rejections (specifics redacted — private project) that D-007 conflated. S1 jumped 32→45 decisions — all +13 verbatim-trust captures D-007 dropped (real fixes with exact commit SHAs 62588ff/047998c/7857c95, OIDC slugs, config keys), not invention.
- **Exact-identifiers (rule 14):** commit hashes preserved verbatim (H3: both 2e1d78b and 1656dfd). The earlier "15 vs 17 docs" concern was GT oversimplification — docs landed in two batches (15 + 3 new); D-008 captured both precisely, *more* accurate than the ground truth.
- **Final-state resolution (rule 12):** H3 open_q 13→8 with promotions carrying careful "pending user ratification" hedges (Beehiiv, Bitwarden) — it did NOT invent resolutions. **The overcorrection safety check passes:** promotions are conservative, new items verbatim-grounded, zero fabrication across H3/S1/H4.
- **Honest miss:** H3 gt10 (Phase-1 manual-only decision) still not captured as a decision — a transcript-phrasing/ratification issue the final-state rule doesn't key on, not a rule failure. And H4's proposed-but-unapplied "[Fix]" items (inferred trust) persist — that's the orthogonal question→action corruption (findings/03), unaffected by D-008.

**The gateway finding (why full re-judge was deferred — and a model recommendation):** the kimi-k2.6 generation lane is the bottleneck and it's structural, not tunable. kimi is a *reasoning* model doing *mechanical JSON extraction*: 94–460s/call, intermittent empty-200 (reasoning-budget exhaustion → `total_tokens=0`), and gateway 429s under concurrent burst. Measured alternatives on the gateway: **`claude-haiku-4-5-via-meridian` ≈ 2× faster than kimi and reliable** (80–130s/call, clean JSON) — viable for generation, but collides with the haiku judge (would need the judge moved to sonnet). `claude-sonnet-4-6-via-meridian` is also a reasoning model → slow like kimi. `gpt-5-mini-via-cliproxy` → **HTTP 401** (cliproxy OAuth not wired for our key). The real cost is **output-token volume** (8–16k completion tokens/call at ~100–130 tok/s on meridian), multiplied by chunking + hierarchical merge — no model choice gives more than ~2×. *Decision:* future runs should move generation off kimi to haiku-4-5-via-meridian (judge → sonnet to avoid self-judging); realizes FR #16 / the 815s-ceiling scar's "fast non-reasoning model" fix. Full RR/FMR re-judge of D-008 is deferred until that lane is in place.

**Pointers:** `serializer.py` (SERIALIZE_SYS rules 12-14, MERGE_SYS rule 11, `PROMPT_VERSION`), `01b-serialize-d007.md` / `01c-merge-checkpoints.md` (doc-of-record parity + drift-guard tests), `runs/<id>/d007-serialize-baseline/` (archived D-007 checkpoints for the comparison), vault `Daimon/decisions/Serialize Fidelity Fix D-008` + `Daimon/progress/D-008 Implementation Plan`, scars `gateway-815s-request-ceiling` / `gateway-response-cache-pins-bad-responses` (both fired again this run, mitigations held).

---

## 2026-06-12 — v2 full-carry-over reconstruct: n=4 verdict flips back to PASS (71.0% RR) — but only the dense session moved

**What happened:** Re-ran reconstruction on H1–H4 from their existing checkpoints with the v2 02-reconstruct prompt (full carry-over, no summarizing — commit a3615aa), then re-judged with the byte-identical protocol from the v1 holdout run (scar 0004: FMR deltas only comparable under identical judge prompts). Reconstruction model kimi-k2.6, judge `claude-haiku-4-5-via-meridian`, all via the gateway. Every miss adversarially verified; every overturn claim, grounding dispute, and stale flag hand-reviewed against the transcript by the orchestrating session.

**Results (audited, vs v1 in parens):**

| session | RR | FMR | recon lines |
|---|---|---|---|
| H1 | **62.5%** (33.9) | 3.8% (4.0) | 176 (68) |
| H2 | 91.3% (91.3) | 4.2% (0.0) | 50 (47) |
| H3 | **57.9%** (68.4) | 0.0% (0.0) | 62 (61) |
| H4 | 72.2% (72.2) | 8.7% (0.0) | 38 (38) |
| n=4 aggregate | **71.0%** (66.5) | 4.2% (1.0) | — |

**Verdict: PASS — BUILD** (mean RR ≥ 70%, mean FMR ≤ 10%). D-006 still emphatic: verbatim 74.1% vs inferred 0.0%.

**The honest read — the fix only moved the session it was designed for:** H1's reconstruction expanded 68 → 176 lines and its RR nearly doubled (33.9 → 62.5), confirming the v1 diagnosis that H1's loss was reconstruct-side compression. But H2–H4 reconstructions barely changed size (47→50, 61→62, 38→38), H2/H4 RR is identical to v1, and **H3 regressed 68.4 → 57.9** (−4 items: gt7 isn't in the checkpoint at all — a v1 judge over-credit on a serialize-side loss; gt12, the docs/01–17 push at commit 2e1d78b, IS in the checkpoint and v2 recon dropped it — carry-over is not airtight). The pass is **marginal**: 71.0 vs a 70.0 bar; a single hand-review judgment call (H2 gt1/gt12 were both accepted overturns) swings the verdict. Treat this as "reconstruction bottleneck confirmed and largely fixed for dense checkpoints," not "pipeline validated."

**Confabulations persist through re-reconstruction:** the v1 H1 false memory (recon asserts the leaked LiteLLM key "was deleted"; transcript L4634: "you haven't deleted the old leaked key in LiteLLM admin yet") reappeared verbatim-equivalent in the v2 reconstruction — carry-over carries false memories exactly as faithfully as true ones. The judge's 25-claim sample missed it; it was hand-added to the claim set (H1 FMR 1/26). Two NEW completed-action confabulations surfaced in H4 (c4: "shortened port name" — proposed, never applied; c10: "ConfigMap was corrected" — interrupted by the PVC discovery, never confirmed) and one causality inversion in H2 (r5: claims KLAP retained creds AFTER the factory reset; transcript L1828 shows the reset RESOLVED it). FMR rose 1.0 → 4.2% — still well under the bar, but the question→action corruption family (findings/03) now has five specimens.

**Judge-quality notes (both scars fired again):** staleness judge flagged 5 items (H2 gt9, H4 gt4/5/7/10) — ALL five were wrong-dimension judge errors (each recon carries the final in-session state; `judge-grades-wrong-dimension` candidate now 10/10 false flags across v1+v2). Recall verifier produced 8 overturn claims; hand review accepted 5, rejected 3 (wrong-item evidence on H1 gt24, partial-component evidence on gt32, and gt48 the persisting confabulation). Grounding grep-verify disputes: 1 of 4 was a judge error (H4 c12 — user confirmed the SLZB address at L732/736).

**Staleness (complete):** mean 1.4% over n=4 — ADVISORY PASS (bar ≤10%). H1: 2/35 = 5.7% — gt1 (3-channel routing push rendered "approved to proceed"; transcript ends "this is done already") and gt34 (signing config implemented + merged in-session at commit 720051d; recon renders it as future plan). Both are completed→pending demotions, the same epistemic family as the confabulations above but in the opposite direction. The staleness judge flagged 3 (gt34/gt40/gt49); hand review upheld 1, overruled 2 — its first real catch across both runs (`judge-grades-wrong-dimension` tally now 12 false flags, 1 real, across v1+v2; gt1 reached scoring only via hand-graded overturns the judge never saw).

**Open holes:** H3's serializer decision→question demotion regression remains unfixed and is now H3's dominant loss term — its remaining misses are serialize-side, out of reach of any reconstruct prompt.

**Pointers:** `runs/H*/haiku-judge/` (v2 judge artifacts with hand-review audit trails), `runs/H*/session-H*.holdout-v2.score.json`, `prompts/02-reconstruct.md` (v2), design ADR in the vault (Daimon/decisions/Recon Density Carryover v2 Prompt), findings/03 (owes the new H2/H4 specimens).

## 2026-06-12 — Holdout completed at n=4: verdict flips to PIVOT (66.5% RR) — loss localized to RECONSTRUCT, not serialize

**What happened:** H1 (the 6,631-line hierarchical-merge session) got its judge pass, completing the un-annotated holdout at n=4. Judge = `claude-haiku-4-5` via the gateway's meridian lane (`claude-haiku-4-5-via-meridian` — same underlying model as the H2–H4 judges, different transport; first judging run through LiteLLM instead of Claude Code subagents). Full protocol held: recall vs GT (56 items, batches of 8), grounding with grep-verify (scar 0004), staleness pass, and EVERY miss/dispute/stale-flag adversarially verified then hand-reviewed.

**Results (audited):**

| session | RR | FMR | staleness |
|---|---|---|---|
| H1 | **33.9%** | 4.0% (1 real confab of 25 claims) | 5.3% |
| n=4 aggregate | **66.5%** | 1.0% | 8.0% adv. PASS |

**Verdict: PIVOT** — mean RR falls below the 70% bar (was 77.3% at n=3). D-006 still emphatic: verbatim 69.4% vs inferred 0.0%.

**The decisive finding — reconstruction is the bottleneck, not serialization:** 36 of H1's 37 upheld recall misses ARE present in the 156-item checkpoint (key-term probe). The serializer + hierarchical merge preserved the content; the 02-reconstruct step compressed 47KB of checkpoint into a 68-line reconstruction and silently dropped the session's early/mid-phase decisions (gateway-first architecture, app scaffolding, infra fixes). RR on dense sessions currently measures reconstruction compression loss, not memory fidelity. That's actionable: scale reconstruction output with checkpoint density, or stop reconstructing through a fixed-size template.

**Judge-quality notes (scar 0004 fired again, twice):** (1) recall judge under-credited — 5 of 39 misses claimed overturnable by the adversarial verifier, of which hand-review accepted only 2 (gt1, gt50); the verifier itself cited wrong-item evidence on 3. (2) The staleness judge graded *topic-uncertainty* instead of *superseded-state pinning* — all 5 of its stale flags were judge errors (each item's GT matches transcript end-state), while it missed the one real stale pin (gt1: recon renders the 3-channel routing push as "uncertain whether merged"; the user confirmed it done in-session). (3) Grounding: 3 of 4 disputed claims were judge errors; the 1 real confabulation is recon asserting the leaked LiteLLM key "was deleted" when the transcript's last word is "you haven't deleted the old leaked key yet" (L4634) — an open question rendered as completed action, the mirror image of H3's decision→question demotion (epistemic-state corruption cuts both ways).

**Honest qualifiers:** H1's GT is much denser (56 items vs 18–38), its serialize used K=2 hierarchical merge while H2–H4 were single-shot, and its reconstruction came from a checkpoint 2–3× denser than the others — the comparison is between pipeline variants, not identical paths. Judge transport differed (gateway vs subagents), same model and temperature 0.

**Pointers:** `runs/H1/haiku-judge/` (recall, miss-verification with hand-review, grounding, staleness — all carry audit trails), `runs/H1/session-H1.holdout.score.json`, `scoring/score.py` n=4 output, prompts/02-reconstruct.md (owes the density fix), findings/03 (owes the question→action corruption twin of decision-demotion).

## 2026-06-10 — 2-cycle degradation test: PASS — FMR does NOT compound (kill-tier risk not realized)

**What happened:** Ran the long-owed 2-cycle CRP degradation test (VALIDATION.md Track A) on ALL 5 sessions, not just one — cycle-2 inputs are small (cycle-1 reconstructions, all single-pass), so n=5 cost ~30 min gateway time. New `--cycle2` mode in `rerun.py` (9 TDD tests; feeds `runs/<id>/rerun/reconstruction.md` back through the shipped serializer as a 1-message transcript, writes `rerun-c2/`; required scoped `DAIMON_MIN_MESSAGES=1` override — `serialize_strict` rejects 1-message inputs by default). Judged with the same Haiku-subagent protocol as the n=5 rerun.

**Results (hand-verified disputes):**

| | cycle 1 | cycle 2 | bar |
|---|---|---|---|
| mean RR | 91.9% | 88.4% | — |
| mean FMR | 0.7% | **0.6%** | cycle-2 ≤ 20% → **PASS** |
| staleness | 3.0% | 1.5% | ≤10% advisory ✅ |

The doubling hypothesis is REFUTED at 2 cycles: the single verified-real false memory (S1 pinned Argo CD OIDC slug `argocd`; transcript shows it 404'd and final was `argocd-oidc`) persisted UNAMPLIFIED from cycle 1 into cycle 2 — no new confabulation appeared in any session. Recall decays ~3.5 pts/cycle: lossy compression compounds as omission (as predicted), not invention. Per-session cycle-2 RR: S1 90.0, S2 89.7, S3 77.8, S4 89.7, S5 94.7.

**Measurement lesson (recorded for future judge runs):** single-judge per-claim FMR at this granularity is noise-dominated. One Haiku judge graded S4's commit `d444109` ungrounded while another graded it grounded; the strict S1 judge marked the entire late-transcript forward-auth block false (didn't reach the tail). Every disputed claim was hand-verified by grep against the transcript: ALL apparent cycle-2 confabulations except the argocd slug were judge errors. Mitigation that worked: instruct judges to grep-verify key terms BEFORE grading false (S3's judge, so instructed, produced 50/50 clean grades). Candidate scar material.

**Status:** Slice 2 part 2 now lacks only the un-annotated holdout. Multi-cycle drift — the MemoryArena kill-tier risk (findings/06 §B) — is bounded at 2 cycles on real data: omission-compounding yes, confabulation-compounding no.

**Pointers:** `experiments/track-a/rerun.py` (--cycle2), `runs/*/rerun-c2/` + `runs/*/session-*.c2.score.json` (local, git-ignored), VALIDATION.md Track A cycle-degradation, findings/03.

---

## 2026-06-11 — UN-ANNOTATED HOLDOUT: PASS at 77.3% RR — contamination confound measured, full-go gate cleared with caveats

**What happened:** The gating test from the team consult (archaeologist + skeptic both named it decisive) ran: 4 real sessions with ZERO `Decision noted:` markers, exported from other projects (H1 hermes/k3s investigation 6,631 lines; H2 network-migration debugging 1,909; H3 content-pipeline planning 1,182; H4 zigbee debugging 809). Ground truth authored BLIND by Sonnet annotator agents (transcript+template only, never saw reconstructions — caveat: agent-authored GT, not human; blindness preserved). Shipped chunked serializer; Haiku judges with the grep-verify protocol (scar 0004); every recall miss and disputed claim adversarially verified (4 recall judge-errors overturned, 19 misses upheld; all 4 apparent H4 confabulations were judge errors — judge missed a late-transcript user override, scar 0004's exact failure mode again).

**Results (verified):**

| session | type | RR | FMR | staleness |
|---|---|---|---|---|
| H2 | debugging/infra | 91.3% | 0% | 21.1% (4 superseded-diagnosis pins) |
| H3 | planning | 68.4% | 0% | 0% |
| H4 | debugging | 72.2% | 0% | 7.7% |
| H1 | investigation, 6.6k lines | **DNF** — 6-chunk merge timed out at 900s AND 1800s (issue #28) | — | — |

Aggregate (n=3 scored): **RR 77.3% / FMR 0.0% / staleness 8.9% — formal verdict PASS**. The contamination confound is now MEASURED: 91.9% on the annotated corpus → 77.3% un-annotated (−14.6 pts). Above the 70% gate, well above the feared 55% floor.

**Three new findings:**
1. **Decision→question regression (H3):** the serializer doesn't just omit unmarked decisions — it DEMOTES them: Beehiiv chosen → reconstructed as "Beehiiv vs Buttondown open question"; locked pilot topics → "topics unknown". Planning sessions where decisions live in conversational ratifications ("Yes" covering a packed list) are the weak case. New failure mode name for findings/03.
2. **D-006 emphatic on holdout:** verbatim RR 80.8% vs inferred RR 0.0%. Inferred (unquotable) items did not survive AT ALL on un-annotated sessions. Extractive pinning isn't just better — it is the only thing that survives without annotation scaffolding.
3. **Merge ceiling (issue #28):** 6-chunk single-shot merge is unserializable on kimi-class latency at any client timeout (gateway likely caps server-side). Long sessions — chunking's whole point — hit the merge wall. Hierarchical merge required.

**Also today:** status subcommand (PR #24) + DAIMON_LLM_TEMPERATURE (PR #25, upstream began rejecting non-1 temperatures — broke ALL serializes including a real fabcap /exit, manually recovered) + relative-path status fix; FRs #26 (self-healing retry), #27 (serialize.log first-class ledger), #28 (hierarchical merge). The status command caught the production failure within minutes of existing.

**Full-go read:** the skeptic's FIX-FIRST condition is satisfied — holdout clears the gate. The honest qualifiers: n=3, single annotator-model GT, planning sessions sit at the edge, staleness runs 3× higher off-corpus, and the longest sessions can't serialize until #28. Sharpest next derisks: hierarchical merge, decision→question regression in the serialize prompt, Q-HERMES-COMMUNITY (still 30 unspent minutes).

**Pointers:** runs/H*/ (local), `sessions/H*.txt`, issue #28, scar 0004 (fired twice more today, mitigation held), findings/03 (owes the decision-demotion section).

---

## 2026-06-10 — Slice 2 part 2: n=5 rerun PASSES both gates (91.9% RR / 0.7% FMR) — Q-RECALL closed at scale

**What happened:** Re-validated the SHIPPED chunked serializer (`serialize_strict`: D-007 prompt + Q-STALE MERGE_SYS + concurrency) on all 5 sessions via the new rerun harness (`experiments/track-a/rerun.py`), then re-judged BOTH the rerun and the original baselines with a single consistent judge so the comparison is same-judge throughout. Judge = Claude Code subagents on Haiku (3 passes/session: recall vs GT; grounding vs transcript; staleness vs transcript — scar 0001 honored, answer key never shown to grounding/staleness judges).

**Results (haiku judge, both arms):**

| arm | mean RR | mean FMR | staleness | verdict |
|---|---|---|---|---|
| baseline single-pass | 65.5% | 0.0% | 0.0% | PIVOT (RR < 70%) |
| **shipped chunked** | **91.9%** | **0.7%** | **3.0%** | **PASS — BUILD** |

Per-session RR baseline→rerun: S1 53.3→100%, S2 51.7→82.8%, S3 77.8→88.9%, S4 55.2→93.1%, S5 89.5→94.7%. The long-session cliff is gone — the three sessions that failed Round 1 (S1/S2/S4, all >2,100 lines) now clear the bar by 12+ points. Every session passes both gates individually.

**The instruments detect:** S1's grounding judge caught a real false memory — reconstruction pinned Argo CD OIDC slug `argocd`, but the transcript shows that slug 404'd and was superseded by `argocd-oidc` (FMR 3.6% on S1; the matching GT item also flagged stale). S2 rerun staleness 8.3% (2 pinned "verification pending" items). Mean staleness 3.0%, under the ≤10% advisory bar — the MERGE_SYS supersession rule mostly works, and the new staleness metric (PR #17) measures what FMR is blind to.

**Judge methodology change:** gateway LLM judges replaced with Claude subagents (Haiku) after the kimi upstream threw 502s and a full-transcript staleness call wedged past `DAIMON_TIMEOUT=420`. Caveats recorded: (a) Haiku grounding leans lenient — baseline FMR 0.0% vs Round-1 human grading which found real false memories; same-judge deltas are sound, absolute FMR is soft; (b) S1 recall 100% spot-checked by hand on the two hardest items (literal password value, nginx snippet story) — genuinely present.

**Timeout finding:** the 3-chunk MERGE call is the biggest call in the pipeline (3 full chunk-checkpoints in, full checkpoint out) and scales with chunk count — S1's merge timed out 3× at 420s on kimi; succeeded at `DAIMON_TIMEOUT=900`. The LOGBOOK's prior 420s guidance came from a single-pass call and is insufficient for long-session merges on slow models. Strengthens FR #16 (claude-cli backend, haiku default).

**Still owed (Slice 2 part 2 remainder):** 2-cycle degradation test, holdout on un-annotated sessions, per-project routing shipped this branch (untested live).

**Pointers:** `experiments/track-a/rerun.py`, `runs/*/session-*.rerun.score.json` + `runs/*/session-*.haiku-base.score.json` + `runs/*/haiku-judge/` (local, git-ignored), PR #17 (staleness metric), `scoring/score.py`, FR #16.

---

## 2026-06-10 — Capture loop closed (PRs #12, #13) + second live dogfood: timeout root-caused

**What happened:** The capture→inject loop is now fully wired in Claude Code. PR #12: `SessionEnd` hook spawns `daimon-briefing serialize <transcript_path>` detached (`/exit` returns instantly); `transcript.from_file` gained Claude Code `.jsonl` parsing (skips sidechain/meta/tool noise). PR #11's gap caught live first: a session start briefed from a 57-min-old checkpoint minutes after an `/exit`. PR #13: every config var falls back to `~/.daimon/env` (hooks inherit host-process env, not shell profile — first live SessionEnd fire failed on missing credentials) + CLI serialize errors name their cause.

**Second live dogfood (the `/exit` after PR #13):** hook fired, detached spawn survived, creds loaded — serialize still failed. Root cause measured: tiny LLM call instant, full checkpoint generation **248s** on kimi/LiteLLM; default `DAIMON_TIMEOUT=120` socket timeout × 3 retries = the 7-minute silent failure. Fix: `DAIMON_TIMEOUT=420` in `~/.daimon/env`. Manual rerun wrote the checkpoint (8 decisions, 7 open questions); the rendered briefing surfaced PR #12 verbatim — self-referential dogfood closed, one manual assist short of fully automatic. True unassisted end-to-end = next `/exit`.

**Diagnostic gap promoted to Slice 2:** `serializer.serialize()` swallows all failure causes into `None`; the timeout took instrumented reproduction to find instead of one log line. Slice 2 adds named failure reasons (ChatError vs JSON vs schema), same class as the PR #13 CLI error split. Also scarred: `uv tool install --force` reuses the cached wheel when the version is unchanged (candidate 0003) — verification ran stale code twice.

**Pointers:** PRs #12 #13, `hook/daimon-session-end.py`, `plugin/daimon_briefing/config.py`, `docs/MVP-DREAM-BRIEFING.md` (Slice 2), `.scars/candidates/0003-*.md`, `~/.daimon/logs/serialize.log`.

---

## 2026-06-09 — Slice 1 built + first live dogfood: cliff reproduced, staleness discovered

**What happened:** Slice 1 shipped to PR #10 (`plugin/` — hermes plugin, 71 tests, strict TDD, review-hardened: secrets suppressed from LLM error paths, atomic store writes, path containment, deadline timeout budget). Then the first **live dogfood**: exported *this very session* (~250 turns) via `claude_sessions.py`, serialized with the installed CLI, read the briefing against lived ground truth.

**Results:**
- **Recall ~58%** — the long-session ~55% prediction reproduced on first contact. ~5/12 major decisions dropped. Slice 2 (chunking) justified on live data.
- Everything surfaced was true, trust-marked, verify-section correct (led with the open PR). Omission, not invention — again.
- **NEW failure mode: stale evidence pinning.** Briefing cited the superseded mid-session probe numbers (broken-judge 93.1%/36.8%) as verbatim evidence for the chunking decision instead of the corrected finals (89.7%/6.7%). Quote is genuinely in the transcript → FMR/grounding judges are blind to it. Intra-session supersession = D-005's validity-interval problem at checkpoint scale. New metric needed: **staleness rate**. New Slice-2 rule: merge prefers latest state of evolving facts. → `Q-STALE`.

**Also:** hermes API reality-check during build corrected 3 MVP-doc claims (pre_llm_call signature, SessionDB instantiation, derived skill namespace). PR #9 (scar 0002) merged. No-attribution rule extended to PR bodies.

**Pointers:** `plugin/`, PR #10, `findings/03` (dogfood section), `OPEN-QUESTIONS.md` (Q-STALE), `docs/MVP-DREAM-BRIEFING.md §4.4`.

---

## 2026-06-09 — D-007 probe: CHUNKING REQUIRED (architectural) — Q-RECALL answered

**What happened:** Ran the three-arm serializer probe on S2 (2,187 lines, the worst long session; `experiments/track-a/probe_d007.py`, kimi-k2.6). Final (transcript-grounded judge): baseline 37.9% RR / 4.3% FMR; D-007 prompt single-pass 58.6% / 4.2%; **D-007 + chunked multi-pass (800/100) + merge 89.7% / 6.7% — clears both bars.** Recall conclusion replicated across 3 judge runs (armC 79.3–93.1%).

**Measurement scar en route:** the first judge graded `grounded` against the GT list → surplus-but-true detail scored as confabulation (armC "FMR 41%"). Fixed to ground against the **transcript**; FMR collapsed ≤6.7% everywhere. Recorded as the repo's first authored scar (`.scars/0001`). A concurrent-run race (two probes, same dirs) also burned ~116k duplicate tokens — accidental replication, conclusion held.

**Decisions:** D-007 **resolved** — richer prompt adopted, but architecture is chunk→extract→merge for long transcripts; Slice 1 serializer design settled (`MVP-DREAM-BRIEFING.md §4`). Q-RECALL 🟢.

**Still owed:** 5-session re-score with chunked serializer, un-annotated holdout, 2-cycle test, human verification of judge scores.

**Also:** SCAR (sibling project) passed its gates 0.1–0.3 same day; daimon `.scars/` graph seeded + first scar authored; convergence noted in MVP doc §7 (one session-end pass, two artifacts — post-MVP).

**Pointers:** `findings/03` (probe section), `DECISIONS.md#d-007`, `OPEN-QUESTIONS.md` (Q-RECALL), `.scars/0001`, `docs/MVP-DREAM-BRIEFING.md §4`.

---

## 2026-06-09 — D-008 approved · Q-5NAMES resolved · dream-briefing MVP scoped

**What happened:** User approved **D-008** (pivot: standalone product → dream-briefing layer on Honcho + upstream contributions to Graphiti); PR #7 merged. **Q-5NAMES answered**: user's "anyone in AI/tech uses tools like these" validates the *category*, not the delta (the classic Mom Test miss for a standalone product) — but under skill framing the build cost is low and distribution rides Hermes, so category demand suffices; resolution = **community-facing skill framing, success metric = post-ship adoption**, no market slides. Then scoped the MVP: `docs/MVP-DREAM-BRIEFING.md` (provenance-tagged, VERIFIED/ASSUMED on every hermes/Honcho internals claim).

**Load-bearing integration findings (from hermes/Honcho source-doc audit):**
- hermes has real lifecycle hooks (`on_session_start/end/finalize`, `pre/post_llm_call`) — but session hooks don't carry the transcript, and `on_session_start` **cannot inject context** (return ignored). Briefing must be injected at the **first `pre_llm_call`** of the next session. *VERIFIED.*
- Full transcript is recoverable anyway: hermes persists sessions in `~/.hermes/state.db` (SQLite), `SessionDB.get_messages(session_id)`. Makes **Slice 1 (local-file checkpoint, zero Honcho) dogfoodable immediately**. *VERIFIED.*
- A pure SKILL.md skill has **no lifecycle activation** — the mechanism must live in hooks, the skill is the user-facing wrapper. Whether one bundle can ship both is the top packaging unknown. *ASSUMED.*

**Why it matters:** the project now has an approved North Star, a closed framing question, and a concrete, slice-1-dogfoodable MVP architecture grounded in read code/docs rather than assumption.

**Pointers:** `docs/MVP-DREAM-BRIEFING.md`, `DECISIONS.md#d-008` (Q-5NAMES folded in), `OPEN-QUESTIONS.md` (Q-5NAMES 🟢).

---

## 2026-06-09 — Track B VERDICT: the differentiator is already shipped (Honcho + Graphiti)

**What happened:** Ran two adversarial capability audits (Honcho, Graphiti) to answer Q-HERMES-DELTA before any serializer Round 2 (the deep-dive critique's #1 sequencing point). Result in `findings/07`.

**Finding:** Daimon's "epistemic graph" differentiator is already in production:
- **Honcho** (AGPL-3.0, ~5k★, in hermes-agent) covers belief extraction, cross-session contradiction *reconciliation* ("reconciles them instead of just accumulating"), belief-evolution, user modeling, and a Dialectic query API.
- **Graphiti** (Apache-2.0, ~27k★, the Zep engine) ships **D-005 verbatim** — temporal validity intervals + overlap-gated contradiction in `resolve_edge_contradictions`. D-005 as written is a reimplementation.
- ~7 of ~9 epistemic-graph/memory features covered. Kill trigger met.

**What survives (the kernel):** the **CRP dream-briefing UX** (session-start "while you were away" artifact — neither incumbent ships it; demonstrated valuable live this very session when the agent lost track of a PR merge), the **initiative taxonomy**, a **Claimify extraction gate** (absent in Graphiti — a contribution), and semantic evolution-vs-contradiction classification (hard, unproven).

**Decisions:** D-005 novelty **retracted** (depend on Graphiti, don't rebuild). D-008 **proposed** (pivot standalone-product → dream-briefing layer on Honcho + upstream contributions) — flagged as the user's call, not the agent's.

**Also (same session):** corrected the Track A overclaim — "confabulation REFUTED" → "no single-cycle confab; multi-cycle untested (cycle test skipped)"; recall gap is **length-driven** (cliff ~1,400 lines), not messiness; named confounds (contamination, single-model, no provenance). See `findings/03`.

**Pointers:** `findings/07`, `findings/03` (corrected), `DECISIONS.md#d-005` (retracted) / `#d-008` (proposed), `OPEN-QUESTIONS.md` (Q-HERMES-DELTA answered).

---

## 2026-06-09 — Track A VERDICT (n=5, SINGLE-CYCLE): PIVOT — no single-cycle confabulation; recall gap is length-driven

> **Corrected after a deep-dive critique (same day).** The first wording overclaimed twice — fixed below. Verdict stays PIVOT; the claims under it are narrower.

**What happened:** Scored all 5 real imported sessions (S1–S5) via kimi-k2.6. GT + scoring for S1–S4 by 4 independent adversarial subagents (blind GT-first); S5 user-verified-true. Aggregate: **mean RR 67.3%, mean FMR 1.0%, 0/5 ≥20% FMR → PIVOT.**

| Session | lines | RR | FMR |
|---|---|---|---|
| S5 | 1,313 | 84.2% | 0% |
| S3 | 1,361 | 88.9% | 0% |
| S2 | 2,186 | 51.7% | 0% |
| S4 | 2,340 | 55.2% | 0% |
| S1 | 3,134 | 56.7% | 4.8% |

**Finding 1 — no confabulation observed SINGLE-CYCLE (narrower than "refuted").** FMR 1.0%, max 4.8%; 4/5 had zero false memories under adversarial scoring. BUT the **2-cycle degradation test (`VALIDATION.md` Track A) was skipped** — and multi-cycle drift is the actual kill-tier risk (`06 §B`, MemoryArena: single-cycle passive recall does not predict multi-session reliability). Honest claim: *no single-cycle confabulation; multi-cycle untested.* "Refuted" retracted.

**Finding 2 — recall tracks LENGTH, not messiness.** Sharp cliff at ~1,400 lines: short (≤1,361)→~86%, long (≥2,186)→~55%. (Earlier "messiness" framing was wrong — S3 is longer than S4 yet scores higher; length is the clean correlate.) Suggests context/attention degradation, not a prompt deficiency. Failure mode = OMISSION not fabrication.

**Confounds (named):** (1) length≠messiness means `D-007` prompt-fix may NOT help long sessions — could need chunked multi-pass extraction (architecture change), untested. (2) Corpus contamination — all 5 carry inline `*Decision noted:*` lines, so the serializer was scored where its target was pre-extracted; true floor likely <55%. (3) Single-model generation — kimi did serialize+reconstruct, so FMR may be a kimi property (scoring was independent: Claude agents + human). (4) No provenance fields on score files.

**Interpretation:** Don't kill (no single-cycle confab), don't ship-as-is (recall too low/variable), don't blindly run Round 2 yet. **Sequencing correction: Track B (Honcho/Graphiti delta) comes FIRST** — it decides whether the serializer is even worth fixing. Then a corrected Round 2: S2 probe (prompt vs architectural) + un-annotated holdout + 2-cycle test + provenance fields.

**Pointers:** `track-a/runs/S1..S5/` (gitignored), `findings/03` (corrected writeup), `findings/07` (Track B, in progress), `DECISIONS.md#d-007`, `OPEN-QUESTIONS.md#q-confab`.

---

## 2026-06-09 — First REAL Track A signal (S5): RR 84.2%, FMR 0% (n=1, preliminary)

**What happened:** Ran the full CRP loop on a real imported session (S5 = homelab-common, a 1,300-line k3s/NFS debugging epic) via kimi-k2.6. Authored ground truth (19 items) from the transcript blind, then scored the reconstruction. Result: **RR 84.2%, FMR 0.0%, OR 15.8% → PASS**.

**The finding (the interesting part):** Zero confabulation — every reconstruction claim traced to the transcript; it even recovered real open loops the GT missed. All **11 decisions** recalled with exact specifics (VM id, IP suffix, NFS mount flags on 16 PVs, a pod memory limit — infra values redacted). The **only 3 misses were all GENERALIZED BELIEFS** ("SPOF not node count", "HA worth it for stateful not stateless", "drift cause only probable"). Pattern: **serialize→reconstruct preserves the concrete, loses the abstract.** This matches the evidence thesis — loss concentrates in the belief/epistemic layer (`findings/03`, `04`). The epistemic graph (the differentiator) is confirmed as the hard part, in miniature.

**Caveats (do NOT overclaim):** (1) n=1. Not the verdict — needs S1–S4. (2) GT authored by the agent, not yet user-verified. (3) **Favorable case:** S5's transcript is pre-loaded with `*Decision noted:*` lines + an end-of-session summary (artifact of the user's CLAUDE.md decision-logging habit), so it was pre-structured for easy checkpointing. 84% is plausibly an UPPER bound. A messy, un-logged session is the real stress test. (4) D-006 split underpowered (1 inferred item) — inconclusive.

**Process note:** `_smoke` (synthetic wiring toy) polluted the first aggregate (inflated mean to 92.1%). Use glob `runs/S*/...` to exclude it. Real number is S5 alone.

**Next:** user verifies S5 GT, labels S1–S4 (include ≥1 messy session), re-scores for the 5-session aggregate verdict.

**Pointers:** `track-a/runs/S5/` (gitignored), `findings/03-crp-reconstruction.md` (added early-signal note).

---

## 2026-06-09 — Claude Code session importer (real corpus unlocked)

**What happened:** Realized the Track A corpus is already on disk — Claude Code stores 838 real sessions as JSONL under `~/.claude/projects/`. Built `experiments/lib/claude_sessions.py` to convert them into clean transcripts (`--list-projects`, `--list --project`, `--session ... --out`). The corpus IS the target domain: real developer↔AI sessions, the messy hard case that actually stresses confabulation.

**Privacy design (two layers, verified):** (1) tool RESULTS are excluded entirely — env dumps / file reads / command output (where secrets live) never enter the transcript; this also focuses the transcript on the discussion. (2) text-borne secrets are regex-redacted. Verified against the daimon session that held the leaked OpenAI key: 0 full-length secrets survive, the key fragment is gone (it was in a tool result, dropped). A `sk-proj-…` *truncated* mention remains but is not a secret.

**Gotcha caught:** initial redaction grep looked like a leak (`sk-proj-` present) — turned out to be a harmless truncated reference, not the live key. Confirmed by checking for full-length (30+ char) secret tokens (0) and the key's distinctive body fragment (absent).

**Routing decision:** kimi-k2.6 (cloud) until local GPU lands Friday 2026-06-12, then revisit local model. Reversal cost low.

**Next:** user picks ~5 real sessions across projects → convert → write ground truth → `runner.py` → score. Avoid the daimon session (self-referential + held the key).

**Pointers:** `experiments/lib/claude_sessions.py`, `experiments/README.md` (import + privacy).

---

## 2026-06-09 — LiteLLM runner added; harness automatable

**What happened:** Wired the harnesses to the self-hosted LiteLLM gateway (in-cluster, reached via `kubectl port-forward`). Added `experiments/lib/llm.py` (dependency-free OpenAI-compatible client, stdlib urllib, secrets from env only), `track-a/runner.py` (automates serialize→reconstruct), and `track-c/extract.py` (automates Stage-1 extraction). Human steps (ground truth, labels, scoring) stay manual to preserve blind-by-design. Found + fixed a bug in the JSON extractor (didn't fall back to array bracket); added a unit test (5/5 pass).

**Why it matters:** Turns the harnesses from copy-paste into repeatable runs. The model is configurable via `LITELLM_MODEL`; `uv run lib/llm.py` lists available models.

**Security/privacy noted:** (1) An OpenAI key was exposed in the session transcript during env probing — user advised to rotate. (2) Routing real sessions through LiteLLM only stays "on your infra" if LiteLLM routes to a LOCAL model; cloud routing sends conversation data to the provider. Documented in `experiments/README.md`; prefer a local model for Track A/C runs to honor the privacy promise.

**Pointers:** `experiments/README.md` (LiteLLM setup + privacy), `experiments/lib/llm.py`, `track-a/runner.py`, `track-c/extract.py`.

---

## 2026-06-09 — Track C pipeline harness built

**What happened:** Built the Track C epistemic-graph harness in `research/experiments/track-c/`: a Claimify-style extraction prompt, a temporal-KG engine (`pipeline/run.py`) implementing validity intervals + supersession + interval-overlap flagging, a raw baseline arm for lift measurement, a scoring rubric, and a committed synthetic fixture. Stdlib only, `uv run`.

**Why it matters:** Tests the **D-005 architecture** (not raw NLI). The self-test demonstrates the whole thesis mechanically: on the synthetic fixture the **baseline misclassifies belief evolution as contradiction 100% of the time (EMR 100%)**, while the temporal pipeline gets it right (EMR 0%, FCR 0%) — a +100pt EMR lift, verdict PASS. That's D-005's claim, proven on data we control.

**Smoke test:** `uv run pipeline/run.py fixtures/evolution-pairs.json` → BEP 88.9%, pipeline FCR 0% / EMR 0% vs baseline FCR 33.3% / EMR 100%, VERDICT PASS.

**Blocked on:** Track A first (a CRP-confabulation kill makes the belief graph moot). Then the user's real sessions for the corpus (≥3 genuine belief evolutions). `corpus/` and `runs/` git-ignored.

**Pointers:** `research/experiments/track-c/README.md`, `findings/04`, `DECISIONS.md#d-005`.

---

## 2026-06-09 — Track A harness built (execution phase begins)

**What happened:** Stopped planning, started executing the gate. Built the runnable Track A confabulation harness in `research/experiments/track-a/`: cognitive-state JSON schema (with D-006 trust classes), serialize + reconstruct prompts, a human scoring rubric, and a stdlib `score.py` that computes RR/FMR/OR + a trust-class (D-006) split + the Build/Pivot/Kill verdict. Smoke-tested against the template — works.

**Why it matters:** Turns the 2-week plan's hardest track into "plug in 5 real sessions → get a number." The harness is blind-by-design (ground truth written before reconstruction is read) and also yields a free D-006 signal (does verbatim-pinned recall beat inferred?). This is validation tooling, not Phase-1 product code — the build gate still holds.

**Blocked on:** the user supplying 5 real past AI sessions (only they have them). `sessions/` and `runs/` are git-ignored — conversations stay local.

**Pointers:** `research/experiments/track-a/README.md` (runbook), `findings/03` (why), `docs/VALIDATION.md` (the gate).

---

## 2026-06-09 — Logbook established + algorithm deep-dive + evidence pinned

**What happened:**
- Set up this file-based research memory (`research/`) because Daimon (the persistent-memory system) doesn't exist yet. Structure mirrors Daimon's own memory layers — deliberate dogfooding.
- Walked through the actual algorithms behind the Daimon concept (retrieval, CRP serialization/reconstruction, epistemic graph, initiative). Distilled into `findings/00`–`06`.
- Ran 3 parallel deep-research agents to pin **primary-source-verified evidence** to each bet. Results in `findings/06-evidence-base.md`.
- **Revised `docs/VALIDATION.md` Track C** per D-005: it now tests the temporal-KG pipeline (Claimify extraction → validity intervals → flag only on interval overlap) with a raw-NLI baseline arm for lift, plus a new **Evolution Misclassification Rate (EMR)** metric (belief change must read as supersession, not contradiction). The old protocol would have just re-confirmed the known 24% NLI floor.

**Key evidence outcomes:**
- **Substrate is proven** (Generative Agents salience formula; MemGPT DMR 32%→92%; Mem0 +26% rel at 91% lower latency; HNSW O(log N)).
- **Confabulation risk confirmed but RE-SHAPED** — a correction to the original framing: the measured mechanism is *silent information loss* (recall −33%) + *no internal error signal* (ECE 0.45–0.75, AUROC ~62.7%) + *history-size degradation* (30–64%), NOT proven multiplicative fabrication. Lost-in-the-middle verified: mid-context state (56.0%) is *worse than no state* (56.1%).
- **Contradiction detection: the killer number** — NLI collapses from 80–93% benchmark to **23.94% precision on real dialogue**. BUT a viable architecture exists: Claimify extraction → temporal-KG validity intervals (Zep/Graphiti) → flag only on interval overlap. Converts a 24%-precision problem into a mechanical check.
- **Honest gap ledger** kept in `06 §D` — what's unmeasured (e.g. per-round summarization drift) is marked, not glossed.

**Why it matters:** Moved from "vision prose" to "named techniques with primary-source numbers, known failure modes, and measurable bars." Two design decisions changed as a result (edit-style memory, temporal-KG contradiction). One of my own earlier claims was corrected by the evidence — logged honestly in `findings/03`.

**Pointers:** `findings/00`–`06`, `OPEN-QUESTIONS.md` (statuses updated), `DECISIONS.md` (D-004 firmed).

---

## 2026-06-09 — Validation plan merged (PR #1)

**What happened:** Authored `docs/VALIDATION.md` — a 10-day go/no-go gate — and merged it to `main` (commit `c4e9330`). It defines three falsifiable validation tracks (A: CRP confabulation, B: Hermes/Honcho delta, C: epistemic-graph false-positive rate) with explicit kill bars and a Build/Pivot/Park/Kill decision table.

**Why it matters:** Establishes the discipline — *prove the load-bearing bet before building*. No Phase-1 code until the gate passes.

**Pointers:** [`../docs/VALIDATION.md`](../docs/VALIDATION.md), `DECISIONS.md#d-002`.

---

## 2026-06-09 — Hermes dependency identified

**What happened:** Confirmed "Hermes" in the docs = [github.com/NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT, v0.16.0, June 2026). It is NOT an internal Daily-Nerd tool. It already ships: persistent cross-session memory (full-text recall + LLM-summarized context + **Honcho** user modeling), a cron scheduler, a multi-platform gateway (Slack/Discord/Telegram/etc.), parallel isolated subagents, MCP, and serverless background execution (Modal/Daytona).

**Why it matters:** Reframes the central question from *"can we build the infra?"* (yes — Hermes has most of it) to *"what does Daimon add over raw Hermes + Honcho?"* Honcho likely overlaps the epistemic graph, Daimon's claimed differentiator. This is now a primary validation target (Track B).

**Pointers:** `findings/01-memory-retrieval.md`, `OPEN-QUESTIONS.md#q-hermes-delta`, `DECISIONS.md#d-001` (license follows from Hermes being MIT).

---

## 2026-06-09 — Three-agent adversarial stress-test of the concept

**What happened:** Ran three independent strategic reviews (skeptic, archaeologist, OSS-cartographer) over the concept docs, blind to each other. They converged on the same conclusions:
- The **Cognitive Resumption Protocol (CRP)** is the single load-bearing bet.
- The unflagged killer risk is **resurrection confabulation** (lossy summarize→reconstruct loop, confidently wrong over many sessions, no ground-truth check).
- The **epistemic graph** is the only feature no funded competitor (Letta, Mem0) has shipped — the real differentiator AND the riskiest piece.
- The "moat is glue, not weights" claim is weak; the real competitive threat is the user's existing stack assembling the same primitives.

**Why it matters:** Independent convergence on the same load-bearing risk is strong signal. It directly produced the validation plan's three tracks.

**Pointers:** `findings/03-crp-reconstruction.md`, `findings/04-epistemic-graph.md`.

---

## 2026-06-09 — Project artifacts created (pre-existing)

**What happened:** Initial commit (`bdeaa27`) with concept README + docs (PROBLEM, PITCH, RFC, ARCHITECTURE). RFC credited "Daimon (AI-conceived, human-refined)." Config hardcodes the author's own Slack handle and repos.

**Why it matters:** Establishes the baseline. Also a tell: the concept may be a personal tool with a category-defining pitch wrapped around it. The "product vs personal tool" honesty check (5-name test) is folded into the validation plan.

**Pointers:** [`../docs/`](../docs/).

---

## 2026-06-11 — Finding 08: CAP asset adoption proposed

**What happened:** CAP's M0.3 state benchmark ran a live Graphiti integration (the D-008 dependency) and surfaced its extraction-coverage gap with hard numbers: default ontology drops attribute-style facts entirely (scar #2 there), search() returns invalidated edges (scar #3), and even with CAP's custom_extraction_instructions mitigation, gold recall reaches only 0.786 vs 0.929 for CAP's extractor at equal budget (small-n: 4 scenarios — ranking unconfirmed until widened run). Finding 08 proposes adopting three CAP assets — the override/staleness harness (Q-STALE grader), the Graphiti scars, and the mitigation as upstream-PR ammunition.

**Why it matters:** D-008 made Graphiti's failure modes Daimon's failure modes; this is the first measured map of those failures. Also identifies the Q-STALE grader without building one from scratch (adaptation still owed: input adapter over checkpoint replay). Explicitly NOT a unification argument — convergence trigger unchanged.

**Pointers:** [`findings/08-cap-asset-adoption.md`](findings/08-cap-asset-adoption.md), Daily-Nerd/context-as-program PRs #2/#3.

---

## 2026-06-12 — H1 serialized: hierarchical merge validated, two gateway landmines found

**What happened:** Hierarchical merge (#28, PR #29) un-DNF'd H1 — the 6,631-line session that single-shot merge could never complete. Took 8 attempts; the 7 failures were all *infrastructure*, not merge logic, and each one bought a fix or a finding: (1) parse-level retry — `chat()` only retried transport errors, an empty-200 sank 37 min of work; (2) cache-busting retry markers — the gateway's exact-match response cache replayed a cached empty body for byte-identical retries, making retry a no-op; (3) `DAIMON_LLM_NO_CACHE` — a transiently-empty chunk response got cache-pinned and failed every subsequent run instantly; (4) progress/retry logging — runs were silent for 40 min, indistinguishable from healthy; (5) a hard ~815s server-side request ceiling, measured four times at 814–816s — dense K=3 merges on kimi generate longer than that and can NEVER complete, so K=2 (`DAIMON_MERGE_GROUP_SIZE=2`) carried the pass: chunks 118–240s, five 2-input merges 149–484s, total 1606s. Checkpoint: valid, 156 items, 110 verbatim (70.5%), 78 decisions — densest in the corpus, which is why only H1 hit these ceilings.

**Why it matters:** The #28 product ceiling (single-shot merge DNF at 6 chunks) is gone, but the real lesson is merge cost scales with checkpoint *density*, not chunk count — K is now the knob that trades call count against per-call generation time under whatever ceiling the serving stack imposes. The two gateway behaviors (response-cache poisoning, request ceiling) are scar candidates because they will eat any future long-call work, not just merges. Holdout can now go to n=4 once H1 gets blind ground truth and judging. Caveat: H1's serialize used K=2 and no-cache while H2–H4 used the single-shot path — same model and prompts, but the merge topology differs; note it when comparing scores.

**Pointers:** PR #29 (5 commits), [`.scars/candidates/gateway-response-cache-pins-bad-responses.md`](../.scars/candidates/gateway-response-cache-pins-bad-responses.md), [`.scars/candidates/gateway-815s-request-ceiling.md`](../.scars/candidates/gateway-815s-request-ceiling.md), `experiments/track-a/runs/H1/rerun/`.
