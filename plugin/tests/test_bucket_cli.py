"""`daimon bucket merge | doctor | stamp` as a person runs them (slice 3b).

All three are human-only repair verbs. Dry run is the default for merge and
stamp (`--apply` writes); doctor is read-only and prints other projects' names
only to a terminal; on a tenant-scoped home every one of them is refused, since
each addresses buckets by name or enumerates them (scar 0071).
"""

import json
import shutil

import pytest

from daimon_briefing import buckets, cli, config, store
from daimon_briefing.cli import projects as cli_projects
from tests.test_bucket_merge import _dir, _fill, _snapshot, pair  # noqa: F401
from tests.test_bucket_stamp_tool import _bucket_for, _repo

PRIVATE = "-the-clients-secret-project"


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)


@pytest.fixture
def not_tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_is_a_dry_run_by_default(pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    before = _snapshot()
    rc = cli.main(["bucket", "merge", f"--from={g}", f"--into={b}"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _snapshot() == before
    assert f"would fold {g} into {b}" in out
    assert "--apply" in out


def test_merge_apply_folds_and_reports_what_it_did(pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    rc = cli.main(["bucket", "merge", f"--from={g}", f"--into={b}", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"folded {g} into {b}" in out
    assert "promoted g2" in out
    assert "b1" in out and "g1" in out
    assert not _dir(g).exists()


def test_merge_json_prints_the_record(pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    assert cli.main(["bucket", "merge", f"--from={g}", f"--into={b}", "--apply",
                     "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["promoted"] == "g2"
    assert record["from_slug"] == g and record["to_slug"] == b


def test_merge_refuses_a_live_root_with_rc_2_and_force_overrides(
        pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    assert cli.main(["bucket", "merge", f"--from={g}", f"--into={b}",
                     "--apply"]) == 2
    assert "still exists" in capsys.readouterr().err
    assert cli.main(["bucket", "merge", f"--from={g}", f"--into={b}", "--apply",
                     "--force"]) == 0


def test_merge_refusal_is_machine_readable_with_json(pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    assert cli.main(["bucket", "merge", f"--from={g}", f"--into={b}", "--json"]) == 2
    assert "still exists" in json.loads(capsys.readouterr().out)["refused"]


def test_merge_exits_1_when_a_leftover_keeps_the_bucket_alive(
        pair, capsys):  # noqa: F811
    main, wt, b, g = pair
    _fill(main, wt)
    shutil.rmtree(wt)
    (_dir(g) / "notes.txt").write_text("x", encoding="utf-8")
    rc = cli.main(["bucket", "merge", f"--from={g}", f"--into={b}", "--apply"])
    assert rc == 1
    assert "notes.txt" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# doctor: names only to a terminal
# ---------------------------------------------------------------------------


def _plant_private(tmp_path):
    d = _dir(PRIVATE)
    d.mkdir(parents=True)
    (d / "root").write_text(str(tmp_path / "gone-client") + "\n",
                            encoding="utf-8")
    (d / "events.jsonl").write_text("{}\n", encoding="utf-8")


def test_doctor_prints_counts_and_no_names_when_not_a_terminal(
        tmp_path, tmp_checkpoint_dir, capsys, not_tty):
    _plant_private(tmp_path)
    assert cli.main(["bucket", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "dangling: 1" in out
    assert PRIVATE not in out
    assert "client" not in out


def test_doctor_json_carries_counts_only_when_not_a_terminal(
        tmp_path, tmp_checkpoint_dir, capsys, not_tty):
    _plant_private(tmp_path)
    assert cli.main(["bucket", "doctor", "--json"]) == 0
    text = capsys.readouterr().out
    assert PRIVATE not in text and "client" not in text
    assert json.loads(text)["findings"]["dangling"] == 1


def test_doctor_names_buckets_on_a_terminal(tmp_path, tmp_checkpoint_dir,
                                            capsys, tty):
    _plant_private(tmp_path)
    assert cli.main(["bucket", "doctor"]) == 0
    assert PRIVATE in capsys.readouterr().out


def test_doctor_path_prints_candidates_as_evidence_on_a_terminal(
        tmp_path, tmp_checkpoint_dir, capsys, tty):
    from daimon_briefing import bucket_tools
    from tests.test_bucket_stamp_tool import _git
    here = _repo(tmp_path / "proj", "https://example.com/o/r.git")
    d = _dir("-old-proj")
    d.mkdir(parents=True)
    (d / "root").write_text("/Users/a/proj\n", encoding="utf-8")
    bucket_tools.write_identity(d, "/Users/a/proj", {
        "origin": "https://example.com/o/r.git",
        "root_commit": _git(here, "rev-list", "--max-parents=0", "HEAD"),
        "folder": "proj"})
    assert cli.main(["bucket", "doctor", "--path", str(here)]) == 0
    out = capsys.readouterr().out
    assert "-old-proj" in out
    assert "evidence, not proof" in out
    # a candidate now has a verb to act on it, named for the person to run
    assert f"daimon bucket adopt --bucket=-old-proj --path {here}" in out


def test_doctor_withholds_merge_suggestions_for_worktrees(
        tmp_path, tmp_checkpoint_dir, capsys, tty):
    gone = tmp_path / "repo" / ".claude" / "worktrees" / "agent-1"
    for name in ("-a", "-b"):
        d = _dir(name)
        d.mkdir(parents=True)
        (d / "root").write_text(str(gone) + "\n", encoding="utf-8")
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert cli.main(["bucket", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "daimon bucket merge" not in out
    assert "withheld" in out


def test_doctor_suggests_a_merge_on_a_terminal_for_a_plain_shared_root(
        tmp_path, tmp_checkpoint_dir, capsys, tty):
    live = tmp_path / "live"
    live.mkdir()
    for name in ("-a", "-b"):
        d = _dir(name)
        d.mkdir(parents=True)
        (d / "root").write_text(str(live) + "\n", encoding="utf-8")
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert cli.main(["bucket", "doctor"]) == 0
    assert "daimon bucket merge --from=" in capsys.readouterr().out


def test_doctor_does_not_suggest_to_a_pipe(tmp_path, tmp_checkpoint_dir,
                                           capsys, not_tty):
    live = tmp_path / "live"
    live.mkdir()
    for name in ("-a", "-b"):
        d = _dir(name)
        d.mkdir(parents=True)
        (d / "root").write_text(str(live) + "\n", encoding="utf-8")
        (d / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert cli.main(["bucket", "doctor"]) == 0
    out = capsys.readouterr().out
    assert "daimon bucket merge" not in out
    assert "shared_root: 1" in out


# ---------------------------------------------------------------------------
# stamp
# ---------------------------------------------------------------------------


def test_stamp_is_a_dry_run_by_default(tmp_path, tmp_checkpoint_dir, capsys,
                                       tty):
    repo = _repo(tmp_path / "proj")
    _bucket_for(repo, stamped=True)
    before = _snapshot()
    assert cli.main(["bucket", "stamp"]) == 0
    assert _snapshot() == before
    out = capsys.readouterr().out
    assert "would" in out and "--apply" in out


def test_stamp_apply_writes_and_scan_stamps_an_unstamped_bucket(
        tmp_path, tmp_checkpoint_dir, capsys, tty):
    repo = _repo(tmp_path / "scan" / "proj")
    slug = _bucket_for(repo, stamped=False)
    assert cli.main(["bucket", "stamp", "--scan", str(tmp_path / "scan"),
                     "--apply"]) == 0
    assert (_dir(slug) / "root").read_text(encoding="utf-8").strip()
    assert slug in capsys.readouterr().out


def test_stamp_lists_counts_only_when_not_a_terminal(
        tmp_path, tmp_checkpoint_dir, capsys, not_tty):
    repo = _repo(tmp_path / "proj")
    slug = _bucket_for(repo, stamped=True)
    assert cli.main(["bucket", "stamp"]) == 0
    out = capsys.readouterr().out
    assert slug not in out
    assert "write identity for: 1" in out


def test_stamp_exits_1_when_two_repositories_match(
        tmp_path, tmp_checkpoint_dir, capsys, tty):
    one = _repo(tmp_path / "scan" / "my" / "app")
    _repo(tmp_path / "scan" / "my-app")
    _bucket_for(one, stamped=False)
    assert cli.main(["bucket", "stamp", "--scan", str(tmp_path / "scan"),
                     "--apply"]) == 1
    assert "ambiguous" in capsys.readouterr().out


def test_stamp_refuses_a_missing_scan_directory_with_rc_2(
        tmp_path, tmp_checkpoint_dir, capsys):
    assert cli.main(["bucket", "stamp", "--scan", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# tenant scope: every verb refused, nothing moves (scar 0071)
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_with_foreign_bucket(tmp_path, tmp_checkpoint_dir, monkeypatch):
    foreign = _dir(PRIVATE)
    foreign.mkdir(parents=True)
    (foreign / "root").write_text(str(tmp_path / "other-tenant") + "\n",
                                  encoding="utf-8")
    (foreign / "requests.jsonl").write_text('{"event_id": "x"}\n',
                                            encoding="utf-8")
    mine = _dir("-mine")
    mine.mkdir()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    return tmp_path


@pytest.mark.parametrize("argv", [
    ["bucket", "merge", f"--from={PRIVATE}", "--into=-mine", "--apply"],
    ["bucket", "merge", "--from=-mine", f"--into={PRIVATE}", "--apply"],
    ["bucket", "stamp", "--apply"],
    ["bucket", "stamp", "--scan", ".", "--apply"],
    ["bucket", "doctor"],
    ["bucket", "doctor", "--path", "."],
])
def test_every_new_verb_is_refused_on_a_tenant_scoped_home(
        tenant_with_foreign_bucket, argv, capsys):
    before = _snapshot()
    assert cli.main(argv) == 2
    assert _snapshot() == before
    out = capsys.readouterr()
    assert "tenant-scoped" in out.err
    assert PRIVATE not in out.out + out.err
    assert buckets.alias_map() == {}
    assert config.checkpoint_dir().is_dir() and store.bucket_dirs().buckets


def test_a_tenant_refusal_is_machine_readable_with_json(
        tenant_with_foreign_bucket, capsys):
    assert cli.main(["bucket", "doctor", "--json"]) == 2
    assert "tenant-scoped" in json.loads(capsys.readouterr().out)["refused"]
