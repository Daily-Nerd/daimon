"""The `bucket merge` that `remap` and `adopt` print for a collision works.

A collision is the fresh bucket a session birthed in the new folder before the
person rebound the old one. Its recorded root IS that folder, and the folder
exists (a collision needs it to), so `merge` refuses to fold it away without
`--force`: the folder is live, so on its own it would birth the bucket again.
The command the verbs print therefore has to carry `--force`, and running it
exactly as printed has to fold the bucket, after which the rebind goes through.
The check is the behavior: parse the printed command and run it.
"""

import re

import pytest

from daimon_briefing import bucket_tools, cli, identity, store
from daimon_briefing.cli import projects as cli_projects
from tests.test_bucket_rebind_cli import _make, _root


@pytest.fixture
def collided(tmp_path, tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    new = tmp_path / "bob" / "proj"
    new.mkdir(parents=True)
    real = str(new.resolve())
    old = _make(tmp_checkpoint_dir, "-alice-proj", str(tmp_path / "alice" / "proj"))
    (old / "events.jsonl").write_text("{}\n", encoding="utf-8")
    fresh = _make(tmp_checkpoint_dir, "-fresh-today", real)
    (fresh / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return old, fresh, real, str(tmp_path / "alice"), str(tmp_path / "bob")


def _printed_merge(text: str) -> list[str]:
    command = re.search(r"daimon bucket merge [^`\n]*", text).group(0)
    return command.split()[1:]


def test_the_merge_remap_prints_for_a_collision_folds_it(collided, capsys):
    old, fresh, real, old_home, new_home = collided
    assert cli.main(["bucket", "remap", f"--from={old_home}",
                     f"--to={new_home}", "--apply"]) == 1
    argv = _printed_merge(capsys.readouterr().out)
    assert cli.main([*argv, "--apply"]) == 0
    assert not fresh.exists()
    assert cli.main(["bucket", "remap", f"--from={old_home}",
                     f"--to={new_home}", "--apply"]) == 0
    assert _root(old) == real
    identity.invalidate()
    assert store.bucket_of(real) == old.name


def test_the_merge_adopt_names_for_a_collision_folds_it(collided, capsys):
    old, fresh, real, *_ = collided
    with pytest.raises(bucket_tools.RebindRefused) as refused:
        bucket_tools.adopt(old.name, real, apply=True)
    argv = _printed_merge(str(refused.value))
    assert cli.main([*argv, "--apply"]) == 0
    assert not fresh.exists()
    bucket_tools.adopt(old.name, real, apply=True)
    assert _root(old) == real
