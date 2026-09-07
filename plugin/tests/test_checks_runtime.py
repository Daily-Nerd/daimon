"""#943 slice 2: the standalone check runtime.

`checks_runtime.py` is loaded by host hook scripts that cannot import the
venv-only package, so every test here loads it by FILE LOCATION under its own
module name, the way a hook will: a relative import or a package import in
the module fails at that load rather than on someone's host.

It loads the CANONICAL file, not the shipped copy. Byte-identity is a
separate drift test (test_hooks_install.py), and keeping the two concerns
apart means an edit that has not been synced yet fails one obvious test
instead of every test in this file.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from daimon_briefing import config

CANONICAL = (Path(__file__).parents[1] / "daimon_briefing"
             / "checks_runtime.py")


def _runtime():
    """The runtime, loaded standalone. A relative import or a
    `daimon_briefing` import in it fails HERE, which is the only place it can
    fail before a host hook hits it in production."""
    spec = importlib.util.spec_from_file_location(
        "_checks_runtime_under_test", CANONICAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rt = _runtime()


# ---- stdlib only (scar 0049) ----------------------------------------------


def test_the_runtime_imports_nothing_from_the_package():
    """Scar 0049 in its own words: `from . import normalize` looks ordinary,
    passes every local test, and breaks the standalone hooks, which cannot
    resolve `daimon_briefing`. An AST walk catches it at authoring time
    rather than on the host."""
    tree = ast.parse(CANONICAL.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import: {ast.dump(node)}"
            assert not str(node.module or "").startswith("daimon_briefing"), \
                f"package import: {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("daimon_briefing"), \
                    f"package import: {alias.name}"


# ---- path mirrors (scar 0043) ---------------------------------------------


@pytest.mark.parametrize("name,mirror,canon", [
    ("DAIMON_CHECKS_DIR", "checks_dir", "checks_dir"),
    ("DAIMON_LOG_DIR", "log_dir", "log_dir"),
])
def test_mirror_and_config_agree_over_the_whole_probe_table(
        name, mirror, canon, tmp_path, monkeypatch):
    """Behavioral equality against the config function, never against a
    literal this test would also have to get right (scar 0043's idiom).

    The strip axis is the one that looks like a bug and is not: config does
    not strip, so "   " is a RELATIVE directory named three spaces. A mirror
    that stripped would resolve somewhere the writer never writes, and the
    hook would read an empty manifest forever with nothing to report."""
    fn, ref = getattr(rt, mirror), getattr(config, canon)
    for raw in (None, "", "   ", "  /tmp/dchecks  ", "/tmp/dchecks",
                "~/dchecks"):
        monkeypatch.delenv(name, raising=False)
        if raw is not None:
            monkeypatch.setenv(name, raw)
        assert fn() == ref(), repr(raw)

    # The env-file PARSER is a second copy of config._file_values and can
    # drift line-form by line-form. Each line discriminates one rule:
    # quoting, the export prefix, whitespace, comments, an empty value.
    monkeypatch.delenv(name, raising=False)
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    resolved = set()
    for line in (f"{name}={tmp_path}/plain",
                 f'{name}="{tmp_path}/quoted"',
                 f"{name}='{tmp_path}/single'",
                 f"export {name}={tmp_path}/exported",
                 f"  export   {name} =  {tmp_path}/spaced  ",
                 f"# {name}={tmp_path}/commented",
                 f"{name}=",
                 f"{name}=~/tilde",
                 f"NOT_THE_VAR={tmp_path}/other",
                 f"{name}={tmp_path}/first\n{name}={tmp_path}/last"):
        env_file.write_text(line + "\n", encoding="utf-8")
        assert fn() == ref(), line
        resolved.add(fn())
    assert len(resolved) > 1, \
        "every probe resolved alike — the env file was never read at all"


def test_the_timeout_mirror_agrees_with_config_over_its_own_probe_table(
        tmp_path, monkeypatch):
    """#943 slice 3: the hook needs the budget, not just the paths.

    `DAIMON_CHECK_TIMEOUT` is a number rather than a path, so it cannot share
    the table above — every probe there is path-shaped and would collapse to
    the same 5.0 here, which is exactly the shape that passes while measuring
    nothing. It gets its own table, asserted the same way: against the config
    function, never against a literal this test would also have to get right.

    The quirks that look like bugs and are not (scar 0043): a present-but-
    unparseable value is the default rather than an error, a value under the
    floor is the floor rather than an unconditional timeout, and the env file
    is read when the process env is silent, because that is the channel a
    GUI-launched host actually uses."""
    name = "DAIMON_CHECK_TIMEOUT"
    resolved = set()
    for raw in (None, "", "   ", "0", "0.1", "3", "  7  ", "12.5", "abc",
                "-2", "1e1"):
        monkeypatch.delenv(name, raising=False)
        if raw is not None:
            monkeypatch.setenv(name, raw)
        assert rt.check_timeout() == config.check_timeout(), repr(raw)
        resolved.add(rt.check_timeout())
    assert len(resolved) > 1, "every probe resolved alike"

    monkeypatch.delenv(name, raising=False)
    env_file = Path(os.environ["DAIMON_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    from_file = set()
    for line in (f"{name}=1",
                 f'{name}="2"',
                 f"{name}='3'",
                 f"export {name}=4",
                 f"  export   {name} =  6  ",
                 f"# {name}=8",
                 f"{name}=",
                 "NOT_THE_VAR=9",
                 f"{name}=1\n{name}=11"):
        env_file.write_text(line + "\n", encoding="utf-8")
        assert rt.check_timeout() == config.check_timeout(), line
        from_file.add(rt.check_timeout())
    assert len(from_file) > 1, \
        "every probe resolved alike — the env file was never read at all"


# ---- manifest -------------------------------------------------------------


def _write_manifest(entries):
    import json
    path = config.checks_dir() / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_no_manifest_reads_as_empty_with_a_reason():
    """Never an exception: the hook runs on every Bash call and a raise here
    is an action that proceeds with no record of why. `no-manifest` is the
    honest signal that lets a stats surface say "armed, never fired" instead
    of "clean" (scar 0057)."""
    loaded = rt.load_manifest()
    assert loaded.entries == []
    assert loaded.reason == "no-manifest"


def test_a_manifest_that_is_not_json_reads_as_empty_with_its_own_reason():
    """Distinct from no-manifest on purpose. Both allow, but only one of
    them means daimon wrote something it can no longer read."""
    path = config.checks_dir() / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = rt.load_manifest()
    assert loaded.entries == []
    assert loaded.reason == "manifest-unreadable"


def test_a_manifest_that_is_not_a_list_reads_as_unreadable():
    _write_manifest({"ruling_id": "r-1"})
    assert rt.load_manifest().reason == "manifest-unreadable"


def test_a_manifest_of_entries_loads_with_no_reason():
    _write_manifest([{"ruling_id": "r-1", "project_dir": "/p/a",
                      "match": "gh pr create", "intent": "warn",
                      "sha256": "a" * 64, "armed_at": "2026-09-06T00:00:00Z"}])
    loaded = rt.load_manifest()
    assert loaded.reason == ""
    assert [e["ruling_id"] for e in loaded.entries] == ["r-1"]


def test_non_object_rows_are_dropped_not_fatal():
    _write_manifest(["nope", 7, {"ruling_id": "r-1", "project_dir": "/p/a",
                                 "match": "x", "intent": "warn",
                                 "sha256": "a" * 64, "armed_at": "t"}])
    loaded = rt.load_manifest()
    assert loaded.reason == ""
    assert [e["ruling_id"] for e in loaded.entries] == ["r-1"]


# ---- armed_for: the prefix match ------------------------------------------


def _manifest(*project_dirs):
    return rt.Manifest([{"ruling_id": f"r-{i}", "project_dir": p,
                         "match": "x", "intent": "warn", "sha256": "a" * 64,
                         "armed_at": "t"}
                        for i, p in enumerate(project_dirs)], "")


def test_the_project_dir_matches_its_own_directory(tmp_path):
    root = os.path.realpath(str(tmp_path))
    armed = rt.armed_for(root, _manifest(root))
    assert [e["ruling_id"] for e in armed] == ["r-0"]


def test_a_subdirectory_of_the_project_matches(tmp_path):
    root = os.path.realpath(str(tmp_path))
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    assert len(rt.armed_for(str(deep), _manifest(root))) == 1


def test_a_sibling_whose_name_merely_starts_the_same_does_not_match(tmp_path):
    """A separator-less prefix test arms /srv/appliance from an entry for
    /srv/app. The separator is the whole point."""
    root = os.path.realpath(str(tmp_path / "app"))
    (tmp_path / "app").mkdir()
    (tmp_path / "appliance").mkdir()
    assert rt.armed_for(str(tmp_path / "appliance"), _manifest(root)) == []


def test_a_foreign_project_matches_nothing(tmp_path):
    other = os.path.realpath(str(tmp_path / "other"))
    (tmp_path / "other").mkdir()
    (tmp_path / "here").mkdir()
    assert rt.armed_for(str(tmp_path / "here"), _manifest(other)) == []


def test_a_slug_shaped_cwd_matches_nothing(tmp_path):
    """A slug is an address daimon mints for a bucket, never a directory.
    Passing one here must resolve to nothing rather than prefix-matching by
    accident."""
    root = os.path.realpath(str(tmp_path))
    assert rt.armed_for("-Users-someone-code-daimon", _manifest(root)) == []


def test_an_entry_without_a_project_dir_is_skipped(tmp_path):
    manifest = rt.Manifest([{"ruling_id": "r-0", "match": "x"}], "")
    assert rt.armed_for(str(tmp_path), manifest) == []


def test_armed_for_follows_symlinks_on_both_sides(tmp_path):
    """The writer stores the physical path (git toplevel, realpath'd). A cwd
    reached through a symlink is the same project and must arm."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    root = os.path.realpath(str(real))
    assert len(rt.armed_for(str(link), _manifest(root))) == 1


def test_armed_for_never_raises_on_a_cwd_that_cannot_be_resolved(tmp_path):
    root = os.path.realpath(str(tmp_path))
    assert rt.armed_for("", _manifest(root)) == []
    assert rt.armed_for(None, _manifest(root)) == []


# ---- matches: the regex prefilter -----------------------------------------


def _entry(match):
    return {"ruling_id": "r-0", "project_dir": "/p", "match": match,
            "intent": "warn", "sha256": "a" * 64, "armed_at": "t"}


def test_the_match_is_an_unanchored_case_sensitive_search():
    assert rt.matches(_entry("gh pr create"), "cd /x && gh pr create --fill")
    assert not rt.matches(_entry("gh pr create"), "GH PR CREATE")


def test_a_command_that_merely_mentions_the_verb_still_matches():
    """Documented behavior, not a defect: a gate matches its own subject
    matter. The remedy is rewording the command, never loosening the
    prefilter."""
    assert rt.matches(_entry("gh pr create"), "echo 'gh pr create'")


def test_the_prefilter_reads_at_most_the_first_4096_bytes():
    """Scar 0022's bound, applied to the INPUT rather than the pattern. The
    pattern is caller-supplied and capped at 200 bytes, which bounds its
    length and not its backtracking; bounding the subject is what keeps a
    pathological pair from outliving the host timeout, which is fail-open."""
    assert rt.matches(_entry("NEEDLE"), "NEEDLE" + "x" * 9000)
    assert not rt.matches(_entry("NEEDLE"), "x" * 5000 + "NEEDLE")


def test_the_bound_counts_bytes_not_characters():
    pad = "é" * 2100  # 4200 bytes, 2100 characters
    assert not rt.matches(_entry("NEEDLE"), pad + "NEEDLE")


def test_an_unusable_pattern_matches_nothing_and_never_raises():
    """A hand-edited manifest can carry a pattern the writer would have
    refused. The hook must not turn that into a traceback on every Bash
    call."""
    assert rt.matches(_entry("("), "anything") is False
    assert rt.matches(_entry(""), "anything") is False
    assert rt.matches({"ruling_id": "r-0"}, "anything") is False


def test_matches_tolerates_a_command_that_is_not_a_string():
    assert rt.matches(_entry("x"), None) is False


# ---- resolver: spec 3.2, every row both ways ------------------------------


def _text_of(subject):
    return Path(subject.path).read_text(encoding="utf-8")


def _resolve(command, cwd):
    got = rt.resolve(command, str(cwd))
    return got


def test_a_command_with_no_file_argument_still_resolves(tmp_path):
    """The command string alone is a subject. A check that only inspects the
    command must not report unresolved for having nothing to read."""
    got = _resolve("gh pr create --title x", tmp_path)
    assert isinstance(got, rt.Subject)
    assert got.files == ()
    assert "gh pr create --title x" in _text_of(got)
    rt.discard(got)


def test_the_subject_file_is_owner_only_and_outside_the_daimon_home(tmp_path):
    """0o600, and in the system temp dir on purpose: a file under ~/.daimon
    would have to be declared in the surface registry and carry a deletion
    story, and this one exists for the length of one exec."""
    got = _resolve("gh pr create", tmp_path)
    path = Path(got.path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert config.checks_dir() not in path.parents
    assert Path.home() not in path.parents
    rt.discard(got)
    assert not path.exists()


@pytest.mark.parametrize("template", [
    "gh pr create --body-file {p}",
    "gh pr create --body-file={p}",
    "gh issue create -F {p}",
    "gh release create v1 --notes-file {p}",
    "gh release create v1 --notes-file={p}",
    "gh api repos/x -F body=@{p}",
    "gh api repos/x --field body=@{p}",
    "gh api repos/x --field=body=@{p}",
])
def test_every_resolving_row_reads_the_file(template, tmp_path):
    body = tmp_path / "body.md"
    body.write_text("the body text\n", encoding="utf-8")
    got = _resolve(template.format(p=body), tmp_path)
    assert isinstance(got, rt.Unresolved) is False, getattr(got, "cause", "")
    assert "the body text" in _text_of(got)
    rt.discard(got)


@pytest.mark.parametrize("template", [
    "gh pr create -F{p}",
    "gh api repos/x -Fbody=@{p}",
    "gh api repos/x -Ffield=@{p}",
])
def test_an_attached_short_flag_value_is_read_like_a_detached_one(
        template, tmp_path):
    """pflag, which gh uses, accepts a shorthand value attached to its flag,
    so `-Fbody.md` is the same command as `-F body.md`. A resolver that skips
    the attached form reports CLEAN for a file it never opened, which is the
    one outcome that lets the action through carrying a record saying it was
    proven safe."""
    body = tmp_path / "body.md"
    body.write_text("the body text\n", encoding="utf-8")
    got = _resolve(template.format(p=body), tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    assert "the body text" in _text_of(got)
    rt.discard(got)


def test_an_attached_short_flag_reading_stdin_is_not_silently_skipped(
        tmp_path):
    got = _resolve("cat notes.md | gh pr create -F-", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "stdin-pipe"


@pytest.mark.parametrize("command", [
    "gh pr create -Fbody.md",
    "gh api repos/x -Fbody=@body.md",
    "gh pr create -F-",
    "gh pr create -F=body.md",
    "gh pr create -Fnotes/body.md",
])
def test_no_attached_short_flag_form_ever_resolves_without_reading_it(
        command, tmp_path):
    """The general guard. Every form here names a file that does not exist,
    so the only honest answers are a read that fails or a form daimon says it
    cannot parse. A Subject carrying no files would mean the resolver decided
    a command it did not understand was safe."""
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Unresolved), \
        f"{command!r} resolved with files {getattr(got, 'files', None)}"
    assert got.cause in rt.CAUSES


def test_an_attached_literal_field_still_names_no_file(tmp_path):
    """The one attached form that legitimately reads nothing: `-Fname=x` is a
    value, not a path, exactly as the detached form is."""
    got = _resolve("gh api repos/x -Fname=daimon", tmp_path)
    assert isinstance(got, rt.Subject)
    assert got.files == ()
    rt.discard(got)


def test_a_relative_path_resolves_against_the_working_directory(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "b.md").write_text("relative body", encoding="utf-8")
    got = _resolve("gh pr create --body-file notes/b.md", tmp_path)
    assert "relative body" in _text_of(got)
    rt.discard(got)


def test_the_subject_is_the_command_then_a_separator_then_headed_file_bytes(
        tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("AAA", encoding="utf-8")
    b.write_text("BBB", encoding="utf-8")
    command = f"gh pr create --body-file {a} --notes-file {b}"
    got = _resolve(command, tmp_path)
    text = _text_of(got)
    assert text.startswith(command)
    # Each file is announced by the flag it came from, in command order.
    # Read AFTER the separator: the command itself names both flags, so a
    # search over the whole subject would find those and prove nothing.
    body = text.split(rt.SUBJECT_SEPARATOR, 1)[1]
    assert body.index("--body-file") < body.index("AAA") < \
        body.index("--notes-file") < body.index("BBB")
    assert [flag for flag, _ in got.files] == ["--body-file", "--notes-file"]
    rt.discard(got)


def test_a_heredoc_body_is_read_out_of_the_command_string(tmp_path):
    command = ("gh pr create --body-file - <<'EOF'\n"
               "the heredoc body\n"
               "EOF")
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Subject)
    assert "the heredoc body" in _text_of(got)
    rt.discard(got)


def test_an_unquoted_heredoc_terminator_resolves_the_same_way(tmp_path):
    command = "gh pr create --body-file - <<EOF\nunquoted body\nEOF"
    got = _resolve(command, tmp_path)
    assert "unquoted body" in _text_of(got)
    rt.discard(got)


def test_a_dash_suppressed_heredoc_resolves(tmp_path):
    command = "gh pr create --body-file - <<-EOF\n\tindented body\n\tEOF"
    got = _resolve(command, tmp_path)
    assert "indented body" in _text_of(got)
    rt.discard(got)


def test_one_heredoc_and_one_reader_bind_to_each_other(tmp_path):
    """The case daimon can answer without guessing."""
    got = _resolve("gh pr create --body-file - <<'EOF'\nthe only body\nEOF",
                   tmp_path)
    assert isinstance(got, rt.Subject)
    assert "the only body" in _text_of(got)
    rt.discard(got)


def test_a_heredoc_belonging_to_another_command_is_never_borrowed(tmp_path):
    """A heredoc redirect belongs to the simple command it is attached to.
    Here it belongs to `cat`, and the governed command reads standard input
    from somewhere daimon cannot see. Binding it anyway builds the subject
    from text the governed command never reads, and if THAT text is clean the
    record says the body was proven safe."""
    command = ("cat <<'EOF' > note.txt\n"
               "JUNK\n"
               "EOF\n"
               "gh pr create -F -")
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_a_heredoc_binds_inside_its_own_command_even_beside_another(tmp_path):
    """Two commands, one heredoc each: neither is ambiguous, so the governed
    one resolves and it resolves to ITS heredoc."""
    command = ("cat <<'EOF' > note.txt\n"
               "JUNK\n"
               "EOF\n"
               "gh pr create --body-file - <<'BODY'\n"
               "the real body\n"
               "BODY")
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    # Past the separator: the subject opens with the whole command string,
    # which quotes both heredocs, so a search over all of it proves nothing
    # about which one was READ.
    body = _text_of(got).split(rt.SUBJECT_SEPARATOR, 1)[1]
    assert "the real body" in body
    assert "JUNK" not in body
    rt.discard(got)


def test_a_trailing_command_after_the_heredoc_does_not_break_the_binding(
        tmp_path):
    got = _resolve("gh pr create -F - <<EOF\nthe body\nEOF && echo done",
                   tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    assert "the body" in _text_of(got)
    rt.discard(got)


def test_two_readers_and_one_heredoc_is_a_guess_too(tmp_path):
    """The other side of the same count, now inside one simple command. Two
    arguments reading standard input and one heredoc: whichever one daimon
    picks, the other is read from somewhere it cannot see."""
    command = ("gh pr create --body-file - --notes-file - <<'EOF'\n"
               "shared\n"
               "EOF")
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_a_pipeline_still_reports_the_pipe_rather_than_the_form(tmp_path):
    """When a `|` feeds the governed command, daimon knows exactly why it
    cannot read the bytes, and says that instead of blaming the argument."""
    got = _resolve("printf x | gh pr create -F -", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "stdin-pipe"


def test_a_dash_fed_by_a_pipe_is_unresolved_as_stdin_pipe(tmp_path):
    """No heredoc in the command string means the bytes are arriving from a
    process daimon cannot see. Never clean."""
    got = _resolve("cat notes.md | gh pr create --body-file -", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "stdin-pipe"


def test_a_dash_after_a_flag_outside_the_table_is_arg_form_unparsed(tmp_path):
    got = _resolve("gh pr create --input -", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_an_at_path_outside_the_table_is_arg_form_unparsed(tmp_path):
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    got = _resolve(f"curl -d @{payload} https://x", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_a_literal_field_value_is_not_a_file_and_resolves(tmp_path):
    """`-F key=value` with no `@` names no file. Reporting unresolved here
    would make every gh api call unprovable for no reason."""
    got = _resolve("gh api repos/x -F name=daimon", tmp_path)
    assert isinstance(got, rt.Subject)
    assert got.files == ()
    rt.discard(got)


def test_a_command_that_cannot_be_tokenized_is_arg_form_unparsed(tmp_path):
    got = _resolve("gh pr create --title 'unbalanced", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


# ---- resolver: the file failure causes ------------------------------------


def test_a_missing_file_is_file_missing(tmp_path):
    got = _resolve(f"gh pr create --body-file {tmp_path}/gone.md", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-missing"


def test_an_unreadable_file_is_file_unreadable(tmp_path):
    if os.getuid() == 0:
        pytest.skip("root reads anything; the mode bit proves nothing")
    body = tmp_path / "locked.md"
    body.write_text("secret", encoding="utf-8")
    body.chmod(0o000)
    try:
        got = _resolve(f"gh pr create --body-file {body}", tmp_path)
        assert isinstance(got, rt.Unresolved)
        assert got.cause == "file-unreadable"
    finally:
        body.chmod(0o600)


def test_a_directory_argument_is_file_unreadable(tmp_path):
    (tmp_path / "adir").mkdir()
    got = _resolve(f"gh pr create --body-file {tmp_path}/adir", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-unreadable"


def test_a_file_over_the_cap_is_file_oversize(tmp_path):
    big = tmp_path / "big.md"
    big.write_bytes(b"x" * (rt.MAX_SUBJECT_FILE_BYTES + 1))
    got = _resolve(f"gh pr create --body-file {big}", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-oversize"


def test_a_file_exactly_at_the_cap_still_resolves(tmp_path):
    at = tmp_path / "at.md"
    at.write_bytes(b"x" * rt.MAX_SUBJECT_FILE_BYTES)
    got = _resolve(f"gh pr create --body-file {at}", tmp_path)
    assert isinstance(got, rt.Subject)
    rt.discard(got)


def test_the_cap_is_enforced_by_the_read_not_by_a_prior_stat(tmp_path):
    """Check-then-read is two answers about one file. A stat that decides the
    cap and an open that happens afterwards disagree whenever the file grows
    in between, and the read wins. Reading one byte past the cap and judging
    THAT is one answer."""
    big = tmp_path / "grows.md"
    big.write_bytes(b"x" * 16)
    real_open = rt.open if hasattr(rt, "open") else open

    def growing(path, *args, **kwargs):
        # Grow it at the moment of opening: a stat taken earlier is now stale.
        if str(path).endswith("grows.md"):
            with real_open(path, "wb") as handle:
                handle.write(b"x" * (rt.MAX_SUBJECT_FILE_BYTES + 1))
        return real_open(path, *args, **kwargs)

    import builtins
    original = builtins.open
    builtins.open = growing
    try:
        got = _resolve(f"gh pr create --body-file {big}", tmp_path)
    finally:
        builtins.open = original
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-oversize"


def test_a_file_that_is_not_utf8_is_file_binary(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\xff\xfe\x00\x01")
    got = _resolve(f"gh pr create --body-file {blob}", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-binary"


def test_the_first_failure_wins_and_no_subject_file_is_left_behind(
        tmp_path, monkeypatch):
    """Left to right, and fail-closed: one unreadable argument means daimon
    cannot prove the subject clean, whatever the other arguments say. The
    temp dir is redirected so the leftover assertion is about THIS call and
    not about whatever else is in /tmp."""
    sink = tmp_path / "tmpsink"
    sink.mkdir()
    monkeypatch.setattr(rt.tempfile, "tempdir", str(sink))
    ok = tmp_path / "ok.md"
    ok.write_text("fine", encoding="utf-8")
    got = _resolve(
        f"gh pr create --body-file {tmp_path}/gone.md --notes-file {ok}",
        tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-missing"
    assert list(sink.iterdir()) == [], "a subject file was left behind"


def test_every_cause_the_resolver_emits_is_a_declared_cause():
    assert {"file-missing", "file-unreadable", "file-oversize", "file-binary",
            "stdin-pipe", "arg-form-unparsed"} <= rt.CAUSES


def test_resolve_never_raises_on_a_command_that_is_not_a_string(tmp_path):
    got = rt.resolve(None, str(tmp_path))
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


# ---- runner (spec 3.3) ----------------------------------------------------


import hashlib  # noqa: E402


def _body(tmp_path, text, name="check.sh"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    path.chmod(0o500)
    return path


def _subject(tmp_path, command="gh pr create --title x"):
    got = rt.resolve(command, str(tmp_path))
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    return got


def _armed(tmp_path, body, ruling_id="r-abc123", pin=True):
    sha = hashlib.sha256(Path(body).read_bytes()).hexdigest()
    return {"ruling_id": ruling_id, "project_dir": str(tmp_path),
            "match": "gh pr create", "intent": "warn",
            "sha256": sha if pin else "f" * 64, "armed_at": "t",
            "body_path": str(body)}


def test_a_check_that_exits_zero_is_clean(tmp_path):
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause, got.exit_code) == ("clean", "", 0)
    assert got.duration_ms >= 0
    rt.discard(subject)


def test_a_check_that_exits_one_is_a_violation_carrying_its_first_line(
        tmp_path):
    subject = _subject(tmp_path)
    body = _body(tmp_path, "echo 'the post names a machine' >&2\n"
                           "echo 'second line' >&2\n"
                           "exit 1\n")
    got = rt.run(body, subject, cwd=str(tmp_path), timeout=5)
    assert (got.outcome, got.cause, got.exit_code) == ("violation", "", 1)
    assert got.reason == "the post names a machine"
    rt.discard(subject)


def test_a_violation_with_no_stderr_still_says_something(tmp_path):
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 1\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert got.outcome == "violation"
    assert got.reason, "a violation with an empty reason renders as blank"
    rt.discard(subject)


def test_any_other_exit_code_is_unresolved_never_a_violation(tmp_path):
    """Exit 5 is a script that broke, not a subject that failed. Folding it
    into violation would deny actions on the strength of a typo."""
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 5\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause, got.exit_code) == \
        ("unresolved", "check-crashed", 5)
    rt.discard(subject)


# What `sh` IS differs by platform, and so does what it says when a script
# operand is missing. Measured on both:
#
#   dash (Debian /bin/sh)      exit 2    "cannot open <path>: No such file"
#   bash and macOS /bin/sh     exit 127  "<path>: No such file or directory"
#
# The claim these tests make is that neither number is 0 or 1, which is what
# puts the run in `check-crashed` rather than in `clean` or `violation`. The
# number itself is the shell's business. Pinning 127 passed on macOS and
# failed every Linux lane, so do not re-pin it.
_SHELL_MISSING_WORDINGS = ("nonexistent", "not found", "can't open",
                           "cannot open", "no such file")


def test_a_body_that_reaches_outside_itself_is_caught_by_the_runner(tmp_path):
    """The slice 1 path refusal catches a bare single-token path. A one-line
    `sh /host/path.sh` body passes that and is observable only here: sh fails
    to open the script and its own first stderr line becomes the reason."""
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "sh /nonexistent/host.sh\n"), subject,
                 cwd=str(tmp_path), timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "check-crashed")
    assert got.exit_code not in (0, 1), \
        "0 would read as clean and 1 as a violation the check never found"
    assert any(word in got.reason.casefold()
               for word in _SHELL_MISSING_WORDINGS), got.reason
    rt.discard(subject)


def test_a_body_that_runs_a_non_executable_file_is_check_crashed(tmp_path):
    subject = _subject(tmp_path)
    plain = tmp_path / "plain.txt"
    plain.write_text("not a program\n", encoding="utf-8")
    plain.chmod(0o644)
    got = rt.run(_body(tmp_path, f"{plain}\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "check-crashed")
    # 126 on both shells measured, and still not pinned: what this test is
    # for is that a body which cannot execute what it names lands outside
    # clean and violation, not that the shell picked a particular number.
    assert got.exit_code not in (0, 1)
    assert got.reason, "sh said why, and the runner must carry it"
    rt.discard(subject)


def test_a_check_that_overruns_its_budget_is_unresolved_not_clean(tmp_path):
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "sleep 30\n"), subject, cwd=str(tmp_path),
                 timeout=0.5)
    assert (got.outcome, got.cause) == ("unresolved", "check-timeout")
    assert got.duration_ms > 0
    rt.discard(subject)


def test_the_budget_kills_the_whole_process_group(tmp_path):
    """start_new_session puts the check in its own group, so a body that
    backgrounded something is killed WITH it. Killing only the direct child
    leaves a grandchild holding the pipe and the next read blocks past the
    host timeout, which is fail-open."""
    subject = _subject(tmp_path)
    marker = tmp_path / "alive"
    body = _body(tmp_path, f"(sleep 20; echo yes > {marker}) &\nsleep 20\n")
    got = rt.run(body, subject, cwd=str(tmp_path), timeout=0.5)
    assert got.cause == "check-timeout"
    assert not marker.exists()
    rt.discard(subject)


def test_a_host_without_sh_reports_runtime_missing(tmp_path, monkeypatch):
    subject = _subject(tmp_path)
    monkeypatch.setenv("PATH", "")
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "runtime-missing")
    rt.discard(subject)


def test_a_body_that_no_longer_hashes_to_its_pin_never_runs(tmp_path):
    """Someone edited the materialized body. Re-hash before exec, and the
    mismatch is unresolved rather than a silent skip."""
    subject = _subject(tmp_path)
    body = _body(tmp_path, "exit 0\n")
    entry = _armed(tmp_path, body, pin=False)
    got = rt.run(entry, subject, cwd=str(tmp_path), timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "body-hash-mismatch")
    assert got.exit_code == -1, "the check must not have run at all"
    rt.discard(subject)


def test_a_body_that_matches_its_pin_runs(tmp_path):
    subject = _subject(tmp_path)
    entry = _armed(tmp_path, _body(tmp_path, "exit 0\n"))
    assert rt.run(entry, subject, cwd=str(tmp_path), timeout=5).outcome == \
        "clean"
    rt.discard(subject)


def test_a_missing_body_file_is_unresolved(tmp_path):
    subject = _subject(tmp_path)
    got = rt.run(tmp_path / "gone.sh", subject, cwd=str(tmp_path), timeout=5)
    assert got.outcome == "unresolved"
    assert got.cause == "check-crashed"
    rt.discard(subject)


def test_the_check_is_handed_the_subject_the_command_and_the_ruling(tmp_path):
    subject = _subject(tmp_path, "gh pr create --title 'a title'")
    out = tmp_path / "seen"
    entry = _armed(tmp_path, _body(
        tmp_path,
        f'printf "%s\\n%s\\n%s\\n" "$DAIMON_CHECK_RULING" '
        f'"$DAIMON_CHECK_COMMAND" "$(cat "$DAIMON_CHECK_SUBJECT")" > {out}\n'))
    assert rt.run(entry, subject, cwd=str(tmp_path), timeout=5).outcome == \
        "clean"
    seen = out.read_text(encoding="utf-8")
    assert seen.startswith("r-abc123\n")
    assert "gh pr create --title 'a title'" in seen
    rt.discard(subject)


def test_the_check_runs_in_the_action_s_working_directory(tmp_path):
    subject = _subject(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    out = tmp_path / "pwd"
    body = _body(tmp_path, f"pwd > {out}\n")
    assert rt.run(body, subject, cwd=str(work), timeout=5).outcome == "clean"
    assert Path(out.read_text(encoding="utf-8").strip()).resolve() == \
        work.resolve()
    rt.discard(subject)


def test_the_check_reads_standard_input_as_empty_and_does_not_hang(tmp_path):
    """stdin is /dev/null, never an open pipe. A body that reads stdin gets
    EOF; an inherited terminal would block until the budget expires and
    report check-timeout for a check that was fine."""
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "cat > /dev/null\nexit 0\n"), subject,
                 cwd=str(tmp_path), timeout=5)
    assert got.outcome == "clean"
    rt.discard(subject)


def test_the_body_does_not_inherit_the_parent_environment(tmp_path,
                                                          monkeypatch):
    """A ratified body is human-armed and runs as the user, so this is not a
    sandbox. It is a blast radius: the body has one job, reading a subject
    file, and handing it every token in the host session widens what a
    mistake in someone else's script can reach for no gain."""
    monkeypatch.setenv("MY_API_TOKEN", "zqx-should-not-travel")
    subject = _subject(tmp_path)
    out = tmp_path / "seen-env"
    got = rt.run(_body(tmp_path, f"env > {out}\nexit 0\n"), subject,
                 cwd=str(tmp_path), timeout=5)
    assert got.outcome == "clean", got
    seen = out.read_text(encoding="utf-8")
    assert "zqx-should-not-travel" not in seen
    assert "MY_API_TOKEN" not in seen
    rt.discard(subject)


def test_the_body_still_gets_what_it_needs_to_run(tmp_path):
    """The other half: a minimal environment that dropped PATH would make
    every body fail to find its own tools, which reads as check-crashed."""
    subject = _subject(tmp_path)
    out = tmp_path / "seen-env"
    entry = _armed(tmp_path, _body(tmp_path, f"env > {out}\nexit 0\n"))
    assert rt.run(entry, subject, cwd=str(tmp_path), timeout=5).outcome == \
        "clean"
    names = {line.split("=", 1)[0]
             for line in out.read_text(encoding="utf-8").splitlines()
             if "=" in line}
    assert {"PATH", "DAIMON_CHECK_SUBJECT", "DAIMON_CHECK_COMMAND",
            "DAIMON_CHECK_RULING"} <= names
    rt.discard(subject)


def test_the_runner_never_raises(tmp_path, monkeypatch):
    """Proven by monkeypatching the failure in. The hook fires before every
    shell action; an exception here is an action that proceeds with no record
    of why."""
    subject = _subject(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("the platform said no")

    monkeypatch.setattr(rt.subprocess, "Popen", boom)
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "check-crashed")
    assert "the platform said no" in got.reason
    rt.discard(subject)


def test_every_cause_the_runner_emits_is_a_declared_cause():
    assert {"check-timeout", "check-crashed", "body-hash-mismatch",
            "runtime-missing"} <= rt.CAUSES


def test_the_body_file_name_is_the_ruling_and_the_head_of_its_hash():
    entry = {"ruling_id": "r-1a2b3c4d5e6f", "sha256": "ab" * 32}
    assert rt.body_name(entry) == "r-1a2b3c4d5e6f-abababababab.sh"
    assert rt.body_path(entry).parent == rt.checks_dir()


def test_run_never_leaves_the_subject_behind_for_its_caller(tmp_path):
    """The runner does not own the subject file: resolve made it and the
    caller discards it. Pinned so a later change cannot quietly move that
    responsibility and leave a double unlink."""
    subject = _subject(tmp_path)
    rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path), timeout=5)
    assert Path(subject.path).exists()
    rt.discard(subject)


# ---- firing log (spec 3.4) ------------------------------------------------


import json  # noqa: E402


def _log_lines():
    path = config.log_dir() / "checks.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_firing_row_carries_the_declared_keys_and_nothing_else():
    assert rt.log_firing({"ruling_id": "r-1", "host": "claude-code",
                          "mode": "warn", "outcome": "clean", "cause": "",
                          "decision_emitted": "allow",
                          "duration_ms": 12}) is True
    rows = _log_lines()
    assert len(rows) == 1
    assert set(rows[0]) == set(rt.FIRING_KEYS)


def test_the_log_never_carries_the_command_the_paths_or_the_reason():
    """Spec 3.4 in its own words: no command text, no paths, no subject. The
    reason shown to the agent may name a path; the log does not. Enforced by
    PROJECTION rather than by asking callers to be careful."""
    secret = "/Users/someone/private/notes.md"
    rt.log_firing({"ruling_id": "r-1", "outcome": "unresolved",
                   "cause": "file-missing", "duration_ms": 3,
                   "reason": f"{secret} does not exist",
                   "command": f"gh pr create --body-file {secret}",
                   "subject": "/tmp/daimon-check-xyz.subject"})
    raw = (config.log_dir() / "checks.jsonl").read_text(encoding="utf-8")
    assert secret not in raw
    assert "gh pr create" not in raw
    assert "daimon-check-xyz" not in raw


def test_a_cause_outside_the_declared_set_is_recorded_without_its_text():
    """The cause field is the one place free text could reach a no-plaintext
    log. An unknown value still signals that something happened; it just
    cannot bring a path along with it."""
    rt.log_firing({"ruling_id": "r-1", "outcome": "unresolved",
                   "cause": "exploded reading /Users/someone/x", "duration_ms": 1})
    rows = _log_lines()
    assert rows[0]["cause"] == "unknown"


@pytest.mark.parametrize("cause", sorted(
    {"file-missing", "no-manifest", "no-match", "manifest-unreadable",
     "check-timeout", "body-hash-mismatch"}))
def test_the_causes_a_hook_actually_emits_survive_the_projection(cause):
    rt.log_firing({"ruling_id": "r-1", "outcome": "unresolved",
                   "cause": cause, "duration_ms": 1})
    assert _log_lines()[0]["cause"] == cause


def test_a_row_with_no_timestamp_is_stamped_in_utc():
    rt.log_firing({"ruling_id": "r-1", "outcome": "clean", "duration_ms": 1})
    ts = _log_lines()[0]["ts"]
    assert ts.endswith("Z") and ts[4] == "-" and "T" in ts


def test_rows_append_rather_than_replace():
    rt.log_firing({"ruling_id": "r-1", "outcome": "clean", "duration_ms": 1})
    rt.log_firing({"ruling_id": "r-2", "outcome": "violation", "duration_ms": 2})
    assert [r["ruling_id"] for r in _log_lines()] == ["r-1", "r-2"]


def test_the_log_directory_is_created_on_first_write():
    assert not (config.log_dir() / "checks.jsonl").exists()
    assert rt.log_firing({"ruling_id": "r-1", "outcome": "clean"}) is True
    assert (config.log_dir() / "checks.jsonl").exists()


def test_a_log_that_cannot_be_written_reports_false_and_never_raises(
        monkeypatch, tmp_path):
    """A hook that cannot write its own log still has an action to allow or
    deny. The write failing is not a reason to lose the decision."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("DAIMON_LOG_DIR", str(blocker))
    assert rt.log_firing({"ruling_id": "r-1", "outcome": "clean"}) is False


def test_the_firing_log_is_declared_in_the_surface_registry():
    from daimon_briefing import surfaces
    entry = [s for s in surfaces.SURFACES if s.shape == "logs/checks.jsonl"]
    assert entry, "a new file under ~/.daimon must be declared"
    assert entry[0].plaintext is False
    assert entry[0].delete == "exempt-no-plaintext"


# ---- the cap (#955) -------------------------------------------------------


def _fill_log(rows):
    """Write `rows` fat rows straight to the log, bypassing log_firing, so a
    test reaches the cap in one write rather than in thousands of appends."""
    path = config.log_dir() / "checks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for i in range(rows):
            handle.write(json.dumps({
                "ts": "2026-09-0%dT00:00:00Z" % (i % 9 + 1),
                "ruling_id": "q-%08d" % i, "host": "claude-code",
                "mode": "warn", "outcome": "clean", "cause": "",
                "decision_emitted": "allow", "duration_ms": i,
            }) + "\n")
    return path


def test_the_firing_log_is_capped_the_way_the_crash_log_is():
    """#955. Every shell action on a hooked host appends a row and nothing
    trimmed them, so the log grew forever and `daimon status` folded all of
    it. The numbers are the crash log's own."""
    assert rt.FIRING_LOG_MAX_BYTES == 262144
    assert rt.FIRING_LOG_KEEP_BYTES == 65536
    path = _fill_log(3000)
    assert path.stat().st_size > rt.FIRING_LOG_MAX_BYTES
    assert rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"}) is True
    assert path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES


def test_every_line_of_a_trimmed_log_is_a_whole_row():
    """The cut lands mid-line, so the trim drops forward to the next line
    boundary. A half row at the head would be a parse error on every read of
    the file, forever."""
    path = _fill_log(3000)
    rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"})
    assert path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES
    rows = _log_lines()
    assert len(rows) > 1
    assert all(set(row) == set(rt.FIRING_KEYS) for row in rows)


def test_the_newest_row_survives_the_trim():
    """The trim runs after the append, so the row that triggered it is in
    the kept tail. A cap that dropped the row it was writing would lose the
    firing it was called to record."""
    path = _fill_log(3000)
    rt.log_firing({"ruling_id": "q-newest", "outcome": "violation"})
    assert path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES
    assert _log_lines()[-1]["ruling_id"] == "q-newest"


def test_a_log_under_the_cap_is_left_exactly_as_it_was():
    _fill_log(10)
    path = config.log_dir() / "checks.jsonl"
    rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"})
    before = path.read_bytes()
    rt.trim_firing_log(path)
    assert path.read_bytes() == before


def test_a_trim_that_fails_leaves_the_bytes_alone_and_still_reports_true(
        monkeypatch):
    """The append is the product; the trim is housekeeping. A trim that
    raised into the hook would cost the record it just wrote."""
    path = _fill_log(3000)
    real_open = Path.open

    def refuse(self, mode="r", *args, **kwargs):
        if "b" in mode:
            raise OSError("no rewrite here")
        return real_open(self, mode, *args, **kwargs)

    before = path.read_bytes()
    monkeypatch.setattr(Path, "open", refuse)
    assert rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"}) is True
    monkeypatch.setattr(Path, "open", real_open)
    after = path.read_bytes()
    # Nothing was rewritten: the old bytes are still at the head, and the
    # only difference is the row this call appended.
    assert after.startswith(before)
    assert json.loads(after[len(before):])["ruling_id"] == "q-newest"
    assert path.stat().st_size > rt.FIRING_LOG_MAX_BYTES


def _write_raw(*chunks):
    path = config.log_dir() / "checks.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for chunk in chunks:
            handle.write(chunk)
    return path


def _fat_row(size, rid="q-giant"):
    """One syntactically whole row padded out to `size` bytes."""
    row = {"ts": "2026-09-06T00:00:00Z", "ruling_id": rid,
           "host": "claude-code", "mode": "warn", "outcome": "clean",
           "cause": "", "decision_emitted": "allow", "duration_ms": 0}
    row["host"] = "claude-code" + "x" * size
    return json.dumps(row).encode("utf-8") + b"\n"


def test_a_row_larger_than_the_keep_window_is_never_destroyed():
    """The window holds no line boundary, so there is no whole row to keep.
    Emptying the file here would take the row that was just written with it,
    and every surface would flip from `fired N times` to `never fired` --
    which the docs define as a different answer, not a smaller one."""
    _fill_log(1200)
    path = _write_raw(_fat_row(70000))
    before = path.read_bytes()
    assert path.stat().st_size > rt.FIRING_LOG_MAX_BYTES
    rt.trim_firing_log(path)
    assert path.read_bytes() == before
    assert json.loads(before.splitlines()[-1])["ruling_id"] == "q-giant"


def test_an_append_onto_such_a_log_still_lands_and_reports_true():
    _fill_log(1200)
    path = _write_raw(_fat_row(70000))
    assert rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"}) is True
    assert _log_lines()[-1]["ruling_id"] == "q-newest"
    assert path.stat().st_size > 0


def test_a_torn_last_line_is_dropped_rather_than_kept():
    """A row without its newline cannot parse, and keeping it at the end of
    the window means the next append is glued onto it."""
    _fill_log(3000)
    path = _write_raw(b'{"ts": "2026-09-06T00:00:00Z", "ruling_id": "q-torn"')
    rt.trim_firing_log(path)
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"q-torn" not in raw
    assert path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES
    assert all(set(row) == set(rt.FIRING_KEYS) for row in _log_lines())


class _TruncateFails:
    """A file object that refuses to truncate `fails` times, then behaves."""

    def __init__(self, handle, fails):
        self._handle = handle
        self._left = fails

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def truncate(self, *args):
        if self._left:
            self._left -= 1
            raise OSError("no truncate today")
        return self._handle.truncate(*args)


def _truncate_breaker(monkeypatch, fails):
    """Returns the restore call. NEVER monkeypatch.undo() here: the autouse
    fixture that isolates HOME and DAIMON_LOG_DIR shares this monkeypatch
    object, and undoing everything drops the test back onto the caller's real
    environment mid-assertion."""
    real_open = Path.open

    def opener(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if "b" in mode and "+" in mode:
            return _TruncateFails(handle, fails)
        return handle

    monkeypatch.setattr(Path, "open", opener)
    return lambda: monkeypatch.setattr(Path, "open", real_open)


def test_a_truncate_that_fails_twice_leaves_the_original_bytes(monkeypatch):
    """In place means the rewrite has two steps, and a failure between them
    would leave the kept tail sitting on top of the rows it was meant to
    replace: every count folded from that file doubles. The head is read
    before it is overwritten so the file can be put back exactly."""
    path = _fill_log(3000)
    before = path.read_bytes()
    restore = _truncate_breaker(monkeypatch, fails=2)
    assert rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"}) is True
    restore()
    after = path.read_bytes()
    assert after.startswith(before)
    assert json.loads(after[len(before):])["ruling_id"] == "q-newest"
    assert all(set(row) == set(rt.FIRING_KEYS) for row in _log_lines())


def test_a_truncate_that_fails_once_is_retried(monkeypatch):
    path = _fill_log(3000)
    restore = _truncate_breaker(monkeypatch, fails=1)
    assert rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"}) is True
    restore()
    assert path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES
    assert _log_lines()[-1]["ruling_id"] == "q-newest"


def test_the_file_is_either_the_original_or_the_trimmed_tail(monkeypatch):
    """Whatever the truncate does, the file never holds a trimmed head
    followed by the middle of what it replaced."""
    for fails in (0, 1, 2):
        for stale in list((config.log_dir()).glob("checks.jsonl")):
            stale.unlink()
        path = _fill_log(3000)
        before = path.read_bytes()
        restore = _truncate_breaker(monkeypatch, fails=fails)
        rt.log_firing({"ruling_id": "q-newest", "outcome": "clean"})
        restore()
        raw = path.read_bytes()
        trimmed = path.stat().st_size <= rt.FIRING_LOG_KEEP_BYTES
        assert trimmed or raw.startswith(before)
        for line in raw.splitlines():
            assert set(json.loads(line)) == set(rt.FIRING_KEYS)


def test_the_trim_never_raises_on_a_log_that_is_not_there():
    rt.trim_firing_log(config.log_dir() / "checks.jsonl")


def test_the_firing_log_entry_precedes_the_generic_log_glob():
    """The registry is order-sensitive: `logs/*.log` would not catch a
    .jsonl, but the specific declaration still has to sit with the other
    log shapes rather than after the catch-all."""
    from daimon_briefing import surfaces
    shapes = [s.shape for s in surfaces.SURFACES]
    assert shapes.index("logs/checks.jsonl") < shapes.index("logs/*.log")


# ---- the fallback branches, each exercised rather than excused ------------
#
# Every test below drives a path that only runs when something has already
# gone wrong. They exist because those are exactly the paths that decide
# whether a hook allows an action with a record saying why, or crashes and
# lets it through saying nothing.


def test_a_manifest_that_is_not_utf8_reads_as_unreadable():
    path = config.checks_dir() / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe\x00 not text")
    loaded = rt.load_manifest()
    assert loaded.entries == []
    assert loaded.reason == "manifest-unreadable"


def test_a_manifest_that_cannot_be_opened_at_all_reads_as_unreadable(tmp_path):
    """A directory where the file should be. Distinct from absent: daimon
    put something there and can no longer read it."""
    target = tmp_path / "manifest.json"
    target.mkdir()
    assert rt.load_manifest(target).reason == "manifest-unreadable"


def test_a_cwd_that_cannot_be_resolved_arms_nothing(tmp_path):
    """An embedded NUL makes realpath raise rather than return. The hook
    still has an action in front of it and needs an answer."""
    root = os.path.realpath(str(tmp_path))
    assert rt.armed_for("\x00not-a-path", _manifest(root)) == []


def test_a_non_object_entry_in_a_hand_built_manifest_is_skipped(tmp_path):
    """load_manifest filters these, but armed_for is called with whatever a
    caller holds, and one bad row must not disarm the good ones."""
    root = os.path.realpath(str(tmp_path))
    manifest = rt.Manifest(["nope", 7, {"ruling_id": "r-0",
                                        "project_dir": root, "match": "x"}], "")
    assert [e["ruling_id"] for e in rt.armed_for(root, manifest)] == ["r-0"]


def test_a_command_that_forges_a_heredoc_marker_is_not_read_as_one(tmp_path):
    """The marker is minted by the substitution and by nothing else, but the
    command string arrives from a host payload and can spell one out. An
    index no heredoc answers to is not a heredoc.

    Before the bounds check this raised IndexError out of a module whose
    whole contract is that it never raises."""
    forged = f"gh pr create --body-file - {rt._HEREDOC_MARK}0\x00"
    got = _resolve(forged, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_a_forged_marker_with_a_junk_index_is_a_plain_argument(tmp_path):
    """Not a marker, so it is read as the path it looks like. `open` refuses
    a NUL before the OS sees it, and it raises ValueError rather than
    OSError, which walked straight past the clause meant to catch it."""
    forged = f"gh pr create --body-file {rt._HEREDOC_MARK}not-a-number\x00"
    got = _resolve(forged, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "file-unreadable"


def test_a_flag_at_the_very_end_with_no_value_is_unparsed(tmp_path):
    got = _resolve("gh pr create --body-file", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"
    assert "no value" in got.reason


def test_a_field_flag_given_a_bare_at_path_reads_it(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("the body text\n", encoding="utf-8")
    got = _resolve(f"gh api repos/x -F @{body}", tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    assert "the body text" in _text_of(got)
    rt.discard(got)


def test_a_field_only_flag_given_a_plain_word_names_no_file(tmp_path):
    """`--field` is not a body flag, so a value with neither `=` nor `@`
    names nothing to read and is not a reason to call the subject
    unprovable."""
    got = _resolve("gh api repos/x --field plain", tmp_path)
    assert isinstance(got, rt.Subject)
    assert got.files == ()
    rt.discard(got)


def test_a_flag_given_an_empty_path_is_unparsed(tmp_path):
    got = _resolve("gh pr create --body-file ''", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"
    assert "empty path" in got.reason


def test_a_temp_dir_that_refuses_the_subject_is_unresolved_not_a_crash(
        tmp_path, monkeypatch):
    """The resolver has read the arguments and has nowhere to put them. It
    still owes the hook an outcome."""
    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(rt.tempfile, "mkstemp", boom)
    got = _resolve("gh pr create --title x", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "check-crashed"
    assert "no space left" in got.reason


def test_a_subject_that_cannot_be_written_takes_its_temp_file_with_it(
        tmp_path, monkeypatch):
    sink = tmp_path / "sink"
    sink.mkdir()
    monkeypatch.setattr(rt.tempfile, "tempdir", str(sink))

    def boom(*args, **kwargs):
        raise OSError("the disk went away mid-write")

    monkeypatch.setattr(rt.os, "fdopen", boom)
    got = _resolve("gh pr create --title x", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "check-crashed"
    assert list(sink.iterdir()) == [], "a half-written subject was left behind"


def test_a_subject_whose_cleanup_also_fails_still_reports(
        tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("write failed")

    def also_boom(*args, **kwargs):
        raise OSError("and so did the cleanup")

    monkeypatch.setattr(rt.os, "fdopen", boom)
    monkeypatch.setattr(rt.os, "unlink", also_boom)
    got = _resolve("gh pr create --title x", tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "check-crashed"


@pytest.mark.parametrize("value", [None, "", 7, object()])
def test_discard_ignores_anything_that_is_not_a_subject(value):
    rt.discard(value)  # must not raise
    rt.discard(rt.Subject(value if isinstance(value, str) else "", (), ""))


def test_discard_survives_a_file_that_is_already_gone(tmp_path):
    """It runs in a `finally` on a path that already has an outcome to
    report, so a second failure there must not replace it."""
    rt.discard(rt.Subject(str(tmp_path / "never-existed"), (), ""))


def test_the_budget_falls_back_to_killing_the_child_alone(tmp_path,
                                                          monkeypatch):
    """A platform without process groups, or a group that vanished between
    the timeout and the kill. The check must still stop."""
    calls = []

    def no_groups(*args, **kwargs):
        raise ProcessLookupError("no such process group")

    monkeypatch.setattr(rt.os, "killpg", no_groups)
    real_popen = rt.subprocess.Popen

    def watched(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        original = proc.kill
        proc.kill = lambda: (calls.append("kill"), original())[1]
        return proc

    monkeypatch.setattr(rt.subprocess, "Popen", watched)
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "sleep 30\n"), subject, cwd=str(tmp_path),
                 timeout=0.5)
    assert got.cause == "check-timeout"
    assert calls == ["kill"], "the fallback kill never ran"
    rt.discard(subject)


def test_a_spawn_that_fails_for_any_other_reason_is_check_crashed(
        tmp_path, monkeypatch):
    """Not a missing sh: an exec that the platform refused. Different cause,
    because runtime-missing tells an operator to install a shell."""
    def boom(*args, **kwargs):
        raise OSError(12, "Cannot allocate memory")

    monkeypatch.setattr(rt.subprocess, "Popen", boom)
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=5)
    assert (got.outcome, got.cause) == ("unresolved", "check-crashed")
    assert "Cannot allocate memory" in got.reason
    rt.discard(subject)


def test_a_reap_that_fails_after_the_kill_still_reports_the_timeout(
        tmp_path, monkeypatch):
    """The outcome is already decided by the time the corpse is collected.
    A second failure there must not turn a timeout into a traceback."""
    class Stubborn:
        pid = -1
        returncode = None

        def communicate(self, timeout=None):
            raise rt.subprocess.TimeoutExpired("sh", timeout or 0)

        def kill(self):
            pass

    monkeypatch.setattr(rt.subprocess, "Popen",
                        lambda *a, **k: Stubborn())
    monkeypatch.setattr(rt.os, "killpg",
                        lambda *a, **k: None)
    monkeypatch.setattr(rt.os, "getpgid", lambda pid: pid)
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=0.01)
    assert (got.outcome, got.cause) == ("unresolved", "check-timeout")
    rt.discard(subject)


def test_a_log_directory_that_cannot_be_made_reports_false(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(rt.Path, "mkdir", boom)
    assert rt.log_firing({"ruling_id": "r-1", "outcome": "clean"}) is False


def test_a_firing_row_whose_duration_is_not_a_number_reports_false():
    assert rt.log_firing({"ruling_id": "r-1", "outcome": "clean",
                          "duration_ms": "soon"}) is False


def test_a_child_that_cannot_be_killed_at_all_still_reports_the_timeout(
        tmp_path, monkeypatch):
    """Both kills refused: no process group, and the direct kill fails too
    (the child is already a zombie, or the platform said no). The outcome
    was decided at the timeout; nothing after it may replace that with a
    traceback."""
    class Unkillable:
        pid = -1
        returncode = None

        def communicate(self, timeout=None):
            raise rt.subprocess.TimeoutExpired("sh", timeout or 0)

        def kill(self):
            raise OSError("no such process")

    def no_groups(*args, **kwargs):
        raise ProcessLookupError("no such process group")

    monkeypatch.setattr(rt.subprocess, "Popen", lambda *a, **k: Unkillable())
    monkeypatch.setattr(rt.os, "killpg", no_groups)
    subject = _subject(tmp_path)
    got = rt.run(_body(tmp_path, "exit 0\n"), subject, cwd=str(tmp_path),
                 timeout=0.01)
    assert (got.outcome, got.cause) == ("unresolved", "check-timeout")
    rt.discard(subject)


# ---- the resolver's own input bound (scar 0022, second door) --------------


def test_a_command_at_the_cap_still_resolves(tmp_path):
    command = "gh pr create --title " + "A" * (
        rt.MAX_COMMAND_BYTES - len("gh pr create --title "))
    assert len(command.encode("utf-8")) == rt.MAX_COMMAND_BYTES
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    rt.discard(got)


def test_a_command_over_the_cap_is_unresolved_never_skipped(tmp_path):
    """One byte past the cap. Unresolved, not allowed: a command daimon
    declined to parse is one it cannot prove anything about."""
    command = "gh pr create --title " + "A" * rt.MAX_COMMAND_BYTES
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"
    assert "too long" in got.reason


def test_the_cap_counts_bytes_not_characters(tmp_path):
    command = "gh pr create --title " + "é" * rt.MAX_COMMAND_BYTES
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Unresolved)
    assert got.cause == "arg-form-unparsed"


def test_a_large_heredoc_body_is_not_what_the_cap_counts(tmp_path):
    """The heredoc is stripped before the cap is measured, so a two megabyte
    PR body still resolves. The cost is in tokenizing a long inline
    ARGUMENT, and that is what the cap bounds."""
    body = "x" * (2 * 1024 * 1024)
    command = f"gh pr create --body-file - <<'EOF'\n{body}\nEOF"
    got = _resolve(command, tmp_path)
    assert isinstance(got, rt.Subject), getattr(got, "cause", "")
    assert body in _text_of(got)
    rt.discard(got)


def test_a_megabyte_of_inline_argument_returns_well_inside_the_budget(
        tmp_path):
    """Measured before the cap existed: 5.3s at 512 KiB and 21s at 1 MiB on
    the author's machine, against a 10s host hook timeout that is fail-open.
    A hook that outlives it does not block and the action proceeds with no
    record, so the resolver has to decline long before then."""
    import time as _time
    command = "gh pr create --title " + "A" * (1024 * 1024)
    started = _time.monotonic()
    got = _resolve(command, tmp_path)
    elapsed = _time.monotonic() - started
    assert isinstance(got, rt.Unresolved)
    assert elapsed < 0.5, f"resolve took {elapsed:.2f}s on a 1 MiB command"
