"""#421 slice 1: pure admission policy + kill-switch gate at the write boundary.

The ordered checkpoint admission pipeline (redact -> forget-gate -> id-stamp)
moves into `policy.admit_checkpoint`, a pure function with the same no-I/O
contract carry.py documents — the caller reads the forgotten-keys set off disk
and injects it. `store.write_checkpoint` gains the kill switch as its FIRST
gate so every entry point (hook, CLI serialize, model-authored write-checkpoint,
anchor rewrite) inherits one consistent answer — with a single narrow
exemption: `daimon forget` must still work while disabled (deletion is the one
promise that outranks "disabled means daimon writes nothing").
"""

import ast
import json
from pathlib import Path

from daimon_briefing import cli, normalize, redact, store
from tests.conftest import FIXTURES

_A = "/repo/policy-A"
# A secret redaction masks (AWS access key id: AKIA + 16 uppercase/digits).
_SECRET_RAW = "rotate the deploy key AKIAABCDEFGHIJKLMNOP before release"
_S = "adopt sqlite for the recall index cache"
_T = "adopt postgres for the analytics warehouse"


def _cp(sid, created, decisions):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {
            "recent_decisions": [{"text": d, "trust": "inferred"} for d in decisions]
        },
    }


def _valid_serialize_json(session_id="sample_transcript"):
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "t", "trust": "inferred"},
                "open_questions": [],
                "recent_decisions": [{"text": "adopt D-007", "trust": "inferred"}],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


# ---- (a) gate order: redact BEFORE forget-gate, stamp AFTER ----


def test_admit_checkpoint_forget_gate_sees_redacted_text():
    """Unit proof of the load-bearing order: the tombstone keys on the STORED
    (post-redaction) text, so a re-extraction carrying the RAW secret only
    folds to the tombstoned key if redaction runs before the forget gate."""
    from daimon_briefing import policy

    redacted, counts = redact.redact_text(_SECRET_RAW)
    assert counts  # the fixture really is secret-shaped
    forgotten = {normalize.content_key(redacted)}
    checkpoint = _cp("S2", "2026-07-03T00:00:00Z", [_SECRET_RAW, _T])

    dropped = policy.admit_checkpoint(checkpoint, forgotten)

    kept = checkpoint["working_context"]["recent_decisions"]
    assert [d["text"] for d in kept] == [_T]
    assert len(dropped) == 1
    # stamping runs AFTER the gate: survivors get ids, the dropped item never did
    assert kept[0].get("id")
    assert not dropped[0].get("id")


def test_write_checkpoint_drops_reasserted_secret_via_redacted_tombstone(
        tmp_checkpoint_dir, monkeypatch):
    """E2E through the store wiring: forget an item whose stored text was
    redacted, then re-capture the RAW sentence — the write pipeline must
    redact first so the forget gate matches and the value stays gone."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_SECRET_RAW]),
                           project_dir=_A)
    stored = store.read_latest(project_dir=_A, fallback=False)
    item = stored["working_context"]["recent_decisions"][0]
    assert "[redacted:aws-key]" in item["text"]  # the tombstone keys on THIS form
    assert cli.main(["forget", item["id"]]) == 0

    store.write_checkpoint("S2", _cp("S2", "2026-07-03T00:00:00Z", [_SECRET_RAW, _T]),
                           project_dir=_A)
    latest = store.read_latest(project_dir=_A, fallback=False)
    assert latest["session_id"] == "S2"
    assert [d["text"] for d in latest["working_context"]["recent_decisions"]] == [_T]


# ---- (b) kill switch: disabled writes nothing, from every entry ----


def test_disabled_write_checkpoint_refuses_and_touches_nothing(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    out = store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S]),
                                 project_dir=_A)
    assert out is None
    # no checkpoint file, no pointer, not even the directory
    assert not tmp_checkpoint_dir.exists()


def test_disabled_cli_serialize_writes_no_checkpoint(
        tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch):
    chat = fake_chat_factory(_valid_serialize_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_DISABLE", "1")

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0  # never-fatal posture, same as the ledger appenders
    out = capsys.readouterr().out
    assert "wrote checkpoint" not in out  # no lying success line ("... None")
    files = (list(tmp_checkpoint_dir.rglob("*.json"))
             if tmp_checkpoint_dir.exists() else [])
    assert files == []


def test_disabled_cli_write_checkpoint_writes_no_checkpoint(
        tmp_checkpoint_dir, capsys, monkeypatch):
    import io
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(_valid_serialize_json("S-intro")))
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    rc = cli.main(["write-checkpoint", "--project", _A])
    assert rc != 0  # a deliberate write command may say no, loudly
    files = (list(tmp_checkpoint_dir.rglob("*.json"))
             if tmp_checkpoint_dir.exists() else [])
    assert files == []


# ---- (c) deletion exemption: `daimon forget` works while disabled ----


def test_disabled_forget_still_removes_value_and_appends_tombstone(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S, _T]),
                           project_dir=_A)
    stored = store.read_latest(project_dir=_A, fallback=False)
    x_id = next(d["id"] for d in stored["working_context"]["recent_decisions"]
                if d["text"] == _S)

    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert cli.main(["forget", x_id]) == 0

    # the value left the live checkpoint on disk (latest AND per-session file)
    latest = store.read_latest(project_dir=_A, fallback=False)
    texts = [d["text"] for d in latest["working_context"]["recent_decisions"]]
    assert _S not in texts and _T in texts
    per_session = store.read_checkpoint("S1")
    texts = [d["text"] for d in per_session["working_context"]["recent_decisions"]]
    assert _S not in texts
    # and its tombstone landed despite the kill switch
    assert normalize.content_key(_S) in store.forgotten_content_keys(_A)


def test_exemption_is_narrow_default_paths_still_refuse(
        tmp_checkpoint_dir, monkeypatch):
    """Only the forget path opts out. A plain append_event and a plain
    write_checkpoint under the kill switch keep refusing."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", _A)
    store.write_checkpoint("S1", _cp("S1", "2026-07-01T00:00:00Z", [_S]),
                           project_dir=_A)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert store.append_event("r-x", "resolved", project_dir=_A) is False
    assert store.write_checkpoint("S2", _cp("S2", "2026-07-02T00:00:00Z", [_T]),
                                  project_dir=_A) is None


