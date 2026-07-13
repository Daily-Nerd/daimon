import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daimon_briefing import cli
from tests.conftest import FIXTURES


def _valid_json(session_id="sample_transcript"):
    return json.dumps(
        {
            "session_id": session_id,
            "working_context": {
                "active_topic": {"text": "t", "trust": "inferred"},
                "open_questions": [
                    {"text": "PR #6 state", "trust": "verbatim",
                     "quote": "merge it myself", "external_state": True}
                ],
                "recent_decisions": [{"text": "adopt D-007", "trust": "inferred"}],
            },
            "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
        }
    )


def test_cli_version_flag(capsys):
    from daimon_briefing import __version__
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"daimon {__version__}"


def test_cli_serialize_writes_checkpoint(tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch):
    from daimon_briefing import store

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")  # fixture has 5 turns

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    # checkpoint file exists, keyed by the transcript stem
    ckpt = store.read_checkpoint("sample_transcript")
    assert ckpt is not None
    assert store.read_latest()["session_id"] == "sample_transcript"


def test_cli_brief_prints_briefing(tmp_checkpoint_dir, sample_checkpoint, capsys,
                                   monkeypatch):
    from daimon_briefing import store

    # Route to the checkpoint's own project — a slugless global-only write
    # would hit the #96 header-only fallback instead of rendering.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/p/A")
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #6" in out
    assert "verify before trusting" in out.lower()


def test_cli_brief_no_checkpoint(tmp_checkpoint_dir, capsys):
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no checkpoint" in out.lower() or "nothing" in out.lower()


def test_cli_serialize_too_short(tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch, tmp_path):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    short = tmp_path / "short.md"
    short.write_text("**user**: hi\n\n**assistant**: hello")
    rc = cli.main(["serialize", str(short)])
    # too short -> benign skip (rc 0), no checkpoint, and the skip SAYS too short
    from daimon_briefing import store

    assert rc == 0
    assert store.read_checkpoint("short") is None
    out = capsys.readouterr().out
    assert "too short" in out.lower()


def test_cli_serialize_names_parse_failure(
    tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch
):
    # Slice 2: the residual error is gone — every failure path names its cause.
    chat = fake_chat_factory("prose, definitely not JSON")
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc != 0
    err = capsys.readouterr().err.lower()
    assert "unparseable" in err or "not a json object" in err


