# Daimon

> A **dream-briefing** for your AI agent: a skimmable "while you were away / here's where we left off" artifact, shown when you resume a session.

---

## What is Daimon?

Daimon is a **dream-briefing** for AI coding agents — **Claude Code, Codex, and [hermes-agent](https://github.com/NousResearch/hermes-agent)-style hosts**. At the end of a session it writes a small cognitive checkpoint; at the start of the next one it reconstructs that checkpoint into a short briefing the agent shows you — so it resumes from a faithful prior state instead of a confident guess.

It is **self-contained and host-agnostic**: a hooks + CLI tool with its own lean memory substrate — a per-project checkpoint store and a prose serializer that pins decisions, beliefs, and open questions with trust class, quote provenance, and supersession. **stdlib-first, offline-first, no server.** It needs only a transcript and an LLM endpoint (pluggable: a local CLI like `claude`, or any OpenAI-compatible gateway). It does **not** depend on [Honcho](https://github.com/plastic-labs/honcho), [Graphiti](https://github.com/getzep/graphiti), or hermes at runtime — those were evaluated as a substrate and deliberately not adopted (see **[D-009](./research/DECISIONS.md)** and `research/memory-backend/`).

The name comes from the Greek *δαίμων* — a guiding spirit (distinct from "demon") believed to accompany a person, offering counsel and warnings.

---

## Quickstart

### Claude Code — plugin install (recommended)

Inside Claude Code, add this repo as a marketplace and install the plugin (it wires the `SessionStart`/`SessionEnd` hooks for you):

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

Then, in a terminal, install the `daimon` CLI the hooks shell out to, and configure an LLM backend:

```sh
# 1. Install the daimon CLI (no clone needed)
uv tool install 'git+https://github.com/Daily-Nerd/daimon#subdirectory=plugin'
#   From a clone instead: uv tool install ./plugin
#   Optional pretty output: uv tool install './plugin[pretty]'
#   (rich tables/panels for `status`/`brief`/`configure`; auto-falls back to plain text when
#    rich is absent, output is piped, NO_COLOR is set, or DAIMON_PLAIN=1)

# 2. Detect/choose an LLM backend and write ~/.daimon/env
daimon configure                    # ✓ already? prints "ready". Gaps? fills them (chmod 600)
```

`daimon configure` resolves the backend the same way the hooks do: if the **`claude` CLI** is on your PATH and no API key is set, you're **zero-config** — it prints `✓ ready` and writes nothing. Otherwise it writes the right `DAIMON_LLM_*` keys (litellm/OpenAI-compatible, or a custom command). Hooks run with the host's inherited env (no shell profile), which is why config lives in `~/.daimon/env`, not your shell.

Order doesn't matter: if the hooks land before the CLI, sessions start normally and the hook prints a one-line install hint instead of a briefing.

> **Don't mix install paths.** The plugin registers the hooks itself — plugin users must **not** also run `hook/daimon-hooks.py install` (the manual path below). Both paths coexisting registers the hooks twice: every session would inject the briefing twice and spawn two checkpoint serializes (two LLM calls). If you're switching to the plugin from a manual install, run `python3 hook/daimon-hooks.py uninstall` first.

### Manual hooks — Codex and non-plugin hosts (fallback)

From a clone, after steps 1–2 above:

```sh
python3 hook/daimon-hooks.py install   # Claude Code without the plugin system
#   Codex: see hook/CODEX.md
```

This copies the hook scripts to `~/.claude/hooks/` and registers them in `~/.claude/settings.json` directly — same hooks, no marketplace required.

That's it. End a session → a checkpoint is written; start the next → a *"while you were away"* briefing appears. Check state anytime with `daimon status`; a failed capture self-heals on the next start. Kill switch: `DAIMON_DISABLE=1`.

Anchor a belief to code: `daimon anchor <file> <symbol>` prints an `anchored_to` block — put it on a checkpoint item, and `daimon brief` flags that item under **CODE DRIFT — verify before trusting** when the symbol's body changes or disappears. Offline, stdlib `ast`, no MCP.

---

## What's net-new

Daimon's surface, honestly scoped:

- **The briefing UX** — a session-*start* artifact (`research/findings/07`).
- **The cognitive-checkpoint serializer** — extractive trust/provenance/supersession over a real transcript (D-006/D-007), self-contained, no graph DB.
- **Host-agnostic hooks** — Claude Code (live-validated daily) + Codex (adapter ships, not yet live-dogfooded), hermes optional; other hosts (Odysseus, openclawd, …) reachable via a thin adapter.
- **The initiative taxonomy** — attention-gated proactive interruption (MVP ships Level 0 only: pull at session start, nothing pings you).

Honcho and Graphiti were evaluated as a memory substrate and **not adopted** — they conflict with Daimon's lean / offline / no-gateway constraints (the lexical-first verdict is in `research/memory-backend/`; rationale in **[D-009](./research/DECISIONS.md)**). A Claimify-style extraction gate, if built, is a natural upstream PR to Graphiti — not a runtime dependency.

---

## Status

This project was previously framed as a standalone "persistent AI companion" with an epistemic-graph differentiator. That framing was **retired** per **[D-008](./research/DECISIONS.md)** (user-approved 2026-06-09): Track B (`findings/07`) found ~7/9 of the proposed epistemic-graph features already shipped by Honcho + Graphiti. The differentiator was a dependency, not a moat.

**Update ([D-009](./research/DECISIONS.md), 2026-06-27):** D-008's "build on Honcho + Graphiti" half **never shipped** and was inverted by later evidence — the gateway outages this cycle plus the memory-backend scale-test (see [research/memory-backend/](./research/memory-backend/) and D-009 in [research/DECISIONS.md](./research/DECISIONS.md)). The runtime is **self-contained and host-agnostic** (Claude Code live-validated; Codex adapter shipped, awaiting first live run; hermes optional); Honcho/Graphiti are not runtime dependencies.

**Authoritative architecture:** [docs/MVP-DREAM-BRIEFING.md](./docs/MVP-DREAM-BRIEFING.md)
**Evidence trail:** [research/](./research/README.md) — algorithms, findings, decisions, open questions

The older `docs/` (RFC, Architecture, Pitch, Problem) are preserved with status banners — superseded ≠ deleted — because the research docs reference them by section.

**License:** Apache-2.0 (D-001) · **Org:** [Daily-Nerd](https://github.com/Daily-Nerd)

Daimon was developed in a private lab repo; this repository starts at v0.2.0. The full evidence trail — algorithms, findings, decisions, logbook — ships in [research/](./research/README.md).

---

## Docs

- [MVP — Dream-Briefing Skill](./docs/MVP-DREAM-BRIEFING.md) — current authoritative architecture (provenance-tagged)
- [Codex hooks](./hook/CODEX.md) — Codex `SessionStart` / throttled `Stop` adapter
- [The Pitch](./docs/PITCH.md) — earlier positioning (depends-on-Honcho/Graphiti framing; superseded by [D-009](./research/DECISIONS.md))
- [The Problem](./docs/PROBLEM.md) — the context-loss thesis (still valid; demonstrated live)
- [Validation Plan](./docs/VALIDATION.md) — the pre-build gate, with its outcome banner
- [Technical RFC](./docs/RFC.md) — superseded; CRP checkpoint sections still live (the MVP reuses them)
- [Architecture](./docs/ARCHITECTURE.md) — superseded by MVP-DREAM-BRIEFING.md
- [Research Logbook](./research/README.md) — the evidence trail
