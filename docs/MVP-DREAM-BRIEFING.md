# MVP — Dream-Briefing (self-contained, host-agnostic)

**Status:** Shipped MVP. This is the authoritative architecture. It describes the **self-contained, host-agnostic** runtime per **[D-009](../research/DECISIONS.md)** (2026-06-27), which supersedes the standalone-product framing in `docs/RFC.md` / `docs/ARCHITECTURE.md` (D-008) *and* D-008's "build on Honcho + Graphiti" substrate framing. Honcho and Graphiti were evaluated as a memory backend and **not adopted as runtime dependencies** — the shipped core is its own lean stack (checkpoint store + prose serializer + deterministic render + pluggable LLM backend).

**Provenance rule for this doc:** every claim about the runtime or a host's hook contract is tagged **VERIFIED** (read in this repo's code or the host's docs, with path) or **ASSUMED** (inference, unread). This project was burned by overclaiming once (`findings/03`, `findings/07`); do not repeat it.

---

## 1. What it is

A **dream-briefing** is a session-*start* artifact: a skimmable "while you were away / here's where we left off" briefing the agent shows you when you resume work, reconstructed from a cognitive checkpoint written at the end of the prior session. It is the one piece of the original Daimon kernel that the memory backends evaluated in D-009 did not ship, and that was demonstrated valuable live.

**Motivating failure (the gap it closes):** the agent confidently asserted a PR was still open when the user had already merged it *outside the AI ecosystem*. The agent had no record of the state change because nothing carried the prior session's open loops forward. The briefing exists to surface exactly those carried-over facts ("last session you were waiting on PR #6 — verify its state before acting") so the model resumes from a faithful prior state instead of a confident guess. The briefing's job is **recall fidelity, not fluency** — `findings/03` proved reconstruction is faithful (FMR ~1%); the weakness is omission (RR 67.3%), which §4 addresses.

---

## 2. Architecture

The **primary integration is native host hooks.** Daimon ships standalone hook scripts that a host (Claude Code, Codex, Gemini) invokes at its own session boundaries; the scripts shell out to an installed `daimon` CLI which owns all serialization, rendering, and storage. There is **no server and no external memory backend** — the runtime is stdlib-only and offline-first. A hermes-agent plugin is an *optional secondary* surface (Appendix A).

### Native hooks — Claude Code (VERIFIED — `hooks/hooks.json`, `hook/`)

`hooks/hooks.json` declares three hooks; the plugin registers them with the host:

| Host event | Script | Role |
|---|---|---|
| `SessionEnd` | `hook/daimon-session-end.py` | Serialize the ending session into a checkpoint |
| `SessionStart` | `hook/daimon-session-brief.py` | Inject the briefing as session context |
| `UserPromptSubmit` | `hook/daimon-prompt-recall.py` | Proactive "you worked on this before" recall |

**Write path (SessionEnd) — VERIFIED `hook/daimon-session-end.py`.** The hook reads the SessionEnd payload from stdin (`{session_id, transcript_path, cwd, reason, ...}`) and spawns `daimon serialize <transcript_path>` as a **detached** background process (`start_new_session=True`, so it survives `/exit`). Serialization is a 30s+ LLM call; blocking session exit on it is unacceptable, so the hook returns immediately and the child finishes on its own. Fail-open: always exit 0; diagnostics go to `~/.daimon/logs/serialize.log`. Per-project routing hands the session's `cwd` to the child so the checkpoint lands under the right project.

**Read path (SessionStart) — VERIFIED `hook/daimon-session-brief.py`.** The hook shells out to `daimon brief`; whatever the CLI prints to stdout is injected as additional session context. The CLI is the *single source of truth* for briefing rendering, so no host re-renders the checkpoint and the hosts never drift. Fail-open (exit 0); a missing CLI prints a one-line install hint instead of a briefing.

**Proactive recall (UserPromptSubmit) — VERIFIED `hook/daimon-prompt-recall.py`.** Fires on every prompt, pipes the prompt to `daimon recall-inject` on stdin, and injects a one-line pointer when the prompt overlaps a prior open loop. Because it fires per-prompt, failures are **silent** (exit 0, no output) — the only thing it ever prints is a real suggestion — and it never re-suggests what the SessionStart briefing already carried.

**Two architectural facts this shape gives us (both VERIFIED, both load-bearing):**

1. **The native SessionEnd payload carries the transcript path.** The serializer reads the transcript directly from the file the host names — there is no separate session-database read and no dependency on host-internal storage.
2. **The briefing is injected at session start, not deferred.** SessionStart stdout becomes session context, so the "while you were away" artifact lands before the first model turn. (Hosts whose start hook cannot inject — see Appendix A for the hermes case — defer to the first model call instead.)

