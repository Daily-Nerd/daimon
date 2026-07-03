# Changelog

All notable changes to daimon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.2](https://github.com/Daily-Nerd/daimon/compare/v0.3.1...v0.3.2) (2026-07-03)


### Bug Fixes

* **carry:** filter document-frequent terms from dedup identity ([#16](https://github.com/Daily-Nerd/daimon/issues/16)) ([5e593e2](https://github.com/Daily-Nerd/daimon/commit/5e593e264e1349ee6e0b40ff53270049b934f82f))

## [0.3.1](https://github.com/Daily-Nerd/daimon/compare/v0.3.0...v0.3.1) (2026-07-03)


### Bug Fixes

* **version:** derive __version__ from installed metadata ([15c73c6](https://github.com/Daily-Nerd/daimon/commit/15c73c641036a969ffe2b2eb7f9cb862cabcfa60))
* **version:** derive __version__ from installed metadata ([#11](https://github.com/Daily-Nerd/daimon/issues/11)) ([3147490](https://github.com/Daily-Nerd/daimon/commit/3147490aceb82fd786b2e321c043369db8b634b2))

## [0.3.0](https://github.com/Daily-Nerd/daimon/compare/v0.2.0...v0.3.0) (2026-07-03)


### Features

* **harvest:** Spanish scar markers — detector no longer silent on Spanish replies ([#7](https://github.com/Daily-Nerd/daimon/issues/7)) ([7622e75](https://github.com/Daily-Nerd/daimon/commit/7622e7576ebdbc1b97cbe56d8c6a07b8a45681d6))
* **recall:** unicode tokenization + diacritic folding + Spanish stopwords ([#6](https://github.com/Daily-Nerd/daimon/issues/6)) ([db785ce](https://github.com/Daily-Nerd/daimon/commit/db785ce6f9ccd4ed917547f806159b647f4a1427))
* **serializer:** D-012 — preserve transcript language in item text ([a02af81](https://github.com/Daily-Nerd/daimon/commit/a02af819585580f0d2308a9444784ded3a0b4434))
* **serializer:** D-012 — preserve transcript language in item text ([#9](https://github.com/Daily-Nerd/daimon/issues/9)) ([58668dd](https://github.com/Daily-Nerd/daimon/commit/58668ddce75ea40249d8e62e4e319e6241978ca5))

## [Unreleased]

### Added

- **Briefing token budget with section-preserving truncation** (#79) — the
  injected plain briefing now fits `DAIMON_BRIEF_MAX_TOKENS` (default 3000,
  `0` = unbounded, estimate = chars/4, no tokenizer dependency). Over budget:
  long items truncate first with `**Label:**` sections preserved over filler,
  then whole items drop lowest-value first (beliefs → uncertainties → oldest
  decisions → lightest open loops, per #78 weights). External verify-first
  items, the active topic, and contradictions are the skeleton — never
  dropped. Every cut is announced with a trim note; under budget the output
  is byte-identical to before.

- **Proactive recall** (#125) — memory that pulls itself. A new
  `UserPromptSubmit` hook matches each prompt against checkpoint history
  (FTS5) and injects up to two "prior work" lines — attributed, trust-marked,
  superseded-flagged — when past sessions genuinely overlap the current ask.
  Silence is the default: no injection without ≥2 salient prompt terms, ≥2
  term overlap in the match, a known project, and a session-scoped cooldown
  (each checkpoint suggested at most once per session; state under
  `~/.daimon/recall_seen/`, disposable). Ranking = FTS5 relevance × #78
  effective weight. Backend is the new `daimon recall-inject` (rc 0 always,
  prompt on stdin); recall index schema bumps to v2 (adds `importance` +
  `first_seen`; auto-rebuilds once). ~150 ms warm. Hosts running an older CLI
  binary get silence, never errors — reinstall the tool to activate.

- **Decay + recency weighting** (#78) — `scoring.effective_weight` orders
  checkpoint items by `importance × recency tier × per-type decay`, with
  non-linear overdue escalation for open questions past their expected
  lifespan: stale items sink, unresolved open loops surface against other
  stale items. Briefing sections (open loops, beliefs, uncertainties) now
  render heaviest-first; decisions stay chronological (serializer contract).
  Pre-D-011 checkpoints get equal neutral weights and render exactly as
  before. Pure stdlib, deterministic.

- **Per-item `importance` + `first_seen`** (#126) — the ranking seed for decay
  (#78) and proactive recall (#125). The serializer now asks the LLM to score
  every checkpoint item 1-10 by consequence (prompt bump D-010 → D-011;
  pre-bump checkpoints fire the usual format warning). Malformed scores are
  clamped or dropped, never a serialize failure. `first_seen` is stamped in
  code at write time: an item whose exact text already appears in the project's
  previous latest checkpoint inherits its birth stamp; new or reworded items
  are stamped with the current checkpoint's `created`. Backward compatible in
  both directions.

### Fixed

- **Heal no longer masquerades as your latest session** (#123): `daimon serialize`
  now stamps the checkpoint's `created` from the transcript's session end (last
  message timestamp, file-mtime fallback) instead of the write clock, so a healed
  old session reports its true age in `status`, `brief`, and team reads. On top of
  that, `store.write_checkpoint` blocks pointer regressions: a checkpoint whose
  session is older than the current `latest.json` (global or per-project) writes
  its per-session file but leaves the pointer — and its prev-N history — untouched.
  Together these make rescuing old failed sessions safe: heal can no longer steal
  the briefing pointer from newer work.

## [0.2.0] — 2026-07-02

The maturity release: real plugin packaging, an unbounded-disk fix, format
versioning, and the complete shared team memory arc.

### Added

- **Shared team memory** — teammates on one repo share a project mind:
  - Phase 1 (#111): opt-in (`DAIMON_TEAM=1`) per-author team mirror under
    `~/.daimon/team/`, `daimon brief --team` with an attributed Teammates
    section. **Schema note:** every checkpoint now carries an `author` field
    (stamped at write time regardless of the flag); team-mirrored copies also
    carry `project_slug`.
  - Phase 2 (#112): `daimon recall <query>` — derived SQLite+FTS5 full-text
    search over local + team checkpoint history; superseded items flagged, not
    hidden; index is disposable and self-rebuilding.
  - Phase 3 (#113): `daimon team <init|sync|status>` — sidecar private-repo
    sync (append-only per-author files, conflict-free by construction),
    ls-remote freshness gate, force-push/rewrite detection,
    author-vs-committer mismatch warning, read-time retention window
    (`DAIMON_TEAM_RETENTION_DAYS`), opportunistic detached sync at SessionStart.
- Claude Code **plugin packaging** (#91): `.claude-plugin/plugin.json` +
  self-listing marketplace — install via `/plugin marketplace add
  Daily-Nerd/daimon` + `/plugin install daimon@daimon`
- **Gemini CLI host adapter** (#106): briefing hook live now; serialize staged
  behind upstream gemini-cli#14715 (`transcript_path` stub)
- Checkpoint **GC** (#92): `DAIMON_CHECKPOINT_KEEP` (default 100) prunes old
  per-session files, never touching pointer-referenced ones; fail-safe aborts
  when the protection set is unknowable
- Checkpoint **format versioning** (#93): `format_version` + `created` stamped
  at write; age computed from `created` (mtime fallback); version-mismatch
  warning in `status`/`brief`
- Scar harvester wired into the serialize path (#100, still opt-in via
  `DAIMON_SCAR_HARVEST`)
- `contradictions_flagged` rendered as its own briefing section; prompt bumped
  to D-010 (#101)
- `daimon anchor --attach <text-match>` (#102): attach a code anchor to a
  cognitive item without hand-editing JSON — makes drift detection reachable
- Shared `hook/_daimon_hook_lib.py` consumed by all six host hooks (#108)
- `daimon --version` flag (#94)
- CI pipeline: full pytest suite on PRs and pushes to `main`, Python 3.10 + 3.13 (#90)

### Changed

- `emotional_valence` removed from the serializer schema (#101). Existing
  checkpoints will show a one-time format-version notice — expected.

### Fixed

- Serializer resamples once with an attempt nonce when the model's output
  fails schema validation — gateway response caches can no longer pin a bad
  response and make sessions permanently unhealable (#118)
- `daimon recall` honors the team retention window — parity with
  `brief --team` (#120)
- Fresh install with no `~/.claude/settings.json` no longer crashes the hook
  installer (#109)
- Fetch+merge in team sync works on machines with no git identity, e.g. CI
  runners (#113)

## [0.1.0] — 2026-07-01

Initial development version. Everything below shipped issue-by-issue on `main`
before a changelog existed; issue numbers are the source of truth.

### Added

- Core pipeline: serialize a session transcript into a cognitive checkpoint at
  session end, render a "while you were away" briefing at session start
- Hooks for Claude Code (SessionStart/SessionEnd) and Codex, with installer
  scripts; hermes entry-point integration
- Pluggable LLM backend: LiteLLM, arbitrary command, and claude-cli headless,
  with `auto` detection (#16)
- Hierarchical chunk merge for long transcripts (#28)
- Self-healing capture: opportunistic serialize retry at SessionStart (#26),
  with heal transparency and `--dry-run` (#86)
- `daimon configure`: backend detection wizard writing `~/.daimon/env` (#48)
- Rich CLI rendering with plain-text fallback (#56)
- Cognition↔code drift detection v1: anchor cognitive items to code entities,
  flag stale anchors (#60)
- Per-session serialize accountability: `serialize.log` as a first-class
  ledger, hung/failed classification (#27, #72, #73)
- `daimon status` health verdict + sibling-bucket split detection (#84)
- Transcript scar harvester: zero-LLM regex pass seeding scar candidates (#76)
- Briefing decision cap: recent-N decisions with overflow marker (#77)
- `write-checkpoint` introspection path with `--source` provenance (#23)
- `serialize --project` routing flag (#34)

### Fixed

- Checkpoint project identity keyed on raw cwd forked phantom buckets for
  subdirectory sessions (#74)
- `daimon brief` ignored cwd and showed the global checkpoint (#57)
- Too-short transcripts are a benign skip, not a serialize failure — no more
  false status alarms or pointless heal retries (#88)
- Serialize tests leaked result lines into the real `~/.daimon` logs (#54)
