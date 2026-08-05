# Results — refutation guard precision

Corpus: 822 checkpoint item texts from projects other than this repository, on
one machine. Seed 573. Every fire below is a false positive by construction.

## Fixed ledger, six refutations on real anchors

| Rail | False fires |
| --- | --- |
| anchor | 1 |
| subject | 0 |

1 of 822 texts hit, or 0.12 percent.

The single fire came from a refutation anchored `issue:48` matching a bare `#48`
in an unrelated project's text.

**The subject rail never fired.** That includes the entry deliberately seeded
with the short generic subject `add caching`. The eight-character floor plus the
verbatim-substring requirement makes that rail very conservative on real data.
A false subject hit is constructible by hand, but it did not occur once here.

## Scaling with ledger size

| Active refutations | False fires | Texts hit | Rate |
| --- | --- | --- | --- |
| 6 | 0 | 0 / 822 | 0.00% |
| 20 | 1 | 1 / 822 | 0.12% |
| 60 | 6 | 5 / 822 | 0.61% |

The rate is roughly linear in ledger size, near one percent per hundred active
refutations on this corpus.

## Reading

The rails are exact and they behave that way. At the sizes an authored ledger
reaches early, the guard is effectively silent on unrelated work, and the
alarm-fatigue concern does not bite yet.

It is a growth problem rather than a precision problem. Extrapolating the slope,
a ledger of several hundred active refutations puts a false fire within every
few dozen deliberations, which is where a warning starts training its reader to
ignore it.

That reframes the open question on mechanical activation. The cost of activating
records without a human is not mainly per-record trust. It is **growth rate**,
because activation volume is the input to the curve above. A policy that
activates more records per unit time reaches the noisy region sooner.

Which suggests a threshold rather than a binary: mechanical activation is safe
while the ledger is small, and needs revisiting past a measured size, with the
observed false-fire rate as the trigger.

## What this cannot carry

- The corpus is checkpoint **item texts**, not raw prompts. The guard is invoked
  on a proposal an agent is considering, whose phrasing and `#N` density may
  differ from post-extraction item text.
- Cross-project pairing is the **easy** case, chosen because it needs no
  labelling. Same-project collisions would be more frequent, and classifying
  those requires judgement, which is exactly what this design avoids.
- One machine, one corpus, 822 texts. This is a signal and a slope, not a
  population estimate.
- Anchors in the scaling run are sampled uniformly from the issue-number range.
  Real refutations cluster on issues that were actually discussed, so
  within-project behaviour will differ from this.

## Follow-on

The decisive field metric is still not computable. `_cmd_refute_guard` emits a
single `refute:guard` usage tag before branching, so a hit and a miss are
indistinguishable in `daimon stats`. `_cmd_resolve` already splits its tags by
outcome. Until the guard does the same, the false-veto receipt the v1 study
named as a gate cannot be produced from field data.
