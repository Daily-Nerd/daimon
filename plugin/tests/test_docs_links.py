"""Every internal link on the docs site resolves, in both languages.

The site build fails on a broken link or anchor, but only in CI and only
after a full build. This test answers the same question from the markdown
files, so a page move that orphans a link fails locally, and it also checks
what the build cannot: every redirect configured in `docusaurus.config.ts`
points at a page that exists and starts at a path that no longer does.
"""
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBSITE = REPO / "website"
TREES = {
    "en": (WEBSITE / "docs", "/docs"),
    "es": (WEBSITE / "i18n" / "es" / "docusaurus-plugin-content-docs"
           / "current", "/docs"),
}

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_EXPLICIT_ID = re.compile(r"\s*\{#([A-Za-z0-9_-]+)\}\s*$")


def _url(rel: Path) -> str:
    """The URL of a page. Docusaurus serves `index.md`, `README.md` and a
    page named like its folder (`team/team.md`) at the folder's own URL."""
    parts = list(rel.with_suffix("").parts)
    if parts[-1] in ("index", "README") or (
            len(parts) > 1 and parts[-1] == parts[-2]):
        parts.pop()
    return "/docs" + ("/" + "/".join(parts) if parts else "")


def _routes(root: Path) -> dict[str, Path]:
    """URL path (under /docs, no trailing slash) -> the file serving it."""
    return {_url(md.relative_to(root)): md for md in root.rglob("*.md")}


def _slug(text: str) -> str:
    """github-slugger, the rule Docusaurus applies to heading text."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_]{1,2}([^*_]+)[*_]{1,2}", r"\1", text)
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if not m:
            continue
        title = m.group(2)
        explicit = _EXPLICIT_ID.search(title)
        if explicit:
            anchors.add(explicit.group(1))
            continue
        base = _slug(title)
        n = seen.get(base, 0)
        seen[base] = n + 1
        anchors.add(base if n == 0 else f"{base}-{n}")
    return anchors


def _links(path: Path):
    in_fence = False
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for m in _LINK.finditer(re.sub(r"`[^`]*`", "", line)):
            yield no, m.group(1)


def _resolve(page_url: str, page: Path, root: Path, href: str):
    """(route url or None for out of scope, anchor)."""
    href, _, anchor = href.partition("#")
    if href.startswith(("http://", "https://", "mailto:")):
        return None, ""
    if not href:
        return page_url, anchor
    href = unquote(href)
    if href.startswith("/"):
        target = href.rstrip("/")
        return (target if target.startswith("/docs") else None), anchor
    if href.endswith(".md"):
        dest = (page.parent / href).resolve()
        if not dest.exists():
            return "/missing/" + href, anchor
        return _url(dest.relative_to(root.resolve())), anchor
    folder_page = page.stem in ("index", "README") or (
        page.stem == page.parent.name and page.parent != root)
    segs = page_url.split("/") if folder_page else page_url.split("/")[:-1]
    for seg in href.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            segs.pop()
        else:
            segs.append(seg)
    return "/".join(segs), anchor


@pytest.mark.parametrize("lang", sorted(TREES))
def test_every_internal_docs_link_and_anchor_resolves(lang):
    root, _ = TREES[lang]
    routes = _routes(root)
    anchors = {p: _anchors(p) for p in routes.values()}
    broken = []
    for url, page in sorted(routes.items()):
        for no, href in _links(page):
            target, anchor = _resolve(url, page, root, href)
            if target is None:
                continue
            if target not in routes:
                broken.append(f"{page.relative_to(WEBSITE)}:{no} -> {href}")
            elif anchor and anchor not in anchors[routes[target]]:
                broken.append(
                    f"{page.relative_to(WEBSITE)}:{no} -> {href} (anchor)")
    assert not broken, "\n".join(broken)


def test_the_two_languages_have_the_same_pages():
    en = set(_routes(TREES["en"][0]))
    es = set(_routes(TREES["es"][0]))
    assert en == es, (sorted(en - es), sorted(es - en))


def _redirects():
    text = (WEBSITE / "docusaurus.config.ts").read_text(encoding="utf-8")
    return re.findall(
        r"\{\s*from:\s*'([^']+)',\s*to:\s*'([^']+)',?\s*\}", text)


def test_every_redirect_points_at_a_page_and_leaves_no_page_behind():
    redirects = _redirects()
    assert redirects, "the redirects plugin has no entries to check"
    for lang in sorted(TREES):
        routes = _routes(TREES[lang][0])
        for old, new in redirects:
            assert new.rstrip("/") in routes, (lang, new)
            assert old.rstrip("/") not in routes, (lang, old)
