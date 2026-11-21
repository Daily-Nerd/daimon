"""Is this file a session transcript of host H? One definition (#1362).

A host's project folder is shared ground: other tools write `.jsonl` logs
there. The catch-up sweep (a standalone hook, stdlib only) and the serializer
(the package) must agree on which files in that folder are sessions, so the
rule lives here, in one stdlib-only file. This file is CANONICAL; `hook/` and
`_hooks/` hold byte-identical derivatives written by `scripts/sync_hooks.py`.
Edit here, then run the script.

A file is "not a transcript" only when ALL of these hold:

  * the host is one whose naming and row markers are verified (Claude Code,
    Codex; every other host answers False, so its behaviour is unchanged);
  * its name does not fit that host's naming (`<uuid>.jsonl`,
    `rollout-<stamp>-<id>.jsonl`);
  * it has at least one row, every row parses as a JSON object with at least
    one key, and no row carries a host marker.

Any one piece of evidence that the file is real keeps it real. A drifted
transcript keeps its host's file name, and an oddly named one keeps its
marked rows, so either still fails loudly as an unreadable transcript
(scar 0045) instead of being skipped in silence.

Markers: a top-level string `type`, a `role`, or a dict `message` / `payload`.
Verified on real files: every row of 200 Claude Code and 212 Codex transcripts
carries a string `type`.
"""
import json
import re
from pathlib import Path

# #634: Codex names its rollout `rollout-<stamp>-<session-id>`. The stamp is
# matched exactly because both halves contain dashes. Owned here;
# `ledger._ROLLOUT_STEM_RE` is this object.
ROLLOUT_STEM_RE = re.compile(
    r"^rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)$")
_UUID_STEM_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE)

_NAME_PATTERNS = {
    "claude-code": _UUID_STEM_RE,
    "codex": ROLLOUT_STEM_RE,
}


def name_fits(host, path) -> bool:
    """Does the file name fit `host`'s transcript naming? False for a host
    whose naming is not verified here."""
    pattern = _NAME_PATTERNS.get(host)
    p = Path(path)
    return bool(pattern and p.suffix == ".jsonl" and pattern.match(p.stem))


def row_has_host_marker(obj) -> bool:
    """True when a parsed row looks like a row of a host's transcript."""
    if not isinstance(obj, dict):
        return False
    return (isinstance(obj.get("type"), str) or "role" in obj
            or isinstance(obj.get("message"), dict)
            or isinstance(obj.get("payload"), dict))


def not_a_transcript(path, host) -> bool:
    """True only when the file is provably not a session transcript of `host`
    (see the module docstring). Reads rows until the first marked one, so a
    real transcript costs a line or two. Never raises: any doubt is False."""
    if host not in _NAME_PATTERNS or name_fits(host, path):
        return False
    rows = 0
    try:
        with Path(path).open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    return False  # a damaged file may be a real transcript
                if not isinstance(obj, dict) or not obj:
                    return False
                if row_has_host_marker(obj):
                    return False
                rows += 1
    except OSError:
        return False
    return rows > 0
