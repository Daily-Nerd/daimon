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


def effective_weight(item, item_type: str, now: float) -> float:
    """Ordering key for one checkpoint item. Tolerant of everything a legacy or
    torn checkpoint can throw: missing/malformed first_seen -> neutral recency,
    missing importance -> mid-scale. Higher = surface earlier."""
    rules = TYPE_RULES.get(item_type, _DEFAULT_RULES)
    imp = item.get("importance")
    if not (isinstance(imp, int) and not isinstance(imp, bool) and 1 <= imp <= 10):
        imp = _DEFAULT_IMPORTANCE
    base = imp / 10.0
    age = _age_days(item, now)
    if age is None:
        return base * _NEUTRAL_RECENCY
    weight = base * recency_weight(age) * _type_decay(age, rules)
    if rules["auto_escalation"] and age > rules["expected_lifespan"]:
        weight *= _overdue_boost(age - rules["expected_lifespan"])
    return weight
