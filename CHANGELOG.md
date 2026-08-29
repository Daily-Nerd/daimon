# Changelog

All notable changes to daimon are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.35.0](https://github.com/Daily-Nerd/daimon/compare/v0.34.0...v0.35.0) (2026-08-29)


### Features

* **decide:** add the human decision queue, scoped to this project ([#775](https://github.com/Daily-Nerd/daimon/issues/775)) ([8cdbede](https://github.com/Daily-Nerd/daimon/commit/8cdbede503438ccefb96d27767027273df44b221))
* **decide:** count what waits in other projects, never their text ([#777](https://github.com/Daily-Nerd/daimon/issues/777)) ([21628d8](https://github.com/Daily-Nerd/daimon/commit/21628d8ff3b002c8e43e3184f7d55da1483f0e63))
* **requests:** deliver a verdict back to the sender at its next turn boundary ([#807](https://github.com/Daily-Nerd/daimon/issues/807)) ([20813c2](https://github.com/Daily-Nerd/daimon/commit/20813c216a18692ede3c7944c07edbb0a5df597c))
* **requests:** ledger layer for live request delivery ([#757](https://github.com/Daily-Nerd/daimon/issues/757)) ([79f8a0e](https://github.com/Daily-Nerd/daimon/commit/79f8a0eb96a22282049279ac81b38c7419285b15))
* **requests:** live request delivery at the next turn boundary ([#759](https://github.com/Daily-Nerd/daimon/issues/759)) ([db8d786](https://github.com/Daily-Nerd/daimon/commit/db8d786b58dd9605858fe35f6f55d8d0fed6a682))


### Bug Fixes

* **anchor:** refuse to attach to a checkpoint this project does not own ([#790](https://github.com/Daily-Nerd/daimon/issues/790)) ([29cfdc0](https://github.com/Daily-Nerd/daimon/commit/29cfdc05bfe095c0e1f2c474d34480b2dce33628))
* **brief:** decide the global fallback before the read, not after it ([#788](https://github.com/Daily-Nerd/daimon/issues/788)) ([13b0d02](https://github.com/Daily-Nerd/daimon/commit/13b0d023a9f260c81830413a79231bff0d5869fc))
* **briefing:** rename the sender-side request panel heading ([#765](https://github.com/Daily-Nerd/daimon/issues/765)) ([c6c9782](https://github.com/Daily-Nerd/daimon/commit/c6c978204fec3deea23aaee93ba2237f1ecd0ab4))
* **briefing:** stop the session-start injection at the project boundary ([#785](https://github.com/Daily-Nerd/daimon/issues/785)) ([19450c5](https://github.com/Daily-Nerd/daimon/commit/19450c5e58ab8969a65d03de84c1ea1ef8c509dd))
* **echo:** recognise the module and timeout invocation forms ([#780](https://github.com/Daily-Nerd/daimon/issues/780)) ([42724d4](https://github.com/Daily-Nerd/daimon/commit/42724d41fb2a438ee818814d36dfe531869a47f4))
* **echo:** stop reading a markdown code span as a command substitution ([#782](https://github.com/Daily-Nerd/daimon/issues/782)) ([bed62c7](https://github.com/Daily-Nerd/daimon/commit/bed62c7d9d969145edcde882179771a6a9bc8702))
* **receipts:** report only on a checkpoint this project may claim ([#792](https://github.com/Daily-Nerd/daimon/issues/792)) ([7f6daee](https://github.com/Daily-Nerd/daimon/commit/7f6daee0f3bccb1b1dacd310eca3e144a6afaeba))
* **request:** fold the sender lane in request list, like the panels already do ([#799](https://github.com/Daily-Nerd/daimon/issues/799)) ([e56c7b5](https://github.com/Daily-Nerd/daimon/commit/e56c7b517a77df4d8200c164e28808e21dd5546d))
* **requests:** a verdict after a revise now gets its own first look ([#804](https://github.com/Daily-Nerd/daimon/issues/804)) ([9c1c564](https://github.com/Daily-Nerd/daimon/commit/9c1c56468c976e1727ea05595e2d433020232e7d))
* **requests:** the live nudge now names the ask its cap withheld ([#802](https://github.com/Daily-Nerd/daimon/issues/802)) ([edd61a5](https://github.com/Daily-Nerd/daimon/commit/edd61a59d4bcc8f0aca8e4f23d009de53906eea4))
* **ui:** stop nulling the fields the producer actually writes ([#797](https://github.com/Daily-Nerd/daimon/issues/797)) ([05b9c47](https://github.com/Daily-Nerd/daimon/commit/05b9c47ff403c3049bb12dccc836d96adddbadcf))


### Documentation

* **config:** describe the request verbs without the excluded term ([#762](https://github.com/Daily-Nerd/daimon/issues/762)) ([0304bf1](https://github.com/Daily-Nerd/daimon/commit/0304bf1e9942eecbc97e38eb5f082c209c12759d))
* **echo:** record the multiline prose trade at the pattern ([#786](https://github.com/Daily-Nerd/daimon/issues/786)) ([dc33a1c](https://github.com/Daily-Nerd/daimon/commit/dc33a1c57934621934ae1042e13604a282474516))
* **reference:** name the request section for what the reader wants to do ([#770](https://github.com/Daily-Nerd/daimon/issues/770)) ([c25e126](https://github.com/Daily-Nerd/daimon/commit/c25e12663ba1d4287c576e6a401350bcbd36e57d))
* **reference:** restate the request and relation verbs in plain terms ([#763](https://github.com/Daily-Nerd/daimon/issues/763)) ([4d6eba7](https://github.com/Daily-Nerd/daimon/commit/4d6eba71d26d82da8710e406ff53eb4be9a0c0a7))

## [0.34.0](https://github.com/Daily-Nerd/daimon/compare/v0.33.0...v0.34.0) (2026-08-26)


### Features

* **cli:** help epilog points to docs and issue tracker ([#737](https://github.com/Daily-Nerd/daimon/issues/737)) ([e069831](https://github.com/Daily-Nerd/daimon/commit/e0698313334023d0eb0c12dcf771d3a520b89406))
* **serializer:** retries carry the failure diagnostic back to the model ([#744](https://github.com/Daily-Nerd/daimon/issues/744)) ([84e6a59](https://github.com/Daily-Nerd/daimon/commit/84e6a5973eb4835f6023d8ad4c80afafc1c589bb))
* **skill:** daimon-end surfaces the handoff decision ([#734](https://github.com/Daily-Nerd/daimon/issues/734)) ([629d8a6](https://github.com/Daily-Nerd/daimon/commit/629d8a6d7c9f39857dbfb39416d483af7b5aefa2))


### Bug Fixes

* **cli:** brief renders the handoff baton when the project has no checkpoint ([#741](https://github.com/Daily-Nerd/daimon/issues/741)) ([113230c](https://github.com/Daily-Nerd/daimon/commit/113230c557fa56672a625920617022cffae6b5d6))
* **config:** silent misconfiguration reads as healthy across rescue, configure, and the capture gate ([#753](https://github.com/Daily-Nerd/daimon/issues/753)) ([488b13b](https://github.com/Daily-Nerd/daimon/commit/488b13b347769942d4f79209e724cbdfcf781297))
* **serializer:** stats classifies starved rescues and gates rescue effectiveness ([#745](https://github.com/Daily-Nerd/daimon/issues/745)) ([91c6631](https://github.com/Daily-Nerd/daimon/commit/91c66318d6ae6b47f714d3d1b102c659e939c96b))


### Documentation

* state the single-live-checkpoint working-set trade ([#739](https://github.com/Daily-Nerd/daimon/issues/739)) ([7d89c18](https://github.com/Daily-Nerd/daimon/commit/7d89c18d39615dabcd74b31ba9e783b70a4b047e))

## [0.33.0](https://github.com/Daily-Nerd/daimon/compare/v0.32.1...v0.33.0) (2026-08-25)


### Features

* **cli:** status reports a waiting handoff baton ([#730](https://github.com/Daily-Nerd/daimon/issues/730)) ([45bd653](https://github.com/Daily-Nerd/daimon/commit/45bd653e874f60edc024d235a71148a6b7dc0d71))


### Bug Fixes

* **inspector:** why answers recall-surfaced ids through an index fallback ([#729](https://github.com/Daily-Nerd/daimon/issues/729)) ([161543e](https://github.com/Daily-Nerd/daimon/commit/161543e659dbe845c21b5d3df5e1769ab0572c51))
* **relations:** contradiction fold catches item-level cycles ([#727](https://github.com/Daily-Nerd/daimon/issues/727)) ([ebf185e](https://github.com/Daily-Nerd/daimon/commit/ebf185e613cadb0d918618e66f996c8f7db7958c))

## [0.32.1](https://github.com/Daily-Nerd/daimon/compare/v0.32.0...v0.32.1) (2026-08-24)


### Bug Fixes

* **serializer:** strip model-supplied carried_from and first_seen ([#726](https://github.com/Daily-Nerd/daimon/issues/726)) ([cfcce84](https://github.com/Daily-Nerd/daimon/commit/cfcce848191eebaefb74e1e105a8447bc8590e28))
* **serializer:** strip model-supplied item ids; id is code-owned ([#724](https://github.com/Daily-Nerd/daimon/issues/724)) ([18d7b58](https://github.com/Daily-Nerd/daimon/commit/18d7b58dbde2772631c6030e68174cdd852cfa96))


### Documentation

* **site:** Spanish mirror for the cross-project request docs ([#722](https://github.com/Daily-Nerd/daimon/issues/722)) ([a8964ad](https://github.com/Daily-Nerd/daimon/commit/a8964ad6f635c4f440f0a2f0b41862feb9feccda))

## [0.32.0](https://github.com/Daily-Nerd/daimon/compare/v0.31.0...v0.32.0) (2026-08-22)


### Features

* **briefing:** cross-project request inbox and panel — PR 2 of [#694](https://github.com/Daily-Nerd/daimon/issues/694) ([#714](https://github.com/Daily-Nerd/daimon/issues/714)) ([db76fd9](https://github.com/Daily-Nerd/daimon/commit/db76fd9ab3656c662574bd0679a09b0ea2c40160))
* **briefing:** request return path closes the cross-project arc — PR 3 of [#694](https://github.com/Daily-Nerd/daimon/issues/694) ([#716](https://github.com/Daily-Nerd/daimon/issues/716)) ([e4b28e9](https://github.com/Daily-Nerd/daimon/commit/e4b28e9a3e0286803b0952d62d3da806989459bf))
* **render:** ledger record headers style their spans apart ([#711](https://github.com/Daily-Nerd/daimon/issues/711)) ([b3cdfb7](https://github.com/Daily-Nerd/daimon/commit/b3cdfb7ca93d890ce039c47775e1a9b77f881840))
* **render:** lifecycle and report verbs route through render.py ([#712](https://github.com/Daily-Nerd/daimon/issues/712)) ([7f2f16e](https://github.com/Daily-Nerd/daimon/commit/7f2f16eb74f226a61e726171e11c8274dcd86b04))
* **render:** route ledger verb output through render.py ([#710](https://github.com/Daily-Nerd/daimon/issues/710)) ([359ebda](https://github.com/Daily-Nerd/daimon/commit/359ebda09ba318282591a86ab1804b0c7a83e5d9))
* **store:** cross-project requests object — PR 1 of [#694](https://github.com/Daily-Nerd/daimon/issues/694) ([#713](https://github.com/Daily-Nerd/daimon/issues/713)) ([cfab76d](https://github.com/Daily-Nerd/daimon/commit/cfab76dc96399d75b56a9cf7dd095eb007803e58))


### Bug Fixes

* **cli:** forget never counts a ruling hit as an item hit ([#717](https://github.com/Daily-Nerd/daimon/issues/717)) ([c8120dc](https://github.com/Daily-Nerd/daimon/commit/c8120dc527fddd9d32c78731881238f132020478))


### Documentation

* **blog:** publish the 0.31.0 ship-log ([#705](https://github.com/Daily-Nerd/daimon/issues/705)) ([7262dff](https://github.com/Daily-Nerd/daimon/commit/7262dff9b4e52eb7db7fade075b26dd60a3e7202))
* **site:** document the cross-project request family ([#721](https://github.com/Daily-Nerd/daimon/issues/721)) ([08068cd](https://github.com/Daily-Nerd/daimon/commit/08068cd6db2c7ca1238795e585088f5d740da8cf))

## [0.31.0](https://github.com/Daily-Nerd/daimon/compare/v0.30.2...v0.31.0) (2026-08-15)


### Features

* **briefing:** pinned rulings reach every context surface, with the echo loop closed ([#702](https://github.com/Daily-Nerd/daimon/issues/702)) ([f61f10c](https://github.com/Daily-Nerd/daimon/commit/f61f10c9eed0cc1da1830579c75a79d5a400175e))
* **refutations:** pinned rulings, the ledger's positive polarity with a human-only activation ceremony ([#700](https://github.com/Daily-Nerd/daimon/issues/700)) ([9272070](https://github.com/Daily-Nerd/daimon/commit/9272070c7ce8b33f5d6315b86b66f813ad88c7c5))

## [0.30.2](https://github.com/Daily-Nerd/daimon/compare/v0.30.1...v0.30.2) (2026-08-15)


### Bug Fixes

* **cli:** forget by value reaches every declared plaintext field of the refutation ledger ([#699](https://github.com/Daily-Nerd/daimon/issues/699)) ([6f09b93](https://github.com/Daily-Nerd/daimon/commit/6f09b9317743dd80243011cf66626633cb6eee35))


### Documentation

* correct amendments docstring contrasts and restructure daimon-end skill ([#696](https://github.com/Daily-Nerd/daimon/issues/696)) ([26a1a49](https://github.com/Daily-Nerd/daimon/commit/26a1a49c65677f3367dc384e0dd592654f5e5383))

## [0.30.1](https://github.com/Daily-Nerd/daimon/compare/v0.30.0...v0.30.1) (2026-08-15)


### Bug Fixes

* **serializer:** strip model-supplied pinned; the flag is code-owned ([#690](https://github.com/Daily-Nerd/daimon/issues/690)) ([4c81e2b](https://github.com/Daily-Nerd/daimon/commit/4c81e2b27b03403301e5b0f77eea4c7b2e31e30d))


### Documentation

* **site:** reference pages for the viewer and the relations verbs ([#686](https://github.com/Daily-Nerd/daimon/issues/686)) ([9d22658](https://github.com/Daily-Nerd/daimon/commit/9d22658642fc8770519352f88f69c77df3fe4db7))

## [0.30.0](https://github.com/Daily-Nerd/daimon/compare/v0.29.0...v0.30.0) (2026-08-13)


### Features

* **cli:** relations adjudication verbs for the typed relation ledger ([#680](https://github.com/Daily-Nerd/daimon/issues/680)) ([be87378](https://github.com/Daily-Nerd/daimon/commit/be87378aae738f6ab2a2b24719e77826590b39f1))
* **cli:** rich rendering for relations list and show ([1fc6de8](https://github.com/Daily-Nerd/daimon/commit/1fc6de84e236befa327879f7424be7bf6aec1d00)), closes [#678](https://github.com/Daily-Nerd/daimon/issues/678)
* **relations:** typed relation ledger in shadow mode ([#679](https://github.com/Daily-Nerd/daimon/issues/679)) ([2e803a0](https://github.com/Daily-Nerd/daimon/commit/2e803a0c069b93d5d4264d8305c4ee94a5f8b875))
* **store:** declare the human event-source set and instrument reverify ([#669](https://github.com/Daily-Nerd/daimon/issues/669)) ([45559a1](https://github.com/Daily-Nerd/daimon/commit/45559a1bfdc330618f01ea4f4c0ce6ad72a8b112))
* **store:** stamp the project directory name on checkpoints at write time ([#673](https://github.com/Daily-Nerd/daimon/issues/673)) ([bfd4cb9](https://github.com/Daily-Nerd/daimon/commit/bfd4cb9a88c73389dbcb5e94466e46ed7695d5e7))
* **ui:** history lane renders confirmed relations inside the entry page ([#682](https://github.com/Daily-Nerd/daimon/issues/682)) ([8b75f2f](https://github.com/Daily-Nerd/daimon/commit/8b75f2f5ca8511173e43c2d8ea443689b3a90909))
* **ui:** ledger screen and session page for the viewer ([#675](https://github.com/Daily-Nerd/daimon/issues/675)) ([589a9d2](https://github.com/Daily-Nerd/daimon/commit/589a9d2e8494685f953cb9bcef3c99e50a65b4ac))
* **ui:** local read-only viewer with search-as-recall and why detail ([#671](https://github.com/Daily-Nerd/daimon/issues/671)) ([d5f8628](https://github.com/Daily-Nerd/daimon/commit/d5f862874753970f977931a34932c729c1e0439d))
* **ui:** print view sets one checkpoint as a printed record ([#677](https://github.com/Daily-Nerd/daimon/issues/677)) ([44f6f7a](https://github.com/Daily-Nerd/daimon/commit/44f6f7aaade093093d9c53f9fd7e6dbc4af85cd0))
* **ui:** refutations lane, ladder-anchored diff, and check strip for the viewer ([#676](https://github.com/Daily-Nerd/daimon/issues/676)) ([23d1e60](https://github.com/Daily-Nerd/daimon/commit/23d1e60420f491cbc3c79df6e6e33501c8d0e0bf))


### Bug Fixes

* **llm:** let the fallback rescue an unparseable response ([#666](https://github.com/Daily-Nerd/daimon/issues/666)) ([e78527c](https://github.com/Daily-Nerd/daimon/commit/e78527cf42255ba1a441e6ba58b2859fd5c6c688)), closes [#663](https://github.com/Daily-Nerd/daimon/issues/663)
* **privacy:** fold compatibility forms before the scrub looks at them ([#661](https://github.com/Daily-Nerd/daimon/issues/661)) ([a5f3a64](https://github.com/Daily-Nerd/daimon/commit/a5f3a642360e8495552067e916c56e63fc2e316b))
* **receipts:** name the real reason a checkpoint is unsigned ([#654](https://github.com/Daily-Nerd/daimon/issues/654)) ([5d0910f](https://github.com/Daily-Nerd/daimon/commit/5d0910f986317e17dab80bde7356ac93614ba2cc))
* **serializer:** restate the extraction contract after the transcript ([#665](https://github.com/Daily-Nerd/daimon/issues/665)) ([dd7b748](https://github.com/Daily-Nerd/daimon/commit/dd7b7483a4fff742d6e2d05d306c34d3db2c49ae)), closes [#664](https://github.com/Daily-Nerd/daimon/issues/664)


### Documentation

* **blog:** publish the 0.29.0 ship-log ([#657](https://github.com/Daily-Nerd/daimon/issues/657)) ([765bac0](https://github.com/Daily-Nerd/daimon/commit/765bac0c5133b8d7cb75ff6f55c1805514cd0e25))

## [0.29.0](https://github.com/Daily-Nerd/daimon/compare/v0.28.0...v0.29.0) (2026-08-08)


### Features

* **refutations:** add evidence-cited negative knowledge ledger ([#575](https://github.com/Daily-Nerd/daimon/issues/575)) ([9fc303e](https://github.com/Daily-Nerd/daimon/commit/9fc303e87b53d9ac71f1b4891f298f86126349eb)), closes [#573](https://github.com/Daily-Nerd/daimon/issues/573) [#576](https://github.com/Daily-Nerd/daimon/issues/576) [#578](https://github.com/Daily-Nerd/daimon/issues/578) [#579](https://github.com/Daily-Nerd/daimon/issues/579) [#645](https://github.com/Daily-Nerd/daimon/issues/645) [#646](https://github.com/Daily-Nerd/daimon/issues/646) [#647](https://github.com/Daily-Nerd/daimon/issues/647)


### Bug Fixes

* **ledger:** resolve Codex rollout stems to the spawned session id ([#642](https://github.com/Daily-Nerd/daimon/issues/642)) ([053e20b](https://github.com/Daily-Nerd/daimon/commit/053e20b9db9ed03c10fe09323594d686f1a6ed98))
* **skill:** move the plugin skills where hosts look, and teach daimon why ([#649](https://github.com/Daily-Nerd/daimon/issues/649)) ([c151741](https://github.com/Daily-Nerd/daimon/commit/c151741e19e35d03c3952d36944199c67154dbf6))


### Documentation

* **reference:** add a claims page with re-run methods ([#641](https://github.com/Daily-Nerd/daimon/issues/641)) ([b8945cb](https://github.com/Daily-Nerd/daimon/commit/b8945cb2f085b1f6b0df2079df4ce7fadc8a5ab1))
* regroup the CLI reference by user intent and drop adjudication wording ([#638](https://github.com/Daily-Nerd/daimon/issues/638)) ([e27dec9](https://github.com/Daily-Nerd/daimon/commit/e27dec935037a4632e5551e7c9f22994e0ba0061))
* **website:** name the reader's problem before the first proof claim ([#636](https://github.com/Daily-Nerd/daimon/issues/636)) ([3d5cacf](https://github.com/Daily-Nerd/daimon/commit/3d5cacf3819dced64d022ee729d8881a8a0f9c76))

## [0.28.0](https://github.com/Daily-Nerd/daimon/compare/v0.27.0...v0.28.0) (2026-08-08)


### Features

* **website:** show-the-receipts landing rework ([#627](https://github.com/Daily-Nerd/daimon/issues/627)) ([2c35964](https://github.com/Daily-Nerd/daimon/commit/2c359644d435765fad06b227f114113a0277a2f5))


### Bug Fixes

* **surfaces:** stop persisting item text under logs/*.log ([#632](https://github.com/Daily-Nerd/daimon/issues/632)) ([1fe3674](https://github.com/Daily-Nerd/daimon/commit/1fe3674b1174bd00bf6489c5f71562e223f04171))


### Documentation

* publish downgrade-rate baseline, fix stale Codex/Windsurf status, docs accuracy wave ([#625](https://github.com/Daily-Nerd/daimon/issues/625)) ([0b92140](https://github.com/Daily-Nerd/daimon/commit/0b921406348fd38f64ef2a65a5d8df42fd4bc579))
* state the quote check's real matching contract ([#630](https://github.com/Daily-Nerd/daimon/issues/630)) ([d73cadd](https://github.com/Daily-Nerd/daimon/commit/d73caddd0af7323fabfb1a15a6f58c138d5064d1))

## [0.27.0](https://github.com/Daily-Nerd/daimon/compare/v0.26.0...v0.27.0) (2026-08-07)


### Features

* **audit:** read-only tombstone residue audit — daimon audit privacy ([#602](https://github.com/Daily-Nerd/daimon/issues/602)) ([cb96945](https://github.com/Daily-Nerd/daimon/commit/cb96945e5d2888fc32004a038afda31e47f1f711))
* **cli:** add trust inspector ([#597](https://github.com/Daily-Nerd/daimon/issues/597)) ([7624c3f](https://github.com/Daily-Nerd/daimon/commit/7624c3fc688514fad67873664bb4e89d7531e447)), closes [#502](https://github.com/Daily-Nerd/daimon/issues/502)
* **cli:** warn when handoff supersedes an unconsumed baton ([#572](https://github.com/Daily-Nerd/daimon/issues/572)) ([417d13d](https://github.com/Daily-Nerd/daimon/commit/417d13d8035bf2281b906896ac5f7f4376c9e640))
* **hooks:** make Codex SessionEnd the primary capture event ([#589](https://github.com/Daily-Nerd/daimon/issues/589)) ([742f587](https://github.com/Daily-Nerd/daimon/commit/742f587b97b1bd40a968c5b7292551edd3757b15))
* **research:** run-attributed merge-fidelity instrument ([#569](https://github.com/Daily-Nerd/daimon/issues/569)) ([284c208](https://github.com/Daily-Nerd/daimon/commit/284c2088829573069d375b33a7b41889eb14e845))
* **store:** surface registry — new stores must declare a delete strategy ([#606](https://github.com/Daily-Nerd/daimon/issues/606)) ([a9069b0](https://github.com/Daily-Nerd/daimon/commit/a9069b06e4fd1442b8425a8ece45f0a70b3036c8))
* **team:** propagate forget tombstones, suppressing by default ([#611](https://github.com/Daily-Nerd/daimon/issues/611)) ([fd14885](https://github.com/Daily-Nerd/daimon/commit/fd148858d392f7c9170fb4502fd24f8a8f97fa11))
* **trust:** add durable quote provenance ([#595](https://github.com/Daily-Nerd/daimon/issues/595)) ([1bbd4db](https://github.com/Daily-Nerd/daimon/commit/1bbd4db3382faaa1faa446eaa5fbc844c7d8a814)), closes [#594](https://github.com/Daily-Nerd/daimon/issues/594)
* **website:** landing page polish — 4-size type scale, spacing grid, CTA targets ([#615](https://github.com/Daily-Nerd/daimon/issues/615)) ([abbfe08](https://github.com/Daily-Nerd/daimon/commit/abbfe080ee993a8300f75390b89a00c10d837c9b))


### Bug Fixes

* **forget:** reach every plaintext surface the project owns ([#584](https://github.com/Daily-Nerd/daimon/issues/584)) ([5ecc9b7](https://github.com/Daily-Nerd/daimon/commit/5ecc9b75eccec64b6e56b9a4ed059f28b286e1bf))
* **forget:** reach quote/scene/links/topic fields and redact the event ledger ([#603](https://github.com/Daily-Nerd/daimon/issues/603)) ([739a366](https://github.com/Daily-Nerd/daimon/commit/739a366fa15775081ac0574fe110d9148b2c20c8))
* **serializer:** stop feeding daimon's own output to the extractor ([#582](https://github.com/Daily-Nerd/daimon/issues/582)) ([25a8139](https://github.com/Daily-Nerd/daimon/commit/25a8139c71a16db1351cad4ded412841fa356524))
* **serializer:** thread the capture clock into quote verification stamps ([#610](https://github.com/Daily-Nerd/daimon/issues/610)) ([e9fdf41](https://github.com/Daily-Nerd/daimon/commit/e9fdf41556412c885f9c2b593b779ac569f81b80))
* **store:** bring the serializer crash log inside the deletion contract ([#605](https://github.com/Daily-Nerd/daimon/issues/605)) ([#617](https://github.com/Daily-Nerd/daimon/issues/617)) ([c7e95d0](https://github.com/Daily-Nerd/daimon/commit/c7e95d0ee2d2e6c91ddd8d5f8f40bf9da547b365))
* **team:** forget scrubs the author's own team-mirror copies ([#608](https://github.com/Daily-Nerd/daimon/issues/608)) ([ecb7bb1](https://github.com/Daily-Nerd/daimon/commit/ecb7bb104d7dbdbfc4f676e39d3ca9672982542f))
* **transcript:** parse Codex 0.147.0 item_completed rollout events ([#623](https://github.com/Daily-Nerd/daimon/issues/623)) ([4222243](https://github.com/Daily-Nerd/daimon/commit/4222243e40352691b957d6e3242b5aed25e8c851))
* **transcript:** recognise daimon invoked through a shell wrapper ([#590](https://github.com/Daily-Nerd/daimon/issues/590)) ([8ddbda2](https://github.com/Daily-Nerd/daimon/commit/8ddbda2bd4e7f4e1bdf59eaed90dd29d7e0f1808))
* **windsurf:** bring the adapter's own transcript store inside the deletion contract ([#609](https://github.com/Daily-Nerd/daimon/issues/609)) ([1bcf685](https://github.com/Daily-Nerd/daimon/commit/1bcf68540b2682daad0dc0851570147922703a1c))


### Documentation

* **research:** correct the linearity reading and the subject-rail zero ([#588](https://github.com/Daily-Nerd/daimon/issues/588)) ([a721460](https://github.com/Daily-Nerd/daimon/commit/a72146051b7c296b21cc3b9c6f3cf15dbff3e36b))
* **scars:** candidate — residue tests must not enumerate via the scrubber's own walk ([#621](https://github.com/Daily-Nerd/daimon/issues/621)) ([ecf1995](https://github.com/Daily-Nerd/daimon/commit/ecf19950fc646e144e1a5d6bab939ad63bd4ba83))

## [0.26.0](https://github.com/Daily-Nerd/daimon/compare/v0.25.0...v0.26.0) (2026-08-04)


### Features

* **brief:** render the HANDOFF baton as a rich panel ([#567](https://github.com/Daily-Nerd/daimon/issues/567)) ([af8e969](https://github.com/Daily-Nerd/daimon/commit/af8e96974931a82273b648526bd789d84d4363c0))
* **serializer:** report which predicate rejected a checkpoint ([#558](https://github.com/Daily-Nerd/daimon/issues/558)) ([75a67c9](https://github.com/Daily-Nerd/daimon/commit/75a67c93e3e5fa7912c9ffb3f451079f82309522))
* **status:** report Claude Code plugin staleness ([#559](https://github.com/Daily-Nerd/daimon/issues/559)) ([05d1103](https://github.com/Daily-Nerd/daimon/commit/05d11030cb9f13341a371a7ee425bef9716656b7))


### Bug Fixes

* **brief:** clear serialize heartbeat when the run ends ([#565](https://github.com/Daily-Nerd/daimon/issues/565)) ([4b77f79](https://github.com/Daily-Nerd/daimon/commit/4b77f79bc962bb5cceaaf4854a6095efa9431b88))
* **serializer:** guarantee the validation resample a minimum budget ([#556](https://github.com/Daily-Nerd/daimon/issues/556)) ([0415d41](https://github.com/Daily-Nerd/daimon/commit/0415d41391bdcdce11087154cda280d1a7dbb777))
* **stats:** say when agent credit became recordable ([#563](https://github.com/Daily-Nerd/daimon/issues/563)) ([66c6c58](https://github.com/Daily-Nerd/daimon/commit/66c6c5874970e9eb424b85a23f7f5fb3611d44d7))

## [0.25.0](https://github.com/Daily-Nerd/daimon/compare/v0.24.0...v0.25.0) (2026-08-04)


### ⚠ BREAKING CHANGES

* **llm:** a `claude` binary present on PATH is no longer adopted implicitly as the command backend. Serializing sends the full session transcript to the configured CLI, so that CLI must now be named: set `DAIMON_LLM_COMMAND` to the invocation that should receive it, or set `DAIMON_LLM_BACKEND=claude-cli` to opt into the built-in zero-config preset. Installs that already set either variable are unaffected. The two configurations that previously resolved a command from PATH alone were `auto` installs with no API key and the litellm rescue path; both now fail with an error naming the remedy instead of using a binary nobody chose.

### Bug Fixes

* **brief:** say when a serialize is in flight instead of silently briefing one session behind ([#542](https://github.com/Daily-Nerd/daimon/issues/542)) ([09e67d6](https://github.com/Daily-Nerd/daimon/commit/09e67d613ae8f270aad71d3c0e0123ab801bb3fc))
* **hooks:** the orphan sweep must not spawn a serialize that is already running ([#548](https://github.com/Daily-Nerd/daimon/issues/548)) ([1a57dc6](https://github.com/Daily-Nerd/daimon/commit/1a57dc659d9b266ad6d992f18ebca6e7955b3f7f))
* **llm:** a claude on PATH is no longer adopted implicitly as the command backend ([#552](https://github.com/Daily-Nerd/daimon/issues/552)) ([a161239](https://github.com/Daily-Nerd/daimon/commit/a1612397e67226987cb6f55fa737701179af8167))
* **llm:** deadline expiry logs as budget expiry, not backend failure ([#539](https://github.com/Daily-Nerd/daimon/issues/539)) ([9a281e8](https://github.com/Daily-Nerd/daimon/commit/9a281e89cd51edebfb81b5a7a18412771784cbfe))
* **llm:** give a command backend a rescue path by splitting DAIMON_LLM_COMMAND's overload ([#547](https://github.com/Daily-Nerd/daimon/issues/547)) ([856cc9c](https://github.com/Daily-Nerd/daimon/commit/856cc9c48056a047dc967616cc7070f6b52695d4))
* **llm:** PATH presence is not consent — name the CLI that receives the transcript ([#549](https://github.com/Daily-Nerd/daimon/issues/549)) ([0f1cbd6](https://github.com/Daily-Nerd/daimon/commit/0f1cbd6ce5bd7ee4ed5832468baf90b75cf8fae0))
* **release:** version the PATH-consent change as breaking, not as a patch ([#551](https://github.com/Daily-Nerd/daimon/issues/551)) ([0d3d1a8](https://github.com/Daily-Nerd/daimon/commit/0d3d1a8b673c1319ef5f3947bbf0669c6c4a7744))

## [0.24.0](https://github.com/Daily-Nerd/daimon/compare/v0.23.0...v0.24.0) (2026-08-03)


### Features

* **cli:** daimon handoff — an authored baton that leads the next briefing ([#524](https://github.com/Daily-Nerd/daimon/issues/524)) ([877f9f5](https://github.com/Daily-Nerd/daimon/commit/877f9f55512ae1945cc1700897c522dfa466b362))
* **cli:** record audit-quotes usage ([#506](https://github.com/Daily-Nerd/daimon/issues/506)) ([d473e01](https://github.com/Daily-Nerd/daimon/commit/d473e0141203c277f6358f830013b3fa4d70c19a))
* **llm:** stream litellm-backend responses so the timeout guards the connection, not the completion length ([#538](https://github.com/Daily-Nerd/daimon/issues/538)) ([247bbc4](https://github.com/Daily-Nerd/daimon/commit/247bbc45321a490494f4eff5801186e10c842fd8))
* **serializer:** decisions carry their because — stated reasoning rides the item ([#528](https://github.com/Daily-Nerd/daimon/issues/528)) ([3e5133e](https://github.com/Daily-Nerd/daimon/commit/3e5133ead749291cc0d5e13bd01b2f08d970e584))
* **serializer:** stamp extraction_version on checkpoints and surface generations in stats ([#520](https://github.com/Daily-Nerd/daimon/issues/520)) ([d8a9ac3](https://github.com/Daily-Nerd/daimon/commit/d8a9ac37f2aa39cf7727df53bcf1a34976bacf86))
* **worldcheck:** render confirmed items as ground, not only contradicted ones as quicksand ([#526](https://github.com/Daily-Nerd/daimon/issues/526)) ([2557671](https://github.com/Daily-Nerd/daimon/commit/255767139bf571ce9248f1d33eed13f72bf69aaa))


### Bug Fixes

* **cli:** audit-quotes verifies carried quotes against their origin transcript ([#508](https://github.com/Daily-Nerd/daimon/issues/508)) ([40b47e9](https://github.com/Daily-Nerd/daimon/commit/40b47e9ed02c3d34c3f31063a66e215b5c0826d0))
* **cli:** write-checkpoint downgrades unverifiable verbatim to inferred ([#515](https://github.com/Daily-Nerd/daimon/issues/515)) ([f68933e](https://github.com/Daily-Nerd/daimon/commit/f68933e8fd81ba8958a2ec741ea7d2981a49ef7c))
* **redact:** route status log tails through redact_text and contain anchor paths ([#519](https://github.com/Daily-Nerd/daimon/issues/519)) ([7b839df](https://github.com/Daily-Nerd/daimon/commit/7b839dfd4df6a3da8f041f879ebad32ffac25cda))
* **serializer:** tool output of daimon's own commands is not a witness ([#518](https://github.com/Daily-Nerd/daimon/issues/518)) ([7d1054a](https://github.com/Daily-Nerd/daimon/commit/7d1054a61cc25c69c6c516186e0168c426f9a9a0))
* **serializer:** treat a redaction marker as a fragment boundary, not as text to delete ([#509](https://github.com/Daily-Nerd/daimon/issues/509)) ([d93f531](https://github.com/Daily-Nerd/daimon/commit/d93f53129fef4bb66da3af0de4a8740cfc5f0199))


### Documentation

* **site:** CLI reference page and the undocumented briefing annotations ([#530](https://github.com/Daily-Nerd/daimon/issues/530)) ([3025ee3](https://github.com/Daily-Nerd/daimon/commit/3025ee3edecd1958e9e9181fe607a5b1a30309bf))

## [0.23.0](https://github.com/Daily-Nerd/daimon/compare/v0.22.1...v0.23.0) (2026-08-01)


### Features

* **briefing:** render pending agent claims, credit resolutions by source, teach the verbs ([#486](https://github.com/Daily-Nerd/daimon/issues/486)) ([ca27d5d](https://github.com/Daily-Nerd/daimon/commit/ca27d5d80c65bd001923b7fd4d5bdf3e51502f69))
* **cli:** agent-initiated resolve — evidence-gated claim that never withholds ([#482](https://github.com/Daily-Nerd/daimon/issues/482)) ([c07c291](https://github.com/Daily-Nerd/daimon/commit/c07c2914d1c9a18de4a484e91ff5ed3ae765ff4b))
* **cli:** loop handles — ids inline and a daimon loops listing ([#481](https://github.com/Daily-Nerd/daimon/issues/481)) ([30744e6](https://github.com/Daily-Nerd/daimon/commit/30744e69bf29df5d17bcdaccdf291d6d89af55d4))
* **provenance:** stamp the served model from the LLM response ([#460](https://github.com/Daily-Nerd/daimon/issues/460)) ([7ceb422](https://github.com/Daily-Nerd/daimon/commit/7ceb422fba2b60a13fa92e055ef0f8d5330855f4))
* **research:** general replay A/B instrument for recall-scoring hypotheses — [#470](https://github.com/Daily-Nerd/daimon/issues/470) measured and refuted ([#472](https://github.com/Daily-Nerd/daimon/issues/472)) ([114a2fa](https://github.com/Daily-Nerd/daimon/commit/114a2fac305f21a2345c9a55b675c96749701518))
* **research:** stance-gate recall variant — [#483](https://github.com/Daily-Nerd/daimon/issues/483) measured and refuted ([#484](https://github.com/Daily-Nerd/daimon/issues/484)) ([f6a83d1](https://github.com/Daily-Nerd/daimon/commit/f6a83d12b3bf39d73ff6e7f6243c05f52ca70eb7))
* **serializer:** verify pending agent resolve claims against the transcript ([#485](https://github.com/Daily-Nerd/daimon/issues/485)) ([81eea67](https://github.com/Daily-Nerd/daimon/commit/81eea67daea0f0a7d387bc2adc481e9d0dae2919))
* **worldcheck:** receipt-validity spot-check class — sampled vitni verification of origin receipts ([#467](https://github.com/Daily-Nerd/daimon/issues/467)) ([4d18129](https://github.com/Daily-Nerd/daimon/commit/4d18129389715a7ffc75c3e968ccaebcd87b7fe6))


### Bug Fixes

* **bench:** verify the served model — run-pinned receipts, producer-verified cache ([#463](https://github.com/Daily-Nerd/daimon/issues/463)) ([54b67f8](https://github.com/Daily-Nerd/daimon/commit/54b67f8f4751a6ee19d7078b6acaaf2d88422cc7))
* **briefing:** cut inside the sections when they overflow the budget ([#493](https://github.com/Daily-Nerd/daimon/issues/493)) ([770ba93](https://github.com/Daily-Nerd/daimon/commit/770ba9324dd2602b540b84c1b3388f0e7648382b))
* **cli:** origin cooldown is a budget, not a session-wide ban ([#501](https://github.com/Daily-Nerd/daimon/issues/501)) ([c29c5da](https://github.com/Daily-Nerd/daimon/commit/c29c5da06bc5a7eec0567dc7157d1fb0df554557))
* **cli:** the age gate no longer waves stale questions through ([#499](https://github.com/Daily-Nerd/daimon/issues/499)) ([93306cf](https://github.com/Daily-Nerd/daimon/commit/93306cf4d1e6de3d39c0b3fe1ab2158212349fba))
* **policy:** mint item ids at 12 hex, not 6 ([#494](https://github.com/Daily-Nerd/daimon/issues/494)) ([9a4be4b](https://github.com/Daily-Nerd/daimon/commit/9a4be4b447b1ea57e903b27c550b5b3c4a4b5242))
* **recall:** age-gate stale injections — &gt;7d items need a 3-term match to fire ([#455](https://github.com/Daily-Nerd/daimon/issues/455)) ([71cd2af](https://github.com/Daily-Nerd/daimon/commit/71cd2afed5b488d27294c37681a29c53deaa3084))
* **recall:** count matched terms on word boundaries, not substrings ([#498](https://github.com/Daily-Nerd/daimon/issues/498)) ([c42ad43](https://github.com/Daily-Nerd/daimon/commit/c42ad437f322336b3a75ffcc843f20e8b63edfdc))
* **scoring:** soft-clip the trust ceiling so it bounds order instead of erasing it ([#497](https://github.com/Daily-Nerd/daimon/issues/497)) ([5e8f1d0](https://github.com/Daily-Nerd/daimon/commit/5e8f1d03252e1a818aee227db3549bdd879151fe))
* **serializer:** verify the chunk-cache producer — served-model envelope, replay attribution ([#466](https://github.com/Daily-Nerd/daimon/issues/466)) ([4598f27](https://github.com/Daily-Nerd/daimon/commit/4598f27d8d2481ecb159152e63ac5e54d542db89))
* **stats:** say when no rescue path exists, instead of reporting it as unused ([#479](https://github.com/Daily-Nerd/daimon/issues/479)) ([e66caf6](https://github.com/Daily-Nerd/daimon/commit/e66caf67e56f532b3293f2e7db5e4ac08c390169))
* **stats:** stop mixing host populations in the retention ratio ([#478](https://github.com/Daily-Nerd/daimon/issues/478)) ([431b151](https://github.com/Daily-Nerd/daimon/commit/431b151546876f5111ac4b990ed5030be53c2336))
* **status:** show why a serialize failed, and stop the backend log erasing itself ([#476](https://github.com/Daily-Nerd/daimon/issues/476)) ([f057ed1](https://github.com/Daily-Nerd/daimon/commit/f057ed12df347caeb8ff2073e6bf522c22a8389c))
* **test:** reset llm fallback flag per test — file-pair runs leaked _fallback_used ([#462](https://github.com/Daily-Nerd/daimon/issues/462)) ([3788109](https://github.com/Daily-Nerd/daimon/commit/3788109a52a3373cd68e4f2b9f3c8c17b87c83e8))


### Documentation

* **brand:** wire the phyllotaxis brand mark through README and docs site ([#469](https://github.com/Daily-Nerd/daimon/issues/469)) ([7080adb](https://github.com/Daily-Nerd/daimon/commit/7080adb4f249f31a4d4ae8839335b586fd82f3f4))

## [0.22.1](https://github.com/Daily-Nerd/daimon/compare/v0.22.0...v0.22.1) (2026-07-30)


### Bug Fixes

* **recall:** content-key the injection dedup — cross-origin duplicates were 15.5% of injections ([#454](https://github.com/Daily-Nerd/daimon/issues/454)) ([981e1f1](https://github.com/Daily-Nerd/daimon/commit/981e1f1c5b5e2227d51820e9a0c51bc9b184a692))
* **recall:** skip machine prompts in the recall-inject hook — 37.9% of injections fired at prompts no human wrote ([#453](https://github.com/Daily-Nerd/daimon/issues/453)) ([2300c25](https://github.com/Daily-Nerd/daimon/commit/2300c250121416d4177f7185044b91d9a5dfde5a))


### Documentation

* **blog:** origin-bound corroboration ship story, en + es ([#448](https://github.com/Daily-Nerd/daimon/issues/448)) ([7d00c56](https://github.com/Daily-Nerd/daimon/commit/7d00c5682197664cdd6d07ad3dd18f1ccc20f795))
* **site:** blog post on negative knowledge for coding agents (en + es) ([#396](https://github.com/Daily-Nerd/daimon/issues/396)) ([952cd6d](https://github.com/Daily-Nerd/daimon/commit/952cd6dd5003db0be25b8440a88737d084efe72c))

## [0.22.0](https://github.com/Daily-Nerd/daimon/compare/v0.21.0...v0.22.0) (2026-07-30)


### Features

* **briefing:** render the corroboration badge — separate axis, never a trust class ([#268](https://github.com/Daily-Nerd/daimon/issues/268) slice 4) ([#445](https://github.com/Daily-Nerd/daimon/issues/445)) ([05e30bb](https://github.com/Daily-Nerd/daimon/commit/05e30bb499d369876f4b72642530d0df9e6960a2))
* **capture:** corroboration events — namespaced ledger rows, derived counts ([#268](https://github.com/Daily-Nerd/daimon/issues/268) slice 3) ([#444](https://github.com/Daily-Nerd/daimon/issues/444)) ([51657f2](https://github.com/Daily-Nerd/daimon/commit/51657f2d86a25d3c86b790993a099970a987e124))
* **carry:** corroboration predicate — origin-proven independence, pure ([#268](https://github.com/Daily-Nerd/daimon/issues/268) slice 2) ([#443](https://github.com/Daily-Nerd/daimon/issues/443)) ([2cd6994](https://github.com/Daily-Nerd/daimon/commit/2cd699430cec817853ed194bd48ea7638674169c))
* **policy:** bind item origin at write time ([#268](https://github.com/Daily-Nerd/daimon/issues/268) slice 1) ([#442](https://github.com/Daily-Nerd/daimon/issues/442)) ([ba7202a](https://github.com/Daily-Nerd/daimon/commit/ba7202aac3edb51aa19e0ef817c9b75ae652f7cc))
* **policy:** extract admission pipeline into pure policy module and gate writes on the kill switch ([#428](https://github.com/Daily-Nerd/daimon/issues/428)) ([a17b644](https://github.com/Daily-Nerd/daimon/commit/a17b644c10bf6a457d7c6c48a6bfeadc8aa92e5a))
* **scoring:** trust class as an authority ceiling — recall frequency can never promote belief ([#413](https://github.com/Daily-Nerd/daimon/issues/413)) ([4f02e2e](https://github.com/Daily-Nerd/daimon/commit/4f02e2efc250b1929b38e48bd2e71008dc731102))
* **serializer:** ground Spanish outcome claims (bilingual lexicon) ([#411](https://github.com/Daily-Nerd/daimon/issues/411)) ([0870dd6](https://github.com/Daily-Nerd/daimon/commit/0870dd698d79b16850e6c8674f345f2c9c43a9ca))
* **serializer:** prefer quote spans that preserve temporal detail ([#417](https://github.com/Daily-Nerd/daimon/issues/417)) ([91ce3b0](https://github.com/Daily-Nerd/daimon/commit/91ce3b0ed13daf0bc728ab1ce39d33405c0917f5))
* **store:** value-keyed forget tombstone arc — canonicalization, re-capture gate, hit accounting ([#412](https://github.com/Daily-Nerd/daimon/issues/412)) ([9daf310](https://github.com/Daily-Nerd/daimon/commit/9daf310eabab2b6eeefffbe31a64e9fb080ee81a))
* **teamsync:** gate inbound team content — scope, redaction, forget, and trust now apply on read ([#430](https://github.com/Daily-Nerd/daimon/issues/430)) ([947ddac](https://github.com/Daily-Nerd/daimon/commit/947ddacaa62906eabd88b647a4c04633a389ff39))
* **worldcheck:** extend spot-check to file-exists, branch-state, and dependency-version claim classes ([#399](https://github.com/Daily-Nerd/daimon/issues/399)) ([ecb7faf](https://github.com/Daily-Nerd/daimon/commit/ecb7fafefa817f0726f46b221ddd4c7f4400a30a))


### Bug Fixes

* **cli:** supersede-candidate emission skips values in the forget ledger ([#425](https://github.com/Daily-Nerd/daimon/issues/425)) ([f60f2d9](https://github.com/Daily-Nerd/daimon/commit/f60f2d9e2dcaa5fc75065d649d7f150d011b6818))
* **forget:** append tombstone before rewrite and remove by value so sibling ids cannot survive ([#424](https://github.com/Daily-Nerd/daimon/issues/424)) ([d3556e1](https://github.com/Daily-Nerd/daimon/commit/d3556e12db08e37da7d880a377e0cf9e462ec66c))
* **forget:** purge the serializer chunk cache so deletion covers the pre-redaction window ([#429](https://github.com/Daily-Nerd/daimon/issues/429)) ([7f73080](https://github.com/Daily-Nerd/daimon/commit/7f730804e1f1bcf4aa9c865ce511b9ccdee7ec89))
* **recall:** resolve forgotten tombstones by content key so sibling-id copies in historical sessions cannot survive rebuild ([#435](https://github.com/Daily-Nerd/daimon/issues/435)) ([59271b6](https://github.com/Daily-Nerd/daimon/commit/59271b6070ce0bf850a78c17498b3f1e0717065a))
* **serializer:** quote verification no longer accepts daimon's own injected text as witness ([#441](https://github.com/Daily-Nerd/daimon/issues/441)) ([4710e17](https://github.com/Daily-Nerd/daimon/commit/4710e1725dae7030d0ad0ca5b88ac303ea618344))


### Documentation

* **pitch:** align PITCH.md with shipped reality ([#393](https://github.com/Daily-Nerd/daimon/issues/393)) ([522a217](https://github.com/Daily-Nerd/daimon/commit/522a217bba088fa4f65324b0b79ad90b50e6df5b))

## [0.21.0](https://github.com/Daily-Nerd/daimon/compare/v0.20.0...v0.21.0) (2026-07-29)


### Features

* **cli:** configure --init — guided wizard for env and team setup ([#386](https://github.com/Daily-Nerd/daimon/issues/386)) ([11d26d4](https://github.com/Daily-Nerd/daimon/commit/11d26d45df9aeda59f065011698ff87ed2e3450b))
* **hooks:** heartbeat liveness for hung-serialize detection ([#382](https://github.com/Daily-Nerd/daimon/issues/382)) ([292bd73](https://github.com/Daily-Nerd/daimon/commit/292bd734d078cb752dac4bc65edc4ef0c1de4692))
* **scars:** qualification filter for the harvest — obligations or no candidate ([#385](https://github.com/Daily-Nerd/daimon/issues/385)) ([42e1a8a](https://github.com/Daily-Nerd/daimon/commit/42e1a8a311dc1723680acf63f07dab767b1a6e5c))
* **serializer:** deterministic auto-pin for hard-imperative constraints ([#381](https://github.com/Daily-Nerd/daimon/issues/381)) ([5c22f72](https://github.com/Daily-Nerd/daimon/commit/5c22f7215e0d16a14f226809e574c786ab036efc))
* **team:** scope-routed dual-write across multiple remotes ([#388](https://github.com/Daily-Nerd/daimon/issues/388)) ([b388191](https://github.com/Daily-Nerd/daimon/commit/b388191104d071c031cfc2e1ebcf7a9e325c77d8))


### Bug Fixes

* **llm:** preflight no longer kills the no-key rescue on explicit litellm ([#389](https://github.com/Daily-Nerd/daimon/issues/389)) ([b92308f](https://github.com/Daily-Nerd/daimon/commit/b92308f6c339921f015688b53d0d809764bdb00d))


### Documentation

* **scars:** give scar harvest the receipts-grade discovery path ([#384](https://github.com/Daily-Nerd/daimon/issues/384)) ([7261fd8](https://github.com/Daily-Nerd/daimon/commit/7261fd8640c7c1477245effb85edaddb239b0c60))
* **scars:** promote the i18n .md-link landmine to active (0024) ([#373](https://github.com/Daily-Nerd/daimon/issues/373)) ([35062e8](https://github.com/Daily-Nerd/daimon/commit/35062e8f07552fd5417ada5f6c9c638b78aa98cb))
* **site:** concept blog post on verbatim vs inferred trust classes (en + es) ([#371](https://github.com/Daily-Nerd/daimon/issues/371)) ([d4da62b](https://github.com/Daily-Nerd/daimon/commit/d4da62bc581fb859a2ff4cdd8f31619b744d7560))

## [0.20.0](https://github.com/Daily-Nerd/daimon/compare/v0.19.0...v0.20.0) (2026-07-22)


### Features

* **briefing:** deterministic external-state spot-check for carried claims ([#366](https://github.com/Daily-Nerd/daimon/issues/366)) ([75b05b8](https://github.com/Daily-Nerd/daimon/commit/75b05b8cd2e9f8717ea1dc5e13d926bd1885897f))
* **heal:** perspective-diverse extraction as the escalation tier for failed serializes ([#363](https://github.com/Daily-Nerd/daimon/issues/363)) ([d9c3251](https://github.com/Daily-Nerd/daimon/commit/d9c325116dcf6109fb3b39475a16ace6aadf5303))
* **serializer:** bind verbatim items to transcript message ids at capture time ([#361](https://github.com/Daily-Nerd/daimon/issues/361)) ([ef90d61](https://github.com/Daily-Nerd/daimon/commit/ef90d615fa0e16e3049e0907f300f8ffe87e8e91))
* **serializer:** ground success claims in tool results and exit codes at capture time ([#362](https://github.com/Daily-Nerd/daimon/issues/362)) ([268134b](https://github.com/Daily-Nerd/daimon/commit/268134bd956d77241b6ea0ececb4371e9bec4c04))
* **skill:** teach the MCP tool surface as a first-class alternative to CLI commands ([#356](https://github.com/Daily-Nerd/daimon/issues/356)) ([9ffc400](https://github.com/Daily-Nerd/daimon/commit/9ffc400ce2a8260896c59e2962e3382527c60ce7))


### Documentation

* **site:** blog release post for 0.19.0 (en + es) ([#353](https://github.com/Daily-Nerd/daimon/issues/353)) ([a32819b](https://github.com/Daily-Nerd/daimon/commit/a32819b5d7e2496fe19c6691b1495e79907d5ce8))

## [0.19.0](https://github.com/Daily-Nerd/daimon/compare/v0.18.0...v0.19.0) (2026-07-21)


### Features

* **cli:** daimon forget — item removal with a tombstone event ([#322](https://github.com/Daily-Nerd/daimon/issues/322)) ([3fa223c](https://github.com/Daily-Nerd/daimon/commit/3fa223c9a1bb81e0d78220acb9ff2ff3c8418f21))
* **docs:** Docusaurus documentation site with GitHub Pages deploy ([#325](https://github.com/Daily-Nerd/daimon/issues/325)) ([df6b8d2](https://github.com/Daily-Nerd/daimon/commit/df6b8d25fb0c50efbfe7d08ef2f272ebf595c9e1))
* **mcp:** opt-in read-only MCP server over stdio ([#347](https://github.com/Daily-Nerd/daimon/issues/347)) ([1159f46](https://github.com/Daily-Nerd/daimon/commit/1159f46727b92268c1f2b3df5481da5ad9c9c6da))
* **serializer:** content-addressed chunk-extraction cache ([#48](https://github.com/Daily-Nerd/daimon/issues/48) slice 1) ([#348](https://github.com/Daily-Nerd/daimon/issues/348)) ([0e5db26](https://github.com/Daily-Nerd/daimon/commit/0e5db263d1328f6a145cb5c7efa2e3db1cfaad06))
* **site:** blog — canonical home for announcements, with bilingual inaugural post ([#338](https://github.com/Daily-Nerd/daimon/issues/338)) ([88740fa](https://github.com/Daily-Nerd/daimon/commit/88740fa944c6790efe37e538338717a9a85f165b))


### Bug Fixes

* **llm:** give the command fallback its own deadline budget ([#346](https://github.com/Daily-Nerd/daimon/issues/346)) ([fe89f79](https://github.com/Daily-Nerd/daimon/commit/fe89f797eca0de9e94a963186aced0539751e3aa))
* **site:** Spanish landing page — wire translate() into custom index ([#340](https://github.com/Daily-Nerd/daimon/issues/340)) ([40268bb](https://github.com/Daily-Nerd/daimon/commit/40268bbf8d1ab192d65f073faaf2ff02a71d3b71))
* **stats:** scope the stale-hook warning to auto-brief-capable hosts ([#350](https://github.com/Daily-Nerd/daimon/issues/350)) ([947cfd9](https://github.com/Daily-Nerd/daimon/commit/947cfd9da8d67167c41ab3a5245eae124d38e8ed))


### Documentation

* **readme:** slim README to pitch, install, and docs-site links ([#327](https://github.com/Daily-Nerd/daimon/issues/327)) ([3d87d4d](https://github.com/Daily-Nerd/daimon/commit/3d87d4d7820e8a0a74eb156d52e73507b7e1e0e3))
* **site:** complete Spanish coverage — all remaining doc pages translated ([#336](https://github.com/Daily-Nerd/daimon/issues/336)) ([f0ba938](https://github.com/Daily-Nerd/daimon/commit/f0ba938d8b9d97f653db0c39554c255ddca6312f))
* **site:** concepts pages — trust classes, carry, receipts, item lifecycle ([#329](https://github.com/Daily-Nerd/daimon/issues/329)) ([7fc22c8](https://github.com/Daily-Nerd/daimon/commit/7fc22c88a46814fa79f1b62415d12b2a9ce77bda))
* **site:** curation — return internal-history pages to the repo ([#332](https://github.com/Daily-Nerd/daimon/issues/332)) ([5c22419](https://github.com/Daily-Nerd/daimon/commit/5c2241914da7dda72d4ff779584bdacc22fd16c0))
* **site:** prose pass on host pages — user tone, no repo-internal references ([#333](https://github.com/Daily-Nerd/daimon/issues/333)) ([f545271](https://github.com/Daily-Nerd/daimon/commit/f545271345f0b36c9286adddab419652cb6b3dec))
* **site:** quickstart — install to first briefing in one page ([#328](https://github.com/Daily-Nerd/daimon/issues/328)) ([4787e3a](https://github.com/Daily-Nerd/daimon/commit/4787e3a5be93a7a1b7ec7d7d037284ce91f2fa6d))
* **site:** Spanish first pass — quickstart and concepts pages ([#334](https://github.com/Daily-Nerd/daimon/issues/334)) ([0285665](https://github.com/Daily-Nerd/daimon/commit/0285665bbbfb0b1c444fbd0161cfdf656caed432))

## [0.18.0](https://github.com/Daily-Nerd/daimon/compare/v0.17.0...v0.18.0) (2026-07-17)


### Features

* **serializer:** opt-in per-item scene traces, indexed for recall ([#318](https://github.com/Daily-Nerd/daimon/issues/318)) ([e85b181](https://github.com/Daily-Nerd/daimon/commit/e85b181fd991c402af13833c77d7fd3746c32f27))


### Bug Fixes

* **bench:** key and pin the scene-traces flag so runs never share cache lanes ([#320](https://github.com/Daily-Nerd/daimon/issues/320)) ([7e2fbd4](https://github.com/Daily-Nerd/daimon/commit/7e2fbd405934822d272999b685055b52d862aa21))
* **llm:** extract fenced JSON from anywhere in a model response ([#313](https://github.com/Daily-Nerd/daimon/issues/313)) ([38cfb93](https://github.com/Daily-Nerd/daimon/commit/38cfb937150992c8426a1711f346e6e25276bac7))
* **serializer:** add a per-run nonce to retry markers so gateway caches cannot pin a bad response through heal ([#315](https://github.com/Daily-Nerd/daimon/issues/315)) ([d756bfb](https://github.com/Daily-Nerd/daimon/commit/d756bfb994cbc62b4c418be99be8bb5dfec1f95f))
* **serializer:** scale the deadline to the wave plan and persist chunk partials across heals ([#316](https://github.com/Daily-Nerd/daimon/issues/316)) ([6e18433](https://github.com/Daily-Nerd/daimon/commit/6e184332359f19ba0e48866d1827e667e1aab314))
* **stats:** collapse adjacent-duplicate result lines in the capture fold ([#308](https://github.com/Daily-Nerd/daimon/issues/308)) ([a16c0f4](https://github.com/Daily-Nerd/daimon/commit/a16c0f4c3ff4ac107d4c5a8ac7b28865b2a2e47f))

## [0.17.0](https://github.com/Daily-Nerd/daimon/compare/v0.16.1...v0.17.0) (2026-07-16)


### Features

* **cli:** add resolve --dry-run — look before the write ([#306](https://github.com/Daily-Nerd/daimon/issues/306)) ([4318911](https://github.com/Daily-Nerd/daimon/commit/43189112caa4cef395a5fd1fb9d8c0df015ce878)), closes [#304](https://github.com/Daily-Nerd/daimon/issues/304)
* **serializer:** capture external-artifact identifiers verbatim (D-014) ([#290](https://github.com/Daily-Nerd/daimon/issues/290)) ([e4e7a1e](https://github.com/Daily-Nerd/daimon/commit/e4e7a1eb407612a08b5cb1e7a69c4a2a35c6cf32)), closes [#287](https://github.com/Daily-Nerd/daimon/issues/287)


### Bug Fixes

* **hooks:** skip sweep_orphans candidates the ledger already shows failed ([#301](https://github.com/Daily-Nerd/daimon/issues/301)) ([1574519](https://github.com/Daily-Nerd/daimon/commit/157451925a3f64c5c8576c9d3a34f8b3ec770649))
* **llm:** enforce the total deadline mid-response, not just between calls ([#302](https://github.com/Daily-Nerd/daimon/issues/302)) ([c2e3d36](https://github.com/Daily-Nerd/daimon/commit/c2e3d36c2bd85e2f462dbad7b4710770dd06e515)), closes [#298](https://github.com/Daily-Nerd/daimon/issues/298)
* **recall:** dedupe results by item content — one hit per distinct item, newest checkpoint wins ([#289](https://github.com/Daily-Nerd/daimon/issues/289)) ([b9afc5b](https://github.com/Daily-Nerd/daimon/commit/b9afc5b40a0a399597d2518215f3e4eb9c2f8db4)), closes [#288](https://github.com/Daily-Nerd/daimon/issues/288)
* **serializer:** strip code-owned provenance keys at the parse boundary ([#295](https://github.com/Daily-Nerd/daimon/issues/295)) ([2ca8237](https://github.com/Daily-Nerd/daimon/commit/2ca8237bf201729a54a45b2dcd32ad457ea58505)), closes [#292](https://github.com/Daily-Nerd/daimon/issues/292)
* **stats:** instrument resolve — usage counters for resolve, resolve:ambiguous, resolve:no-match ([#305](https://github.com/Daily-Nerd/daimon/issues/305)) ([e21633c](https://github.com/Daily-Nerd/daimon/commit/e21633c4794fd296667bac5bc0d45460a3d92b59)), closes [#303](https://github.com/Daily-Nerd/daimon/issues/303)
* **status:** give the format-drift warning a direction sense ([#297](https://github.com/Daily-Nerd/daimon/issues/297)) ([e4bc89d](https://github.com/Daily-Nerd/daimon/commit/e4bc89db7c121bea35f6285a1fc9f8dcd3871e53)), closes [#294](https://github.com/Daily-Nerd/daimon/issues/294)
* **store:** make transcript_unchanged version-aware ([#296](https://github.com/Daily-Nerd/daimon/issues/296)) ([d649371](https://github.com/Daily-Nerd/daimon/commit/d649371f960f0ff05b58ab354b9e9177c5a75a9a)), closes [#293](https://github.com/Daily-Nerd/daimon/issues/293)

## [0.16.1](https://github.com/Daily-Nerd/daimon/compare/v0.16.0...v0.16.1) (2026-07-14)


### Bug Fixes

* **config:** raise default DAIMON_TIMEOUT 120 -&gt; 420 to fit real serialize calls ([#285](https://github.com/Daily-Nerd/daimon/issues/285)) ([14a689c](https://github.com/Daily-Nerd/daimon/commit/14a689cac24f4ab65086a2d2d6ea19467dbef9db)), closes [#284](https://github.com/Daily-Nerd/daimon/issues/284)
* **team:** default-closed per-remote scope gate on checkpoint dual-write ([#281](https://github.com/Daily-Nerd/daimon/issues/281)) ([c255c76](https://github.com/Daily-Nerd/daimon/commit/c255c76d09685c10e817bec1642fc1d2bb49e762)), closes [#279](https://github.com/Daily-Nerd/daimon/issues/279)


### Documentation

* **demo:** fix the flag-rerun recipe — delete the checkpoint before re-serializing ([#286](https://github.com/Daily-Nerd/daimon/issues/286)) ([c31cdf5](https://github.com/Daily-Nerd/daimon/commit/c31cdf5a1580c2f8fdbe2ffe2d3d1f068afc48eb)), closes [#283](https://github.com/Daily-Nerd/daimon/issues/283)

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
