"""The machine-move runbook, run end to end (#1384 slice 6).

`website/docs/guides/move-to-a-new-machine.md` tells a person which commands to
run on the old machine and on the new one. This test runs those commands, taken
from the page itself, against two fake homes: the old one holds a real git
repository under a username that does not exist on the new machine, with a real
checkpoint written through the store and a transcript Claude Code recorded
there. The new one starts empty. The assertions are what the runbook promises:
after the move the project at its NEW path reads the OLD history, and nothing
that must not travel did.

What keeps it honest: every `daimon ...` command and every `rsync` block is read
out of the page (a command the test runs but the page does not carry fails
first), the `daimon` commands run through the real CLI, the copy runs the real
`rsync`, and every directory is the default one under each fake HOME, never a
path the test chose for the store. `$HOME` is the only thing that differs
between the machines, the way it does for a person.

Not covered, and said so: ssh (the page's destination is a plain path here),
Windows (see test_identity_windows_semantics.py for the comparisons), and
`team sync` against a real remote.
"""

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import cli, store
from tests.test_bucket_migration import _checkpoint
from tests.test_bucket_stamp_tool import _git, _repo
from tests.test_import_cmd import _STAMPS, _transcript

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "website/docs/guides/move-to-a-new-machine.md"
PAGE_ES = (REPO / "website/i18n/es/docusaurus-plugin-content-docs/current"
           / "guides/move-to-a-new-machine.md")
TEXT = PAGE.read_text(encoding="utf-8")

# The page's example names. The test swaps them for the fake homes.
PAGE_OLD, PAGE_NEW = "/Users/alice", "/Users/bob"

DAIMON_VARS = (
    "DAIMON_CHECKPOINT_DIR", "DAIMON_TEAM_DIR", "DAIMON_KEYS_DIR",
    "DAIMON_CHECKS_DIR", "DAIMON_LOG_DIR", "DAIMON_RECALL_SEEN_DIR",
    "DAIMON_RECALL_DB", "DAIMON_ENV_FILE", "DAIMON_WINDSURF_DIR",
    "DAIMON_CLAUDE_PROJECTS_DIR", "DAIMON_AUTHOR", "DAIMON_PROJECT_DIR",
    "CLAUDE_CODE_SESSION_ID")

needs_rsync = pytest.mark.skipif(shutil.which("rsync") is None,
                                 reason="rsync is not installed")


def _fences() -> list[str]:
    return re.findall(r"```(?:sh|bash)\n(.*?)```", TEXT, re.S)


def _page_has(command: str) -> str:
    """The command as the page writes it, or a failure: the test never runs a
    command the page does not carry."""
    lines = [ln.strip().removeprefix("$ ") for block in _fences()
             for ln in block.replace("\\\n", " ").splitlines()]
    assert command in lines, f"the page does not carry: {command}"
    return command


class Machine:
    """One fake computer: a HOME with nothing of daimon's under it but what
    the commands put there."""

    def __init__(self, tmp_path, monkeypatch, capsys, name):
        self.home = (tmp_path / name).resolve()
        self.home.mkdir()
        self.monkeypatch, self.capsys = monkeypatch, capsys

    def use(self):
        mp = self.monkeypatch
        mp.setenv("HOME", str(self.home))
        mp.setenv("USERPROFILE", str(self.home))
        for var in DAIMON_VARS:
            mp.delenv(var, raising=False)
        mp.setenv("DAIMON_MIN_MESSAGES", "3")
        # the resolver and root memos are keyed by what they were asked, not
        # by HOME: a second machine must not read the first one's answers
        from daimon_briefing import config, identity
        identity.invalidate()
        config.resolve_project_root.cache_clear()
        config._git_user_name_in.cache_clear()
        return self

    @property
    def dot(self) -> Path:
        return self.home / ".daimon"

    def run(self, command: str, *, cwd=None, subs=()):
        """Run one `daimon ...` command from the page, with the page's example
        names swapped for this test's. Returns (rc, stdout, stderr)."""
        _page_has(command)
        for old, new in subs:
            command = command.replace(old, new)
        argv = shlex.split(command.replace("~", str(self.home)))
        assert argv[0] == "daimon"
        self.use()
        if cwd is not None:
            self.monkeypatch.chdir(cwd)
        self.capsys.readouterr()
        rc = cli.main(argv[1:])
        out = self.capsys.readouterr()
        return rc, out.out, out.err


@pytest.fixture
def world(tmp_path, monkeypatch, capsys):
    old = Machine(tmp_path, monkeypatch, capsys, "alice").use()
    new = Machine(tmp_path, monkeypatch, capsys, "bob")
    old_app = _repo(old.home / "code" / "app", "https://example.com/o/app.git")
    new_app = new.home / "code" / "app"
    new_app.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(old_app), str(new_app)],
                   check=True, capture_output=True)
    _git(new_app, "remote", "set-url", "origin", "https://example.com/o/app.git")
    return old, new, old_app, new_app


