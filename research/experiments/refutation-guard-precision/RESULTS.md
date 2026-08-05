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
  extrapolation from a slope that is flat over the measured range, and nothing
  here establishes that it stays flat.

## Follow-on

The field metric is now computable. `_cmd_refute_guard` emitted a single
`refute:guard` usage tag before branching, so a hit and a miss were
indistinguishable in `daimon stats`. It now splits by outcome and rail
(`refute:guard:hit:anchor`, `:hit:subject`, `:miss`), mirroring `_cmd_resolve`,
so the false-veto receipt the v1 study named as its expansion gate can be
produced from field data rather than from replay.