def test_cli_serialize_no_api_key_names_the_cause(
    tmp_checkpoint_dir, capsys, monkeypatch
):
    # The live failure mode: hook fired but no LLM credentials configured.
    # Must fail fast BEFORE the LLM call, naming the missing key — not the
    # conflated "too short, LLM error, or invalid output".
    # Backend pinned litellm: since #52 pre-flight only requires a key on
    # litellm-bound transports (a dev machine's `claude` on PATH would
    # otherwise legitimately pass).
    for var in ("DAIMON_LLM_API_KEY", "LITELLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "api key" in err.lower()
    assert "too short" not in err.lower()


# ---- duration logging: serialize.log must show elapsed time per checkpoint ----


def test_cli_serialize_success_reports_duration(
    tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch
):
    # Checkpoint generation runs 4-25 min in production; the "wrote checkpoint"
    # line that lands in serialize.log must carry elapsed seconds.
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    out = capsys.readouterr().out
    assert re.search(r"wrote checkpoint: .+ \(took \d+s\)", out)


def test_cli_serialize_failure_reports_duration(
    tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch
):
    # Named failures must also carry elapsed time — and keep the named cause.
    chat = fake_chat_factory("prose, definitely not JSON")
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc != 0
    err = capsys.readouterr().err
    assert "unparseable" in err.lower() or "not a json object" in err.lower()
    assert re.search(r"after \d+s", err)


# ---- FR #27: first-class result logging — manual serializes reach serialize.log ----


def test_cli_serialize_success_appends_result_to_log(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # A MANUAL serialize must write its result line to serialize.log so `status`
    # reports it — not only hook-spawned runs (the bug behind a stale "last
    # serialize result" after a manual recovery).
    from daimon_briefing import config

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    log = (config.log_dir() / "serialize.log").read_text()
    # byte-identical to the printed line, so _RESULT_OK_RE still matches it
    assert re.search(r"^wrote checkpoint: .+ \(took \d+s\)$", log.strip(), re.M)

    capsys.readouterr()  # drop the serialize stdout
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "last serialize result: success" in out


def test_cli_serialize_failure_appends_error_to_log(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # A manual serialize FAILURE must also reach serialize.log so `status` shows
    # the real last outcome, not a stale one.
    from daimon_briefing import config

    chat = fake_chat_factory("prose, definitely not JSON")
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc != 0
    log = (config.log_dir() / "serialize.log").read_text()
    assert re.search(r"^error: .* after \d+s$", log.strip(), re.M)


# ---- per-project routing: serialize/brief thread DAIMON_PROJECT_DIR through ----


def test_cli_serialize_routes_to_project_latest(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/Users/x/projA")

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    assert (tmp_checkpoint_dir / "-Users-x-projA" / "latest.json").exists()
    assert (tmp_checkpoint_dir / "latest.json").exists()  # global kept for other consumers


def test_cli_serialize_defaults_to_cwd_when_no_env(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    # FR #34: the recovery-path bug. A manual re-run with no DAIMON_PROJECT_DIR
    # must still write the PROJECT pointer (cwd), not global-only — otherwise the
    # project checkpoint stays stale exactly when re-serializing a failed run.
    from daimon_briefing import store

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    monkeypatch.chdir(proj)

    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    assert store.project_latest_path(str(proj)).exists()


def test_cli_serialize_project_flag_overrides_env(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch
):
    # Explicit --project beats DAIMON_PROJECT_DIR, mirroring `status --project`.
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/env")

    rc = cli.main(
        ["serialize", "--project", "/p/flag", str(FIXTURES / "sample_transcript.md")]
    )
    assert rc == 0
    assert (tmp_checkpoint_dir / "-p-flag" / "latest.json").exists()
    assert not (tmp_checkpoint_dir / "-p-env" / "latest.json").exists()


def test_cli_serialize_project_dot_resolves_to_cwd(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    # "." must resolve to the absolute cwd BEFORE slugging, so the written slug
    # matches what status/brief later compute for the same dir.
    from daimon_briefing import store

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    monkeypatch.chdir(proj)

    rc = cli.main(
        ["serialize", "--project", ".", str(FIXTURES / "sample_transcript.md")]
    )
    assert rc == 0
    assert store.project_latest_path(str(proj)).exists()


def test_resolve_project_routes_through_resolve_project_root(monkeypatch):
    # #74: _resolve_project must normalize the dir via config.resolve_project_root
    # so a subdir session maps to the git-toplevel bucket. Assert the resolver is
    # called and its return value flows out unchanged.
    from daimon_briefing import config

    seen = {}

    def _sentinel(raw):
        seen["raw"] = raw
        return "/git/toplevel"

    monkeypatch.setattr(config, "resolve_project_root", _sentinel)
    out = cli._resolve_project("/some/repo/subdir")
    assert out == "/git/toplevel"
    # the resolver received the absolute-normalized dir (not stripped of routing)
    assert seen["raw"] == str(Path("/some/repo/subdir").expanduser().resolve())


def test_cli_brief_prefers_project_latest(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store

    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-mine"
    mine["working_context"]["open_questions"][0]["text"] = "PR #42 state — project A loop"
    store.write_checkpoint("S-mine", mine, project_dir="/p/A")
    other = {**sample_checkpoint, "session_id": "S-other"}
    store.write_checkpoint("S-other", other)  # global latest now belongs to another project

    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #42" in out


def test_cli_brief_falls_back_to_global(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #96: the global fallback is header-only by default — the foreign body
    # renders only on explicit opt-in (covered by test_brief_fallback_full_*).
    from daimon_briefing import store

    store.write_checkpoint("S-global", sample_checkpoint)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/never-seen")
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no briefing for this project yet" in out.lower()
    assert "PR #6" not in out


def test_cli_brief_routes_to_cwd_when_no_env(
    tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path
):
    # No DAIMON_PROJECT_DIR (manual shell): brief must route by cwd like status,
    # not silently fall through to the global pointer of another project.
    from daimon_briefing import store

    proj = (tmp_path / "myproj").resolve()
    proj.mkdir()
    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-cwd"
    mine["working_context"]["open_questions"][0]["text"] = "PR #99 state — cwd loop"
    store.write_checkpoint("S-cwd", mine, project_dir=str(proj))
    # another project owns the most recent GLOBAL checkpoint
    store.write_checkpoint("S-other", {**sample_checkpoint, "session_id": "S-other"})

    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    monkeypatch.chdir(proj)
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #99" in out


def test_cli_brief_project_flag_overrides(
    tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    mine = json.loads(json.dumps(sample_checkpoint))
    mine["session_id"] = "S-flag"
    mine["working_context"]["open_questions"][0]["text"] = "PR #77 state — flag loop"
    store.write_checkpoint("S-flag", mine, project_dir="/p/flag")
    store.write_checkpoint("S-other", {**sample_checkpoint, "session_id": "S-other"})

    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    rc = cli.main(["brief", "--project", "/p/flag"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #77" in out


# ---- status: checkpoint presence, age, and last serialize outcome ----


def _age_file(path, seconds):
    """Backdate a file's mtime so status reports a known age."""
    past = time.time() - seconds
    os.utime(path, (past, past))


@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here; expose the path.
    return tmp_path / ".daimon" / "logs"


def _write_log(log_dir, lines):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _with_session(checkpoint, session_id):
    """Copy of a checkpoint with its embedded session_id replaced (status reads
    the blob's session_id, not the filename)."""
    out = json.loads(json.dumps(checkpoint))
    out["session_id"] = session_id
    return out


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (59, "59s"),
        (60, "1m"),
        (61, "1m"),
        (3599, "59m"),
        (3600, "1h"),
        (86399, "23h"),
        (86400, "1d"),
        (5 * 86400, "5d"),
    ],
)
def test_status_format_age_boundaries(seconds, expected):
    assert cli._format_age(seconds) == expected


# ---- age from `created` stamp, mtime fallback for legacy checkpoints (#93) ----


def test_checkpoint_info_prefers_created_over_mtime(tmp_path):
    from datetime import datetime, timezone

    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"session_id": "S", "created": "2026-06-01T00:00:00Z"}))
    _age_file(p, 10)  # mtime ~10s ago — must be ignored in favor of `created`
    now = datetime(2026, 6, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()  # 1h after created
    info = cli._checkpoint_info(p, now)
    assert info["age_seconds"] == 3600


def test_checkpoint_info_falls_back_to_mtime_for_legacy(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"session_id": "S"}))  # legacy: no created
    _age_file(p, 120)
    info = cli._checkpoint_info(p, time.time())
    assert 110 <= info["age_seconds"] <= 130


def test_checkpoint_info_bad_created_falls_back_to_mtime(tmp_path):
    p = tmp_path / "latest.json"
    p.write_text(json.dumps({"session_id": "S", "created": "not-a-timestamp"}))
    _age_file(p, 120)
    info = cli._checkpoint_info(p, time.time())
    assert 110 <= info["age_seconds"] <= 130


def test_cli_status_project_checkpoint_present(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    # Age now derives from the written `created` stamp, not file mtime (#93), so
    # drive the 3m age through `created` rather than backdating the pointer's mtime.
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - (3 * 60 + 5)))
    store.write_checkpoint("S-prev", {**sample_checkpoint, "created": old}, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")

    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "S-prev" in out
    assert "3m" in out
    assert str(tmp_checkpoint_dir / "-p-A" / "latest.json") in out


def test_cli_status_nothing_exists_exits_1(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/empty")
    rc = cli.main(["status"])
    assert rc == 1
    out = capsys.readouterr().out.lower()
    assert "none" in out or "no checkpoint" in out


def test_cli_status_global_fallback_labeled(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    store.write_checkpoint("S-global", _with_session(sample_checkpoint, "S-global"))  # global only
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/never-seen")
    rc = cli.main(["status"])
    assert rc == 0  # global exists -> 0
    out = capsys.readouterr().out
    assert "fallback" in out.lower()
    assert "S-global" in out


def test_cli_status_same_session_dedup_noted(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    # One write updates BOTH pointers with the same session.
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    # The project produced the most recent checkpoint anywhere; global merely
    # coincides — must NOT read as "project fell back to global".
    assert "same as project" in out
    assert "most recent checkpoint anywhere" in out


def test_cli_status_lists_buried_failure(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    transcript_a = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for sample_transcript "
            "(reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript_a}) after 1s",
            "2026-06-10T12:10:00Z session-end: spawned serialize for other_sess "
            "(reason: exit, project: /p/B)",
            "wrote checkpoint: /c/other_sess.json (took 5s)",
        ],
    )
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc in (0, 1)
    assert "failed to serialize" in out
    assert "sample_transcript" in out
    assert "run `daimon heal`" in out


def test_cli_status_log_success_line(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-123 (reason: exit, project: /p/A)",
            "wrote checkpoint: /tmp/ck/S-123.json (took 78s)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "78s" in out
    assert "wrote checkpoint" in out or "success" in out
    assert "S-123" in out  # last spawn reported


def test_cli_status_log_error_line(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-9 (reason: exit, project: /p/A)",
            "error: LLM call failed: timeout (transcript: /t/S-9.md) after 120s",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "error" in out.lower()
    assert "120s" in out


def test_cli_status_log_missing(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cli.main(["status"])  # tmp_log_dir has no serialize.log
    out = capsys.readouterr().out.lower()
    assert "no serialize history" in out


def test_cli_status_log_interleaved_last_result_wins(
    tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch
):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    # Two overlapping sessions: spawn A, spawn B, A's error, B's success.
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-A (reason: exit, project: /p/A)",
            "2026-06-10T12:00:05Z session-end: spawned serialize for S-B (reason: exit, project: /p/B)",
            "error: too short (transcript: /t/S-A.md) after 1s",
            "wrote checkpoint: /tmp/ck/S-B.json (took 42s)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    # last result line wins (S-B's success), last spawn line wins (S-B).
    assert "42s" in out
    assert "S-B" in out
    # The top "last serialize" summary (last-of-kind, no pairing) still reports
    # only B; the outstanding block below it is what now surfaces A's buried
    # failure (that's the point of this feature — see test_cli_status_lists_buried_failure).
    top_summary, _, outstanding_block = out.partition("failed to serialize")
    assert "S-A" not in top_summary
    assert "S-A" in outstanding_block


def test_cli_status_codex_stop_spawn_recognized(
    tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch
):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    # A Codex Stop hook spawn AFTER an older Claude session-end spawn: the
    # Codex one is the last spawn and must win, not the stale Claude one.
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-old (reason: exit, project: /p/A)",
            "2026-06-10T13:00:00Z codex-stop: spawned serialize for 019eb-rollout (project: /p/A)",
            "wrote checkpoint: /tmp/ck/019eb-rollout.json (took 237s)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "019eb-rollout" in out
    # The top "last serialize" summary still reports only the latest spawn
    # (019eb-rollout); S-old is a spawn that never got a result, so it now
    # surfaces separately in the outstanding block as hung (20d >> the 30m ceiling).
    top_summary, _, outstanding_block = out.partition("failed to serialize")
    assert "S-old" not in top_summary
    assert "S-old" in outstanding_block


def test_cli_status_gemini_session_end_spawn_recognized(
    tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch
):
    # The Gemini hook logs `gemini-session-end: spawned serialize for <sid>` —
    # _SPAWN_RE must recognize it or Gemini serializes are invisible to status,
    # hung detection, and heal.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    # Spawn with NO result line, long past the hung ceiling: only the spawn
    # regex can surface it — if the prefix isn't recognized the session is
    # simply absent from the output.
    _write_log(
        tmp_log_dir,
        [
            "2026-07-01T12:00:00Z gemini-session-end: spawned serialize for G-sess "
            "(reason: exit, project: /p/A)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "G-sess" in out


def test_cli_status_result_duration_not_duplicated(
    tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch
):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-123 (reason: exit, project: /p/A)",
            "wrote checkpoint: /tmp/ck/S-123.json (took 78s)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    # The raw log line already carries "(took 78s)" — status must not append
    # a second copy of the same duration.
    assert out.count("took 78s") == 1


def test_cli_status_json_shape(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-prev (reason: exit, project: /p/A)",
            "wrote checkpoint: /tmp/ck/S-prev.json (took 7s)",
        ],
    )
    rc = cli.main(["status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"project", "global", "last_serialize", "outstanding",
                         "siblings", "health", "team", "crash", "disabled",
                         "skipped_recent", "recall_error", "recall_index",
                         "receipts", "capture_alarm", "hook_drift"}
    assert data["capture_alarm"] is None  # #265 FAIL-only probe silent by default
    assert data["team"] is None  # no team remote configured -> explicit null (#113)
    assert data["receipts"] is None  # #204 feature off -> explicit null
    assert data["project"]["exists"] is True
    assert data["project"]["session_id"] == "S-prev"
    assert data["project"]["dir"] == "/p/A"
    assert isinstance(data["project"]["age_seconds"], int)
    assert data["global"]["exists"] is True
    assert data["global"]["same_session_as_project"] is True
    assert data["last_serialize"]["result"]["outcome"] == "success"
    assert data["last_serialize"]["result"]["duration_seconds"] == 7
    assert data["last_serialize"]["spawn"]["session_id"] == "S-prev"


def test_cli_status_json_exit_1_when_nothing(tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/empty")
    rc = cli.main(["status", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["project"]["exists"] is False
    assert data["global"]["exists"] is False


def test_cli_status_project_flag_overrides_env(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    store.write_checkpoint("S-flag", _with_session(sample_checkpoint, "S-flag"), project_dir="/p/flag")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/env")
    rc = cli.main(["status", "--project", "/p/flag"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "/p/flag" in out
    assert "S-flag" in out


def test_cli_status_env_overrides_cwd(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch, tmp_path
):
    from daimon_briefing import store

    store.write_checkpoint("S-env", _with_session(sample_checkpoint, "S-env"), project_dir="/p/env")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/env")
    monkeypatch.chdir(tmp_path)  # cwd has no checkpoint; env must win
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "/p/env" in out
    assert "S-env" in out


def test_cli_status_project_dot_resolves_to_cwd_checkpoint(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch, tmp_path
):
    from daimon_briefing import store

    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    store.write_checkpoint("S-dot", _with_session(sample_checkpoint, "S-dot"), project_dir=str(proj))
    monkeypatch.chdir(proj)
    rc = cli.main(["status", "--project", "."])
    assert rc == 0
    out = capsys.readouterr().out
    assert "S-dot" in out  # "." resolved to the absolute dir the store slugged
    assert f"project: {proj}" in out  # header shows the resolved path, not "."


def test_cli_status_cwd_is_last_resort(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch, tmp_path
):
    from daimon_briefing import store

    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    store.write_checkpoint("S-cwd", _with_session(sample_checkpoint, "S-cwd"), project_dir=str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "S-cwd" in out


def test_cli_status_recognizes_retry_spawn(
    tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch
):
    # #26 part C: the heal retry marker is a spawn-style line; status must parse
    # it as the last serialize spawn, not ignore it as noise.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for S-OLD (reason: exit, project: /p/A)",
            "error: boom (transcript: /t/S-OLD.md) after 1s",
            "2026-06-10T12:05:00Z session-start: retry serialize for S-NEW (prior: error: boom)",
        ],
    )
    cli.main(["status"])
    out = capsys.readouterr().out
    # The retry line is the last spawn -> S-NEW wins, proving it was parsed.
    assert "last serialize spawn: session S-NEW" in out


# ---- #26: self-healing serialize retry at SessionStart (`heal` subcommand) ----


def test_cli_heal_reserializes_failed_session(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # A failed serialize left no checkpoint though the transcript persists.
    # `heal` re-serializes it, routing to the FAILED session's project (/p/A),
    # and leaves a retry marker in the log.
    from daimon_briefing import config, store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: LLM call failed: bad temperature (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert store.read_checkpoint(stem) is not None
    # routed to the recovered project, NOT the heal-time cwd
    assert (tmp_checkpoint_dir / "-p-A" / "latest.json").exists()
    log = (config.log_dir() / "serialize.log").read_text()
    assert f"session-start: retry serialize for {stem}" in log


def test_cli_heal_noop_when_checkpoint_exists(
    tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, fake_chat_factory, monkeypatch
):
    # Nothing was lost: a checkpoint already exists for the failed stem -> no-op.
    from daimon_briefing import config, store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    store.write_checkpoint(stem, _with_session(sample_checkpoint, stem), project_dir="/p/A")
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert chat.calls == []  # no serialize attempted
    log = (config.log_dir() / "serialize.log").read_text()
    assert "retry serialize" not in log


def test_cli_heal_dedup_one_retry_per_session(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # A retry marker already exists for the stem -> never retry twice (loop guard).
    from daimon_briefing import store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
            f"2026-06-10T12:05:00Z session-start: retry serialize for {stem} (prior: error: boom)",
            f"error: boom again (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert chat.calls == []
    assert store.read_checkpoint(stem) is None


def test_cli_heal_skips_when_project_unrecoverable(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # No spawn line matches the failed stem -> we will NOT guess the project;
    # mis-routing a recovered checkpoint is worse than not healing.
    from daimon_briefing import config, store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            "2026-06-10T12:00:00Z session-end: spawned serialize for SOME-OTHER (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert store.read_checkpoint(stem) is None
    assert chat.calls == []
    log = (config.log_dir() / "serialize.log").read_text()
    assert "retry serialize" not in log


def test_cli_heal_noop_when_last_result_success(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # The most recent serialize succeeded -> nothing lost -> no-op.
    from daimon_briefing import config

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
            f"wrote checkpoint: /tmp/ck/{stem}.json (took 5s)",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)

    rc = cli.main(["heal"])
    assert rc == 0
    assert chat.calls == []
    log = (config.log_dir() / "serialize.log").read_text()
    assert "retry serialize" not in log


def test_cli_heal_routes_to_global_when_project_unknown(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # The matching spawn line had no project (`?`) -> global-only retry, no slug
    # dir; we never invent a project for a no-project session.
    from daimon_briefing import store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: ?)",
            f"error: boom (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert store.read_checkpoint(stem) is not None
    assert (tmp_checkpoint_dir / "latest.json").exists()  # global pointer written
    slug_dirs = [p for p in tmp_checkpoint_dir.iterdir() if p.is_dir()]
    assert slug_dirs == []  # no per-project pointer routed


def test_cli_heal_repairs_failure_buried_by_later_other_session(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # A's serialize failed, but B's LATER, unrelated serialize succeeded after
    # it. `heal` must attribute state PER SESSION and still repair A -- the
    # global "last result" is B's success, but A was never recovered.
    from daimon_briefing import config, store

    stem_a = "sample_transcript"          # the failed one (has a real fixture transcript)
    transcript_a = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem_a} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript_a}) after 1s",
            "2026-06-10T12:10:00Z session-end: spawned serialize for other_sess (reason: exit, project: /p/B)",
            "wrote checkpoint: /c/other_sess.json (took 5s)",   # B success — must NOT hide A
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert store.read_checkpoint(stem_a) is not None            # A was healed
    log = (config.log_dir() / "serialize.log").read_text()
    assert f"session-start: retry serialize for {stem_a}" in log


def test_cli_heal_noop_when_no_log(tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch):
    # No serialize.log at all -> nothing to heal.
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    rc = cli.main(["heal"])
    assert rc == 0
    assert chat.calls == []


# ---- #219: live progress indicator (render.working) around heal's re-serialize ----


def test_cli_heal_shows_working_indicator_before_result(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # A real target: the plain (non-TTY, as in tests) working() line must
    # print exactly once, BEFORE _run_serialize's own result line — a house
    # spinner that fires after the result would be silently useless.
    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: LLM call failed: bad temperature (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    out = capsys.readouterr().out
    working_line = f"healing {stem} — re-serializing transcript...\n"
    assert out.count(working_line) == 1
    working_at = out.index(working_line)
    result_at = out.index("wrote checkpoint:")
    assert working_at < result_at  # spinner line precedes the result line


def test_cli_heal_dry_run_has_no_working_indicator(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # --dry-run never touches _run_serialize — nothing slow happens, so no
    # spinner/line should appear alongside the "would heal" explanation.
    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: LLM call failed: bad temperature (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"would heal {stem}" in out
    assert "re-serializing transcript" not in out
    assert chat.calls == []


def test_cli_heal_no_target_has_no_working_indicator(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch
):
    # No serialize.log -> no healable target -> nothing slow happens -> no
    # working() line, only the plan's "nothing to heal" note.
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)

    rc = cli.main(["heal"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "re-serializing transcript" not in out
    assert chat.calls == []


# ---- #15: `heal --force` — explicit escape hatch past the one-retry-ever cap ----


def test_cli_heal_force_reheals_retry_exhausted_session(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # A retry marker already exists for the stem (default heal refuses, see
    # test_cli_heal_dedup_one_retry_per_session) -> --force ignores the marker
    # and repairs it anyway, appending a SECOND retry marker in the same
    # parseable format (so the session re-classifies as retry-exhausted again
    # until the next --force).
    from daimon_briefing import config, store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
            f"2026-06-10T12:05:00Z session-start: retry serialize for {stem} (prior: error: boom)",
            f"error: boom again (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal", "--force"])
    assert rc == 0
    assert store.read_checkpoint(stem) is not None
    log = (config.log_dir() / "serialize.log").read_text()
    assert log.count(f"session-start: retry serialize for {stem}") == 2
    # the new marker is still parseable by the ledger's spawn regex
    outstanding = cli._compute_outstanding(log, time.time())
    assert outstanding == []  # healed -> checkpoint now exists, nothing outstanding


def test_cli_heal_default_still_refuses_after_force(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # Default policy is unchanged: a session already retried (even via
    # --force) is retry-exhausted again for a plain `daimon heal`.
    from daimon_briefing import store

    stem = "sample_transcript"
    transcript = FIXTURES / "sample_transcript.md"
    _write_log(
        tmp_log_dir,
        [
            f"2026-06-10T12:00:00Z session-end: spawned serialize for {stem} (reason: exit, project: /p/A)",
            f"error: boom (transcript: {transcript}) after 1s",
            f"2026-06-10T12:05:00Z session-start: retry serialize for {stem} (prior: error: boom)",
            f"error: boom again (transcript: {transcript}) after 1s",
        ],
    )
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    rc = cli.main(["heal"])
    assert rc == 0
    assert chat.calls == []
    assert store.read_checkpoint(stem) is None


def test_heal_argparser_has_force(monkeypatch):
    # Mirrors test_heal_argparser_has_dry_run: stub _cmd_heal to capture the
    # parsed namespace without running a real heal.
    from daimon_briefing import cli
    seen = {}
    monkeypatch.setattr(cli, "_cmd_heal", lambda args: seen.setdefault("force", args.force) or 0)

    cli.main(["heal", "--force"])
    assert seen["force"] is True

    seen.clear()
    cli.main(["heal"])
    assert seen["force"] is False


# ---- #48: `configure` — detect/report backend + fill gaps in ~/.daimon/env ----


_CFG_LLM_VARS = (
    "DAIMON_LLM_BACKEND",
    "DAIMON_LLM_API_KEY", "LITELLM_API_KEY",
    "DAIMON_LLM_MODEL", "LITELLM_MODEL",
    "DAIMON_LLM_BASE_URL", "LITELLM_BASE_URL",
    "DAIMON_LLM_COMMAND", "DAIMON_LLM_COMMAND_OUTPUT", "DAIMON_LLM_COMMAND_INPUT",
)


def _clear_llm_env(monkeypatch):
    for var in _CFG_LLM_VARS:
        monkeypatch.delenv(var, raising=False)


def _set_claude(monkeypatch, present):
    from daimon_briefing import llm

    monkeypatch.setattr(
        llm.shutil, "which",
        lambda name: "/usr/bin/claude" if (present and name == "claude") else None,
    )


def test_cli_configure_claude_zero_config_writes_nothing(
    capsys, monkeypatch, tmp_path
):
    # claude on PATH, no API key -> zero-config ready; nothing must be written.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, True)

    rc = cli.main(["configure"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "ready" in out
    assert "command" in out  # the resolved backend is named
    assert not env_file.exists()  # zero-config writes nothing


def test_cli_configure_litellm_flags_write_env(capsys, monkeypatch, tmp_path):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)

    rc = cli.main([
        "configure", "--backend", "litellm",
        "--api-key", "K", "--model", "M", "--base-url", "U",
    ])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_BACKEND"] == "litellm"
    assert values["DAIMON_LLM_API_KEY"] == "K"
    assert values["DAIMON_LLM_MODEL"] == "M"
    assert values["DAIMON_LLM_BASE_URL"] == "U"
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_cli_configure_command_flags_write_env(capsys, monkeypatch, tmp_path):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)

    rc = cli.main([
        "configure", "--backend", "command",
        "--command", "mycli -p", "--output", "json:result",
    ])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_BACKEND"] == "command"
    assert values["DAIMON_LLM_COMMAND"] == "mycli -p"
    assert values["DAIMON_LLM_COMMAND_OUTPUT"] == "json:result"


def test_cli_configure_command_input_flag_write_env(capsys, monkeypatch, tmp_path):
    # #58: --input alongside --command/--output, persisted the same way.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)

    rc = cli.main([
        "configure", "--backend", "command",
        "--command", "devin -p", "--input", "file:--prompt-file",
    ])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_BACKEND"] == "command"
    assert values["DAIMON_LLM_COMMAND"] == "devin -p"
    assert values["DAIMON_LLM_COMMAND_INPUT"] == "file:--prompt-file"


def test_cli_configure_status_surfaces_input_spec(capsys, monkeypatch, tmp_path):
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "devin -p")
    monkeypatch.setenv("DAIMON_LLM_COMMAND_INPUT", "file:--prompt-file")

    rc = cli.main(["configure"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "file:--prompt-file" in out


def test_cli_configure_not_ready_non_tty_prints_guidance(
    capsys, monkeypatch, tmp_path
):
    # No backend, no flags, stdin not a TTY -> must NOT block; print guidance.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    rc = cli.main(["configure"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "no backend" in out or "missing" in out or "✗" in out
    assert not env_file.exists()


def test_cli_configure_interactive_litellm(capsys, monkeypatch, tmp_path):
    # Not ready + TTY + no flags -> interactive prompt path via the _prompt seam.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    answers = iter(["litellm", "U", "M"])  # api_key goes through getpass (#29)
    monkeypatch.setattr(cli, "_prompt", lambda q: next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "K")

    rc = cli.main(["configure"])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_BACKEND"] == "litellm"
    assert values["DAIMON_LLM_API_KEY"] == "K"
    assert values["DAIMON_LLM_MODEL"] == "M"


def test_cli_configure_interactive_command_with_input_spec(
    capsys, monkeypatch, tmp_path
):
    # #58: the interactive command-backend path asks for the input spec too;
    # answered specs are persisted alongside command/output.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    answers = iter(["command", "devin -p", "json:result", "file:--prompt-file"])
    monkeypatch.setattr(cli, "_prompt", lambda q: next(answers))

    rc = cli.main(["configure"])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_BACKEND"] == "command"
    assert values["DAIMON_LLM_COMMAND"] == "devin -p"
    assert values["DAIMON_LLM_COMMAND_OUTPUT"] == "json:result"
    assert values["DAIMON_LLM_COMMAND_INPUT"] == "file:--prompt-file"


def test_cli_configure_interactive_command_blank_input_not_written(
    capsys, monkeypatch, tmp_path
):
    # A blank input-spec answer keeps the stdin default implicit — no key
    # written, matching how blank command/output answers behave.
    env_file = tmp_path / "env"
    monkeypatch.setenv("DAIMON_ENV_FILE", str(env_file))
    _clear_llm_env(monkeypatch)
    _set_claude(monkeypatch, False)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)

    answers = iter(["command", "mycli -p", "", ""])
    monkeypatch.setattr(cli, "_prompt", lambda q: next(answers))

    rc = cli.main(["configure"])
    assert rc == 0

    from daimon_briefing import config

    values = config._file_values()
    assert values["DAIMON_LLM_COMMAND"] == "mycli -p"
    assert "DAIMON_LLM_COMMAND_OUTPUT" not in values  # blank answer -> not written
    assert "DAIMON_LLM_COMMAND_INPUT" not in values


# ---- write-checkpoint: introspection path (#23) — JSON on stdin -> store ----


def _stdin(monkeypatch, text):
    import io
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(text))


def test_cli_write_checkpoint_routes_and_stamps_source(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store

    _stdin(monkeypatch, _valid_json("S-intro"))
    rc = cli.main(["write-checkpoint", "--project", "/p/A"])
    assert rc == 0
    ck = store.read_latest(project_dir="/p/A")
    assert ck["session_id"] == "S-intro"
    assert ck["source"] == "introspection"  # default provenance stamp
    assert (tmp_checkpoint_dir / "-p-A" / "latest.json").exists()


def test_cli_write_checkpoint_source_override(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store

    _stdin(monkeypatch, _valid_json("S-x"))
    rc = cli.main(["write-checkpoint", "--project", "/p/A", "--source", "reconstruction"])
    assert rc == 0
    assert store.read_latest(project_dir="/p/A")["source"] == "reconstruction"


def test_cli_write_checkpoint_invalid_json(tmp_checkpoint_dir, monkeypatch, capsys):
    _stdin(monkeypatch, "not json at all")
    rc = cli.main(["write-checkpoint"])
    assert rc != 0
    assert "invalid checkpoint json" in capsys.readouterr().err.lower()


def test_cli_write_checkpoint_schema_fail(tmp_checkpoint_dir, monkeypatch, capsys):
    import json as _json

    _stdin(monkeypatch, _json.dumps({"session_id": "x"}))  # missing working_context/epistemic
    rc = cli.main(["write-checkpoint"])
    assert rc != 0
    assert "schema validation" in capsys.readouterr().err.lower()


def test_cli_write_checkpoint_no_session_id(tmp_checkpoint_dir, monkeypatch, capsys):
    import json as _json

    _stdin(monkeypatch, _json.dumps({"working_context": {}, "epistemic_snapshot": {}}))
    rc = cli.main(["write-checkpoint"])
    assert rc != 0
    assert "session_id" in capsys.readouterr().err.lower()


def test_top_level_help_has_examples(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Examples:" in out
    assert "daimon brief" in out


def test_status_help_has_example(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["status", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Examples:" in out


# ---- #68: --help formatter — rich-argparse when present, stock when absent -


def test_help_falls_back_to_stock_formatter_when_rich_argparse_absent(monkeypatch, capsys):
    # Same seam test_render.py uses to force an ImportError (_force_rich_absent):
    # a None entry in sys.modules makes `import rich_argparse` raise, so this
    # exercises _formatter_class()'s except-ImportError branch even though the
    # dev venv has rich-argparse installed for the rich-path tests below.
    monkeypatch.setitem(sys.modules, "rich_argparse", None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Examples:" in out
    assert "daimon brief" in out


def test_help_uses_rich_formatter_when_rich_argparse_present(capsys, monkeypatch):
    pytest.importorskip("rich_argparse")
    # The autouse fixture sets DAIMON_PLAIN=1 for test determinism; that would
    # now (correctly) force the stock formatter regardless of rich-argparse's
    # presence, so unset it here to actually exercise the rich path.
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    # content-only smoke: rich-argparse's formatter still carries the epilog
    # and subcommand descriptions through, just with different chrome.
    assert "Examples:" in out
    assert "daimon brief" in out


def test_formatter_class_honors_daimon_plain_even_with_rich_argparse(monkeypatch):
    # DAIMON_PLAIN must win over an importable rich_argparse — _formatter_class
    # has to mirror render.supports_rich()'s ENV-VAR gate (DAIMON_PLAIN checked
    # first, then NO_COLOR), not just the bare import guard. Regression for a
    # review finding: --help used to ignore plain-mode opt-outs entirely.
    pytest.importorskip("rich_argparse")
    import argparse

    monkeypatch.setenv("DAIMON_PLAIN", "1")
    assert cli._formatter_class() is argparse.RawDescriptionHelpFormatter


def test_help_propagates_to_nested_subparsers(capsys):
    # #68: argparse does NOT propagate formatter_class from parent to child —
    # a nested subcommand (team sync, hooks install, skill install) must still
    # get a working --help regardless of which formatter is selected.
    with pytest.raises(SystemExit) as exc:
        cli.main(["team", "sync", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--project" in out

    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        cli.main(["hooks", "install", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "host" in out


def test_cli_anchor_prints_resolved_block(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    proj = (tmp_path / "proj").resolve()
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "m.py").write_text("def foo():\n    return 1\n")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--project", str(proj)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["qualified_name"] == "pkg/m.py::foo"
    assert out["symbol"] == "foo" and len(out["body_hash"]) == 64


def test_cli_anchor_unresolvable_exits_nonzero(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    proj = (tmp_path / "proj").resolve()
    proj.mkdir()
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    rc = cli.main(["anchor", "nope.py", "ghost", "--project", str(proj)])
    assert rc != 0
    assert "could not resolve" in capsys.readouterr().err.lower()


# ---- anchor --attach: patch an anchor into the latest checkpoint (#102) ----


def _anchor_proj(tmp_path, monkeypatch):
    """A resolvable project: proj/pkg/m.py defining foo()."""
    proj = (tmp_path / "proj").resolve()
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "m.py").write_text("def foo():\n    return 1\n")
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    return proj


def test_cli_anchor_attach_single_match_persists(
    tmp_checkpoint_dir, capsys, monkeypatch, tmp_path, sample_checkpoint
):
    from daimon_briefing import store

    proj = _anchor_proj(tmp_path, monkeypatch)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=proj)
    capsys.readouterr()

    # "pinning" matches exactly one item: the strong belief.
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "PINNING",
                   "--project", str(proj)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Extractive pinning prevents silent fact loss" in out
    assert "pkg/m.py::foo" in out

    # The attach persisted through the normal store path: read_latest sees it.
    latest = store.read_latest(project_dir=proj)
    belief = latest["epistemic_snapshot"]["strong_beliefs"][0]
    anchored = belief["anchored_to"]
    assert anchored["file"] == "pkg/m.py" and anchored["symbol"] == "foo"
    assert len(anchored["body_hash"]) == 64
    # Same session re-written, not a new one.
    assert latest["session_id"] == "S-prev"
    # Normal store path means rotation: the pre-attach state became prev-1.
    prev = json.loads(
        (tmp_checkpoint_dir / store.project_slug(proj) / "prev-1.json").read_text()
    )
    assert "anchored_to" not in prev["epistemic_snapshot"]["strong_beliefs"][0]


def test_cli_anchor_attach_zero_matches_exits_nonzero(
    tmp_checkpoint_dir, capsys, monkeypatch, tmp_path, sample_checkpoint
):
    from daimon_briefing import store

    proj = _anchor_proj(tmp_path, monkeypatch)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=proj)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "no-such-text",
                   "--project", str(proj)])
    assert rc != 0
    err = capsys.readouterr().err.lower()
    assert "no cognitive item" in err and "no-such-text" in err
    # Nothing was re-written: latest is untouched.
    latest = store.read_latest(project_dir=proj)
    assert "anchored_to" not in latest["epistemic_snapshot"]["strong_beliefs"][0]


def test_cli_anchor_attach_multiple_matches_lists_candidates(
    tmp_checkpoint_dir, capsys, monkeypatch, tmp_path, sample_checkpoint
):
    from daimon_briefing import store

    proj = _anchor_proj(tmp_path, monkeypatch)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=proj)
    # "serializer" hits the chunk-threshold question AND the D-007 decision.
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "serializer",
                   "--project", str(proj)])
    assert rc != 0
    err = capsys.readouterr().err
    assert "Chunk threshold for the serializer" in err
    assert "Adopt the D-007 prompt for the serializer" in err
    latest = store.read_latest(project_dir=proj)
    for item in latest["working_context"]["open_questions"]:
        assert "anchored_to" not in item


def test_cli_anchor_attach_no_checkpoint_exits_nonzero(
    tmp_checkpoint_dir, capsys, monkeypatch, tmp_path
):
    proj = _anchor_proj(tmp_path, monkeypatch)
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "anything",
                   "--project", str(proj)])
    assert rc != 0
    assert "no checkpoint" in capsys.readouterr().err.lower()


def test_cli_anchor_attach_missing_session_id_exits_nonzero(
    tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path
):
    # Defensive branch: a latest checkpoint without session_id cannot be
    # re-written (write_checkpoint needs it as the filename) — rc 1, no write.
    from daimon_briefing import store
    proj = _anchor_proj(tmp_path, monkeypatch)
    torn = {k: v for k, v in sample_checkpoint.items() if k != "session_id"}
    store.write_checkpoint("S-torn", torn, project_dir=str(proj))
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--attach", "Chunk threshold",
                   "--project", str(proj)])
    assert rc != 0
    assert "session_id" in capsys.readouterr().err


def test_cli_anchor_without_attach_writes_nothing(
    tmp_checkpoint_dir, capsys, monkeypatch, tmp_path, sample_checkpoint
):
    from daimon_briefing import store

    proj = _anchor_proj(tmp_path, monkeypatch)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=proj)
    latest_path = tmp_checkpoint_dir / store.project_slug(proj) / "latest.json"
    before = latest_path.read_text()
    rc = cli.main(["anchor", "pkg/m.py", "foo", "--project", str(proj)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)  # still prints the plain block
    assert out["qualified_name"] == "pkg/m.py::foo"
    assert latest_path.read_text() == before  # byte-identical: no re-write
    assert not (tmp_checkpoint_dir / store.project_slug(proj) / "prev-1.json").exists()


# ---- per-session ledger: serialize.log attribution without cross-session masking ----


def test_session_ledger_buries_nothing_across_sessions():
    # THE BUG: A errors, then B succeeds. Per-session state must keep A=error.
    text = "\n".join([
        "2026-06-10T12:00:00Z session-end: spawned serialize for A (reason: exit, project: /p/A)",
        "error: boom (transcript: /t/A.jsonl) after 3s",
        "2026-06-10T12:10:00Z session-end: spawned serialize for B (reason: exit, project: /p/B)",
        "wrote checkpoint: /c/B.json (took 5s)",
    ])
    led = cli._session_ledger(text, now=0.0)
    assert led["A"]["result_kind"] == "error"
    assert led["A"]["transcript"] == "/t/A.jsonl"
    assert led["A"]["project"] == "/p/A"
    assert led["A"]["spawned"] is True
    assert led["B"]["result_kind"] == "success"


def test_session_ledger_tracks_retry_marker():
    text = "\n".join([
        "2026-06-10T12:00:00Z session-end: spawned serialize for A (reason: exit, project: /p/A)",
        "error: boom (transcript: /t/A.jsonl) after 1s",
        "2026-06-10T12:05:00Z session-start: retry serialize for A (prior: error: boom)",
        "error: boom again (transcript: /t/A.jsonl) after 1s",
    ])
    led = cli._session_ledger(text, now=0.0)
    assert led["A"]["retried"] is True
    assert led["A"]["result_kind"] == "error"


def test_session_ledger_drops_preflight_error_without_transcript():
    text = "error: no LLM API key — set DAIMON_LLM_API_KEY"
    led = cli._session_ledger(text, now=0.0)
    assert led == {}


def test_session_ledger_computes_spawn_age():
    from datetime import datetime, timezone
    now = datetime(2026, 6, 10, 12, 5, 0, tzinfo=timezone.utc).timestamp()
    text = "2026-06-10T12:00:00Z session-end: spawned serialize for A (reason: exit, project: ?)"
    led = cli._session_ledger(text, now=now)
    assert led["A"]["spawn_age"] == 300
    assert led["A"]["project"] is None  # "?" normalises to None
    assert led["A"]["spawned"] is True
    assert led["A"]["result_kind"] is None


# ---- _outstanding_failures: classify lost sessions per checkpoint store ----


def _led(**over):
    base = {"spawned": True, "spawn_ts": 0.0, "spawn_age": 100, "project": "/p/X",
            "result_kind": "error", "result_line": "error: boom (transcript: /t/X.jsonl) after 1s",
            "transcript": "/t/X.jsonl", "retried": False}
    base.update(over)
    return base


def test_outstanding_error_without_checkpoint_is_healable():
    ledger = {"X": _led()}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert len(out) == 1
    assert out[0]["sid"] == "X"
    assert out[0]["kind"] == "error"
    assert out[0]["class"] == "healable"


def test_outstanding_excludes_session_with_checkpoint():
    ledger = {"X": _led()}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: True, 1800, lambda p: True)
    assert out == []


def test_outstanding_excludes_success():
    ledger = {"X": _led(result_kind="success", transcript=None)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out == []


def test_outstanding_retry_marker_is_exhausted_not_healable():
    ledger = {"X": _led(retried=True)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "retry-exhausted"


def test_outstanding_force_promotes_retry_exhausted_to_healable():
    # #15: --force ignores the retry marker when the transcript is still on
    # disk — the session is repairable again, not permanently retry-exhausted.
    ledger = {"X": _led(retried=True)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True, force=True)
    assert out[0]["class"] == "healable"


def test_outstanding_force_does_not_resurrect_missing_transcript():
    # --force can't repair what genuinely can't be repaired: transcript gone
    # is still unrecoverable even when forced.
    ledger = {"X": _led(retried=True, transcript="/gone/X.jsonl")}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: False, force=True)
    assert out[0]["class"] == "unrecoverable"


def test_outstanding_without_force_retry_exhausted_stays_exhausted():
    # force defaults False — status's own call (no force) must keep classifying
    # a retried session as retry-exhausted, matching default heal's refusal.
    ledger = {"X": _led(retried=True)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "retry-exhausted"


def test_outstanding_hung_only_past_ceiling():
    young = {"X": _led(result_kind=None, result_line=None, transcript=None, spawn_age=100)}
    assert cli._outstanding_failures(young, 0.0, lambda sid: False, 1800, lambda p: True) == []
    old = {"X": _led(result_kind=None, result_line=None, transcript=None, spawn_age=3600)}
    out = cli._outstanding_failures(old, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["kind"] == "hung"
    assert out[0]["class"] == "hung"


def test_outstanding_sorted_newest_first():
    ledger = {
        "OLD": _led(spawn_age=9000, transcript="/t/OLD.jsonl"),
        "NEW": _led(spawn_age=60, transcript="/t/NEW.jsonl"),
    }
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert [f["sid"] for f in out] == ["NEW", "OLD"]


def test_outstanding_error_missing_transcript_is_unrecoverable():
    ledger = {"X": _led(transcript="/gone/X.jsonl")}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: False)
    assert out[0]["class"] == "unrecoverable"


def test_outstanding_error_not_spawned_is_unrecoverable():
    ledger = {"X": _led(spawned=False)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "unrecoverable"


def test_outstanding_healable_requires_spawn_and_transcript_on_disk():
    ledger = {"X": _led()}  # spawned=True, transcript set
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "healable"


# ---- _status_health: objective verdict from checkpoint + siblings + outstanding ----


def _proj(exists=True, sid="P", age=100):
    return {"exists": exists, "session_id": sid, "age_seconds": age, "age": "1m"} if exists else {"exists": False}


def test_status_health_fresh():
    h = cli._status_health(_proj(), {"exists": True, "session_id": "P", "same_session_as_project": True},
                           [], [], now=1000.0)
    assert h["ok"] is True and h["verdict"].startswith("✓")


def test_status_health_flags_newer_sibling():
    # project checkpoint mtime = now - age = 1000 - 100 = 900; sibling mtime 950 is NEWER
    sib = {"slug": "-p-sub", "path": "/x", "session_id": "C", "mtime": 950.0}
    h = cli._status_health(_proj(age=100), {"exists": False}, [], [sib], now=1000.0)
    assert h["ok"] is False
    assert any("-p-sub" in w and "split" in w.lower() for w in h["warnings"])
    assert h["verdict"].startswith("⚠")


def test_status_health_older_sibling_not_flagged():
    sib = {"slug": "-p-sub", "path": "/x", "session_id": "C", "mtime": 800.0}  # older than 900
    h = cli._status_health(_proj(age=100), {"exists": True}, [], [sib], now=1000.0)
    assert h["ok"] is True


def test_status_health_no_checkpoint():
    h = cli._status_health(_proj(exists=False), {"exists": False}, [], [], now=1000.0)
    assert h["ok"] is False
    assert any("no checkpoint" in w.lower() for w in h["warnings"])


def test_status_health_outstanding_failures():
    h = cli._status_health(_proj(), {"exists": True}, [{"sid": "S"}], [], now=1000.0)
    assert h["ok"] is False
    assert any("failed to serialize" in w.lower() for w in h["warnings"])


def test_status_health_flags_version_mismatch():
    proj = {"exists": True, "session_id": "P", "age_seconds": 100, "age": "1m",
            "format_version": "D-000"}
    h = cli._status_health(proj, {"exists": True}, [], [], now=1000.0)
    assert h["ok"] is False
    assert any("format" in w.lower() and "D-000" in w for w in h["warnings"])


def test_status_health_current_version_no_warning():
    from daimon_briefing import serializer
    proj = {"exists": True, "session_id": "P", "age_seconds": 100, "age": "1m",
            "format_version": serializer.PROMPT_VERSION}
    h = cli._status_health(proj, {"exists": True, "same_session_as_project": True},
                           [], [], now=1000.0)
    assert h["ok"] is True


def test_status_health_legacy_checkpoint_no_version_warning():
    # A legacy checkpoint carries no format_version — nothing to compare, no warning.
    proj = {"exists": True, "session_id": "P", "age_seconds": 100, "age": "1m"}
    h = cli._status_health(proj, {"exists": True, "same_session_as_project": True},
                           [], [], now=1000.0)
    assert h["ok"] is True


def test_status_health_version_mismatch_on_global_fallback():
    # No project checkpoint -> briefing falls back to global; its version is checked.
    glob = {"exists": True, "session_id": "G", "format_version": "D-000"}
    h = cli._status_health(_proj(exists=False), glob, [], [], now=1000.0)
    assert h["ok"] is False
    assert any("format" in w.lower() for w in h["warnings"])


def test_cmd_status_json_has_health_and_siblings(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    # a project checkpoint + a newer sibling bucket
    root = str(tmp_checkpoint_dir.parent)  # some real dir; slug derived from it
    monkeypatch.setattr(cli, "_resolve_project", lambda p: root)
    slug = store.project_slug(root)
    (tmp_checkpoint_dir / slug).mkdir(parents=True)
    (tmp_checkpoint_dir / slug / "latest.json").write_text('{"session_id": "P"}')
    child = tmp_checkpoint_dir / (slug + "-sub")
    child.mkdir()
    (child / "latest.json").write_text('{"session_id": "C"}')

    class A:
        project = root
        json = True
    cli._cmd_status(A())
    out = json.loads(capsys.readouterr().out)
    assert "health" in out and "siblings" in out
    assert out["project"]["slug"] == slug


# ---- #86: _heal_plan — pure decision function ----


def test_heal_plan_targets_newest_healable(monkeypatch):
    from daimon_briefing import cli, ledger
    # Build outstanding via a stubbed _compute_outstanding so the plan logic is unit-tested in isolation.
    items = [
        {"sid": "S-new", "class": "healable", "transcript": "/t/new.jsonl", "project": "/p",
         "age_str": "1m", "line": "error: boom (transcript: /t/new.jsonl) after 3s"},
        {"sid": "S-old", "class": "retry-exhausted", "transcript": "/t/old.jsonl", "project": "/p",
         "age_str": "9m", "line": "error: boom (transcript: /t/old.jsonl) after 3s"},
    ]
    monkeypatch.setattr(ledger, "_compute_outstanding", lambda text, now, force=False: items)
    plan = cli._heal_plan("logtext", 1000.0)
    assert plan["target"]["sid"] == "S-new"
    assert plan["target"]["transcript"] == "/t/new.jsonl"
    assert plan["note"] == ""
    assert len(plan["skipped"]) == 1
    assert plan["skipped"][0]["sid"] == "S-old"
    assert "retry already attempted" in plan["skipped"][0]["reason"]


def test_heal_plan_second_healable_says_rerun(monkeypatch):
    from daimon_briefing import cli, ledger
    items = [
        {"sid": "S1", "class": "healable", "transcript": "/t/1", "project": "/p", "age_str": "1m",
         "line": "error: x (transcript: /t/1) after 1s"},
        {"sid": "S2", "class": "healable", "transcript": "/t/2", "project": "/p", "age_str": "2m",
         "line": "error: x (transcript: /t/2) after 1s"},
    ]
    monkeypatch.setattr(ledger, "_compute_outstanding", lambda text, now, force=False: items)
    plan = cli._heal_plan("x", 1000.0)
    assert plan["target"]["sid"] == "S1"
    assert plan["skipped"][0]["sid"] == "S2"
    assert "re-run" in plan["skipped"][0]["reason"]


def test_heal_plan_no_log(monkeypatch):
    from daimon_briefing import cli, ledger
    monkeypatch.setattr(ledger, "_compute_outstanding", lambda text, now, force=False: [])
    plan = cli._heal_plan("", 1000.0)
    assert plan["target"] is None and plan["skipped"] == []
    assert "no serialize activity logged" in plan["note"]


def test_heal_plan_no_outstanding(monkeypatch):
    from daimon_briefing import cli, ledger
    monkeypatch.setattr(ledger, "_compute_outstanding", lambda text, now, force=False: [])
    plan = cli._heal_plan("some log with only successes", 1000.0)
    assert plan["target"] is None
    assert "no outstanding failures" in plan["note"]


def test_heal_plan_only_unrepairable(monkeypatch):
    from daimon_briefing import cli, ledger
    items = [{"sid": "H1", "class": "hung", "transcript": None, "project": None,
              "age_str": "40m", "line": None}]
    monkeypatch.setattr(ledger, "_compute_outstanding", lambda text, now, force=False: items)
    plan = cli._heal_plan("x", 1000.0)
    assert plan["target"] is None
    assert "can't be auto-repaired" in plan["note"]
    assert plan["skipped"][0]["sid"] == "H1" and "hung" in plan["skipped"][0]["reason"]


def test_heal_plan_force_targets_retry_exhausted(monkeypatch):
    # #15: force=True forwards to _compute_outstanding, which itself
    # promotes retry-exhausted -> healable; _heal_plan just picks it up like
    # any other healable target — no special-casing needed at this layer.
    from daimon_briefing import cli, ledger
    items = [
        {"sid": "S-old", "class": "healable", "transcript": "/t/old.jsonl", "project": "/p",
         "age_str": "9m", "line": "error: boom (transcript: /t/old.jsonl) after 3s"},
    ]
    seen = {}

    def fake_compute_outstanding(text, now, force=False):
        seen["force"] = force
        return items

    monkeypatch.setattr(ledger, "_compute_outstanding", fake_compute_outstanding)
    plan = cli._heal_plan("x", 1000.0, force=True)
    assert seen["force"] is True
    assert plan["target"]["sid"] == "S-old"


# ---- #86: _cmd_heal — render always, gate the serialize, --dry-run ----


def test_cmd_heal_dry_run_does_not_serialize(monkeypatch, capsys):
    from daimon_briefing import cli
    plan = {"target": {"sid": "S-A", "transcript": "/t/a.jsonl", "project": "/p",
                       "age_str": "3m", "line": "error: x (transcript: /t/a.jsonl) after 1s"},
            "skipped": [], "note": ""}
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now, force=False: plan)
    called = {"n": 0}
    monkeypatch.setattr(cli, "_run_serialize", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or 0)
    monkeypatch.setattr(cli, "_append_retry_log", lambda *a, **k: called.__setitem__("retry", True))

    class A:
        dry_run = True
    rc = cli._cmd_heal(A())
    assert rc == 0
    assert called["n"] == 0 and "retry" not in called          # no serialize, no retry-log
    assert "would heal S-A" in capsys.readouterr().out


def test_cmd_heal_real_serializes_target(monkeypatch, tmp_path):
    from daimon_briefing import cli
    tp = tmp_path / "a.jsonl"
    tp.write_text("{}")
    plan = {"target": {"sid": "S-A", "transcript": str(tp), "project": "/p",
                       "age_str": "3m", "line": "error: x (transcript: %s) after 1s" % tp},
            "skipped": [], "note": ""}
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now, force=False: plan)
    monkeypatch.setattr(cli, "_append_retry_log", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(cli, "_run_serialize", lambda path, proj: seen.update(path=path, proj=proj) or 0)

    class A:
        dry_run = False
    rc = cli._cmd_heal(A())
    assert rc == 0
    assert str(seen["path"]) == str(tp) and seen["proj"] == "/p"


def test_cmd_heal_no_target_returns_zero(monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now, force=False: {"target": None, "skipped": [], "note": "nothing to heal — no outstanding failures"})
    monkeypatch.setattr(cli, "_run_serialize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not serialize")))

    class A:
        dry_run = False
    assert cli._cmd_heal(A()) == 0
    assert "no outstanding failures" in capsys.readouterr().out


def test_cmd_heal_forwards_force_to_heal_plan(monkeypatch):
    from daimon_briefing import cli
    seen = {}

    def fake_heal_plan(text, now, force=False):
        seen["force"] = force
        return {"target": None, "skipped": [], "note": "nothing to heal — no outstanding failures"}

    monkeypatch.setattr(cli, "_heal_plan", fake_heal_plan)

    class A:
        dry_run = False
        force = True
    assert cli._cmd_heal(A()) == 0
    assert seen["force"] is True


def test_heal_argparser_has_dry_run(monkeypatch):
    # `main()` builds the parser inline (no exposed `_build_parser`/`_parse_args`
    # factory) and immediately dispatches to `args.func(args)`. Stub `_cmd_heal`
    # to capture the parsed namespace without running a real heal.
    from daimon_briefing import cli
    seen = {}
    monkeypatch.setattr(cli, "_cmd_heal", lambda args: seen.setdefault("dry_run", args.dry_run) or 0)

    cli.main(["heal", "--dry-run"])
    assert seen["dry_run"] is True

    seen.clear()
    cli.main(["heal"])
    assert seen["dry_run"] is False


def test_run_serialize_too_short_is_skipped(tmp_path, tmp_log_dir, fake_chat_factory, monkeypatch, capsys):
    import json
    from daimon_briefing import cli
    tp = tmp_path / "S-short.jsonl"
    # one user message — below the default min_messages (10)
    tp.write_text(json.dumps({"type": "user", "role": "user", "content": "hi"}) + "\n")
    monkeypatch.setattr(cli, "_chat", fake_chat_factory("{}"))  # must NOT be called
    rc = cli._run_serialize(tp, "/p")
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped serialize for S-short" in out
    assert "transcript too short" in out
    log = (tmp_log_dir / "serialize.log").read_text()
    assert "skipped serialize for S-short" in log
    assert "error:" not in log            # NOT an error line


# ---- #185: identical-bytes guard skips a re-serialize of an unchanged transcript ----


def test_run_serialize_skips_when_transcript_hash_matches_checkpoint(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    from daimon_briefing import store, transcript

    tp = FIXTURES / "sample_transcript.md"
    sid = tp.stem
    sha = transcript.file_sha256(tp)
    store.write_checkpoint(sid, {**json.loads(_valid_json(sid)), "transcript_hash": sha})

    monkeypatch.setattr(cli, "_chat", fake_chat_factory("must not be called"))
    rc = cli._run_serialize(tp, "/p")
    assert rc == 0
    out = capsys.readouterr().out
    assert f"skipped serialize for {sid}: transcript unchanged since checkpoint (hash match)" in out
    log = (tmp_log_dir / "serialize.log").read_text()
    assert f"skipped serialize for {sid}: transcript unchanged since checkpoint (hash match)" in log
    assert "error:" not in log


def test_run_serialize_proceeds_when_transcript_hash_differs(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    from daimon_briefing import store

    tp = FIXTURES / "sample_transcript.md"
    sid = tp.stem
    store.write_checkpoint(sid, {**json.loads(_valid_json(sid)), "transcript_hash": "stale-hash"})

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json(sid)))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(tp, "/p")
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote checkpoint" in out
    assert "skipped" not in out


def test_run_serialize_proceeds_when_no_existing_checkpoint(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    tp = FIXTURES / "sample_transcript.md"
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json(tp.stem)))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(tp, "/p")
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote checkpoint" in out


def test_run_serialize_proceeds_when_existing_checkpoint_has_no_hash(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    from daimon_briefing import store

    tp = FIXTURES / "sample_transcript.md"
    sid = tp.stem
    ck = json.loads(_valid_json(sid))
    ck.pop("transcript_hash", None)
    store.write_checkpoint(sid, ck)

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json(sid)))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli._run_serialize(tp, "/p")
    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote checkpoint" in out


def test_run_serialize_skip_is_classified_skipped_by_ledger(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    # #185: the new skip reason must classify identically to the existing
    # too-short skip — never "error", never outstanding/healable.
    import time

    from daimon_briefing import store, transcript

    tp = FIXTURES / "sample_transcript.md"
    sid = tp.stem
    sha = transcript.file_sha256(tp)
    store.write_checkpoint(sid, {**json.loads(_valid_json(sid)), "transcript_hash": sha})
    monkeypatch.setattr(cli, "_chat", fake_chat_factory("must not be called"))
    cli._run_serialize(tp, "/p")
    capsys.readouterr()

    log = (tmp_log_dir / "serialize.log").read_text()
    entry = cli._session_ledger(log, time.time()).get(sid)
    assert entry is not None
    assert entry["result_kind"] == "skipped"
    outstanding = cli._compute_outstanding(log, time.time())
    assert sid not in [f["sid"] for f in outstanding]


def test_session_ledger_recognizes_skipped():
    from daimon_briefing import cli
    text = "skipped serialize for S1: transcript too short (1 < 10 messages)\n"
    led = cli._session_ledger(text, 1000.0)
    assert led["S1"]["result_kind"] == "skipped"


def test_outstanding_excludes_skipped_even_with_spawn():
    from daimon_briefing import cli
    ledger = {"S1": {"spawned": True, "spawn_age": 9999, "project": None,
                     "result_kind": "skipped", "result_line": "x",
                     "transcript": None, "retried": False}}
    out = cli._outstanding_failures(ledger, 10000.0, has_checkpoint=lambda s: False,
                                    ceiling=100, transcript_exists=lambda p: True)
    assert out == []


def test_outstanding_still_flags_real_error():
    from daimon_briefing import cli
    ledger = {"E1": {"spawned": True, "spawn_age": 5, "project": "/p",
                     "result_kind": "error",
                     "result_line": "error: boom (transcript: /t/E1.jsonl) after 1s",
                     "transcript": "/t/E1.jsonl", "retried": False}}
    out = cli._outstanding_failures(ledger, 100.0, has_checkpoint=lambda s: False,
                                    ceiling=100, transcript_exists=lambda p: True)
    assert len(out) == 1 and out[0]["class"] == "healable"


def test_compute_outstanding_ignores_too_short_skip(tmp_checkpoint_dir):
    from daimon_briefing import cli
    text = "skipped serialize for S1: transcript too short (1 < 10 messages)\n"
    assert cli._compute_outstanding(text, 1000.0) == []


def test_compute_outstanding_spawn_then_skip_not_outstanding(tmp_checkpoint_dir):
    # The real scenario the fix targets: a session was spawned, then serialize
    # found it too short -> skipped. It must NOT be outstanding (not hung, not healable).
    from daimon_briefing import cli
    sid = "S-spawnskip"
    text = (
        f"2026-07-01T12:00:00Z session-end: spawned serialize for {sid} (model: m, platform: cli, project: /p)\n"
        f"skipped serialize for {sid}: transcript too short (1 < 10 messages)\n"
    )
    # now far in the future so a bare spawn would be 'hung' if the skip weren't recognized
    assert cli._compute_outstanding(text, 9_999_999_999.0) == []


# ---- #100: scar-candidate harvest wired into the serialize SUCCESS path ----
# harvest.run shipped fully tested but was reachable only from the (never-shipped)
# hermes host; the real hosts (Claude Code / Codex) reach it via `daimon serialize`,
# so _run_serialize now fires it — opt-in, and strictly best-effort so it can never
# change the rc or the byte-identical print/log result contract.


def _harvest_recorder(monkeypatch):
    """Replace harvest.run with a recorder; return the dict it fills on call."""
    from daimon_briefing import harvest
    seen = {}

    def _fake(messages, project_root, session_id):
        seen.update(messages=messages, project_root=project_root, session_id=session_id)
        return 0

    monkeypatch.setattr(harvest, "run", _fake)
    return seen


def _no_harvest(monkeypatch):
    """Record harvest.run calls; the caller asserts the list stayed empty. A
    raising fake would be swallowed by the call site's best-effort except —
    recording keeps the not-called assertion honest."""
    from daimon_briefing import harvest
    calls = []
    monkeypatch.setattr(harvest, "run", lambda *a, **k: calls.append((a, k)) or 0)
    return calls


def test_serialize_success_runs_harvest_when_enabled(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    seen = _harvest_recorder(monkeypatch)

    from daimon_briefing import transcript
    path = FIXTURES / "sample_transcript.md"
    rc = cli._run_serialize(path, "/p")

    assert rc == 0
    # same transcript messages, project routed AS-IS, session = transcript stem
    assert seen["messages"] == transcript.from_file(path)
    assert seen["project_root"] == "/p"
    assert seen["session_id"] == "sample_transcript"


def test_serialize_success_skips_harvest_when_disabled(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # DAIMON_SCAR_HARVEST unset -> gate closed -> harvest never fires.
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("DAIMON_SCAR_HARVEST", raising=False)
    calls = _no_harvest(monkeypatch)

    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", "/p")
    assert rc == 0
    assert calls == []


def test_serialize_too_short_never_harvests(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, tmp_path
):
    # A benign too-short skip returns before the checkpoint write; harvest follows
    # ONLY a successful write, so it must not run even with the gate open.
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    calls = _no_harvest(monkeypatch)

    short = tmp_path / "S-short.jsonl"
    short.write_text(json.dumps({"type": "user", "role": "user", "content": "hi"}) + "\n")
    rc = cli._run_serialize(short, "/p")
    assert rc == 0
    assert calls == []


def test_serialize_failure_never_harvests(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # A SerializeError (unparseable LLM output) fails before the write; no harvest.
    monkeypatch.setattr(cli, "_chat", fake_chat_factory("prose, definitely not JSON"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    calls = _no_harvest(monkeypatch)

    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", "/p")
    assert rc == 1
    assert calls == []


def test_harvest_exception_leaves_rc_and_result_line_intact(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch, capsys
):
    # ANY harvest failure is swallowed: rc stays 0 and the success result line is
    # printed AND logged byte-identically (the #27 contract must not shift).
    from daimon_briefing import harvest
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    monkeypatch.setattr(
        harvest, "run",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("harvest blew up")),
    )

    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", "/p")
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("wrote checkpoint:") and out.endswith("s)")
    log = (tmp_log_dir / "serialize.log").read_text().splitlines()
    assert log[-1] == out  # logged line byte-identical to the printed one


def test_serialize_forwards_none_project_to_harvest(
    tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, monkeypatch
):
    # project=None (global-only serialize) is forwarded AS-IS; harvest.run owns the
    # None guard (it no-ops), so the call site stays a thin gate — no duplicate check.
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_SCAR_HARVEST", "1")
    seen = _harvest_recorder(monkeypatch)

    rc = cli._run_serialize(FIXTURES / "sample_transcript.md", None)
    assert rc == 0
    assert seen["project_root"] is None


# ---- brief --team: Teammates section, self-exclusion, empty-team no-op (#111) ----


def test_cli_brief_team_shows_teammate(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-1", sample_checkpoint, project_dir=proj)
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    rc = cli.main(["brief", "--team", "--project", proj])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Teammates" in out
    assert "grace" in out
    assert "Wiring the on_session_end hook" in out  # teammate's active topic


def test_cli_brief_team_excludes_self(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("a-1", sample_checkpoint, project_dir=proj)

    rc = cli.main(["brief", "--team", "--project", proj])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Teammates" not in out  # only self in the team dir → no section


def test_cli_brief_team_empty_is_byte_identical(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    # DAIMON_TEAM off (fixture default) → nothing mirrored → empty team dir.
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=proj)

    cli.main(["brief", "--project", proj])
    plain = capsys.readouterr().out
    cli.main(["brief", "--team", "--project", proj])
    teamed = capsys.readouterr().out
    assert teamed == plain  # empty team → byte-identical to a non-team briefing


def test_cli_brief_team_respects_decision_cap(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_MAX_BRIEFING_DECISIONS", "1")  # #77 cap, reused for teammates
    monkeypatch.setenv("DAIMON_AUTHOR", "grace")
    store.write_checkpoint("g-1", sample_checkpoint, project_dir=proj)  # 2 recent_decisions
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")

    rc = cli.main(["brief", "--team", "--project", proj])
    assert rc == 0
    out = capsys.readouterr().out
    assert "earlier decision" in out  # overflow marker: cap dropped 1 of grace's 2


# ---- recall: FTS search over local + team checkpoint history (#112) ----


def _recall_checkpoint(sid, text, created="2025-01-01T00:00:00Z"):
    return {
        "session_id": sid,
        "created": created,
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [{"text": text, "trust": "inferred"}],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def test_cli_recall_prints_result_lines(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1", _recall_checkpoint("S1", "Adopt pangolin caching"), project_dir=proj)
    rc = cli.main(["recall", "pangolin", "--project", proj])
    assert rc == 0
    out = capsys.readouterr().out
    # `[author] [trust] [kind] text (session, age)`
    assert "[ada]" in out
    assert "[inferred]" in out
    assert "[decision]" in out
    assert "Adopt pangolin caching" in out
    assert "(S1," in out
    assert "ago)" in out


def test_cli_recall_multiword_query(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1", _recall_checkpoint("S1", "Adopt pangolin caching"), project_dir=proj)
    rc = cli.main(["recall", "pangolin", "caching", "--project", proj])
    assert rc == 0
    assert "pangolin caching" in capsys.readouterr().out


def test_cli_recall_flags_typed_supersession_only(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    # v3 (#234): recency alone renders NO flag; a typed supersedes link does.
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-old", _recall_checkpoint(
            "S-old", "meerkat burrow mapping plan for the colony",
            "2021-01-01T00:00:00Z"),
        project_dir=proj)
    newer = _recall_checkpoint(
        "S-new", "abandoned meerkat burrow mapping plan colony too unstable",
        "2025-01-01T00:00:00Z")
    newer["working_context"]["recent_decisions"][0]["links"] = [
        {"type": "supersedes", "target": "meerkat burrow mapping plan colony"}]
    store.write_checkpoint("S-new", newer, project_dir=proj)
    rc = cli.main(["recall", "meerkat", "--project", proj])
    assert rc == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "meerkat" in ln]
    assert len(lines) == 2
    assert "superseded" not in lines[0]  # live item first, no recency flag
    assert "superseded by S-new" in lines[1]  # link evidence renders


def test_cli_recall_json(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S1", _recall_checkpoint("S1", "Adopt pangolin caching"), project_dir=proj)
    rc = cli.main(["recall", "pangolin", "--project", proj, "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["text"] == "Adopt pangolin caching"
    assert data[0]["author"] == "ada"
    assert data[0]["kind"] == "decision"
    assert data[0]["session_id"] == "S1"


def test_cli_recall_project_scoping_and_all_projects(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj_a = str((tmp_path / "proj-a").resolve())
    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")  # team stamp attributes both projects
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", _recall_checkpoint("S-a", "lemur work in a"),
                           project_dir=proj_a)
    store.write_checkpoint("S-b", _recall_checkpoint("S-b", "lemur work in b"),
                           project_dir=proj_b)

    rc = cli.main(["recall", "lemur", "--project", proj_a])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lemur work in a" in out
    assert "lemur work in b" not in out

    rc = cli.main(["recall", "lemur", "--all-projects"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lemur work in a" in out
    assert "lemur work in b" in out


def test_cli_recall_no_matches(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    proj = str((tmp_path / "proj").resolve())
    rc = cli.main(["recall", "nonexistentword", "--project", proj])
    assert rc == 0
    assert "no matches" in capsys.readouterr().out


def test_cli_recall_hostile_query_never_tracebacks(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S1", _recall_checkpoint("S1", "x"), project_dir=proj)
    for hostile in ['"', 'AND', '(((', 'a NEAR/2 b', '🔥"']:
        rc = cli.main(["recall", hostile, "--project", proj])
        assert rc == 0  # weird query → no matches, never a traceback


def test_cli_recall_fts5_unavailable_clear_error(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import recall

    proj = str((tmp_path / "proj").resolve())

    def _boom(*args, **kwargs):
        raise recall.RecallError("sqlite3 has no FTS5 module — details here")

    monkeypatch.setattr(recall, "search", _boom)
    rc = cli.main(["recall", "anything", "--project", proj])
    assert rc == 1
    err = capsys.readouterr().err
    assert "FTS5" in err
    assert "Traceback" not in err


# ---- serialize stamps `created` from session end, not write time (#123) ----


def _timed_jsonl(tmp_path, name, stamps):
    rows = []
    for i, ts in enumerate(stamps):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append({"type": role,
                     "message": {"role": role, "content": f"turn {i}"},
                     "timestamp": ts})
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def test_cli_serialize_stamps_created_from_transcript_end(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    from daimon_briefing import store

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("healed")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    p = _timed_jsonl(tmp_path, "healed.jsonl", [
        "2026-06-30T22:00:00.000Z",
        "2026-06-30T22:05:00.250Z",
        "2026-06-30T22:10:05.999Z",
    ])
    rc = cli.main(["serialize", str(p)])
    assert rc == 0
    ckpt = store.read_checkpoint("healed")
    assert ckpt["created"] == "2026-06-30T22:10:05Z"


def test_cli_serialize_created_falls_back_to_transcript_mtime(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    from daimon_briefing import store

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("old_notes")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    p = tmp_path / "old_notes.md"
    p.write_text(
        "**user**: alpha\n**assistant**: beta\n**user**: gamma\n"
        "**assistant**: delta\n**user**: epsilon\n",
        encoding="utf-8",
    )
    # transcript last touched 2026-06-01T00:00:00Z — long before "now"
    import calendar
    epoch = calendar.timegm((2026, 6, 1, 0, 0, 0))
    os.utime(p, (epoch, epoch))
    rc = cli.main(["serialize", str(p)])
    assert rc == 0
    ckpt = store.read_checkpoint("old_notes")
    assert ckpt["created"] == "2026-06-01T00:00:00Z"


def test_cli_heal_of_old_session_does_not_steal_latest(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    """End-to-end #123: serialize a NEWER session, then re-serialize an OLDER
    transcript (the heal case) — latest must keep pointing at the newer one."""
    from daimon_briefing import store

    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-newer")))
    newer = _timed_jsonl(tmp_path, "S-newer.jsonl", [
        "2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z",
    ])
    assert cli.main(["serialize", str(newer)]) == 0

    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-older")))
    older = _timed_jsonl(tmp_path, "S-older.jsonl", [
        "2026-06-25T08:00:00Z", "2026-06-25T08:01:00Z", "2026-06-25T08:02:00Z",
    ])
    assert cli.main(["serialize", str(older)]) == 0

    assert store.read_latest()["session_id"] == "S-newer"
    assert store.read_checkpoint("S-older") is not None


# ---- #125: recall-inject — proactive suggestion backend for the prompt hook ----


def _seed_recall_history(project="/repo/x"):
    from daimon_briefing import store

    store.write_checkpoint(
        "S-old",
        {"session_id": "S-old", "created": "2026-06-20T00:00:00Z",
         "working_context": {
             "active_topic": {"text": "gateway debugging", "trust": "inferred"},
             "open_questions": [{
                 "text": "LiteLLM gateway response cache pins identical bad responses",
                 "trust": "verbatim", "quote": "cache answers instantly",
                 "importance": 9, "first_seen": "2026-06-20T00:00:00Z"}],
             "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}},
        project_dir=project,
    )
    store.write_checkpoint(
        "S-latest",
        {"session_id": "S-latest", "created": "2026-06-28T00:00:00Z",
         "working_context": {
             "active_topic": {"text": "unrelated newer work", "trust": "inferred"},
             "open_questions": [], "recent_decisions": []},
         "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                "contradictions_flagged": []}},
        project_dir=project,
    )


def _inject(monkeypatch, capsys, prompt, session="S-now", project="/repo/x"):
    monkeypatch.setattr("sys.stdin", io.StringIO(prompt))
    rc = cli.main(["recall-inject", "--project", project, "--session", session])
    return rc, capsys.readouterr().out


import io  # noqa: E402 — used by _inject above


def test_recall_inject_surfaces_prior_work(tmp_checkpoint_dir, capsys, monkeypatch):
    _seed_recall_history()
    rc, out = _inject(monkeypatch, capsys,
                      "debugging the litellm gateway cache pinning again")
    assert rc == 0
    assert "S-old" in out and "cache" in out
    assert "daimon recall" in out  # points at the deep-dive command


def test_recall_inject_excludes_latest_briefed_session(tmp_checkpoint_dir, capsys, monkeypatch):
    _seed_recall_history()
    # Prompt matching only the LATEST checkpoint's content: briefing covered it.
    rc, out = _inject(monkeypatch, capsys, "continuing the unrelated newer work")
    assert rc == 0
    assert out == ""


def test_recall_inject_cooldown_fires_once_per_session(tmp_checkpoint_dir, capsys, monkeypatch):
    _seed_recall_history()
    rc1, out1 = _inject(monkeypatch, capsys,
                        "debugging the litellm gateway cache pinning again")
    rc2, out2 = _inject(monkeypatch, capsys,
                        "still stuck on that litellm gateway cache pinning")
    assert out1 != "" and rc1 == 0
    assert out2 == "" and rc2 == 0  # same checkpoint already suggested this session


def test_recall_inject_no_match_is_silent_rc_zero(tmp_checkpoint_dir, capsys, monkeypatch):
    _seed_recall_history()
    rc, out = _inject(monkeypatch, capsys, "completely unrelated flamingo topiary hobby")
    assert rc == 0 and out == ""


def test_recall_inject_never_fails(tmp_checkpoint_dir, capsys, monkeypatch):
    # No history, no index, unknown project — still rc 0, still silent.
    monkeypatch.setattr("sys.stdin", io.StringIO("anything at all here"))
    rc = cli.main(["recall-inject", "--session", "S-x"])
    assert rc == 0


# ---- #33 Phase 2: deterministic carry wired into the serialize path ----


def _prev_with_open_question(project, prev_created, first_seen):
    from daimon_briefing import store

    prev = {
        "session_id": "S-prev",
        "created": prev_created,
        "working_context": {
            "active_topic": {"text": "prior topic", "trust": "inferred"},
            "open_questions": [
                {"text": "quorint reconciliation loop unresolved", "trust": "inferred",
                 "importance": 7, "first_seen": first_seen},
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    store.write_checkpoint("S-prev", prev, project_dir=project)


def test_serialize_carry_folds_prev_open_question(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    from daimon_briefing import store

    project = "/p/carry"
    _prev_with_open_question(project, "2026-06-25T08:00:00Z", "2026-06-28T00:00:00Z")

    # FakeChat emits a valid checkpoint WITHOUT the prev question (_valid_json's
    # only open question is "PR #6 state").
    chat = fake_chat_factory(_valid_json("S-new"))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)

    p = _timed_jsonl(tmp_path, "S-new.jsonl", [
        "2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z",
    ])
    rc = cli.main(["serialize", str(p)])
    assert rc == 0

    written = store.read_latest(project_dir=project)
    carried = [q for q in written["working_context"]["open_questions"]
               if q.get("text") == "quorint reconciliation loop unresolved"]
    assert len(carried) == 1
    assert carried[0]["carried_from"] == "S-prev"


def test_serialize_carry_kill_switch(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    from daimon_briefing import store

    project = "/p/carry-off"
    _prev_with_open_question(project, "2026-06-25T08:00:00Z", "2026-06-28T00:00:00Z")

    chat = fake_chat_factory(_valid_json("S-new"))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    monkeypatch.setenv("DAIMON_CARRY", "0")

    p = _timed_jsonl(tmp_path, "S-new.jsonl", [
        "2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z",
    ])
    rc = cli.main(["serialize", str(p)])
    assert rc == 0

    written = store.read_latest(project_dir=project)
    texts = {q.get("text") for q in written["working_context"]["open_questions"]}
    assert "quorint reconciliation loop unresolved" not in texts


def test_serialize_carry_ignores_other_projects_checkpoint(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    """#94: a fresh project's FIRST serialize has no per-project pointer, so
    carry's read_latest used to fall back to the global pointer — the most
    recent checkpoint of ANY project — and permanently fold a foreign
    project's items into this project's bucket. Carry must read only the
    project's own pointer."""
    from daimon_briefing import store

    _prev_with_open_question("/p/other-project", "2026-06-25T08:00:00Z",
                             "2026-06-28T00:00:00Z")

    chat = fake_chat_factory(_valid_json("S-new"))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/fresh-project")

    p = _timed_jsonl(tmp_path, "S-new.jsonl", [
        "2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z",
    ])
    rc = cli.main(["serialize", str(p)])
    assert rc == 0

    written = store.read_latest(project_dir="/p/fresh-project")
    texts = {q.get("text") for q in written["working_context"]["open_questions"]}
    assert "quorint reconciliation loop unresolved" not in texts


def test_serialize_carry_failure_still_writes_checkpoint(
    tmp_checkpoint_dir, fake_chat_factory, monkeypatch, tmp_path
):
    """A broken carry.merge must not lose the checkpoint (advisory feature —
    fail-open, same idiom as harvest.run's swallow just below)."""
    from daimon_briefing import carry, store

    project = "/p/carry-broken"
    _prev_with_open_question(project, "2026-06-25T08:00:00Z", "2026-06-28T00:00:00Z")

    def _boom(*args, **kwargs):
        raise RuntimeError("carry exploded")

    monkeypatch.setattr(carry, "merge", _boom)

    chat = fake_chat_factory(_valid_json("S-new"))
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)

    p = _timed_jsonl(tmp_path, "S-new.jsonl", [
        "2026-07-01T10:00:00Z", "2026-07-01T10:01:00Z", "2026-07-01T10:02:00Z",
    ])
    rc = cli.main(["serialize", str(p)])
    assert rc == 0

    written = store.read_latest(project_dir=project)
    assert written["session_id"] == "S-new"


# ---- #14 Task 4: candidate emission behind the human-speaks-once gate ----


def test_candidate_emitted_when_no_prior_event(tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import store

    project = "/p/candidate-emit"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    pairs = [("r-old", "r-new", "old text")]
    count = cli._emit_supersede_candidates(pairs, {}, project)
    assert count == 1

    slug = store.project_slug(project)
    lines = (tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()
    evt = json.loads(lines[0])
    assert evt["item_ref"] == "r-old"
    assert evt["status"] == "supersede-candidate:r-new"
    assert evt["source"] == "serializer"
    assert evt["item_text"] == "old text"


def test_candidate_skipped_after_human_confirm_and_reject(tmp_checkpoint_dir, monkeypatch):
    project = "/p/candidate-human"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    pairs = [("r-old", "r-new", "old text")]

    events_confirmed = {"r-old": {"status": "superseded-by:r-new", "source": "cli"}}
    assert cli._emit_supersede_candidates(pairs, events_confirmed, project) == 0

    events_rejected = {"r-old": {"status": "reopened", "source": "cli"}}
    assert cli._emit_supersede_candidates(pairs, events_rejected, project) == 0


def test_candidate_idempotent_and_new_id_reemits(tmp_checkpoint_dir, monkeypatch):
    project = "/p/candidate-idem"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    pairs = [("r-old", "r-new", "old text")]

    events_same = {"r-old": {"status": "supersede-candidate:r-new", "source": "serializer"}}
    assert cli._emit_supersede_candidates(pairs, events_same, project) == 0

    events_diff = {"r-old": {"status": "supersede-candidate:r-other", "source": "serializer"}}
    assert cli._emit_supersede_candidates(pairs, events_diff, project) == 1


def test_stamp_before_bind_gives_fresh_native_a_real_new_id(tmp_checkpoint_dir):
    # Sequence pin: fresh native items only get ids inside write_checkpoint,
    # which runs AFTER the carry block — so the serialize wiring must stamp
    # ids itself before binding, or every fresh decision binds with
    # new_id="" and gate (c) collapses all such pairs into one.
    from daimon_briefing import carry, store

    prev = {
        "session_id": "S-prev",
        "working_context": {
            "open_questions": [],
            "recent_decisions": [
                {"text": "use gateway A for serialize", "id": "r-old001"},
            ],
        },
        "epistemic_snapshot": {"uncertainties": []},
    }
    cp = {
        "session_id": "S-new",
        "working_context": {
            "open_questions": [],
            "recent_decisions": [
                # FRESH native decision — no id yet, exactly as the serializer
                # emits it before write_checkpoint stamps.
                {"text": "use gateway B",
                 "links": [{"type": "supersedes",
                            "target": "gateway A serialize choice"}]},
            ],
        },
        "epistemic_snapshot": {"uncertainties": []},
    }
    store._stamp_item_ids(cp)
    pairs = carry.bind_links(cp, prev)
    assert len(pairs) == 1
    old_id, new_id, old_text = pairs[0]
    assert old_id == "r-old001"
    assert new_id  # non-empty — the stamped id, not ""
    assert new_id == cp["working_context"]["recent_decisions"][0]["id"]
    assert old_text == "use gateway A for serialize"


def test_candidate_skips_falsy_new_id(tmp_checkpoint_dir, monkeypatch):
    # Defense-in-depth: never write "supersede-candidate:" with no target.
    from daimon_briefing import store

    project = "/p/candidate-falsy"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    pairs = [("r-old", "", "old text")]
    assert cli._emit_supersede_candidates(pairs, {}, project) == 0
    slug = store.project_slug(project)
    assert not (tmp_checkpoint_dir / slug / "events.jsonl").exists()


def test_carry_still_carries_candidate_ref(tmp_checkpoint_dir, monkeypatch):
    # B1 pin: a supersede-candidate event must NOT resolve the item — the
    # serialize path's `resolved` set (resolutions + is_resolved) must not
    # contain it, or carry would silently drop the candidate item.
    from daimon_briefing import store

    project = "/p/candidate-carry-pin"
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    store.append_event("X", "supersede-candidate:r-new", source="serializer",
                       project_dir=project)

    events = store.resolutions(project_dir=project)
    resolved = frozenset(ref for ref, evt in events.items()
                         if store.is_resolved(evt))
    assert "X" not in resolved


# ---- #29: UX-contract batch — surface messages must match what the code does ----


def test_status_health_heal_hint_gated_on_healable():
    # status must only say "run 'daimon heal'" when heal can actually repair
    # something — a healable failure is present.
    h = cli._status_health(_proj(), {"exists": True},
                           [{"sid": "S", "class": "healable"}], [], now=1000.0)
    assert any("daimon heal" in w for w in h["warnings"])


def test_status_health_no_heal_hint_when_unrepairable():
    # Live contradiction (audit): status said "run 'daimon heal'"; heal
    # answered "nothing to heal". No healable failure -> no heal hint.
    h = cli._status_health(_proj(), {"exists": True},
                           [{"sid": "S", "class": "unrecoverable"},
                            {"sid": "T", "class": "hung"}], [], now=1000.0)
    assert any("failed to serialize" in w for w in h["warnings"])
    assert not any("daimon heal" in w for w in h["warnings"])


def test_brief_labels_global_fallback_for_other_project(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # brief --project X with no checkpoint for X silently rendered ANOTHER
    # project's briefing. status labels the same fallback; brief must too.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/some-other-project"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "fallback" in out.lower()


def test_brief_no_fallback_label_for_own_project(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    assert "fallback" not in capsys.readouterr().out.lower()


def test_brief_fallback_header_only_by_default(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #96: a fresh project's fallback briefing was 100% another project's
    # content under one warning line — two field reports read it as
    # contamination. Default is now an orientation header; the foreign body
    # renders only on explicit opt-in.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/some-other-project"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no briefing for this project yet" in out.lower()
    assert "-repo-x" in out  # says WHERE the recent activity actually lives
    # none of the foreign checkpoint's items may render
    assert "PR #6 state" not in out
    assert "D-007" not in out


def test_brief_fallback_full_via_flag(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/other", "--global-fallback"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #6 state" in out          # full foreign body on opt-in
    assert "fallback" in out.lower()     # #29 warning label retained


def test_brief_fallback_full_via_env(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_BRIEF_GLOBAL_FALLBACK", "full")
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/other"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #6 state" in out
    assert "fallback" in out.lower()


def test_brief_fallback_header_only_with_team_shows_teammates(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #223: the header-only fallback (#96) used to `return 0` before the
    # teammate fan-in ever ran — `brief --team` silently dropped the flag on
    # any machine with a global pointer but no own-project checkpoint, which
    # is exactly the new-teammate case where reading the team matters most.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")

    def _fake_read_team(project_dir=None):
        assert project_dir == "/repo/some-other-project"
        return [("grace", sample_checkpoint)]

    monkeypatch.setattr(store, "read_team", _fake_read_team)
    rc = cli.main(["brief", "--team", "--project", "/repo/some-other-project"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no briefing for this project yet" in out.lower()  # orient note kept
    assert "Teammates" in out
    assert "grace" in out
    assert "Wiring the on_session_end hook" in out  # teammate's active topic


def test_brief_fallback_header_only_without_team_flag_unchanged(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # Same setup as above, but no --team: output must stay byte-identical to
    # the pre-#223 behavior — no teammate content, no regression of #96.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")

    def _fake_read_team(project_dir=None):
        return [("grace", sample_checkpoint)]

    monkeypatch.setattr(store, "read_team", _fake_read_team)
    rc = cli.main(["brief", "--project", "/repo/some-other-project"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no briefing for this project yet" in out.lower()
    assert "Teammates" not in out
    assert "grace" not in out


def test_brief_fallback_header_only_with_team_empty_is_byte_identical(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # Empty team -> _print_teammates no-ops -> --team must not change a single
    # byte of the header-only fallback note for a team-less machine.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")

    rc = cli.main(["brief", "--project", "/repo/some-other-project"])
    plain = capsys.readouterr().out
    rc2 = cli.main(["brief", "--team", "--project", "/repo/some-other-project"])
    teamed = capsys.readouterr().out
    assert rc == 0 and rc2 == 0
    assert teamed == plain


def test_brief_withholds_resolved_item_and_notes_suppression(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #103: a resolved item must not print in the brief, and the withheld
    # count must be announced so the suppression is never silent.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    # write_checkpoint stamps a stable per-item id (#102) on every item, so a
    # real checkpoint's items are id-bearing by the time brief reads them —
    # resolve that exact id (the id-less fuzzy path is for legacy checkpoints
    # predating id-stamping, covered by the pure-function tests).
    written = store.read_latest(project_dir="/repo/x")
    item_id = written["working_context"]["open_questions"][1]["id"]
    store.append_event(item_id, "resolved", project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Chunk threshold for the serializer" not in out
    assert "1 resolved item(s) withheld" in out
    assert "--suppressed" in out


def test_brief_warns_on_stale_carried_item(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #215: a carried item whose effective last-verified age exceeds the
    # staleness budget gets a visible warning line — agreement between two
    # agent-written sources (the carried item + the fresh checkpoint restating
    # it) is not corroboration.
    from daimon_briefing import store
    import time as _time
    monkeypatch.setenv("DAIMON_STALE_DAYS", "7")
    stale_iso = _time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", _time.gmtime(_time.time() - 10 * 86400))
    cp = dict(sample_checkpoint)
    cp["working_context"] = dict(cp["working_context"])
    cp["working_context"]["open_questions"] = [{
        "text": "an old carried loop nobody rechecked",
        "trust": "inferred", "carried_from": "S-prev",
        "first_seen": stale_iso,
    }]
    store.write_checkpoint("S-mine", cp, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "carried item(s) unverified for >7 days" in out
    assert "world-check before repeating as true" in out


def test_brief_no_stale_note_when_nothing_stale(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # House rule: no line, no false alarms — a briefing with no stale carried
    # items must emit NOTHING about staleness.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "unverified for" not in out
    assert "world-check" not in out


def test_brief_fails_open_when_stale_carried_raises(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #215 fail-open: a broken stale_carried must never take the briefing
    # down with it — the brief still renders and exits clean, just without
    # the budget line.
    from daimon_briefing import briefing, store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(briefing, "stale_carried", _boom)
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "While you were away" in out
    assert "world-check" not in out


def test_brief_fails_open_when_resolutions_raises(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #103: withhold machinery must never take the briefing down with it —
    # a broken events.jsonl (or any resolutions() failure) still renders the
    # full, unfiltered brief and exits clean.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "resolutions", _boom)
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Chunk threshold for the serializer" in out


def test_status_suppressed_lists_withheld_item(tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #103: `daimon status --suppressed` answers the brief's "N resolved
    # item(s) withheld" note with the actual listing, reusing briefing.withhold
    # rather than reimplementing the classification.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    written = store.read_latest(project_dir="/repo/x")
    item = written["working_context"]["open_questions"][1]
    item_id = item["id"]
    store.append_event(item_id, "resolved", project_dir="/repo/x")
    rc = cli.main(["status", "--suppressed", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "suppressed items (1):" in out
    assert item_id in out
    assert "Chunk threshold for the serializer" in out
    assert "resolved" in out
    # the live item must never show up in the suppressed listing
    assert "PR #6 state" not in out


def test_status_suppressed_lists_withheld_strong_belief(tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #103 I2: `daimon resolve` accepts all five item kinds (store._ITEM_LISTS),
    # but withhold used to iterate only carry._CARRIED_KINDS (3 of 5) — a
    # resolved strong_beliefs id never suppressed. Cover the gap end-to-end.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    written = store.read_latest(project_dir="/repo/x")
    item = written["epistemic_snapshot"]["strong_beliefs"][0]
    item_id = item["id"]
    store.append_event(item_id, "resolved", project_dir="/repo/x")
    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Extractive pinning prevents silent fact loss" not in out
    assert "1 resolved item(s) withheld" in out
    rc = cli.main(["status", "--suppressed", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "suppressed items (1):" in out
    assert item_id in out


def test_status_suppressed_none_prints_message(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    rc = cli.main(["status", "--suppressed", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "no suppressed items"


# ---- #14: withhold's third outcome, end-to-end (brief annotation + status subsection) ----


def test_brief_shows_annotation_and_status_lists_subsection(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    written = store.read_latest(project_dir="/repo/x")
    item = written["working_context"]["recent_decisions"][0]
    item_id = item["id"]
    # new-id must be serializer-shaped (kind initial + hex slice) — withhold's
    # #14 shape gate refuses anything else, so the fixture uses a real shape.
    store.append_event(item_id, "supersede-candidate:r-9f3a2b", project_dir="/repo/x")

    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "likely superseded by r-9f3a2b" in out
    assert f"daimon resolve {item_id} --status superseded-by:r-9f3a2b" in out

    rc = cli.main(["status", "--suppressed", "--project", "/repo/x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "likely superseded (unconfirmed):" in out
    assert item_id in out
    assert "r-9f3a2b" in out
    # #111: the subsection prints both the confirm and the reject command,
    # so a human who disagrees with the guess has a printed path.
    assert f"daimon resolve {item_id} --status superseded-by:r-9f3a2b" in out
    assert f"daimon reverify {item_id}" in out


def test_transient_field_never_persisted(tmp_checkpoint_dir, sample_checkpoint, capsys):
    # The `_supersede_candidate` stamp lives ONLY on withhold's returned copy —
    # no writer ever persists it. Run a brief (which triggers withhold), then
    # re-read the checkpoint straight off disk and confirm it never appears.
    from daimon_briefing import store
    store.write_checkpoint("S-mine", sample_checkpoint, project_dir="/repo/x")
    written = store.read_latest(project_dir="/repo/x")
    item_id = written["working_context"]["recent_decisions"][0]["id"]
    store.append_event(item_id, "supersede-candidate:r-9f3a2b", project_dir="/repo/x")

    rc = cli.main(["brief", "--project", "/repo/x"])
    assert rc == 0
    capsys.readouterr()

    path = store.project_latest_path("/repo/x")
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "_supersede_candidate" not in json.dumps(on_disk)


def test_recall_rejects_nonpositive_limit(tmp_checkpoint_dir, capsys):
    # --limit 0 / -N used to clamp silently to 1 result. Reject loudly.
    rc = cli.main(["recall", "anything", "--limit", "0"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "limit" in err.lower()
    rc = cli.main(["recall", "anything", "--limit", "-3"])
    assert rc == 2


def test_configure_interactive_api_key_uses_getpass(monkeypatch, capsys):
    # The api_key prompt must not echo the secret to the terminal: it goes
    # through getpass, never through the plain input() _prompt.
    import getpass as getpass_mod
    from daimon_briefing import configure as configure_mod, render as render_mod

    monkeypatch.setattr(configure_mod, "status", lambda: {"ready": False})
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)

    asked = []
    answers = {"backend": "litellm", "base_url": "", "model": ""}

    def fake_prompt(q):
        asked.append(q)
        for key, val in answers.items():
            if key in q:
                return val
        return ""

    monkeypatch.setattr(cli, "_prompt", fake_prompt)
    monkeypatch.setattr(getpass_mod, "getpass", lambda prompt="": "sk-secret")

    written = {}

    def fake_write_env(updates):
        written.update(updates)
        return Path("/dev/null")

    monkeypatch.setattr(configure_mod, "write_env", fake_write_env)
    monkeypatch.setattr(render_mod, "render_configure", lambda st: None)

    rc = cli.main(["configure"])
    assert rc == 0
    assert written.get("DAIMON_LLM_API_KEY") == "sk-secret"
    assert not any("api_key" in q for q in asked)


def test_team_sync_project_flag_warns_ignored(monkeypatch, capsys):
    # `team sync --project` is accepted "for CLI symmetry" but does nothing —
    # a scoped sync must say it's ignored, not silently sync everything.
    from daimon_briefing import teamsync
    monkeypatch.setattr(teamsync, "git_available", lambda: False)
    rc = cli.main(["team", "sync", "--project", "/repo/x"])
    assert rc == 0
    assert "project" in capsys.readouterr().err.lower()


# ---- #28 S1: transcript path on spawn lines — hung sessions become healable ----


def test_session_ledger_reads_transcript_from_spawn_line():
    # A crashed/killed child never writes an error line, so the spawn line is
    # the only place the transcript path can survive. The ledger must pick it
    # up from there (#28).
    text = ("2026-06-10T12:00:00Z session-end: spawned serialize for A "
            "(reason: exit, project: /p/A) (transcript: /t/A.jsonl)")
    led = cli._session_ledger(text, now=0.0)
    assert led["A"]["transcript"] == "/t/A.jsonl"
    assert led["A"]["project"] == "/p/A"  # project parse unaffected by the suffix
    assert led["A"]["result_kind"] is None


def test_outstanding_hung_with_live_transcript_is_healable():
    # F1 (audit): kill -9 mid-serialize lost the checkpoint AND heal declared
    # it unrepairable even though the transcript was still on disk. With the
    # transcript recorded at spawn time, a hung session becomes healable.
    ledger = {"X": _led(result_kind=None, result_line=None, spawn_age=3600)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["kind"] == "hung"
    assert out[0]["class"] == "healable"
    assert out[0]["transcript"] == "/t/X.jsonl"


def test_outstanding_hung_without_transcript_stays_hung():
    ledger = {"X": _led(result_kind=None, result_line=None, spawn_age=3600,
                        transcript=None)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "hung"


def test_outstanding_hung_transcript_gone_stays_hung():
    ledger = {"X": _led(result_kind=None, result_line=None, spawn_age=3600)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: False)
    assert out[0]["class"] == "hung"


def test_outstanding_hung_already_retried_stays_hung():
    # One-retry-ever policy (#26) applies to hung heals too: a healed-hung
    # session that hangs again must not loop.
    ledger = {"X": _led(result_kind=None, result_line=None, spawn_age=3600,
                        retried=True)}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "hung"


def test_heal_plan_targets_hung_healable(tmp_checkpoint_dir, tmp_path):
    # End-to-end through the pure plan: a hung spawn whose transcript is still
    # on disk becomes the heal target instead of an unrepairable skip.
    from datetime import datetime, timezone
    transcript = tmp_path / "H.jsonl"
    transcript.write_text("{}\n")
    text = ("2026-06-10T12:00:00Z session-end: spawned serialize for H "
            f"(reason: exit, project: /p/H) (transcript: {transcript})")
    now = datetime(2026, 6, 10, 13, 0, 0, tzinfo=timezone.utc).timestamp()
    plan = cli._heal_plan(text, now)
    assert plan["target"] is not None
    assert plan["target"]["sid"] == "H"
    assert plan["target"]["transcript"] == str(transcript)


# ---- #28 S2: serialize-crash.log stops being a write-only dead-drop ----


def test_crash_log_info_missing_file_is_none(tmp_path):
    assert cli._crash_log_info(tmp_path / "nope.log", now=0.0) is None


def test_crash_log_info_empty_file_is_none(tmp_path):
    p = tmp_path / "serialize-crash.log"
    p.write_text("")
    assert cli._crash_log_info(p, now=0.0) is None


def test_crash_log_info_reports_last_line_and_header_age(tmp_path):
    # #194: age comes from the #92 header stamp, NOT file mtime — a later
    # stray write must not make an old crash look fresh (mtime deliberately
    # newer than the stamp here to prove the header wins).
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "--- crash 2026-07-09T00:00:00Z pid=123 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "OSError: [Errno 28] No space left on device\n"
    )
    import os
    os.utime(p, (9e9, 9e9))
    now = datetime(2026, 7, 9, 0, 5, 0, tzinfo=timezone.utc).timestamp()
    info = cli._crash_log_info(p, now=now)
    assert info["last_line"] == "OSError: [Errno 28] No space left on device"
    assert info["age_seconds"] == 300
    assert info["age"] == "5m"
    assert info["path"] == str(p)


def test_crash_log_info_warnings_only_is_none(tmp_path):
    # #194: pre-fix, lastResort dumped serializer WARNINGs into this file and
    # status misreported them as a crash. No `--- crash ` header -> no crash.
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "quote verification: downgraded verbatim->inferred: some item text\n"
        "quote verification: downgraded verbatim->inferred: another item\n"
    )
    assert cli._crash_log_info(p, now=0.0) is None


def test_crash_log_info_mixed_reports_crash_block(tmp_path):
    # Warnings before AND after the crash block: the reported line is still
    # the traceback's exception line, not a stray trailing warning.
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "quote verification: downgraded verbatim->inferred: pre-crash noise\n"
        "--- crash 2026-07-09T00:00:00Z pid=123 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "RuntimeError: child exploded\n"
        "quote verification: downgraded verbatim->inferred: post-crash noise\n"
    )
    info = cli._crash_log_info(p, now=0.0)
    assert info["last_line"] == "RuntimeError: child exploded"


def test_crash_log_info_reports_last_of_multiple_crashes(tmp_path):
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "--- crash 2026-07-08T00:00:00Z pid=1 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "OSError: old crash\n"
        "--- crash 2026-07-09T00:00:00Z pid=2 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "y.py", line 2, in <module>\n'
        "ValueError: new crash\n"
    )
    now = datetime(2026, 7, 9, 0, 5, 0, tzinfo=timezone.utc).timestamp()
    info = cli._crash_log_info(p, now=now)
    assert info["last_line"] == "ValueError: new crash"
    assert info["age_seconds"] == 300  # newest header's stamp, not the old one


def test_crash_log_info_bad_header_stamp_falls_back_to_mtime(tmp_path):
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "--- crash NOT-A-STAMP pid=1 cmd=serialize ---\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "OSError: boom\n"
    )
    import os
    os.utime(p, (1000.0, 1000.0))
    info = cli._crash_log_info(p, now=1300.0)
    assert info["last_line"] == "OSError: boom"
    assert info["age_seconds"] == 300


def test_status_json_includes_crash_info(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    crash = config.log_dir() / "serialize-crash.log"
    crash.parent.mkdir(parents=True, exist_ok=True)
    crash.write_text(
        "--- crash 2026-07-09T00:00:00Z pid=123 cmd=serialize ---\n"
        "RuntimeError: child exploded\n"
    )
    rc = cli.main(["status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["crash"]["last_line"] == "RuntimeError: child exploded"


def test_status_plain_shows_crash_line(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    crash = config.log_dir() / "serialize-crash.log"
    crash.parent.mkdir(parents=True, exist_ok=True)
    crash.write_text(
        "--- crash 2026-07-09T00:00:00Z pid=123 cmd=serialize ---\n"
        "RuntimeError: child exploded\n"
    )
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "last serialize crash" in out.lower()
    assert "RuntimeError: child exploded" in out


def test_status_plain_shows_recall_index_attribution(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #233: dark matter must be visible — a stampless legacy flat file indexes
    # with project_slug NULL and only status can tell the user it exists.
    from daimon_briefing import config, recall, store
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/p/A")
    (config.checkpoint_dir() / "S9.json").write_text(json.dumps({
        "session_id": "S9",
        "working_context": {
            "active_topic": {"text": "orphaned prior work", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }), encoding="utf-8")
    recall.rebuild()
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "recall index:" in out
    assert "unattributed — reachable only via recall --all-projects" in out


def test_status_plain_recall_index_clause_drops_when_fully_attributed(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # Silence stays the default: a fully-stamped store shows the count with
    # no dark-matter clause.
    from daimon_briefing import recall, store
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/p/A")
    recall.rebuild()
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "recall index:" in out
    assert "unattributed" not in out


def test_status_rich_shows_recall_index_attribution(
        tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # #29 mirror rule: the rich path must state the same fact as plain.
    pytest.importorskip("rich")
    from daimon_briefing import config, recall, render, store
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/p/A")
    (config.checkpoint_dir() / "S9.json").write_text(json.dumps({
        "session_id": "S9",
        "working_context": {
            "active_topic": {"text": "orphaned prior work", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }), encoding="utf-8")
    recall.rebuild()
    capsys.readouterr()
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "recall index:" in out
    assert "unattributed" in out


def test_status_plain_no_recall_index_line_without_db(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # No index on disk -> no line, and status must NOT build one as a side
    # effect (the helper is read-only by contract).
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "recall index:" not in out
    assert not config.recall_db().exists()


def test_status_plain_no_crash_line_for_warnings_only_log(
        tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #194: the live misreport — lastResort warnings in serialize-crash.log
    # made status render "last serialize crash" for a healthy pipeline.
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    crash = config.log_dir() / "serialize-crash.log"
    crash.parent.mkdir(parents=True, exist_ok=True)
    crash.write_text(
        "quote verification: downgraded verbatim->inferred: some item\n")
    rc = cli.main(["status"])
    assert rc == 0
    assert "last serialize crash" not in capsys.readouterr().out.lower()


def test_status_plain_no_crash_line_when_log_absent(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    rc = cli.main(["status"])
    assert rc == 0
    assert "last serialize crash" not in capsys.readouterr().out.lower()


# ---- #194: serializer diagnostics land in serialize.log, not the crash file ----


@pytest.fixture
def _detach_serialize_log_handler():
    # The #194 handler survives on the package logger across tests (module
    # state); detach so a later test's DAIMON_LOG_DIR repoint starts clean.
    yield
    pkg = logging.getLogger("daimon_briefing")
    for h in list(pkg.handlers):
        if getattr(h, "_daimon_serialize_log", None):
            pkg.removeHandler(h)
            h.close()


def test_serialize_routes_downgrade_warning_to_serialize_log(
        tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys,
        monkeypatch, _detach_serialize_log_handler):
    # A quote-verification downgrade must land in serialize.log UNTRUNCATED
    # (no %.80s cap — this line is the only surviving record of the item text).
    tail_marker = ("the item text runs well past eighty characters so the old "
                   "prefix cap would have cut it long before END-OF-ITEM")
    assert len(tail_marker) > 80
    payload = json.dumps({
        "session_id": "sample_transcript",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [
                {"text": tail_marker, "trust": "verbatim",
                 "quote": "THIS QUOTE APPEARS NOWHERE IN THE TRANSCRIPT"}
            ],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    })
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(payload))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    log = (tmp_log_dir / "serialize.log").read_text()
    assert "downgraded verbatim->inferred" in log
    assert "END-OF-ITEM" in log  # full text survived, cap dropped


def test_attach_serialize_log_handler_is_idempotent(
        tmp_log_dir, _detach_serialize_log_handler):
    # Repeat in-process serializes (tests, heal after serialize) must not
    # stack handlers — each record appears exactly once.
    cli._attach_serialize_log_handler()
    cli._attach_serialize_log_handler()
    pkg = logging.getLogger("daimon_briefing")
    ours = [h for h in pkg.handlers if getattr(h, "_daimon_serialize_log", None)]
    assert len(ours) == 1
    logging.getLogger("daimon_briefing.serializer").warning("once-sentinel")
    assert (tmp_log_dir / "serialize.log").read_text().count("once-sentinel") == 1


def test_attach_serialize_log_handler_follows_log_dir_repoint(
        tmp_path, monkeypatch, _detach_serialize_log_handler):
    # DAIMON_LOG_DIR changed between attaches (test isolation) -> the stale
    # handler is replaced, records land in the NEW dir only.
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("DAIMON_LOG_DIR", str(a))
    cli._attach_serialize_log_handler()
    monkeypatch.setenv("DAIMON_LOG_DIR", str(b))
    cli._attach_serialize_log_handler()
    pkg = logging.getLogger("daimon_briefing")
    ours = [h for h in pkg.handlers if getattr(h, "_daimon_serialize_log", None)]
    assert len(ours) == 1
    logging.getLogger("daimon_briefing.serializer").warning("repoint-sentinel")
    assert "repoint-sentinel" in (b / "serialize.log").read_text()
    assert not (a / "serialize.log").exists() or \
        "repoint-sentinel" not in (a / "serialize.log").read_text()


def test_serialize_log_handler_suppresses_lastresort_stderr(
        tmp_log_dir, capsys, _detach_serialize_log_handler):
    # The whole #194 chain starts with logging.lastResort dumping WARNINGs to
    # stderr (which spawn_serialize points at serialize-crash.log). A handler
    # anywhere on the logger chain suppresses lastResort — prove it with root
    # handlers cleared (pytest's own capture handler would mask the check).
    cli._attach_serialize_log_handler()
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers[:] = []
    try:
        logging.getLogger("daimon_briefing.serializer").warning("stderr-sentinel")
    finally:
        root.handlers[:] = saved
    assert "stderr-sentinel" not in capsys.readouterr().err
    assert "stderr-sentinel" in (tmp_log_dir / "serialize.log").read_text()


def test_serialize_survives_unwritable_log_dir(
        tmp_path, tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch,
        _detach_serialize_log_handler):
    # Fail open: a log dir that cannot exist (parent is a FILE) must not fail
    # the serialize itself.
    blocker = tmp_path / "blocked"
    blocker.write_text("")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(blocker / "logs"))
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json()))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    assert "wrote checkpoint" in capsys.readouterr().out


def test_attach_serialize_log_handler_fails_open_on_log_dir_error(
        monkeypatch, _detach_serialize_log_handler):
    # Fail open one seam earlier than the unwritable-dir case: config.log_dir()
    # itself raising (bad DAIMON_LOG_DIR expansion, broken config) must leave
    # the logger untouched instead of failing the serialize.
    def boom():
        raise RuntimeError("no log dir")
    monkeypatch.setattr(cli.config, "log_dir", boom)
    cli._attach_serialize_log_handler()
    pkg = logging.getLogger("daimon_briefing")
    assert not [h for h in pkg.handlers
                if getattr(h, "_daimon_serialize_log", None)]


def test_diagnostic_lines_are_invisible_to_ledger_parsers(tmp_log_dir):
    # The timestamped `<iso> LEVEL logger: message` shape must never match a
    # ledger regex — a diagnostic that parses as a result/spawn would corrupt
    # status/heal/stats. (Load-bearing contract, see ledger.py header.)
    diag = ("2026-07-09T12:00:00Z WARNING daimon_briefing.serializer: "
            "quote verification: downgraded verbatim->inferred: error: fake")
    assert not cli._SPAWN_RE.match(diag)
    assert not cli._RESULT_OK_RE.match(diag)
    assert not cli._RESULT_ERR_RE.match(diag)
    assert not cli._LEDGER_SKIP_RE.match(diag)
    lines = [
        "2026-07-09T11:59:00Z session-end: spawned serialize for S9 "
        "(reason: end, project: /p) (transcript: /t/S9.md)",
        diag,
        "wrote checkpoint: /tmp/S9.json (took 7s)",
    ]
    _write_log(tmp_log_dir, lines)
    parsed = cli._parse_serialize_log(tmp_log_dir / "serialize.log", now=0.0)
    assert parsed["spawn"]["session_id"] == "S9"
    assert parsed["result"]["outcome"] == "success"


# ---- #28 S3+S4: disabled banner + skipped-session visibility ----


def test_status_health_disabled_is_top_warning():
    # F4 (audit): DAIMON_DISABLE=1 silently stops capture; status never said so.
    h = cli._status_health(_proj(), {"exists": True, "same_session_as_project": True},
                           [], [], now=1000.0, disabled=True)
    assert h["ok"] is False
    assert "DAIMON_DISABLE" in h["verdict"]
    assert "capture is OFF" in h["verdict"]


def test_status_health_not_disabled_unchanged():
    h = cli._status_health(_proj(), {"exists": True, "same_session_as_project": True},
                           [], [], now=1000.0, disabled=False)
    assert h["ok"] is True


def test_status_shows_disabled_banner(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DAIMON_DISABLE" in out and "capture is OFF" in out


def test_status_json_reports_disabled(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["disabled"] is True


def test_status_counts_recent_skipped_sessions(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys, monkeypatch):
    # F5 (audit): a too-short session skips serialize by design, but status
    # implied the session was captured. Surface the count.
    from daimon_briefing import store
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir="/p/A")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_log(tmp_log_dir, [
        "2026-06-10T12:00:00Z session-end: spawned serialize for S-tiny (reason: exit, project: /p/A)",
        "skipped serialize for S-tiny: too short (3 < 10 messages)",
    ])
    rc = cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["skipped_recent"] == 1
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "skipped" in out and "too short" in out


def test_status_no_skip_line_when_none(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    cli.main(["status"])
    assert "skipped" not in capsys.readouterr().out


def test_status_surfaces_recall_error(tmp_checkpoint_dir, sample_checkpoint, capsys):
    # #28 S5: the recall breadcrumb must reach status, or it's a second
    # dead-drop.
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    p = config.log_dir() / "recall-error.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("2026-07-03T10:00:00Z search: OSError: disk full\n")
    rc = cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert "disk full" in data["recall_error"]["last_line"]
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "last recall error" in out


def test_serialize_result_line_marks_llm_fallback(
        tmp_checkpoint_dir, tmp_log_dir, fake_chat_factory, capsys, monkeypatch):
    # #28 S6 (F2): a gateway outage silently swapped in the weaker command
    # backend and stamped plain success. The result line — which status shows
    # verbatim — must carry the downgrade.
    from daimon_briefing import llm

    def chat_with_fallback(*a, **k):
        llm._fallback_used = True  # simulate llm.chat falling back mid-serialize
        return fake_chat_factory(_valid_json("S-fb"))(*a, **k)

    monkeypatch.setattr(cli, "_chat", chat_with_fallback)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[fallback backend]" in out
    log_text = (tmp_log_dir / "serialize.log").read_text(encoding="utf-8")
    assert "[fallback backend]" in log_text
    # the marked line still parses as a success for status/ledger
    led = cli._session_ledger(log_text, now=0.0)
    assert led["sample_transcript"]["result_kind"] == "success"


def test_serialize_result_line_clean_without_fallback(
        tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_chat", fake_chat_factory(_valid_json("S-nf")))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    rc = cli.main(["serialize", str(FIXTURES / "sample_transcript.md")])
    assert rc == 0
    assert "[fallback backend]" not in capsys.readouterr().out


def test_spawn_re_recognizes_windsurf_cascade_prefix():
    # cli.py hard rule: a new host adapter MUST add its prefix to _SPAWN_RE or
    # its serializes are invisible to status/hung detection/heal (#35).
    text = ("2026-07-03T23:00:00Z windsurf-cascade: spawned serialize for T-1 "
            "(project: /p/A) (transcript: /t/T-1.md)")
    led = cli._session_ledger(text, now=0.0)
    assert led["T-1"]["spawned"] is True
    assert led["T-1"]["transcript"] == "/t/T-1.md"
    assert led["T-1"]["project"] == "/p/A"


def test_spawn_re_recognizes_windsurf_finalizer_prefix():
    # #42: the debounced finalizer spawns serializes of its own. Same hard
    # rule as any host adapter — its prefix must be in _SPAWN_RE or those
    # spawns are invisible to status/hung detection/heal.
    text = ("2026-07-10T23:00:00Z windsurf-finalizer: spawned serialize for T-2 "
            "(project: /p/A) (transcript: /t/T-2.md)")
    led = cli._session_ledger(text, now=0.0)
    assert led["T-2"]["spawned"] is True
    assert led["T-2"]["transcript"] == "/t/T-2.md"
    assert led["T-2"]["project"] == "/p/A"


def test_stats_host_re_counts_windsurf_finalizer(tmp_log_dir):
    # #42: _STATS_HOST_RE is deliberately the same alternation as _SPAWN_RE —
    # the finalizer's fires must show up in per-host capture counts too.
    from daimon_briefing import ledger
    _write_log(tmp_log_dir, [
        "2026-07-10T11:00:00Z windsurf-finalizer: spawned serialize for W2 "
        "(project: /p/B) (transcript: /t/W2.md)",
    ])
    assert ledger._stats_capture()["hosts"]["windsurf-finalizer"] == 1


def test_stats_capture_too_short_error_lines_count_as_skipped(tmp_log_dir):
    # #235: pre-0.2.0 logs carry too-short outcomes in error shape (the write
    # side skips them since e2eb989). The fold reclassifies retroactively so
    # `errors` means "capture should have worked and didn't" — nothing else.
    from daimon_briefing import ledger
    _write_log(tmp_log_dir, [
        "error: transcript too short (2 < 10 messages) "
        "(transcript: /t/S1.jsonl) after 0s",
        "error: transcript too short (0 < 10 messages)",
        "error: LLM call failed on transcript: ChatError: unreachable "
        "(transcript: /t/S2.jsonl) after 12s",
        "skipped serialize for S3: transcript too short (4 < 10 messages)",
    ])
    cap = ledger._stats_capture()
    assert cap["errors"] == 1        # only the LLM failure
    assert cap["skipped"] == 3       # both fossils join the real skip line


# ---- #49: heal crash on hung targets + preflight-error attribution ----


def test_heal_hung_target_does_not_crash(tmp_checkpoint_dir, tmp_log_dir, tmp_path, monkeypatch):
    # Live crash (first Windsurf field run): a hung-healable target has
    # line=None and _cmd_heal's retry-marker builder split() it.
    from datetime import datetime, timezone
    transcript = tmp_path / "H1.md"
    transcript.write_text("**user**: hola\n")
    _write_log(tmp_log_dir, [
        "2026-07-03T22:00:00Z windsurf-cascade: spawned serialize for H1 "
        f"(project: /p/H) (transcript: {transcript})",
    ])
    ran = {}
    monkeypatch.setattr(cli, "_run_serialize", lambda p, proj: ran.update(p=p) or 0)
    monkeypatch.setattr(cli.time, "time",
                        lambda: datetime(2026, 7, 3, 23, 0, 0, tzinfo=timezone.utc).timestamp())
    rc = cli.main(["heal"])
    assert rc == 0
    assert ran.get("p") == transcript  # the hung session actually got healed
    # the retry marker landed with a non-crashy prior
    log_text = (tmp_log_dir / "serialize.log").read_text()
    assert "retry serialize for H1" in log_text


def test_preflight_error_line_attributes_to_session(tmp_checkpoint_dir):
    # A child that dies pre-flight (no API key) must not read as hung/killed:
    # the error line carries the transcript so the ledger attributes it.
    text = "\n".join([
        "2026-07-03T22:00:00Z windsurf-cascade: spawned serialize for P1 "
        "(project: /p/A) (transcript: /t/P1.md)",
        "error: no LLM API key — set DAIMON_LLM_API_KEY (env or ~/.daimon/env) "
        "(transcript: /t/P1.md)",
    ])
    led = cli._session_ledger(text, now=0.0)
    assert led["P1"]["result_kind"] == "error"
    assert led["P1"]["transcript"] == "/t/P1.md"


def test_preflight_error_session_is_healable_when_transcript_exists():
    ledger = {"P1": _led(result_line="error: no LLM API key — set DAIMON_LLM_API_KEY "
                                     "(env or ~/.daimon/env) (transcript: /t/P1.md)")}
    out = cli._outstanding_failures(ledger, 0.0, lambda sid: False, 1800, lambda p: True)
    assert out[0]["class"] == "healable"


def test_serialize_preflight_errors_carry_transcript_suffix(
        tmp_checkpoint_dir, tmp_log_dir, monkeypatch, tmp_path):
    # End-to-end: _run_serialize's own pre-flight error lines carry the suffix.
    # Backend pinned litellm — pre-flight is backend-aware since #52.
    monkeypatch.delenv("DAIMON_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setattr(cli.config, "llm_api_key", lambda: None)
    p = tmp_path / "S-pre.md"
    p.write_text("**user**: hola\n")
    rc = cli.main(["serialize", str(p)])
    assert rc == 1
    log_text = (tmp_log_dir / "serialize.log").read_text()
    assert f"(transcript: {p})" in log_text


# ---- #52: pre-flight must mirror llm.chat's backend routing ----


def _clear_llm_env_52(monkeypatch):
    for var in ("DAIMON_LLM_API_KEY", "LITELLM_API_KEY", "DAIMON_LLM_MODEL",
                "LITELLM_MODEL", "DAIMON_LLM_BACKEND", "DAIMON_LLM_COMMAND"):
        monkeypatch.delenv(var, raising=False)


def test_preflight_passes_for_command_backend_without_key(monkeypatch, tmp_path):
    # A `command` backend needs no API key and no model — pre-flight killed
    # it anyway (field-found: a command-backend user could never serialize).
    _clear_llm_env_52(monkeypatch)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "command")
    monkeypatch.setenv("DAIMON_LLM_COMMAND", "some-llm-cli")
    assert cli._preflight_error(Path("/t/x.md")) is None


def test_preflight_passes_for_claude_cli_backend_without_key(monkeypatch):
    _clear_llm_env_52(monkeypatch)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "claude-cli")
    assert cli._preflight_error(Path("/t/x.md")) is None


def test_preflight_passes_for_auto_when_command_resolves(monkeypatch):
    # The advertised zero-config path: auto backend, claude on PATH, no key.
    from daimon_briefing import llm
    _clear_llm_env_52(monkeypatch)
    monkeypatch.setattr(llm, "_resolve_command", lambda: ("claude -p", "text"))
    assert cli._preflight_error(Path("/t/x.md")) is None


def test_preflight_names_missing_key_when_litellm_bound(monkeypatch):
    from daimon_briefing import llm
    _clear_llm_env_52(monkeypatch)
    monkeypatch.setattr(llm, "_resolve_command", lambda: None)  # nothing on PATH
    err = cli._preflight_error(Path("/t/x.md"))
    assert err is not None and "DAIMON_LLM_API_KEY" in err
    assert "(transcript: /t/x.md)" in err  # #49 attribution survives


def test_preflight_names_missing_model_when_key_present(monkeypatch):
    _clear_llm_env_52(monkeypatch)
    monkeypatch.setenv("DAIMON_LLM_BACKEND", "litellm")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-x")
    err = cli._preflight_error(Path("/t/x.md"))
    assert err is not None and "DAIMON_LLM_MODEL" in err


# ---- #54: daimon stats — local usage + capture aggregates, zero phone-home ----


def test_brief_appends_usage_line(tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["brief"]) == 0
    usage = (tmp_log_dir / "usage.log").read_text()
    assert "brief" in usage


def test_usage_logging_respects_kill_switch(tmp_checkpoint_dir, tmp_log_dir,
                                            sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert cli.main(["brief"]) == 0
    assert not (tmp_log_dir / "usage.log").exists()


def test_brief_auto_logs_distinct_single_token(tmp_checkpoint_dir, tmp_log_dir,
                                               sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["brief", "--auto"]) == 0
    lines = (tmp_log_dir / "usage.log").read_text().splitlines()
    assert len(lines) == 1
    assert lines[0].split()[1] == "brief:auto"  # single token: len(parts)==2 filter survives


def test_brief_without_auto_logs_plain_token(tmp_checkpoint_dir, tmp_log_dir,
                                             sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["brief"]) == 0
    lines = (tmp_log_dir / "usage.log").read_text().splitlines()
    assert lines[0].split()[1] == "brief"


def test_stats_json_reports_usage_capture_and_store(
        tmp_checkpoint_dir, tmp_log_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint, project_dir="/p/A")
    _write_log(tmp_log_dir, [
        "2026-07-03T10:00:00Z session-end: spawned serialize for S1 "
        "(reason: exit, project: /p/A) (transcript: /t/S1.jsonl)",
        "wrote checkpoint: /c/S1.json (took 42s)",
        "2026-07-03T11:00:00Z windsurf-cascade: spawned serialize for W1 "
        "(project: /p/B) (transcript: /t/W1.md)",
        "skipped serialize for W1: too short (2 < 10 messages)",
        "error: boom (transcript: /t/X.jsonl) after 3s",
        "wrote checkpoint: /c/S2.json (took 8s) [fallback backend]",
    ])
    assert cli.main(["brief"]) == 0
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    rc = cli.main(["stats", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["usage"]["brief"] == 2
    cap = data["capture"]
    assert cap["success"] == 2
    assert cap["skipped"] == 1
    assert cap["errors"] == 1
    assert cap["fallback_serializes"] == 1
    assert cap["hosts"]["session-end"] == 1
    assert cap["hosts"]["windsurf-cascade"] == 1
    assert cap["max_serialize_seconds"] == 42
    st = data["store"]
    assert st["checkpoints"] >= 1
    assert st["items_verbatim"] >= 1   # sample_checkpoint carries verbatim items
    assert st["items_inferred"] >= 1


def test_stats_plain_renders_key_lines(tmp_checkpoint_dir, tmp_log_dir,
                                       sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["brief"]) == 0
    capsys.readouterr()
    rc = cli.main(["stats"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage" in out.lower()
    assert "brief" in out
    assert "verbatim" in out


def test_stats_empty_world_is_calm(tmp_checkpoint_dir, capsys):
    rc = cli.main(["stats", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["usage"] == {}
    assert data["capture"]["success"] == 0
    assert data["store"]["checkpoints"] == 0
    # events instrument reports zeroes when there is no log to fold
    assert data["events"] == {"lines": 0, "fold_ms": 0.0, "resolved_refs": 0}


def test_stats_events_reports_line_count_and_fold_time(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # measure-first instrument (#106): the events section counts every appended
    # line for the CURRENT project and times a full read+fold at stats time.
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    store.append_event("o-a", "resolved", project_dir="/p/A")
    store.append_event("o-a", "reopened", project_dir="/p/A")  # same ref, later line
    store.append_event("o-b", "resolved", project_dir="/p/A")
    rc = cli.main(["stats", "--json"])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out)["events"]
    assert ev["lines"] == 3          # every appended line, not folded refs
    assert ev["resolved_refs"] == 2  # o-a and o-b after fold
    assert isinstance(ev["fold_ms"], float)
    assert ev["fold_ms"] >= 0.0


def test_stats_events_scoped_to_current_project(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # the section reports ONLY the current project's log — another project's
    # events must not leak into the count.
    from daimon_briefing import store
    store.append_event("o-a", "resolved", project_dir="/p/A")
    store.append_event("o-b", "resolved", project_dir="/p/OTHER")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["stats", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["events"]["lines"] == 1


# ---- retention: hook briefings vs deliberate re-reads ----


def _usage_line(days_ago, cmd, now):
    stamp = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{stamp} {cmd}\n"


def test_retention_counts_hook_briefs_and_rereads_in_window(tmp_log_dir):
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(
        _usage_line(20, "brief", now)          # before first auto -> untagged
        + _usage_line(10, "brief:auto", now)   # first auto = upgrade marker
        + _usage_line(5, "brief:auto", now)
        + _usage_line(4, "brief", now)         # after marker -> deliberate re-read
        + _usage_line(3, "status", now)
        + _usage_line(2, "recall", now)
        + _usage_line(16, "status", now)       # outside the 14d window -> ignored
    )
    r = cli._stats_retention(now=now)
    assert r["window_days"] == 14
    assert r["hook_briefs"] == 2
    # status is ops polling, not a memory re-read (#232): tracked separately,
    # never in the total or the ratio.
    assert r["rereads"] == {"brief": 1, "recall": 1}
    assert r["status_checks"] == 1
    assert r["rereads_total"] == 2
    assert r["rereads_per_hook_brief"] == 1.0
    assert r["untagged_briefs"] == 1
    assert r["stale_hook_warning"] is False


def test_retention_zero_hook_briefs_ratio_is_none(tmp_log_dir):
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(_usage_line(1, "status", now))
    r = cli._stats_retention(now=now)
    assert r["hook_briefs"] == 0
    assert r["rereads_per_hook_brief"] is None


def test_retention_status_alone_never_moves_the_value_signal(tmp_log_dir):
    """A pure debugging session (#232): status polling plus hook briefings,
    zero memory reads. The ratio must read 0.0, not climb with ops noise."""
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(
        _usage_line(5, "brief:auto", now)
        + _usage_line(3, "status", now)
        + _usage_line(2, "status", now)
        + _usage_line(1, "status", now)
    )
    r = cli._stats_retention(now=now)
    assert r["status_checks"] == 3
    assert r["rereads_total"] == 0
    assert r["rereads_per_hook_brief"] == 0.0


def test_retention_all_briefs_untagged_when_no_auto_ever(tmp_log_dir):
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(
        _usage_line(3, "brief", now) + _usage_line(1, "brief", now))
    r = cli._stats_retention(now=now)
    assert r["untagged_briefs"] == 2
    assert r["rereads"]["brief"] == 0  # never guessed


def test_retention_stale_hook_warning_fires_on_spawns_without_auto(tmp_log_dir):
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    stamp = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        f"{stamp} session-end: spawned serialize for S9 (pid 1)\n")
    r = cli._stats_retention(now=now)
    assert r["stale_hook_warning"] is True


def test_retention_no_warning_without_spawns(tmp_log_dir):
    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    r = cli._stats_retention(now=now)
    assert r["stale_hook_warning"] is False


def test_spawns_in_window_skips_garbage_and_stale_spawns(tmp_log_dir):
    # Non-spawn lines are skipped, an unparseable stamp is not a crash, and a
    # spawn older than the window reports False rather than a stale True.
    from daimon_briefing import ledger

    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        "totally unrelated line\n"
        f"{old} session-end: spawned serialize for S-old (pid 1)\n"
    )
    assert ledger._spawns_in_window(now - timedelta(days=14)) is False
    assert ledger._parse_stamp("not-a-stamp") is None


def test_spawns_in_window_count_tallies_in_window_only(tmp_log_dir):
    # The FAIL-alarm's read side (#265): count every hook spawn inside the
    # window, skip stale ones and non-spawn noise. The boolean probe is just
    # count > 0, so both stay in lockstep on one regex.
    from daimon_briefing import ledger

    now = datetime(2026, 7, 6, tzinfo=timezone.utc)
    recent = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        "not a spawn line\n"
        f"{recent} session-end: spawned serialize for S1 (pid 1)\n"
        f"{recent} codex-stop: spawned serialize for S2 (pid 2)\n"
        f"{old} session-end: spawned serialize for S-old (pid 3)\n"
    )
    cutoff = now - timedelta(days=14)
    assert ledger._spawns_in_window_count(cutoff) == 2
    assert ledger._spawns_in_window(cutoff) is True


# ---- capture alarm: silent-capture FAIL tier (#265) ----


def _spawn_line(days_ago, sid, now_epoch):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch - days_ago * 86400))
    return f"{stamp} session-end: spawned serialize for {sid} (reason: exit, project: /p/A)\n"


def test_capture_alarm_fails_on_spawns_without_checkpoints(tmp_log_dir, tmp_checkpoint_dir):
    now = time.time()
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(2, "S1", now) + _spawn_line(3, "S2", now) + _spawn_line(4, "S3", now))
    alarm = cli._capture_alarm(now)
    assert alarm is not None
    assert alarm["verdict"] == "fail"
    assert alarm["spawns"] == 3
    assert alarm["checkpoints"] == 0
    assert alarm["window_days"] == 14


def test_capture_alarm_silent_below_min_sessions(tmp_log_dir, tmp_checkpoint_dir):
    # Two spawns, zero checkpoints — still below the guard, so NO verdict at all:
    # silence, never a false FAIL on a low-activity machine.
    now = time.time()
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(1, "S1", now) + _spawn_line(2, "S2", now))
    assert cli._capture_alarm(now) is None


def test_capture_alarm_silent_when_checkpoints_landing(
    tmp_log_dir, tmp_checkpoint_dir, sample_checkpoint
):
    from daimon_briefing import store

    now = time.time()
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(2, "S1", now) + _spawn_line(3, "S2", now) + _spawn_line(4, "S3", now))
    store.write_checkpoint("S1", {**sample_checkpoint, "session_id": "S1"})
    assert cli._capture_alarm(now) is None


def test_capture_alarm_stale_spawns_dont_count(tmp_log_dir, tmp_checkpoint_dir):
    # Three spawns but all older than the window → below the in-window guard → silence.
    now = time.time()
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(20, "S1", now) + _spawn_line(21, "S2", now) + _spawn_line(22, "S3", now))
    assert cli._capture_alarm(now) is None


def test_status_shows_capture_fail_at_top(tmp_log_dir, tmp_checkpoint_dir, capsys, monkeypatch):
    now = time.time()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(1, "S1", now) + _spawn_line(2, "S2", now)
        + _spawn_line(3, "S3", now) + _spawn_line(4, "S4", now))
    cli.main(["status"])
    out = capsys.readouterr().out
    first = out.strip().splitlines()[0]
    assert "FAIL" in first and "silent capture" in first.lower()
    # Every fix hint the operator needs, unmissable near the top.
    assert "daimon heal" in out
    assert "serialize.log" in out
    assert "daimon configure --test" in out


def test_status_json_includes_capture_alarm(tmp_log_dir, tmp_checkpoint_dir, capsys, monkeypatch):
    now = time.time()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(1, "S1", now) + _spawn_line(2, "S2", now) + _spawn_line(3, "S3", now))
    cli.main(["status", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["capture_alarm"]["verdict"] == "fail"
    assert data["capture_alarm"]["spawns"] == 3


def test_status_no_capture_alarm_when_healthy(
    tmp_log_dir, tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch
):
    from daimon_briefing import store

    now = time.time()
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        _spawn_line(1, "S1", now) + _spawn_line(2, "S2", now) + _spawn_line(3, "S3", now))
    store.write_checkpoint("S1", {**sample_checkpoint, "session_id": "S1"}, project_dir="/p/A")
    cli.main(["status"])
    assert "silent capture" not in capsys.readouterr().out.lower()
    cli.main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["capture_alarm"] is None


def test_stats_json_includes_retention(tmp_checkpoint_dir, tmp_log_dir,
                                       sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    assert cli.main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "retention" in data
    assert set(data["retention"]) >= {"window_days", "hook_briefs", "rereads",
                                      "status_checks", "rereads_total",
                                      "rereads_per_hook_brief",
                                      "untagged_briefs", "stale_hook_warning"}


def test_stats_plain_renders_retention_section(tmp_checkpoint_dir, tmp_log_dir,
                                               sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    store.write_checkpoint("S1", sample_checkpoint)
    now = datetime.now(timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(_usage_line(1, "brief:auto", now)
                                           + _usage_line(0, "status", now))
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "retention (last 14d):" in out
    assert "hook briefings: 1" in out
    # the lone status poll is ops, not retention (#232)
    assert "status checks: 1  (ops, not counted)" in out
    assert "re-reads per hook briefing: 0.0" in out


def test_stats_rich_renders_retention_section(tmp_checkpoint_dir, tmp_log_dir,
                                              sample_checkpoint, capsys, monkeypatch):
    pytest.importorskip("rich")
    from daimon_briefing import render, store
    monkeypatch.setattr(render, "supports_rich", lambda: True)
    store.write_checkpoint("S1", sample_checkpoint)
    now = datetime.now(timezone.utc)
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "usage.log").write_text(_usage_line(1, "brief:auto", now)
                                           + _usage_line(0, "status", now))
    assert cli.main(["stats"]) == 0
    out = capsys.readouterr().out
    assert "retention (last 14d)" in out
    assert "hook briefings" in out
    assert "status checks (ops, not counted)" in out
    assert "0.0" in out  # ratio: the status poll counts apart (#232)


def test_stats_plain_warns_on_stale_hook(tmp_checkpoint_dir, tmp_log_dir,
                                         sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PLAIN", "1")
    store.write_checkpoint("S1", sample_checkpoint)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tmp_log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_log_dir / "serialize.log").write_text(
        f"{stamp} session-end: spawned serialize for S9 (pid 1)\n")
    assert cli.main(["stats"]) == 0
    assert "re-run `daimon hooks install`" in capsys.readouterr().out


# ---- crash stamping (#92): timestamp header before uncaught tracebacks ------


def test_crash_excepthook_stamps_before_traceback(capsys, monkeypatch):
    # serialize-crash.log is the detached child's raw stderr — the only place
    # a timestamp can come from is the crashing process itself. The hook must
    # print one ISO-stamped header line, then the normal traceback.
    monkeypatch.setattr(sys, "argv", ["daimon", "serialize", "/tmp/t.md"])
    try:
        raise RuntimeError("boom for the stamp test")
    except RuntimeError:
        exc_type, exc, tb = sys.exc_info()
    cli._crash_stamp_excepthook(exc_type, exc, tb)
    err = capsys.readouterr().err
    first = err.splitlines()[0]
    assert re.fullmatch(
        r"--- crash \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z pid=\d+ "
        r"cmd=serialize ---", first)
    assert "RuntimeError: boom for the stamp test" in err


def test_main_installs_crash_excepthook(monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert sys.excepthook is cli._crash_stamp_excepthook


# ---- #102: daimon resolve / daimon log ----


def _write_cp_with_ids(store, project="/p/A"):
    cp = {"working_context": {"open_questions": [
        {"text": "release pipeline awaiting manual approval step", "trust": "inferred"},
        {"text": "serializer chunk retry budget unclear", "trust": "inferred"},
    ]}}
    store.write_checkpoint("S1", cp, project_dir=project)
    return cp


def test_resolve_by_exact_id_appends_event(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    assert cli.main(["resolve", iid]) == 0
    r = store.resolutions(project_dir="/p/A")
    assert store.is_resolved(r[iid])
    assert r[iid]["item_text"] == "release pipeline awaiting manual approval step"


def test_resolve_by_unique_fuzzy_query(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    assert cli.main(["resolve", "release pipeline manual approval",
                     "--status", "resolved", "--note", "approved and shipped"]) == 0
    r = store.resolutions(project_dir="/p/A")
    assert r[iid]["note"] == "approved and shipped"


def test_resolve_ambiguous_refuses_and_lists_candidates(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = {"working_context": {"open_questions": [
        {"text": "gateway retry budget for serializer chunks"},
        {"text": "serializer chunk retry budget unclear"},
    ]}}
    store.write_checkpoint("S1", cp, project_dir="/p/A")
    assert cli.main(["resolve", "serializer chunk retry budget"]) == 1
    out = capsys.readouterr().out
    for item in cp["working_context"]["open_questions"]:
        assert item["id"] in out  # both candidates listed with ids
    assert store.resolutions(project_dir="/p/A") == {}  # nothing appended


def test_resolve_no_match_exits_1(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_cp_with_ids(store)
    assert cli.main(["resolve", "completely unrelated nonsense query"]) == 1
    assert store.resolutions(project_dir="/p/A") == {}


def test_log_appends_freeform_event(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_cp_with_ids(store)
    assert cli.main(["log", "--text", "midnight deploy went clean"]) == 0
    slug = store.project_slug("/p/A")
    line = json.loads((tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()[0])
    assert line["kind"] == "note"
    assert line["note"] == "midnight deploy went clean"
    assert line["item_ref"] == ""


# ---- #103: daimon reverify — evidence-gated reopen ----


def test_reverify_not_found_exits_1(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_cp_with_ids(store)
    assert cli.main(["reverify", "no-such-id"]) == 1
    assert store.resolutions(project_dir="/p/A") == {}


def test_reverify_refuses_without_evidence_when_no_anchor(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    assert cli.main(["reverify", iid]) == 1
    out = capsys.readouterr().out
    assert "without evidence" in out
    r = store.resolutions(project_dir="/p/A")
    assert store.is_resolved(r[iid])  # unchanged — still resolved, nothing appended


def test_reverify_with_evidence_reopens(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    assert cli.main(["reverify", iid, "--evidence", "checked release page"]) == 0
    r = store.resolutions(project_dir="/p/A")
    assert not store.is_resolved(r[iid])
    assert "checked release page" in r[iid]["note"]


def test_reverify_anchor_live_reopens_without_evidence(tmp_checkpoint_dir, tmp_path, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = {"working_context": {"open_questions": [
        {"text": "anchored claim about foo()", "trust": "inferred",
         "anchored_to": {"file": "foo.py", "symbol": "foo", "body_hash": "deadbeef"}},
    ]}}
    store.write_checkpoint("S1", cp, project_dir="/p/A")
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    monkeypatch.setattr(cli.anchor, "check", lambda a, p: "live")
    assert cli.main(["reverify", iid]) == 0
    r = store.resolutions(project_dir="/p/A")
    assert not store.is_resolved(r[iid])
    assert "anchor live" in r[iid]["note"]


def test_reverify_anchor_live_and_evidence_combined_note(tmp_checkpoint_dir, capsys, monkeypatch):
    # M4: when the anchor is live AND --evidence is supplied, the note
    # concatenates both — "reverified: anchor live; <evidence>".
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = {"working_context": {"open_questions": [
        {"text": "anchored claim about foo()", "trust": "inferred",
         "anchored_to": {"file": "foo.py", "symbol": "foo", "body_hash": "deadbeef"}},
    ]}}
    store.write_checkpoint("S1", cp, project_dir="/p/A")
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    monkeypatch.setattr(cli.anchor, "check", lambda a, p: "live")
    assert cli.main(["reverify", iid, "--evidence", "saw it work"]) == 0
    r = store.resolutions(project_dir="/p/A")
    assert not store.is_resolved(r[iid])
    assert r[iid]["note"] == "reverified: anchor live; saw it work"


def test_reverify_anchor_drifted_still_refused_without_evidence(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = {"working_context": {"open_questions": [
        {"text": "anchored claim about bar()", "trust": "inferred",
         "anchored_to": {"file": "bar.py", "symbol": "bar", "body_hash": "deadbeef"}},
    ]}}
    store.write_checkpoint("S1", cp, project_dir="/p/A")
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    monkeypatch.setattr(cli.anchor, "check", lambda a, p: "hard")
    assert cli.main(["reverify", iid]) == 1
    r = store.resolutions(project_dir="/p/A")
    assert store.is_resolved(r[iid])  # unchanged — still resolved, nothing appended


# ---- #111: candidate reject — evidence-free, silences re-detection ----


def test_reverify_rejects_candidate_without_evidence(tmp_checkpoint_dir, capsys, monkeypatch):
    # A supersede-candidate is an unconfirmed machine SUGGESTION, never a
    # withheld item — rejecting a guess needs no proof. reverify with no
    # --evidence must succeed and write a reopened (human) event.
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "supersede-candidate:o-9f3a2b",
                       source="serializer", project_dir="/p/A")
    assert cli.main(["reverify", iid]) == 0
    r = store.resolutions(project_dir="/p/A")
    # latest event is now a human reopen — the item stays live (not resolved)
    # and is no longer a candidate.
    assert not store.is_resolved(r[iid])
    assert str(r[iid].get("source")) != "serializer"


def test_reverify_candidate_reject_silences_re_detection(tmp_checkpoint_dir, capsys, monkeypatch):
    # After an evidence-free reject, the human-speaks-once gate (#14) keeps
    # the machine silent forever — re-serialize offers nothing.
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "supersede-candidate:o-9f3a2b",
                       source="serializer", project_dir="/p/A")
    assert cli.main(["reverify", iid]) == 0
    events = store.resolutions(project_dir="/p/A")
    pairs = [(iid, "o-9f3a2b", "release pipeline awaiting manual approval step")]
    assert cli._emit_supersede_candidates(pairs, events, "/p/A") == 0


def test_reverify_still_refuses_resolved_item_without_evidence(tmp_checkpoint_dir, capsys, monkeypatch):
    # Guard: the evidence-free path is candidate-only. A genuinely RESOLVED
    # item (not a machine candidate) still demands evidence — #103 semantics
    # are untouched.
    from daimon_briefing import store
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    cp = _write_cp_with_ids(store)
    iid = cp["working_context"]["open_questions"][0]["id"]
    store.append_event(iid, "resolved", project_dir="/p/A")
    assert cli.main(["reverify", iid]) == 1
    out = capsys.readouterr().out
    assert "without evidence" in out


# ---- daimon projects: cross-project bucket list (#243) ----


def _write_bucket(d, slug, session_id, created, branch=None, topic=None):
    bucket = d / slug
    bucket.mkdir(parents=True, exist_ok=True)
    cp = {"session_id": session_id, "project_slug": slug, "created": created}
    if branch:
        cp["git_branch"] = branch
    if topic:
        cp["working_context"] = {"active_topic": {"text": topic, "trust": "inferred"}}
    (bucket / "latest.json").write_text(json.dumps(cp))


def test_projects_lists_buckets_newest_first(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_bucket(tmp_checkpoint_dir, "-p-old", "S-old", "2026-07-01T00:00:00Z",
                  branch="main", topic="old work")
    _write_bucket(tmp_checkpoint_dir, "-p-new", "S-new", "2026-07-11T00:00:00Z",
                  branch="feat-x", topic="new work")
    assert cli.main(["projects"]) == 0
    out = capsys.readouterr().out
    assert out.index("-p-new") < out.index("-p-old")
    assert "feat-x" in out
    assert "new work" in out


def test_projects_marks_current_project(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    from daimon_briefing import store
    slug = store.project_slug("/p/A")
    _write_bucket(tmp_checkpoint_dir, slug, "S-me", "2026-07-11T00:00:00Z")
    _write_bucket(tmp_checkpoint_dir, "-p-other", "S-o", "2026-07-10T00:00:00Z")
    assert cli.main(["projects"]) == 0
    out = capsys.readouterr().out
    me = next(ln for ln in out.splitlines() if slug in ln)
    other = next(ln for ln in out.splitlines() if "-p-other" in ln)
    assert "*" in me and "*" not in other


def test_projects_json_shape(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    _write_bucket(tmp_checkpoint_dir, "-p-b", "S-1", "2026-07-11T00:00:00Z",
                  branch="main", topic="topic text")
    assert cli.main(["projects", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows == [{
        "slug": "-p-b", "session_id": "S-1", "created": "2026-07-11T00:00:00Z",
        "git_branch": "main", "topic": "topic text", "current": False,
    }]


def test_projects_torn_bucket_listed_with_unknowns(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    torn = tmp_checkpoint_dir / "-p-torn"
    torn.mkdir(parents=True)
    (torn / "latest.json").write_text("{not json")
    assert cli.main(["projects"]) == 0
    assert "-p-torn" in capsys.readouterr().out


def test_projects_empty_store_says_so(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    assert cli.main(["projects"]) == 0
    assert "no project" in capsys.readouterr().out.lower()


# ---- recall --slug: bucket-identity scoping (#243) ----


def test_cli_recall_slug_scopes_by_bucket_identity(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj_a = str((tmp_path / "proj-a").resolve())
    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-a", _recall_checkpoint("S-a", "marmot work in a"),
                           project_dir=proj_a)
    store.write_checkpoint("S-b", _recall_checkpoint("S-b", "marmot work in b"),
                           project_dir=proj_b)

    # run from proj_a's scope, target proj_b by slug — no path involved
    monkeypatch.setenv("DAIMON_PROJECT_DIR", proj_a)
    rc = cli.main(["recall", "marmot", "--slug", store.project_slug(proj_b)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "marmot work in b" in out
    assert "marmot work in a" not in out


def test_cli_recall_slug_conflicts_with_project(tmp_checkpoint_dir, capsys, tmp_path):
    rc = cli.main(["recall", "x", "--slug", "-p-b", "--project", str(tmp_path)])
    assert rc == 2
    assert "--slug" in capsys.readouterr().err


def test_cli_recall_slug_conflicts_with_all_projects(tmp_checkpoint_dir, capsys):
    rc = cli.main(["recall", "x", "--slug", "-p-b", "--all-projects"])
    assert rc == 2
    assert "--slug" in capsys.readouterr().err


# ---- brief --slug: deliberate cross-project briefing (#243) ----


def test_cli_brief_slug_renders_target_bucket(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store

    other = json.loads(json.dumps(sample_checkpoint))
    other["session_id"] = "S-other"
    other["working_context"]["open_questions"][0]["text"] = "PR #99 state — project B loop"
    store.write_checkpoint("S-other", other, project_dir="/p/B")

    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    slug = store.project_slug("/p/B")
    rc = cli.main(["brief", "--slug", slug])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #99" in out
    # provenance header: a cross-project briefing must name its origin
    assert slug in out


def test_cli_brief_slug_missing_bucket_never_falls_back(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import store

    # a global pointer exists — an implicit fallback would leak it
    store.write_checkpoint("S-global", sample_checkpoint)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["brief", "--slug", "-p-nonexistent"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "daimon projects" in out
    assert "PR #6" not in out  # sample_checkpoint body must not render


def test_cli_brief_slug_conflicts_with_project(tmp_checkpoint_dir, capsys, tmp_path):
    rc = cli.main(["brief", "--slug", "-p-b", "--project", str(tmp_path)])
    assert rc == 2
    assert "--slug" in capsys.readouterr().err


def test_cli_brief_slug_conflicts_with_team(tmp_checkpoint_dir, capsys):
    rc = cli.main(["brief", "--slug", "-p-b", "--team"])
    assert rc == 2
    assert "--slug" in capsys.readouterr().err


def test_cli_brief_slug_withholds_resolved_items(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    # the events ledger is slug-routed, so lifecycle folding must survive the
    # path-less read
    from daimon_briefing import store

    cp = json.loads(json.dumps(sample_checkpoint))
    cp["session_id"] = "S-b"
    store.write_checkpoint("S-b", cp, project_dir="/p/B")
    written = store.read_latest(project_dir="/p/B", fallback=False)
    iid = written["working_context"]["open_questions"][0]["id"]
    q_text = written["working_context"]["open_questions"][0]["text"]
    store.append_event(iid, "resolved", project_dir="/p/B")

    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    rc = cli.main(["brief", "--slug", store.project_slug("/p/B")])
    assert rc == 0
    out = capsys.readouterr().out
    assert q_text not in out
    assert "withheld" in out


def test_projects_truncates_long_topic(tmp_checkpoint_dir, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    long_topic = "x" * 200
    _write_bucket(tmp_checkpoint_dir, "-p-long", "S-1", "2026-07-11T00:00:00Z",
                  topic=long_topic)
    assert cli.main(["projects"]) == 0
    out = capsys.readouterr().out
    assert "…" in out
    assert long_topic not in out


def test_main_defaults_to_sys_argv(tmp_checkpoint_dir, capsys, monkeypatch):
    # the --slug pre-parse fuse reads sys.argv when main() gets no argv —
    # the installed console_script path
    monkeypatch.setattr(sys, "argv", ["daimon", "projects"])
    assert cli.main() == 0
    assert "no project" in capsys.readouterr().out.lower()


# ---- #246: eager index warm at write time ----


def _count_warm(monkeypatch):
    from daimon_briefing import recall
    calls = []
    monkeypatch.setattr(recall, "warm", lambda: calls.append(1))
    return calls


def test_serialize_warms_index_after_write(tmp_checkpoint_dir, fake_chat_factory, capsys, monkeypatch):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    calls = _count_warm(monkeypatch)
    assert cli.main(["serialize", str(FIXTURES / "sample_transcript.md")]) == 0
    assert calls == [1]
    # the byte-identical result contract is untouched
    assert "wrote checkpoint:" in capsys.readouterr().out


def test_write_checkpoint_cmd_warms_index(tmp_checkpoint_dir, capsys, monkeypatch):
    calls = _count_warm(monkeypatch)
    _stdin(monkeypatch, _valid_json("S-wc"))
    assert cli.main(["write-checkpoint", "--project", "/p/A"]) == 0
    assert calls == [1]


def test_anchor_attach_warms_index(tmp_checkpoint_dir, tmp_path, capsys, monkeypatch):
    from daimon_briefing import store
    src = tmp_path / "mod.py"
    src.write_text("def fn():\n    return 1\n")
    cp = {"session_id": "S-anchor", "working_context": {"open_questions": [
        {"text": "anchor target item", "trust": "inferred"}]}}
    store.write_checkpoint("S-anchor", cp, project_dir=str(tmp_path))
    calls = _count_warm(monkeypatch)
    rc = cli.main(["anchor", "mod.py", "fn", "--project", str(tmp_path),
                   "--attach", "anchor target"])
    assert rc == 0
    assert calls == [1]


def test_team_sync_warms_index(tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import teamsync
    monkeypatch.setattr(teamsync, "sync", lambda: [])
    calls = _count_warm(monkeypatch)
    assert cli.main(["team", "sync"]) == 0
    assert calls == [1]


# ---- #259: zero-match scoped recall reports WHERE matches exist (counts only) ----


def test_recall_zero_match_teases_other_projects(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj_a = str((tmp_path / "proj-a").resolve())
    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-b1", _recall_checkpoint("S-b1", "homeauto wiring notes"),
                           project_dir=proj_b)

    rc = cli.main(["recall", "homeauto", "--project", proj_a])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no matches in this project" in out
    assert store.project_slug(proj_b) in out
    assert "(1)" in out
    assert "--all-projects" in out
    # counts only — the foreign item's content must NOT appear
    assert "homeauto wiring notes" not in out


def test_recall_zero_match_everywhere_stays_plain_no_matches(tmp_checkpoint_dir, capsys, tmp_path):
    proj = str((tmp_path / "proj").resolve())
    rc = cli.main(["recall", "nonexistentword", "--project", proj])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip() == "no matches"


def test_recall_zero_match_json_contract_untouched(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj_a = str((tmp_path / "proj-a").resolve())
    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-b2", _recall_checkpoint("S-b2", "homeauto wiring notes"),
                           project_dir=proj_b)
    rc = cli.main(["recall", "homeauto", "--project", proj_a, "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_recall_zero_match_no_tease_under_explicit_scope(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    # --all-projects already searched everything; --slug was an explicit
    # target — neither gets a second-guess teaser
    from daimon_briefing import store

    proj_b = str((tmp_path / "proj-b").resolve())
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint("S-b3", _recall_checkpoint("S-b3", "homeauto wiring notes"),
                           project_dir=proj_b)
    rc = cli.main(["recall", "zebrafish", "--all-projects"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "no matches"
    rc = cli.main(["recall", "homeauto", "--slug", "-p-empty"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "no matches"


def test_recall_zero_match_teaser_fails_open_on_recall_error(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    # the widening probe is best-effort: if the unscoped rerun dies (e.g.
    # FTS5 vanished between calls), the plain no-matches line still prints
    from daimon_briefing import recall as recall_mod

    real_search = recall_mod.search

    def flaky(query, project_dir=None, all_projects=False, limit=20, slug=None):
        if all_projects:
            raise recall_mod.RecallError("no FTS5")
        return real_search(query, project_dir=project_dir,
                           all_projects=all_projects, limit=limit, slug=slug)

    monkeypatch.setattr(cli.recall, "search", flaky)
    proj = str((tmp_path / "proj").resolve())
    rc = cli.main(["recall", "nonexistentword", "--project", proj])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "no matches"
