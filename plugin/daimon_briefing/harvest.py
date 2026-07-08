"""Zero-LLM scar-candidate harvester (#76). Session-end, opt-in, never breaks a session.

Scans the transcript for anchorable negative knowledge and drafts scar *candidates*
into <project_root>/.scars/candidates/. Path-anchored only (Fork 2): a hit with no
real file/dir path in its own span is dropped — precision over recall, because a
scar system dies from noise, not a missed lesson. Emits candidates only; a human
reviewer promotes. Pure stdlib.
"""

import datetime
import json
import logging
import re
from pathlib import Path
from typing import NamedTuple

from . import redact, transcript

log = logging.getLogger("daimon_briefing")


class Hit(NamedTuple):
    kind: str
    sentence: str
    context: str
    msg_index: int


_AVOID_RE = re.compile(
    r"\b(avoid|don't|do not|never|gotcha|pitfall|footgun|broke|breaks|mistake|dead[ -]?end"
    # Spanish band mirrors the English markers (#4). Bare "no" is far more
    # frequent than "don't", so only specific imperative constructions fire —
    # never plain negation ("no devuelve" stays silent).
    r"|evit(?:a|á|ar|es|en)|nunca|jam[áa]s|trampa|romp(?:e|i[óo]|en)"
    r"|callej[óo]n sin salida|punto muerto|no (?:hagas|toques|uses|llames))\b",
    re.IGNORECASE,
)
_INTENT_RE = re.compile(
    r"\b(on purpose|intentional(?:ly)?|deliberately|looks wrong but|must stay|keep this"
    r"|a prop[óo]sito|intencional(?:mente)?|adrede|deliberadamente"
    r"|parece (?:mal|incorrecto) pero|debe quedar(?:se)?)\b",
    re.IGNORECASE,
)


def _split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def detect(messages: list[dict]) -> list[Hit]:
    """Assistant-only marker scan. Returns Hits (no I/O, no anchoring yet)."""
    hits: list[Hit] = []
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        content = transcript._text_of(m.get("content"))
        for s in _split_sentences(content):
            if _INTENT_RE.search(s):
                hits.append(Hit("intentional", s, content, i))
            elif _AVOID_RE.search(s):
                hits.append(Hit("avoidance", s, content, i))
    return hits


# path-like token: a/b/c.ext (ext-whitelisted to keep prose out) OR a nested a/b/ dir.
_PATH_RE = re.compile(
    r"([\w.\-/]+\.(?:py|md|js|ts|tsx|go|rs|json|ya?ml|toml|sh|txt|cfg|ini)"
    r"|[\w.\-]+(?:/[\w.\-]+)+/?)"
)


def anchor_of(hit, project_root):
    """First path token in the hit's sentence that exists INSIDE project_root.

    Returns a repo-relative posix path str, or None → drop hit. Absolute tokens
    and ``..`` traversal that escape the root are rejected: the resolved path must
    stay under the resolved root. The existence + containment check is the
    precision gate — garbled, hallucinated, or escaping paths vanish.
    """
    root = Path(project_root).resolve()
    for m in _PATH_RE.finditer(hit.sentence):
        cand = m.group(1).rstrip(":,.)")
        if not cand or Path(cand).is_absolute():
            continue
        try:
            resolved = (root / cand).resolve()
            if resolved.is_relative_to(root) and resolved.exists():
                return resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
    return None


_DEADEND_RE = re.compile(
    r"\b(tried|attempted|turned out|didn't work|doesn't work|gave up)\b", re.IGNORECASE
)


def _scar_type(hit):
    if hit.kind == "intentional":
        return "fence"
    return "deadend" if _DEADEND_RE.search(hit.sentence) else "landmine"


def _slug(title):
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:60] or "harvested-scar"


def to_candidate(hit, anchor, session_id, today):
    """Build (slug, markdown). Lint-valid frontmatter, single path-only anchor.

    `title` is emitted via json.dumps → a valid double-quoted YAML scalar even when
    the sentence contains ':' or quotes (the #1 hand-written-scar YAML footgun).

    The verbatim sentence is secret-scrubbed here (#109) before it becomes the
    candidate's title, slug, and body — candidate files are committable, so a
    quoted secret must never persist to .scars/candidates/*.md. Classification
    (_scar_type) reads the raw hit: its markers are prose, untouched by redaction.
    """
    typ = _scar_type(hit)
    sentence, _ = redact.redact_text(hit.sentence)
    title = " ".join(sentence.split())[:80].rstrip()
    slug = _slug(title)
    review = (
        datetime.date.fromisoformat(today) + datetime.timedelta(days=365)
    ).isoformat()
    md = (
        "---\n"
        "id: 0\n"
        f"type: {typ}\n"
        f"title: {json.dumps(title)}\n"
        "severity: medium\n"
        "confidence: 0.5\n"
        f"created: {today}\n"
        'authors: ["daimon-harvest"]\n'
        "anchors:\n"
        f"  - path: {anchor}\n"
        "evidence:\n"
        f"  - note: {json.dumps('auto-harvested from session ' + session_id)}\n"
        "expires:\n"
        '  condition: "the referenced code is removed or the constraint no longer holds"\n'
        f"  review_after: {review}\n"
        "status: candidate\n"
        "---\n\n"
        f"{sentence.strip()}\n\n"
        "Auto-harvested from the session transcript — a human must verify the claim "
        "and confirm the anchor before promotion.\n"
    )
    return slug, md


_MAX_CANDIDATES = 5


def run(messages, project_root, session_id):
    """detect -> anchor-gate -> candidate -> dedup + cap -> write. Returns count.

    Skips silently when the repo hasn't opted into scars (no .scars/). Never
    overwrites an existing candidate (a human may have edited it), so re-running
    the same session writes nothing new.
    """
    if not project_root:
        return 0
    root = Path(project_root)
    if not (root / ".scars").exists():
        return 0
    cand_dir = root / ".scars" / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today().isoformat()
    written, dropped = 0, 0
    seen = set()
    for hit in detect(messages):
        anchor = anchor_of(hit, project_root)
        if anchor is None:
            continue
        slug, md = to_candidate(hit, anchor, session_id, today)
        if slug in seen:
            continue
        seen.add(slug)
        path = cand_dir / f"{slug}.md"
        if path.exists():
            continue
        if written >= _MAX_CANDIDATES:
            dropped += 1
            continue
        path.write_text(md, encoding="utf-8")
        written += 1
    if dropped:
        log.info(
            "daimon: scar harvest capped at %d, dropped %d candidate(s)",
            _MAX_CANDIDATES, dropped,
        )
    return written
