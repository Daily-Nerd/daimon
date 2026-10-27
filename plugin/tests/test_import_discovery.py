"""#1236 slice 3: `daimon import --project <dir> [--sessions N]`, discovery.

With no `--transcript`, the import finds the project's transcripts under the
automatic hosts' roots (Claude Code, Codex), keeps the N newest by session
end, and hands exactly those to the slice 2 planner. Every root the
discovery reads is a tmp path (the `home` fixture: `DAIMON_CLAUDE_PROJECTS_DIR`,
`CODEX_HOME`, `KIMI_CODE_HOME` and `HOME` under tmp), `cli._chat` is faked and
counts its calls, and the autouse isolation keeps the store and the log
under tmp.
"""

import json
import os

import pytest

from daimon_briefing import cli, importer, provenance, store
from daimon_briefing.surfaces import Writer
from tests.test_import_cmd import (
    OLD,
    PROJECT,
    _reporting,
    _rows,
    _transcript,
    _valid_json,
)

OTHER = "/p/other"
STAMPS = [f"2026-0{m}-0{d}T10:00:00Z" for m in (7, 8, 9) for d in (1, 2, 3)]


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The slice 2 fixture's environment: HOME and every host root the
    discovery walks under tmp, the live-session id and the project env
    cleared."""
    h = tmp_path / "home"
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("DAIMON_CLAUDE_PROJECTS_DIR", str(h / ".claude" / "projects"))
    monkeypatch.setenv("KIMI_CODE_HOME", str(h / ".kimi-code"))
    monkeypatch.setenv("CODEX_HOME", str(h / ".codex"))
    monkeypatch.setenv("DAIMON_MIN_MESSAGES", "3")
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("DAIMON_PROJECT_DIR", raising=False)
    return h


def _slug_dir(home, project=PROJECT):
    return home / ".claude" / "projects" / store.project_slug(project)


def _claude(home, sid, ended, *, cwd=PROJECT, slug_of=PROJECT, n=4, mtime=OLD):
    return _transcript(_slug_dir(home, slug_of), sid, ended, n=n, cwd=cwd, mtime=mtime)


def _codex(home, stamp_id, ended, *, cwd=PROJECT, root="sessions", n=4):
    """A Codex rollout at the measured layout: `session_meta` first, with
    the cwd under `payload`, then the conversation rows (no cwd on them)."""
    d = home / ".codex" / root / "2026" / "10" / "01"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-2026-10-01T10-00-00-{stamp_id}.jsonl"
    rows = [{"type": "session_meta", "payload": {"cwd": cwd, "id": stamp_id}}]
    rows += _rows(ended, n, cwd=None)
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    os.utime(p, (OLD, OLD))
    return p


def _argv(*extra, project=PROJECT):
    argv = ["import"]
    if project is not None:
        argv += ["--project", project]
    return argv + list(extra)


def _planned(out):
    """The session ids of the numbered plan rows, in print order."""
    rows = [ln for ln in out.splitlines() if ln.startswith("  ") and ln[2].isdigit()]
    return [ln.split("  note:")[0].split()[-1] for ln in rows]


# ---- the capability table: one source for the plan line and the docs ----------


def test_the_capability_table_names_every_host_once():
    rows = {c.host: (c.mode, c.reason) for c in importer.CAPABILITIES}
    assert rows == {
        "claude-code": ("automatic", ""),
        "codex": ("automatic", ""),
        "windsurf": ("path-given", "records no project, pass --transcript"),
        "kimi": ("path-given", "records no project, pass --transcript"),
        "gemini": ("unsupported", "no transcript file"),
        "hermes": ("unsupported", "no transcript file"),
    }
    assert [c.host for c in importer.CAPABILITIES] == [
        h for h in provenance.HOSTS if h != "manual"
    ]
    assert set(rows) == set(provenance.HOSTS) - {"manual"}
    assert importer.DISCOVERED_HOSTS == ("claude-code", "codex")
    assert importer.not_discovered_line() == (
        "  not discovered: windsurf, kimi (records no project, pass "
        "--transcript); gemini, hermes (no transcript file)"
    )


# ---- discovery: cwd is the match, the slug never is -----------------------------


def test_discover_matches_on_the_recorded_cwd_newest_first(tmp_path, home):
    newest = _claude(home, "S-new", STAMPS[8])
    mid = _codex(home, "mid", STAMPS[5])
    oldest = _claude(home, "S-old", STAMPS[0])
    # A session run from a subdirectory lands in another slug directory and
    # records this project's cwd: discovered. One filed under this project's
    # slug that records another cwd: not discovered.
    subdir = _claude(home, "S-sub", STAMPS[3], slug_of=PROJECT + "/plugin")
    _claude(home, "S-theirs", STAMPS[7], cwd=OTHER)
    _codex(home, "theirs", STAMPS[6], cwd=OTHER)
    archived = _codex(home, "arch", STAMPS[4], root="archived_sessions")
    _transcript(_slug_dir(home), "notes", STAMPS[8], suffix=".md")
    resolver = provenance.SourceResolver(claude_projects=_slug_dir(home).parent)
    assert importer.discover(PROJECT, resolver).paths == [
        newest, mid, archived, subdir, oldest
    ]
    assert importer.discover(OTHER, resolver).paths == [
        _slug_dir(home) / "S-theirs.jsonl",
        home / ".codex" / "sessions" / "2026" / "10" / "01"
        / "rollout-2026-10-01T10-00-00-theirs.jsonl",
    ]
    assert importer.discover("/p/nobody", resolver).paths == []
    assert "S-sub" not in str(_slug_dir(home))  # the slug was not the match


def test_discover_resolves_the_recorded_cwd_like_the_cli_resolves_project(
    tmp_path, home
):
    """A cwd recorded with a trailing slash or a `..` segment names the same
    project the CLI resolved `--project` to: one resolver on both sides."""
    a = _claude(home, "S-slash", STAMPS[1], cwd=PROJECT + "/")
    b = _claude(home, "S-dots", STAMPS[2], cwd=PROJECT + "/plugin/..")
    resolver = provenance.SourceResolver(claude_projects=_slug_dir(home).parent)
    assert importer.discover(PROJECT, resolver).paths == [b, a]


# ---- the window: newest N before the skips ----------------------------------------


def test_the_default_window_is_five_newest_and_the_plan_runs_them_oldest_first(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    for i in range(7):
        _claude(home, f"S-{i}", STAMPS[i])
    assert importer.DEFAULT_SESSIONS == 5
    assert cli.main(_argv("--plan")) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd (claude-code): 5 sessions, about 5 model calls" in out
    assert _planned(out) == ["S-2", "S-3", "S-4", "S-5", "S-6"]
    assert importer.not_discovered_line() in out
    assert chat.calls == []
    assert not (tmp_path / ".daimon" / "checkpoints").exists()


def test_sessions_n_picks_the_n_newest_across_both_hosts(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = _reporting(fake_chat_factory, _valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    _claude(home, "S-a", STAMPS[0])
    _codex(home, "bbb", STAMPS[4])
    _claude(home, "S-c", STAMPS[8])
    _claude(home, "S-theirs", STAMPS[7], cwd=OTHER)
    assert cli.main(_argv("--sessions", "2")) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd (claude-code+codex): 2 sessions" in out
    assert _planned(out) == ["rollout-2026-10-01T10-00-00-bbb", "S-c"]
    assert len(chat.calls) == 2
    assert store.read_checkpoint("S-c") is not None
    assert store.read_checkpoint("rollout-2026-10-01T10-00-00-bbb") is not None
    assert store.read_checkpoint("S-a") is None
    assert store.read_checkpoint("S-theirs") is None
    assert "imported 2 of 2, 2 calls (2 planned)" in out


def test_the_window_is_cut_before_the_skips_so_a_resume_rebuilds_the_same_plan(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """`--sessions 3` means the three newest transcripts on disk. The one
    already captured is named as skipped inside that three; the fourth
    newest does not take its place (design 4(d): the same command rebuilds
    the same plan)."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    for i in range(4):
        _claude(home, f"S-{i}", STAMPS[i])
    store.write_checkpoint(
        "S-3", json.loads(_valid_json()) | {"session_id": "S-3"},
        project_dir=PROJECT, writer=Writer.HUMAN,
    )
    assert cli.main(_argv("--sessions", "3", "--plan")) == 0
    out = capsys.readouterr().out
    assert "2 sessions, about 2 model calls" in out
    assert _planned(out) == ["S-1", "S-2"]
    assert "skipped S-3: already has a checkpoint" in out
    assert "S-0" not in out
    assert chat.calls == []


