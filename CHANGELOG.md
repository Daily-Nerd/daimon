# Changelog

All notable changes to daimon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.0](https://github.com/Daily-Nerd/daimon/compare/v0.15.0...v0.16.0) (2026-07-13)


### Features

* **hooks:** daimon hooks status — detect stale installed hook copies ([#271](https://github.com/Daily-Nerd/daimon/issues/271)) ([917b731](https://github.com/Daily-Nerd/daimon/commit/917b731d0ec8e526d57d22aa3dee0762c0cb86b9))
* **recall:** warm the index at write time — rebuild off the first-prompt path ([#248](https://github.com/Daily-Nerd/daimon/issues/248)) ([1f05f46](https://github.com/Daily-Nerd/daimon/commit/1f05f46bdd557a297728aef86c0a08446bedc318))
* **recall:** zero-match scoped search reports where matches exist ([#260](https://github.com/Daily-Nerd/daimon/issues/260)) ([8a169cb](https://github.com/Daily-Nerd/daimon/commit/8a169cb78ca4c9b5bf62799d7458375fb44c0d1b))
* **skill:** teach agents to use memory — recall on reference, resolve to close loops ([#258](https://github.com/Daily-Nerd/daimon/issues/258)) ([8b6da55](https://github.com/Daily-Nerd/daimon/commit/8b6da55801d94b05f6a3fb09ffef7fa9e96d00d7))
* **status:** silent-capture alarm — sessions observed vs checkpoints written ([#270](https://github.com/Daily-Nerd/daimon/issues/270)) ([889d1ee](https://github.com/Daily-Nerd/daimon/commit/889d1ee0e77947fc5b699cd6448c5bf51c15d1d0))


### Bug Fixes

* **hooks:** package Codex lifecycle hooks in `daimon hooks install` ([#263](https://github.com/Daily-Nerd/daimon/issues/263)) ([2b57c2a](https://github.com/Daily-Nerd/daimon/commit/2b57c2a8fba2df63906040eac5a429b2cdf3e521)), closes [#262](https://github.com/Daily-Nerd/daimon/issues/262)
* **llm:** backend failure log carries stdout too — CLIs that error on stdout no longer leave a bare header ([#251](https://github.com/Daily-Nerd/daimon/issues/251)) ([9af0559](https://github.com/Daily-Nerd/daimon/commit/9af055973e2e5aa610114a923a5b3d2136c792b8))
* **recall:** index liveness fold reuses store.is_resolved — one rule, no drift ([#256](https://github.com/Daily-Nerd/daimon/issues/256)) ([e63c068](https://github.com/Daily-Nerd/daimon/commit/e63c068fdc619b323953c52d9af1c5ee61ef266f))
* **recall:** resolve events invalidate the index — events.jsonl joins the fingerprint ([#247](https://github.com/Daily-Nerd/daimon/issues/247)) ([ed66be2](https://github.com/Daily-Nerd/daimon/commit/ed66be27db8728a6fa2f00ee354a75cc4cd5d29a))


### Documentation

* document serializer chunking knobs in configuration.md ([#253](https://github.com/Daily-Nerd/daimon/issues/253)) ([da93321](https://github.com/Daily-Nerd/daimon/commit/da93321d4b321040a989e2e8f5abd8463459d226))

## [0.15.0](https://github.com/Daily-Nerd/daimon/compare/v0.14.0...v0.15.0) (2026-07-11)


### Features

* **cli:** cross-project context switching — daimon projects + --slug on brief/recall ([#244](https://github.com/Daily-Nerd/daimon/issues/244)) ([fbdf461](https://github.com/Daily-Nerd/daimon/commit/fbdf461eddec2495ef0beac4cc2320be8a440704))
* **recall:** item-level supersession from typed links — recency stops crying wolf ([#242](https://github.com/Daily-Nerd/daimon/issues/242)) ([0437f85](https://github.com/Daily-Nerd/daimon/commit/0437f858247415a50eb8ca1d353e2e53139a42c4)), closes [#234](https://github.com/Daily-Nerd/daimon/issues/234)
* **status:** surface unattributed recall items — dark matter made visible ([#239](https://github.com/Daily-Nerd/daimon/issues/239)) ([c2047a0](https://github.com/Daily-Nerd/daimon/commit/c2047a0ed1a39277a9851c8eaa7a3684c2c884a9))


### Bug Fixes

* **recall:** stamped checkpoints outrank stampless in the supersession frontier ([#241](https://github.com/Daily-Nerd/daimon/issues/241)) ([7b030ad](https://github.com/Daily-Nerd/daimon/commit/7b030ad4147d711b600b305261b6b56e686eb68a))
* **stats:** count status as ops polling, not a deliberate re-read ([#236](https://github.com/Daily-Nerd/daimon/issues/236)) ([e47d0b3](https://github.com/Daily-Nerd/daimon/commit/e47d0b3ed95044f26555b706308cb78897539a83))
* **stats:** reclassify too-short error lines as skips at fold time ([#238](https://github.com/Daily-Nerd/daimon/issues/238)) ([5e1be74](https://github.com/Daily-Nerd/daimon/commit/5e1be746acda6c04e393d6ebb539b78d59675070))

## [0.14.0](https://github.com/Daily-Nerd/daimon/compare/v0.13.0...v0.14.0) (2026-07-11)


### Features

* **briefing:** staleness budget for carried items — last_verified stamp + age-aware brief warning ([#220](https://github.com/Daily-Nerd/daimon/issues/220)) ([08c463a](https://github.com/Daily-Nerd/daimon/commit/08c463ab40ed4987c763ce42e54613535a4798b5))
* **heal:** live progress indicator during re-serialize ([#227](https://github.com/Daily-Nerd/daimon/issues/227)) ([6bc1a93](https://github.com/Daily-Nerd/daimon/commit/6bc1a93377dfb10b858ebe7799a3de548e67c0f3))
* **hosts:** debounced finalizer flushes windsurf session tails after quiet period ([#212](https://github.com/Daily-Nerd/daimon/issues/212)) ([8be2f40](https://github.com/Daily-Nerd/daimon/commit/8be2f403559e8be2935c4de82aa13d51a405470a))
* **serializer:** stamp checkpoints with the resolved backend and model ([#231](https://github.com/Daily-Nerd/daimon/issues/231)) ([276aa70](https://github.com/Daily-Nerd/daimon/commit/276aa706198936e166abbda19ca02195ace30905))
* **skill:** session-start brief pull covers team briefings — closes the windsurf injection gap ([#216](https://github.com/Daily-Nerd/daimon/issues/216)) ([46947b2](https://github.com/Daily-Nerd/daimon/commit/46947b27dbffe36ad85be01d46f429b2dca24f52))
* **store:** stamp checkpoints with the git branch at capture time ([#228](https://github.com/Daily-Nerd/daimon/issues/228)) ([323cb10](https://github.com/Daily-Nerd/daimon/commit/323cb104e63945ee5b813bf8fc469f848087d509))


### Bug Fixes

* **carry:** stop inheriting quote_verified:false — failed-check stamps are fresh-only signals ([#213](https://github.com/Daily-Nerd/daimon/issues/213)) ([2f0c5ee](https://github.com/Daily-Nerd/daimon/commit/2f0c5eee883a40954642ed41af7b105e6ffcb84a))
* **cli:** brief --team renders teammates from the header-only fallback path ([#224](https://github.com/Daily-Nerd/daimon/issues/224)) ([97ddf46](https://github.com/Daily-Nerd/daimon/commit/97ddf46a05fc9cba3143013803b114a655d1a6e7))
* **llm:** log stderr on command-backend empty output and retry it like an empty response ([#226](https://github.com/Daily-Nerd/daimon/issues/226)) ([935f6fe](https://github.com/Daily-Nerd/daimon/commit/935f6fe2782dda92d596dacb0b1e928df492fc09))
* **serializer:** copy-paste quote discipline + unicode punctuation folding in tier-f verify ([#210](https://github.com/Daily-Nerd/daimon/issues/210)) ([9dc646e](https://github.com/Daily-Nerd/daimon/commit/9dc646eff0eb4e6b5ae3b3f14121507c85c7e66a))
* **teamsync:** surface uncommitted pending checkpoints in team status ([#218](https://github.com/Daily-Nerd/daimon/issues/218)) ([fab7ae3](https://github.com/Daily-Nerd/daimon/commit/fab7ae323fc00d4987eb4afc33984b56f56335a1))


### Documentation

* field-tested backend/model matrix — measured, dated, versioned ([#229](https://github.com/Daily-Nerd/daimon/issues/229)) ([0f4b80e](https://github.com/Daily-Nerd/daimon/commit/0f4b80e9061850ed9b7de737e4ea1ca9440d4171))

## [0.13.0](https://github.com/Daily-Nerd/daimon/compare/v0.12.3...v0.13.0) (2026-07-10)


### Features

* **configure:** live progress indicator while --test runs the backend roundtrip ([#183](https://github.com/Daily-Nerd/daimon/issues/183)) ([b857851](https://github.com/Daily-Nerd/daimon/commit/b857851a0b0160678ea2fd551c35dc715a3f1c1a)), closes [#182](https://github.com/Daily-Nerd/daimon/issues/182)
* **heal:** add --force to override the one-retry-ever cap ([#191](https://github.com/Daily-Nerd/daimon/issues/191)) ([2e7c1d6](https://github.com/Daily-Nerd/daimon/commit/2e7c1d6efc50b0a3ae83dd7397b1e33c34f4dd20))
* **hosts:** port the orphan catch-up sweep to Codex session-start ([#189](https://github.com/Daily-Nerd/daimon/issues/189)) ([394e22d](https://github.com/Daily-Nerd/daimon/commit/394e22d0f988cca0a2f2a10b8eb15a64c7011d2e))
* **llm:** command-backend input spec — arg/file prompt delivery ([#190](https://github.com/Daily-Nerd/daimon/issues/190)) ([1382b78](https://github.com/Daily-Nerd/daimon/commit/1382b78aaa3bdffce097ba11678637b43f623f98)), closes [#58](https://github.com/Daily-Nerd/daimon/issues/58)
* **receipts:** prefer vitni keygen for public-key derivation, openssl fallback ([#207](https://github.com/Daily-Nerd/daimon/issues/207)) ([d246c25](https://github.com/Daily-Nerd/daimon/commit/d246c25880392428bc5f024ccc17d3077b764b77))
* **receipts:** signed provenance receipts for checkpoints via vitni (opt-in) ([#205](https://github.com/Daily-Nerd/daimon/issues/205)) ([afa2b92](https://github.com/Daily-Nerd/daimon/commit/afa2b92a49d114f52ff7b3ca35f380505239e810))
* **team:** architect-authored project layout for the team sidecar ([#201](https://github.com/Daily-Nerd/daimon/issues/201)) ([b34811b](https://github.com/Daily-Nerd/daimon/commit/b34811bb4a7b516236f00f8d81e80116e2872195))


### Bug Fixes

* **carry:** add quantity-conflict guard to stop unlinked twin false merge ([#187](https://github.com/Daily-Nerd/daimon/issues/187)) ([8c5939a](https://github.com/Daily-Nerd/daimon/commit/8c5939adc39f91f5aa4ba2d71df01a25363c3f69)), closes [#173](https://github.com/Daily-Nerd/daimon/issues/173)
* **cli:** stop status misreporting quote-verification warnings as a serialize crash ([#195](https://github.com/Daily-Nerd/daimon/issues/195)) ([c8d22ac](https://github.com/Daily-Nerd/daimon/commit/c8d22ac6753d182117fd1bcd558b3771b08ecd0a))
* **hooks:** close the claude --resume capture gap ([#186](https://github.com/Daily-Nerd/daimon/issues/186)) ([c354c7e](https://github.com/Daily-Nerd/daimon/commit/c354c7e193beeb2ae5ff55f117eaf6b189ece7e2)), closes [#185](https://github.com/Daily-Nerd/daimon/issues/185)


### Documentation

* add environment-variable reference ([#198](https://github.com/Daily-Nerd/daimon/issues/198)) ([a3da832](https://github.com/Daily-Nerd/daimon/commit/a3da83258218ec0fe8008816749f0fac6627411c))
* add team memory setup guide ([#199](https://github.com/Daily-Nerd/daimon/issues/199)) ([71d6a70](https://github.com/Daily-Nerd/daimon/commit/71d6a70044904dec6e2fcb8753044ee7cc50295f))

## [0.12.3](https://github.com/Daily-Nerd/daimon/compare/v0.12.2...v0.12.3) (2026-07-09)


### Bug Fixes

* **plugin:** remove duplicate hooks declaration from the plugin manifest ([#180](https://github.com/Daily-Nerd/daimon/issues/180)) ([34903f7](https://github.com/Daily-Nerd/daimon/commit/34903f741b5a01d0b83199e32126df1d33c00dbd)), closes [#179](https://github.com/Daily-Nerd/daimon/issues/179)

## [0.12.2](https://github.com/Daily-Nerd/daimon/compare/v0.12.1...v0.12.2) (2026-07-09)


### Bug Fixes

* **packaging:** complete PyPI metadata — project URLs, classifiers, keywords, license in the wheel ([#177](https://github.com/Daily-Nerd/daimon/issues/177)) ([b05aeae](https://github.com/Daily-Nerd/daimon/commit/b05aeae5d1db7def1aad9cfcc7abdf2220d37e6a)), closes [#176](https://github.com/Daily-Nerd/daimon/issues/176)

## [0.12.1](https://github.com/Daily-Nerd/daimon/compare/v0.12.0...v0.12.1) (2026-07-09)


### Bug Fixes

* **anchor:** derive the drift-scan item walk from the shared schema ([#163](https://github.com/Daily-Nerd/daimon/issues/163)) ([75b0466](https://github.com/Daily-Nerd/daimon/commit/75b04664ae09f17970f3309b29e77405308fc8e1))
* **briefing:** stop fuzzy-withholding live items against id-bearing closed loops ([#156](https://github.com/Daily-Nerd/daimon/issues/156)) ([4977578](https://github.com/Daily-Nerd/daimon/commit/4977578b3bb06f4b17e4ebda3c442d572baf771a))
* **briefing:** validate active_topic quotes in the LLM-render gate ([#164](https://github.com/Daily-Nerd/daimon/issues/164)) ([7e08d30](https://github.com/Daily-Nerd/daimon/commit/7e08d30e6a32ff601e203a1b94694a0cd4aaeae8))
* **carry:** exclude verified reversals from twin candidacy so the freeze cannot erase them ([#169](https://github.com/Daily-Nerd/daimon/issues/169)) ([887b0ed](https://github.com/Daily-Nerd/daimon/commit/887b0ed5f5eacaaf53ec4be11579a1ecafda3134)), closes [#167](https://github.com/Daily-Nerd/daimon/issues/167)
* **carry:** full-vocabulary fallback for link targets that generic subtraction strips ([#170](https://github.com/Daily-Nerd/daimon/issues/170)) ([fce854a](https://github.com/Daily-Nerd/daimon/commit/fce854aa5b3e1ce13a6c602baa189bdf07d6b2ac)), closes [#168](https://github.com/Daily-Nerd/daimon/issues/168)
* **hooks:** ledger in-process capture failures so status and heal see them ([#157](https://github.com/Daily-Nerd/daimon/issues/157)) ([25598d5](https://github.com/Daily-Nerd/daimon/commit/25598d58fc84443f7ff41e2bd93445d41071044d))
* **redact:** close plaintext log seams outside the redaction choke point ([#153](https://github.com/Daily-Nerd/daimon/issues/153)) ([06ac859](https://github.com/Daily-Nerd/daimon/commit/06ac8598441671f30f0e1de2d5bd9f4c611a2dab))
* **store:** make same-second resolution ties content-deterministic ([#154](https://github.com/Daily-Nerd/daimon/issues/154)) ([77d0f3a](https://github.com/Daily-Nerd/daimon/commit/77d0f3a45348b4b30f80857c2cccb65186cd4113))
* **store:** stop cross-project first_seen bleed and tolerate corrupt checkpoint pointers ([#140](https://github.com/Daily-Nerd/daimon/issues/140)) ([77988aa](https://github.com/Daily-Nerd/daimon/commit/77988aa525bd501ee98e99253d51e84dc5653f5d))
* **teamsync:** guard git timeouts into offline degradation + non-interactive credential handling ([#137](https://github.com/Daily-Nerd/daimon/issues/137)) ([d9c3c03](https://github.com/Daily-Nerd/daimon/commit/d9c3c03377729f2d2e378933b21a79e754afc63c))
* **teamsync:** scope sync commit to the author's own directory ([#152](https://github.com/Daily-Nerd/daimon/issues/152)) ([38dbc8a](https://github.com/Daily-Nerd/daimon/commit/38dbc8ab72ccaafa185e185f21e9b2b5168c59fb))


### Documentation

* **readme:** add a recorded demo of the trust loop ([#172](https://github.com/Daily-Nerd/daimon/issues/172)) ([b84fbde](https://github.com/Daily-Nerd/daimon/commit/b84fbde56f59f3354213b620e5686a58b6fa00e5)), closes [#171](https://github.com/Daily-Nerd/daimon/issues/171)

## [0.12.0](https://github.com/Daily-Nerd/daimon/compare/v0.11.1...v0.12.0) (2026-07-08)


### Features

* **serializer:** verify verbatim quotes against transcript at serialize time ([#126](https://github.com/Daily-Nerd/daimon/issues/126)) ([6766046](https://github.com/Daily-Nerd/daimon/commit/6766046d6595e2d3c342baf122bdd2fe1d44350e))


### Bug Fixes

* **briefing:** reject and tolerate null text/quote instead of crashing the render ([#135](https://github.com/Daily-Nerd/daimon/issues/135)) ([fe6637d](https://github.com/Daily-Nerd/daimon/commit/fe6637d309dc75648075a3ac0f4bd1cb30aa1a85))
* **redact:** close secret-leak gaps for quoted keys, token prefixes, and password-only URLs ([#133](https://github.com/Daily-Nerd/daimon/issues/133)) ([f66b64e](https://github.com/Daily-Nerd/daimon/commit/f66b64e53f407171052f79f8ff9dca6e79cd994e))


### Documentation

* add Codecov coverage badge to root and plugin READMEs ([#131](https://github.com/Daily-Nerd/daimon/issues/131)) ([aead9fe](https://github.com/Daily-Nerd/daimon/commit/aead9fe8a2200d39ef5bb8b0ebc72ff1f146751f))

## [0.11.1](https://github.com/Daily-Nerd/daimon/compare/v0.11.0...v0.11.1) (2026-07-08)


### Documentation

* propagate 0.6.0–0.11.0 reality into public docs ([#121](https://github.com/Daily-Nerd/daimon/issues/121)) ([0ceb2b5](https://github.com/Daily-Nerd/daimon/commit/0ceb2b54897ff6303c5ab1c38c70b7bf5531e255))
* split per-host setup guides into docs/hosts/ ([#124](https://github.com/Daily-Nerd/daimon/issues/124)) ([cf6cc55](https://github.com/Daily-Nerd/daimon/commit/cf6cc55c85bb35d112600fb74d2a04133d1be1ba))

## [0.11.0](https://github.com/Daily-Nerd/daimon/compare/v0.10.0...v0.11.0) (2026-07-08)


### Features

* **redact:** extend redaction to remaining transcript-persistence seams ([#116](https://github.com/Daily-Nerd/daimon/issues/116)) ([738ebe6](https://github.com/Daily-Nerd/daimon/commit/738ebe6d4e9e69741ffabd59e0e81d363a26cf9d)), closes [#109](https://github.com/Daily-Nerd/daimon/issues/109)


### Documentation

* **plugin:** rewrite package README for PyPI ([#118](https://github.com/Daily-Nerd/daimon/issues/118)) ([#119](https://github.com/Daily-Nerd/daimon/issues/119)) ([6df3745](https://github.com/Daily-Nerd/daimon/commit/6df374565f87d5f4b99ceda6103b2e046edc0226))

## [0.10.0](https://github.com/Daily-Nerd/daimon/compare/v0.9.0...v0.10.0) (2026-07-07)


### Features

* **brief:** reject path for supersede candidates — hint in annotation, evidence-free reject ([#112](https://github.com/Daily-Nerd/daimon/issues/112)) ([542fdcd](https://github.com/Daily-Nerd/daimon/commit/542fdcd015c8656f77876df3beee14775ae653a0))
* **brief:** withhold event-resolved items — evidence-gated reverify ([#103](https://github.com/Daily-Nerd/daimon/issues/103)) ([#107](https://github.com/Daily-Nerd/daimon/issues/107)) ([0ae8f12](https://github.com/Daily-Nerd/daimon/commit/0ae8f1268859cc64a9459e9d391b77583a057bd8))
* **scar:** add deadend for exact-shape guards on host transcript rows failing silently ([3affcb2](https://github.com/Daily-Nerd/daimon/commit/3affcb24543efd81809a34223541f93bcb059100))
* **schema:** typed supersedes links with candidate events — detect, offer, human confirms ([#110](https://github.com/Daily-Nerd/daimon/issues/110)) ([b45619a](https://github.com/Daily-Nerd/daimon/commit/b45619a5c9c1167a3f95e0a0f84c5249d5cbfcec))
* **stats:** distinguish hook-driven briefings from deliberate re-reads ([#101](https://github.com/Daily-Nerd/daimon/issues/101)) ([aecec3b](https://github.com/Daily-Nerd/daimon/commit/aecec3bcfd726522eb43b36e366368b06dd8dff1))
* **store:** append-only resolution events — supersede-not-delete lifecycle ([#102](https://github.com/Daily-Nerd/daimon/issues/102)) ([#105](https://github.com/Daily-Nerd/daimon/issues/105)) ([304f9c7](https://github.com/Daily-Nerd/daimon/commit/304f9c7fd34d87f1a6f8f7b0a8fab5551d0dfaf4))
* **store:** capture-time secret redaction on checkpoint and event writes ([#108](https://github.com/Daily-Nerd/daimon/issues/108)) ([83cdba0](https://github.com/Daily-Nerd/daimon/commit/83cdba0a10fa94085ac2696b964f125042645895))

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
