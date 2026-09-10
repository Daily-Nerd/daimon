"""`daimon hooks install kimi` — the TOML registration path (#988).

Every shipped host manager so far writes JSON and can re-serialize the whole
file safely. Kimi Code reads a TOML `config.toml` that its own login flow
populates with provider credentials and model tables, and it has a trap the
JSON hosts do not: a `[[hooks]]` entry with a FIFTH field fails the WHOLE
config load, not just that entry. Two rules follow, and these tests are what
holds them.

1. Never re-serialize. The installer appends and removes TEXT BLOCKS, so
   everything it did not write comes back byte-identical, comments and key
   order included. A round trip (install then remove) is the assertion.
2. Exactly four fields per entry, always.

Measured against Kimi Code 0.42.0 on 2026-09-09; the probe notes carry the
payloads and the config shape.
"""

import stat
import sys
from pathlib import Path

import pytest

from daimon_briefing import kimi_hooks

REPO = Path(__file__).resolve().parents[2]
PKG_HOOKS = REPO / "plugin" / "daimon_briefing" / "_hooks"

# A config.toml shaped like the one Kimi's login flow writes: provider tables,
# an OAuth sub-table, a comment, and no hooks at all. Nothing daimon owns.
BASE_CONFIG = """\
# Kimi Code configuration.
model = "kimi-code/kimi-for-coding"

[services.moonshot]
api_key = "REDACTED"

[services.moonshot_fetch.oauth]
storage = "file"
key = "oauth/kimi-code"
"""


def _home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".kimi-code").mkdir(parents=True)
    return home


def _write_config(home: Path, text: str) -> Path:
    path = home / ".kimi-code" / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _install(home: Path, env=None):
    return kimi_hooks.install(PKG_HOOKS, home, env=env if env is not None else {})


# ---- where things go ----

def test_the_config_home_defaults_to_the_dot_directory(tmp_path):
    home = _home(tmp_path)
    assert kimi_hooks.config_home(home, env={}) == home / ".kimi-code"


def test_kimi_code_home_overrides_the_default(tmp_path):
    """Kimi documents `KIMI_CODE_HOME` for its user scope. Honouring it is what
    lets a test, and a person with a relocated config, install somewhere that
    is not the real `~/.kimi-code`."""
    elsewhere = tmp_path / "relocated"
    home = _home(tmp_path)
    env = {"KIMI_CODE_HOME": str(elsewhere)}
    assert kimi_hooks.config_home(home, env=env) == elsewhere
    assert kimi_hooks.config_path(home, env=env) == elsewhere / "config.toml"


def test_install_copies_every_file_and_marks_the_scripts_executable(tmp_path):
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    hooks_dir = kimi_hooks.hooks_dir(home, env={})
    for name in kimi_hooks.FILES:
        dest = hooks_dir / name
        assert dest.exists(), f"{name} was not installed"
        assert dest.read_bytes() == (PKG_HOOKS / name).read_bytes()
        executable = bool(dest.stat().st_mode & stat.S_IXUSR)
        # The shared modules are imported by same-dir lookup, never executed.
        assert executable is (name not in kimi_hooks.MODULES), name


# ---- the four-field rule ----

def test_every_written_entry_carries_exactly_the_four_allowed_fields(tmp_path):
    """The trap this whole module is shaped around. A fifth field does not
    degrade the entry, it fails the entire config load, so a `statusMessage`
    copied over from the Codex manager would silently unhook the host."""
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    blocks = kimi_hooks._hook_blocks(kimi_hooks.config_path(home, env={})
                                     .read_text(encoding="utf-8"))
    assert len(blocks) == len(kimi_hooks.HOOKS)
    for block in blocks:
        assert set(block.fields) == {"event", "matcher", "command", "timeout"}


def test_the_timeout_written_is_inside_the_hosts_accepted_range(tmp_path):
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    for block in kimi_hooks._hook_blocks(
            kimi_hooks.config_path(home, env={}).read_text(encoding="utf-8")):
        assert 1 <= int(block.fields["timeout"]) <= 600


def test_the_three_measured_events_are_registered(tmp_path):
    """No `SessionStart`: its stdout is dropped by the host, so registering it
    would spawn an interpreter per session to write to a closed pipe. No
    `PreToolUse`: the deny channel is unmeasured, so no check profile ships."""
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    events = {b.fields["event"] for b in kimi_hooks._hook_blocks(
        kimi_hooks.config_path(home, env={}).read_text(encoding="utf-8"))}
    assert events == {"UserPromptSubmit", "SessionEnd", "Stop"}