def _seed_old_machine(old, old_app):
    """What a year of use leaves: a signed checkpoint, a derived index, a chunk
    cache, a half-written file, a signing key pair, an env file, a team mirror."""
    from daimon_briefing.surfaces import Writer
    old.use()
    store.write_checkpoint(
        "S-old-1", _checkpoint("S-old-1", "2026-11-01T00:00:00Z"),
        project_dir=str(old_app), writer=Writer.HUMAN)
    ckpt = old.dot / "checkpoints"
    (ckpt / ".chunk-cache").mkdir()
    (ckpt / ".chunk-cache" / "c.json").write_text("{}", encoding="utf-8")
    (ckpt / "S-half.json.tmp").write_text("{", encoding="utf-8")
    (ckpt / ".pointer.lock").write_text("", encoding="utf-8")
    (old.dot / "recall.db").write_text("derived", encoding="utf-8")
    for name in ("recall", "recall_seen", "checks", "hooks", "logs"):
        (old.dot / name).mkdir()
        (old.dot / name / "x").write_text("derived", encoding="utf-8")
    (old.dot / "keys").mkdir()
    (old.dot / "keys" / "signing.seed").write_text("seed", encoding="utf-8")
    (old.dot / "keys" / "signing.pub.json").write_text("{}", encoding="utf-8")
    (old.dot / "team" / "local").mkdir(parents=True)
    (old.dot / "team" / "local" / "mine.json").write_text("{}", encoding="utf-8")
    (old.dot / "env").write_text("DAIMON_AUTHOR=alice\n", encoding="utf-8")
    return ckpt


