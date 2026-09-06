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
