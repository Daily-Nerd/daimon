"""Per-item effective weight (#78): importance x recency x type decay x overdue
escalation. Pure stdlib, deterministic — callers inject `now` (epoch seconds).

The ACB bones (priority_calculator TYPE_RULES, dynamic_relevance_score recency
tiers) without the lifecycle apparatus — see memory `acb-graveyard-mined`. The
score is a RELATIVE ordering key, not a calibrated probability: stale items sink,
overdue open loops surface against other stale items (a fresh item still beats an
escalated one — escalation counters decay, it does not defeat freshness).

Consumers: briefing.build section ordering now; recall ranking later (#125).
"""

from . import store

# Per-type aging rules. decay_rate is linear per day (floored — see _type_decay);
# auto_escalation marks the one type whose staleness means UNRESOLVED, not
# irrelevant: an open question past expected_lifespan grows back weight.
TYPE_RULES = {
    "open_question":   {"decay_rate": 0.010, "expected_lifespan": 14, "auto_escalation": True},
    "recent_decision": {"decay_rate": 0.020, "expected_lifespan": 30, "auto_escalation": False},
    "strong_belief":   {"decay_rate": 0.005, "expected_lifespan": 90, "auto_escalation": False},
    "uncertainty":     {"decay_rate": 0.020, "expected_lifespan": 21, "auto_escalation": False},
    "active_topic":    {"decay_rate": 0.050, "expected_lifespan": 7,  "auto_escalation": False},
}
_DEFAULT_RULES = TYPE_RULES["recent_decision"]

_DEFAULT_IMPORTANCE = 5   # unscored (pre-D-011) items sit mid-scale
_NEUTRAL_RECENCY = 0.5    # unstamped items: between fresh (1.0) and ancient (0.2)
_DECAY_FLOOR = 0.1        # decay never zeroes an item — only ordering may bury it
_ESCALATION_CAP = 3.0     # overdue boost ceiling; keeps weights comparable

# Trust class is a CEILING on authority, not a display label (#408). This is a
# monotone lid on the weight ANY read path may hand an item, keyed on the one
# stable property set at capture: its trust class. It exists to structurally
# kill "recall frequency becomes truth" — the self-reference loop where an
# inferred belief that recurs, scores fresh, or escalates as an open loop drifts
# into the standing of a verified verbatim quote. No accumulation vector
# (importance, recency, overdue escalation, carry, recall frequency) can promote
# a lower-trust item across its lid, because the lid is applied AFTER every one
# of them, in the single function all consumers route through.
#
#   verbatim  -> _ESCALATION_CAP: the full range, overdue boost included. A
#                verified verbatim quote is the top band; nothing clips it.
#   inferred  -> 0.7: below the ~1.0 a fresh, max-importance NON-escalated item
#                reaches, so an inferred item can never sit in the band a
#                verbatim item of the same shape occupies.
#   untagged/ -> _DEFAULT_CEILING: an item that never earned a tag gets no more
#   unknown      authority than an inferred one — the lid, never a promotion.
#
# The ONLY things that lift an item's lid are a change of its trust CLASS, and
# the class changes by exactly two routes: evidence-gated reverify (cli
# _cmd_reverify) or explicit human action. Scoring, carry, and recall READ the
# class; none of them raises it.
TRUST_CEILING: dict[str, float] = {
    "verbatim": _ESCALATION_CAP,
    "inferred": 0.7,
}
_DEFAULT_CEILING = 0.7    # absent or unknown trust tag: the inferred lid


def trust_ceiling(trust: str | None) -> float:
    """Maximum effective_weight the given trust class may ever reach. A missing
    or unrecognized class collapses to the default (inferred) lid — never the
    verbatim band, which must be earned."""
    return TRUST_CEILING.get(trust, _DEFAULT_CEILING) if trust else _DEFAULT_CEILING


def recency_weight(age_days: float) -> float:
    """Tiered recency (ACB dynamic_relevance_score:1121 verbatim): step function,
    not a curve, so ordering is stable within a tier and cheap to reason about."""
    if age_days <= 1:
        return 1.0
    if age_days <= 7:
        return 0.9
    if age_days <= 30:
        return 0.7
    if age_days <= 90:
        return 0.4
    return 0.2


