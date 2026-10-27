"""Durable quote provenance and strict transcript source resolution (#594).

Checkpoint ``source_ref`` describes the source captured by that checkpoint.
Item ``quote_provenance`` snapshots the complete evidence receipt so carried
items do not depend on their original checkpoint surviving retention GC.

This module deliberately knows nothing about checkpoint or transcript parsing.
It validates/stamps plain data and resolves a source to at most one local path;
callers own parsing and quote matching.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TypeGuard


SOURCE_REF_VERSION = 1
QUOTE_PROVENANCE_VERSION = 1
QUOTE_VERIFIER_ID = "tier-f"
QUOTE_VERIFIER_VERSION = 1

# The public host enumeration, in the order public surfaces list hosts (the
# import capability table, the plan's "not discovered" line). `manual` is
# not a host a transcript comes from: it names a file outside every
# registered root. `_HOSTS` is the same set for membership tests.
HOSTS = ("claude-code", "codex", "windsurf", "kimi", "gemini", "hermes", "manual")
_HOSTS = frozenset(HOSTS)
# #1064: Kimi was captured for several releases before it was a registered
# host here, so every checkpoint it produced was stamped host "manual",
# locator "unsupported". This module resolves a source ref at READ time and
# never rewrites one already on disk, so those older checkpoints stay
# exactly as recorded; only a NEW capture (or a resolve of a checkpoint that
# already carries host "kimi") benefits from the roots and aliases below.
_LOCATORS = frozenset(("managed", "host-api", "unsupported"))
# #1236: the hosts whose transcripts record no project directory, the one
# capability row the import consults beside the roots `infer_host` knows.
# Windsurf writes none in either of its roots; Kimi's wire.jsonl has no
# measured cwd field (design section 7). A path-given import of one of these
# needs the project named on the command line, since nothing in the file can
# confirm it. Claude Code rows and the Codex session_meta row carry a cwd,
# so neither belongs here; a host added to `infer_host` with no cwd field
# joins this row, never a literal in the importer.
HOSTS_RECORDING_NO_PROJECT = frozenset(("windsurf", "kimi"))
# #1236: why each host with no registered root (`SourceResolver.roots`
# answers None) cannot be imported, by discovery or by `--transcript`. One
# entry per such host; the import capability table refuses to build for a
# rootless host missing here. Gemini CLI does write a transcript (its
# SessionEnd hook passes `transcript_path`; gemini-cli v0.21.0 fixed the
# empty stub of gemini-cli#14715), but this resolver registers no root for
# it, so `infer_host` classes the file as `manual` and nothing in it names
# a project. Hermes has no transcript file at all (host-api locator).
NO_ROOT_REASONS = {
    "gemini": "no registered root",
    "hermes": "no transcript file",
}
# Gemini CLI's config home, the directory its hooks and settings live in
# (`host_detect` probes the same name, `skill_install` writes GEMINI.md
# under it). Not a transcript root: it is consulted only to NAME a file
# as Gemini's when someone hands it to the import.
_GEMINI_HOME = ".gemini"
_OUTCOMES = frozenset(("verified", "not-verified"))
_BINDING_MODES = frozenset(("message-ids", "transcript-scan"))
_DIGEST_SCOPES = frozenset(("raw-file", "rendered-transcript"))
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceResolution:
    """One strict resolver decision. ``path`` is internal and never rendered."""

    state: str
    path: Path | None = None


def valid_session_id(value) -> TypeGuard[str]:
    """Bounded, glob/path-safe host session identifier.

    A TypeGuard rather than a plain bool (#842): the body already establishes
    `isinstance(value, str)`, and every caller reads a session id out of an
    untyped checkpoint dict and passes it straight into something that wants
    a str. Returning bool made each of those call sites re-prove, or skip
    proving, what this function had just checked."""
    return isinstance(value, str) and _SESSION_RE.fullmatch(value) is not None


def _daimon_windsurf_transcripts(home: Path) -> Path:
    """The daimon-authored Windsurf transcript root, resolved the same way
    the hook that writes it and the purge that deletes it resolve it (#607).
    Hardcoding home here would put a THIRD component out of step with the
    other two: with DAIMON_WINDSURF_DIR set, every Windsurf receipt would
    report absent-local over a transcript sitting on disk. `home` stays a
    parameter so the injected-home tests keep working when the var is
    unset."""
    raw = (os.environ.get("DAIMON_WINDSURF_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser() / "transcripts"
    return home / ".daimon" / "windsurf" / "transcripts"


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.expanduser().resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".codex"


def _kimi_home() -> Path:
    """Kimi's config directory, same env var and default as the Kimi hooks
    and installer already use (`daimon_briefing.kimi_hooks.config_home`,
    `_daimon_hook_lib.kimi_home`): `KIMI_CODE_HOME` when set, else
    `~/.kimi-code`. Kept a free function, mirroring `_codex_home`, so the
    default resolves from the real environment unless a caller injects
    `kimi_home` explicitly."""
    raw = os.environ.get("KIMI_CODE_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".kimi-code"


def infer_host(transcript_path, *, home=None, codex_home=None,
               claude_projects=None, kimi_home=None) -> tuple[str, str]:
    """Infer a supported host only from registered transcript roots."""
    if transcript_path is None:
        return "manual", "unsupported"
    path = Path(transcript_path).expanduser()
    home = Path(home).expanduser() if home is not None else Path.home()
    claude_root = (Path(claude_projects).expanduser()
                    if claude_projects is not None
                    else home / ".claude" / "projects")
    if _under(path, claude_root):
        return "claude-code", "managed"
    codex_home = (Path(codex_home).expanduser() if codex_home is not None
                  else _codex_home())
    if (_under(path, codex_home / "sessions")
            or _under(path, codex_home / "archived_sessions")):
        return "codex", "managed"
    if (_under(path, home / ".windsurf" / "transcripts")
            or _under(path, _daimon_windsurf_transcripts(home))):
        return "windsurf", "managed"
    kimi_home = (Path(kimi_home).expanduser() if kimi_home is not None
                 else _kimi_home())
    # Kimi's session id is a DIRECTORY name nested under an unpredictable
    # workspace-hash directory (`sessions/wd_<dir>_<hex>/<session id>/agents/
    # main/wire.jsonl`, measured 0.42.0); `_under` only needs the shared
    # `sessions` root, the same way codex only needs `sessions`/
    # `archived_sessions` and never the intervening date directories.
    if _under(path, kimi_home / "sessions"):
        return "kimi", "managed"
    return "manual", "unsupported"


def unsupported_import_host(transcript_path, *, home=None) -> str | None:
    """The host a file belongs to when that host has no registered root
    (`NO_ROOT_REASONS`), or None (#1236). `infer_host` answers `manual`
    for such a file, which is right for a receipt (nothing can resolve it)
    and wrong for a refusal line, which should name the host. Today the
    one detectable case is Gemini: a path under `~/.gemini`, the config
    home Gemini CLI keeps everything it writes in. Hermes writes no file,
    so it is never detected. Never raises."""
    if transcript_path is None:
        return None
    path = Path(transcript_path).expanduser()
    home = Path(home).expanduser() if home is not None else Path.home()
    if _under(path, home / _GEMINI_HOME):
        return "gemini"
    return None


def session_key(transcript_path, *, host: str) -> str:
    """The session id a transcript names on `host`, the id a serialize of
    it must run under (#1236).

    Every host adapted before Kimi names the file for its session, so the
    stem is the id (a Codex rollout stem is its own id too: the ledger
    folds it to the bare id on its side, `ledger._session_key`). Kimi's
    file is always `wire.jsonl` under `sessions/wd_<dir>_<hash>/<session
    id>/agents/main/`, so the id is the directory segment three levels up,
    the segment the Kimi glob in `SourceResolver._candidates` matches and
    the hook's own lookup walks (scar 0046, scar 0061: never the stem).
    `host` is `infer_host`'s answer for the same path, so the two readers
    cannot disagree about which layout applies."""
    path = Path(transcript_path).expanduser()
    if host == "kimi" and path.name == "wire.jsonl" and len(path.parents) > 3:
        return path.parents[2].name
    return path.stem


def normalize_host(value) -> str | None:
    """Normalize trusted hook hints to the public host enum."""
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().replace("_", "-")
    aliases = {
        "claude": "claude-code",
        "claude-code": "claude-code",
        "codex": "codex",
        "windsurf": "windsurf",
        "windsurf-cascade": "windsurf",
        "gemini": "gemini",
        "gemini-cli": "gemini",
        "hermes": "hermes",
        "manual": "manual",
        "kimi": "kimi",
        "kimi-code": "kimi",
    }
    return aliases.get(raw)


def capture_source_ref(session_id: str, transcript_path=None, *, author=None,
                       host_hint=None, home=None, codex_home=None,
                       claude_projects=None, kimi_home=None) -> dict | None:
    """Create code-owned capture metadata, or None for an unsafe session id."""
    if not valid_session_id(session_id):
        return None
    inferred_host, locator = infer_host(
        transcript_path, home=home, codex_home=codex_home,
        claude_projects=claude_projects, kimi_home=kimi_home)
    hinted = normalize_host(host_hint)
    host = hinted or inferred_host
    if transcript_path is None and host == "hermes":
        locator = "host-api"
    elif inferred_host == "manual":
        # A hint identifies the producer, but an arbitrary path is not a
        # registered locator and must never be presented as resolvable.
        locator = "unsupported"
    source = {
        "version": SOURCE_REF_VERSION,
        "host": host,
        "session_id": session_id,
        "locator": locator,
    }
    if isinstance(author, str) and author.strip():
        source["author"] = author.strip()
    return source


def valid_source_ref(value) -> TypeGuard[dict]:
    """A TypeGuard for the same reason valid_session_id is one (#842): the
    body opens with isinstance(value, dict), and every caller reads a source
    ref out of an untyped checkpoint and then subscripts it."""
    if not isinstance(value, dict) or value.get("version") != SOURCE_REF_VERSION:
        return False
    if value.get("host") not in _HOSTS or value.get("locator") not in _LOCATORS:
        return False
    if not valid_session_id(value.get("session_id")):
        return False
    author = value.get("author")
    return author is None or (isinstance(author, str) and bool(author.strip()))


def source_digest(transcript_hash, rendered_transcript: str) -> dict:
    """Prefer the raw-file digest; otherwise bind the verified render bytes."""
    if isinstance(transcript_hash, str):
        raw = transcript_hash.strip().lower()
        if _SHA256_RE.fullmatch(raw):
            return {"algorithm": "sha256", "scope": "raw-file", "value": raw}
    rendered = rendered_transcript.encode("utf-8")
    return {
        "algorithm": "sha256",
        "scope": "rendered-transcript",
        "value": hashlib.sha256(rendered).hexdigest(),
    }


def quote_receipt(source_ref, digest, *, outcome: str, checked_at: str,
                  binding_mode: str, message_ids=(),
                  stitching=None, roles=None) -> dict | None:
    """Build a complete item receipt from code-derived inputs.

    `stitching` (#829, optional): the serializer's fragment-attribution
    verdict — {"cross_message": bool, "cross_role": bool} — recorded only
    for verified quotes where per-message attribution was possible. Absent
    means unknown (legacy receipts, transcript-less callers), never False.
    Additive under version 1: the field is optional, so every pre-#829
    receipt stays valid.

    `roles` (#1237, optional): the sorted, de-duplicated transcript roles
    behind a verified quote, derived by the serializer from the same
    per-message views verification matched against: for a message-ids
    binding, the roles of the messages `message_ids` names; for a
    transcript-scan binding, the channels that can account for the quote
    (serializer._scan_roles). Stored under `binding.roles`. Absent means
    the receipt predates the field or was stamped with no per-message
    transcript to read, never "no role". Role is the message channel (user,
    assistant, tool), never a human identity: `daimon serialize <file>`
    turns any file into one user-role message, and `stated_by` is the
    field that names a person."""
    if not valid_source_ref(source_ref) or outcome not in _OUTCOMES:
        return None
    if binding_mode not in _BINDING_MODES:
        return None
    ids = []
    if binding_mode == "message-ids":
        for value in message_ids or ():
            if isinstance(value, str) and value and value not in ids:
                ids.append(value)
    binding: dict = {"mode": binding_mode, "message_ids": ids}
    if roles is not None:
        # Same reject-not-coerce posture as stitching below: the raw value is
        # copied through for valid_quote_receipt to refuse, never sorted or
        # de-duplicated here into a list the serializer did not derive.
        binding["roles"] = (list(roles) if isinstance(roles, (list, tuple))
                            else roles)
    receipt = {
        "version": QUOTE_PROVENANCE_VERSION,
        "source": dict(source_ref),
        "digest": dict(digest),
        "verifier": {
            "id": QUOTE_VERIFIER_ID,
            "version": QUOTE_VERIFIER_VERSION,
        },
        "outcome": outcome,
        "checked_at": checked_at,
        "binding": binding,
    }
    if stitching is not None:
        # #831 review: reject-not-coerce. The raw value is copied through for
        # the trailing valid_quote_receipt gate to refuse — never coerced
        # into a verdict the code did not derive (bool() would launder any
        # truthy junk into True).
        receipt["stitching"] = (dict(stitching) if isinstance(stitching, dict)
                                else stitching)
    return receipt if valid_quote_receipt(receipt) else None


def valid_quote_receipt(value) -> TypeGuard[dict]:
    """A TypeGuard, matching the two validators above (#842). carry's
    _capture_verified is the case that shows why: it calls this and then
    immediately reads receipt["outcome"], which only holds because this
    function proved the dict and then discarded the proof at its return."""
    if not isinstance(value, dict) or value.get("version") != QUOTE_PROVENANCE_VERSION:
        return False
    if not valid_source_ref(value.get("source")):
        return False
    digest = value.get("digest")
    if not isinstance(digest, dict):
        return False
    if (digest.get("algorithm") != "sha256"
            or digest.get("scope") not in _DIGEST_SCOPES
            or not isinstance(digest.get("value"), str)
            or _SHA256_RE.fullmatch(digest["value"]) is None):
        return False
    verifier = value.get("verifier")
    if not isinstance(verifier, dict):
        return False
    if (verifier.get("id") != QUOTE_VERIFIER_ID
            or not isinstance(verifier.get("version"), int)
            or isinstance(verifier.get("version"), bool)
            or verifier["version"] < 1):
        return False
    if value.get("outcome") not in _OUTCOMES:
        return False
    checked = value.get("checked_at")
    if not isinstance(checked, str):
        return False
    try:
        datetime.strptime(checked, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    binding = value.get("binding")
    if not isinstance(binding, dict) or binding.get("mode") not in _BINDING_MODES:
        return False
    ids = binding.get("message_ids")
    if (not isinstance(ids, list) or len(ids) > 64
            or any(not isinstance(v, str) or not v or len(v) > 256 for v in ids)):
        return False
    if len(ids) != len(set(ids)):
        return False
    if "roles" in binding:
        # #1237: optional (absent = pre-field or transcript-less), but
        # present means exactly what the serializer derives: a non-empty
        # list of distinct non-empty strings in sorted order, on either
        # binding mode, and on a message-ids binding never more roles than
        # messages, since every role there comes from one of the bound
        # messages. Explicit null is NOT "absent".
        roles = binding["roles"]
        if not isinstance(roles, list) or not roles:
            return False
        if binding["mode"] == "message-ids" and len(roles) > len(ids):
            return False
        if any(not isinstance(r, str) or not r or len(r) > 64 for r in roles):
            return False
        if roles != sorted(set(roles)):
            return False
    if "stitching" in value:
        # #829/#831: optional (absent = unknown on every pre-D-019 receipt),
        # but present means the published contract's exact shape: verified
        # receipts only (a not-verified check matched no fragments to
        # attribute), an object — explicit null is NOT "absent" — and
        # exactly boolean verdicts, so truthy junk can never read as an
        # attribution the code derived.
        stitching = value["stitching"]
        if value.get("outcome") != "verified":
            return False
        if not isinstance(stitching, dict):
            return False
        for key in ("cross_message", "cross_role"):
            if not isinstance(stitching.get(key), bool):
                return False
    return binding["mode"] != "transcript-scan" or not ids


def binding_message_ids(receipt) -> list[str]:
    """Authoritative message binding from a valid receipt."""
    if not valid_quote_receipt(receipt):
        return []
    binding = receipt["binding"]
    return list(binding["message_ids"]) if binding["mode"] == "message-ids" else []


def binding_roles(receipt) -> list[str] | None:
    """The roles behind a valid receipt's quote (#1237), or None when the
    receipt is invalid or carries no role list (it predates the field, or
    was stamped with no per-message transcript). None is "daimon cannot
    tell you", never an empty role list."""
    if not valid_quote_receipt(receipt):
        return None
    binding = receipt["binding"]
    if "roles" not in binding:
        return None
    return list(binding["roles"])


# One glob per root, index-aligned with `SourceResolver.roots(host)`: the
# on-disk layout `_candidates` always matched. Claude Code `<projects>/
# <slug>/<session>.jsonl`, exactly one directory deep: never a loose file
# at the root, and never a sidechain log below a session directory
# (`<slug>/<session>/subagents/agent-<id>.jsonl`), which is not a session
# of its own; Codex
# any `.jsonl` anywhere below `sessions` and `archived_sessions` (the date
# directories between are not part of the contract); Windsurf `<id>.jsonl`
# in its own root and `<id>.md` in daimon's; Kimi the wire log under every
# workspace and session directory (scar 0061: the file is always
# `wire.jsonl`, the session is the directory). A host with roots and no
# entry here, or with a pattern count that differs from its root count, is
# refused by `transcripts` rather than listed as empty.
_LISTINGS: dict[str, tuple[str, ...]] = {
    "claude-code": ("*/*.jsonl",),
    "codex": ("**/*.jsonl", "**/*.jsonl"),
    "windsurf": ("*.jsonl", "*.md"),
    "kimi": ("*/*/agents/main/wire.jsonl",),
}


class Listing(NamedTuple):
    """What `SourceResolver.transcripts` found: the files, sorted, and the
    roots it could not list."""

    files: list[Path]
    unreadable: tuple[Path, ...]


class SourceResolver:
    """Strict resolver over registered host roots; never guesses or falls back."""

    def __init__(self, *, home=None, codex_home=None, claude_projects=None,
                 kimi_home=None, current_author=None):
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self.codex_home = (Path(codex_home).expanduser() if codex_home is not None
                           else _codex_home())
        self.claude_projects = (
            Path(claude_projects).expanduser() if claude_projects is not None
            else self.home / ".claude" / "projects")
        self.kimi_home = (Path(kimi_home).expanduser() if kimi_home is not None
                          else _kimi_home())
        self.current_author = current_author
        self._indexes: dict[str, dict[str, list[Path]]] = {}

    def roots(self, host: str) -> tuple[Path, ...] | None:
        """The registered transcript roots of `host`, or None for a host
        with no registered root: Gemini (it writes a transcript, but under
        no directory this resolver knows), Hermes (no transcript file) and
        `manual` (a file outside every root). The one table of per-host
        roots: `infer_host` reads the same directories, `transcripts` lists
        them, `resolve` confines a candidate to them, and the import
        capability table asks this to tell a rootless host (its reason is
        in `NO_ROOT_REASONS`) from a path-given one."""
        if host == "claude-code":
            return (self.claude_projects,)
        if host == "codex":
            return (self.codex_home / "sessions",
                    self.codex_home / "archived_sessions")
        if host == "windsurf":
            return (self.home / ".windsurf" / "transcripts",
                    _daimon_windsurf_transcripts(self.home))
        if host == "kimi":
            return (self.kimi_home / "sessions",)
        return None

    def transcripts(self, host: str) -> Listing:
        """Every transcript file under `host`'s registered roots, sorted,
        beside the roots that could not be listed; an empty listing for a
        host with no root (#1236).

        The one lister over `roots`: `daimon import` discovers a project's
        sessions through it, and `_candidates` builds its stem index from
        the same files for the two hosts it indexes, so the files the
        import can find and the files a receipt can resolve to are one set.
        The pattern per root is `_LISTINGS`; a host with roots and no
        pattern raises `LookupError`, so a host added to `roots` cannot
        read as "nothing on disk". A missing root and a root that is a
        file list as empty (`Path.glob` returns nothing for both); a root
        under a parent this user cannot read raises `PermissionError` out
        of `glob` on Python 3.10 (3.13 returns nothing), so each root is
        guarded on its own and named in `unreadable`, and the other roots
        of the host still list.

        Codex only: a rollout present under both `sessions` and
        `archived_sessions` is one session that Codex moved (it archives
        by moving the file), so the stem lists once, from `sessions`, the
        root that comes first. For every other host each root is its own
        writer (Windsurf's own log and daimon's hook log can both exist for
        one session) and nothing is folded."""
        roots = self.roots(host)
        if roots is None:
            return Listing([], ())
        patterns = _LISTINGS.get(host)
        if patterns is None or len(patterns) != len(roots):
            raise LookupError(
                f"host {host} has {len(roots)} transcript root(s) and "
                f"{0 if patterns is None else len(patterns)} listing pattern(s)"
            )
        files: list[Path] = []
        unreadable: list[Path] = []
        stems: set[str] = set()
        for root, pattern in zip(roots, patterns):
            try:
                found = sorted(root.glob(pattern))
            except OSError:
                unreadable.append(root)
                continue
            if host == "codex":
                found = [p for p in found if p.stem not in stems]
                stems.update(p.stem for p in found)
            files.extend(found)
        return Listing(sorted(files), tuple(unreadable))

    def _candidates(self, source: dict) -> list[Path] | None:
        host = source["host"]
        sid = source["session_id"]
        try:
            if host in ("claude-code", "codex"):
                if host not in self._indexes:
                    self._indexes[host] = self._build_index(
                        self.transcripts(host).files)
                return list(self._indexes[host].get(sid, ()))
            if host == "windsurf":
                return [
                    self.home / ".windsurf" / "transcripts" / f"{sid}.jsonl",
                    _daimon_windsurf_transcripts(self.home) / f"{sid}.md",
                ]
            if host == "kimi":
                # scar 0061: never key this on the host payload id directly
                # without walking the real on-disk layout — here the payload
                # session_id IS the directory segment, but the file itself is
                # always named "wire.jsonl", so a stem-keyed index (the
                # claude-code/codex approach) would collide every Kimi
                # session onto the same stem. Glob the session-id segment
                # instead, exactly like `_daimon_hook_lib.kimi_transcript`
                # does for the hook's own capture-time lookup, so hook and
                # CLI resolve the same session to the same path.
                return sorted(
                    (self.kimi_home / "sessions").glob(
                        f"*/{sid}/agents/main/wire.jsonl"))
        except OSError:
            return []
        return None

    @staticmethod
    def _build_index(paths) -> dict[str, list[Path]]:
        index: dict[str, list[Path]] = {}
        for path in paths:
            index.setdefault(path.stem, []).append(path)
        return index

    def resolve(self, source) -> SourceResolution:
        if not valid_source_ref(source):
            return SourceResolution("unsupported")
        if source["locator"] != "managed":
            return SourceResolution("unsupported")
        candidates = self._candidates(source)
        if candidates is None:
            return SourceResolution("unsupported")
        roots = self.roots(source["host"]) or ()
        existing = []
        seen = set()
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                resolved_path = path.resolve()
                if not any(_under(resolved_path, root) for root in roots):
                    continue
                key = str(resolved_path)
            except (OSError, RuntimeError):
                continue
            if key not in seen:
                seen.add(key)
                existing.append(path)
        if len(existing) > 1:
            return SourceResolution("ambiguous")
        if not existing:
            author = source.get("author")
            if (isinstance(author, str) and author.strip()
                    and author.strip().lower() != "unknown"
                    and isinstance(self.current_author, str)
                    and self.current_author.strip()
                    and self.current_author.strip().lower() != "unknown"
                    and author.strip() != self.current_author.strip()):
                return SourceResolution("remote-author")
            return SourceResolution("absent-local")
        path = existing[0]
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError:
            return SourceResolution("unreadable")
        return SourceResolution("resolved", path)