# ---- (d) purity guard: policy.py stays I/O-free (carry.py's contract) ----


def test_policy_module_is_pure():
    from daimon_briefing import policy

    src = Path(policy.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned_stdlib = {"os", "io", "pathlib", "shutil", "subprocess", "socket",
                     "tempfile", "sqlite3", "json", "logging"}
    # sibling modules that do I/O or read env — policy must not reach them
    banned_siblings = {"config", "store", "llm", "receipts", "teamproject",
                       "transcript", "recall", "ledger", "cli", "hooks"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned_stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if node.level:  # relative: from . import x, y
                hit = names & banned_siblings
                assert not hit, f"policy imports I/O sibling(s): {hit}"
            else:
                assert (node.module or "").split(".")[0] not in banned_stdlib
    for token in ("open(", ".write_text", ".read_text", ".write_bytes",
                  ".read_bytes", ".mkdir", "os.environ", "getenv"):
        assert token not in src, f"policy.py contains I/O-shaped token {token!r}"


# ---- #423: inbound gate — admit_foreign (scope, redact, forget, trust clamp) ----


def _foreign_cp():
    return {
        "session_id": "S-f",
        "author": "grace",
        "working_context": {
            "active_topic": {"text": "tuning the ingest path", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [
                {"text": _S, "trust": "verbatim", "quote": "we adopt sqlite"},
                {"text": _T, "trust": "inferred"},
            ],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }


def test_admit_foreign_out_of_scope_returns_none():
    from daimon_briefing import policy

    cp = _foreign_cp()
    assert policy.admit_foreign(
        cp, member=False, forgotten_keys=set(),
        redact_fn=redact.redact_text) is None


def test_admit_foreign_redacts_with_injected_fn():
    from daimon_briefing import policy

    cp = _foreign_cp()
    cp["working_context"]["recent_decisions"][0]["text"] = _SECRET_RAW
    out = policy.admit_foreign(
        cp, member=True, forgotten_keys=set(), redact_fn=redact.redact_text)
    assert out is cp  # in-place mutation, the established module contract
    text = out["working_context"]["recent_decisions"][0]["text"]
    assert "AKIA" not in text
    assert "[redacted:" in text
    assert out.get("redactions")  # visible counter, same as the write path


def test_admit_foreign_drops_locally_forgotten_value():
    from daimon_briefing import policy

    out = policy.admit_foreign(
        _foreign_cp(), member=True,
        forgotten_keys={normalize.content_key(_S)},
        redact_fn=redact.redact_text)
    kept = [d["text"] for d in out["working_context"]["recent_decisions"]]
    assert _S not in kept
    assert _T in kept


def test_admit_foreign_forget_gate_sees_redacted_text():
    """Order mirror of admit_checkpoint: redaction runs BEFORE the forget
    gate, so a foreign re-assertion of a redacted-then-forgotten sentence
    folds to the tombstoned (post-redaction) key and stays gone."""
    from daimon_briefing import policy

    redacted, counts = redact.redact_text(_SECRET_RAW)
    assert counts  # the fixture really is secret-shaped
    cp = _foreign_cp()
    cp["working_context"]["recent_decisions"][0]["text"] = _SECRET_RAW
    out = policy.admit_foreign(
        cp, member=True, forgotten_keys={normalize.content_key(redacted)},
        redact_fn=redact.redact_text)
    kept = [d["text"] for d in out["working_context"]["recent_decisions"]]
    assert kept == [_T]


def test_admit_foreign_clamps_verbatim_and_marks_claim():
    from daimon_briefing import policy

    out = policy.admit_foreign(
        _foreign_cp(), member=True, forgotten_keys=set(),
        redact_fn=redact.redact_text)
    first, second = out["working_context"]["recent_decisions"]
    assert first["trust"] == "inferred"
    assert first["foreign_verbatim_claim"] is True
    assert second["trust"] == "inferred"
    assert "foreign_verbatim_claim" not in second  # never claimed — unmarked


def test_admit_foreign_tolerates_malformed_checkpoints():
    from daimon_briefing import policy

    # A non-dict blob fails CLOSED (dropped), never raises.
    assert policy.admit_foreign(
        [1, 2], member=True, forgotten_keys=set(),
        redact_fn=redact.redact_text) is None
    # Junk items / missing sections ride through without raising.
    cp = {"working_context": {"recent_decisions": ["bare string", 7, None],
                              "active_topic": "not-a-dict"}}
    out = policy.admit_foreign(
        cp, member=True, forgotten_keys={normalize.content_key(_S)},
        redact_fn=redact.redact_text)
    assert out is cp
