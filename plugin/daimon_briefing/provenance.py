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
from typing import TypeGuard


SOURCE_REF_VERSION = 1
QUOTE_PROVENANCE_VERSION = 1
QUOTE_VERIFIER_ID = "tier-f"
QUOTE_VERIFIER_VERSION = 1

_HOSTS = frozenset((
    "claude-code", "codex", "windsurf", "gemini", "hermes", "manual"))
_LOCATORS = frozenset(("managed", "host-api", "unsupported"))
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


def infer_host(transcript_path, *, home=None, codex_home=None,
               claude_projects=None) -> tuple[str, str]:
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
    return "manual", "unsupported"


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
    }
    return aliases.get(raw)


def capture_source_ref(session_id: str, transcript_path=None, *, author=None,
                       host_hint=None, home=None, codex_home=None,
                       claude_projects=None) -> dict | None:
    """Create code-owned capture metadata, or None for an unsafe session id."""
    if not valid_session_id(session_id):
        return None
    inferred_host, locator = infer_host(
        transcript_path, home=home, codex_home=codex_home,
        claude_projects=claude_projects)
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
                  stitching=None) -> dict | None:
    """Build a complete item receipt from code-derived inputs.

    `stitching` (#829, optional): the serializer's fragment-attribution
    verdict — {"cross_message": bool, "cross_role": bool} — recorded only
    for verified quotes where per-message attribution was possible. Absent
    means unknown (legacy receipts, transcript-less callers), never False.
    Additive under version 1: the field is optional, so every pre-#829
    receipt stays valid."""
    if not valid_source_ref(source_ref) or outcome not in _OUTCOMES:
        return None
    if binding_mode not in _BINDING_MODES:
        return None
    ids = []
    if binding_mode == "message-ids":
        for value in message_ids or ():
            if isinstance(value, str) and value and value not in ids:
                ids.append(value)
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
        "binding": {"mode": binding_mode, "message_ids": ids},
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


class SourceResolver:
    """Strict resolver over registered host roots; never guesses or falls back."""

    def __init__(self, *, home=None, codex_home=None, claude_projects=None,
                 current_author=None):
        self.home = Path(home).expanduser() if home is not None else Path.home()
        self.codex_home = (Path(codex_home).expanduser() if codex_home is not None
                           else _codex_home())
        self.claude_projects = (
            Path(claude_projects).expanduser() if claude_projects is not None
            else self.home / ".claude" / "projects")
        self.current_author = current_author
        self._indexes: dict[str, dict[str, list[Path]]] = {}

    def _roots(self, host: str) -> tuple[Path, ...] | None:
        if host == "claude-code":
            return (self.claude_projects,)
        if host == "codex":
            return (self.codex_home / "sessions",
                    self.codex_home / "archived_sessions")
        if host == "windsurf":
            return (self.home / ".windsurf" / "transcripts",
                    _daimon_windsurf_transcripts(self.home))
        return None

    def _candidates(self, source: dict) -> list[Path] | None:
        host = source["host"]
        sid = source["session_id"]
        try:
            if host == "claude-code":
                if host not in self._indexes:
                    self._indexes[host] = self._build_index(
                        self.claude_projects.glob("*/*.jsonl"))
                return list(self._indexes[host].get(sid, ()))
            if host == "codex":
                if host not in self._indexes:
                    paths: list[Path] = []
                    for root in self._roots(host) or ():
                        if root.exists():
                            paths.extend(root.rglob("*.jsonl"))
                    self._indexes[host] = self._build_index(paths)
                return list(self._indexes[host].get(sid, ()))
            if host == "windsurf":
                return [
                    self.home / ".windsurf" / "transcripts" / f"{sid}.jsonl",
                    _daimon_windsurf_transcripts(self.home) / f"{sid}.md",
                ]
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
        roots = self._roots(source["host"]) or ()
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
