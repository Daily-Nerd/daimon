"""#943 slice 2: the standalone check runtime.

`checks_runtime.py` is loaded by host hook scripts that cannot import the
venv-only package, so every test here loads the SHIPPED copy by file location
under its own module name — the way a hook will. Byte-identity with the
canonical module is a separate drift test (test_hooks_install.py); together
they mean these assertions bind the canonical file too.
"""

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from daimon_briefing import config

CANONICAL = (Path(__file__).parents[1] / "daimon_briefing"
             / "checks_runtime.py")
SHIPPED = (Path(__file__).parents[1] / "daimon_briefing" / "_hooks"
           / "checks_runtime.py")


def _runtime():
    """The shipped runtime, loaded standalone. A relative import or a
    `daimon_briefing` import in the canonical file fails HERE, which is the
    only place it can fail before a host hook hits it in production."""
    spec = importlib.util.spec_from_file_location(
        "_checks_runtime_under_test", SHIPPED)
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
