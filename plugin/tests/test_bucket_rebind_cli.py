"""`daimon bucket adopt | remap` as a person runs them (slice 4b).

Both rewrite a bucket's `root`, the one binding, so both preview by default
(`--apply` writes), refuse on any doubt with rc 2, print other projects' roots
and names only to a terminal, and are refused outright on a tenant-scoped home
(scar 0071): a tenant may not choose which bucket a folder answers for.
"""

import json
import os
from pathlib import Path

import pytest

from daimon_briefing import cli, identity, store
from daimon_briefing.cli import projects as cli_projects

from tests.test_bucket_merge import _snapshot

PRIVATE = "-the-clients-secret-project"


@pytest.fixture
def tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)


@pytest.fixture
def not_tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)


def _make(root_dir, name, root=None):
    d = root_dir / name
    d.mkdir(parents=True, exist_ok=True)
    if root is not None:
        (d / "root").write_text(f"{root}\n", encoding="utf-8")
    return d


def _root(bucket):
    return (bucket / "root").read_text(encoding="utf-8").strip()


@pytest.fixture
def moved(tmp_path, tmp_checkpoint_dir):
    """A bucket recorded under a home that is gone, and the same project under
    the home that exists now."""
    new = tmp_path / "bob" / "proj"
    new.mkdir(parents=True)
    old = str(tmp_path / "alice" / "proj")
    bucket = _make(tmp_checkpoint_dir, "-alice-proj", old)
    return bucket, old, str(new.resolve()), str(tmp_path / "bob")


# ---- adopt ----------------------------------------------------------------------------


def test_adopt_is_a_dry_run_by_default(moved, tty, capsys):
    bucket, old, new, _ = moved
    before = _snapshot()
    rc = cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _snapshot() == before
    assert "would rebind -alice-proj" in out and "--apply" in out


def test_adopt_apply_rebinds_and_reports(moved, tty, capsys):
    bucket, old, new, _ = moved
    rc = cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}",
                   "--apply"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _root(bucket) == new
    assert "rebound -alice-proj" in out
    assert store.bucket_of(new) == "-alice-proj"


def test_adopt_prints_the_old_root_and_evidence_to_a_terminal_only(
        moved, capsys, monkeypatch):
    bucket, old, new, _ = moved
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}"])
    assert old in capsys.readouterr().out
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)
    cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}"])
    assert old not in capsys.readouterr().out


def test_adopt_json_hides_the_old_root_off_a_terminal(moved, not_tty, capsys):
    bucket, old, new, _ = moved
    assert cli.main(["bucket", "adopt", "--bucket=-alice-proj",
                     f"--path={new}", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["action"] == "rewrite" and record["to"] == new
    assert "from" not in record and "evidence" not in record


def test_adopt_json_on_a_terminal_carries_the_whole_receipt(
        moved, tty, capsys):
    bucket, old, new, _ = moved
    cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}",
              "--apply", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert record["applied"] is True and record["from"] == old


def test_adopt_refuses_with_rc_2_and_leaves_the_store(moved, capsys):
    bucket, old, new, _ = moved
    _make(bucket.parent, "-fresh-today", new)
    before = _snapshot()
    rc = cli.main(["bucket", "adopt", "--bucket=-alice-proj", f"--path={new}",
                   "--apply"])
    assert rc == 2
    assert _snapshot() == before
    assert "bucket merge" in capsys.readouterr().err


def test_adopt_refusal_is_machine_readable_with_json(moved, capsys):
    rc = cli.main(["bucket", "adopt", "--bucket=-alice-proj",
                   "--path=/nowhere/at/all", "--json"])
    assert rc == 2
    assert "not a directory" in json.loads(capsys.readouterr().out)["refused"]


def test_adopt_force_moves_a_bucket_whose_root_still_exists(
        moved, tmp_path, capsys):
    bucket, old, new, _ = moved
    live = tmp_path / "live"
    live.mkdir()
    _make(bucket.parent, "-live", str(live.resolve()))
    assert cli.main(["bucket", "adopt", "--bucket=-live", f"--path={new}",
                     "--apply"]) == 2
    assert "still exists" in capsys.readouterr().err
    assert cli.main(["bucket", "adopt", "--bucket=-live", f"--path={new}",
                     "--apply", "--force"]) == 0
    assert _root(bucket.parent / "-live") == new


# ---- remap ----------------------------------------------------------------------------


def test_remap_is_a_dry_run_by_default(moved, tty, capsys):
    bucket, old, new, new_home = moved
    before = _snapshot()
    rc = cli.main(["bucket", "remap", f"--from={Path(old).parent}",
                   f"--to={new_home}"])
    out = capsys.readouterr().out
    assert rc == 0
    assert _snapshot() == before
    assert "would rewrite" in out and "--apply" in out


def test_remap_apply_rewrites_and_a_rerun_says_nothing_to_do(
        moved, tty, capsys):
    bucket, old, new, new_home = moved
    argv = ["bucket", "remap", f"--from={Path(old).parent}",
            f"--to={new_home}", "--apply"]
    assert cli.main(argv) == 0
    assert _root(bucket) == new
    capsys.readouterr()
    assert cli.main(argv) == 0
    assert "no bucket" in capsys.readouterr().out


def test_remap_counts_off_a_terminal_and_names_on_one(
        moved, capsys, monkeypatch):
    bucket, old, new, new_home = moved
    argv = ["bucket", "remap", f"--from={Path(old).parent}",
            f"--to={new_home}"]
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)
    cli.main(argv)
    plain = capsys.readouterr().out
    assert "-alice-proj" not in plain and old not in plain
    assert "rewrite: 1" in plain
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    cli.main(argv)
    assert "-alice-proj" in capsys.readouterr().out


