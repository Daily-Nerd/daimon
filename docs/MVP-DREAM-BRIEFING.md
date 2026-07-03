# MVP — Dream-Briefing Skill (on hermes-agent + Honcho)

**Status:** Scoping. Supersedes the standalone-product framing in `docs/RFC.md` / `docs/ARCHITECTURE.md` per **D-008** (user-approved 2026-06-09).

**Provenance rule for this doc:** every claim about hermes-agent or Honcho internals is tagged **VERIFIED** (read in their code/docs, with path) or **ASSUMED** (inference, unread). This project was burned by overclaiming once (`findings/03`, `findings/07`); do not repeat it.

---

## 1. What it is

A **dream-briefing** is a session-*start* artifact: a skimmable "while you were away / here's where we left off" briefing the agent shows you when you resume work, reconstructed from a cognitive checkpoint written at the end of the prior session. It is the one piece of the original Daimon kernel that neither Honcho nor Graphiti ships (`findings/07` delta map) and that was demonstrated valuable live.

**Motivating failure (the gap it closes):** the agent confidently asserted a PR was still open when the user had already merged it *outside the AI ecosystem*. The agent had no record of the state change because nothing carried the prior session's open loops forward. The briefing exists to surface exactly those carried-over facts ("last session you were waiting on PR #6 — verify its state before acting") so the model resumes from a faithful prior state instead of a confident guess. The briefing's job is **recall fidelity, not fluency** — `findings/03` proved reconstruction is faithful (FMR ~1%); the weakness is omission (RR 67.3%).

---

## 2. Architecture

### Extension points (hermes-agent) — VERIFIED

hermes-agent has an **event-hooks system** (not just SKILL.md skills). Two hook flavors share the same event names:
- **Shell hooks** — declared in `cli-config.yaml`, run as subprocesses; Hermes pipes a JSON payload to stdin and reads JSON from stdout. *VERIFIED — `website/docs/user-guide/features/hooks.md`.*
- **Plugin hooks** — Python, registered via `ctx.register_hook("<event>", callback)` from a `register(ctx)` entrypoint in `plugin.yaml`. *VERIFIED — `website/docs/user-guide/features/hooks.md`.*

Relevant events (plugin-hook names) — *VERIFIED, same source:*

| Event | Fires | Signature (args the callback receives) |
|---|---|---|
| `on_session_start` | session begins | `(session_id: str, model: str, platform: str, **kwargs)` — **return value ignored** (cannot inject) |
| `on_session_end` | end of every `run_conversation()` | `(session_id, completed, interrupted, model, platform, **kwargs)` |
| `on_session_finalize` | session identity torn down ("last chance to flush state") | `(session_id, platform, **kwargs)` |
| `pre_llm_call` | before each model call | receives `conversation_history: list` (OpenAI format); **can inject** — returning `{"context": "..."}` (or a non-empty string) appends text to the **current turn's user message** [*Slice-1 build correction: full signature is `(session_id, user_message, conversation_history, is_first_turn, model, platform, **kwargs)` — extra `user_message` param vs. the table originally claimed; VERIFIED at code level during build*] |
| `post_llm_call` | after each model call | receives `conversation_history` |

**Two architectural constraints this forces (both VERIFIED, both load-bearing):**

1. **Session-lifecycle hooks do NOT receive the transcript.** `on_session_start` / `on_session_end` / `on_session_finalize` get only session metadata. The serializer therefore cannot get the transcript *from the hook payload* — it must read it from storage by `session_id` (see below).
2. **`on_session_start` cannot inject context.** Its return value is ignored. The briefing therefore cannot be injected at the start hook. It must be injected at the **first `pre_llm_call`** of the new session (which both receives history *and* can append to the user message). Injection lands in the **user message, not system prompt** (deliberate, to preserve prompt caching) — *VERIFIED.*

### Transcript access — VERIFIED

hermes persists session message history in **`~/.hermes/state.db`** (SQLite, `messages` table keyed by `session_id`). Programmatic read: instantiate first — `db = SessionDB(); db.get_messages_as_conversation(session_id)` (OpenAI-format array), from `hermes_state.py`. *VERIFIED — `website/docs/developer-guide/session-storage.md`; corrected during Slice-1 build: `SessionDB` is instantiated, not called statically as this doc originally wrote.* So the session-end serializer reads the full transcript by `session_id` even though the hook payload omits it. (Legacy `~/.hermes/sessions/*.jsonl` may exist for old sessions — *VERIFIED, same source.*)

### Checkpoint store

