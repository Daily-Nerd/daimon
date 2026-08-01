"""Variant registry for the recall-scoring replay A/B harness.

Arm A is ALWAYS today's shipped `recall.suggest()`, unmodified. Arm B is a
VARIANT: one hypothesis about how recall should select or score differently,
expressed as a single callable

    def variant(ctx, suggest) -> list[dict]

`suggest()` runs the shipped call for this prompt and returns its match list.
Keyword arguments override that call's inputs (`prompt=`, `limit=`,
`exclude_sessions=`, ...). So a hypothesis can be expressed

  * OUTPUT-side — ``return [m for m in suggest() if keep(m)]``
    (a gate, a re-rank, a truncation over what recall already chose)
  * INPUT-side  — ``return suggest(prompt=rewrite(ctx["prompt"]))``
    (a different query, a wider fetch, a different exclusion set)
  * or both.

`ctx` keys:
  prompt   — the replayed prompt text
  terms    — recall.salient_terms(prompt)
  project  — the project dir the historical session ran in
  session  — the historical session id
  ts       — the prompt's epoch timestamp (frozen `now` for this replay)
  param    — this arm's sweep value, a STRING, or None when --sweep is
             omitted. A variant with a knob (a threshold, a weight, a
             marker) parses `param` itself; a knobless variant ignores it.
             One arm B is replayed per sweep value.
  db_path  — the snapshot recall db, for a variant that needs its own
             lookups. Open it READ-ONLY and close it before returning.

Contract a variant MUST honour, or the comparison stops being interpretable:

  1. Rows must come from `suggest()` (or a re-parameterised call to it).
     Never fabricate a row — every judging unit is built from arm output,
     and a synthesised row has no provenance to judge.
  2. No side effects: do not write the db, the seen state, the env, or any
     module-level constant in `daimon_briefing`. Arm A is replayed first and
     must be reproducible afterwards.
  3. Deterministic for a given (ctx, param). verify.py byte-compares two
     runs of every analytical artifact; a nondeterministic variant fails it.

Registering a hypothesis: add a function here and name it in `BUILTIN`, or
keep it out of the repo entirely and pass `--variant mymodule:myfunc`.
"""

import hashlib
import importlib
import re
import sys
from pathlib import Path

from daimon_briefing import store

# ---------------------------------------------------------------------------
# stance_gate (#483): does the prompt APPROACH the memory's territory (needs
# it) or REPORT from inside it (already has it)? Overlap-based matching
# cannot tell the two apart — both share the vocabulary. This is a
# deterministic SURFACE classifier of epistemic stance: no LLM, no new deps.
# ---------------------------------------------------------------------------

