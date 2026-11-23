"""Every published briefing sample is the output of the real renderer.

scripts/sync_briefing_samples.py renders fixture checkpoints through
`briefing.render` (the call the session-start hook makes) and writes the
result into the README, the docs (English and Spanish), the blog posts and the
landing data. A sample typed by hand drifts; this suite fails when one does."""

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from daimon_briefing import briefing, marks

_REPO = Path(__file__).parents[2]
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import sync_briefing_samples as samples  # noqa: E402

_EM_DASH = "—"
_PUBLISHED = [(fixture, rel) for fixture, paths in
              samples.MARKDOWN_SAMPLES.items() for rel in paths]


@pytest.mark.parametrize("fixture,rel", _PUBLISHED)
def test_a_published_sample_equals_the_renderer_output(fixture, rel):
    source = (_REPO / rel).read_text("utf-8")
    assert samples.sync_markdown(fixture, source) == source, (
        f"{rel} no longer matches the renderer: run "
        "`uv run --project plugin python scripts/sync_briefing_samples.py`")


def test_the_landing_data_equals_the_renderer_output():
    path = _REPO / samples.GENERATED
    assert path.read_text("utf-8") == samples.generated_text()


def test_the_sample_files_are_all_in_sync():
    assert samples.check() == 0


def test_the_hand_off_to_the_landing_keeps_each_class():
    items = json.loads((_REPO / samples.GENERATED).read_text("utf-8"))[
        "landing"]["items"]
    assert [i["kind"] for i in items] == ["verbatim", "inferred", "stale"]
    assert items[0]["quote"] and not items[1]["quote"]


def test_the_skill_text_quotes_a_line_the_renderer_writes():
    # skill_content.py wraps its example inside prose, so it cannot be
    # regenerated; it is checked instead, whitespace folded.
    item = samples.sample("trust_lines").splitlines()[0]
    assert item.startswith("- [")
    squash = lambda t: re.sub(r"\s+", " ", t)  # noqa: E731
    skill = (_REPO / "plugin/daimon_briefing/skill_content.py").read_text("utf-8")
    assert squash(item[2:]) in squash(skill)
    assert squash(item) in squash(skill)


def test_no_published_text_keeps_the_old_quote_separator():
    # Snippets that illustrate other states (carried, noted, stale) are still
    # typed by hand, so this guards the one thing the renderer changed: an item
    # line must not put its quote after two spaces and an em-dash.
    old = re.compile(r"\S  " + _EM_DASH + r' "')
    paths = [_REPO / "README.md",
             _REPO / "plugin/daimon_briefing/skill_content.py",
             *(p for root in ("website/docs", "website/blog", "website/i18n")
               for p in (_REPO / root).rglob("*.md"))]
    assert [str(p) for p in paths if old.search(p.read_text("utf-8"))] == []


def test_the_samples_carry_no_em_dash():
    # The fixtures are written without one, and the template adds none.
    for name in ("full", "trust_lines", "landing"):
        assert _EM_DASH not in samples.render(name)
        assert _EM_DASH not in (
            _REPO / samples.FIXTURES / f"{name}.json").read_text("utf-8")


def test_a_sample_opens_with_the_one_greeting():
    assert samples.render("full").splitlines()[0] == marks.GREETING


def test_the_render_does_not_depend_on_the_clock(monkeypatch):
    # An age-weighted order would make a published sample drift with time.
    first = samples.render("full")
    monkeypatch.setattr(briefing.time, "time", lambda: 4_000_000_000.0)
    assert samples.render("full") == first


def test_the_render_ignores_the_machine_it_runs_on(monkeypatch):
    first = samples.render("full")
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "50")
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    assert samples.render("full") == first


def _tree(tmp_path):
    shutil.copytree(_REPO / samples.FIXTURES, tmp_path / samples.FIXTURES)
    for _, rel in _PUBLISHED:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_REPO / rel, tmp_path / rel)
    return tmp_path


def test_check_passes_on_a_synced_tree(tmp_path):
    root = _tree(tmp_path)
    assert samples.sync(root) == 0
    assert samples.check(root) == 0


def test_check_catches_a_hand_edited_sample(tmp_path, capsys):
    root = _tree(tmp_path)
    samples.sync(root)
    readme = root / "README.md"
    readme.write_text(readme.read_text("utf-8").replace(
        "Postgres advisory locks", "Postgres locks"), "utf-8")
    assert samples.check(root) == 1
    assert "out of date: README.md" in capsys.readouterr().err
    assert samples.sync(root) == 0
    assert samples.check(root) == 0


def test_sync_rewrites_a_stale_greeting_block(tmp_path):
    legacy = marks.LEGACY_GREETINGS[0]
    source = f"intro\n\n```\n{legacy}\n\nold body\n```\n\noutro\n"
    out = samples.sync_markdown("full", source, _REPO)
    assert legacy not in out
    assert out.startswith("intro\n\n```\n" + marks.GREETING)
    assert out.endswith("```\n\noutro\n")


def test_a_registered_file_without_a_sample_block_is_an_error():
    with pytest.raises(SystemExit):
        samples.sync_markdown("full", "# a page with no sample\n", _REPO)


def test_main_check_flag_reports_without_writing(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(samples, "REPO_ROOT", root)
    assert samples.main(["--check"]) == 1
    assert not (root / samples.GENERATED).exists()
    assert samples.main([]) == 0
    assert (root / samples.GENERATED).exists()
    assert samples.main(["--check"]) == 0
