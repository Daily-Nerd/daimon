"""#1236 slice 2: `daimon import --transcript PATH ...`, the bounded first fill.

Every test runs with `DAIMON_CHECKPOINT_DIR`, `DAIMON_LOG_DIR` and `HOME`
under tmp (conftest's autouse isolation plus the `home` fixture here), with
`cli._chat` faked, so no model is called and no real store is touched. The
fake counts its calls: "zero spend" is asserted as zero calls, never
inferred from output.
"""

import json
import os

import pytest

from daimon_briefing import cli, importer, ledger, llm, serializer, store, transcript

PROJECT = "/p/import-cmd"
OLD = 1_782_000_000  # a fixed mtime well outside the live window

_STAMPS = ["2026-07-01T10:00:00Z", "2026-08-01T10:00:00Z", "2026-09-01T10:00:00Z"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """HOME under tmp beside the autouse DAIMON_* isolation, and the Claude
    Code projects root under it, so the no-cwd fallback rule has a root."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv(
        "DAIMON_CLAUDE_PROJECTS_DIR", str(tmp_path / "home" / ".claude" / "projects")
    )
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    return tmp_path / "home"


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


def _transcript(directory, sid, ended, n=4, cwd=PROJECT, mtime=OLD):
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / f"{sid}.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) for r in _rows(ended, n, cwd)), encoding="utf-8"
    )
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _three(tmp_path):
    return [_transcript(tmp_path / "t", f"S-{i}", _STAMPS[i]) for i in range(3)]


def _argv(paths, *extra):
    argv = ["import", "--project", PROJECT]
    for p in paths:
        argv += ["--transcript", str(p)]
    return argv + list(extra)


def _log_text(tmp_path):
    p = tmp_path / ".daimon" / "logs" / "serialize.log"
    return p.read_text(encoding="utf-8") if p.exists() else ""


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


def test_the_call_estimate_is_the_pipelines_own_count(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """A chunked transcript costs chunks plus merges; the plan says so before
    the run, and the run then makes exactly that many calls."""
    monkeypatch.setenv("DAIMON_CHUNK_LINES", "3")
    monkeypatch.setenv("DAIMON_CHUNK_OVERLAP", "0")
    monkeypatch.setenv("DAIMON_CHUNK_CONCURRENCY", "1")
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-long", _STAMPS[0], n=6)
    messages = transcript.from_file(p)
    planned = serializer.planned_calls(messages)
    assert planned > 1
    assert cli.main(_argv([p], "--plan")) == 0
    assert f"1 session, about {planned} model calls" in capsys.readouterr().out
    assert chat.calls == []
    assert cli.main(_argv([p])) == 0
    assert len(chat.calls) == planned
    assert f"imported 1 of 1, {planned} calls" in capsys.readouterr().out


def test_merge_calls_counts_every_non_singleton_group():
    assert serializer.merge_calls(1, 3) == 0
    assert serializer.merge_calls(2, 3) == 1
    assert serializer.merge_calls(3, 3) == 1
    assert serializer.merge_calls(4, 3) == 2  # (3)(1) -> 2 partials -> 1
    assert serializer.merge_calls(7, 3) == 3  # (3)(3)(1) -> 3 -> 1
    assert serializer.merge_calls(9, 3) == 4  # (3)(3)(3) -> 3 -> 1


# ---- the bound ----------------------------------------------------------------


def test_a_bounded_run_stops_before_the_session_that_would_pass_max_calls(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths, "--max-calls", "2")) == 0
    out = capsys.readouterr().out
    assert len(chat.calls) == 2
    assert store.read_checkpoint("S-0") is not None
    assert store.read_checkpoint("S-1") is not None
    assert store.read_checkpoint("S-2") is None
    assert "stopped before [3/3] S-2: its 1 call would pass --max-calls 2" in out
    assert "2 of 3 imported, 1 left." in out
    assert cli.import_cmd.RESUME_HINT in out
    assert "imported 2 of 3" not in out  # a bounded stop prints no summary line


def test_max_calls_rejects_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["import", "--project", PROJECT, "--transcript", "x", "--max-calls", "0"]
        )
    assert exc.value.code == 2
    assert "--max-calls needs a count of 1 or more" in capsys.readouterr().err


# ---- the first failure stops the run ------------------------------------------


def test_a_partial_run_stops_at_the_first_failure_with_the_hint(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(
        [_valid_json(), llm.ChatError("LLM HTTP 429"), _valid_json()]
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)
    assert cli.main(_argv(paths)) == 1
    captured = capsys.readouterr()
    assert len(chat.calls) == 2  # the third session was never started
    assert store.read_checkpoint("S-0") is not None
    assert store.read_checkpoint("S-1") is None
    assert "[2/3] error after" in captured.out
    assert (
        "stopped after the first failed session. 1 of 3 imported, 2 left."
        in captured.out
    )
    assert cli.import_cmd.RESUME_HINT in captured.out
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
    chat = fake_chat_factory(
        [_valid_json(), llm.ChatError("quota"), _valid_json(), _valid_json()]
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
    assert "imported 2 of 2, 2 calls" in out
    capsys.readouterr()
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out
    assert "0 sessions, about 0 model calls" in out
    assert "nothing to import" in out
    assert len(chat.calls) == 4


def test_a_pruned_checkpoint_is_still_done_by_its_import_line(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """The flat store prunes old checkpoints first; the ` [import]` success
    line in serialize.log is what keeps a resume from paying again."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-gone", _STAMPS[0])
    assert cli.main(_argv([p])) == 0
    assert len(chat.calls) == 1
    (tmp_path / ".daimon" / "checkpoints" / "S-gone.json").unlink()
    assert store.read_checkpoint("S-gone") is None
    capsys.readouterr()
    assert cli.main(_argv([p])) == 0
    assert "skipped S-gone: already imported (serialize.log)" in capsys.readouterr().out
    assert len(chat.calls) == 1