- **MVP / Slice 1: local file.** `~/.daimon/checkpoints/<session_id>.json` (the Track-A `cognitive-state.schema.json`). No Honcho dependency. Lets the user dogfood immediately.
- **Slice 2+: Honcho.** Write the checkpoint as a message from a dedicated analysis peer via `session.add_messages(...)`; Honcho's deriver enqueues representation derivation async. Read at session start via `session.context(summary=True, tokens=N)` (prompt-ready bundle, `.to_anthropic()` / `.to_openai()`) and/or `peer.chat(query)` for targeted recall. *VERIFIED method names — Honcho docs/PyPI `honcho-ai` (`session.add_messages`, `session.context(tokens=...)`, `context.to_openai/to_anthropic`, `peer.chat(query)`, `peer.representation()`).* Which Honcho call best fits the briefing (static `context` vs. reasoned `peer.chat`) is **ASSUMED** until probed — see §9.

### Diagram (ASCII)

```
  SESSION N (work happens)
  ─────────────────────────────────────────────────────────────────
        │ user merges PR #6 OUTSIDE the AI ecosystem (no record yet)
        ▼
  [ session ends ] ──fires──► on_session_end(session_id, …)        ← VERIFIED hook
        │                          │
        │            (1) SessionDB.get_messages(session_id)         ← VERIFIED read path
        │                          ▼
        │                 ┌─────────────────────┐
        │                 │   SERIALIZER         │  Track-A serialize prompt
        │                 │  transcript → CHKPT  │  + cognitive-state.schema.json
        │                 │  (extractive-pinned, │  (D-006 trust classes)
        │                 │   chunked if >~1200ln)│  (D-007 recall fix)
        │                 └─────────┬───────────┘
        │                           ▼
        │         Slice 1: ~/.daimon/checkpoints/<id>.json   (local file)
        │         Slice 2+: session.add_messages(...)         (Honcho)   ← VERIFIED
  ─────────────────────────────────────────────────────────────────
  SESSION N+1 (resume)
  ─────────────────────────────────────────────────────────────────
   on_session_start(session_id, …)   ← VERIFIED; CANNOT inject (return ignored)
        │ (load latest checkpoint into skill state)
        ▼
   first pre_llm_call(conversation_history)   ← VERIFIED; CAN inject
        │   RECONSTRUCT checkpoint → briefing (Track-A reconstruct prompt)
        │   Slice 2+: blend with session.context(summary=True) / peer.chat()  ← VERIFIED
        ▼
   return {"context": "<dream briefing>"}  → appended to user message  ← VERIFIED
        ▼
   model resumes with carried-over state ("you merged PR #6 — verify before acting")
```

**Why a hybrid skill+hooks shape, not a pure SKILL.md skill:** a pure agentskills.io SKILL.md is invoked by slash command or conversation — it has **no automatic session-start/session-end trigger** (*VERIFIED — skills docs describe no lifecycle activation*). The briefing must fire *automatically* at boundaries, so the serialize/inject mechanism lives in **hooks**; the SKILL.md is the user-facing surface (`/daimon-briefing`, config, docs) that ships the hook registration.

**Packaging verdict (VERIFIED — see §9 Q-A):** a SKILL.md dir and a plugin are **two separate, incompatible installation surfaces**. There is no field in the agentskills.io frontmatter that registers a plugin hook. Single-bundle UX is possible only via **a plugin that also calls `ctx.register_skill(name, path)`** — the plugin ships the hook logic AND exposes the skill via a namespaced name. [*Slice-1 build correction: the namespace is DERIVED as `<plugin-name>:<skill-dir>` — ours is `daimon-briefing:daimon-briefing`, not the freely-chosen `daimon:briefing` this doc originally assumed.*] Distribution: `hermes plugins install owner/daimon-plugin --enable`. One `hermes` command, zero manual config edits. *VERIFIED — `website/docs/user-guide/features/plugins.md` (ctx.register_skill table row), `website/docs/guides/build-a-hermes-plugin.md` (Bundle skills section, lines 383–424).* The plugin-first shape also allows a pip distribution path (`hermes_agent.plugins` entry-point group — *VERIFIED `hermes_cli/plugins.py` `ENTRY_POINTS_GROUP`*), meaning `pip install daimon-agent` could be a one-command install story for Slice 3+.

---

## 3. What we reuse from Track A

The Track-A rig (`research/experiments/track-a/`) is the serializer seed — reuse, don't rewrite:

