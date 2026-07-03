# 05 — Initiative: The Interruption Decision

**Status:** 🟢 Investigated (tractable — this is decision theory, not ML research)

**Verdict:** The easiest of Daimon's bets. The RFC over-specifies it with invented thresholds; the real system is a learned policy. No fundamental risk here.

---

## The core question

Not "what did the background worker find?" but **"should I interrupt the human with it *right now*?"** That is a decision-theory problem, not a perception problem.

## The algorithm: expected-value gate

The RFC's threshold table (interrupt if confidence>7 AND relevance>8, escalate by channel) is a hand-tuned expected-value rule:

```
interrupt  iff  E[value to user]  >  E[cost of interruption]

E[value] = relevance × confidence × time_sensitivity
E[cost]  = f(attention_state)      // in-meeting: high; idle: low; focus-mode: very high
```

The four-level channel taxonomy (silent dream-log → chat → Slack DM → DM+mention) is just bucketing the value/cost ratio into escalating intrusiveness.

## The attention model (the gate's key input)

Cost of interruption is dominated by the user's current state, inferred from:
- **Calendar** — in a meeting? focus block? off hours?
- **Activity** — actively typing vs idle for hours?
- **Explicit signals** — `/dnd`, "focus mode", "only P0".

This gates every Level 1–3 interruption. It is the difference between a colleague and a notification spammer.

## Why the RFC version is weak (and the fix)

The thresholds `7, 8, 9` are **invented** — no data produced them. That's fine as placeholders, wrong as a final design. Better:

- **Contextual bandit.** Learn per-user thresholds from their reactions. Dismissed interruption = negative reward; engaged = positive. Over time the policy adapts to *this* human instead of a global guess. This turns the hand-tuned table into a learned one and directly addresses RFC open question Q-RFC-3 ("how do we prevent the agent becoming annoying?").
- **Attention as a budget, not a threshold.** Interruptions draw down a finite daily attention budget; rate-limit even high-value pings. Prevents the failure mode where ten "important" things all fire at once.

## Alternatives

| Approach | Trade-off |
|---|---|
| Static threshold table (RFC) | Simple, ships day 1, but tone-deaf and untuned |
| Contextual bandit | Adapts per user; needs interaction data to learn; cold-start problem |
| Pure rules + explicit user config | Predictable, no surprises; high user-config burden |
| Full RL policy | Overkill; data-hungry; not worth it at this scale |

**Recommendation:** ship the static EV gate for MVP (it's honest about being a placeholder), instrument every interrupt with an engaged/dismissed signal, then upgrade to a contextual bandit once there's data. Make the attention model a hard pre-filter regardless.

## Evidence note

This is the best-understood bet — interruption/notification timing is a mature HCI + decision-theory area (interruptibility research predates LLMs by two decades). The risk is product-design discipline, not algorithmic feasibility. → cross-ref `06-evidence-base.md`.

## The real risk is not technical

The danger isn't "can we decide when to interrupt?" — it's "will users tolerate *any* proactive AI?" That is an adoption/behavioral risk flagged in the stress-test, not an algorithm risk. Mitigate with: silent-by-default (Level 0), opt-in escalation per channel, and a one-tap snooze. → `OPEN-QUESTIONS.md#q-rfc-3`.
