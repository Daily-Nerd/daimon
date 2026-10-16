"""Supported test helpers for hosts that embed daimon.

This is a versioned, public surface for a host's own test harness (golden
snapshots, reproducible ledgers). `api` stays read-only and exports nothing
that writes; the way to make writes reproducible lives here instead.

    from daimon_briefing import testing

    with testing.deterministic(testing.StepClock(1_700_000_000 * 10**9)):
        ...  # open_request, rulings, amendments: same order, ts, ids every run

A sandbox gives a host test a throwaway store to render against:

    with testing.sandbox(tmp_path / "daimon", settings={"DAIMON_PROJECT_DIR": p}):
        testing.seed_checkpoint(p, testing.sample_checkpoint("a decision"))
        testing.seed_ruling(p, subject="s", verdict="never deploy on Friday")
        ...  # render: only this store and only these settings are read

The clock is carried in a ContextVar: it follows `asyncio.to_thread` but not
`threading.Thread` or `loop.run_in_executor` (see `daimon_briefing.clock`). So
are the sandbox overrides: a thread the test starts sees the real configuration.

Limits the sandbox cannot lift. The check host reads `DAIMON_DISABLE` from the
process env directly (`checks_host`), so a test process with it set gets a
kill-switched check host. A `heal_all` launched inside the sandbox starts a real
child confined to the sandbox store, but its LLM backend is whatever the
sandbox `settings` name: set a stub backend there before running a real target.
"""

import copy
import os
import secrets
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import NamedTuple

from . import config, refutations, schema, store, teamproject
from .clock import Clock, StepClock, use as deterministic
from .surfaces import Writer

__all__ = ["Clock", "StepClock", "deterministic", "Sandbox", "SandboxRequired",
           "sandbox", "sample_checkpoint", "seed_checkpoint", "seed_ruling"]

# Lives in the checkpoint dir: the seeders compare it against the dir the
# CURRENT config resolves, so a checkpoint dir re-pointed inside the sandbox
# finds no marker. Every store scan skips a file that is neither `*.json` nor
# `*.tmp` nor a directory, so it is inert there.
_MARKER = ".sandbox"


class Sandbox(NamedTuple):
    root: Path
    checkpoint_dir: Path
    log_dir: Path


class SandboxRequired(RuntimeError):
    """A seeder was called outside the sandbox that owns the store it would
    write: no sandbox is active, the checkpoint dir was re-pointed away from
    it, or `project_dir` is outside its root."""


# (the active sandbox, its marker token); a ContextVar like every override
_ACTIVE: ContextVar[tuple[Sandbox, str] | None] = ContextVar(
    "daimon_sandbox", default=None)


def _real(path) -> Path:
    return Path(os.path.realpath(path))


def _at_or_below(path: Path, base: Path) -> bool:
    return path == base or base in path.parents


def _refuse_root(root: Path) -> None:
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"sandbox root {root} is not empty")
    if _at_or_below(root, _real(Path.home())):
        raise ValueError(f"sandbox root {root} is at or below the home "
                         "directory")
    for here in (root, *root.parents):
        if (here / ".git").exists():
            raise ValueError(f"sandbox root {root} is inside a git work tree "
                             f"({here})")
    configured = (config.checkpoint_dir(), config.log_dir(), config.team_dir(),
                  config.recall_db(), config.recall_seen_dir(),
                  config.windsurf_state_dir(), config.keys_dir(),
                  config.checks_dir(), config.claude_projects_dir())
    for dir_ in configured:
        if _at_or_below(root, _real(dir_)):
            raise ValueError(f"sandbox root {root} is inside a daimon "
                             f"directory ({dir_})")


def _clear_caches() -> None:
    teamproject._cache.clear()
    config._git_toplevel.cache_clear()


