"""`bucket doctor` points a person whose buckets lost their folders at the
move runbook (#1384 slice 6).

A dangling or unstamped bucket is what a machine move, a username change or a
renamed folder leaves behind, and the person reading the survey is the one who
needs the page that says which command to run next. The hint is a line of
text for a person: JSON stays data, a healthy store stays quiet.
"""

import json
from pathlib import Path

import pytest

from daimon_briefing import cli
from daimon_briefing.cli import projects as cli_projects
from tests.test_bucket_doctor import _stamped

PAGE = "guides/move-to-a-new-machine"
URL = f"https://daily-nerd.github.io/daimon/docs/{PAGE}"
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def not_tty(monkeypatch):
    monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: False)


def test_a_dangling_bucket_gets_the_runbook_line(
        tmp_path, tmp_checkpoint_dir, capsys):
    _stamped("-gone", str(tmp_path / "gone"))
    assert cli.main(["bucket", "doctor"]) == 0
    out = capsys.readouterr().out
    assert f"moved to a new machine or folder? {URL}" in out


def test_an_unstamped_bucket_gets_it_too(tmp_path, tmp_checkpoint_dir, capsys):
    bare = tmp_checkpoint_dir / "-bare"
    bare.mkdir(parents=True)
    (bare / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert cli.main(["bucket", "doctor"]) == 0
    assert URL in capsys.readouterr().out


def test_a_healthy_store_stays_quiet(tmp_path, tmp_checkpoint_dir, capsys):
    live = tmp_path / "live"
    live.mkdir()
    _stamped("-live", str(live))
    assert cli.main(["bucket", "doctor"]) == 0
    assert URL not in capsys.readouterr().out


def test_json_stays_data(tmp_path, tmp_checkpoint_dir, capsys):
    _stamped("-gone", str(tmp_path / "gone"))
    assert cli.main(["bucket", "doctor", "--json"]) == 0
    out = capsys.readouterr().out
    assert URL not in out
    json.loads(out)


@pytest.mark.parametrize("root", [
    "website/docs", "website/i18n/es/docusaurus-plugin-content-docs/current"])
def test_the_page_the_hint_names_exists_in_both_languages(root):
    assert (REPO / root / f"{PAGE}.md").is_file()
