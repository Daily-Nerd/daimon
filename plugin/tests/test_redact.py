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
