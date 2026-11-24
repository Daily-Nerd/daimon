"""The one way a test lists repository files (#1410).

A test about repository content must read what git tracks, never whatever
happens to sit on disk: an agent worktree under `.claude/worktrees/`, a
scratch file or a build leftover would otherwise fail the test locally while
CI, which checks out tracked files only, passes. Shared helper under `tests/`
(like `_leaves.py`) so no scan keeps a private directory-skip list.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def tracked_files(root: Path = REPO):
    """Every existing file git tracks under `root`, as absolute paths.

    Paths git still tracks but the working tree deleted are dropped, so a
    caller reads only files it can open.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", str(root)],
        cwd=REPO, check=True, capture_output=True,
    ).stdout
    paths = (REPO / name.decode() for name in out.split(b"\0") if name)
    return [p for p in paths if p.is_file()]
