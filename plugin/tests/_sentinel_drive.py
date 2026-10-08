"""The harness of the read census (#1132 PR 6b): run one invocation of a read
surface against the fixture world and report what came back.

Four kinds of surface, one result shape:
  cli   `cli.main(argv)` in-process, stdout/stderr captured, stdin and the
        stdout TTY answer chosen by the case
  mcp   `mcp_tools.HANDLERS[name](arguments)`, the returned ToolResult (text, then notes)
  http  a `daimon_ui.server._Handler` built without a socket (no port, no
        thread, no flake), `do_GET()` with `_send` captured
  hook  the callbacks `daimon_briefing.register(FakeCtx)` registers

Every run also diffs the files under the watched root before and after, so
bytes a read verb WRITES (usage lines, seen state, telemetry, a rebuilt index)
are scanned like output. Output printed from a thread or an atexit handler is
not captured; none of the drives start one (state in the report).
"""

import contextlib
import io
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from tests import _sentinel_world as sw


@dataclass
class Result:
    rc: object = None
    chunks: list = field(default_factory=list)      # text the surface returned
    written: list = field(default_factory=list)     # bytes the run wrote
    error: str = ""

    def text(self) -> str:
        return "\n".join(self.chunks)


class OutTty(io.StringIO):
    """A stdout whose isatty answer is chosen (the rich-renderer gate)."""

    def __init__(self, tty=False):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def snapshot_sizes(root: Path) -> dict:
    sizes = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                continue
    return sizes


def written_since(root: Path, before: dict) -> list:
    out = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        old = before.get(path)
        if old is None:
            out.append(path.read_bytes())
        elif size != old:
            data = path.read_bytes()
            out.append(data[old:] if size > old else data)
    return out


class Pristine:
    """A copy of the built world, restored after every run so the cases are
    independent (a write verb, a rebuilt index or a seen file never carries
    over)."""

    def __init__(self, root: Path, keep: Path):
        self.root, self.keep = root, keep
        if keep.exists():
            shutil.rmtree(keep)
        shutil.copytree(root, keep, symlinks=True)

    @classmethod
    def adopt(cls, root: Path, keep: Path) -> "Pristine":
        """The copy a run already made: `keep` is trusted as clean, nothing is
        copied. For a test that runs after the drive has dirtied `root`."""
        self = cls.__new__(cls)
        self.root, self.keep = root, keep
        return self

    def restore(self):
        for child in list(self.root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in self.keep.iterdir():
            if child.is_dir():
                shutil.copytree(child, self.root / child.name, symlinks=True)
            else:
                shutil.copy2(child, self.root / child.name)


@contextlib.contextmanager
def environment(env):
    with pytest.MonkeyPatch.context() as m:
        for key, value in env:
            if value is None:
                m.delenv(key, raising=False)
            else:
                m.setenv(key, value)
        yield m


def run_cli(argv, *, stdin="", stdin_tty=False, stdout_tty=False, env=()):
    from daimon_briefing import cli
    out, err = OutTty(stdout_tty), io.StringIO()
    result = Result()
    with environment(env) as m:
        m.setattr(cli.sys, "stdin", sw.TtyStdin(stdin, tty=stdin_tty))
        m.setattr("builtins.input", lambda prompt="": "y")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                result.rc = cli.main(list(argv))
            except SystemExit as exc:
                result.rc = exc.code
            except Exception as exc:  # noqa: BLE001 — recorded, judged by the caller
                result.rc = "raised"
                result.error = f"{type(exc).__name__}: {exc}"
    result.chunks = [out.getvalue(), err.getvalue()]
    return result


def run_mcp(name, arguments, *, env=()):
    from daimon_briefing import mcp_tools
    result = Result()
    with environment(env):
        try:
            got = mcp_tools.HANDLERS[name](dict(arguments))
            result.chunks = [got.text, *got.notes]
            result.rc = 0
        except Exception as exc:  # noqa: BLE001
            result.rc = "raised"
            result.error = f"{type(exc).__name__}: {exc}"
            result.chunks = [str(exc)]
    return result


def run_http(path_query, data_dir: Path, slug: str, *, env=()):
    from daimon_ui import server
    handler = object.__new__(server._Handler)
    handler.data_dir, handler.default_slug = data_dir, slug
    handler.project_label = slug
    handler.headers = {"Host": "127.0.0.1"}
    handler.path = path_query
    bodies = []
    handler._send = lambda status, ctype, body: bodies.append((status, body))
    result = Result()
    with environment(env):
        try:
            handler.do_GET()
            result.rc = bodies[0][0] if bodies else None
            result.chunks = [b.decode("utf-8", errors="replace")
                             for _s, b in bodies]
        except Exception as exc:  # noqa: BLE001
            result.rc = "raised"
            result.error = f"{type(exc).__name__}: {exc}"
    return result


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_skill(self, *args, **kwargs):
        pass


def run_hook(name, kwargs, *, env=()):
    import daimon_briefing
    ctx = FakeCtx()
    daimon_briefing.register(ctx)
    result = Result()
    with environment(env):
        try:
            got = ctx.hooks[name](**kwargs)
            result.rc = 0
            result.chunks = [repr(got)]
        except Exception as exc:  # noqa: BLE001
            result.rc = "raised"
            result.error = f"{type(exc).__name__}: {exc}"
    return result


def route_of(case_path: str) -> str:
    return urlsplit(case_path).path


def tree_root() -> Path:
    return Path(os.environ["HOME"])


assert sys  # the module patches `cli.sys`; keep the import explicit
