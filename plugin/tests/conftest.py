from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_daimon_home(tmp_path, monkeypatch):
    """Total isolation: no test may read or write the developer's real ~/.daimon.

    Redirects the env file, checkpoint dir, AND serialize-log dir under tmp, and
    clears host overrides. Isolation is automatic (autouse) — the opt-in fixtures
    below just expose these paths — so a serialize test that forgets a fixture
    still cannot leak its result line into the real ledger (issue #54)."""
    home = tmp_path / ".daimon"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(home / "no-such-env-file"))
    monkeypatch.setenv("DAIMON_CHECKPOINT_DIR", str(home / "checkpoints"))
    monkeypatch.setenv("DAIMON_LOG_DIR", str(home / "logs"))
    # Redirect the #111 team mirror too — no test may read or write the real
    # ~/.daimon/team, and dual-write is off by default so a stray host DAIMON_TEAM
    # can't make every test mirror.
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(home / "team"))
    # Redirect the #112 recall index too — a recall test must never rebuild or
    # read the developer's real derived index (which scans the real history).
    monkeypatch.setenv("DAIMON_RECALL_DB", str(home / "recall.db"))
    # And the #125 per-session suggestion-cooldown state, same reasoning.
    monkeypatch.setenv("DAIMON_RECALL_SEEN_DIR", str(home / "recall_seen"))
    # #607: the Windsurf adapter's own transcript store — no test may read or
    # write the developer's real ~/.daimon/windsurf, and the purge/reap paths
    # DELETE, so an unredirected default would destroy real host state.
    monkeypatch.setenv("DAIMON_WINDSURF_DIR", str(home / "windsurf"))
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    monkeypatch.delenv("DAIMON_AUTHOR", raising=False)
    # #896: and the host-declared per-session speaker — a developer who exports
    # it in their own shell must not silently attribute every derived item.
    monkeypatch.delenv("DAIMON_SESSION_SPEAKER", raising=False)
    # #899: a developer's own tenant flags must not narrow or widen the suite.
    monkeypatch.delenv("DAIMON_TENANT_SCOPED", raising=False)
    monkeypatch.delenv("DAIMON_EXTRA_READ_SLUGS", raising=False)
    # #200: a host DAIMON_TEAM_PROJECT would nest every team write; and the
    # resolver caches per (project_dir, team_dir, env) — clear it so no test
    # can see another test's resolution.
    monkeypatch.delenv("DAIMON_TEAM_PROJECT", raising=False)
    from daimon_briefing import teamproject
    teamproject._cache.clear()
    # Clear kill switch / overrides that may leak from the host env.
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
    monkeypatch.delenv("DAIMON_MIN_MESSAGES", raising=False)
    monkeypatch.setenv("DAIMON_PLAIN", "1")  # tests assert plain output deterministically
    return home


@pytest.fixture(autouse=True)
def _reset_llm_module_state():
    """#458/#461: llm keeps module-sticky per-unit-of-work state (the #28
    fallback flag, the #458 served-model collector) — clear both per test.
    Without the fallback reset, test_llm's fallback tests leak
    `_fallback_used = True` into any later chunk-cache test in the same
    process and `_save_chunk_cache` refuses writes: the file-pair run
    `pytest tests/test_llm.py tests/test_serializer.py` failed 7 tests
    while the full suite happened to dodge the ordering (#461)."""
    from daimon_briefing import llm
    llm.reset_fallback()
    llm.reset_served_models()


# ---- fake vitni CLI (#204 receipts, #439 worldcheck receipt-validity) -------
#
# CI has NO node and NO vitni CLI, so every test that needs the signer/verifier
# stubs it with this fake executable (a python script echoing canned JSON and
# capturing the stdin it received, so the input contract can be asserted).
# Lifted here from test_receipts.py when #439 gave worldcheck a second consumer
# — the CLI contract is ONE contract, and two divergent copies of the fake
# would let the two call sites drift apart silently.

