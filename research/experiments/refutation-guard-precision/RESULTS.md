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

**The subject rail did not fire on any of these six probes.** That includes the
entry deliberately seeded with the short generic subject `add caching`. The
eight-character floor plus the verbatim-substring requirement makes that rail
conservative on real data. A false subject hit is constructible by hand, but it
did not occur here.

Six probes is the whole of the subject-rail evidence in this study. The scaling
run below cannot add to it, for a reason given there.

## Scaling with ledger size

200 random anchor draws per size. Rate is the share of the 824 corpus texts that
received at least one fire.

| Active refutations | Mean | Median | p05–p95 | Full range |
| --- | --- | --- | --- | --- |
| 6 | 0.113% | 0.000% | 0.000–0.485% | 0.000–1.456% |
| 20 | 0.407% | 0.243% | 0.000–1.214% | 0.000–1.942% |
| 60 | 1.209% | 1.092% | 0.364–2.427% | 0.121–3.641% |
| 100 | 2.014% | 2.002% | 0.971–3.519% | 0.728–4.733% |

Every fire in this table is the anchor rail.

**The subject rail's silence here carries no information, and an earlier version
of this document reported it as though it did.** The scaling run seeds subjects
as `rejected approach number {i} in the daimon serialize path`. The subject rail
requires the record's whole subject as a verbatim substring of the query, and
this corpus is by construction drawn from projects other than this repository,
so that string cannot occur in it. The 800 runs contain zero informative subject
comparisons, not 800 passing ones. "The subject rail did not fire in any of the
800 runs" has been removed from this section.

This study also has **no positive control for the subject rail**: nothing in it
demonstrates that the rail can fire at all, so a silent rail and a broken rail
are indistinguishable from these results alone. The only evidence the rail works
is a unit test (`plugin/tests/test_refutations.py`). A future revision should
seed at least one subject drawn from the corpus itself, so that a zero is a
measurement rather than a construction.

### The previous slope is withdrawn

The first version of this study reported 0.00 / 0.12 / 0.61 percent at sizes
6 / 20 / 60 from a single seed, and read them as "near one percent per hundred
active refutations". That seed sits near the bottom of the distribution: at 60
records it drew 0.607% against a mean of 1.209%, so the published curve
understated the rate by about half.

Per active refutation the mean rate is 0.0188, 0.0204, 0.0202 and 0.0201 percent
at the four sizes: flat across a sixteenfold range of ledger size. The corrected
slope is about **two percent per hundred active refutations** on this corpus,
not one.

**That flatness is not a measurement of the rails, and an earlier version of this
document read it as one.** The whole table reproduces from a model containing no
guard code, no ledger and no records, using only "sample k anchors from 1-581,
do any of them appear in this text's `#N` set": 0.117 / 0.394 / 1.180 / 1.973
percent against the 0.113 / 0.407 / 1.209 / 2.014 above. For sampling without
replacement against a fixed target set, the hit rate is approximately
`k · |S| / 581` while coverage is low, so it is linear **by construction**. The
flat per-record figures are the sampling design restated, not a property the
guard was measured to have. The claim "the linearity survives, and is now much
better supported" is withdrawn.

What the scaling run does establish is the **constant**, roughly 0.0197 percent
per active refutation on this corpus, and the spread around it. Those are real
and are what the rest of this document uses.

Where the linear reading dies is computable and was not stated: only 83 of the
824 texts contain any in-range `#N` at all, so the rate has a **hard ceiling of
10.073 percent** no ledger size can exceed. Saturation is still mild at the sizes
discussed below (at 200 records the true mean is 3.810 percent against 4.03 from
the linear reading), so the extrapolation there survives; by 300 records the gap
is wider (5.598 percent actual) and past that the linear reading is wrong.

**A point estimate is not usable here.** At 60 records the p05–p95 interval
spans 0.364% to 2.427%, nearly sevenfold. Which issues a project's refutations
happen to land on matters more than how many there are, over the range measured.