def _copy(old, new):
    """The page's copy blocks, verbatim, run by bash with the page's variables
    pointing at the two fake homes."""
    blocks = [b for b in _fences() if "rsync" in b]
    assert blocks, "the page has no rsync block"
    new.dot.mkdir(parents=True, exist_ok=True)
    (new.dot / "team").mkdir(exist_ok=True)
    (new.dot / "keys").mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(old.home),
               DEST_CKPT=str(new.dot / "checkpoints"),
               DEST_TEAM=str(new.dot / "team"),
               DEST_KEYS=str(new.dot / "keys"),
               DEST_ENV=str(new.dot / "env"))
    for var in DAIMON_VARS:
        env.pop(var, None)
    for block in blocks:
        done = subprocess.run(["bash", "-ec", _with_old_vars(block)], env=env,
                              capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def _with_old_vars(block: str) -> str:
    """The page's own `OLD_...=` lines, then the block: the defaults the page
    writes are what resolve the old machine's directories."""
    lines = [ln for b in _fences() for ln in b.splitlines()
             if re.match(r"OLD_[A-Z]+=", ln)]
    return "\n".join(lines) + "\n" + block


def _sub(old, new):
    return ((PAGE_OLD, str(old.home)), (PAGE_NEW, str(new.home)))


@needs_rsync
def test_the_moved_project_finds_its_history_by_the_runbook(world):
    old, new, old_app, new_app = world
    ckpt = _seed_old_machine(old, old_app)
    bucket = store.bucket_of(str(old_app))
    flat_before = (ckpt / "S-old-1.json").read_bytes()
    subs = _sub(old, new)

    # -- old machine -------------------------------------------------------
    rc, _, _ = old.run("daimon heal --dry-run", cwd=old_app)
    assert rc == 0
    rc, _, _ = old.run("daimon heal --all", cwd=old_app)
    assert rc == 0
    rc, out, _ = old.run("daimon bucket stamp --scan ~/code", cwd=old_app)
    assert rc == 0 and "plan only" in out
    rc, out, _ = old.run("daimon bucket stamp --scan ~/code --apply", cwd=old_app)
    assert rc == 0
    assert (ckpt / bucket / store._IDENTITY_NAME).is_file(), out
    _copy(old, new)
    # both fake machines share one disk; the old machine's folders are not there
    # for the new one, which is what `live-source` would otherwise refuse
    shutil.rmtree(old.home / "code")

    # -- what travelled and what did not --------------------------------------
    moved = new.dot / "checkpoints"
    assert (moved / "S-old-1.json").read_bytes() == flat_before
    assert (moved / "S-old-1.receipt").exists() == (ckpt / "S-old-1.receipt").exists()
    assert (moved / bucket / "root").read_text().strip() == str(old_app)
    assert (new.dot / "team" / "local" / "mine.json").is_file()
    assert (new.dot / "keys" / "signing.seed").is_file()
    assert (new.dot / "keys" / "signing.pub.json").is_file()
    assert (new.dot / "env").is_file()
    for absent in (".chunk-cache", "S-half.json.tmp", ".pointer.lock"):
        assert not (moved / absent).exists(), absent
    for absent in ("recall.db", "recall", "recall_seen", "checks", "hooks",
                   "logs"):
        assert not (new.dot / absent).exists(), absent
    # mtimes survive (retention reads them)
    assert (moved / "S-old-1.json").stat().st_mtime == pytest.approx(
        (ckpt / "S-old-1.json").stat().st_mtime, abs=1)

    # -- new machine: before the rebind the project finds nothing -------------
    new.use()
    assert store.bucket_of(str(new_app)) != bucket

    rc, out, _ = new.run("daimon bucket remap --from /Users/alice --to /Users/bob",
                         cwd=new_app, subs=subs)
    assert rc == 0 and "plan only" in out, out
    assert (moved / bucket / "root").read_text().strip() == str(old_app)
    rc, _, _ = new.run(
        "daimon bucket remap --from /Users/alice --to /Users/bob --apply",
        cwd=new_app, subs=subs)
    assert rc == 0
    assert (moved / bucket / "root").read_text().strip() == str(new_app)

    # -- the point of the whole page -------------------------------------------
    new.use()
    assert store.bucket_of(str(new_app)) == bucket
    rc, out, _ = new.run("daimon brief --project .", cwd=new_app)
    assert rc == 0 and "S-old-1" in out
    rc, out, _ = new.run("daimon status --project .", cwd=new_app)
    assert rc == 0
    assert (moved / "S-old-1.json").read_bytes() == flat_before  # untouched

    rc, out, _ = new.run("daimon bucket doctor --path .", cwd=new_app)
    assert rc == 0 and "dangling" not in out
    rc, _, _ = new.run("daimon ledger repair events --dry-run --project .",
                       cwd=new_app)
    assert rc in (0, 1)
    rc, _, _ = new.run("daimon check sync --project .", cwd=new_app)
    assert rc == 0

    # -- history: a transcript the old machine recorded imports here ------------
    _transcript(new.home / ".claude" / "projects" / "-Users-alice-code-app",
                "S-recorded-on-alice", _STAMPS[0], cwd=str(old_app))
    rc, out, _ = new.run("daimon import --project . --plan", cwd=new_app)
    assert rc == 0 and "S-recorded-on-alice" in out


@needs_rsync
def test_a_session_opened_before_the_rebind_is_folded_by_the_printed_merge(world):
    """The page says to run `remap` before opening a session in a moved project.
    When a person did not, remap lists the collision with the merge that folds
    it, and after that merge the history is found."""
    old, new, old_app, new_app = world
    _seed_old_machine(old, old_app)
    bucket = store.bucket_of(str(old_app))
    subs = _sub(old, new)
    _copy(old, new)
    shutil.rmtree(old.home / "code")

    new.use()
    from daimon_briefing import identity
    from daimon_briefing.surfaces import Writer
    store.write_checkpoint(
        "S-day-one", _checkpoint("S-day-one", "2026-11-05T00:00:00Z"),
        project_dir=str(new_app), writer=Writer.HUMAN)
    fresh = store.bucket_of(str(new_app))
    assert fresh != bucket
    identity.invalidate()

    # names print only to a terminal, and the page is read at one
    from daimon_briefing.cli import projects as cli_projects
    new.monkeypatch.setattr(cli_projects, "_stdout_is_tty", lambda: True)
    rc, out, err = new.run(
        "daimon bucket remap --from /Users/alice --to /Users/bob --apply",
        cwd=new_app, subs=subs)
    assert rc == 1
    assert (new.dot / "checkpoints" / bucket / "root").read_text().strip() == str(old_app)
    printed = f"daimon bucket merge --from={fresh} --into={bucket}"
    assert printed in out + err, (out, err)

    rc, mout, merr = new.run("daimon bucket merge --from=BUCKET --into=BUCKET --force --apply",
                       cwd=new_app, subs=subs + (
                           ("--from=BUCKET", f"--from={fresh}"),
                           ("--into=BUCKET", f"--into={bucket}")))
    assert rc == 0, (mout, merr)
    rc, _, _ = new.run(
        "daimon bucket remap --from /Users/alice --to /Users/bob --apply",
        cwd=new_app, subs=subs)
    assert rc == 0
    new.use()
    assert store.bucket_of(str(new_app)) == bucket
    rc, out, _ = new.run("daimon brief --project .", cwd=new_app)
    assert "S-day-one" in out  # the newer session of the folded bucket leads
    assert (new.dot / "checkpoints" / "S-old-1.json").is_file()


def test_the_spanish_page_carries_the_same_commands():
    def commands(text):
        return [ln.strip() for b in re.findall(r"```(?:sh|bash)\n(.*?)```",
                                               text, re.S)
                for ln in b.replace("\\\n", " ").splitlines()
                if ln.strip().startswith(("daimon ", "rsync "))]
    assert commands(PAGE_ES.read_text(encoding="utf-8")) == commands(TEXT)


def test_the_page_never_assumes_the_default_home_without_a_variable():
    """Paths resolve through configuration: each directory is the variable the
    configuration reads, with the default only as the fallback."""
    for var in ("DAIMON_CHECKPOINT_DIR", "DAIMON_TEAM_DIR", "DAIMON_KEYS_DIR",
                "DAIMON_ENV_FILE"):
        assert var in TEXT
