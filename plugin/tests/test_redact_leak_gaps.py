"""#132: close secret-leak gaps — quoted/JSON keys, common token prefixes,
password-only credential URLs. Each leak case is asserted red-first; a
precision block guards prose from over-redaction; a completion/timing block
guards scar-0022's ReDoS bound on the new/changed patterns."""

import time
from pathlib import Path

from daimon_briefing import redact


def _one(s):
    out, counts = redact.redact_text(s)
    return out, counts


# ---- gap 1: quoted / JSON key-value ----------------------------------------


def test_json_api_key_value_redacted():
    out, c = _one('{"api_key": "sk-liveAbC123456789"}')
    assert "sk-liveAbC123456789" not in out and c.get("api-key") == 1


def test_json_password_value_redacted():
    out, c = _one('{"password": "supersecret123"}')
    assert "supersecret123" not in out and c.get("api-key") == 1


def test_single_quoted_json_key_redacted():
    out, c = _one("'secret' = 'topsecretvalue1'")
    assert "topsecretvalue1" not in out and c.get("api-key") == 1


# ---- gap 2: common fixed-prefix tokens -------------------------------------


def test_github_personal_token_redacted():
    out, c = _one("token ghp_" + "A" * 36 + " committed")
    assert "ghp_" + "A" * 36 not in out and c.get("github-token") == 1


def test_github_fine_grained_pat_redacted():
    out, c = _one("use github_pat_" + "B" * 40 + " here")
    assert "B" * 40 not in out and c.get("github-token") == 1


def test_gitlab_token_redacted():
    out, c = _one("glpat-" + "C" * 20 + " leaked")
    assert "C" * 20 not in out and c.get("gitlab-token") == 1


def test_slack_bot_token_redacted():
    out, c = _one("xoxb-123456789012-abcdefghijkl in config")
    assert "abcdefghijkl" not in out and c.get("slack-token") == 1


def test_google_api_key_redacted():
    out, c = _one("key AIza" + "D" * 35 + " loaded")
    assert "AIza" + "D" * 35 not in out and c.get("google-key") == 1


def test_openai_key_redacted():
    out, c = _one("sk-proj-" + "E" * 40 + " set")
    assert "E" * 40 not in out and c.get("openai-key") == 1


def test_bare_jwt_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abcDEF123_-xyz"
    out, c = _one("session " + jwt + " expired")
    assert jwt not in out and c.get("jwt") == 1


# ---- gap 3: password-only credential URLs ----------------------------------


def test_password_only_redis_url_redacted():
    out, c = _one("redis://:mypassword123@localhost:6379/0")
    assert "mypassword123" not in out
    assert "redis://:[redacted:credential-url]@localhost" in out
    assert c.get("credential-url") == 1


def test_password_only_amqp_url_redacted():
    out, c = _one("amqp://:secretpass@rabbit:5672")
    assert "secretpass" not in out
    assert "amqp://:[redacted:credential-url]@rabbit" in out
    assert c.get("credential-url") == 1


def test_user_and_password_credential_url_still_redacted():
    out, c = _one("postgres://admin:hunter2secret@db.example.com:5432/x")
    assert "hunter2secret" not in out
    assert "postgres://admin:[redacted:credential-url]@db.example.com" in out


# ---- precision: prose must survive untouched -------------------------------


def test_prose_not_over_redacted():
    for s in (
        "the token budget was blown",
        "password rotation policy is quarterly",
        "see the api key rotation notes",
        "the deploy secret is documented elsewhere",
        "https://docs.example.com/path and http://localhost:8080/x",
    ):
        out, c = _one(s)
        assert c == {}, f"over-redacted: {s!r} -> {c}"
        assert out == s


# ---- scar-0022: no catastrophic backtracking on the new/changed patterns ---


def test_no_backtracking_on_adversarial_inputs():
    # Completion IS the signal (scar-0022 convention: python re has no timeout,
    # a quadratic blowup hangs rather than raises). The documented bad case ran
    # 7-28s; a generous 2s ceiling cleanly separates linear from quadratic
    # without the CI flakiness of a tight bound.
    adversarial = (
        ("a-" * 5000) + "password=" + "x" * 8,   # bounded-prefix keyword run
        ("password" + " " * 20000 + "= " + "y" * 8),  # keyword + whitespace run
        "eyJ" + "a" * 50000,                     # near-JWT, no dots
        "eyJ" + "a" * 20000 + ".eyJ" + "a" * 20000,  # two segments, no 3rd dot
        "sk-" + "z" * 50000,                     # long token body
        "a." * 25000,                            # long scheme run, no ://
    )
    start = time.perf_counter()
    for s in adversarial:
        _one(s)
    assert time.perf_counter() - start < 2.0


# ---- twin: canonical module and shipped hook copy stay byte-identical ------


def test_redact_twin_byte_identical():
    canonical = (Path(redact.__file__)).read_bytes()
    twin = (Path(redact.__file__).parent / "_hooks" / "redact.py").read_bytes()
    assert twin == canonical, "redact.py and _hooks/redact.py drifted"
