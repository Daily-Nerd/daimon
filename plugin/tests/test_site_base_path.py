"""The site's base path has one source: the `baseUrl` constant in the config.

Docusaurus prefixes React links and static assets with baseUrl itself, but not
head tags, and a manifest is read by the browser, not by Docusaurus. A second
hand-written "/daimon/" in either one stays right until the site moves, then
breaks silently (icons 404, the installed app scopes the wrong path). So the
config builds head hrefs from the constant, and the manifest uses paths
relative to its own URL, which already sits under the base path.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBSITE = REPO / "website"
CONFIG = WEBSITE / "docusaurus.config.ts"
MANIFEST = WEBSITE / "static" / "site.webmanifest"

# "/daimon/" as a site path, not the tail of "Daily-Nerd/daimon/tree/...".
_BASE_PATH = re.compile(r"(?<![\w.-])/daimon(?:/|\b)")
_DECLARATION = re.compile(r"^\s*const baseUrl = '/daimon/';\s*$")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_config_writes_the_base_path_only_in_the_baseurl_declaration():
    lines = CONFIG.read_text(encoding="utf-8").splitlines()
    assert any(_DECLARATION.match(line) for line in lines), (
        "config must declare `const baseUrl = '/daimon/';` once"
    )
    stray = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(lines, 1)
        if _BASE_PATH.search(line) and not _DECLARATION.match(line)
    ]
    assert not stray, f"base path hardcoded outside the declaration: {stray}"


def test_manifest_hardcodes_no_base_path_and_uses_relative_urls():
    raw = MANIFEST.read_text(encoding="utf-8")
    assert not _BASE_PATH.search(raw), "manifest must not spell out the base path"
    data = _manifest()
    urls = [data["start_url"], data["scope"], *(i["src"] for i in data["icons"])]
    rooted = [u for u in urls if u.startswith("/") or "://" in u]
    assert not rooted, f"manifest URLs must be relative to the manifest: {rooted}"


def test_every_manifest_icon_exists_under_static():
    missing = [
        i["src"]
        for i in _manifest()["icons"]
        if not (MANIFEST.parent / i["src"]).is_file()
    ]
    assert not missing, f"manifest icons missing from website/static: {missing}"


# rel -> the head tag Docusaurus will not prefix for us.
_HEAD_RELS = ("icon", "apple-touch-icon", "manifest")


@pytest.mark.parametrize("rel", _HEAD_RELS)
def test_head_tag_exists_and_builds_its_href_from_the_baseurl_constant(rel):
    lines = [
        line
        for line in CONFIG.read_text(encoding="utf-8").splitlines()
        if f"rel: '{rel}'" in line
    ]
    assert len(lines) == 1, f"expected exactly one headTags entry with rel {rel!r}"
    assert re.search(r"href: `\$\{baseUrl\}[^`]+`", lines[0]), (
        f"rel {rel!r} href must be built from the baseUrl constant: {lines[0].strip()}"
    )


def test_social_image_exists_under_static():
    match = re.search(r"^\s*image: '([^']+)'", CONFIG.read_text(encoding="utf-8"), re.M)
    assert match, "themeConfig.image not found"
    assert (WEBSITE / "static" / match.group(1)).is_file()


# Names of retired brand files, split so this file does not match itself.
_RETIRED = re.compile(
    "emblem" + r"\.svg|wordmark(?:-dark)?" + r"\.svg|social" + "-card"
)


def test_no_repo_file_points_at_the_retired_brand_assets():
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    hits = []
    for name in files:
        path = REPO / name
        if name.endswith("package-lock.json") or not path.is_file():
            continue  # deleted in the working tree, or a lockfile
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary
        if _RETIRED.search(text):
            hits.append(name)
    assert not hits, f"retired brand assets still referenced: {hits}"