- **`schema/cognitive-state.schema.json`** — the checkpoint format, already carries per-item **trust classes** (`verbatim` + required `quote` / `inferred`) implementing **D-006** (extractive pinning). This is the MVP checkpoint schema.
- **`prompts/01-serialize.md`** (= `runner.py:SERIALIZE_SYS`) — the session-end serialize prompt with the anti-confabulation constraint. Becomes the serializer the `on_session_end` hook runs. D-007 revisions land here.
- **`prompts/02-reconstruct.md`** (= `runner.py:RECONSTRUCT_SYS`) — the checkpoint→briefing prompt. Becomes what the `pre_llm_call` injection runs (PART 2 "dream sequence" *is* the briefing).
- **`scoring/score.py` + the 5 ground-truthed sessions** — keep as a **regression harness**. Any prompt/schema/chunking change re-runs against the held answer keys; gate = RR ≥70%, FMR ≤10% (`findings/03`). This is the guardrail that stops a recall fix from reintroducing fabrication.
- **`runner.py`** — the serialize→reconstruct automation; adapt into the hook's serializer entrypoint.

---

## 4. The recall problem and the plan (D-007) — **PROBE RUN, FORK RESOLVED: CHUNKING**

`findings/03`: recall is **length-driven**, sharp cliff at **~1,400 transcript lines**. The D-007 fork (prompt vs architecture) **was probed on 2026-06-09** (`experiments/track-a/probe_d007.py`, S2, three arms, transcript-grounded LLM judge):

| Arm | RR | FMR | Bars (≥70% / ≤10%) |
|---|---|---|---|
| baseline prompt, single-pass | 37.9% | 4.3% | ❌ |
| D-007 prompt, single-pass | 58.6% | 4.2% | ❌ recall |
| D-007 prompt + chunked (800 ln / 100 overlap) + merge | **89.7%** | **6.7%** | ✅ |

**Resolved: the Slice 1 serializer is chunk→extract→merge for long transcripts; the D-007 prompt is adopted in both regimes.** Prompt alone helps (+21pp) but cannot clear the bar on a 2,187-line transcript — context/attention degradation, as `findings/03` predicted. The merge pass did not fabricate (FMR 6.7% under bar) — D-006 verbatim `quote` pinning held.

**Remaining plan:**
1. **Single-pass below ~1,200 lines, chunked above** (margin under the 1,400 cliff; threshold from n=5 + this probe, one model — re-tune with more sessions).
2. **Re-score on all 5 sessions** with the chunked serializer (probe was S2-only). Add the pieces `findings/03` still names as owed: un-annotated **holdout** (contamination confound), **2-cycle** degradation test before any "no confabulation" claim.
3. Judge caveat: probe scored by LLM-as-judge (transcript-grounded — see `.scars/0001`), not human-verified. Same-judge cross-arm deltas are sound; absolute RR is judge-dependent (judge is harsher than the human Round-1 scoring).
4. **Staleness (Q-STALE, from first live dogfood — `findings/03`):** facts that evolve within a session get pinned to their earliest strong quote (observed live: the briefing cited the superseded broken-judge probe numbers as verbatim evidence). Slice 2 serializer/merge MUST prefer the latest state of an evolving fact — pin the final quote, optionally noting the evolution — and the regression harness gains a **staleness-rate** metric alongside RR/FMR. FMR cannot catch this (the stale quote is genuinely in the transcript).

---

## 5. Initiative taxonomy (v0)

MVP ships **Level 0 only — pull, at session start.** The briefing appears when you resume; nothing pings you proactively. This is the silent-by-default posture `findings/05` recommends ("the danger isn't *can we decide when to interrupt* — it's *will users tolerate any proactive AI*"). Level 0 sidesteps the entire interruptibility/attention-model problem.

**Deferred (Level 1–3):** confidence×relevance×attention EV gate, the four-channel escalation (dream-log → chat → Slack DM → DM+mention), the attention model (calendar/activity/`/dnd`), and the contextual-bandit upgrade. All designed in `findings/05`; none in MVP. Rationale: each needs proactive-interrupt infrastructure and an attention signal hermes does not obviously expose (ASSUMED — not verified), and adoption risk dominates algorithm risk here.

---

## 6. Out of scope for MVP

- **Epistemic graph / belief store / contradiction detection.** Use Honcho (reconciliation) + Graphiti (temporal validity intervals). `findings/07` / D-005-retracted: building it is reimplementing shipped, funded products. The briefing *consumes* their output; it does not replace it.
- **Proactive interruption** (Level 1–3) — §5.
- **Multi-platform** — target the hermes CLI/gateway path only; no Slack/web/mobile surfaces.
- **Checkpoint versioning / rollback** — `findings/03`: rollback to a checkpoint made by the same lossy process is a weak defense against confabulation; it guards crashes, not drift. Honcho/Graphiti carry temporal history if needed later.
- **Semantic evolution-vs-contradiction classification** — hard, unproven (`findings/07`); research follow-up, not MVP.

