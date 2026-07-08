"""Deterministic capture-time secret redaction (#104) — stdlib only, zero LLM.

Checkpoints persist outside the session and the team mirror replicates them
to a shared remote, so a quoted secret must never reach disk. Patterns are
precision-first: each requires a concrete shape (assignment operator, URL
scheme, key prefix, header keyword) — never bare entropy — so prose
("the token budget", "password rotation policy") survives untouched.
Replacement is a stable visible marker, [redacted:<kind>]: auditable,
never silent."""

import re

# (kind, compiled pattern). Order matters only for overlap (pem first: a key
# block may contain assignment-looking lines that must not double-count).
_PATTERNS = (
    ("pem", re.compile(
        r"-----BEGIN [A-Z0-9 ]*(?:KEY|CERTIFICATE)[A-Z0-9 ]*-----"
        r"(?:.*?-----END [A-Z0-9 ]*(?:KEY|CERTIFICATE)[A-Z0-9 ]*-----|.*)",
        re.DOTALL)),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("stripe-key", re.compile(r"\b[rsp]k_live_[0-9A-Za-z]{8,}\b")),
    # Fixed-prefix vendor tokens: anchored on a documented literal prefix +
    # charset (same "concrete shape, never bare entropy" precision as aws/
    # stripe). A literal prefix before a single char class has no ambiguous
    # backtracking, so an open-ended body ({N,}) is linear and safe — and
    # redacts the whole token (a capped upper bound would leak the tail).
    ("github-token", re.compile(
        r"\b(?:gh[oprsu]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("bearer", re.compile(
        r"(?i)\b(?:bearer|authorization:\s*\w+)\s+[A-Za-z0-9._~+/=-]{16,}")),
    # Separator tolerates an optional quote before the operator ("api_key":)
    # and the value may itself be quote-wrapped, so JSON/pasted-config bodies
    # redact. The bounded lazy prefix [\w-]{0,32}? stays per scar-0022 — no
    # unbounded class before the keyword alternation.
    ("api-key", re.compile(
        r"(?i)\b([\w-]{0,32}?(?:api[_-]?key|secret|token|password|passwd))"
        r"(['\"]?\s*[=:]\s*)['\"]?(?!\[redacted:)[^\s'\"]{8,}")),
    # User portion optional ([^/\s:@]*) so password-only URLs (redis://:pw@h)
    # match; the group(1)+":[redacted…]@" reconstruction stays valid empty.
    ("credential-url", re.compile(
        r"\b([a-z][a-z0-9+.-]{0,15}://[^/\s:@]*):(?!\[redacted:)[^/\s@]+@")),
)


def redact_text(s):
    """(redacted, {kind: count}). Non-string / empty input passes through
    unchanged — callers scrub optional fields without type checks. A regex
    failure skips that pattern (fail-open: redaction must never cost the
    write; unreachable with these static patterns, cheap insurance)."""
    counts: dict = {}
    if not isinstance(s, str) or not s:
        return s, counts

    def _mark(kind):
        counts[kind] = counts.get(kind, 0) + 1

    for kind, rx in _PATTERNS:
        def _sub(m, kind=kind):
            _mark(kind)
            if kind == "api-key":
                return m.group(1) + m.group(2) + "[redacted:api-key]"
            if kind == "credential-url":
                return m.group(1) + ":[redacted:credential-url]@"
            return f"[redacted:{kind}]"
        try:
            s = rx.sub(_sub, s)
        except re.error:  # pragma: no cover — static patterns
            continue
    return s, counts
