"""#1236 slice 2: `daimon import --transcript PATH ...`, the bounded first fill.

Every test runs with `DAIMON_CHECKPOINT_DIR`, `DAIMON_LOG_DIR` and `HOME`
under tmp (conftest's autouse isolation plus the `home` fixture here), with
`cli._chat` faked, so no model is called and no real store is touched. The
fake counts its calls: "zero spend" is asserted as zero calls, never
inferred from output. `_reporting` wraps it as a backend that records one
usage record per answered call, the way `llm` does for the real ones, so
the checkpoint's `capture_cost.calls` stamp (the number the bound reads)
equals the fake's call count.
"""

import json
import os

import pytest

from daimon_briefing import (
    capture,
    cli,
    importer,
    ledger,
    llm,
    provenance,
    serializer,
    store,
    transcript,
)
from daimon_briefing.surfaces import Writer

PROJECT = "/p/import-cmd"
OLD = 1_782_000_000  # a fixed mtime well outside the live window

_STAMPS = ["2026-07-01T10:00:00Z", "2026-08-01T10:00:00Z", "2026-09-01T10:00:00Z"]
KIMI_MS = 1_782_000_000_000


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME under tmp beside the autouse DAIMON_* isolation, with the Claude
    Code projects root and the Kimi home under it, so every host root the
    plan consults is a tmp path."""
    h = tmp_path / "home"
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(h / ".claude" / "projects"))
    monkeypatch.setenv("KIMI_CODE_HOME", str(h / ".kimi-code"))
    monkeypatch.setenv("CODEX_HOME", str(h / ".codex"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    return h


def _valid_json(text="an open loop"):
    return json.dumps(
        {
            "session_id": "ignored",
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [{"text": text, "trust": "inferred"}],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


# Parseable, and rejected by validation: no working_context at all. The
# serializer resamples once (#118), so the session costs one extra call.
_INVALID_JSON = json.dumps({"session_id": "ignored"})


def _rows(ended, n=4, cwd=PROJECT):
    rows = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        row = {
            "type": role,
            "message": {"role": role, "content": f"turn {i}"},
            "timestamp": ended,
        }
        if cwd is not None:
            row["cwd"] = cwd
        rows.append(row)
    return rows


def _transcript(directory, sid, ended, n=4, cwd=PROJECT, mtime=OLD, suffix=".jsonl"):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{sid}{suffix}"
    p.write_text(
        "\n".join(json.dumps(r) for r in _rows(ended, n, cwd)), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _three(tmp_path):
    return [_transcript(tmp_path / "t", f"S-{i}", _STAMPS[i]) for i in range(3)]


def _kimi_wire(home, sid, workspace="wd_proj_0123456789ab", n=4):
    """A Kimi wire.jsonl at the measured layout, with `n` conversation
    messages and no cwd anywhere."""
    p = (
        home
        / ".kimi-code"
        / "sessions"
        / workspace
        / sid
        / "agents"
        / "main"
        / "wire.jsonl"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "metadata", "protocol_version": "1.0", "created_at": KIMI_MS}]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append(
            {
                "type": "context.append_message",
                "agentId": "main",
                "message": {
                    "role": role,
                    "content": [{"type": "text", "text": f"kimi turn {i}"}],
                    "toolCalls": [],
                    "origin": {"kind": "user"},
                },
                "time": KIMI_MS + i,
            }
        )
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (OLD, OLD))
    return p


def _argv(paths, *extra, project=PROJECT):
    argv = ["import"]
    if project is not None:
        argv += ["--project", project]
    for p in paths:
        argv += ["--transcript", str(p)]
    return argv + list(extra)


def _log_text(tmp_path):
    p = tmp_path / ".daimon" / "logs" / "serialize.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


class _Reporting:
    """A backend that answers from `inner` and records one usage record per
    answered call (#1235), silent unless `usage` names a dict for it."""

    def __init__(self, inner, usage=()):
        self.inner = inner
        self.calls = inner.calls
        self._usage = iter(usage)

    def __call__(self, messages, **kwargs):
        out = self.inner(messages, **kwargs)
        usage = next(self._usage, None)
        llm._note_call_usage("fake", "m", llm._normalize_usage(usage), None)
        return out


def _reporting(fake_chat_factory, responses, usage=()):
    return _Reporting(fake_chat_factory(responses), usage)


# ---- the plan: estimate, order, zero spend ----------------------------------


def test_plan_prints_the_estimate_oldest_first_and_spends_nothing(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(reversed(paths), "--plan")) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd (manual): 3 sessions, about 3 model calls" in out
    lines = [ln for ln in out.splitlines() if ln.strip().startswith(("1 ", "2 ", "3 "))]
    assert [ln.split()[-1] for ln in lines] == ["S-0", "S-1", "S-2"]
    assert lines[0].split()[1] == "2026-07-01"
    assert chat.calls == []
    assert not (tmp_path / ".daimon" / "checkpoints").exists()


def test_the_call_estimate_is_the_pipelines_own_count_over_two_merge_levels(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """A chunked transcript costs chunks plus merges over every merge level;
    the plan says so before the run, and the run then makes exactly that
    many calls (M3: one statement of the merge tree's shape)."""
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "3")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "0")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    chat = _reporting(fake_chat_factory, _valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-long", _STAMPS[0], n=20)
    messages = transcript.from_file(p)
    planned = serializer.planned_calls(messages)
    rendered = serializer._render_transcript(serializer.extraction_messages(messages))
    n_chunks = len(serializer.chunk_transcript(rendered, 3, 0))
    assert len(list(serializer.merge_call_groups(n_chunks, 3))) >= 2
    assert cli.main(_argv([p], "--plan")) == 0
    assert f"1 session, about {planned} model calls" in capsys.readouterr().out
    assert chat.calls == []
    assert cli.main(_argv([p])) == 0
    assert len(chat.calls) == planned
    assert (
        f"imported 1 of 1, {planned} calls ({planned} planned)"
        in capsys.readouterr().out
    )


def test_merge_call_groups_feed_both_the_wave_plan_and_the_call_count():
    assert list(serializer.merge_call_groups(1, 3)) == []
    assert list(serializer.merge_call_groups(4, 3)) == [1, 1]  # (3)(1) -> 2 -> 1
    assert list(serializer.merge_call_groups(13, 3)) == [4, 2, 1]
    assert serializer.merge_calls(9, 3) == 4
    # _plan_waves consumes the same groups: 4 chunks, 4 workers, k=3 is one
    # chunk wave plus one wave per level that costs a call.
    assert serializer._plan_waves(4, workers=4, k=3) == 3
    assert serializer._plan_waves(13, workers=4, k=3) == 4 + 1 + 1 + 1


# ---- the bound: spent calls, not planned ones -------------------------------


def test_a_bounded_run_stops_before_the_session_that_would_pass_max_calls(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = _reporting(fake_chat_factory, _valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths, "--max-calls", "2")) == 0
    out = capsys.readouterr().out
    assert len(chat.calls) == 2
    assert store.read_checkpoint("S-0") is not None
    assert store.read_checkpoint("S-1") is not None
    assert store.read_checkpoint("S-2") is None
    assert (
        "stopped before [3/3] S-2: its 1 planned call would pass "
        "--max-calls 2 (2 spent so far). 2 of 3 imported, 1 left."
    ) in out
    assert cli.import_cmd.RESUME_HINT in out
    assert out.rstrip().endswith(
        "imported 2 of 3, 2 calls (2 planned), 0 tokens reported, 2 sessions unreported"
    )


def test_the_bound_reads_spent_calls_so_a_resample_counts(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """H1: the first session fails validation once and resamples, so it
    costs two calls where the plan said one. The default bound (3 planned)
    then stops the run before the third session, and the summary says what
    was spent beside what was planned."""
    chat = _reporting(
        fake_chat_factory, [_INVALID_JSON, _valid_json(), _valid_json(), _valid_json()]
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out
    assert "3 sessions, about 3 model calls" in out
    assert len(chat.calls) == 3
    assert store.read_checkpoint("S-0")["capture_cost"]["calls"] == 2
    assert store.read_checkpoint("S-2") is None
    assert (
        "stopped before [3/3] S-2: its 1 planned call would pass "
        "--max-calls 3 (3 spent so far)"
    ) in out
    assert out.rstrip().endswith(
        "imported 2 of 3, 3 calls (2 planned), 0 tokens reported, 2 sessions unreported"
    )


def test_max_calls_rejects_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["import", "--project", PROJECT, "--transcript", "x", "--max-calls", "0"]
        )
    assert exc.value.code == 2
    assert "--max-calls needs a count of 1 or more" in capsys.readouterr().err


def test_max_calls_rejects_a_word(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["import", "--project", PROJECT, "--transcript", "x", "--max-calls", "two"]
        )
    assert exc.value.code == 2
    assert "not a whole number: 'two'" in capsys.readouterr().err


# ---- the first failure stops the run ------------------------------------------


def test_a_partial_run_stops_at_the_first_failure_with_the_hint_and_summary(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = _reporting(
        fake_chat_factory, [_valid_json(), llm.ChatError("LLM HTTP 429"), _valid_json()]
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths)) == 1
    captured = capsys.readouterr()
    assert len(chat.calls) == 2  # the third session was never started
    assert store.read_checkpoint("S-0") is not None
    assert store.read_checkpoint("S-1") is None
    assert "[2/3] S-1: error after" in captured.out
    assert (
        "stopped after the first failed session. 1 of 3 imported, 2 left."
        in captured.out
    )
    assert cli.import_cmd.RESUME_HINT in captured.out
    # L2: the summary prints on every exit after spend.
    assert captured.out.rstrip().endswith(
        "imported 1 of 3, 1 call (1 planned), 0 tokens reported, 1 session unreported"
    )
    assert "error: " in captured.err and "S-1.jsonl" in captured.err
    text = _log_text(tmp_path)
    assert "import: started serialize for S-2" not in text
    # The failed attempt is an import attempt in the ledger, never heal's.
    led = cli._session_ledger(text, now=0.0)
    assert led["S-1"]["reason"] == ledger.IMPORT_REASON
    assert led["S-1"]["result_kind"] == "error"


# ---- resume ---------------------------------------------------------------------


def test_a_resume_skips_done_sessions_at_zero_cost_and_says_so(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = _reporting(
        fake_chat_factory,
        [_valid_json(), llm.ChatError("quota"), _valid_json(), _valid_json()],
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths)) == 1
    assert len(chat.calls) == 2
    capsys.readouterr()
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out
    assert "2 sessions, about 2 model calls" in out
    assert "skipped S-0: already has a checkpoint" in out
    assert len(chat.calls) == 4
    assert "imported 2 of 2, 2 calls (2 planned)" in out
    capsys.readouterr()
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out
    assert "0 sessions, about 0 model calls" in out
    assert "nothing to import" in out
    assert len(chat.calls) == 4


def test_a_pruned_checkpoint_is_still_done_by_its_success_line(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """The flat store prunes old checkpoints first; the success line in
    serialize.log is what keeps a resume from paying again, for an import
    and for a live capture alike (M2)."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-gone", _STAMPS[0])
    assert cli.main(_argv([p])) == 0
    assert len(chat.calls) == 1
    (tmp_path / ".daimon" / "checkpoints" / "S-gone.json").unlink()
    assert store.read_checkpoint("S-gone") is None
    # A live capture of another session, pruned the same way: only its
    # suffix-less success line remains.
    live = _transcript(tmp_path / "t", "S-live-gone", _STAMPS[1])
    ledger._append_serialize_log(
        f"wrote checkpoint: {tmp_path}/.daimon/checkpoints/S-live-gone.json (took 9s)"
    )
    capsys.readouterr()
    assert cli.main(_argv([p, live])) == 0
    out = capsys.readouterr().out
    assert "skipped S-gone: already captured (serialize.log)" in out
    assert "skipped S-live-gone: already captured (serialize.log)" in out
    assert len(chat.calls) == 1


def test_captured_sessions_reads_every_success_line_whatever_its_suffix():
    text = "\n".join(
        [
            "wrote checkpoint: /x/S-a.json (took 3s) [import]",
            "wrote checkpoint: /x/S-b.json (took 3s) [fallback backend] [import]",
            "wrote checkpoint: /x/S-live.json (took 3s)",
            "wrote checkpoint: /x/S-fb.json (took 3s) [fallback backend]",
            "wrote checkpoint: /x/rollout-2026-10-01T10-00-00-abc.json (took 1s) [import]",
            "2026-10-26T14:02:11Z import: started serialize for S-hung (reason: import, project: /p) (transcript: /t/S-hung.jsonl)",
            "skipped serialize for S-short: transcript too short (1 < 3 messages)",
            "error: boom (transcript: /t/S-err.jsonl) after 1s",
        ]
    )
    assert ledger.captured_sessions(text) == frozenset(
        {"S-a", "S-b", "S-live", "S-fb", "abc"}
    )


def test_the_done_set_agrees_with_the_heal_classifier_on_one_store(
    tmp_path, home, monkeypatch
):
    """L6: one store state, a plain checkpoint and a Codex rollout one, both
    with a failed attempt in the log beside a third session that has no
    checkpoint. `_compute_outstanding` (status and heal) and `plan_import`
    name the same session as the only one still to do."""
    rollout = "rollout-2026-10-01T10-00-00-abc"
    for sid in ("S-plain", rollout):
        store.write_checkpoint(
            sid,
            json.loads(_valid_json()) | {"session_id": sid},
            project_dir=PROJECT,
            writer=Writer.HUMAN,
        )
    paths = [
        _transcript(tmp_path / "t", sid, _STAMPS[0])
        for sid in ("S-plain", rollout, "S-lost")
    ]
    now = OLD + 10_000.0  # past the live window of the fixed transcript mtime
    stamp = "2026-06-21T20:00:00Z"
    lines = []
    for sid, p in zip(("S-plain", "abc", "S-lost"), paths):
        lines.append(
            f"{stamp} session-end: spawned serialize for {sid} "
            f"(reason: exit, project: {PROJECT}) (transcript: {p})"
        )
        lines.append(f"error: boom (transcript: {p}) after 1s")
    text = "\n".join(lines) + "\n"
    outstanding = {f["sid"] for f in ledger._compute_outstanding(text, now + 100)}
    plan = importer.plan_import(PROJECT, paths, now=now, log_text=text)
    assert outstanding == {"S-lost"}
    assert [s.session_id for s in plan.sessions] == ["S-lost"]
    assert {k.session_id: k.reason for k in plan.skipped} == {
        "S-plain": "already has a checkpoint",
        rollout: "already has a checkpoint",
    }


# ---- refusals before spend --------------------------------------------------------


def test_a_project_mismatch_refuses_before_spend(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    good = _transcript(tmp_path / "t", "S-mine", _STAMPS[0])
    other = _transcript(tmp_path / "t", "S-theirs", _STAMPS[1], cwd="/p/other")
    assert cli.main(_argv([good, other])) == 2
    captured = capsys.readouterr()
    assert f"refused: transcript belongs to /p/other, not {PROJECT}" in captured.err
    assert "import plan" not in captured.out
    assert chat.calls == []
    assert "import:" not in _log_text(tmp_path)


def test_a_transcript_with_no_recorded_cwd_outside_every_host_root_is_refused(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    loose = _transcript(tmp_path / "t", "S-loose", _STAMPS[0], cwd=None)
    assert cli.main(_argv([loose])) == 2
    err = capsys.readouterr().err
    assert "records no project directory and is not under" in err
    assert chat.calls == []
    export = _transcript(tmp_path / "t", "notes", _STAMPS[0], cwd=None, suffix=".md")
    assert cli.main(_argv([export])) == 2
    assert "notes.md records no project directory" in capsys.readouterr().err
    assert chat.calls == []
    root = home / ".claude" / "projects" / store.project_slug(PROJECT)
    housed = _transcript(root, "S-housed", _STAMPS[0], cwd=None)
    assert cli.main(_argv([housed], "--plan")) == 0
    assert "1 session, about 1 model call" in capsys.readouterr().out


def test_a_windsurf_transcript_needs_the_project_typed_on_the_command_line(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    ws = _transcript(home / ".windsurf" / "transcripts", "ws-1", _STAMPS[0], cwd=None)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(_argv([ws], project=None)) == 2
    err = capsys.readouterr().err
    assert "ws-1.jsonl records no project directory (windsurf never does)" in err
    assert "--project" in err
    assert chat.calls == []
    assert cli.main(_argv([ws])) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd (windsurf): 1 session" in out
    assert "ws-1  note: project from --project (windsurf records none)" in out
    assert len(chat.calls) == 1
    assert store.read_checkpoint("ws-1")["project_slug"] == store.project_slug(PROJECT)


def test_two_kimi_sessions_plan_as_two_ids_and_write_two_checkpoints(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """H2: every Kimi file is `wire.jsonl`; the session is the directory
    segment, and the marker, the checkpoint and the done check all use it."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    a = _kimi_wire(home, "k-aaaa")
    b = _kimi_wire(home, "k-bbbb", workspace="wd_proj_fedcba987654")
    assert cli.main(_argv([a, b])) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd (kimi): 2 sessions" in out
    assert "k-aaaa  note: project from --project (kimi records none)" in out
    assert "k-bbbb  note: project from --project (kimi records none)" in out
    assert len(chat.calls) == 2
    assert store.read_checkpoint("k-aaaa") is not None
    assert store.read_checkpoint("k-bbbb") is not None
    assert store.read_checkpoint("wire") is None
    text = _log_text(tmp_path)
    assert "import: started serialize for k-aaaa (reason: import" in text
    assert "import: started serialize for k-bbbb (reason: import" in text
    capsys.readouterr()
    assert cli.main(_argv([a, b])) == 0
    out = capsys.readouterr().out
    assert "skipped k-aaaa: already has a checkpoint" in out
    assert "skipped k-bbbb: already has a checkpoint" in out
    assert len(chat.calls) == 2


def test_session_key_is_the_stem_except_on_kimi(tmp_path):
    kimi = (
        tmp_path / "sessions" / "wd_x_0123" / "k-1" / "agents" / "main" / "wire.jsonl"
    )
    assert provenance.session_key(kimi, host="kimi") == "k-1"
    assert provenance.session_key(kimi, host="manual") == "wire"
    assert provenance.session_key(tmp_path / "S-1.jsonl", host="claude-code") == "S-1"
    rollout = tmp_path / "rollout-2026-10-01T10-00-00-abc.jsonl"
    assert provenance.session_key(rollout, host="codex") == rollout.stem
    assert provenance.HOSTS_RECORDING_NO_PROJECT == frozenset({"windsurf", "kimi"})


def test_one_session_named_by_two_files_is_refused(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    a = _transcript(tmp_path / "a", "S-dup", _STAMPS[0])
    b = _transcript(tmp_path / "b", "S-dup", _STAMPS[1])
    assert cli.main(_argv([a, b])) == 2
    err = capsys.readouterr().err
    assert f"refused: two transcripts name session S-dup: {a} and {b}" in err
    assert chat.calls == []


def test_a_path_given_twice_is_planned_once(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-twice", _STAMPS[0])
    relative = os.path.relpath(p, os.getcwd())
    assert cli.main(_argv([p, relative], "--plan")) == 0
    out = capsys.readouterr().out
    assert "1 session, about 1 model call" in out
    assert out.count("S-twice") == 1


def test_recorded_cwd_reads_claude_rows_and_codex_meta(tmp_path):
    p = tmp_path / "claude.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) for r in _rows(_STAMPS[0], cwd="/p/x")),
        encoding="utf-8",
    )
    assert transcript.recorded_cwd(p) == "/p/x"
    c = tmp_path / "rollout-2026-10-01T10-00-00-abc.jsonl"
    c.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/p/codex"}})
        + "\n"
        + json.dumps({"role": "user", "content": "hi"}),
        encoding="utf-8",
    )
    assert transcript.recorded_cwd(c) == "/p/codex"
    n = tmp_path / "none.jsonl"
    n.write_text(
        json.dumps({"role": "user", "content": "hi", "cwd": ""}), encoding="utf-8"
    )
    assert transcript.recorded_cwd(n) is None
    assert transcript.recorded_cwd(tmp_path / "missing.jsonl") is None
    assert transcript.recorded_cwd(tmp_path / "notes.md") is None


def test_recorded_cwd_stops_at_the_first_row_and_never_raises(tmp_path):
    row = json.dumps({"type": "user", "cwd": "/p/x", "timestamp": _STAMPS[0]})
    cut = tmp_path / "S-cut.jsonl"
    # A tail cut mid-character by a crashed host: invalid UTF-8 after row 1.
    cut.write_bytes(row.encode() + b"\n" + b'{"cwd": "/p/y\xe2\x80')
    assert transcript.recorded_cwd(cut) == "/p/x"
    head = tmp_path / "S-head.jsonl"
    head.write_bytes(b"\xff\xfe garbage\n" + row.encode())
    assert transcript.recorded_cwd(head) is None
    assert transcript.last_timestamp(cut) is None
    os.utime(cut, (OLD, OLD))
    assert capture.session_end_stamp(cut) == "2026-06-21T00:00:00Z"
    loose = tmp_path / "S-loose.jsonl"
    loose.write_text(
        'not json\n[1, 2]\n{"payload": {"cwd": "  "}}\n{"payload": {"cwd": "/p/z"}}\n',
        encoding="utf-8",
    )
    assert transcript.recorded_cwd(loose) == "/p/z"


def test_the_kill_switch_refuses_before_spend(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert cli.main(_argv(_three(tmp_path))) == 2
    assert "DAIMON_DISABLE" in capsys.readouterr().err
    assert chat.calls == []


def test_a_missing_transcript_is_a_named_skip_not_a_refusal(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    good = _transcript(tmp_path / "t", "S-here", _STAMPS[0])
    assert cli.main(_argv([tmp_path / "t" / "S-away.jsonl", good])) == 0
    out = capsys.readouterr().out
    assert "skipped S-away: transcript missing" in out
    assert "imported 1 of 1" in out
    assert len(chat.calls) == 1


def test_a_too_short_transcript_is_skipped_in_the_plan(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    short = _transcript(tmp_path / "t", "S-short", _STAMPS[0], n=2)
    assert cli.main(_argv([short])) == 0
    out = capsys.readouterr().out
    assert "skipped S-short: transcript too short (2 < 3 messages)" in out
    assert "nothing to import" in out
    assert chat.calls == []


def test_an_unreadable_transcript_is_a_named_skip(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    root = home / ".claude" / "projects" / store.project_slug(PROJECT)
    p = _transcript(root, "S-sealed", _STAMPS[0], cwd=None)
    p.chmod(0o000)
    try:
        assert cli.main(_argv([p])) == 0
        out = capsys.readouterr().out
        assert "skipped S-sealed: transcript unreadable" in out
        assert "nothing to import" in out
        assert chat.calls == []
    finally:
        p.chmod(0o600)


def test_a_compaction_note_is_shown_in_the_plan_before_spend(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    rows = [
        {
            "type": "user",
            "cwd": PROJECT,
            "isCompactSummary": True,
            "message": {"role": "user", "content": "summary of earlier work"},
            "timestamp": _STAMPS[0],
            "uuid": "u-0",
        }
    ] + _rows(_STAMPS[0], n=4)
    d = tmp_path / "t"
    d.mkdir()
    p = d / "S-compact.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (OLD, OLD))
    assert cli.main(_argv([p], "--plan")) == 0
    out = capsys.readouterr().out
    assert "S-compact  note: compact-summary-only-context" in out
    assert chat.calls == []


# ---- the live session is never imported ----------------------------------------


def test_the_live_session_is_excluded_by_id_and_by_freshness(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    named = _transcript(tmp_path / "t", "S-named", _STAMPS[0])
    fresh = _transcript(tmp_path / "t", "S-fresh", _STAMPS[1], mtime=None)
    old = _transcript(tmp_path / "t", "S-old", _STAMPS[2])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S-named")
    assert cli.main(_argv([named, fresh, old])) == 0
    out = capsys.readouterr().out
    assert "skipped S-named: the live session (CLAUDE_CODE_SESSION_ID)" in out
    assert "skipped S-fresh: modified in the last 10 minutes, may be live" in out
    assert "imported 1 of 1" in out
    assert len(chat.calls) == 1
    assert store.read_checkpoint("S-old") is not None


# ---- history only: behind the bucket's live latest ------------------------------


def test_an_import_behind_the_live_latest_is_named_history_before_spend(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """M1: a live capture newer than the transcript already holds `latest`.
    The plan says so, the run leaves `latest` where it is, and the summary
    counts it."""
    live = json.loads(_valid_json("live loop")) | {
        "session_id": "S-live",
        "created": "2026-10-20T10:00:00Z",
    }
    store.write_checkpoint("S-live", live, project_dir=PROJECT, writer=Writer.HUMAN)
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-older", _STAMPS[0])
    assert cli.main(_argv([p], "--plan")) == 0
    out = capsys.readouterr().out
    assert f"S-older  note: {importer.HISTORY_NOTE}" in out
    assert chat.calls == []
    assert cli.main(_argv([p])) == 0
    out = capsys.readouterr().out
    assert "[1/1] S-older: 0 tokens, history only" in out
    assert out.rstrip().endswith(", 1 history only")
    latest = store.read_latest_body(
        project_dir=PROJECT, route=store.Route.OWN, admit=store.Admit.ANY
    )
    assert latest["session_id"] == "S-live"
    written = store.read_checkpoint("S-older")
    assert written["created"] == _STAMPS[0]
    assert "live loop" not in json.dumps(written)


# ---- the per-session line and the summary -----------------------------------------


def test_the_summary_reports_tokens_unreported_sessions_and_downgrades(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    bad_quote = json.dumps(
        {
            "session_id": "ignored",
            "working_context": {
                "active_topic": {"text": "topic", "trust": "inferred"},
                "open_questions": [
                    {
                        "text": "q",
                        "trust": "verbatim",
                        "quote": "this quote is nowhere in the transcript",
                    }
                ],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )
    chat = _reporting(
        fake_chat_factory,
        [bad_quote, _valid_json()],
        [{"prompt_tokens": 41_000, "completion_tokens": 210}, None],
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)[:2]
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out.splitlines()
    assert "[1/2] S-0: 41,210 tokens, 1 quote downgraded" in out
    assert "[2/2] S-1: tokens unreported" in out
    assert out[-1] == (
        "imported 2 of 2, 2 calls (2 planned), 41,210 tokens reported, "
        "1 session unreported"
    )
    # L4: the count is the serializer's own return, read in process.
    assert serializer.quotes_downgraded() == 0  # the second session downgraded none


def test_the_summary_names_every_session_the_fallback_produced(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    inner = fake_chat_factory(_valid_json())

    def chat(messages, **kwargs):
        llm.note_fallback_used()  # the rescue fired inside this serialize
        return inner(messages, **kwargs)

    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-rescued", _STAMPS[0])
    assert cli.main(_argv([p])) == 0
    out = capsys.readouterr().out
    assert "[1/1] S-rescued: 0 tokens [fallback backend]" in out
    assert (
        "imported 1 of 1, 0 calls (1 planned), 0 tokens reported, "
        "1 via fallback backend (S-rescued)"
    ) in out
    assert _log_text(tmp_path).rstrip().endswith(" [fallback backend] [import]")


def test_cost_of_reads_only_stamped_integers():
    cost_of = cli.import_cmd._cost_of
    assert cost_of(None) == (None, None)
    assert cost_of({"capture_cost": "x"}) == (None, None)
    assert cost_of({"capture_cost": {"total_tokens": None}}) == (None, None)
    assert cost_of({"capture_cost": {"total_tokens": True, "calls": True}}) == (
        None,
        None,
    )
    assert cost_of({"capture_cost": {"total_tokens": 12, "calls": 3}}) == (12, 3)


# ---- the ledger ------------------------------------------------------------------------


def test_the_marker_precedes_each_serialize_and_every_result_is_an_import(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths)) == 0
    lines = _log_text(tmp_path).splitlines()
    for i in range(3):
        sid = f"S-{i}"
        marker = next(
            k
            for k, ln in enumerate(lines)
            if f"import: started serialize for {sid} (reason: import, "
            f"project: {PROJECT}) (transcript: {paths[i]})" in ln
        )
        result = next(
            k
            for k, ln in enumerate(lines)
            if ln.startswith("wrote checkpoint: ") and f"/{sid}.json" in ln
        )
        assert marker < result
        assert lines[result].endswith(" [import]")
    # Oldest first, so the markers appear in session-end order.
    order = [
        ln.split("started serialize for ")[1].split()[0]
        for ln in lines
        if "import: started serialize for" in ln
    ]
    assert order == ["S-0", "S-1", "S-2"]
    for i in range(3):
        assert store.read_checkpoint(f"S-{i}")["capture_door"] == "import"
