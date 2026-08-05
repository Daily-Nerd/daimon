# Results — refutation guard precision

Corpus: 824 checkpoint item texts from projects other than this repository, on
one machine. Every fire below is a false positive by construction.

## Fixed ledger, six refutations on real anchors

| Rail | False fires |
| --- | --- |
| anchor | 1 |
| subject | 0 |

1 of 824 texts hit, or 0.12 percent.

The single fire came from a refutation anchored `issue:48` matching a bare `#48`
in an unrelated project's text.

**The subject rail never fired.** That includes the entry deliberately seeded
with the short generic subject `add caching`. The eight-character floor plus the
verbatim-substring requirement makes that rail very conservative on real data.
A false subject hit is constructible by hand, but it did not occur once here.

## Scaling with ledger size

200 random anchor draws per size. Rate is the share of the 824 corpus texts that
received at least one fire.

| Active refutations | Mean | Median | p05–p95 | Full range |
| --- | --- | --- | --- | --- |
| 6 | 0.113% | 0.000% | 0.000–0.485% | 0.000–1.456% |
| 20 | 0.407% | 0.243% | 0.000–1.214% | 0.000–1.942% |
| 60 | 1.209% | 1.092% | 0.364–2.427% | 0.121–3.641% |
| 100 | 2.014% | 2.002% | 0.971–3.519% | 0.728–4.733% |

The subject rail did not fire in any of the 800 runs. Every fire in this table
is the anchor rail.

### The previous slope is withdrawn

The first version of this study reported 0.00 / 0.12 / 0.61 percent at sizes
6 / 20 / 60 from a single seed, and read them as "near one percent per hundred
active refutations". That seed sits near the bottom of the distribution: at 60
records it drew 0.607% against a mean of 1.209%, so the published curve
understated the rate by about half.

**The linearity survives, and is now much better supported.** Per active
refutation the mean rate is 0.0188, 0.0204, 0.0202 and 0.0201 percent at the
four sizes: flat across a sixteenfold range of ledger size. The corrected slope
is about **two percent per hundred active refutations** on this corpus, not one.

**A point estimate is not usable here.** At 60 records the p05–p95 interval
spans 0.364% to 2.427%, nearly sevenfold. Which issues a project's refutations
happen to land on matters more than how many there are, over the range measured.
Any threshold built on this must be built on the interval.

## Reading

The rails are exact and they behave that way. At the sizes an authored ledger
reaches early, the guard is effectively silent on unrelated work, and the
alarm-fatigue concern does not bite yet.

It is a growth problem rather than a precision problem, and the corrected slope
brings the noisy region twice as close. Around two hundred active refutations
the mean puts a false fire in roughly one deliberation in twenty five, which is
where a warning starts training its reader to ignore it.

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
  those requires judgement, which is exactly what this design avoids. It is also
  not assumption-free: it assumes an unrelated project's text cannot legitimately
  match, which is true of the refuted *approach* but is carried entirely by the
  anchor rail's `#N` matching, and `#N` is not project-scoped vocabulary.
- One machine, one corpus, 824 texts. The spread across seeds is a property of
  the anchor draw, not a confidence interval for a population.
- Anchors in the scaling run are sampled uniformly from the issue-number range.
  Real refutations cluster on issues that were actually discussed, and low issue
  numbers collide with unrelated prose more often than high ones, so within-project
  behaviour will differ from this in a direction this design cannot predict.
- Sizes 6 through 100 are measured. The two-hundred-record reading above is an
  extrapolation from a slope that is flat over the measured range, and nothing
  here establishes that it stays flat.

## Follow-on

The field metric is now computable. `_cmd_refute_guard` emitted a single
`refute:guard` usage tag before branching, so a hit and a miss were
indistinguishable in `daimon stats`. It now splits by outcome and rail
(`refute:guard:hit:anchor`, `:hit:subject`, `:miss`), mirroring `_cmd_resolve`,
so the false-veto receipt the v1 study named as its expansion gate can be
produced from field data rather than from replay.
