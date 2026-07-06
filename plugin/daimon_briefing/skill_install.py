"""Per-host skill writers (#66).

Two writer families. Owned-file: the destination is daimon's own file
(SKILL.md, daimon.mdc, daimon.md) — overwrite is safe and idempotent by
construction. Marker-block: the destination is a file the USER owns
(AGENTS.md, GEMINI.md) — daimon may only replace the
region between its own version-stamped markers, appends the block at the END
of the file (every vendor resolves instruction conflicts later-wins), and
refuses on half-broken marker state rather than guess.

Host paths verified against live docs 2026-07-03; they drift — re-verify on
failure reports, the Windsurf adapter arc caught the docs lying repeatedly.
Cursor global rules live in IDE settings, not a file: cursor is a
project-only host here.
"""

import re
from pathlib import Path

from . import __version__, skill_content

_MARK_START = "<!-- daimon:skill v{v} start -->"
_MARK_END = "<!-- daimon:skill v{v} end -->"
_START_RE = re.compile(r"<!-- daimon:skill v\S+ start -->")
_END_RE = re.compile(r"<!-- daimon:skill v\S+ end -->")
_BLOCK_RE = re.compile(
    r"<!-- daimon:skill v\S+ start -->.*?<!-- daimon:skill v\S+ end -->",
    re.DOTALL)

# host -> writer spec. "owned" writes a daimon-owned file; "block" edits a
# marker region inside a shared file. Paths are relative to home (global)
# or cwd (project); None = unsupported scope.
HOSTS = {
    "claude": {
        "global": (".claude/skills/daimon/SKILL.md", "owned", "full"),
        "project": (".claude/skills/daimon/SKILL.md", "owned", "full"),
    },
    "codex": {
        "global": (".codex/AGENTS.md", "block", "compact"),
        "project": ("AGENTS.md", "block", "compact"),
        "char_cap": 32768,  # Codex project_doc_max_bytes default — stops reading past it
    },
    "windsurf": {
        # Global skills live at ~/.codeium/windsurf/skills/<name>/SKILL.md
        # (#88 field report) — memories/global_rules.md is Windsurf's MEMORIES
        # store, which pre-#88 versions polluted with a compact block; install
        # and uninstall both clean that legacy block (see _WINDSURF_LEGACY).
        # Project scope stays a rules file: the project-level skills path is
        # unverified on a live machine.
        "global": (".codeium/windsurf/skills/daimon/SKILL.md", "owned", "full"),
        "project": (".windsurf/rules/daimon.md", "owned", "compact"),
    },
    "cursor": {
        "global": None,
        "project": (".cursor/rules/daimon.mdc", "owned", "compact"),
    },
    "gemini": {
        "global": (".gemini/GEMINI.md", "block", "compact"),
        "project": ("GEMINI.md", "block", "compact"),
    },
}

_OWNED_WRAPPERS = {
    # per (host, variant): callable(body) -> file text
    ("claude", "full"): lambda body: body,  # render_full already has frontmatter
    ("cursor", "compact"): lambda body: (
        "---\ndescription: Daimon cross-session memory protocol\n"
        f"alwaysApply: true\n---\n<!-- daimon:skill v{__version__} -->\n\n{body}"),
    ("windsurf", "compact"): lambda body: (
        f"---\ntrigger: always_on\n---\n<!-- daimon:skill v{__version__} -->\n\n{body}"),
}


class SkillInstallError(Exception):
    pass


# Pre-#88 Windsurf global installs wrote a marker block into the MEMORIES
# file. Both install and uninstall strip that stale block so an upgrade (or
# removal) never leaves skill content squatting in the memories channel.
_WINDSURF_LEGACY = ".codeium/windsurf/memories/global_rules.md"


def _clean_windsurf_legacy(home: Path) -> str | None:
    """Remove the daimon marker block from the legacy memories file if present.
    Returns a report line, or None when there is nothing to clean. Same
    lossless-boundary contract as the block uninstall path."""
    legacy = home / _WINDSURF_LEGACY
    try:
        text = legacy.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not _BLOCK_RE.search(text):
        return None
    new = _BLOCK_RE.sub("", text, count=1).rstrip("\n")
    legacy.write_text((new + "\n") if new else "", encoding="utf-8")
    return f"removed legacy daimon:skill block from {legacy} (memories, pre-#88)"


