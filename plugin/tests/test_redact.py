"""#104: capture-time secret redaction — per-pattern positive + negative."""

from daimon_briefing import redact


def _one(s):
    out, counts = redact.redact_text(s)
    return out, counts


def test_pem_block_redacted_terminated_and_unterminated():
    out, c = _one("key follows\n-----BEGIN RSA PRIVATE KEY-----\nabc\n"
                  "-----END RSA PRIVATE KEY-----\ndone")
    assert "[redacted:pem]" in out and "abc" not in out and c["pem"] == 1
    out2, c2 = _one("-----BEGIN PRIVATE KEY-----\nabc def")
    assert "[redacted:pem]" in out2 and "abc" not in out2


def test_aws_key_redacted_prose_survives():
    out, c = _one("creds AKIAIOSFODNN7EXAMPLE were used")
    assert "AKIAIOSFODNN7EXAMPLE" not in out and c["aws-key"] == 1
    out2, c2 = _one("the AKIA prefix identifies aws keys")
    assert c2 == {} and "AKIA prefix" in out2


def test_stripe_key_redacted_docs_mention_survives():
    out, c = _one("use sk_live_a1B2c3D4e5F6g7H8 in prod")
    assert "sk_live_a1B2c3D4e5F6g7H8" not in out and c["stripe-key"] == 1
    out2, c2 = _one("rotate the sk_live key monthly")
    assert c2 == {}


def test_bearer_token_redacted_prose_survives():
    out, c = _one("header was Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.x")
    assert "eyJhbGci" not in out and c["bearer"] == 1
    out2, c2 = _one("the bearer of good news arrived")
    assert c2 == {}


def test_api_key_assignment_redacted_keeps_name_and_separator():
    out, c = _one("set DAIMON_LLM_API_KEY=sk-abcdef1234567890 in env")
    assert "sk-abcdef1234567890" not in out
    assert "API_KEY=[redacted:api-key]" in out and c["api-key"] == 1
    out2, c2 = _one("api_key: 'x9y8z7w6v5u4t3s2'")
    assert "x9y8z7" not in out2 and c2["api-key"] == 1
    out3, c3 = _one("the token budget and password rotation policy")
    assert c3 == {}


def test_credential_url_redacts_password_keeps_host():
    out, c = _one("postgres://admin:hunter2secret@db.example.com:5432/x")
    assert "hunter2secret" not in out
    assert "postgres://admin:[redacted:credential-url]@db.example.com" in out
    assert c["credential-url"] == 1
    out2, c2 = _one("see https://docs.example.com/path and http://localhost:8080/x")
    assert c2 == {}


def test_multiple_hits_counted():
    out, c = _one("AKIAIOSFODNN7EXAMPLE and AKIAI44QH8DHBEXAMPLE")
    assert c["aws-key"] == 2


def test_non_string_and_empty_passthrough():
    assert redact.redact_text("") == ("", {})
    assert redact.redact_text(None) == (None, {})
    assert redact.redact_text(7) == (7, {})


# ---- #104 final-review fixes: backtracking bounds + marker idempotency ----


def test_redaction_markers_never_rematch_on_second_pass():
    # I3: [redacted:api-key] / [redacted:credential-url] markers satisfy the
    # patterns' own value class, so re-running redact_text over already-
    # redacted text (the anchor --attach rewrite path) must not re-count.
    s = ("set DAIMON_LLM_API_KEY=sk-abcdef1234567890 in env and "
         "postgres://admin:hunter2secret@db.example.com:5432/x")
    out, counts = _one(s)
    assert counts.get("api-key") == 1
    assert counts.get("credential-url") == 1
    out2, counts2 = _one(out)
    assert out2 == out
    assert counts2 == {}


def test_api_key_pattern_no_quadratic_blowup_on_long_word_run():
    # C1: unbounded prefix quantifier overlapping the keyword alternation
    # caused O(N^2) backtracking on long separator-free [\w-] runs.
    out, counts = _one("a" * 50000)
    assert counts == {}


def test_api_key_pattern_no_quadratic_blowup_on_long_hyphenated_run():
    out, counts = _one("a-" * 25000)
    assert counts == {}


def test_credential_url_pattern_no_quadratic_blowup_on_long_scheme_run():
    # I2: unbounded scheme span caused O(N^2) backtracking on long
    # lowercase-dotted runs that never resolve to "://".
    out, counts = _one("a." * 25000)
    assert counts == {}


# ---- #660: compatibility forms must not walk past the scrub --------------


def _wide(s):
    """ASCII -> fullwidth, the cheapest evasion of an uppercase-anchored rule."""
    return "".join(chr(ord(c) - 0x21 + 0xFF01) if 0x21 <= ord(c) <= 0x7E else c
                   for c in s)


def _synthetic_aws_key():
    """Built from parts so no credential-shaped literal sits in the source."""
    return "AK" + "IA" + "".join(chr(65 + (i * 7) % 26) for i in range(16))


def test_fullwidth_secret_is_detected_not_passed_through():
    # The scrub returned hits={} for this input, which a caller cannot tell
    # apart from "there was nothing to redact" — so `audit privacy` reported
    # the surface clean while the value sat in it, recoverable by one NFKC.
    key = _synthetic_aws_key()
    out, counts = _one(f"avoid {_wide(key)} here")
    assert counts.get("aws-key") == 1, f"compatibility form evaded the scrub: {counts}"
    assert key not in out


def test_fullwidth_secret_is_not_recoverable_by_normalizing_the_output():
    # The actual harm: not that the stored bytes ARE the key, but that one
    # normalize() call turns them back into it.
    import unicodedata
    key = _synthetic_aws_key()
    out, _ = _one(f"avoid {_wide(key)} here")
    assert key not in unicodedata.normalize("NFKC", out)


def test_math_alphanumeric_secret_is_detected():
    # Fullwidth is not the only compatibility block; the fix must be the
    # normalization, not a fullwidth-shaped special case.
    key = _synthetic_aws_key()
    bold = "".join(chr(0x1D400 + ord(c) - 65) if "A" <= c <= "Z" else c for c in key)
    _, counts = _one(f"avoid {bold} here")
    assert counts.get("aws-key") == 1, f"math-alphanumeric form evaded: {counts}"


def test_clean_text_is_returned_byte_identical():
    # Fold-on-hit-only: normalization is a side effect of redacting, never a
    # rewrite of content that had nothing to redact. Fullwidth CJK punctuation
    # in ordinary prose must survive untouched.
    original = "設定を確認する（フルワイド）and some ASCII"
    out, counts = _one(original)
    assert out == original
    assert counts == {}
