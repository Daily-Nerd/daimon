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


# ---- #405: forbidden-hit dimension ------------------------------------------
#
# Recall asks "did the right memory surface?". Forbidden hits ask the opposite:
# "did material that must NOT surface reach the model anyway?". The load-bearing
# rule is WHERE it is scored — against the ASSEMBLED BRIEF (the top-k rows a
# briefing delivers), not the raw retriever output. Suppression the retriever
# performs but assembly undoes is not suppression; suppression assembly performs
# (withholding a resolved row recall still returns) must be credited. Scoring the
# raw depth-N retriever rows would both miss the first and false-positive the
# second.


def assembled_brief_text(recall_results: list[dict], k: int) -> str:
    """The brief text actually delivered to the model: the top-k SURVIVING rows'
    text+quote, joined.

    Two assembly steps mirror the briefing, and are exactly what makes this
    differ from the raw retriever output:
      - WITHHOLD: rows carrying a `superseded_by` stamp (event-resolved or
        superseded — a downgraded standing) are dropped, as briefing.withhold
        drops them, even though recall still returns them ranked-down. A withheld
        row does not consume a top-k slot.
      - WINDOW: only the first k survivors reach the prompt; a match ranked below
        the window is retrieved but never delivered.
    """
    survivors: list[dict] = []
    for row in recall_results:
        if row.get("superseded_by"):
            continue  # withheld at assembly — recall returns it, the brief omits it
        parts = [str(row.get("text") or ""), str(row.get("quote") or "")]
        survivors.append({"blob": " ".join(p for p in parts if p)})
        if len(survivors) >= max(0, k):
            break
    return "\n".join(s["blob"] for s in survivors)


def forbidden_hits_found(brief_text: str, forbidden: list[str]) -> list[str]:
    """The forbidden strings present in the assembled brief (case-insensitive
    substring), de-duplicated in listed order. Blank entries are ignored."""
    haystack = brief_text.lower()
    out: list[str] = []
    seen: set[str] = set()
    for needle in forbidden:
        n = str(needle or "").strip()
        if not n or n in seen:
            continue
        seen.add(n)
        if n.lower() in haystack:
            out.append(needle)
    return out


def scored_recall(base: float | None, matched: int, total: int) -> float | None:
    """Recall floored by leakage: max(0, base - matched/total).

    Leakage is disqualifying, not averaged away — it is subtracted from the
    case's OWN score, so a full leak (matched == total) drives a perfect-recall
    case to zero. No forbidden material defined (total <= 0) leaves base
    untouched; an abstention/None base stays None."""
    if base is None:
        return None
    if total <= 0:
        return base
    return max(0.0, base - matched / total)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(per_question: list[dict], k: int) -> dict:
    """Roll per-question rows into run-level metrics.

    Retrieval means (recall@k, hit@k, mrr) are taken over SCORED questions only
    (abstention rows excluded). The token average is over ALL questions — it is
    the efficiency of the whole run, abstentions included.

    Error rows (#343: `error` set — e.g. a served-model mismatch failed the
    question loudly) are excluded from every mean AND counted explicitly in
    `questions_error`, so a run that hit failures carries them in its
    aggregate forever — a mixed-model run can never present itself as a
    clean score.
    """
    errors = [q for q in per_question if q.get("error")]
    scored = [q for q in per_question
              if not q.get("abstention") and not q.get("error")]
    recalls = [q["recall_at_5"] for q in scored if q.get("recall_at_5") is not None]
    hits = [1.0 if q["hit_at_5"] else 0.0 for q in scored
            if q.get("hit_at_5") is not None]
    rrs = [q["mrr"] for q in scored if q.get("mrr") is not None]
    tokens = [q["injected_tokens"] for q in per_question
              if q.get("injected_tokens") is not None]
    # #405: forbidden-hit dimension. The leak rate is over the questions that
    # actually DEFINE forbidden material (forbidden_total > 0) — a question with
    # nothing forbidden is not a clean pass, it is not-applicable, so it never
    # dilutes the rate. Penalized recall is the leak-floored recall averaged over
    # all scored questions (falling back to raw recall for rows that predate the
    # dimension), reported ALONGSIDE recall under the same reporting policy.
    forbidden_qs = [q for q in scored if (q.get("forbidden_total") or 0) > 0]
    leak_flags = [1.0 if q.get("forbidden_hit") else 0.0 for q in forbidden_qs]
    penalized = [
        q["recall_at_5_penalized"] if q.get("recall_at_5_penalized") is not None
        else q["recall_at_5"]
        for q in scored
        if (q.get("recall_at_5_penalized")
            if q.get("recall_at_5_penalized") is not None
            else q.get("recall_at_5")) is not None
    ]
    return {
        "k": k,
        "questions_total": len(per_question),
        "questions_scored": len(scored),
        "questions_abstention": len(per_question) - len(scored) - len(errors),
        "questions_error": len(errors),
        "recall_at_5": _mean(recalls),
        "hit_at_5": _mean(hits),
        "mrr": _mean(rrs),
        "avg_injected_tokens": _mean([float(t) for t in tokens]),
        "questions_with_forbidden": len(forbidden_qs),
        "forbidden_hit_rate": _mean(leak_flags),
        "recall_at_5_penalized": _mean(penalized),
    }