def test_each_command_points_at_the_installed_script_by_absolute_path(tmp_path):
    """Kimi runs the command as given, from the session's cwd. A relative path
    would resolve against the project the person happens to be in."""
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    hooks_dir = kimi_hooks.hooks_dir(home, env={})
    commands = {b.fields["command"] for b in kimi_hooks._hook_blocks(
        kimi_hooks.config_path(home, env={}).read_text(encoding="utf-8"))}
    assert len(commands) == len(kimi_hooks.HOOKS)
    for command in commands:
        assert str(hooks_dir) in command
        script = command.split()[-1]
        assert Path(script).is_absolute()
        assert Path(script).exists()


# ---- never disturb what we do not own ----

def test_everything_above_our_blocks_survives_byte_for_byte(tmp_path):
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    _install(home)
    after = path.read_text(encoding="utf-8")
    assert after.startswith(BASE_CONFIG), (
        "the installer rewrote content it does not own")


def test_install_then_remove_restores_the_file_byte_for_byte(tmp_path):
    """The round trip is the real guard. It catches a re-serializing writer,
    a dropped comment, a reordered key, and blank lines accumulating across
    repeated install/remove cycles, none of which a field-level assertion
    would notice."""
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    for _ in range(3):
        _install(home)
        kimi_hooks.remove(home, env={})
        assert path.read_text(encoding="utf-8") == BASE_CONFIG


def test_a_crlf_config_keeps_its_line_endings_across_install_and_remove(tmp_path):
    """A config.toml that passed through a Windows editor ends every line in
    CRLF. Reading it with universal newlines and writing LF back would rewrite
    every line the installer does not own, which is the exact thing "never
    re-serialize" exists to prevent. The installer's own lines follow the
    file's convention so the result is not a mixed-ending file either."""
    home = _home(tmp_path)
    path = home / ".kimi-code" / "config.toml"
    crlf = BASE_CONFIG.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(crlf)
    _install(home)
    after = path.read_bytes()
    assert after.startswith(crlf)
    assert b"\n" not in after.replace(b"\r\n", b""), (
        "the installer's own lines must match the file's line endings")
    kimi_hooks.remove(home, env={})
    assert path.read_bytes() == crlf


def test_a_config_without_a_trailing_newline_gains_exactly_one(tmp_path):
    """The one byte the round trip does not restore, pinned so the docs can
    say exactly that instead of "byte for byte". A last line with no newline
    needs one before a block can be appended, and remove has no way to tell
    that newline from one the person wrote."""
    home = _home(tmp_path)
    bare = BASE_CONFIG.rstrip("\n")
    path = _write_config(home, bare)
    _install(home)
    kimi_hooks.remove(home, env={})
    assert path.read_text(encoding="utf-8") == bare + "\n"


def test_a_foreign_hooks_entry_is_never_touched(tmp_path):
    """Someone else's `[[hooks]]` block is the case where "remove our entries"
    and "remove the hooks array" differ, and only one of them is correct."""
    foreign = BASE_CONFIG + """
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "python3 /home/someone/my-own-linter.py"
timeout = 5
"""
    home = _home(tmp_path)
    path = _write_config(home, foreign)
    _install(home)
    kimi_hooks.remove(home, env={})
    assert path.read_text(encoding="utf-8") == foreign


def test_install_is_idempotent(tmp_path):
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    _install(home)
    once = path.read_text(encoding="utf-8")
    lines = _install(home)
    assert path.read_text(encoding="utf-8") == once
    assert any("already registered" in line for line in lines)


def test_a_stale_registration_is_refreshed_rather_than_duplicated(tmp_path):
    """A person who installed under an older path, or moved KIMI_CODE_HOME,
    has a daimon block pointing at a script that is no longer there. It must
    end up corrected, not doubled: two `UserPromptSubmit` entries mean two
    briefings in one prompt."""
    stale = BASE_CONFIG + """
[[hooks]]
event = "UserPromptSubmit"
matcher = ".*"
command = "python3 /old/path/daimon-kimi-user-prompt-submit.py"
timeout = 10
"""
    home = _home(tmp_path)
    path = _write_config(home, stale)
    _install(home)
    text = path.read_text(encoding="utf-8")
    blocks = [b for b in kimi_hooks._hook_blocks(text)
              if b.fields.get("event") == "UserPromptSubmit"]
    assert len(blocks) == 1
    assert "/old/path/" not in text