def _type_decay(age_days: float, rules: dict) -> float:
    return max(_DECAY_FLOOR, 1.0 - age_days * rules["decay_rate"])


def _overdue_boost(overdue_days: float) -> float:
    """Non-linear escalation for unresolved open loops: age**1.5 scaled to cross
    1.0 immediately and hit the cap around two months overdue."""
    return min(_ESCALATION_CAP, 1.0 + overdue_days ** 1.5 / 100.0)


_SKEW_TOLERANCE = 300.0   # seconds a stamp may sit in the future (normal
                          # machine-to-machine clock skew) and still count as
                          # fresh; beyond it the stamp is a lie (#31 item 8)


def _age_days(item, now: float) -> float | None:
    epoch = store._created_epoch(item.get("first_seen"))
    if epoch is None:
        return None
    # A stamp further in the future than clock skew explains gets NEUTRAL
    # recency (None), never max: a future-stamped teammate item must not
    # outrank genuinely fresh local work (#31 item 8).
    if epoch - now > _SKEW_TOLERANCE:
        return None
    return max(0.0, (now - epoch) / 86400.0)


_SOFT_CLIP_DELTA = 0.1   # knee at C*(1-delta): below it, nothing changes


def _soft_clip(weight: float, ceiling: float) -> float:
    """Saturate `weight` toward `ceiling` without ever reaching it, preserving
    order (#488).

    #408 applied the lid with min(), which caps correctly and is not injective
    on [C, inf): every accumulation at or above the ceiling collapsed onto the
    same number, so importance stopped separating exactly the items the lid was
    protecting. Measured on the real corpus, fresh inferred items at importance
    8, 9 and 10 all scored 0.700, leaving briefing._by_weight's stable sort to
    fall back on serializer emission order.

    The invariant #408 states is sup(w) <= C. It never required collapsing the
    domain. With K = C(1-delta):

        f(w) = w                              for w <= K
        f(w) = C - (C-K)^2 / (w - 2K + C)     for w > K

    - bounded, strictly: w > K => w-2K+C > C-K > 0, so f(w) < C, approaching C
      from below. Tighter than min(), which ATTAINS C.
    - strictly increasing: f'(w) = (C-K)^2 / (w-2K+C)^2 > 0. Order-preserving.
    - C1-continuous at the knee: f(K) = K and f'(K+) = 1.
    - identity below K, so the great majority of items are untouched and the
      carry_floor comparisons that sit far below the knee cannot move.

    A zero ceiling needs no special case: K collapses to 0, the gap term
    vanishes, and every weight maps to 0.0 — so a trust class with no
    authority silences its items by arithmetic rather than by a guard.
    """
    knee = ceiling * (1.0 - _SOFT_CLIP_DELTA)
    if weight <= knee:
        return weight
    gap = ceiling - knee
    return ceiling - (gap * gap) / (weight - 2.0 * knee + ceiling)


def effective_weight(item, item_type: str, now: float) -> float:
    """Ordering key for one checkpoint item. Tolerant of everything a legacy or
    torn checkpoint can throw: missing/malformed first_seen -> neutral recency,
    missing importance -> mid-scale. Higher = surface earlier."""
    rules = TYPE_RULES.get(item_type, _DEFAULT_RULES)
    imp = item.get("importance")
    if not (isinstance(imp, int) and not isinstance(imp, bool) and 1 <= imp <= 10):
        imp = _DEFAULT_IMPORTANCE
    base = imp / 10.0
    # The trust ceiling is applied LAST, to whatever the accumulation vectors
    # produced (#408): no importance/recency/escalation combination can lift an
    # item past the lid its trust class earns. The cap is a property of the
    # record every caller inherits — not a policy any one of them may skip.
    ceiling = trust_ceiling(item.get("trust"))
    age = _age_days(item, now)
    if age is None:
        return _soft_clip(base * _NEUTRAL_RECENCY, ceiling)
    weight = base * recency_weight(age) * _type_decay(age, rules)
    if rules["auto_escalation"] and age > rules["expected_lifespan"]:
        weight *= _overdue_boost(age - rules["expected_lifespan"])
    return _soft_clip(weight, ceiling)