---

## 7. Upstream contribution track

Two net-new pieces from `findings/07` whose natural home is a PR, not a standalone build:

1. **Claimify-style extraction gate → PR to Graphiti.** Confirmed absent from Graphiti's `node_operations.py` (no confidence / verification / decontextualization gate before a fact enters the graph) — `findings/07`. The Track-A serializer's `verbatim`+`quote` extractive pinning is a working seed of this gate. Contribution = a high-confidence, decontextualized extraction pass in front of edge/node creation. **Verify line-level against current Graphiti before claiming the gap** (`findings/07` caveat: quotes were WebFetch @ v0.29.1).
2. **Evolution-vs-contradiction classification → research follow-up, not a PR yet.** Graphiti distinguishes the two only by interval overlap; classifying the *kind* of change (corrected misconception vs. real-world state change vs. refinement) is reasoning neither Graphiti nor Honcho does. Hardest, narrowest, unproven — keep as a research note until there's evidence it works.

### Adjacent: SCAR convergence (not MVP scope)

SCAR (sibling project — git-native negative-knowledge graph: deadends/fences/landmines, `.scars/`) runs a session-end candidate-drafter that LLM-reads the transcript for *abandonment* signals; Daimon's serializer LLM-reads the same transcript for *state* (decisions/open-loops/fixes). Same hook point, same input, opposite polarity. Running both naively doubles per-session extraction cost. Future merge: **one session-end extraction pass emitting two artifacts** — the cognitive checkpoint AND scar candidates. The D-007 serializer prompt already extracts assistant-side fixes/diagnoses with root cause, which is most of a `deadend` draft. Deliberately NOT coupled during MVP (two concept-stage projects, independent validation gates); revisit after SCAR's gate 0.4 and Daimon Slice 1 both conclude.

---

## 8. Milestones (thin vertical slices)

Each slice is independently shippable and testable. **Slice 1 runs without Honcho** so the user dogfoods on day one.

**Slice 1 — Local-file briefing, no Honcho.**
`on_session_end` hook → read transcript via `SessionDB.get_messages(session_id)` → run Track-A serializer → write `~/.daimon/checkpoints/<id>.json`. First `pre_llm_call` of next session (`is_first_turn: bool` flag in hook signature — *VERIFIED `website/docs/user-guide/features/hooks.md` `pre_llm_call` parameter table*) → reconstruct → return `{"context": briefing}`. **Ship as a hermes plugin** (`plugin.yaml` + `__init__.py` with `ctx.register_hook()` + `ctx.register_skill()` + bundled `skills/daimon-briefing/SKILL.md`). Install: `hermes plugins install owner/daimon-plugin --enable`. *Dogfoodable in hermes/Claude Code immediately; zero external deps; single install command.* [*Packaging shape changed from "skill bundle" to "plugin with bundled skill" — VERIFIED as the only single-command install path — see §9 Q-A.*] Test: does the next session's briefing surface the prior session's open loops (the PR-merge gap)?