_FAKE_CLI_SRC = r'''#!/usr/bin/env python3
import sys, json, os
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.stdin.read()
cap = os.environ.get("FAKE_VITNI_CAPTURE")
if cap:
    with open(cap, "a") as f:
        f.write(json.dumps({"cmd": cmd, "stdin": raw}) + "\n")
mode = os.environ.get("FAKE_VITNI_MODE", "ok")
if mode == "garbage":
    sys.stdout.write("not json at all")
    sys.exit(0)
if mode == "rc1":
    sys.stderr.write("boom")
    sys.exit(1)
if mode == "hang":
    import time
    time.sleep(30)
try:
    data = json.loads(raw)
except ValueError:
    print(json.dumps({"error": "invalid_json"})); sys.exit(0)
if cmd == "sign":
    print(json.dumps({"signed_receipt": os.environ.get("FAKE_VITNI_JWS", "aaa.bbb.ccc")}))
elif cmd == "verify":
    verdict = os.environ.get("FAKE_VITNI_VERDICT", "ok")
    if verdict == "ok":
        print(json.dumps({"valid": True, "reason": "ok"}))
    else:
        print(json.dumps({"valid": False, "reason": verdict}))
elif cmd == "keygen":
    kmode = os.environ.get("FAKE_VITNI_KEYGEN_MODE", "ok")
    if kmode == "unknown":          # simulate an old CLI without keygen
        print(json.dumps({"error": "unknown_command"})); sys.exit(0)
    if kmode == "error":            # {"error"} on exit 0 — never rc
        print(json.dumps({"error": "invalid_seed"})); sys.exit(0)
    if kmode == "nojwk":            # malformed output shape
        print(json.dumps({"private_key_b64": data.get("seed_b64", "")})); sys.exit(0)
    seed = data.get("seed_b64")
    if seed == "":                 # present-but-empty != absent -> invalid_seed
        print(json.dumps({"error": "invalid_seed"})); sys.exit(0)
    PROBE = "nWGxne/9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A="
    PROBE_X = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
    if seed == PROBE:
        x = os.environ.get("FAKE_VITNI_PROBE_X", PROBE_X)
    else:
        x = os.environ.get("FAKE_VITNI_KEYGEN_X",
                           "Kg2fakeKEYGENxKg2fakeKEYGENxKg2fakeKEYGENxK")
    print(json.dumps({"jwk": {"alg": "EdDSA", "crv": "Ed25519", "kty": "OKP",
                              "status": "active", "x": x},
                      "private_key_b64": seed}))
else:
    print(json.dumps({"error": "unknown_command"}))
sys.exit(0)
'''


@pytest.fixture
def fake_cli_src():
    """The fake CLI's source, for tests that need to install it themselves
    (e.g. two distinct binaries to prove a per-binary cache)."""
    return _FAKE_CLI_SRC


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Install a fake vitni CLI on DAIMON_VITNI_CLI + capture file. Returns the
    capture-path so a test can assert what daimon actually sent on stdin (and,
    by its absence, that the CLI was never invoked at all)."""
    script = tmp_path / "fake-vitni"
    script.write_text(_FAKE_CLI_SRC)
    script.chmod(0o755)
    capture = tmp_path / "vitni-capture.jsonl"
    monkeypatch.setenv("DAIMON_VITNI_CLI", str(script))
    monkeypatch.setenv("FAKE_VITNI_CAPTURE", str(capture))
    return capture


@pytest.fixture
def tmp_checkpoint_dir(tmp_path):
    # The autouse fixture already points DAIMON_CHECKPOINT_DIR here; expose the path.
    return tmp_path / ".daimon" / "checkpoints"


@pytest.fixture
def sample_checkpoint():
    """A valid checkpoint with an external-state open question (the PR-merge gap)."""
    return {
        "session_id": "S-prev",
        "working_context": {
            "active_topic": {"text": "Wiring the on_session_end hook", "trust": "inferred"},
            "open_questions": [
                {
                    "text": "PR #6 state — user said they'd merge it from the GitHub UI",
                    "trust": "verbatim",
                    "quote": "I'll merge it myself later from the GitHub UI",
                    "external_state": True,
                },
                {
                    "text": "Chunk threshold for the serializer",
                    "trust": "verbatim",
                    "quote": "do we chunk below 1200 lines or single-pass?",
                },
            ],
            "recent_decisions": [
                {
                    "text": "Adopt the D-007 prompt for the serializer",
                    "trust": "verbatim",
                    "quote": "we adopt the D-007 prompt for the serializer",
                },
                {"text": "Single-pass for Slice 1, chunking is Slice 2", "trust": "inferred"},
            ],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [
                {"text": "Extractive pinning prevents silent fact loss", "trust": "inferred"}
            ],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
        "worker_queue": [{"task": "Wire on_session_end hook", "status": "pending"}],
    }


class FakeChat:
    """Injectable replacement for llm.chat — records calls, returns a canned response.

    response may be a list: one response per call, in order (for multi-call flows
    like chunked serialization). A list entry that is an Exception is raised.
    """

    def __init__(self, response):
        self._response = response
        self.calls = []

    def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        response = self._response
        if isinstance(response, list):
            if len(self.calls) > len(response):
                raise AssertionError("FakeChat: more calls than scripted responses")
            response = response[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_chat_factory():
    return FakeChat


def make_messages(n):
    """n alternating user/assistant messages with quotable content."""
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({"role": role, "content": f"line {i} from {role}"})
    return out
