# Changelog

All notable changes to daimon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.0](https://github.com/Daily-Nerd/daimon/compare/v0.8.2...v0.9.0) (2026-07-07)


### Features

* **cli:** header-only brief fallback by default, full body opt-in ([#97](https://github.com/Daily-Nerd/daimon/issues/97)) ([da89330](https://github.com/Daily-Nerd/daimon/commit/da8933041ac0527c116a04b9db58d428984a9862))

## [0.8.2](https://github.com/Daily-Nerd/daimon/compare/v0.8.1...v0.8.2) (2026-07-07)


### Bug Fixes

* **cli:** stamp uncaught crashes with a timestamp header before the traceback ([#93](https://github.com/Daily-Nerd/daimon/issues/93)) ([e282c1f](https://github.com/Daily-Nerd/daimon/commit/e282c1fec2347a85bff88e30a6b850e6a2411d93))
* **release:** reach through release-please's tagged TOML values in the uv.lock jsonpath ([#86](https://github.com/Daily-Nerd/daimon/issues/86)) ([968ae42](https://github.com/Daily-Nerd/daimon/commit/968ae42b802c4bf18c646ed49cb6d189ec53c148))
* **skill:** frontmatter name matches the skill directory name ([#91](https://github.com/Daily-Nerd/daimon/issues/91)) ([8b09e50](https://github.com/Daily-Nerd/daimon/commit/8b09e50917ea43c62af54e384ade7c2caa27bee5))
* **skill:** install the Windsurf global skill into the skills directory, not memories ([#89](https://github.com/Daily-Nerd/daimon/issues/89)) ([606be08](https://github.com/Daily-Nerd/daimon/commit/606be0868b6499ab3df9afee0abadb8bc1c6021c))
* **store:** carry reads only the project's own latest pointer ([#95](https://github.com/Daily-Nerd/daimon/issues/95)) ([468ba04](https://github.com/Daily-Nerd/daimon/commit/468ba0464bb7fd222fd7611569bd67584616c699))

## [0.8.1](https://github.com/Daily-Nerd/daimon/compare/v0.8.0...v0.8.1) (2026-07-06)


### Documentation

* **hosts:** Windsurf terminal briefing is permanent — no hook channel reaches the agent ([#82](https://github.com/Daily-Nerd/daimon/issues/82)) ([8fe88c6](https://github.com/Daily-Nerd/daimon/commit/8fe88c6ff1ddd9f0ec864ecd683af5d0441e64f5))

## [0.8.0](https://github.com/Daily-Nerd/daimon/compare/v0.7.0...v0.8.0) (2026-07-04)


### Features

* **cli:** rich-parity for remaining human-facing output ([#76](https://github.com/Daily-Nerd/daimon/issues/76)) ([9e0920f](https://github.com/Daily-Nerd/daimon/commit/9e0920f37086b76b0329ddd2206da320de89a1db))
* **hosts:** serialize Windsurf sessions from the native Cascade transcript ([#71](https://github.com/Daily-Nerd/daimon/issues/71)) ([5d8d4e8](https://github.com/Daily-Nerd/daimon/commit/5d8d4e8589541c5b8ae85dd0273a681f98d8c6a1))

## [0.7.0](https://github.com/Daily-Nerd/daimon/compare/v0.6.0...v0.7.0) (2026-07-04)


### Features

* **cli:** daimon skill — portable agent skill installed per host ([#67](https://github.com/Daily-Nerd/daimon/issues/67)) ([2ccbb9a](https://github.com/Daily-Nerd/daimon/commit/2ccbb9a51bc5cc7b1b4c30077dfb6273dda92ddd))
* **cli:** daimon stats — local usage + capture aggregates, zero phone-home ([#55](https://github.com/Daily-Nerd/daimon/issues/55)) ([b19da46](https://github.com/Daily-Nerd/daimon/commit/b19da46ca11880668756f3f887c7788cae2ad9f1))
* **cli:** rich-parity for stats, recall, hooks, team, and --help ([#69](https://github.com/Daily-Nerd/daimon/issues/69)) ([282ee5e](https://github.com/Daily-Nerd/daimon/commit/282ee5ee85bbfaca5488a70817cafc3ecdb081c4))
* **configure:** `--test` smoke-tests the backend; command stderr lands in a local log ([#57](https://github.com/Daily-Nerd/daimon/issues/57)) ([6e94136](https://github.com/Daily-Nerd/daimon/commit/6e9413680bfb48226c8d2f02e03bfc714dc935a3))


### Bug Fixes

* **configure:** `--test` proves JSON-extraction fitness, not just transport ([#60](https://github.com/Daily-Nerd/daimon/issues/60)) ([11b4e20](https://github.com/Daily-Nerd/daimon/commit/11b4e2049e08ebd423ff6a42335cf1f87dca5490))
* **heal:** survive hung targets; attribute pre-flight errors to their session ([#50](https://github.com/Daily-Nerd/daimon/issues/50)) ([29c7d93](https://github.com/Daily-Nerd/daimon/commit/29c7d93bf2df4928c50fb1d68c0e6064fa583fea))
* **hosts:** Windsurf adapter probe-dumps payloads it previously dropped silently ([#63](https://github.com/Daily-Nerd/daimon/issues/63)) ([e9d0120](https://github.com/Daily-Nerd/daimon/commit/e9d0120524318f66d21b63365a9cf70757025438))
* **serialize:** backend-aware pre-flight — command/claude-cli backends need no API key ([#53](https://github.com/Daily-Nerd/daimon/issues/53)) ([1827604](https://github.com/Daily-Nerd/daimon/commit/1827604872f84ef1475905fd8361f69b3d0b4006))

## [0.6.0](https://github.com/Daily-Nerd/daimon/compare/v0.5.0...v0.6.0) (2026-07-04)


### Features

* **cli:** ship host hook scripts in the package — `daimon hooks install <host>` ([#44](https://github.com/Daily-Nerd/daimon/issues/44)) ([0db3ba0](https://github.com/Daily-Nerd/daimon/commit/0db3ba0c5d83ac1f9c78fe602131e86b4b8d6eee))


### Documentation

* **readme:** PyPI-first quickstart, sample briefing, Windsurf setup, plain-language status ([#47](https://github.com/Daily-Nerd/daimon/issues/47)) ([989b4c0](https://github.com/Daily-Nerd/daimon/commit/989b4c0b60f51697dec5d2c58992a29088aab7b8))

## [0.5.0](https://github.com/Daily-Nerd/daimon/compare/v0.4.0...v0.5.0) (2026-07-03)


### Features

* **hosts:** probe --scan-vscdb — locate Cascade conversations in Windsurf's sqlite state ([#38](https://github.com/Daily-Nerd/daimon/issues/38)) ([0c83a55](https://github.com/Daily-Nerd/daimon/commit/0c83a5574fb41b6d9c08086eb0c7ff9ee5a66181))
* **hosts:** Windsurf Cascade adapter — accumulated transcript, throttled serialize ([#41](https://github.com/Daily-Nerd/daimon/issues/41)) ([98cfa8f](https://github.com/Daily-Nerd/daimon/commit/98cfa8fbf195aed7a3798b64bd56f30b893afc2f))

## [0.4.0](https://github.com/Daily-Nerd/daimon/compare/v0.3.3...v0.4.0) (2026-07-03)


### Features

* **carry:** freeze verbatim items on re-discovery to stop rewording erosion ([#23](https://github.com/Daily-Nerd/daimon/issues/23)) ([72c5846](https://github.com/Daily-Nerd/daimon/commit/72c5846417d54d4dadd0513fcda54a7c436b7183)), closes [#22](https://github.com/Daily-Nerd/daimon/issues/22)
* **hosts:** Windsurf Cascade payload probe — ground truth before the adapter ([#37](https://github.com/Daily-Nerd/daimon/issues/37)) ([00bb1ec](https://github.com/Daily-Nerd/daimon/commit/00bb1ec34fbb610478144ceae0173e3a1441ee39))
* **observability:** silent failures leave traces — crash log surfaced, hung serializes healable, fallback stamped ([#34](https://github.com/Daily-Nerd/daimon/issues/34)) ([7fc3d6f](https://github.com/Daily-Nerd/daimon/commit/7fc3d6fb574fb8ec62d1f1a5ec256e5b997556ce))
* **recall:** AND-then-OR fallback — multi-term queries degrade to ranked partials instead of zeroing out ([#26](https://github.com/Daily-Nerd/daimon/issues/26)) ([69e9879](https://github.com/Daily-Nerd/daimon/commit/69e987926170f11e32cab9140efcfe91614b1206))


### Bug Fixes

* **briefing:** verbatim integrity on every render surface — LLM render post-validated, truncation exemption, untagged trust ([#36](https://github.com/Daily-Nerd/daimon/issues/36)) ([2e1dd8e](https://github.com/Daily-Nerd/daimon/commit/2e1dd8e7102cf7630bca6c4d64af05bff5236e0d))
* **cli:** UX-contract batch — surface messages match behavior (7 repairs) ([#33](https://github.com/Daily-Nerd/daimon/issues/33)) ([d28dd52](https://github.com/Daily-Nerd/daimon/commit/d28dd529228a54a5587b88648fd847a71ee1701d))
* **recall:** fold suggest() haystack — accented Spanish prior work was silenced by the overlap gate ([#32](https://github.com/Daily-Nerd/daimon/issues/32)) ([13226ad](https://github.com/Daily-Nerd/daimon/commit/13226ad3fe1921d6e94d4be961c71c0e5baff925))

## [0.3.3](https://github.com/Daily-Nerd/daimon/compare/v0.3.2...v0.3.3) (2026-07-03)


### Documentation

* propagate D-009 pivot to install-facing artifacts ([#20](https://github.com/Daily-Nerd/daimon/issues/20)) ([e7d5448](https://github.com/Daily-Nerd/daimon/commit/e7d5448f2310cb27751ee72231410383e39f0991)), closes [#19](https://github.com/Daily-Nerd/daimon/issues/19)

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

- Serializer resamples once with an attempt nonce when the model's output
  fails schema validation — gateway response caches can no longer pin a bad
  response and make sessions permanently unhealable (#118)
  
- `daimon recall` honors the team retention window — parity with
  `brief --team` (#120)
  
- Fresh install with no `~/.claude/settings.json` no longer crashes the hook
  installer (#109)
  
- Fetch+merge in team sync works on machines with no git identity, e.g. CI
  runners (#113)

### Changed

- `emotional_valence` removed from the serializer schema (#101). Existing
  checkpoints will show a one-time format-version notice — expected.


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
