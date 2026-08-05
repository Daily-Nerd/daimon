# Refutation study results — issue #573

Cutoff: `2026-08-05T02:06:37Z`. These results describe the local Daimon
project corpus at that cutoff. Private candidate text, transcript context, and
manual labels remain in gitignored `*.local.*` files.

## Executive verdict

The problem is real, but the proposed checkpoint item class is not supported
by the evidence.

- One rejected design genuinely re-armed, at `re_proposed` severity.
- No rejected design was rerun or rebuilt.
- The pre-registered re-arm threshold did not fire; the sample is
  inconclusive rather than negative.
- Refutation-shaped knowledge has poor independently addressable survival in
  checkpoints.
- The observed re-arm would not have been prevented by prompt-time lexical
  slot priority.
- Naive extraction is far below the activation bar, and no independent blind
  classifier run was completed.

The evidence supports an authored, append-only refutation ledger and a
deliberation-time check. It does not support automatic extraction, permanent
checkpoint carry, or a prompt-only guard as the primary protection.

## Study 1 — historical re-arm

### Corpus reduction

The audit read 55 canonical main-session checkpoints. A broad negative-shape
screen produced 64 unique candidate rows. Human review accepted 18 rows,
which deduplicated to 13 logical refutations.

The accepted set required a subject, verdict, basis, and scope. Preferences,
temporary deferrals, unsupported negative opinions, failed commands, and
implementation invariants were rejected.

The later-context scan found 83 lexical hits. Most were history, compliant
restatements, injected output, or tool output—not genuine opportunities. Three
logical subjects had a later decision opportunity:

1. Front-selection/backfill was reconsidered through a prompt-level gate with
   different seen-state mechanics. This was a legitimate scope change.
2. Echo protection was reconsidered for host-injected memory rather than
   Daimon-origin output. This was a legitimate provenance-scope change.
3. The original `#502 daimon why` receipt design was presented again as the
   only user-facing priority without mentioning its measured rejection. This
   was a genuine `re_proposed` event.

Rates:

- All accepted logical refutations: 1/13, 7.7%, Wilson 95% [1.4%, 33.3%].
- Opportunity-bearing refutations: 1/3, 33.3%, Wilson 95% [6.1%, 79.2%].
- `re_run` or `rebuilt`: 0.

The registered firing rule required two independent re-arms or one
`re_run`/`rebuilt`. It did not fire. The absence rule required at least 15
opportunity-bearing refutations with zero re-arms; that did not fire either.
The correct result is **inconclusive**.

### Curated-graveyard audit

The secondary stratum did not add an independent re-arm:

- “Self-assigned identity rejected twice” conflated two scopes. Scar 0032
  rejects a requested gateway alias as served-model provenance. The later
  self-assigned agent UUID proposal was analogous but was the first proposal
  in that scope.
- The secret-scan method failed twice inside one continuing investigation; it
  was not resurrected by a later session lacking the verdict.
- Revisiting ACB explicitly introduced a new team-memory scope. Under the
  registered rubric that is legitimate reconsideration, not re-arm.

This corrects the pre-study exploratory wording. The graveyard was useful as
a lead generator, but it cannot substitute for scoped evidence.

## Study 2 — protection counterfactual

Of the 13 accepted logical refutations:

- 3 were exactly matchable in the next canonical checkpoint;
- 5 were matchable if Daimon's current fuzzy same-item predicate is allowed;
- 0 were independently matchable at the cutoff.

The fuzzy figure is an upper bound: one match crossed item kinds through a
later rewording. Conversely, zero independently matchable records does not
mean all semantics vanished. The cutoff checkpoint contained a compressed
graveyard paragraph, but individual verdicts were no longer independently
addressable, rankable, or lifecycle-managed.

The observed `#502` re-arm is especially diagnostic:

- its strongest verdict was extracted as a belief, a class that never carries;
- it was absent from the checkpoints that would brief the re-arm session;
- the re-arm transcript contained no injection of the measured verdict;
- the triggering user prompt only requested an open-issue ranking and did not
  mention `#502`, receipts, or carried-item verification.

Therefore a prompt-time lexical guard would have been silent. A permanent
checkpoint item might have appeared in the briefing, but that spends global
attention on every session and still depends on an agent connecting a broad
planning request to the correct record.

The original `no decay + topic slot priority` mechanism does not earn its
place from this counterfactual. The required protection point is later:
compare the approaches an agent is about to recommend against the negative
ledger before presenting the recommendation.

## Study 3 — extraction feasibility

The existing checkpoint extractor plus a broad negative-shape screen is a
useful baseline, not a dedicated classifier:

- proposed rows: 64;
- accepted rows: 18;
- row precision: 28.1%, Wilson 95% [18.6%, 40.1%].

False positives were dominated by ordinary choices between alternatives,
temporary deferrals, generic “measure first” rules, implementation diagnoses,
and summaries that duplicated several earlier refutations.

The registered 40-span independent blind-classifier run was not executed. An
independent judge was not available for this retrospective, and the same agent
designing, generating, and judging records would not be blind. This limitation
is not papered over as a score.

Consequently extraction has not earned write authority. V1 must be authored.
A later independent run can test whether extraction may propose candidates;
it can never activate them by itself.

## Deviation from the registered stop rule

**This section records a protocol deviation, not a finding.** The rule below was
registered before the data were seen. It was not followed. What follows is the
argument for departing from it, offered so a reader can judge the departure
rather than discover it.

The registered gate, verbatim:

- study 1 fires and study 2 fires → full design;
- study 1 fires and study 2 does not → rendering only;
- study 1 does not fire → close with numbers.

Study 1 did not fire (1/13, 7.7%, Wilson 95% [1.4%, 33.3%]; the rule required
two independent re-arms or one `re_run`/`rebuilt`). **Under the rule as
registered, the outcome is "close with numbers."** Shipping a v1 instead is a
departure decided after the results were known.

The argument for departing: the preregistration admitted only three states and
the observed one is a fourth — insufficient recurrence sample, one real verified
failure, and a falsified prevention mechanism. Closing would discard a verified
cross-session failure; approving the full design would overstate weak evidence.
The chosen move is a smaller, instrumented v1: authored ledger, explicit
rendering, exact-anchor lookup, and deliberation-time guarding, whose own usage,
false-veto and prevention receipts decide whether proactive surfaces expand.

That argument may well be right. It is still a post-hoc amendment to a rule
written to prevent exactly this, and the honest label for the evidence behind v1
is **permitted by amendment**, not **preregistered**. A reader who wants the
stricter reading should treat study 1 as closed and v1 as unjustified by it.

Two auditability defects follow from this, both real:

- The gate above is quoted here because it **no longer appears in issue #573**.
  The only surviving copy of the registered stop rule is inside the document that
  departs from it, which is precisely the arrangement preregistration exists to
  prevent. It should be restored to the issue verbatim.
- A fourth state discovered after unblinding cannot be distinguished, from the
  outside, from a state constructed to fit the result. Future studies in this
  repository should register an explicit "none of the above" branch and name in
  advance who may invoke it.
