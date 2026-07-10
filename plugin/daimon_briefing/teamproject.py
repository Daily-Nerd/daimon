"""Logical team-project resolution (#200): which projects/<path…> a session's
checkpoints belong to in the shared team sidecar.

The hierarchy is ORGANIZATIONAL, not forge-derived: the team architect authors
`daimon-team.toml` at the sidecar root — the logical squad tree with repos
mapped INTO it. Daimon only READS that file; humans write and commit it, so
the no-daimon-written-mutable-shared-state invariant survives (sync still
commits own author dirs only; the config arrives through normal fetch).

    [projects."core/cosmo/dusters/finance-1"]
    repos = ["git@github.com:org/finance-svc.git",
             "https://github.com/org/finance-web"]

Resolution order for a project working dir:
    1. DAIMON_TEAM_PROJECT env — explicit per-machine intent, beats everything
    2. config mapping — normalized `git remote get-url origin` match
    3. origin-derived fallback — the origin's path portion (zero-config default)
    4. no origin resolvable → None → the legacy flat era exactly

Writes go to the WINNING tier only (resolve). Reads fan across the full
candidate set (read_candidates: winner first, then the lower tiers' paths
when they differ) — mapping or env-overriding a repo AFTER it already synced
must never orphan the history sitting under its earlier path.

Every segment is munged with the store.project_slug char rules (non-word,
non-dash → '-'), so a resolved path can never contain a separator or `..` and
cannot escape the sidecar. Broken/unreadable TOML is treated as absent (fail
open to tier 3); the parse error surfaces in `daimon team status`.

The git dependency lives HERE (the resolution/policy layer, mirroring
config.resolve_project_root) — store stays pure file-ops. Results are cached
per process per project dir: one bounded git probe per write/read path.
"""

import re
import subprocess
from pathlib import Path

try:
    import tomllib  # py3.11+ stdlib
except ModuleNotFoundError:  # py3.10: tomli is the conditional runtime dep
    import tomli as tomllib  # type: ignore[no-redef]

from . import config

CONFIG_NAME = "daimon-team.toml"
GIT_TIMEOUT = 5  # seconds — bounded; resolution must never block a write

# (project_dir, team_dir, DAIMON_TEAM_PROJECT) -> ordered candidate list,
# winner first (see read_candidates). The extra key parts keep test isolation
# honest; in a real (short-lived) process they are constant and this is the
# per-project cache the docstring promises.
_cache: dict[tuple, list[tuple[str, ...]]] = {}


def _munge_segment(seg: str) -> str:
    # Same char rules as store.project_slug (kept local — store imports this
    # module, so importing store back would cycle): every char that is not a
    # word char or '-' becomes '-'. The result can never contain a path
    # separator or a '.', so `..` and friends cannot escape the sidecar.
    return re.sub(r"[^\w-]", "-", seg)


def logical_segments(path_str) -> tuple[str, ...]:
    """Munged segments of a "/"-separated logical path; empties dropped."""
    if not path_str:
        return ()
    return tuple(
        s for s in (_munge_segment(p.strip()) for p in str(path_str).split("/")) if s
    )


def normalize_repo_url(url) -> str | None:
    """Canonical host/path form for a repo URL, so ssh/https/scp spellings of
    the same repo compare equal: scheme stripped, credentials stripped, scp
    ':' → '/', trailing '.git' and slashes stripped, lowercased."""
    s = str(url or "").strip()
    if not s:
        return None
    s = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", s)  # scheme
    s = re.sub(r"^[^/@]+@", "", s)                      # user[:pass]@
    s = s.replace(":", "/", 1)                          # scp-form host:path
    s = s.strip("/")
    if s.lower().endswith(".git"):
        s = s[: -len(".git")]
    return s.strip("/").lower() or None