def _render(variant: str) -> str:
    return (skill_content.render_full() if variant == "full"
            else skill_content.render_compact())


def _block(variant: str) -> str:
    start = _MARK_START.format(v=__version__)
    end = _MARK_END.format(v=__version__)
    return f"{start}\n{_render(variant)}\n{end}"


def _spec(host: str, project: bool):
    spec = HOSTS.get(host)
    if spec is None:
        known = ", ".join(sorted(HOSTS))
        raise SkillInstallError(f"unknown host '{host}' (known: {known})")
    entry = spec["project"] if project else spec["global"]
    if entry is None:
        raise SkillInstallError(
            f"{host} has no global rules file (rules live in IDE settings) — "
            f"use --project inside a repo instead")
    return spec, entry


def _replace_block(text: str, block: str) -> str:
    """Insert/replace the daimon:skill marker block.

    Contract (test_uninstall_block_removes_only_block): appending to an
    existing user file and later removing the block must restore the
    user's original bytes exactly. So the append here must add exactly
    one separating blank-line boundary that uninstall can undo losslessly.
    """
    starts = len(_START_RE.findall(text))
    ends = len(_END_RE.findall(text))
    if starts == 0 and ends == 0:
        if not text:
            return f"{block}\n"
        # Ensure exactly one trailing newline on the user content, then a
        # blank line before the block — this is the boundary uninstall's
        # rstrip("\n") + "\n" restores byte-for-byte.
        base = text if text.endswith("\n") else text + "\n"
        return f"{base}\n{block}\n"
    if starts == 1 and ends == 1 and _BLOCK_RE.search(text):
        return _BLOCK_RE.sub(lambda _m: block, text, count=1)
    raise SkillInstallError(
        "broken daimon:skill markers in target file — fix or remove them "
        "manually, daimon will not guess at the boundary")


def install(host: str, *, project: bool, home: Path, cwd: Path) -> list[str]:
    spec, (rel, kind, variant) = _spec(host, project)
    dest = (cwd if project else home) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if kind == "owned":
        wrapper = _OWNED_WRAPPERS.get((host, variant), lambda b: b)
        dest.write_text(wrapper(_render(variant)), encoding="utf-8")
    else:
        old = dest.read_text(encoding="utf-8") if dest.exists() else ""
        new = _replace_block(old, _block(variant))
        dest.write_text(new, encoding="utf-8")
        cap = spec.get("char_cap")
        # Codex's documented cap is bytes (project_doc_max_bytes). Byte length
        # is also the conservative comparison if a chars-capped host ever
        # returns here — bytes >= chars for any non-ASCII text, so a byte
        # warning can only fire earlier, never later.
        new_size = len(new.encode("utf-8"))
        if cap and new_size > cap:
            lines.append(
                f"warning: {dest} is {new_size:,} bytes — {host} truncates "
                f"this file at {cap:,} bytes; trim your own rules or use --project")
    if host == "windsurf" and not project:
        cleaned = _clean_windsurf_legacy(home)
        if cleaned:
            lines.append(cleaned)
    lines.insert(0, f"installed daimon skill ({variant}) -> {dest}")
    return lines


def uninstall(host: str, *, project: bool, home: Path, cwd: Path) -> list[str]:
    _spec_dict, (rel, kind, _variant) = _spec(host, project)
    dest = (cwd if project else home) / rel
    legacy_lines = []
    if host == "windsurf" and not project:
        cleaned = _clean_windsurf_legacy(home)
        if cleaned:
            legacy_lines.append(cleaned)
    if not dest.exists():
        return legacy_lines + [f"nothing installed at {dest}"]
    if kind == "owned":
        dest.unlink()
        return legacy_lines + [f"removed {dest}"]
    text = dest.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(text):
        return [f"no daimon:skill block in {dest} — left untouched"]
    new = _BLOCK_RE.sub("", text, count=1)
    # Undo the "\n\n<block>\n" boundary _replace_block appended: strip the
    # trailing newline(s) left after removing the block, then restore a
    # single trailing newline if any content remains.
    new = new.rstrip("\n")
    dest.write_text((new + "\n") if new else "", encoding="utf-8")
    return [f"removed daimon:skill block from {dest}"]
