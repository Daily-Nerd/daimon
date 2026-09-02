"""Packaging metadata guards (#176). The package builds from plugin/, so the
license text must exist INSIDE the package dir for wheels/sdists to carry it —
and that copy must never drift from the repo-root original (same byte-identity
contract as the hook-shipped redact.py mirror in test_hooks_install.py)."""
from pathlib import Path

try:
    import tomllib  # stdlib from 3.11
except ModuleNotFoundError:  # py3.10: tomli backport (dev extra, marker-gated)
    import tomli as tomllib

_PLUGIN = Path(__file__).resolve().parents[1]
_REPO_ROOT = _PLUGIN.parent


def test_plugin_license_is_byte_identical_to_root():
    root = (_REPO_ROOT / "LICENSE").read_bytes()
    shipped = (_PLUGIN / "LICENSE").read_bytes()
    assert shipped == root, "plugin/LICENSE drifted from the repo-root LICENSE"


def test_pyproject_declares_spdx_license_and_urls():
    with open(_PLUGIN / "pyproject.toml", "rb") as f:
        meta = tomllib.load(f)["project"]
    assert meta["license"] == "Apache-2.0"  # PEP 639 SPDX expression
    urls = meta["urls"]
    assert urls["Repository"] == "https://github.com/Daily-Nerd/daimon"
    assert "Issues" in urls and "Changelog" in urls
    assert meta["keywords"]  # non-empty
    assert any(c.startswith("Programming Language :: Python :: 3")
               for c in meta["classifiers"])


def test_plugin_skills_sit_where_claude_code_discovers_them():
    """#643: `.claude-plugin/marketplace.json` sets `"source": "./"`, so the
    plugin root IS the repository root, and Claude Code reads plugin skills
    from `<plugin-root>/skills/`. They lived at `plugin/skills/`, one directory
    too deep, so neither skill was discoverable — confirmed against a real
    install at 0.28.0, whose cached tree had no `skills/` at its root.

    The reference plugin (obra/superpowers) has no `skills` key in its manifest
    and ships `<plugin-root>/skills/`; that layout, not a manifest field, is
    what the loader honours."""
    skills = _REPO_ROOT / "skills"
    assert skills.is_dir(), (
        "no skills/ at the plugin root — Claude Code cannot discover any skill")
    shipped = sorted(p.name for p in skills.iterdir()
                     if (p / "SKILL.md").exists())
    assert shipped == ["daimon-briefing", "daimon-end"], shipped
    assert not (_PLUGIN / "skills").exists(), (
        "plugin/skills/ came back; one home only, or the two drift")


def test_every_shipped_skill_declares_a_name_and_description():
    """A SKILL.md without front matter never loads, and the failure is silent
    — the skill is simply absent from the session's list."""
    for skill in sorted((_REPO_ROOT / "skills").iterdir()):
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill.name}: no front matter"
        front = text.split("---\n")[1]
        assert "name:" in front, f"{skill.name}: no name"
        assert "description:" in front, f"{skill.name}: no description"


def test_the_end_skill_names_every_store_and_says_which_surface():
    """#902: an agent uses what its loaded skill names and reads the skill
    as complete. The session-end skill taught three of seven stores, so
    durable facts went to the harness's own memory file instead. It must
    now name all seven, and be honest that a `daimon log` note is an audit
    trail nothing reads back, so the agent never routes a durable fact
    there on the strength of the name."""
    text = (_REPO_ROOT / "skills" / "daimon-end" / "SKILL.md").read_text(
        encoding="utf-8")
    for store in ("write-checkpoint", "daimon handoff", "daimon resolve",
                  "daimon log", "daimon ruling propose", "daimon amend",
                  "daimon refute"):
        assert store in text, f"daimon-end skill never names {store}"
    section = text.split("## Where things go")[1].split("\n## ")[0]
    assert "daimon log" in section
    assert "nothing reads" in section.lower()
    assert "never decay" in section.lower() or "never decays" in section.lower()


def test_the_end_skill_teaches_a_close_for_every_store_it_opens():
    """#904: step 1 taught `daimon resolve` alone, so a session that shipped
    a fix satisfying an accepted inbound request wrote its checkpoint,
    handed off, resolved its loops and left the request owed. Three such
    requests sat accepted for days after #836/#838 landed. The close step
    must be a checklist naming every close verb, and the routing table must
    carry the request row it lacked."""
    text = (_REPO_ROOT / "skills" / "daimon-end" / "SKILL.md").read_text(
        encoding="utf-8")
    step_one = text.split("## What to do when invoked")[1].split("\n2. ")[0]
    for verb in ("daimon resolve", "daimon amend", "daimon request done",
                 "daimon reverify"):
        assert verb in step_one, f"daimon-end step 1 never names {verb}"
    assert "daimon request inbox" in step_one
    table = text.split("## Where things go")[1].split("\n## ")[0]
    assert "daimon request done" in table


def test_the_briefing_skill_closing_loops_names_the_request_close():
    """#904: the plugin briefing skill's "Closing loops" section covered
    `resolve` and `reverify` and never said `request`, so the two shipped
    skills disagreed on what closing means."""
    text = (_REPO_ROOT / "skills" / "daimon-briefing" / "SKILL.md").read_text(
        encoding="utf-8")
    closing = text.split("## Closing loops")[1].split("\n## ")[0]
    assert "daimon request done" in closing
    assert "daimon request inbox" in closing