**Slice 2 — Recall fix (D-007).**
Add chunked multi-pass extraction for transcripts >~1,200 ln + edit-style merge. Gate against the §3 regression harness (RR ≥70%, FMR ≤10%). Run the S2 probe, the holdout, and the 2-cycle test. No new surface — same Slice-1 UX, better checkpoints.
Also: **named serializer failures** (from second live dogfood, 2026-06-10) — `serializer.serialize()` swallows all failure causes into `None` (`except Exception: return None`); the 120s-timeout root cause took instrumented reproduction to find instead of one log line. Distinguish ChatError vs JSON-parse vs schema-validation failures and surface the reason to the CLI/log (same class of fix as the PR #13 CLI error split, one layer deeper).

**Slice 3 — Honcho-backed checkpoint + recall.**
Swap the local-file store for `session.add_messages()` (write) and blend `session.context(summary=True)` / `peer.chat()` into the briefing (read). Checkpoint store becomes pluggable (`file` | `honcho`). Gives cross-session user modeling + reconciliation for free. Behind a config flag; local-file remains the default fallback.

**Slice 4 (optional) — Claimify gate PR to Graphiti.**
Extract the extraction-gate logic, verify the gap against current Graphiti, open the PR. Independent of Slices 1–3.

---

## 9. Open questions (VERIFIED vs ASSUMED)

**VERIFIED (read in their docs/code):**
- hermes has an event-hooks system with `on_session_start` / `on_session_end` / `on_session_finalize` / `pre_llm_call` / `post_llm_call` — `website/docs/user-guide/features/hooks.md`.
- Session-lifecycle hooks do **not** carry the transcript; only `pre_llm_call`/`post_llm_call` carry `conversation_history` — same source.
- `on_session_start` **cannot** inject context (return ignored); `pre_llm_call` **can** (`{"context": ...}` → appended to the user message, not system prompt) — same source.
- Transcript is readable by `session_id` from `~/.hermes/state.db` via `SessionDB.get_messages()` (`hermes_state.py`) — `website/docs/developer-guide/session-storage.md`.
- hermes skills follow the agentskills.io SKILL.md standard and have **no** automatic session-lifecycle activation (slash/conversation only) — skills docs.
- Honcho SDK (`honcho-ai`): `session.add_messages()`, `session.context(tokens=N)` → `.to_openai()`/`.to_anthropic()`, `peer.chat(query)`, `peer.representation()` — Honcho docs / PyPI.
- Honcho is **AGPL-3.0**; hermes is **MIT**; Daimon is **Apache-2.0** (D-001) — repo license files.

**VERIFIED (added this session):**
- **Q-A — Single-install UX: VERIFIED. Answer: YES, via plugin-with-bundled-skill.** A SKILL.md directory alone **cannot** register a hook — there is no frontmatter field for that in the agentskills.io SKILL.md schema. The correct shape is a **hermes plugin** that (a) calls `ctx.register_hook("on_session_end", ...)` and `ctx.register_hook("pre_llm_call", ...)` in its `register(ctx)` entrypoint, and (b) calls `ctx.register_skill("briefing", skill_md_path)` to expose the SKILL.md as `daimon:briefing`. Both hooks and the skill ship in one plugin directory. Install: `hermes plugins install owner/daimon-plugin --enable` — one command, zero manual config edits. Also distributable as a pip package via the `hermes_agent.plugins` entry-point group (`ENTRY_POINTS_GROUP` constant — `hermes_cli/plugins.py`). *VERIFIED — `website/docs/user-guide/features/plugins.md` (capability table: "Bundle skills → `ctx.register_skill()`"); `website/docs/guides/build-a-hermes-plugin.md` ("Bundle skills" section, complete working example); `website/docs/developer-guide/creating-skills.md` (SKILL.md frontmatter schema — no hook field exists); `hermes_cli/plugins.py` (`ENTRY_POINTS_GROUP = "hermes_agent.plugins"`).*
- **Q-B — Briefing injection timing: VERIFIED.** `pre_llm_call` receives `is_first_turn: bool` as a named parameter — **no flag state needs to persist across the session-start → first-llm boundary**. The hook can gate injection with `if not is_first_turn: return None` directly. *VERIFIED — `website/docs/user-guide/features/hooks.md` (`pre_llm_call` parameter table: `is_first_turn | bool | True if this is the first turn of a new session, False on subsequent turns`); `website/docs/guides/build-a-hermes-plugin.md` (same parameter listed in hook reference table).*

**ASSUMED / UNVERIFIED (could not confirm in code — verify before relying):**
- **Q-C — Honcho call choice for the briefing.** Static `session.context(summary=True)` vs. reasoned `peer.chat()` vs. both. Untested; decide empirically in Slice 3. Also: Honcho's *queryable belief-revision history* (vs. silent reconciliation) was the one under-documented Honcho seam (`findings/07` caveat) — probe before depending on it.
- **Q-D — Serializer model in-hook.** Track-A used a LiteLLM gateway; which model the hook serializer calls (hermes's configured model? a separate cheap model?) and its latency budget at session end are unspecified. Single-model confound (`findings/03`) still applies — don't reuse kimi-k2.6 numbers as a floor.
- **Q-E — AGPL-3.0 posture.** Calling Honcho over its API/SDK from an Apache-2.0 plugin is **not** a derivative work under FSF's own position (mere API/network communication does not create a combined work; AGPL's §13 copyleft triggers on *conveying or running a modified Honcho*, not on a separate client calling it). **Verified at the principle level; NOT verified for our exact deployment** — if we ever *bundle/self-host a modified* Honcho, or statically link the SDK in a way that forms one program, re-check. hermes already integrates Honcho, which is corroborating but not a legal clearance. Get a license read before shipping Slice 3. *(FSF AGPL-3.0 text; not legal advice.)*