def test_the_existing_file_is_backed_up_before_the_first_write(tmp_path):
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    _install(home)
    backups = list((home / ".kimi-code").glob("config.toml.daimon-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == BASE_CONFIG


def test_a_missing_config_is_created_rather_than_refused(tmp_path):
    """`hooks install` before the first `kimi` login. Nothing to preserve, so
    nothing to back up, and the file we write is ours alone."""
    home = _home(tmp_path)
    lines = _install(home)
    path = kimi_hooks.config_path(home, env={})
    assert path.exists()
    assert len(kimi_hooks._hook_blocks(path.read_text(encoding="utf-8"))) == 3
    assert not list((home / ".kimi-code").glob("config.toml.daimon-backup-*"))
    assert any("registered" in line for line in lines)


def test_remove_on_a_config_with_no_daimon_blocks_writes_nothing(tmp_path):
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    before = path.stat().st_mtime_ns
    kimi_hooks.remove(home, env={})
    assert path.read_text(encoding="utf-8") == BASE_CONFIG
    assert path.stat().st_mtime_ns == before, "a no-op still rewrote the file"


def test_remove_without_a_config_file_is_not_an_error(tmp_path):
    home = _home(tmp_path)
    lines = kimi_hooks.remove(home, env={})
    assert lines and not kimi_hooks.config_path(home, env={}).exists()


# ---- the block parser itself ----

def test_a_block_ends_at_the_next_table_header(tmp_path):
    text = """\
[[hooks]]
event = "Stop"
command = "python3 /x/daimon-kimi-stop.py"

[services.other]
key = "value"
"""
    blocks = kimi_hooks._hook_blocks(text)
    assert len(blocks) == 1
    assert "services.other" not in "\n".join(
        text.splitlines()[blocks[0].start:blocks[0].end])


def test_the_parser_reads_indented_and_commented_entries(tmp_path):
    """Not a shape daimon writes, but one a person can hand-edit into the file.
    A parser that misses it would leave a duplicate behind on the next
    install."""
    text = """\
  [[hooks]]
  event = "Stop"   # fires per turn
  matcher = ".*"
  command = "python3 /x/hooks/daimon-kimi-stop.py"
  timeout = 10
"""
    blocks = kimi_hooks._hook_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].fields["event"] == "Stop"
    assert blocks[0].fields["command"].endswith("daimon-kimi-stop.py")
    assert kimi_hooks._is_ours(blocks[0])


def test_a_command_naming_no_daimon_script_is_not_ours():
    text = """\
[[hooks]]
event = "Stop"
matcher = ".*"
command = "python3 /x/daimon-ish-but-not-ours.py"
timeout = 10
"""
    assert not kimi_hooks._is_ours(kimi_hooks._hook_blocks(text)[0])


def test_an_unreadable_hooks_entry_refuses_rather_than_clobbers(tmp_path):
    """The case that matters: a `[[hooks]]` entry holding a line this parser
    cannot read. It cannot tell where that block ends or whether daimon owns
    it, so removing or replacing it would be a guess, and the file it would be
    guessing inside holds the person's provider credentials. Refusing leaves
    them with a working host and a message."""
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG + """
[[hooks]]
event = "Stop"
command = [ "python3", "/x/y.py" ]
""")
    before = path.read_text(encoding="utf-8")
    with pytest.raises(kimi_hooks.ConfigError):
        _install(home)
    assert path.read_text(encoding="utf-8") == before


def test_a_file_that_is_not_toml_at_all_is_still_appended_to(tmp_path):
    """The deliberate limit of the refusal above, stated so it is a decision
    rather than an oversight. This module is not a TOML validator, and it does
    not need to be: a file the HOST cannot parse is already not loading, the
    append preserves every existing byte, and once the person fixes their typo
    their hooks are there. Refusing here would block a repair, not protect
    anything."""
    home = _home(tmp_path)
    broken = "[[hooks]\nevent = broken\n"
    path = _write_config(home, broken)
    _install(home)
    assert path.read_text(encoding="utf-8").startswith(broken)


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11+")
def test_what_the_installer_writes_is_valid_toml(tmp_path):
    """The kernel floor is 3.10, so the installer cannot depend on `tomllib`
    and hand-rolls its block parser. The TEST is free to use it, and this is
    the assertion that the hand-rolled writer produces something the real
    parser accepts."""
    import tomllib

    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    _install(home)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["services"]["moonshot"]["api_key"] == "REDACTED"
    assert len(parsed["hooks"]) == 3
    for entry in parsed["hooks"]:
        assert set(entry) == {"event", "matcher", "command", "timeout"}
        assert isinstance(entry["timeout"], int)


# ---- registration status, the audit `daimon hooks status` reads ----

def test_registration_status_reports_the_three_states(tmp_path):
    home = _home(tmp_path)
    _write_config(home, BASE_CONFIG)
    assert kimi_hooks.registration_status(home, env={}) == "UNREGISTERED"
    _install(home)
    assert kimi_hooks.registration_status(home, env={}) == "REGISTERED"
    kimi_hooks.remove(home, env={})
    assert kimi_hooks.registration_status(home, env={}) == "UNREGISTERED"