def test_the_live_session_counts_against_the_window(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    _claude(home, "S-old", STAMPS[0])
    _claude(home, "S-done", STAMPS[1])
    _claude(home, "S-named", STAMPS[2])
    _claude(home, "S-fresh", STAMPS[3], mtime=None)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S-named")
    assert cli.main(_argv("--sessions", "3")) == 0
    out = capsys.readouterr().out
    assert "skipped S-named: the live session (CLAUDE_CODE_SESSION_ID)" in out
    assert "skipped S-fresh: modified in the last 10 minutes, may be live" in out
    assert _planned(out) == ["S-done"]
    assert "S-old" not in out
    assert len(chat.calls) == 1
    assert store.read_checkpoint("S-done") is not None


def test_a_resume_after_a_failure_keeps_the_window_and_skips_the_done_one(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    from daimon_briefing import llm

    chat = fake_chat_factory([_valid_json(), llm.ChatError("quota"), _valid_json(), _valid_json()])
    monkeypatch.setattr(cli, "_chat", chat)
    for i in range(4):
        _claude(home, f"S-{i}", STAMPS[i])
    assert cli.main(_argv("--sessions", "3")) == 1
    assert len(chat.calls) == 2
    capsys.readouterr()
    assert cli.main(_argv("--sessions", "3")) == 0
    out = capsys.readouterr().out
    assert "2 sessions, about 2 model calls" in out
    assert "skipped S-1: already has a checkpoint" in out
    assert _planned(out) == ["S-2", "S-3"]
    assert len(chat.calls) == 4
    assert store.read_checkpoint("S-0") is None


def test_nothing_on_disk_prints_an_empty_plan_and_the_hosts_line(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    assert cli.main(_argv()) == 0
    out = capsys.readouterr().out
    assert "import plan for import-cmd: 0 sessions, about 0 model calls" in out
    assert importer.not_discovered_line() in out
    assert "nothing to import" in out
    assert chat.calls == []


def test_a_path_given_run_prints_no_hosts_line(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    p = _claude(home, "S-given", STAMPS[0])
    assert cli.main(_argv("--transcript", str(p), "--plan")) == 0
    out = capsys.readouterr().out
    assert "1 session, about 1 model call" in out
    assert "not discovered" not in out


def test_an_unlistable_root_is_one_named_line_never_a_traceback(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    from pathlib import Path

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    _claude(home, "S-ok", STAMPS[0])
    sealed = home / ".codex" / "sessions"
    sealed.mkdir(parents=True)
    real_glob = Path.glob

    def glob(self, pattern, *a, **k):
        if self == sealed:
            raise PermissionError(13, "Permission denied", str(self))
        return real_glob(self, pattern, *a, **k)

    monkeypatch.setattr(Path, "glob", glob)
    assert cli.main(_argv("--plan")) == 0
    out = capsys.readouterr().out
    assert f"  codex root unreadable: {sealed}" in out
    assert _planned(out) == ["S-ok"]
    assert chat.calls == []
    resolver = provenance.SourceResolver(claude_projects=_slug_dir(home).parent)
    assert importer.discover(PROJECT, resolver).unreadable == (("codex", sealed),)


def test_max_calls_bounds_a_discovered_run_the_same_way(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = _reporting(fake_chat_factory, _valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    for i in range(3):
        _claude(home, f"S-{i}", STAMPS[i])
    assert cli.main(_argv("--max-calls", "2")) == 0
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


def test_identical_end_stamps_break_the_tie_by_path_ascending(tmp_path, home):
    same = STAMPS[4]
    b = _claude(home, "S-b", same)
    c = _codex(home, "ccc", same)
    a = _claude(home, "S-a", same)
    resolver = provenance.SourceResolver(claude_projects=_slug_dir(home).parent)
    found = importer.discover(PROJECT, resolver).paths
    assert found == sorted([a, b, c], key=str)
    # The window cut follows the same order, so `--sessions 2` is predictable.
    assert found[:2] == sorted([a, b, c], key=str)[:2]


def test_a_symlinked_project_resolves_to_the_directory_the_transcript_recorded(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """`--project` through a symlink and a recorded cwd through the real
    path name one project: both go through config.resolve_project_dir,
    which collapses symlinks before comparing (scar 0023 is the other way
    round: a slug computed from an unresolved path)."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"
    link.symlink_to(real)
    resolved = os.path.realpath(real)
    _claude(home, "S-real", STAMPS[0], cwd=resolved, slug_of=resolved)
    _claude(home, "S-link", STAMPS[1], cwd=str(link), slug_of=str(link))
    assert cli.main(_argv("--plan", project=str(link))) == 0
    out = capsys.readouterr().out
    assert _planned(out) == ["S-real", "S-link"]
    assert chat.calls == []
    assert cli.main(_argv(project=str(link))) == 0
    assert store.read_checkpoint("S-real")["project_slug"] == store.project_slug(resolved)
    assert store.read_checkpoint("S-link")["project_slug"] == store.project_slug(resolved)


def test_a_window_of_captured_sessions_says_how_to_reach_older_history(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """L5: the window is cut before the skips, so a project daimon has been
    capturing live shows its newest sessions as done. The plan says what
    to do, and only when older transcripts exist beyond the window."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    for i in range(4):
        _claude(home, f"S-{i}", STAMPS[i])
    for sid in ("S-3", "S-2"):
        store.write_checkpoint(
            sid, json.loads(_valid_json()) | {"session_id": sid},
            project_dir=PROJECT, writer=Writer.HUMAN,
        )
    assert cli.main(_argv("--sessions", "2")) == 0
    out = capsys.readouterr().out
    assert "skipped S-3: already has a checkpoint" in out
    assert "skipped S-2: already has a checkpoint" in out
    hint = "  all 2 newest sessions already captured; raise --sessions to reach older history"
    assert hint in out
    assert out.index(hint) < out.index(importer.not_discovered_line())
    assert "nothing to import" in out
    assert chat.calls == []
    # With every transcript on disk inside the window there is nothing older
    # to reach, and the line stays out.
    capsys.readouterr()
    assert cli.main(_argv("--sessions", "4")) == 0
    out = capsys.readouterr().out
    assert "raise --sessions" not in out
    assert _planned(out) == ["S-0", "S-1"]
    # One window row skipped for another reason (too short) is not "all captured".
    _claude(home, "S-short", STAMPS[5], n=2)
    capsys.readouterr()
    assert cli.main(_argv("--sessions", "3", "--plan")) == 0
    out = capsys.readouterr().out
    assert "skipped S-short: transcript too short" in out
    assert "raise --sessions" not in out


# ---- Codex: session_meta cwd in, rollout stem out -----------------------------------


def test_codex_rollouts_are_keyed_by_stem_and_done_by_the_bare_id(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    from daimon_briefing import ledger

    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    _codex(home, "abc", STAMPS[0])
    _codex(home, "def", STAMPS[1], root="archived_sessions")
    ledger._append_serialize_log(
        "wrote checkpoint: /x/rollout-2026-10-01T10-00-00-def.json (took 1s)"
    )
    assert cli.main(_argv()) == 0
    out = capsys.readouterr().out
    stem = "rollout-2026-10-01T10-00-00-abc"
    assert "import plan for import-cmd (codex): 1 session" in out
    assert "skipped rollout-2026-10-01T10-00-00-def: already captured" in out
    assert len(chat.calls) == 1
    assert store.read_checkpoint(stem) is not None
    assert f"import: started serialize for {stem} (reason: import" in (
        tmp_path / ".daimon" / "logs" / "serialize.log"
    ).read_text(encoding="utf-8")


def test_a_rollout_moved_to_archived_sessions_plans_once_not_as_a_refusal(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    live = _codex(home, "moved", STAMPS[2])
    _codex(home, "moved", STAMPS[2], root="archived_sessions")
    assert cli.main(_argv("--plan")) == 0
    out = capsys.readouterr().out
    assert _planned(out) == ["rollout-2026-10-01T10-00-00-moved"]
    assert "refused" not in out
    resolver = provenance.SourceResolver(claude_projects=_slug_dir(home).parent)
    assert importer.discover(PROJECT, resolver).paths == [live]


# ---- an undecodable transcript inside the window is a named skip -----------------


def test_an_undecodable_transcript_in_the_window_is_a_named_skip_not_a_traceback(
    tmp_path, home, fake_chat_factory, capsys, monkeypatch
):
    """The cwd reader streams and stops before the damage, so the file is
    discovered; the planner then names it as unreadable, beside a missing
    or permission-denied file, and the other sessions run."""
    chat = fake_chat_factory(_valid_json())
    monkeypatch.setattr(cli, "_chat", chat)
    _claude(home, "S-fine", STAMPS[0])
    cut = _slug_dir(home) / "S-cut.jsonl"
    rows = "\n".join(json.dumps(r) for r in _rows(STAMPS[1], 4))
    cut.write_bytes(rows.encode() + b"\n" + b'{"cwd": "/p/y\xe2\x80')
    os.utime(cut, (OLD, OLD))
    assert cli.main(_argv()) == 0
    out = capsys.readouterr().out
    assert "skipped S-cut: transcript unreadable" in out
    assert _planned(out) == ["S-fine"]
    assert "imported 1 of 1" in out
    assert len(chat.calls) == 1
    # Path-given, the same file is the same named skip.
    capsys.readouterr()
    assert cli.main(_argv("--transcript", str(cut), "--plan")) == 0
    assert "skipped S-cut: transcript unreadable" in capsys.readouterr().out


# ---- the flags ---------------------------------------------------------------------------


def test_transcript_and_sessions_together_is_a_usage_error(tmp_path, home, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(_argv("--transcript", "x", "--sessions", "2"))
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_sessions_rejects_zero_and_a_word(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(_argv("--sessions", "0"))
    assert exc.value.code == 2
    assert "--sessions needs a count of 1 or more" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        cli.main(_argv("--sessions", "five"))
    assert exc.value.code == 2
    assert "not a whole number: 'five'" in capsys.readouterr().err
