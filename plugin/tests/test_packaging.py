"""Packaging metadata guards (#176). The package builds from plugin/, so the
license text must exist INSIDE the package dir for wheels/sdists to carry it —
and that copy must never drift from the repo-root original (same byte-identity
contract as the hook-shipped redact.py mirror in test_hooks_install.py)."""
import tomllib
from pathlib import Path

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