## Where the spread comes from

`guard` promotes every `#N` in a query to an `issue:N` anchor, so the corpus's
`#N` tokens are the entire false-positive surface of the anchor rail. An anchor
can only collide with a number some unrelated project already writes down.
That surface is small and very unevenly distributed:

| Issue band | `#N` tokens in corpus |
| --- | --- |
| 1-100 | 45 |
| 101-200 | 37 |
| 201-300 | 19 |
| 301-400 | 0 |
| 401-500 | 1 |
| 501-600 | 3 |

(Bands are mechanical hundreds; the repository's issue numbers stop at 581, and
`measurements.json` omits empty bands rather than writing a zero.)

105 in-range tokens across 824 texts, only **51 distinct values**. 78 percent
sit below 200. The 301-400 band is empty.

This is the mechanism behind the seed spread, and it is measured rather than
assumed. A uniform draw from 1-581 puts about a sixth of its anchors below 100,
where 43 percent of the collisions live; a draw that misses that region scores
near zero. Same ledger size, sevenfold difference in rate.

**The consequence is that ledger size is the wrong control variable.** A
refutation anchored `issue:3` is intrinsically noisy and one anchored `issue:573`
is nearly free, and that is knowable per record at write time, before anything
is activated. A size threshold treats those two as identical.

**It also means this study is pessimistic about real use, by a knowable amount.**
It samples anchors uniformly across the whole issue range. Real refutations in
this repository anchor on recent work, in the 500+ region that contributes 3
tokens to the entire corpus. The two-percent-per-hundred slope is an upper bound
on a distribution real usage does not draw from.

The caveat on that: the sparse high region is not permanent. Those other projects
reference low `#N` because they are younger, and their numbering climbs over
time. The safety of a high anchor decays as the neighbours mature.

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

An earlier version of this section concluded that mechanical activation should
therefore be gated on a ledger-size threshold. **The spread data withdraws that.**
Size predicts the mean and says almost nothing about a given project: the p05-p95
interval at a fixed size spans sevenfold, and the band table above shows why. Two
ledgers of sixty records differ by the anchors they hold, not by their count.

The control that survives is **anchor shape**, and it is strictly better than a
size threshold in three ways. It is per-record rather than per-ledger, so it
discriminates where a threshold cannot. It is knowable at write time, before
activation, rather than after a rate has been observed. And it is cheap: the
riskiest anchors are low `#N`, which is a property of the anchor string itself.

A size threshold would still be a reasonable backstop. It should not be the
primary instrument, and this study is not evidence that it works.

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
  Real refutations cluster on recent issues, so the uniform draw overstates the
  rate. The band table quantifies the direction and rough size of that gap, but
  it does not substitute for measuring an actual authored ledger.
- The band table is a property of **this** corpus at **this** moment. It is 51
  distinct numbers; a handful of new texts referencing a high `#N` would move it.
  Nothing here should be turned into a hard-coded safe range.
- Sizes 6 through 100 are measured. The two-hundred-record reading above is an
  extrapolation. It is checked against the saturating model rather than assumed
  (3.810 percent actual against 4.03 linear), but it remains an extrapolation.
- The scaling table measures the **anchor draw**, not the guard. Its shape is
  fixed by the sampling design, and a model with no guard in it reproduces the
  table. Only the constant and the spread are results.
- **Nothing here measures the subject rail.** The scaling run cannot fire it by
  construction, and there is no positive control. Six probes in the fixed arm are
  the entire subject-rail evidence.

## Follow-on

The field metric is now computable. `_cmd_refute_guard` emitted a single
`refute:guard` usage tag before branching, so a hit and a miss were
indistinguishable in `daimon stats`. It now splits by outcome and rail
(`refute:guard:hit:anchor`, `:hit:subject`, `:miss`), mirroring `_cmd_resolve`,
so the false-veto receipt the v1 study named as its expansion gate can be
produced from field data rather than from replay.
