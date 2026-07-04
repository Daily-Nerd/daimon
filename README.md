# Daimon

> A **dream-briefing** for your AI agent: a skimmable "while you were away / here's where we left off" artifact, shown when you resume a session.

Your agent forgets everything between sessions. Daimon writes a small cognitive checkpoint when a session ends and turns it into a briefing when the next one starts — so the agent resumes from a faithful prior state instead of a confident guess:

```
While you were away — here's where we left off.

VERIFY BEFORE TRUSTING (state may have changed outside this session):
- [✓ verbatim] PR #212 state — you said you'd merge it yourself from the UI  — "I'll merge it after the demo"

Open loops:
- [✓ verbatim] Retry policy for the payments webhook — exponential or fixed?  — "don't ship the retry loop until we pick a policy"
- [~ inferred] The staging config drift needs an owner [carried]

Decisions made:
- [✓ verbatim] Postgres advisory locks over Redis locks for the scheduler  — "let's not add a Redis dependency for this"
- [~ inferred] Feature-flag the new invoice path; default off until QA signs off

Active topic: Migrating the scheduler off cron to the new worker pool
```

Every item carries its **trust class**: `✓ verbatim` items are pinned to an exact quote from the transcript and are never reworded — not by carry-over between sessions, not by rendering, not by budget truncation. `~ inferred` items are the agent's own conclusions and are allowed to evolve. Items carried from older sessions say so. That distinction — knowing which memories are quotes and which are guesses — is the point.

The name comes from the Greek *δαίμων* — a guiding spirit (distinct from "demon") believed to accompany a person, offering counsel and warnings.

---

## Install

The `daimon` CLI ships on PyPI:

```sh
uv tool install 'daimon-briefing[pretty]'
#   pipx works identically: pipx install 'daimon-briefing[pretty]'
#   [pretty] adds rich tables/panels for status/brief; plain text without it
```

**Upgrading:**

```sh
uv tool upgrade daimon-briefing
daimon hooks install <host>     # refresh installed hook scripts to match (non-plugin hosts)
```

### Connect an LLM (one time)

Serialization needs an LLM endpoint. If the `claude` CLI is on your PATH you are **zero-config** — `daimon configure` prints `✓ ready` and you're done. Otherwise, any **OpenAI-compatible endpoint** works — for example a free-tier Gemini key:

```sh
daimon configure --backend litellm \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --api-key <YOUR-KEY> --model gemini-2.5-flash
```

Or any headless CLI that reads a prompt on stdin and prints the response:

```sh
daimon configure --backend command --command '<your-llm-cli>' --output text
```

