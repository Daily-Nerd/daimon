import json
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
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    monkeypatch.delenv("DAIMON_AUTHOR", raising=False)
    # Clear kill switch / overrides that may leak from the host env.
    monkeypatch.delenv("DAIMON_DISABLE", raising=False)
    monkeypatch.delenv("DAIMON_MIN_MESSAGES", raising=False)
    monkeypatch.setenv("DAIMON_PLAIN", "1")  # tests assert plain output deterministically
    return home


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
