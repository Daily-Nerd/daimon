# Refutation class study — issue #573

Pre-design retrospective for deciding whether Daimon needs a protected
negative-knowledge primitive and, if so, which read/write mechanics it earns.
No production code is changed by this experiment.

Results: [`RESULTS.md`](RESULTS.md). Revised design:
[`DESIGN.md`](DESIGN.md). Machine-readable aggregates:
[`measurements.json`](measurements.json).

## Corpus and cutoff

- Project: `Daily-Nerd/daimon`
- Project bucket: the local bucket derived from the repository root
- Durable source: top-level per-session checkpoints in the local Daimon store
- Transcript source: matching top-level JSONL sessions from the local agent host
- Cutoff: `2026-08-05T02:06:37Z`, the checkpoint that first recorded #573
- Subagent transcripts are excluded from the primary analysis. They may be
  reported separately, never mixed into the denominator: a delegated model
  sees different context and is a different recurrence opportunity.

The audit is read-only. Local rows contain private transcript text and are
gitignored. Only aggregate measurements and redacted case descriptions may be
committed.

## Prior exploration — disclosed before the formal runs

The formal studies are not blind to these observations:

1. The local project corpus contains 55 checkpoints before the cutoff.
2. A deliberately broad lexical scan produced 323 unique negative-shaped
   items; a stricter scan left 51 and still included obvious false positives
   (ordinary preferences, rejected alternatives, and generic invariants).
3. At least three recurrence-shaped cases are already known from the recorded
   graveyard: self-assigned identity was rejected twice, one secret-scan method
   was invalid twice, and the abandoned ACB architecture was re-mined after a
   do-not-re-mine instruction.

Those observations establish neither classifier precision nor a re-arm rate.
They do establish that the motivating event is not literally impossible. The
studies below measure the shape and cost honestly instead of rediscovering it.

## Vocabulary

### Logical refutation

One claim or approach whose applicability was narrowed or rejected by evidence.
Repeated checkpoint copies and later summaries are one logical record.

A refutation must name all of:

- **subject** — the approach or claim being rejected;
- **verdict** — what no longer holds;
- **basis** — measurement, deterministic code/world evidence, or explicit
  human ratification;
- **scope** — where the verdict applies.

An ordinary preference, temporary deferral, unsupported opinion, implementation
invariant, or failed command is not a refutation.

### Opportunity

A later human or agent message that discusses the same subject closely enough
that the stored verdict could have changed the next action. Pure history,
quoted/injected Daimon output, tool output, and compliant statements such as
"do not rerun X" are not opportunities.

### Re-arm

An opportunity that crosses one of these ordered severities:

1. `re_proposed` — presents the rejected approach as available without noting
   the verdict;
2. `re_run` — repeats the rejected experiment;
3. `rebuilt` — implements or ships the rejected approach.

A changed scope or a satisfied `revisit_when` condition is not a re-arm; it is
legitimate reconsideration and must be recorded separately.

## Study 1 — historical trap re-arm

### Procedure

1. Harvest negative-shaped checkpoint items at or before the cutoff.
2. Human-grade them into `refutation`, `not_refutation`, or `uncertain` using
   only the item, quote, and named artifact.
3. Deduplicate accepted rows into logical refutations before reading later
   transcripts.
4. For each logical refutation, inspect later main-session messages for the
   same subject and grade every opportunity using the categories above.
5. Report both denominators:
   - all accepted logical refutations;
   - opportunity-bearing logical refutations.
6. Report Wilson 95% intervals. Never count repeated checkpoint copies as
   independent trials.

A secondary, explicitly non-pooled stratum uses the 22 curated graveyard
entries recited at the cutoff. Those entries expose exactly the cross-host gap
#573 is about: several lived in host memory rather than as independently
searchable checkpoint items. They are inspected with the same opportunity and
re-arm rubric, but their recurrence rate is reported separately because a
curated graveyard is selected on consequence and recurrence by construction.

### Decision rule

- The motivating claim **fires** if at least two independent logical
  refutations re-arm, or if one reaches `re_run`/`rebuilt` severity.
- It is **not observed at useful scale** if at least 15 logical refutations have
  later opportunities and none re-arms.
- Fewer than 10 opportunity-bearing refutations with no severe event is
  **inconclusive**, not permission to claim the trap absent.

## Study 2 — protection counterfactual

This is not one generic ranking comparison. Current code has four different
surfaces: deterministic carry, the session briefing, explicit FTS recall, and
prompt-time proactive recall. Explicit recall has no age-decay ranking, while
the other surfaces have different caps and ordering rules.

### Procedure

For every accepted logical refutation and each later opportunity:

1. Determine whether the verdict was present in the latest checkpoint that
   would have briefed that session.
2. Determine whether ordinary proactive recall would have selected it.
3. Replay a **separate guard lane**: topic-match active refutations, then emit
   at most one compact guard without consuming either ordinary recall slot.
4. Grade whether the guard would have been:
   - `preventive` — would have warned before a re-arm;
   - `relevant_nonpreventive` — useful context but no mistake pending;
   - `false_veto` — same vocabulary, materially different approach/scope;
   - `silent` — no match.
5. Carry and briefing survival are reported separately from prompt-time guard
   coverage. A search-index row is not counted as an injection.

### Decision rule

- A protected read path **earns its place** if it would prevent at least one
  observed re-arm and produces zero false vetoes in the graded opportunity
  set.
- A dedicated lane is rejected if it prevents none, or if any false veto is
  not eliminated by adding an explicit scope/revisit condition.
- `no decay` applies only to active records. Candidates may expire; overturned
  records remain audit history but never enter the guard lane.

## Study 3 — extraction-side candidate precision

### Procedure

1. Draw 40 candidate spans from raw main-session transcript messages, one
   candidate per logical subject, stratified across measured-result language,
   deterministic diagnoses, rejections/deferrals, and normative language.
2. A candidate generator sees the raw span and may output a proposed
   `{subject, verdict, basis, scope}` record or `none`.
3. A judge grades only the proposed record plus its source span, without seeing
   the generator rule or the other candidates.
4. Primary metric: precision among proposed candidates. Secondary diagnostics:
   missing subject, unsupported generalization, absent scope, opinion mistaken
   for evidence, and duplicate logical records.

### Decision rule

- Precision >=80% with Wilson lower bound >=60%: extraction may propose
  reviewable candidates.
- Otherwise: authored command only.
- Extraction never activates a refutation. Activation requires either
  mechanically grounded outcome evidence or explicit human ratification,
  regardless of measured classifier precision.

## Architecture hypotheses held until results

These are design hypotheses, not conclusions smuggled into the studies:

- A refutation is a project-level evidence record, not a forever-carried
  checkpoint item.
- `[refuted]` is semantic state, orthogonal to trust class, outcome grounding,
  authorship, and human ratification.
- Append-only lifecycle events should assert, ratify, revise, and overturn a
  refutation; no update silently rewrites history.
- Prompt-time protection should use a bounded guard lane rather than steal an
  ordinary recall slot.