def _parse_config(path: Path) -> tuple[list[tuple[tuple[str, ...], set[str]]], str | None]:
    """One config file -> ([(logical segments, normalized repo urls)], error).

    Absent file = ([], None). Broken TOML or an unreadable file = ([], message)
    — the config is treated as absent (fail open) but the error is surfaced.
    Junk shapes inside valid TOML are skipped entry-by-entry, never fatal."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [], f"{CONFIG_NAME} unreadable ({exc}) — mapping ignored"
    entries: list[tuple[tuple[str, ...], set[str]]] = []
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return [], None
    for logical, table in projects.items():
        segs = logical_segments(logical)
        repos = table.get("repos") if isinstance(table, dict) else None
        if not segs or not isinstance(repos, list):
            continue
        normalized = {n for n in (normalize_repo_url(r) for r in repos) if n}
        if normalized:
            entries.append((segs, normalized))
    return entries, None


def config_error(sidecar: Path) -> str | None:
    """Parse error for a sidecar's daimon-team.toml, or None (missing/valid).
    `daimon team status` surfaces this — a broken mapping fails open silently
    on the write path and must not stay invisible."""
    _entries, err = _parse_config(Path(sidecar) / CONFIG_NAME)
    return err


def _config_entries() -> list[tuple[tuple[str, ...], set[str]]]:
    """Mappings from every sidecar's config, fanned in across <team_dir>/*/
    (the same every-remote fan-in read_team uses). Sorted dir order so a
    (pathological) duplicate repo mapping resolves deterministically."""
    entries: list[tuple[tuple[str, ...], set[str]]] = []
    try:
        subdirs = sorted(p for p in config.team_dir().iterdir() if p.is_dir())
    except OSError:
        return []
    for d in subdirs:
        rows, _err = _parse_config(d / CONFIG_NAME)
        entries.extend(rows)
    return entries


def _origin_url(project_dir) -> str | None:
    """`git remote get-url origin` for a working dir, or None on ANY failure
    (no git, not a repo, no remote, timeout). Bounded, never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def resolve(project_dir) -> tuple[str, ...] | None:
    """Logical-path segments for a project working dir — the WRITE target —
    or None (legacy flat era). The winning tier only; readers use
    read_candidates so a later remap never strands what this wrote."""
    cands = read_candidates(project_dir)
    return cands[0] if cands else None


def read_candidates(project_dir) -> list[tuple[str, ...]]:
    """Every logical path this project's nested history may live under,
    winner first, deduped. Writes use only the winner (resolve); reads fan
    across the whole set — a repo mapped (or env-overridden) AFTER it synced
    under a lower tier's path must keep that earlier history readable, not
    orphan it. [] = nothing resolves (legacy flat era only).

    Cached per process; never raises — resolution failure must degrade to
    the flat era, not fail the write/read that asked."""
    key = (str(project_dir or ""), str(config.team_dir()),
           config.team_project() or "")
    if key in _cache:
        return _cache[key]
    try:
        result = _candidates(project_dir)
    except Exception:  # belt-and-braces: a resolver bug must not break writes
        result = []
    _cache[key] = result
    return result


def _candidates(project_dir) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = []
    # Tier 1: explicit local intent — needs no git, wins over central config.
    env = config.team_project()
    if env:
        segs = logical_segments(env)
        if segs:
            out.append(segs)
    if project_dir:
        origin = normalize_repo_url(_origin_url(project_dir))
        if origin:
            # Tier 2: the architect's mapping. Several repos may map to ONE
            # logical project — the squad-level shared pool.
            for segs, repos in _config_entries():
                if origin in repos:
                    out.append(segs)
                    break
            # Tier 3: origin-derived fallback — path portion WITHOUT the
            # host, so unmapped repos still get portable identity. Kept as a
            # read candidate even when a higher tier wins: pre-mapping
            # history lives here.
            derived = logical_segments("/".join(origin.split("/")[1:]))
            if derived:
                out.append(derived)
    # Tier 4: nothing resolved → [] → flat era. Dedupe preserves tier order.
    seen: set[tuple[str, ...]] = set()
    return [s for s in out if not (s in seen or seen.add(s))]
