import json
import os
import re
import sys
import time
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


def test_cli_brief_prints_briefing(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store

    store.write_checkpoint("S-prev", sample_checkpoint)
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
    from daimon_briefing import store

    store.write_checkpoint("S-global", sample_checkpoint)
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/never-seen")
    rc = cli.main(["brief"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #6" in out


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
                         "skipped_recent", "recall_error"}
    assert data["team"] is None  # no team remote configured -> explicit null (#113)
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


# ---- #48: `configure` — detect/report backend + fill gaps in ~/.daimon/env ----


_CFG_LLM_VARS = (
    "DAIMON_LLM_BACKEND",
    "DAIMON_LLM_API_KEY", "LITELLM_API_KEY",
    "DAIMON_LLM_MODEL", "LITELLM_MODEL",
    "DAIMON_LLM_BASE_URL", "LITELLM_BASE_URL",
    "DAIMON_LLM_COMMAND", "DAIMON_LLM_COMMAND_OUTPUT",
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
    rc = cli._cmd_status(A())
    out = json.loads(capsys.readouterr().out)
    assert "health" in out and "siblings" in out
    assert out["project"]["slug"] == slug


# ---- #86: _heal_plan — pure decision function ----


def test_heal_plan_targets_newest_healable(monkeypatch):
    from daimon_briefing import cli
    # Build outstanding via a stubbed _compute_outstanding so the plan logic is unit-tested in isolation.
    items = [
        {"sid": "S-new", "class": "healable", "transcript": "/t/new.jsonl", "project": "/p",
         "age_str": "1m", "line": "error: boom (transcript: /t/new.jsonl) after 3s"},
        {"sid": "S-old", "class": "retry-exhausted", "transcript": "/t/old.jsonl", "project": "/p",
         "age_str": "9m", "line": "error: boom (transcript: /t/old.jsonl) after 3s"},
    ]
    monkeypatch.setattr(cli, "_compute_outstanding", lambda text, now: items)
    plan = cli._heal_plan("logtext", 1000.0)
    assert plan["target"]["sid"] == "S-new"
    assert plan["target"]["transcript"] == "/t/new.jsonl"
    assert plan["note"] == ""
    assert len(plan["skipped"]) == 1
    assert plan["skipped"][0]["sid"] == "S-old"
    assert "retry already attempted" in plan["skipped"][0]["reason"]


def test_heal_plan_second_healable_says_rerun(monkeypatch):
    from daimon_briefing import cli
    items = [
        {"sid": "S1", "class": "healable", "transcript": "/t/1", "project": "/p", "age_str": "1m",
         "line": "error: x (transcript: /t/1) after 1s"},
        {"sid": "S2", "class": "healable", "transcript": "/t/2", "project": "/p", "age_str": "2m",
         "line": "error: x (transcript: /t/2) after 1s"},
    ]
    monkeypatch.setattr(cli, "_compute_outstanding", lambda text, now: items)
    plan = cli._heal_plan("x", 1000.0)
    assert plan["target"]["sid"] == "S1"
    assert plan["skipped"][0]["sid"] == "S2"
    assert "re-run" in plan["skipped"][0]["reason"]


def test_heal_plan_no_log(monkeypatch):
    from daimon_briefing import cli
    monkeypatch.setattr(cli, "_compute_outstanding", lambda text, now: [])
    plan = cli._heal_plan("", 1000.0)
    assert plan["target"] is None and plan["skipped"] == []
    assert "no serialize activity logged" in plan["note"]


def test_heal_plan_no_outstanding(monkeypatch):
    from daimon_briefing import cli
    monkeypatch.setattr(cli, "_compute_outstanding", lambda text, now: [])
    plan = cli._heal_plan("some log with only successes", 1000.0)
    assert plan["target"] is None
    assert "no outstanding failures" in plan["note"]


def test_heal_plan_only_unrepairable(monkeypatch):
    from daimon_briefing import cli
    items = [{"sid": "H1", "class": "hung", "transcript": None, "project": None,
              "age_str": "40m", "line": None}]
    monkeypatch.setattr(cli, "_compute_outstanding", lambda text, now: items)
    plan = cli._heal_plan("x", 1000.0)
    assert plan["target"] is None
    assert "can't be auto-repaired" in plan["note"]
    assert plan["skipped"][0]["sid"] == "H1" and "hung" in plan["skipped"][0]["reason"]


# ---- #86: _cmd_heal — render always, gate the serialize, --dry-run ----


def test_cmd_heal_dry_run_does_not_serialize(monkeypatch, capsys):
    from daimon_briefing import cli
    plan = {"target": {"sid": "S-A", "transcript": "/t/a.jsonl", "project": "/p",
                       "age_str": "3m", "line": "error: x (transcript: /t/a.jsonl) after 1s"},
            "skipped": [], "note": ""}
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now: plan)
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
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now: plan)
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
    monkeypatch.setattr(cli, "_heal_plan", lambda text, now: {"target": None, "skipped": [], "note": "nothing to heal — no outstanding failures"})
    monkeypatch.setattr(cli, "_run_serialize", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not serialize")))

    class A:
        dry_run = False
    assert cli._cmd_heal(A()) == 0
    assert "no outstanding failures" in capsys.readouterr().out


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


def test_cli_recall_flags_superseded(tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    from daimon_briefing import store

    proj = str((tmp_path / "proj").resolve())
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    store.write_checkpoint(
        "S-old", _recall_checkpoint("S-old", "meerkat plan v1", "2021-01-01T00:00:00Z"),
        project_dir=proj)
    store.write_checkpoint(
        "S-new", _recall_checkpoint("S-new", "meerkat plan v2", "2025-01-01T00:00:00Z"),
        project_dir=proj)
    rc = cli.main(["recall", "meerkat", "--project", proj])
    assert rc == 0
    lines = [l for l in capsys.readouterr().out.splitlines() if "meerkat" in l]
    assert len(lines) == 2
    assert "superseded" not in lines[0]  # live item first
    assert "superseded by S-new" in lines[1]


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


def test_crash_log_info_reports_last_line_and_age(tmp_path):
    p = tmp_path / "serialize-crash.log"
    p.write_text(
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "OSError: [Errno 28] No space left on device\n"
    )
    import os
    os.utime(p, (1000.0, 1000.0))
    info = cli._crash_log_info(p, now=1300.0)
    assert info["last_line"] == "OSError: [Errno 28] No space left on device"
    assert info["age_seconds"] == 300
    assert info["age"] == "5m"
    assert info["path"] == str(p)


def test_status_json_includes_crash_info(tmp_checkpoint_dir, sample_checkpoint, capsys, monkeypatch):
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    crash = config.log_dir() / "serialize-crash.log"
    crash.parent.mkdir(parents=True, exist_ok=True)
    crash.write_text("RuntimeError: child exploded\n")
    rc = cli.main(["status", "--json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["crash"]["last_line"] == "RuntimeError: child exploded"


def test_status_plain_shows_crash_line(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import config, store
    store.write_checkpoint("S1", sample_checkpoint)
    crash = config.log_dir() / "serialize-crash.log"
    crash.parent.mkdir(parents=True, exist_ok=True)
    crash.write_text("RuntimeError: child exploded\n")
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "last serialize crash" in out.lower()
    assert "RuntimeError: child exploded" in out


def test_status_plain_no_crash_line_when_log_absent(tmp_checkpoint_dir, sample_checkpoint, capsys):
    from daimon_briefing import store
    store.write_checkpoint("S1", sample_checkpoint)
    rc = cli.main(["status"])
    assert rc == 0
    assert "last serialize crash" not in capsys.readouterr().out.lower()


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
    rc = cli.main(["status", "--json"])
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
    rc = cli.main(["status"])
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