def test_a_partial_registration_is_reported_as_partial(tmp_path):
    """The shape a half-finished hand edit leaves behind. Reporting it as
    REGISTERED would tell someone their sessions are being captured when one
    of the two capture events is gone."""
    home = _home(tmp_path)
    path = _write_config(home, BASE_CONFIG)
    _install(home)
    text = path.read_text(encoding="utf-8")
    blocks = [b for b in kimi_hooks._hook_blocks(text)
              if b.fields["event"] == "Stop"]
    lines = text.splitlines(keepends=True)
    del lines[blocks[0].start:blocks[0].end]
    path.write_text("".join(lines), encoding="utf-8")
    assert kimi_hooks.registration_status(home, env={}) == "PARTIAL"


def test_an_unreadable_config_reports_unregistered_not_a_crash(tmp_path):
    home = _home(tmp_path)
    _write_config(home, "[[hooks]\nbroken")
    assert kimi_hooks.registration_status(home, env={}) == "UNREGISTERED"


# ---- wiring into the shipped CLI ----

def test_kimi_is_a_known_hooks_host():
    from daimon_briefing import cli

    spec = cli._HOOK_HOSTS.get("kimi")
    assert spec is not None, "`daimon hooks install kimi` has no host entry"
    assert spec["register"] == "kimi"
    assert set(spec["files"]) == set(kimi_hooks.FILES), (
        "the status audit walks `files`; a file the install writes and this "
        "list omits goes stale invisibly")
    assert set(spec["events"]) == {"UserPromptSubmit", "SessionEnd", "Stop"}


def test_the_status_audit_looks_inside_the_kimi_config_directory(tmp_path):
    from daimon_briefing import cli

    home = _home(tmp_path)
    spec = cli._HOOK_HOSTS["kimi"]
    assert cli._host_install_dir(spec, home) == home / ".kimi-code" / "hooks"


def test_the_ledger_can_attribute_both_new_capture_tags():
    """A host adapter whose spawn lines the ledger cannot parse is invisible to
    `daimon status`, to hung detection, and to `heal`."""
    from daimon_briefing import ledger

    ts = "2026-09-09T17:05:49Z"
    for tag in ("kimi-session-end", "kimi-stop"):
        line = f"{ts} {tag}: spawned serialize for session_abc (transcript: /t.jsonl)"
        assert ledger._SPAWN_RE.match(line), f"{tag} spawns are invisible"
        assert ledger._SPAWN_RE.match(line).group(2) == "session_abc"
        assert ledger._STATS_HOST_RE.match(line).group(1) == tag


def test_the_new_scripts_are_in_the_sync_manifest():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_hooks_kimi", REPO / "scripts" / "sync_hooks.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sources = {src for src, _dst in mod.SYNC_PAIRS}
    for script in (h.script for h in kimi_hooks.HOOKS):
        assert f"hook/{script}" in sources, (
            f"{script} is hand-copied outside SYNC_PAIRS and drifts silently")


def test_no_check_profile_ships_for_kimi_yet():
    """Deliberate, and asserted so it cannot be added by reflex. Kimi's deny
    channel (exit 2, and the JSON permission decision) has never been seen to
    block a command on a live session. A profile would put `enforce` in the
    ladder for an enforcement daimon has not watched land."""
    from daimon_briefing import checks_host

    assert "kimi" not in checks_host.PROFILES


# ---- the agent skill (`daimon skill install kimi`) ----

def test_kimi_is_a_known_skill_host(tmp_path):
    """Kimi's skill contract is Claude's, measured against 0.42.0: a directory
    form `<scope>/skills/<name>/SKILL.md` with `name` and `description`
    frontmatter, scanned from `~/.kimi-code/skills/` and the project's
    `.kimi-code/skills/`, with a LISTING injected at session start and the
    body loaded only on invocation. So it takes the `owned`/`full` shape, not
    the always-injected compact block the rules-file hosts need."""
    from daimon_briefing import skill_install

    spec = skill_install.HOSTS.get("kimi")
    assert spec is not None
    assert spec["global"] == (".kimi-code/skills/daimon/SKILL.md", "owned", "full")
    assert spec["project"] == (".kimi-code/skills/daimon/SKILL.md", "owned", "full")


def test_installing_the_skill_for_kimi_writes_a_usable_skill_file(tmp_path):
    from daimon_briefing import skill_install

    skill_install.install("kimi", project=False, home=tmp_path, cwd=tmp_path)
    path = tmp_path / ".kimi-code" / "skills" / "daimon" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    # The host requires both frontmatter fields in directory form, and skips a
    # skill whose `name` does not match its directory.
    assert text.startswith("---")
    assert "name: daimon" in text
    assert "description:" in text
