"""A host page under docs/ is a pointer to the site, never a copy.

The repo used to carry two hand-maintained copies of the host setup guides:
one GitHub-readable, under the top-level "docs" hosts folder, and one on the
Docusaurus site, with a Spanish mirror. Only the website tree is gated by
the reader-vocabulary test and the ES-mirror rule, so it is the one that
gets edited when a host's story changes, and the top-level copies drift
silently. The top-level README was already missing the PreToolUse
pre-action hook shipped in #953 by the time this was noticed.

The fix is to stop maintaining two copies. Each top-level host page becomes
a short stub that points at the maintained website page instead of
repeating its content, so there is nothing left to drift.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOSTS_DIR = REPO / "docs" / "hosts"
WEBSITE_HOSTS_DIR = REPO / "website" / "docs" / "hosts"

# Directories this repo can contain that are never worth walking, plus the
# two trees that are allowed to reference docs/hosts/ by relative path
# (docs/hosts/ itself, and the website tree which has its own hosts/ folder
# one level down). CHANGELOG.md and .scars/ are historical record, not live
# documentation, so a stale relative link there is not a defect to fix.
_SKIP_DIRS = {REPO / d for d in (".git", ".venv", "node_modules")}
_SKIP_TREES = {HOSTS_DIR, REPO / "website", REPO / ".scars"}
_SKIP_FILES = {REPO / "CHANGELOG.md", Path(__file__).resolve()}
_TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".json", ".toml", ".txt"}

_RELATIVE_LINK = re.compile(r"(?:\.\./|\./|\b)docs/hosts/")
_URL = re.compile(r"https://\S+")


def _under_any(path, trees):
    return any(tree == path or tree in path.parents for tree in trees)


def _line_has_bare_reference(line):
    for match in _RELATIVE_LINK.finditer(line):
        if not any(
            url.start() <= match.start() and match.end() <= url.end()
            for url in _URL.finditer(line)
        ):
            return True
    return False

# stub filename -> matching website filename
PAGES = {
    "README.md": "index.md",
    "claude-code.md": "claude-code.md",
    "codex.md": "codex.md",
    "gemini.md": "gemini.md",
    "windsurf.md": "windsurf.md",
    "kimi.md": "kimi.md",
}

MAIN_LINK = re.compile(r"https://daily-nerd\.github\.io/daimon/docs/hosts/")
ES_LINK = re.compile(r"https://daily-nerd\.github\.io/daimon/es/docs/hosts/")


def _non_blank_lines(text):
    return [line for line in text.splitlines() if line.strip()]


def test_docs_hosts_pages_are_short_stubs_with_one_site_link():
    for name in PAGES:
        text = (HOSTS_DIR / name).read_text()
        lines = _non_blank_lines(text)
        assert len(lines) <= 8, (
            f"{name}: {len(lines)} non-blank lines, expected a short stub"
        )

        main_hits = MAIN_LINK.findall(text)
        assert len(main_hits) == 1, (
            f"{name}: expected exactly one link to the website host page, "
            f"found {len(main_hits)}"
        )

        es_hits = ES_LINK.findall(text)
        assert len(es_hits) == 1, (
            f"{name}: expected exactly one link to the Spanish mirror, "
            f"found {len(es_hits)}"
        )


def test_docs_hosts_pages_omit_website_section_headings():
    for stub_name, website_name in PAGES.items():
        website_text = (WEBSITE_HOSTS_DIR / website_name).read_text()
        headings = re.findall(r"^## .+$", website_text, re.MULTILINE)
        stub_text = (HOSTS_DIR / stub_name).read_text()
        for heading in headings:
            assert heading not in stub_text, (
                f"{stub_name} copies a website section heading: {heading!r}"
            )


def test_nothing_outside_docs_hosts_and_website_links_to_it_by_relative_path():
    offenders = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        if _under_any(path, _SKIP_DIRS) or _under_any(path, _SKIP_TREES):
            continue
        if path in _SKIP_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _line_has_bare_reference(line):
                offenders.append(
                    f"{path.relative_to(REPO)}:{lineno}: {line.strip()}"
                )

    assert not offenders, (
        "non-absolute reference(s) to docs/hosts/ found outside its own "
        "tree:\n" + "\n".join(offenders)
    )
