"""#403: one shared, stdlib-only text-normalization function.

Every path that compares memory text — the forget-ledger value key (#402) and
any render-time comparison — folds through `canonical_text` so a value blocked
on one path cannot leak on another. If two callers each rolled their own
normalization they would drift, and a forgotten value suppressed at capture
would re-surface at render (or vice versa).

Sound normalization, not the partial patch prior art commonly ships:

  1. NFKC — fold compatibility forms (fullwidth, ligatures, ...).
  2. Strip the FULL invisible-character range set (not a partial list): the
     zero-width block, word-joiner / invisible-operator block, bidi isolates,
     BOM/ZWNBSP, soft hyphen, combining grapheme joiner, Mongolian free
     variation selectors, variation selectors, the tag block, and the
     object/replacement placeholders.
  3. Collapse every control + whitespace run to a single space; strip ends.
  4. casefold().
  5. Fold a small Latin/Cyrillic/Greek confusables skeleton so a letter and its
     look-alike twin key the same value.

stdlib only (`unicodedata`, `re`, `hashlib`) — no new dependency, ADR-clean.
Every regex obeys the house bounded-quantifier rule (scar 0022): the capture
path runs on every text of every item on every checkpoint write, and Python
`re` has no timeout, so a quadratic backtrack would freeze the write with no
exception to catch. A character-class `+` has no alternation overlap and is
linear; the mandated large-input completion test in test_normalize.py guards
it regardless.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Invisible / zero-width / formatting characters: no visible content, but each
# defeats a byte hash. Stripped as a class BEFORE the whitespace collapse and
# casefold. Expressed as ranges so a whole block is covered, never a partial
# patch (issue #403's central complaint about prior art).
_INVISIBLE = (
    "­"                  # SOFT HYPHEN
    "͏"                  # COMBINING GRAPHEME JOINER
    "᠋-᠍"           # MONGOLIAN FREE VARIATION SELECTOR ONE..THREE
    "​-‏"           # ZERO WIDTH SPACE .. RIGHT-TO-LEFT MARK
    "⁠-⁤"           # WORD JOINER .. INVISIBLE PLUS
    "⁦-⁩"           # bidi isolates (LRI/RLI/FSI/PDI)
    "︀-️"           # VARIATION SELECTOR-1..16
    "﻿"                  # ZERO WIDTH NO-BREAK SPACE / BOM
    "￼�"            # OBJECT REPLACEMENT / REPLACEMENT CHARACTER
    "\U000e0000-\U000e007f"   # TAG block (language tag + tag chars + cancel)
)
_INVISIBLE_RE = re.compile("[" + _INVISIBLE + "]+")

# Control (C0/C1 + DEL) and any whitespace run -> a single space. \s covers
# Unicode whitespace; the explicit control ranges add the C0/C1 + DEL that \s
# omits. One substitution per run — linear.
_WS_RE = re.compile(r"[\s\x00-\x1f\x7f-\x9f]+")

# A small Latin/Cyrillic/Greek confusables skeleton: look-alike letters that
# render (near-)identically fold onto one Latin representative. Applied AFTER
# casefold, so only lowercase forms need entries. Deliberately small — the
# common homoglyph-attack letters, not a full Unicode confusables table.
_CONFUSABLES = {
    # Cyrillic -> Latin
    "а": "a",  # CYRILLIC SMALL LETTER A
    "е": "e",  # IE
    "о": "o",  # O
    "р": "p",  # ER
    "с": "c",  # ES
    "у": "y",  # U
    "х": "x",  # HA
    "і": "i",  # BYELORUSSIAN-UKRAINIAN I
    "ј": "j",  # JE
    "һ": "h",  # SHHA
    "ԁ": "d",  # KOMI DE
    "ѕ": "s",  # DZE
    "т": "t",  # TE
    "м": "m",  # EM (visually close in some faces)
    # Greek -> Latin
    "ο": "o",  # SMALL OMICRON
    "α": "a",  # SMALL ALPHA
    "ι": "i",  # SMALL IOTA
    "ν": "v",  # SMALL NU
    "ρ": "p",  # SMALL RHO
    "χ": "x",  # SMALL CHI
    "υ": "u",  # SMALL UPSILON
    "κ": "k",  # SMALL KAPPA
}
_CONFUSABLE_TABLE = {ord(k): v for k, v in _CONFUSABLES.items()}

# Bound the canonical text before hashing. A pathological input can never
# balloon the key computation, and — the deliberate trade — two inputs sharing
# a long common prefix key IDENTICALLY, so the ledger OVER-blocks on a prefix
# collision. Over-suppression is the fail-safe direction for a deletion
# guarantee (a forgotten value re-appearing is the worse failure).
_MAX_KEY_INPUT = 4096

# Hash-prefix length for the ledger key. >= the legacy 12 the tombstone printed,
# so an already-canonical value's key still contains the old sha256[:12] prefix.
_KEY_HEX_LEN = 16


def compat_fold(text) -> str:
    """NFKC + invisible strip, and NOTHING that changes case (#660).

    Split out of `canonical_text` for scanners whose patterns are anchored on
    uppercase ASCII: `redact.py` classes like aws-key and jwt stop matching if
    the text is casefolded first, which is the #647 failure one layer up. A
    compatibility form (fullwidth, mathematical alphanumeric) walks past every
    such pattern, so a scanner must fold BEFORE it looks, then decide for
    itself whether to keep the folded text.

    Pure, stdlib-only, total, idempotent."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = unicodedata.normalize("NFKC", text)
    return _INVISIBLE_RE.sub("", text)


def canonical_text(text) -> str:
    """Fold `text` to a comparison-stable canonical form. Pure, stdlib-only,
    total — never raises for any input; a non-str is coerced first. Idempotent:
    canonical_text of its own output is a fixed point."""
    text = compat_fold(text)
    text = _WS_RE.sub(" ", text).strip()
    text = text.casefold()
    text = text.translate(_CONFUSABLE_TABLE)
    return text


def content_key(text) -> str:
    """Bounded canonical hash key for the forget ledger. The canonical form is
    length-bounded (`_MAX_KEY_INPUT`) before hashing and the digest truncated
    to `_KEY_HEX_LEN` hex chars — a prefix collision over-blocks, the fail-safe
    direction for a deletion guarantee."""
    canon = canonical_text(text)[:_MAX_KEY_INPUT]
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:_KEY_HEX_LEN]
