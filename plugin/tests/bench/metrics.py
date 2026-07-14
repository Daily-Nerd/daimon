"""Retrieval metrics for the LongMemEval harness (#267).

The unit of retrieval is a SESSION: daimon serializes each haystack session into
a checkpoint whose items carry that session's id, so a recall hit is scored by
whether a retrieved item's source session is one of the question's evidence
(`answer_session_ids`) sessions. Definitions used throughout:

- Recall@k  = |gold ∩ top-k retrieved sessions| / |gold|, averaged over questions.
              A coverage metric — did we surface the evidence, and how much of it.
- Hit@k     = 1 if any gold session is in the top-k, else 0 (a laxer success rate).
- MRR       = mean of 1 / rank-of-first-gold-session (0 when no gold is retrieved).
- Injected tokens = estimated token cost of the top-k item texts a briefing would
              inject — daimon's efficiency story, not a quality metric.

Abstention questions (LongMemEval `*_abs`, empty `answer_session_ids`) have no
evidence session to retrieve, so retrieval metrics are None for them and they are
excluded from the means — never scored as zero, which would understate recall.

All functions are pure and deterministic given their inputs.
"""

from __future__ import annotations

# A briefing has no tokenizer (daimon is stdlib-only), so the injected-token
# figure is an estimate, not an exact count. ~4 chars/token is the standard
# rough English ratio; the number is comparative (across configs/runs), and the
# harness records that it is an estimate so it is never quoted as exact.
_CHARS_PER_TOKEN = 4


def ranked_sessions(recall_results: list[dict]) -> list[str]:
    """Ranked, de-duplicated source-session ids from recall results.

    recall.search returns items (multiple per session); the retrieval unit is the
    session, so collapse to first-occurrence order — an item's rank is its
    session's rank the first time that session appears.
    """
    seen: set[str] = set()
    out: list[str] = []
    for row in recall_results:
        sid = row.get("session_id")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def attributed_sessions(recall_results: list[dict],
                        attribution: dict[tuple[str, str], str]) -> list[str]:
    """Ranked sessions with carried copies credited to their ORIGIN session.

    Scoring rule for carry-on runs (#274): a retrieval counts for a session only
    when it is ATTRIBUTABLE to that session — the session that first produced
    the item. With carry on, a later checkpoint hosts verbatim copies of earlier
    sessions' unresolved items, and the recall index knows only the hosting
    checkpoint's session_id; scoring the hosting session would credit a non-gold
    session for gold evidence (or rank gold below its own carried copy). So each
    retrieved row is mapped through `attribution` — (hosting session_id, item
    text) -> origin session, built by the adapter from `carried_from` at write
    time — and falls back to the hosting session when unmapped (native items,
    carry-off runs). First-occurrence dedup then guarantees a session is
    credited at most once: gold surfaced both natively and as a carried copy
    counts once, at its best rank, and never double-counts.
    """
    seen: set[str] = set()
    out: list[str] = []
    for row in recall_results:
        sid = row.get("session_id")
        if not sid:
            continue
        sid = attribution.get((sid, str(row.get("text") or "")), sid)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float | None:
    """Fraction of gold sessions present in the top-k. None when gold is empty."""
    if not gold:
        return None
    window = set(ranked[:k])
    return len(gold & window) / len(gold)


def hit_at_k(ranked: list[str], gold: set[str], k: int) -> bool | None:
    """True when at least one gold session is in the top-k. None when gold empty."""
    if not gold:
        return None
    return bool(gold & set(ranked[:k]))


def reciprocal_rank(ranked: list[str], gold: set[str]) -> float | None:
    """1 / (1-based rank) of the first gold session, 0.0 if none. None when empty."""
    if not gold:
        return None
    for i, sid in enumerate(ranked):
        if sid in gold:
            return 1.0 / (i + 1)
    return 0.0


def estimate_tokens(text: str) -> int:
    """Rough token count via the ~4-chars/token heuristic. Estimate, not exact."""
    if not text:
        return 0
    return len(text) // _CHARS_PER_TOKEN


def injected_tokens(recall_results: list[dict], k: int) -> int:
    """Estimated tokens of the top-k retrieved item texts — the briefing budget."""
    return sum(estimate_tokens(str(r.get("text") or "")) for r in recall_results[:k])


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(per_question: list[dict], k: int) -> dict:
    """Roll per-question rows into run-level metrics.

    Retrieval means (recall@k, hit@k, mrr) are taken over SCORED questions only
    (abstention rows excluded). The token average is over ALL questions — it is
    the efficiency of the whole run, abstentions included.
    """
    scored = [q for q in per_question if not q.get("abstention")]
    recalls = [q["recall_at_5"] for q in scored if q.get("recall_at_5") is not None]
    hits = [1.0 if q["hit_at_5"] else 0.0 for q in scored
            if q.get("hit_at_5") is not None]
    rrs = [q["mrr"] for q in scored if q.get("mrr") is not None]
    tokens = [q["injected_tokens"] for q in per_question
              if q.get("injected_tokens") is not None]
    return {
        "k": k,
        "questions_total": len(per_question),
        "questions_scored": len(scored),
        "questions_abstention": len(per_question) - len(scored),
        "recall_at_5": _mean(recalls),
        "hit_at_5": _mean(hits),
        "mrr": _mean(rrs),
        "avg_injected_tokens": _mean([float(t) for t in tokens]),
    }
