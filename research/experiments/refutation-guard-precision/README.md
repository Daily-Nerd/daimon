# Refutation guard precision — negative-control replay

How often does `daimon refute guard` fire on work that has nothing to do with
the refuted approach, and how does that rate move as the ledger grows?

Results: [`RESULTS.md`](RESULTS.md). Machine-readable aggregates:
[`measurements.json`](measurements.json). No production code is changed by this
experiment.

## Why it matters

An active refutation is permission to interrupt someone's work. A single false
fire costs attention. Repeated false fires cost something worse: agents learn
that guard hits are noise, and the one correct fire gets dismissed along with
the rest. For a memory system whose whole argument is trust, a guard that cries
wolf argues against itself.

The v1 study that produced the ledger said its own "usage, false-veto, and
prevention receipts decide whether proactive surfaces expand". This experiment
supplies the first of those numbers.

## Method

The guard has two rails, both exact by design:

- **anchor** — a canonical anchor such as `issue:502`, which also matches a
  bare `#502` appearing anywhere in the query.
- **subject** — the record's subject as a verbatim substring of the query, with
  an eight-character floor.

Refutations are built from this repository's own refuted work. They are then
replayed against checkpoint item texts drawn from **other projects** on the same
machine.

That cross-pairing is the whole design. No unrelated project's session can
legitimately be reviving a daimon-internal refuted approach, so **every fire is
a false positive by construction**. There is no labelling step, no judgement
call, and no way for the experimenter's expectations to influence the count.

Two runs:

1. **fixed** — six refutations on real anchors, including one deliberately
   short generic subject (`add caching`) to probe the subject rail's floor.
2. **scaling** — anchors sampled from this repository's issue-number range,
   evaluated at ledger sizes 6, 20, 60 and 100, over 200 random draws per size
   (`REPLAY_SEEDS` overrides the count).

Two things about the scaling run are corrections to its first version, and both
matter more than the numbers they changed.

**A ledger size is created by seeding that many records.** The first version
seeded all 60 once and filtered hits down to the anchors nominally active at
each size. That filter passed every *subject*-rail hit through unconditionally,
so all 60 subject records were live in the size-6 and size-20 conditions too,
and only the anchor rail actually scaled. It did not corrupt the published
counts, because the subject rail never fired at all, but the design could not
have detected it if it had.

**One seed is one draw, and the draw dominates.** A sample that lands on `#3`
and `#12` measures a different world from one that lands on `#481` and `#522`,
because the corpus's own `#N` tokens are concentrated at the low end. The first
version reported a single seed and read its three points as a slope. At 60
records the spread across draws is roughly sevenfold, larger than the effect the
three points were being used to establish.

The run therefore also reports a **collision surface**: every `#N` in the corpus,
counted by magnitude. `guard` promotes each one to an `issue:N` anchor, so those
tokens are the whole false-positive surface of the anchor rail, and their
distribution is the mechanism behind the spread rather than a story about it.

## Reproducing

```sh
cd research/experiments/refutation-guard-precision
DAIMON_CHECKPOINT_DIR=$(mktemp -d) uv run --project ../../../plugin python replay.py
```

The scratch `DAIMON_CHECKPOINT_DIR` is required: the script seeds synthetic
refutations, and they must not land in a real project bucket.

## Privacy

The script reads the local checkpoint store and never emits item text.
`measurements.json` contains counts only. Local intermediates matching
`*.local.json` are gitignored.
