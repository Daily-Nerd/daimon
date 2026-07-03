"""#10: the reported version must track the packaged version — the 0.3.0 wheel
shipped introducing itself as 0.2.0 because __version__ was a hardcoded string
outside release-please's lock-step surface."""

import re
from pathlib import Path

import daimon_briefing


def _pyproject_version() -> str:
    # regex, not tomllib — the suite's floor is py3.10 which lacks tomllib.
    text = (Path(daimon_briefing.__file__).parent.parent / "pyproject.toml").read_text(
        encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    assert m, "pyproject.toml lost its version line"
    return m.group(1)


def test_dunder_version_matches_packaged_version():
    assert daimon_briefing.__version__ == _pyproject_version()