@contextmanager
def sandbox(root, *, settings: Mapping[str, str] | None = None,
            clock: Clock | None = None) -> Iterator[Sandbox]:
    """A throwaway daimon store under `root`, for the calling context only.

    Refuses unless `root` is missing or empty, not at or below the home
    directory, not inside a git work tree and not inside any directory the
    current config resolves. Every home-rooted directory is redirected under
    `root`, the environment and the user's env file are not read (`settings`
    is the whole configuration). `settings` cannot move a redirected
    directory. `clock` also activates `deterministic`.
    Receipts stay off unless `settings` turns them on."""
    base = _real(Path(root).expanduser())
    _refuse_root(base)
    dirs = {
        "DAIMON_CHECKPOINT_DIR": base / "checkpoints",
        "DAIMON_LOG_DIR": base / "logs",
        "DAIMON_TEAM_DIR": base / "team",
        "DAIMON_RECALL_SEEN_DIR": base / "recall_seen",
        "DAIMON_WINDSURF_DIR": base / "windsurf",
        "DAIMON_KEYS_DIR": base / "keys",
        "DAIMON_CHECKS_DIR": base / "checks",
        "DAIMON_CLAUDE_PROJECTS_DIR": base / "claude-projects",
    }
    mapping = {str(k): str(v) for k, v in (settings or {}).items()}
    mapping.update({k: str(v) for k, v in dirs.items()})
    mapping["DAIMON_RECALL_DB"] = str(base / "recall.db")
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    sb = Sandbox(base, dirs["DAIMON_CHECKPOINT_DIR"], dirs["DAIMON_LOG_DIR"])
    token = secrets.token_hex(16)
    (sb.checkpoint_dir / _MARKER).write_text(token, encoding="utf-8")
    with ExitStack() as stack:
        stack.enter_context(config.settings_override(mapping))
        stack.enter_context(config.checkpoint_dir_override(sb.checkpoint_dir))
        stack.enter_context(config.log_dir_override(sb.log_dir))
        if clock is not None:
            stack.enter_context(deterministic(clock))
        stack.callback(_clear_caches)
        stack.callback(_ACTIVE.reset, _ACTIVE.set((sb, token)))
        _clear_caches()
        yield sb


def _require(project_dir) -> str:
    """The seeders' gate: a sandbox is active, the checkpoint dir the config
    resolves is still its own, and `project_dir` is inside its root."""
    active = _ACTIVE.get()
    if active is None:
        raise SandboxRequired("no sandbox is active: seed only inside "
                              "`testing.sandbox(...)`")
    sb, token = active
    try:
        marker = (config.checkpoint_dir() / _MARKER).read_text(encoding="utf-8")
    except OSError:
        marker = None
    if marker != token:
        raise SandboxRequired("the checkpoint dir is not the sandbox's own")
    where = _real(project_dir)
    if not _at_or_below(where, sb.root):
        raise SandboxRequired(f"project_dir {project_dir} is outside the "
                              f"sandbox root {sb.root}")
    return str(where)


def sample_checkpoint(*decisions: str, topic: str = "a seeded topic",
                      questions=()) -> dict:
    """A valid checkpoint dict (pure: nothing is written). `decisions` become
    `recent_decisions`, `questions` become `open_questions`."""
    fields = {f.kind: f for f in schema.ITEM_FIELDS}
    checkpoint: dict = {"session_id": "S-seed", "created": "2026-08-01T00:00:00Z",
                        "epistemic_snapshot": {}}

    def put(kind, value):
        field = fields[kind]
        checkpoint.setdefault(field.section, {})[field.key] = value

    put("topic", {"text": topic, "trust": "inferred"})
    put("decision", [{"text": t, "trust": "inferred"} for t in decisions])
    put("question", [{"text": t, "trust": "inferred"} for t in questions])
    return checkpoint


def seed_checkpoint(project_dir, checkpoint: dict, *,
                    session_id: str = "S-seed") -> Path:
    """Write `checkpoint` into the sandbox store the way an admission writes
    it. Raises `SandboxRequired` outside the sandbox that owns the store;
    refusals (`jsonl.Refused`) pass through, and a write the kill switch drops
    raises too: a write helper fails loudly."""
    project = _require(project_dir)
    path = store.write_checkpoint(session_id, copy.deepcopy(checkpoint),
                                  project_dir=project, writer=Writer.ADMISSION,
                                  admit=False)
    if path is None:
        raise RuntimeError("the checkpoint was not written: DAIMON_DISABLE is "
                           "set in the sandbox settings")
    return path


def seed_ruling(project_dir, *, subject: str, verdict: str,
                scope: str = "this project",
                evidence=("artifact:test-seed",)) -> str:
    """Found a RATIFIED ruling in the sandbox store, byte for byte what the
    human channel (`cli-tty`) writes. Returns the ruling id. Raises
    `SandboxRequired` outside the sandbox that owns the store;
    `RefutationError` passes through."""
    project = _require(project_dir)
    return refutations.assert_ruling(
        subject=subject, verdict=verdict, scope=scope,
        evidence=list(evidence), channel="cli-tty", ratified=True,
        project_dir=project)
