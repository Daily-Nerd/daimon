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
Cursor global rules live in IDE settings, not a file: its global scope is a
skills-only row (no rules file, just the bundled skills).
"""

import re
from pathlib import Path
from typing import NamedTuple

from . import __version__, skill_content

_MARK_START = "<!-- daimon:skill v{v} start -->"
_MARK_END = "<!-- daimon:skill v{v} end -->"
_START_RE = re.compile(r"<!-- daimon:skill v\S+ start -->")
_END_RE = re.compile(r"<!-- daimon:skill v\S+ end -->")
_BLOCK_RE = re.compile(
    r"<!-- daimon:skill v\S+ start -->.*?<!-- daimon:skill v\S+ end -->",
    re.DOTALL)

class HostSpec(NamedTuple):
    """ONE host scope's delivery, one owner for install, uninstall and audit.

    Two facts that used to share one string (#1327) are separate fields:

    - `density` ("compact" | "full") decides ONLY how verbose the always-loaded
      protocol text is. It never decides which skills exist.
    - `skills_root` is the host's native skills directory (relative to home
      for global, to the repo for project) that daimon may write the bundled
      skills into, or None when the host cannot take them. Skill folder is the
      unit of ownership: daimon writes and removes `<skills_root>/<name>/`
      only, never the shared root.

    `invoke` is how a person actually triggers a bundled skill on this host,
    because the hosts differ and a promise of `/daimon-end` on Codex or Gemini
    is false.
    """

    # `rel` is the always-loaded rules artifact, or None when the host has no
    # file for it at this scope (Cursor global: rules live in IDE settings).
    # None is a "skills" row: only the bundled skills are delivered, and
    # install, uninstall and audit all skip the rules artifact together.
    rel: str | None
    # "owned" (daimon's own file) | "block" (marker region in a user file)
    # | "skills" (no rules file, `rel` is None)
    kind: str
    density: str  # "compact" | "full" | "none" (a skills-only row has no text)
    skills_root: str | None
    invoke: str


_SLASH = "invoke it with /daimon-end"
_DOLLAR = "invoke it with $daimon-end or pick it in /skills"
_BY_DESCRIPTION = ("Gemini activates it by its description, there is no "
                   "by-name command")

# host -> scope -> HostSpec. "owned" writes a daimon-owned file; "block" edits
# a marker region inside a shared file. Paths are relative to home (global) or
# cwd (project); None = unsupported scope (see UNSUPPORTED_SKILLS).
# ONE value shape per entry (#842): read caps live in CHAR_CAPS, keyed by host.
HOSTS: dict[str, dict[str, HostSpec | None]] = {
    "claude": {
        "global": HostSpec(".claude/skills/daimon/SKILL.md", "owned", "full",
                           ".claude/skills", _SLASH),
        "project": HostSpec(".claude/skills/daimon/SKILL.md", "owned", "full",
                            ".claude/skills", _SLASH),
    },
    # Codex scans `$HOME/.agents/skills` (user) and `<repo>/.agents/skills`
    # (repo) per its skills documentation, and invokes by `$name` or the
    # `/skills` selector. The AGENTS.md block stays compact: it is read into
    # every prompt under a byte cap.
    "codex": {
        "global": HostSpec(".codex/AGENTS.md", "block", "compact",
                           ".agents/skills", _DOLLAR),
        "project": HostSpec("AGENTS.md", "block", "compact", ".agents/skills",
                            _DOLLAR),
    },
    "windsurf": {
        # Global skills live at ~/.codeium/windsurf/skills/<name>/SKILL.md
        # (#88 field report) — memories/global_rules.md is Windsurf's MEMORIES
        # store, which pre-#88 versions polluted with a compact block; install
        # and uninstall both clean that legacy block (see _WINDSURF_LEGACY).
        # Project scope stays a rules file: the project-level skills path is
        # unverified on a live machine (see UNSUPPORTED_SKILLS).
        "global": HostSpec(".codeium/windsurf/skills/daimon/SKILL.md", "owned",
                           "full", ".codeium/windsurf/skills", _SLASH),
        "project": HostSpec(".windsurf/rules/daimon.md", "owned", "compact",
                            None, _SLASH),
    },
    "cursor": {
        "global": HostSpec(None, "skills", "none", ".cursor/skills", _SLASH),
        "project": HostSpec(".cursor/rules/daimon.mdc", "owned", "compact",
                            ".cursor/skills", _SLASH),
    },
    # Gemini activates a skill by matching its description, not by name: there
    # is no command to type, `/skills` only manages them.
    "gemini": {
        "global": HostSpec(".gemini/GEMINI.md", "block", "compact",
                           ".gemini/skills", _BY_DESCRIPTION),
        "project": HostSpec("GEMINI.md", "block", "compact", ".gemini/skills",
                            _BY_DESCRIPTION),
    },
    # #988. Kimi Code's skill contract is Claude's, not the rules-file shape:
    # a directory-form `<scope>/skills/<name>/SKILL.md` whose frontmatter
    # `name` MUST equal the directory name, scanned from `~/.kimi-code/skills/`
    # (user) and `<repo>/.kimi-code/skills/` (project). At session start the
    # host injects only the LISTING (name plus description) and loads the body
    # when the skill is invoked, so this is description-gated like Claude and
    # takes `full` rather than the always-injected compact block.
    "kimi": {
        "global": HostSpec(".kimi-code/skills/daimon/SKILL.md", "owned", "full",
                           ".kimi-code/skills", _SLASH),
        "project": HostSpec(".kimi-code/skills/daimon/SKILL.md", "owned",
                            "full", ".kimi-code/skills", _SLASH),
    },
}

# #1327. A scope that cannot take the bundled skills says so, with the reason.
# Every row of HOSTS has a `skills_root` or an entry here: silence is how
# Codex, Gemini and Cursor went without `daimon-end`. Tests pin the census.
UNSUPPORTED_SKILLS: dict[tuple[str, str], str] = {
    ("windsurf", "project"): (
        "the project-level skills path is unverified on a live machine; "
        "install the global scope instead"),
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


def _spec(host: str, project: bool) -> HostSpec:
    spec = HOSTS.get(host)
    if spec is None:
        known = ", ".join(sorted(HOSTS))
        raise SkillInstallError(f"unknown host '{host}' (known: {known})")
    entry = spec["project"] if project else spec["global"]
    if entry is None:
        raise SkillInstallError(
            f"{host} has no {'project' if project else 'global'} install — "
            f"use the other scope instead")
    return entry


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


def installed_marker(spec: HostSpec, base: Path) -> Path:
    """The path whose existence means "daimon installed this scope": the rules
    artifact when the row has one, else the first bundled skill. One answer
    for every caller that asks, so none of them indexes `spec.rel` blindly."""
    if spec.rel is not None:
        return base / spec.rel
    assert spec.skills_root is not None
    return base / spec.skills_root / BUNDLED_SKILLS[0] / "SKILL.md"


def _install_bundled(root: Path, invoke: str) -> list[str]:
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
        lines.append(f"installed daimon skill ({name}) -> {dest} ({invoke})")
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
    spec = _spec(host, project)
    rel, kind, variant = spec.rel, spec.kind, spec.density
    base = cwd if project else home
    lines = []
    dest = base / rel if rel is not None else None
    if dest is None:
        pass
    elif kind == "owned":
        dest.parent.mkdir(parents=True, exist_ok=True)
        default = _stamp_full if variant == "full" else (lambda b: b)
        wrapper = _OWNED_WRAPPERS.get((host, variant), default)
        dest.write_text(wrapper(_render(variant)), encoding="utf-8")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
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
    if spec.skills_root is not None:
        lines.extend(_install_bundled(base / spec.skills_root, spec.invoke))
    if dest is None:
        lines.insert(0, f"{host}: skills only, no rules file at this scope "
                        f"(its rules live in IDE settings)")
    else:
        lines.insert(0, f"installed daimon skill ({variant}) -> {dest}")
    return lines


def uninstall(host: str, *, project: bool, home: Path, cwd: Path) -> list[str]:
    spec = _spec(host, project)
    base = cwd if project else home
    dest = base / spec.rel if spec.rel is not None else None
    kind = spec.kind
    extra_lines = []
    if host == "windsurf" and not project:
        cleaned = _clean_windsurf_legacy(home)
        if cleaned:
            extra_lines.append(cleaned)
    if spec.skills_root is not None:
        extra_lines.extend(_uninstall_bundled(base / spec.skills_root))
    if dest is None:
        if extra_lines:
            return extra_lines
        assert spec.skills_root is not None
        return [f"nothing installed at {base / spec.skills_root}"]
    if not dest.exists():
        return extra_lines + [f"nothing installed at {dest}"]
    if kind == "owned":
        dest.unlink()
        return extra_lines + [f"removed {dest}"]
    text = dest.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(text):
        return extra_lines + [f"no daimon:skill block in {dest} — left untouched"]
    new = _BLOCK_RE.sub("", text, count=1)
    # Undo the "\n\n<block>\n" boundary _replace_block appended: strip the
    # trailing newline(s) left after removing the block, then restore a
    # single trailing newline if any content remains.
    new = new.rstrip("\n")
    dest.write_text((new + "\n") if new else "", encoding="utf-8")
    return extra_lines + [f"removed daimon:skill block from {dest}"]


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

# LEFTOVER drifts on purpose (#1008). Two copies of one skill arriving from
# two channels is a real misconfiguration whose repair is a single command, so
# a provisioning gate should catch it. PLUGIN on its own is a healthy machine
# and stays out of the count.
_STATE_DRIFTED = ("STALE", "MISSING", "BROKEN", "UNREADABLE", "LEFTOVER")


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


def _row(host, scope, skill, path, state, version=None,
         channel="settings") -> dict:
    return {"host": host, "scope": scope, "skill": skill, "path": str(path),
            "state": state, "version": version, "channel": channel,
            "drift": state in _STATE_DRIFTED}


def _audit_owned(host, scope, spec, base) -> dict:
    variant = spec.density
    assert spec.rel is not None
    dest = base / spec.rel
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


def _audit_block(host, scope, spec, base) -> dict:
    """A marker-block host writes into a file the USER owns. Only the region
    between daimon's markers is audited: their own rules changing is not
    daimon drift, and reporting it as such trains the reader to ignore this."""
    variant = spec.density
    assert spec.rel is not None
    dest = base / spec.rel
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


def _audit_bundled(host, scope, spec, base,
                   absent: str = "MISSING") -> list[dict]:
    assert spec.skills_root is not None
    root = base / spec.skills_root
    rows = []
    for name in BUNDLED_SKILLS:
        dest = root / name / "SKILL.md"
        if not dest.exists():
            # The host is installed and one of its skills is gone: a different
            # repair from "never installed here", so a different word. The
            # caller decides which applies by looking at the `daimon` row.
            rows.append(_row(host, scope, name, dest, absent))
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


def _plugin_hosts(home: Path) -> set[str]:
    """Hosts served through a channel daimon's own install verbs do not write.

    ONE resolver, shared with `host_detect` (#1008). Detection learned the
    plugin channel in #1001 and this audit shipped in #1006 without it, so the
    two commands held different answers to "who serves this host" and the
    audit handed out a repair the installer declines to perform.

    User-level only: the plugin is a home-scoped install, so a repo-scoped
    skill is daimon's own file wherever the plugin sits.
    """
    from . import host_detect

    try:
        return {"claude"} if host_detect.plugin_serves_claude(home) else set()
    except Exception:  # noqa: BLE001 - an audit never crashes on a foreign file
        return set()


def audit(*, home: Path, cwd: Path) -> list[dict]:
    """Every host and scope daimon can write a skill to, and what is there."""
    report: list[dict] = []
    plugin_hosts = _plugin_hosts(home)
    for host in sorted(HOSTS):
        for scope in ("global", "project"):
            entry = HOSTS[host].get(scope)
            if entry is None:
                continue
            base = cwd if scope == "project" else home
            if entry.rel is None:
                # Skills-only row: the skill is the ONLY artifact, so there is
                # no `daimon` row to be missing a sibling of. Absent means
                # never installed here, which is not drift.
                report.extend(_audit_bundled(host, scope, entry, base,
                                             absent="NOT INSTALLED"))
                continue
            audit_one = _audit_owned if entry.kind == "owned" else _audit_block
            main = audit_one(host, scope, entry, base)
            if host in plugin_hosts and scope == "global":
                # Not a content verdict: the file's freshness is not the
                # question when another channel owns the skill. Either the
                # plugin serves it and there is nothing here, or an older CLI
                # install left a copy behind that now duplicates it.
                main["channel"] = "plugin"
                main["state"] = ("PLUGIN" if main["state"] == "NOT INSTALLED"
                                 else "LEFTOVER")
                main["drift"] = main["state"] in _STATE_DRIFTED
            report.append(main)
            if entry.skills_root is None:
                continue
            if main["state"] in ("NOT INSTALLED", "PLUGIN", "LEFTOVER"):
                # Nothing daimon should be maintaining here, so the bundled
                # skills are not missing. A `daimon-end MISSING` row conjured
                # under a leftover sent the reader to create a SECOND copy
                # beside whatever the plugin ships.
                continue
            report.extend(_audit_bundled(host, scope, entry, base))
    return report