def test_imported_sessions_reads_only_the_import_marker():
    text = "\n".join(
        [
            "wrote checkpoint: /x/S-a.json (took 3s) [import]",
            "wrote checkpoint: /x/S-b.json (took 3s) [fallback backend] [import]",
            "wrote checkpoint: /x/S-live.json (took 3s)",
            "wrote checkpoint: /x/S-fb.json (took 3s) [fallback backend]",
            "wrote checkpoint: /x/rollout-2026-10-01T10-00-00-abc.json (took 1s) [import]",
            "2026-10-26T14:02:11Z import: started serialize for S-hung (reason: import, project: /p) (transcript: /t/S-hung.jsonl)",
        ]
    )
    assert ledger.imported_sessions(text) == frozenset({"S-a", "S-b", "abc"})


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


def test_a_transcript_with_no_recorded_cwd_needs_the_projects_host_root(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    loose = _transcript(tmp_path / "t", "S-loose", _STAMPS[0], cwd=None)
    assert cli.main(_argv([loose])) == 2
    err = capsys.readouterr().err
    assert "records no project directory" in err
    assert chat.calls == []
    root = home / ".claude" / "projects" / store.project_slug(PROJECT)
    housed = _transcript(root, "S-housed", _STAMPS[0], cwd=None)
    assert cli.main(_argv([housed], "--plan")) == 0
    assert "1 session, about 1 model call" in capsys.readouterr().out


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


# ---- the per-session line and the summary -----------------------------------------


def _chat_reporting(fake_chat_factory, responses, usage_per_call):
    """A fake backend that records usage the way a real one does: a dict
    reports, None stays silent (#1235)."""
    inner = fake_chat_factory(responses)
    calls = iter(usage_per_call)

    def chat(messages, **kwargs):
        out = inner(messages, **kwargs)
        llm._note_call_usage("fake", "m", llm._normalize_usage(next(calls)), None)
        return out

    chat.inner = inner  # type: ignore[attr-defined]
    return chat


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
    chat = _chat_reporting(
        fake_chat_factory,
        [bad_quote, _valid_json()],
        [{"prompt_tokens": 41_000, "completion_tokens": 210}, None],
    )
    monkeypatch.setattr(cli, "_chat", chat)
    paths = _three(tmp_path)[:2]
    assert cli.main(_argv(paths)) == 0
    out = capsys.readouterr().out.splitlines()
    first = next(ln for ln in out if ln.startswith("[1/2]"))
    second = next(ln for ln in out if ln.startswith("[2/2]"))
    assert first.startswith("[1/2] wrote checkpoint (took ")
    assert first.endswith("s) 41,210 tokens, 1 quote downgraded")
    assert second.endswith("s) tokens unreported")
    assert (
        out[-1]
        == "imported 2 of 2, 2 calls, 41,210 tokens reported, 1 session unreported"
    )


def test_the_summary_names_every_session_the_fallback_produced(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    inner = fake_chat_factory(_valid_json())

    def chat(messages, **kwargs):
        llm._fallback_used = True  # the rescue fired inside this serialize
        return inner(messages, **kwargs)

    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-rescued", _STAMPS[0])
    assert cli.main(_argv([p])) == 0
    out = capsys.readouterr().out
    assert "[1/1] wrote checkpoint (took " in out
    assert "tokens [fallback backend]" in out
    assert (
        "imported 1 of 1, 1 call, 0 tokens reported, 1 via fallback backend (S-rescued)"
        in out
    )
    assert _log_text(tmp_path).rstrip().endswith(" [fallback backend] [import]")


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


def test_import_done_agrees_with_the_heal_classifier_on_a_checkpoint(
    tmp_path, home, monkeypatch
):
    monkeypatch.setattr(ledger, "has_checkpoint", lambda sid: sid == "S-yes")
    assert importer.import_done("S-yes", frozenset()) == "already has a checkpoint"
    assert (
        importer.import_done("S-no", frozenset({"S-no"}))
        == "already imported (serialize.log)"
    )
    assert importer.import_done("S-no", frozenset()) is None


# ---- the edges of the plan and the run --------------------------------------


def test_a_path_given_twice_is_planned_once(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _transcript(tmp_path / "t", "S-twice", _STAMPS[0])
    assert cli.main(_argv([p, p], "--plan")) == 0
    out = capsys.readouterr().out
    assert "1 session, about 1 model call" in out
    assert out.count("S-twice") == 1


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


def test_max_calls_rejects_a_word(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["import", "--project", PROJECT, "--transcript", "x", "--max-calls", "two"]
        )
    assert exc.value.code == 2
    assert "not a whole number: 'two'" in capsys.readouterr().err


def test_tokens_of_reads_only_a_stamped_integer():
    tokens_of = cli.import_cmd._tokens_of
    assert tokens_of(None) is None
    assert tokens_of({"capture_cost": "x"}) is None
    assert tokens_of({"capture_cost": {"total_tokens": None}}) is None
    assert tokens_of({"capture_cost": {"total_tokens": True}}) is None
    assert tokens_of({"capture_cost": {"total_tokens": 12}}) == 12


def test_the_log_readers_answer_zero_without_a_log(tmp_path, monkeypatch):
    monkeypatch.setenv("DAIMON_LOG_DIR", str(tmp_path / "no-such-dir"))
    assert cli.import_cmd._log_size() == 0
    assert cli.import_cmd._downgrades_since(0) == 0
