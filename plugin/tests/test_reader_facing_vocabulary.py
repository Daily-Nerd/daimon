"""A short list of terms stays out of reader-facing prose, and nothing but a
reviewer's attention was enforcing it.

That is not a theoretical gap. #761 and #762 removed a dozen instances across
two reference pages, two blog posts and both Spanish mirrors. One of them had
been introduced days earlier, in #759, by a session that was applying the same
rule elsewhere in the very same change. Every one of those had been reviewed.

The shape of the failure is what makes it worth a test: a sweep runs, the
surfaces are corrected, the result is recorded as clean, and then ordinary
feature work walks the words back in. Correcting the prose again without a
guard just resets the clock. This is the guard.

Scope is prose a reader sees. Code identifiers are deliberately out: a CLI
flag and a JSON payload key use one of these terms on purpose, machine-facing,
and renaming them breaks callers.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Reader-facing surfaces. `docs/` is deliberately absent — it holds working
# notes and validation records, not published prose.
SURFACES = (
    "website/docs",
    "website/blog",
    "website/i18n",
    "README.md",
)

# Stem-aware, not word-list-aware: one past instance was a spelling the
# word-boundary check walked straight past, so `judgement` and `judgment` are
# both spelled out and `testimon\w*` catches the noun and the adjective alike.
# Spanish is included because the mirror is a governed surface in its own
# right, and one earlier correction was Spanish-only.
EXCLUDED = re.compile(
    r"\b(trials?|verdicts?|judge[sd]?|judgement|judgment|jury|testimon\w*"
    r"|hearsay|prosecutes?|guilty|innocent"
    r"|veredictos?|juicios?|jurado|testimonios?|culpable|inocente)\b",
    re.IGNORECASE,
)

# (path relative to repo root, the exact word allowed there, why).
#
# An allowance carries a reason. An unexplained exception is how a guard
# quietly stops guarding. Each of these three uses the term to DENY the thing
# rather than to claim it, which is the same form already accepted for the
# storage-immutability disclaimer.
ALLOWED = (
    ("website/docs/reference/claims.md", "testimonial",
     "marketing sense, in a sentence saying this page carries none"),
    ("website/docs/reference/claims.md", "testimonials",
     "same sentence, plural"),
    ("website/i18n/es/docusaurus-plugin-content-docs/current/reference/claims.md",
     "testimonio", "Spanish mirror of the claims.md denial"),
    ("website/i18n/es/docusaurus-plugin-content-docs/current/reference/claims.md",
     "testimonios", "same sentence, plural"),
    ("website/blog/2026-07-28-verbatim-vs-inferred.md", "judgment",
     "denies the tool applies any: 'no LLM, no judgment'"),
    ("website/docs/concepts/lifecycle.md", "judgment",
     "the reader's own discretion, not the tool's behaviour"),
    ("website/i18n/es/docusaurus-plugin-content-docs/current/concepts/lifecycle.md",
     "juicios",
     "Spanish mirror of the lifecycle.md line above; this guard found it on "
     "its first run, after a hand-written sweep missed the plural"),
)


def _prose_files():
    for surface in SURFACES:
        target = REPO / surface
        if target.is_file():
            yield target
            continue
        for path in sorted(target.rglob("*")):
            if path.suffix in (".md", ".mdx") and path.is_file():
                yield path


def _hits(path):
    """(line number, matched word) for every occurrence in one file."""
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        out.extend((n, m.group(0)) for m in EXCLUDED.finditer(line))
    return out


def _allowed_words(rel):
    return {word.lower() for path, word, _ in ALLOWED if path == rel}


def test_the_pattern_still_matches_what_this_test_assumes():
    """A guard that cannot fire is worse than no guard, because it reads as
    coverage. Pin the pattern against strings it must catch, including the
    stem case that slipped past a word-boundary check once already."""
    for probe in ("Land a verdict.", "the honest verdicts are",
                  "a person's judgement", "Un veredicto requiere",
                  "the gate is on trial"):
        assert EXCLUDED.search(probe), f"pattern missed: {probe!r}"
    for clean in ("Land a decision.", "as a human decision",
                  "una decisión humana", "decide"):
        assert not EXCLUDED.search(clean), f"false positive: {clean!r}"


def test_no_excluded_term_reaches_reader_facing_prose():
    found = []
    for path in _prose_files():
        rel = path.relative_to(REPO).as_posix()
        allowed = _allowed_words(rel)
        for line_no, word in _hits(path):
            if word.lower() not in allowed:
                found.append(f"{rel}:{line_no} {word!r}")
    assert not found, (
        "these reach reader-facing prose:\n  " + "\n  ".join(found)
        + "\n\nRestate the sentence. If the use is deliberate — the term "
          "denying the thing rather than claiming it — add it to ALLOWED "
          "with the reason, in this file.")


def test_every_allowance_is_still_earning_its_place():
    """A stale allowance is a hole. If the prose it excused was rewritten or
    deleted, the entry must go too, or it silently pre-authorises the next
    occurrence in that file."""
    stale = []
    for rel, word, reason in ALLOWED:
        path = REPO / rel
        if not path.exists():
            stale.append(f"{rel} (file is gone) — {reason}")
            continue
        if word.lower() not in {w.lower() for _, w in _hits(path)}:
            stale.append(f"{rel}: {word!r} no longer appears — {reason}")
    assert not stale, (
        "these allowances no longer describe anything:\n  " + "\n  ".join(stale)
        + "\n\nDelete the entry. The prose it excused is already gone.")