### Host adapters — Codex and Gemini (VERIFIED — `hook/_daimon_hook_lib.py`, `hook/daimon-codex-*.py`, `hook/daimon-gemini-*.py`)

The hook scripts are standalone and **stdlib-only**: they run inside whatever interpreter the host invokes and **cannot import the `daimon_briefing` package** (it lives in an isolated uv-tool venv), so they locate and shell out to the installed `daimon` CLI. Everything the adapters would otherwise duplicate — kill-switch check, CLI resolution, per-project env, detached spawn helpers, checkpoint-age formatting — lives in the shared `hook/_daimon_hook_lib.py`. Host-specific behavior stays in each script: Gemini's pure-JSON stdout, Codex's `additionalContext` envelope and throttled `Stop` hook (Codex has no SessionEnd, so it serializes on a throttled Stop). This is the "thin host adapter" D-009 calls for — new hosts (Odysseus, openclawd, …) become first-class by adding a script pair against the same shared lib.

### Diagram (ASCII — native-hook path)

```
  SESSION N (work happens)
  ─────────────────────────────────────────────────────────────────
        │ user merges PR #6 OUTSIDE the AI ecosystem (no record yet)
        ▼
  [ session ends ] ──host fires──► SessionEnd hook (daimon-session-end.py)
        │                                │  reads {transcript_path, cwd} from stdin
        │                    spawn DETACHED: `daimon serialize <transcript>`
        │                                ▼
        │                 ┌─────────────────────┐
        │                 │   SERIALIZER         │  D-010 serialize prompt
        │                 │  transcript → CHKPT  │  + cognitive-state schema
        │                 │  (extractive-pinned, │  (D-006 trust classes)
        │                 │   chunked if long)   │  (D-007 recall fix)
        │                 └─────────┬───────────┘
        │                           ▼
        │            ~/.daimon/checkpoints/<project>/<id>.json   (per-project store)
        │            deterministic carry: unresolved loops → next checkpoint, [carried]
  ─────────────────────────────────────────────────────────────────
  SESSION N+1 (resume)
  ─────────────────────────────────────────────────────────────────
   SessionStart hook (daimon-session-brief.py)   ← shells out to `daimon brief`
        │   RECONSTRUCT checkpoint → briefing (deterministic render)
        ▼
   stdout → injected as session context           ← briefing lands before first turn
        ▼
   model resumes with carried-over state ("you merged PR #6 — verify before acting")
        │
        ▼   … and on each later prompt:
   UserPromptSubmit hook (daimon-prompt-recall.py) → `daimon recall-inject`
        │   prompt overlaps a prior open loop? inject a one-line pointer (else silent)
```