# Sentence-initial interrogative forms named in #483's pre-registration.
_QUESTION_OPENERS = frozenset({
    "how", "why", "what", "when", "where", "which", "who",
    "can", "could", "should", "does", "is",
})
# Phrase markers named in #483's pre-registration: "trying to", "can't get",
# "doesn't work", "need to figure", plus imperative help-requests.
_QUESTION_PHRASES = (
    "trying to", "can't get", "doesn't work", "need to figure",
    "help me", "figure out",
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LEAD_WORD_RE = re.compile(r"^[\s\"'(*_-]*([a-zA-Z']+)")


def classify_stance(prompt: str) -> str:
    """Deterministic surface classification of a prompt's epistemic stance.

    Returns ``"question"`` for prompts that show question-shaped surface
    markers, else ``"statement"`` (completed-work reports and declarations —
    the default when no marker fires). Case-insensitive. The trailing '?'
    check looks at the whole prompt; opener/phrase markers look at the
    first ~2 sentences only, so a late aside inside a long statement-shaped
    report doesn't flip the whole prompt.

    Markers (per #483's pre-registration):
      - trailing '?' anywhere in the prompt
      - sentence-initial interrogative: how/why/what/when/where/which/who/
        can/could/should/does/is (covers "is it ...")
      - phrase markers: "trying to", "can't get", "doesn't work",
        "need to figure"
      - imperative help-requests: "help me", "figure out"

    >>> classify_stance("How do I handle network timeouts?")
    'question'
    >>> classify_stance("Why does the retry loop spin forever")
    'question'
    >>> classify_stance("I implemented idempotent retry with exponential backoff")
    'statement'
    >>> classify_stance("Shipped the retry logic and closed the ticket.")
    'statement'
    >>> classify_stance("trying to get the retry logic working")
    'question'
    >>> classify_stance("The deploy doesn't work on the staging box")
    'question'
    >>> classify_stance("Deployed the staging box, all green.")
    'statement'
    >>> classify_stance("need to figure out why the index rebuild is slow")
    'question'
    >>> classify_stance("help me debug this stack trace")
    'question'
    >>> classify_stance("Merged #470, refuted and closed.")
    'statement'
    >>> classify_stance("Is it safe to rerun the migration")
    'question'
    >>> classify_stance("")
    'statement'
    """
    text = (prompt or "").strip()
    if not text:
        return "statement"
    if "?" in text:
        return "question"
    sentences = _SENTENCE_SPLIT_RE.split(text)[:2]
    lead = " ".join(sentences).lower()
    if any(p in lead for p in _QUESTION_PHRASES):
        return "question"
    for sentence in sentences:
        m = _LEAD_WORD_RE.match(sentence)
        if m and m.group(1).lower() in _QUESTION_OPENERS:
            return "question"
    return "statement"


def stance_gate(ctx, suggest):
    """H (#483): gate injection on the prompt's epistemic stance.

    Question-shaped prompts pass through unchanged — arm B is arm A for
    that prompt. Statement-shaped prompts suppress injection entirely (the
    pre-registered PRIMARY form; downweighting is a follow-up hypothesis,
    not this variant).

    Prompt-level gate — but NOT leak-free, the run proved (#483, run-04):
    when arm B suppresses an earlier statement-shaped prompt, it never marks
    that origin session "seen", so a LATER question-shaped prompt in the
    same session faces a different candidate pool than arm A's — 38 of 166
    diff prompts carried b_only injections through exactly this seen-state
    drift. Session-cooldown-layer analog of #470's slot-promotion trap: any
    suppressing variant inherits it, and "pass-through prompts are
    identical to arm A" is only true until a session runs long enough.
    """
    if classify_stance(ctx["prompt"]) == "question":
        return suggest()
    return []


def none(ctx, suggest):
    """Identity variant: arm B is arm A, exactly.

    The default, and the only one that ships. It measures nothing about
    recall — it proves the RIG is sound: replay, snapshotting, per-arm
    cooldown state and the diff machinery must all produce A == B when the
    two arms are the same code. Any diff under `none` is a harness bug, not
    a finding. Start every session with it before wiring a real hypothesis.
    """
    return suggest()


# ---------------------------------------------------------------------------
# placebo (#495): the NULL CONTROL. Suppresses rows at random, optionally at a
# different rate per age band, so a real hypothesis can be compared against
# "drop this many rows of this age and nothing else".
#
# It exists because a scoring proxy keyed on item age — the cheap way to score
# a policy without blind grading — is a function of the age HISTOGRAM of the
# rows a rule drops, and of nothing else. Every rule dropping the same profile
# scores identically, including this one. A hypothesis that does not beat its
# own placebo has measured the histogram, not the hypothesis.
# ---------------------------------------------------------------------------

_BANDS = ("<=1d", "2-7d", ">7d", "unknown")


def _age_band(row, now) -> str:
    epoch = store._created_epoch(row.get("first_seen"))
    if epoch is None or epoch > now:
        return "unknown"
    age = (now - epoch) / 86400.0
    return "<=1d" if age <= 1 else "2-7d" if age <= 7 else ">7d"


def _placebo_rates(param):
    """`param` is either a bare probability applied to every band ("0.15"), or
    comma-separated per-band rates ("<=1d:0,2-7d:0.07,>7d:0.11") so a placebo
    can be matched to a treatment arm's observed drop histogram. Unnamed bands
    default to 0 — a placebo must never suppress more than it was asked to."""
    if param is None:
        return {}
    token = str(param).strip()
    if not token:
        return {}
    if ":" not in token:
        try:
            rate = float(token)
        except ValueError:
            raise SystemExit(
                f"replay-ab: placebo param {param!r} is neither a probability "
                f"nor band:rate pairs")
        return {b: rate for b in _BANDS}
    rates = {}
    for piece in token.split(","):
        band, _, value = piece.partition(":")
        band = band.strip()
        if band not in _BANDS:
            raise SystemExit(
                f"replay-ab: placebo band {band!r} unknown; expected "
                f"{list(_BANDS)}")
        rates[band] = float(value)
    return rates


def placebo(ctx, suggest):
    """Null control: suppress rows at random, per-band, deterministically.

    The draw is a hash of (prompt, row text, param) rather than a PRNG, so it
    is stable under replay and re-entry — verify.py byte-compares two runs of
    every artifact, and a stateful generator would drift the moment the arm
    order or the prompt set changed. Rows are only ever REMOVED: like every
    gate, the placebo can decline what `suggest()` returned and can never
    invent a row it did not.
    """
    rows = suggest()
    rates = _placebo_rates(ctx.get("param"))
    if not rates or not rows:
        return rows
    kept = []
    for row in rows:
        rate = rates.get(_age_band(row, ctx["ts"]), 0.0)
        if rate <= 0.0:
            kept.append(row)
            continue
        seed = f"{ctx['prompt']}\x00{row.get('text') or ''}\x00{ctx['param']}"
        draw = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)
        if draw / 0xFFFFFFFF >= rate:
            kept.append(row)
    return kept


BUILTIN = {"none": none, "stance_gate": stance_gate, "placebo": placebo}


def resolve(spec: str):
    """'none' -> a builtin; 'pkg.mod:func' / 'path/to/file.py:func' -> an
    external variant. The file form puts the file's directory on sys.path so
    a one-file hypothesis needs no packaging."""
    if spec in BUILTIN:
        return BUILTIN[spec]
    if ":" not in spec:
        raise SystemExit(
            f"replay-ab: unknown variant {spec!r}; expected one of "
            f"{sorted(BUILTIN)} or 'module:function'")
    mod_spec, _, func = spec.partition(":")
    if mod_spec.endswith(".py"):
        path = Path(mod_spec).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"replay-ab: no variant file at {path}")
        sys.path.insert(0, str(path.parent))
        mod_spec = path.stem
    try:
        mod = importlib.import_module(mod_spec)
    except ImportError as exc:
        raise SystemExit(f"replay-ab: cannot import variant module "
                         f"{mod_spec!r}: {exc}") from exc
    try:
        fn = getattr(mod, func)
    except AttributeError as exc:
        raise SystemExit(f"replay-ab: variant module {mod_spec!r} has no "
                         f"{func!r}") from exc
    if not callable(fn):
        raise SystemExit(f"replay-ab: variant {spec!r} is not callable")
    return fn