def test_remap_json_off_a_terminal_is_counts_only(moved, not_tty, capsys):
    bucket, old, new, new_home = moved
    cli.main(["bucket", "remap", f"--from={Path(old).parent}",
              f"--to={new_home}", "--json"])
    record = json.loads(capsys.readouterr().out)
    assert record["counts"] == {"rewrite": 1}
    assert "actions" not in record


def test_remap_exit_1_when_a_bucket_needs_a_person(moved, tty, capsys):
    bucket, old, new, new_home = moved
    _make(bucket.parent, "-fresh-today", new)
    rc = cli.main(["bucket", "remap", f"--from={Path(old).parent}",
                   f"--to={new_home}", "--apply"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "collision" in out and "bucket merge --from=-fresh-today" in out


def test_remap_missing_targets_are_not_an_error(moved, tty, capsys):
    bucket, old, new, new_home = moved
    _make(bucket.parent, "-not-cloned", str(Path(old).parent / "never"))
    rc = cli.main(["bucket", "remap", f"--from={Path(old).parent}",
                   f"--to={new_home}", "--apply"])
    assert rc == 0
    assert "missing" in capsys.readouterr().out


def test_remap_refuses_the_filesystem_root_with_rc_2(moved, capsys):
    bucket, old, new, new_home = moved
    before = _snapshot()
    assert cli.main(["bucket", "remap", "--from=/", f"--to={new_home}",
                     "--apply"]) == 2
    assert _snapshot() == before
    assert "every bucket" in capsys.readouterr().err


def test_the_xfail_scenario_by_hand(moved, capsys):
    """Old home to new home, rebind, and the new folder reads the old bucket."""
    bucket, old, new, new_home = moved
    assert store.bucket_of(new) != "-alice-proj"
    cli.main(["bucket", "remap", f"--from={Path(old).parent}",
              f"--to={new_home}", "--apply"])
    assert store.bucket_of(new) == "-alice-proj"
    assert identity.claim(new).slug == "-alice-proj"
    assert os.path.isdir(bucket)


# ---- tenant scope: both refused, nothing moves (scar 0071) ---------------------------------


@pytest.fixture
def tenant_with_foreign_bucket(tmp_path, tmp_checkpoint_dir, monkeypatch):
    other = tmp_path / "other-tenant"
    other.mkdir()
    foreign = _make(tmp_checkpoint_dir, PRIVATE, str(other.resolve()))
    (foreign / "requests.jsonl").write_text('{"event_id": "x"}\n',
                                            encoding="utf-8")
    _make(tmp_checkpoint_dir, "-mine")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    return tmp_path


@pytest.mark.parametrize("argv", [
    ["bucket", "adopt", f"--bucket={PRIVATE}", "--path=.", "--apply"],
    ["bucket", "adopt", "--bucket=-mine", "--path=.", "--apply", "--force"],
    ["bucket", "remap", "--from=/", "--to=/x", "--apply"],
    ["bucket", "remap", "--from=/some", "--to=/other", "--apply"],
])
def test_adopt_and_remap_are_refused_on_a_tenant_scoped_home(
        tenant_with_foreign_bucket, argv, capsys):
    before = _snapshot()
    assert cli.main(argv) == 2
    assert _snapshot() == before
    out = capsys.readouterr()
    assert "tenant-scoped" in out.err
    assert PRIVATE not in out.out + out.err


def test_a_planted_foreign_bucket_is_not_rebound_under_tenant_scope(
        tenant_with_foreign_bucket, tmp_checkpoint_dir, capsys):
    """The primitive the refusal protects: pointing the foreign bucket at this
    tenant's folder would hand the tenant another tenant's history."""
    mine = tenant_with_foreign_bucket / "mine"
    mine.mkdir()
    foreign_root = _root(tmp_checkpoint_dir / PRIVATE)
    assert cli.main(["bucket", "adopt", f"--bucket={PRIVATE}",
                     f"--path={mine}", "--apply", "--force"]) == 2
    assert _root(tmp_checkpoint_dir / PRIVATE) == foreign_root
    assert store.bucket_of(str(mine.resolve())) != PRIVATE


def test_a_tenant_refusal_is_machine_readable_with_json(
        tenant_with_foreign_bucket, capsys):
    assert cli.main(["bucket", "remap", "--from=/a", "--to=/b",
                     "--json"]) == 2
    assert "tenant-scoped" in json.loads(capsys.readouterr().out)["refused"]
