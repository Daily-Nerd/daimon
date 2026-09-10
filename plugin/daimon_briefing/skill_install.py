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
# Per-host install scopes. ONE value shape per entry (#842): a read cap used
# to live in here beside the scopes, which made the mapping's value type a
# union of "a scope" and "a number", and `spec["project"]` therefore something
# no caller could index without first ruling out the cap. Caps live in
# CHAR_CAPS below, keyed by host, so this table answers exactly one question.
HOSTS: dict[str, dict[str, tuple[str, str, str] | None]] = {
    "claude": {
        "global": (".claude/skills/daimon/SKILL.md", "owned", "full"),
        "project": (".claude/skills/daimon/SKILL.md", "owned", "full"),
    },
    "codex": {
        "global": (".codex/AGENTS.md", "block", "compact"),
        "project": ("AGENTS.md", "block", "compact"),
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
    # #988. Kimi Code's skill contract is Claude's, not the rules-file shape:
    # a directory-form `<scope>/skills/<name>/SKILL.md` whose frontmatter
    # `name` MUST equal the directory name, scanned from `~/.kimi-code/skills/`
    # (user) and `<repo>/.kimi-code/skills/` (project). At session start the
    # host injects only the LISTING (name plus description) and loads the body
    # when the skill is invoked, so this is description-gated like Claude and
    # takes `full` rather than the always-injected compact block. Project scope
    # is the same relative path under the repo root, which is why both rows
    # read the same and only the base directory differs.
    "kimi": {
        "global": (".kimi-code/skills/daimon/SKILL.md", "owned", "full"),
        "project": (".kimi-code/skills/daimon/SKILL.md", "owned", "full"),
    },
}

# #1000. Skills daimon ships WHOLE, as their own directory, alongside the
# generated `daimon` skill. `daimon-end` was packaged from the day it was
# written and installed by nothing: Claude Code's plugin loader discovers it at
# the plugin root, so a maintainer running from the checkout always had it,
# while everyone who installed the wheel and ran `daimon skill install` got the
# `daimon` skill alone and no way to learn `/daimon-end` exists.
#
# The wheel carries `daimon_briefing/` and nothing else, so the plugin-root
# `skills/` tree cannot be the source here. `_skills/` is a byte-identical
# mirror kept in scripts/sync_hooks.py's manifest and guarded in
# tests/test_packaging.py.
BUNDLED_SKILLS: tuple[str, ...] = ("daimon-end",)
_BUNDLED_DIR = Path(__file__).resolve().parent / "_skills"

# Per-host read caps, in BYTES. Codex documents project_doc_max_bytes and
# stops reading past it, so a file over the cap is silently half-applied.
# Absent host means no known cap, which is why the warning below tests the
# value rather than the membership.
CHAR_CAPS: dict[str, int] = {"codex": 32768}

_STAMP_RE = re.compile(r"<!-- daimon:skill v(\S+) -->")


def _stamp(version: str = __version__) -> str:
    return f"<!-- daimon:skill v{version} -->"


def _stamp_full(body: str) -> str:
    """Put the version marker AFTER the frontmatter block (#1006).

    `full` shipped as `lambda body: body`, so the file written for claude,
    windsurf global and kimi said nothing about which release wrote it, and a
    stale skill was invisible even to a person reading the artifact.

    Placement is load-bearing, not cosmetic. The directory-form hosts require
    `name` and `description` in frontmatter, and Kimi skips a skill whose
    `name` does not equal its directory: a marker landing inside or before
    that block does not warn, it silently disables the skill.
    """
    head, sep, rest = body.partition("\n---\n")
    if body.startswith("---\n") and sep:
        return f"{head}{sep}{_stamp()}\n{rest}"
    return f"{_stamp()}\n\n{body}"


_OWNED_WRAPPERS = {
    # per (host, variant): callable(body) -> file text
    ("claude", "full"): _stamp_full,  # render_full already has frontmatter
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


def _bundled_root(rel: str, variant: str) -> Path | None:
    """The host's skills directory for a directory-form row, or None.

    `full` is the DIRECTORY-FORM contract, not a density: the host scans
    `<root>/skills/<name>/SKILL.md` and loads a body only when the skill is
    invoked. `compact` rows are single rules files concatenated into every
    prompt — they have no directory to scan, so a second skill there is
    content no host would ever load. test_skill_install.py pins the shape
    these two lines derive the root from.
    """
    if variant != "full":
        return None
    return Path(rel).parent.parent


def _install_bundled(root: Path) -> list[str]:
    lines = []
    for name in BUNDLED_SKILLS:
        src = _BUNDLED_DIR / name / "SKILL.md"
        try:
            body = src.read_text(encoding="utf-8")
        except OSError as exc:  # a build that lost its package data
            raise SkillInstallError(
                f"packaged skill '{name}' is missing from this install "
                f"({src}) — reinstall daimon-briefing") from exc
        dest = root / name / "SKILL.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
        lines.append(f"installed daimon skill ({name}) -> {dest}")
    return lines


def _uninstall_bundled(root: Path) -> list[str]:
    lines = []
    for name in BUNDLED_SKILLS:
        dest = root / name / "SKILL.md"
        if not dest.exists():
            continue
        dest.unlink()
        # daimon created this directory, so it takes it back — but only while
        # it holds nothing else. Anything the user put beside the skill stays,
        # and so does the directory holding it.
        try:
            dest.parent.rmdir()
        except OSError:
            pass
        lines.append(f"removed {dest}")
    return lines


def install(host: str, *, project: bool, home: Path, cwd: Path) -> list[str]:
    spec, (rel, kind, variant) = _spec(host, project)
    dest = (cwd if project else home) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if kind == "owned":
        default = _stamp_full if variant == "full" else (lambda b: b)
        wrapper = _OWNED_WRAPPERS.get((host, variant), default)
        dest.write_text(wrapper(_render(variant)), encoding="utf-8")
    else:
        old = dest.read_text(encoding="utf-8") if dest.exists() else ""
        new = _replace_block(old, _block(variant))
        dest.write_text(new, encoding="utf-8")
        cap = CHAR_CAPS.get(host)
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
    bundled_root = _bundled_root(rel, variant)
    if bundled_root is not None:
        lines.extend(_install_bundled((cwd if project else home) / bundled_root))
    lines.insert(0, f"installed daimon skill ({variant}) -> {dest}")
    return lines


def uninstall(host: str, *, project: bool, home: Path, cwd: Path) -> list[str]:
    _spec_dict, (rel, kind, variant) = _spec(host, project)
    base = cwd if project else home
    dest = base / rel
    extra_lines = []
    if host == "windsurf" and not project:
        cleaned = _clean_windsurf_legacy(home)
        if cleaned:
            extra_lines.append(cleaned)
    bundled_root = _bundled_root(rel, variant)
    if bundled_root is not None:
        extra_lines.extend(_uninstall_bundled(base / bundled_root))
    if not dest.exists():
        return extra_lines + [f"nothing installed at {dest}"]
    if kind == "owned":
        dest.unlink()
        return extra_lines + [f"removed {dest}"]
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


# ---- drift audit (#1006) ----------------------------------------------------
#
# The hooks half of this shipped as #266: byte-hash what is installed against
# what is packaged, report CURRENT/STALE/MISSING, exit non-zero on drift. The
# skill half had nothing, while every host page said "re-run install after
# every upgrade" with no way to find out whether anyone did.
#
# Compared against a FRESH RENDER rather than a hash recorded at install time.
# Rendering is deterministic and is already the source of truth; a stored
# hash is a second one that can go stale or be lost on its own.

_STATE_DRIFTED = ("STALE", "MISSING", "BROKEN", "UNREADABLE")


def _despite_version(text: str) -> str:
    """Content with every version marker neutralized.

    The stamp records which release wrote the file, so a byte comparison would
    call every release a drift even when the protocol text did not move, and
    an audit that cries wolf on each upgrade stops being read. The VERSION is
    still reported on the row; it just does not decide the verdict.
    """
    text = _STAMP_RE.sub("<!-- daimon:skill -->", text)
    text = _START_RE.sub("<!-- daimon:skill start -->", text)
    return _END_RE.sub("<!-- daimon:skill end -->", text)


def _installed_version(text: str) -> str | None:
    m = _STAMP_RE.search(text) or re.search(r"<!-- daimon:skill v(\S+) start -->",
                                            text)
    return m.group(1) if m else None


def _row(host, scope, skill, path, state, version=None) -> dict:
    return {"host": host, "scope": scope, "skill": skill, "path": str(path),
            "state": state, "version": version,
            "drift": state in _STATE_DRIFTED}


def _audit_owned(host, scope, rel, variant, base) -> dict:
    dest = base / rel
    expected = _OWNED_WRAPPERS.get(
        (host, variant), _stamp_full if variant == "full" else (lambda b: b)
    )(_render(variant))
    if not dest.exists():
        return _row(host, scope, "daimon", dest, "NOT INSTALLED")
    try:
        actual = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _row(host, scope, "daimon", dest, "UNREADABLE")
    state = ("CURRENT" if _despite_version(actual) == _despite_version(expected)
             else "STALE")
    return _row(host, scope, "daimon", dest, state, _installed_version(actual))


def _audit_block(host, scope, rel, variant, base) -> dict:
    """A marker-block host writes into a file the USER owns. Only the region
    between daimon's markers is audited: their own rules changing is not
    daimon drift, and reporting it as such trains the reader to ignore this."""
    dest = base / rel
    if not dest.exists():
        return _row(host, scope, "daimon", dest, "NOT INSTALLED")
    try:
        text = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _row(host, scope, "daimon", dest, "UNREADABLE")
    found = _BLOCK_RE.search(text)
    if not found:
        # Half-broken markers are the state `install` REFUSES on rather than
        # guess at the boundary. An audit must never raise: it is the command
        # a person runs to find out what is wrong.
        if _START_RE.search(text) or _END_RE.search(text):
            return _row(host, scope, "daimon", dest, "BROKEN")
        return _row(host, scope, "daimon", dest, "NOT INSTALLED")
    state = ("CURRENT"
             if _despite_version(found.group(0)) == _despite_version(_block(variant))
             else "STALE")
    return _row(host, scope, "daimon", dest, state, _installed_version(text))


def _audit_bundled(host, scope, rel, base) -> list[dict]:
    root = base / Path(rel).parent.parent
    rows = []
    for name in BUNDLED_SKILLS:
        dest = root / name / "SKILL.md"
        if not dest.exists():
            # The host is installed and one of its skills is gone: a different
            # repair from "never installed here", so a different word. The
            # caller decides which applies by looking at the `daimon` row.
            rows.append(_row(host, scope, name, dest, "MISSING"))
            continue
        try:
            actual = dest.read_text(encoding="utf-8")
            expected = (_BUNDLED_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            rows.append(_row(host, scope, name, dest, "UNREADABLE"))
            continue
        rows.append(_row(host, scope, name, dest,
                         "CURRENT" if actual == expected else "STALE"))
    return rows


def audit(*, home: Path, cwd: Path) -> list[dict]:
    """Every host and scope daimon can write a skill to, and what is there."""
    report: list[dict] = []
    for host in sorted(HOSTS):
        for scope in ("global", "project"):
            entry = HOSTS[host].get(scope)
            if entry is None:
                continue
            rel, kind, variant = entry
            base = cwd if scope == "project" else home
            audit_one = _audit_owned if kind == "owned" else _audit_block
            main = audit_one(host, scope, rel, variant, base)
            report.append(main)
            if _bundled_root(rel, variant) is None:
                continue
            if main["state"] == "NOT INSTALLED":
                # Nothing was installed here, so the bundled skills are not
                # missing, they were never written. One absent host, one row.
                continue
            report.extend(_audit_bundled(host, scope, rel, base))
    return report