Config lives in `~/.daimon/env` (hooks run with the host's inherited env, not your shell profile). Kill switch: `DAIMON_DISABLE=1`.

### Hook up your host

**Claude Code (plugin — recommended):**

```
/plugin marketplace add Daily-Nerd/daimon
/plugin install daimon@daimon
```

The plugin registers the `SessionStart`/`SessionEnd` hooks itself. Order doesn't matter: if the hooks land before the CLI, sessions start normally and the hook prints a one-line install hint instead of a briefing.

> **Don't mix install paths.** Plugin users must **not** also run the manual hook installer below — both paths coexisting registers the hooks twice (double briefings, double serialize LLM calls). Switching from manual to plugin: run `python3 hook/daimon-hooks.py uninstall` first.

**Windsurf:**

```sh
daimon hooks install windsurf
```

This copies the hook script to the stable path `~/.daimon/hooks/` and prints the registration snippet: point Windsurf's Cascade hooks config (user-level JSON — see [Windsurf's hooks docs](https://docs.windsurf.com/windsurf/cascade/hooks)) at that script for **both** the `pre_user_prompt` and `post_cascade_response` events. Windsurf has no session-end event, so daimon accumulates its own transcript per conversation and serializes on a throttle (`DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL`, default 300s; `0` serializes every turn). Briefings are read with `daimon brief` in a terminal. Two knobs worth setting for your first week:

```sh
echo 'DAIMON_MIN_MESSAGES=4' >> ~/.daimon/env                      # don't skip short first sessions
echo 'DAIMON_WINDSURF_MIN_SERIALIZE_INTERVAL=0' >> ~/.daimon/env   # no tail loss while you evaluate
```

**Codex and other hosts (manual, from a clone):**

```sh
python3 hook/daimon-hooks.py install   # Claude Code without the plugin system
#   Codex: see hook/CODEX.md
```

That's it. End a session → a checkpoint is written; start the next → the briefing appears. Check state anytime with `daimon status` — it reports capture health honestly, including failures, skips, and crashes; a failed capture self-heals on the next start.

---

## What you get beyond the briefing

- **`daimon recall <terms>`** — full-text search over your whole checkpoint history (and your team's, if enabled). Multi-term queries degrade to ranked partial matches instead of returning nothing.
- **Proactive recall** — when a new prompt overlaps prior work from an older session, the briefing surfaces it ("you worked on this before"), in English or Spanish — accented text is a first-class citizen.
- **Code anchors** — `daimon anchor <file> <symbol>` pins a belief to a code symbol; if that code changes or disappears, the next briefing flags the item under **CODE DRIFT — verify before trusting**. Offline, stdlib `ast`.
- **Team memory (opt-in)** — `daimon team init <remote>` mirrors checkpoints through a git remote; teammates' active topics and decisions appear in your briefing, clearly attributed, never merged into your own sections.

## What's net-new here

- **Trust-classed, quote-pinned memory** — every briefing item is marked verbatim (exact quote, immutable everywhere) or inferred (allowed to evolve), with provenance and supersession tracked extractively. No embeddings, no graph database, no server: the store is per-project JSON plus a derived SQLite FTS5 index, stdlib-first and offline-first.
- **The briefing UX** — memory that arrives as a session-*start* artifact you can skim in 30 seconds, ordered by what to verify first.
- **Host-agnostic hooks** — Claude Code (live-validated daily), Windsurf (adapter shipped, in live validation), Codex (adapter shipped, awaiting first live run); other hosts are reachable via the same thin adapter shape.

## Status

| Surface | State |
|---------|-------|
| Claude Code plugin + hooks | live-validated daily |
| CLI (`brief`, `status`, `recall`, `heal`, `anchor`, `configure`, `hooks`) | stable, on PyPI |
| Windsurf adapter | shipped, in live validation |
| Codex adapter | shipped, awaiting first live run |
| Gemini host hooks | blocked upstream (`gemini-cli#14715`) |
| Team memory | shipped, opt-in, early |

Daimon is self-contained at runtime — no external memory backend, no server. The full evidence trail behind every design decision (algorithms, findings, decision records, rejected alternatives) lives in [research/](./research/README.md); the architecture is documented in [docs/MVP-DREAM-BRIEFING.md](./docs/MVP-DREAM-BRIEFING.md).

**License:** Apache-2.0 · **Org:** [Daily-Nerd](https://github.com/Daily-Nerd) · This repository starts at v0.2.0 (earlier development happened in a private lab repo; the research trail ships in [research/](./research/README.md)).

---

## Docs

- [MVP — Dream-Briefing](./docs/MVP-DREAM-BRIEFING.md) — authoritative architecture
- [Codex hooks](./hook/CODEX.md) — Codex adapter setup
- [The Problem](./docs/PROBLEM.md) — the context-loss thesis
- [Research Logbook](./research/README.md) — findings, decisions, evidence trail
- Historical docs ([RFC](./docs/RFC.md), [Architecture](./docs/ARCHITECTURE.md), [Pitch](./docs/PITCH.md), [Validation Plan](./docs/VALIDATION.md)) are preserved with status banners — superseded ≠ deleted.
