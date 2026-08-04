"""#536 frozen classification rubric — pure functions, zero I/O.

Implements, verbatim, the pre-registered protocol on issue #536:
normalization is casefold + whitespace collapse; fuzzy is
difflib.SequenceMatcher ratio at threshold 0.55 with greedy pairing in
deterministic (sorted) order; native items classify in fixed pass order
survived -> reworded -> freeze-explained -> emitted-new; union items
left without a descendant are true-lost. The containment check and the
cross-chunk twin rate are diagnostics, not decision-bearing.

The thresholds are frozen. Nothing here may grow a tuning knob.
"""
from dataclasses import dataclass, field
from difflib import SequenceMatcher

FUZZY_THRESHOLD = 0.55
CONTAINMENT_SLICE = 60


@dataclass(frozen=True)
class UnionItem:
    text: str
    chunk: int
    trust: str = "untagged"


@dataclass(frozen=True)
class NativeItem:
    text: str
    trust: str = "untagged"


@dataclass
class Result:
    survived: int = 0
    reworded: int = 0
    freeze_explained: int = 0
    emitted_new: int = 0
    true_lost: int = 0
    union_total: int = 0
    native_total: int = 0
    emitted_new_by_trust: dict = field(default_factory=dict)
    true_lost_by_trust: dict = field(default_factory=dict)
    containment_flags: int = 0
    twin_items: int = 0
    lost_texts: list = field(default_factory=list)
    emitted_new_texts: list = field(default_factory=list)


def normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _bump(d: dict, key: str) -> None:
    d[key] = d.get(key, 0) + 1


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson 95% score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


def classify(native, union, prev_verbatim) -> Result:
    """Classify one join. `native`: NativeItem list (non-carried items of
    the checkpoint). `union`: UnionItem list (all chunk-partial items of
    the producing run). `prev_verbatim`: normalized-or-raw text list of
    the PREVIOUS checkpoint's verbatim items (freeze-explanation pool)."""
    r = Result(native_total=len(native), union_total=len(union))

    # deterministic order everywhere: sort by normalized text
    natives = sorted(native, key=lambda i: normalize(i.text))
    unions = sorted(union, key=lambda i: normalize(i.text))
    union_free = {id(u): u for u in unions}
    prev_pool = sorted(normalize(t) for t in prev_verbatim)
    prev_free = list(prev_pool)

    unmatched_native = []

    # pass 1: survived (exact normalized match, one-to-one)
    for n in natives:
        nn = normalize(n.text)
        hit = next((u for u in unions
                    if id(u) in union_free and normalize(u.text) == nn), None)
        if hit is not None:
            r.survived += 1
            del union_free[id(hit)]
        else:
            unmatched_native.append(n)

    # pass 2: reworded (best fuzzy pair >= threshold, greedy in sorted order)
    still_unmatched = []
    for n in unmatched_native:
        nn = normalize(n.text)
        best, best_ratio = None, FUZZY_THRESHOLD
        for u in unions:
            if id(u) not in union_free:
                continue
            score = _ratio(nn, normalize(u.text))
            if score > best_ratio or (score == best_ratio and best is None
                                      and score >= FUZZY_THRESHOLD):
                best, best_ratio = u, score
        if best is not None and best_ratio >= FUZZY_THRESHOLD:
            r.reworded += 1
            del union_free[id(best)]
        else:
            still_unmatched.append(n)

    # pass 3: freeze-explained (exact or fuzzy vs previous checkpoint's
    # verbatim items; carry's reconsolidation freeze rewrites native twins)
    for n in still_unmatched:
        nn = normalize(n.text)
        hit_idx = None
        for i, p in enumerate(prev_free):
            if p == nn or _ratio(nn, p) >= FUZZY_THRESHOLD:
                hit_idx = i
                break
        if hit_idx is not None:
            r.freeze_explained += 1
            prev_free.pop(hit_idx)
        else:
            # pass 4: emitted-new
            r.emitted_new += 1
            _bump(r.emitted_new_by_trust, n.trust)
            r.emitted_new_texts.append(n.text)

    # union items with no descendant after passes 1-2: true-lost
    for u in unions:
        if id(u) in union_free:
            r.true_lost += 1
            _bump(r.true_lost_by_trust, u.trust)
            r.lost_texts.append(u.text)

    # diagnostic: containment (split/combine artifacts one-to-one miscounts).
    # Checked over unpaired natives x unpaired unions only.
    unpaired_native_texts = [normalize(n.text) for n in still_unmatched]
    for u in unions:
        if id(u) not in union_free:
            continue
        un = normalize(u.text)
        for nn in unpaired_native_texts:
            short, long_ = (un, nn) if len(un) <= len(nn) else (nn, un)
            if len(short) >= CONTAINMENT_SLICE and (
                    short[:CONTAINMENT_SLICE] in long_
                    or short[-CONTAINMENT_SLICE:] in long_):
                r.containment_flags += 1
                break

    # diagnostic: cross-chunk twin rate input (items with a fuzzy twin in a
    # DIFFERENT chunk of the same run — the merge's genuine dedup workload)
    twins = set()
    for i, a in enumerate(unions):
        na = normalize(a.text)
        for b in unions[i + 1:]:
            if a.chunk == b.chunk:
                continue
            if _ratio(na, normalize(b.text)) >= FUZZY_THRESHOLD:
                twins.add(id(a))
                twins.add(id(b))
    r.twin_items = len(twins)

    return r