**Why hooks + a CLI, not a pure SKILL.md skill:** a SKILL.md skill is invoked by slash command or conversation — it has **no automatic session-start/session-end trigger**. The briefing must fire *automatically* at boundaries, so the serialize/inject/recall mechanism lives in **host hooks**, and the bundled `skills/daimon-briefing/SKILL.md` is only the user-facing surface (what a stranger's agent reads to explain the tool).

---

## 3. What we reuse from Track A

The Track-A rig (`research/experiments/track-a/`) was the serializer seed — its prompts and schema ship, hardened, in `plugin/daimon_briefing/`:

- **`schema/cognitive-state.schema.json`** — the checkpoint format, carrying per-item **trust classes** (`verbatim` + required `quote` / `inferred`) implementing **D-006** (extractive pinning). This is the shipped checkpoint schema.
- **`prompts/01-serialize.md`** (now `serializer.py`, D-010 prompt) — the session-end serialize prompt with the anti-confabulation constraint. Runs behind `daimon serialize`; D-007 revisions landed here.
- **`prompts/02-reconstruct.md`** — the checkpoint→briefing prompt, now the deterministic render behind `daimon brief`.
- **`scoring/score.py` + the 5 ground-truthed sessions** — kept as a **regression harness**. Any prompt/schema/chunking change re-runs against the held answer keys; gate = RR ≥70%, FMR ≤10% (`findings/03`). This is the guardrail that stops a recall fix from reintroducing fabrication.

---

## 4. The recall problem and the fix (D-007) — **SHIPPED: CHUNKING**

`findings/03`: recall is **length-driven**, with a sharp cliff at **~1,400 transcript lines**. The D-007 fork (prompt vs architecture) was probed on 2026-06-09 (`experiments/track-a/probe_d007.py`, S2, three arms, transcript-grounded LLM judge):

| Arm | RR | FMR | Bars (≥70% / ≤10%) |
|---|---|---|---|
| baseline prompt, single-pass | 37.9% | 4.3% | ❌ |
| D-007 prompt, single-pass | 58.6% | 4.2% | ❌ recall |
| D-007 prompt + chunked (800 ln / 100 overlap) + merge | **89.7%** | **6.7%** | ✅ |

**Shipped: the serializer is chunk→extract→merge for long transcripts; the D-007 prompt is adopted in both regimes** (`serializer.py` — single-pass below the chunk threshold, chunked multi-pass above, with a merge pass). Prompt alone helps (+21pp) but cannot clear the bar on a 2,187-line transcript — context/attention degradation, as `findings/03` predicted. The merge pass did not fabricate (FMR 6.7% under bar) — D-006 verbatim `quote` pinning held.

**Staleness (Q-STALE, from live dogfood — `findings/03`):** facts that evolve within a session can be pinned to their earliest strong quote (observed live: the briefing cited superseded probe numbers as verbatim evidence). The merge prompt now prefers the **latest state** of an evolving fact (see the final-state reconciliation rules in `serializer.py:MERGE_SYS`), and unresolved loops that survive a session are moved forward by **deterministic carry** (`carry.py`) — exact salient-term overlap, no LLM — and labelled `[carried]` in the briefing so a survived loop is visibly distinct from a freshly observed one. FMR cannot catch staleness on its own (the stale quote is genuinely in the transcript), so the regression harness tracks it separately.

---

## 5. Initiative taxonomy (v0)

MVP ships **Level 0 only — pull, at session start** (plus the lightweight per-prompt recall pointer, which is still pull-shaped: it answers the prompt you just typed, it does not ping you unprompted). The briefing appears when you resume; nothing interrupts you. This is the silent-by-default posture `findings/05` recommends ("the danger isn't *can we decide when to interrupt* — it's *will users tolerate any proactive AI*"). Level 0 sidesteps the entire interruptibility/attention-model problem.

**Deferred (Level 1–3):** confidence×relevance×attention EV gate, the four-channel escalation (dream-log → chat → Slack DM → DM+mention), the attention model (calendar/activity/`/dnd`), and the contextual-bandit upgrade. All designed in `findings/05`; none in MVP. Rationale: each needs proactive-interrupt infrastructure and an attention signal, and adoption risk dominates algorithm risk here.

---

## 6. Out of scope for MVP

- **Epistemic graph / belief store / contradiction detection.** D-009 evaluated the temporal-KG memory backends (Honcho for reconciliation, Graphiti for temporal validity intervals) as a runtime substrate and **rejected them** — a server + graph DB + embeddings reintroduce the gateway/embedding fragility that offline-first is meant to avoid, and Graphiti's one load-bearing idea (temporal-validity supersession) the serializer schema already does cheaply. The briefing does not build an epistemic graph; it carries load-bearing facts forward extractively.
- **Proactive interruption** (Level 1–3) — §5.
- **Multi-platform surfaces** — target the host-hook path only; no Slack/web/mobile.
- **Checkpoint versioning / rollback** — `findings/03`: rollback to a checkpoint made by the same lossy process is a weak defense against confabulation; it guards crashes, not drift.
- **Semantic evolution-vs-contradiction classification** — hard, unproven (`findings/07`); research follow-up, not MVP.

---

## 7. Upstream contribution track (optional, not a runtime dependency)

One net-new piece from `findings/07` whose natural home is a PR to an existing project rather than a Daimon runtime dependency:

- **Claimify-style extraction gate → optional PR to Graphiti.** `findings/07` found Graphiti's `node_operations.py` has no confidence / verification / decontextualization gate before a fact enters the graph. The serializer's `verbatim`+`quote` extractive pinning is a working seed of such a gate. This is a *contribution*, not a dependency — Daimon does not run on Graphiti. **Verify line-level against current Graphiti before claiming the gap** (`findings/07` caveat: quotes were WebFetch @ v0.29.1).
- **Evolution-vs-contradiction classification → research follow-up.** Classifying the *kind* of change (corrected misconception vs. real-world state change vs. refinement) is reasoning the evaluated backends do not do. Hardest, narrowest, unproven — a research note until there's evidence it works.

### Adjacent: SCAR convergence

SCAR (git-native negative-knowledge graph: deadends/fences/landmines, `.scars/`) runs a session-end candidate-drafter that LLM-reads the transcript for *abandonment* signals; Daimon's serializer LLM-reads the same transcript for *state* (decisions/open-loops/fixes). Same hook point, same input, opposite polarity. Daimon already ships an optional session-end scar-harvest pass (`harvest.run`, gated by config) that emits scar candidates alongside the checkpoint — so a single session-end extraction can feed both artifacts.

---

## 8. Delivery status

What ships today, behind `daimon`:

- **Checkpoint → briefing loop** — SessionEnd serialize, SessionStart briefing, per-project checkpoint store. Runs on Claude Code and Codex via native hooks; a host-free dogfood CLI (`daimon serialize` / `daimon brief`) needs no host at all.
- **Recall fix (D-007)** — chunked multi-pass extraction + merge for long transcripts, gated against the §3 regression harness (RR ≥70%, FMR ≤10%). Serializer failures are surfaced by cause (ChatError vs JSON-parse vs schema-validation) to the CLI/log rather than swallowed.
- **Deterministic carry** — unresolved open loops carried forward by exact term overlap, marked `[carried]`.
- **Proactive recall** — `daimon recall-inject` behind the UserPromptSubmit hook.
- **Code-drift detection** — `daimon anchor <file> <symbol>` binds a checkpoint item to a code symbol; the briefing flags it under **CODE DRIFT** when the symbol's body changes (offline, stdlib `ast`).
- **Operability** — `daimon status` (health), `daimon heal` (one-shot recovery of a failed serialize, also fired detached at SessionStart), `daimon configure` (backend detection + `~/.daimon/env`).

Not shipped / not planned as a runtime dependency: any external memory server or graph backend (D-009).

---

## 9. Open questions (VERIFIED vs ASSUMED)

**VERIFIED (read in this repo's code):**
- Native Claude Code hooks are declared in `hooks/hooks.json` (`SessionStart` / `UserPromptSubmit` / `SessionEnd`) and implemented under `hook/`; the SessionEnd payload carries `transcript_path` and `cwd`.
- The hook scripts are stdlib-only and shell out to the installed `daimon` CLI; they cannot import `daimon_briefing` (isolated uv-tool venv). Shared helpers live in `hook/_daimon_hook_lib.py`.
- The serializer chunks long transcripts and merges, preferring latest-state on evolving facts (`serializer.py`); deterministic carry is LLM-free (`carry.py`).
- hermes is an **optional** host, not a requirement: `transcript.py` guards the hermes import and the CLI works without hermes (Appendix A).

**ASSUMED / UNVERIFIED (verify before relying):**
- **Q-model — serializer model + latency budget in-hook.** Which model the detached serializer calls (the host's configured model? a separate cheap model? the `claude` CLI backend?) and its latency budget at session end depend on the user's `daimon configure` result. Single-model confound (`findings/03`) still applies — don't reuse old kimi-k2.6 numbers as a floor.
- **Q-host — new host adapters.** The Codex/Gemini adapters are VERIFIED; Odysseus/openclawd/other hosts are ASSUMED reachable via the same `_daimon_hook_lib.py` shape until an adapter is actually written and dogfooded.
- **Q-carry — carry tuning.** The salient-term overlap thresholds and generic-term filtering in `carry.py` are tuned on limited live data; re-tune as more sessions accumulate.

---

## Appendix A — Secondary integration: hermes-agent plugin (optional)

hermes-agent is **one optional host**, not a requirement. When Daimon runs under hermes, the same serialize→brief loop is wired through hermes's in-process plugin-hook system instead of standalone host scripts. This path still exists in code (`plugin/daimon_briefing/__init__.py`, `plugin/daimon_briefing/hooks.py`) and is guarded so a missing hermes never breaks the standalone CLI.

**Registration — VERIFIED `plugin/daimon_briefing/__init__.py`.** A `register(ctx)` entrypoint wires two hooks and bundles the skill:
- `ctx.register_hook("on_session_end", hooks.on_session_end)`
- `ctx.register_hook("pre_llm_call", hooks.pre_llm_call)`
- `ctx.register_skill(name, skill_md_path)` for each bundled skill dir.

**Two constraints specific to the hermes path (VERIFIED — `plugin/daimon_briefing/hooks.py`, hermes hook docs):**
1. **Session-lifecycle hooks do not receive the transcript.** `on_session_end` gets only session metadata, so the serializer reads the transcript from hermes session storage by `session_id` (`transcript.from_session`) — unlike the native path, which is handed `transcript_path` directly.
2. **The session-start hook cannot inject context** (its return value is ignored). The briefing is therefore injected at the **first `pre_llm_call`** of the next session, gated on the `is_first_turn` flag, and appended to the user message (not the system prompt, to preserve prompt caching).

The hermes callbacks run in-process (not a detached subprocess), but delegate to the same `serializer`, `store`, `briefing`, and `carry` modules the CLI uses, so both integration paths produce identical checkpoints and briefings.
