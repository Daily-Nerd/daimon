"""One bucket identity (#1294): `scope.parse_bucket` is the only parser, and
every validator that accepts, lists or checks a bucket name delegates to it."""

import ast
import json
import random
import re
import sys
from pathlib import Path

import pytest

from daimon_briefing import config, requests, store, view

PKG = Path(__file__).resolve().parent.parent / "daimon_briefing"


def _legacy_slug_ok(name: str) -> bool:
    """What `requests._SLUG_RE.fullmatch` accepted on main 369db6a."""
    return re.fullmatch(r"[\w-]{1,255}", name) is not None


def _legacy_config_ok(tok: str) -> bool:
    """What `config.extra_read_slugs` accepted on main 369db6a (past the
    empty-token skip)."""
    return all(ch.isalnum() or ch in "_-" for ch in tok)


def _samples():
    rng = random.Random(1294)
    fixed = ["", "a", "-", "_", "-Users-k-daimon", "x@security", "@org", "a b",
             "a/b", "..", ".", ".chunk-cache", "a\n", "é", "日本", "a" * 255,
             "a" * 256, "x@", "٠١", "ǅ", "a‍b"]
    for _ in range(3000):
        n = rng.randint(0, 12)
        fixed.append("".join(chr(rng.choice(
            [rng.randint(0, 0x7F), rng.randint(0x80, 0x24FF),
             rng.randint(0x3000, 0x30FF), rng.randint(0x1F300, 0x1F64F)]))
            for _ in range(n)))
    return fixed


def test_scope_is_a_leaf_module():
    """stdlib only: nothing from the package, so every module can import it."""
    tree = ast.parse((PKG / "scope.py").read_text(encoding="utf-8"))
    stdlib = sys.stdlib_module_names
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in a leaf module"
            mods = [node.module or ""]
        else:
            continue
        for m in mods:
            assert m.split(".")[0] in stdlib, m


def test_project_slug_never_emits_a_character_outside_word_and_dash():
    from daimon_briefing import scope  # noqa: F401  (module must exist)
    for s in _samples():
        out = store.project_slug(s)
        if out is not None:
            assert re.fullmatch(r"[\w-]*", out), (s, out)
            assert "@" not in out


def test_every_project_slug_is_parsed_back_as_itself():
    from daimon_briefing import scope
    for s in _samples():
        out = store.project_slug(s)
        if out is None or len(out) > 255:
            continue
        b = scope.parse_bucket(out)
        assert b is not None and b.name == out


def test_parse_bucket_round_trips():
    from daimon_briefing import scope
    for s in _samples():
        b = scope.parse_bucket(s)
        if b is not None:
            assert scope.parse_bucket(b.name) == b
            assert b.name == s


def test_parse_bucket_matches_the_legacy_shape_exactly():
    from daimon_briefing import scope
    for s in _samples():
        assert (scope.parse_bucket(s) is not None) == _legacy_slug_ok(s), repr(s)


def test_parse_bucket_rejects_non_strings_and_at_names():
    from daimon_briefing import scope
    assert scope.parse_bucket(None) is None
    assert scope.parse_bucket(b"abc") is None
    assert scope.parse_bucket("x@security") is None
    assert scope.parse_bucket("@org") is None


def test_extra_read_slugs_only_differs_from_legacy_past_255_chars(monkeypatch):
    """The one place config's own test was not length-bounded. A token over 255
    characters cannot name a directory (NAME_MAX), so it never matched a
    bucket; reported on the issue, pinned here."""
    for s in _samples():
        if not s or "," in s or s != s.strip() or s in ("",):
            continue
        if "\x00" in s:
            continue  # cannot be put in an environment variable
        monkeypatch.setenv("DAIMON_EXTRA_READ_SLUGS", s)
        got = config.extra_read_slugs() == (s,)
        want = _legacy_config_ok(s) and len(s) <= 255
        assert got == want, repr(s)


def test_refutations_defines_no_slug_regex_of_its_own():
    """Checked on the AST: a comment can satisfy a text search (scar 0054)."""
    tree = ast.parse((PKG / "refutations.py").read_text(encoding="utf-8"))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    assert "_POLICY_SLUG_RE" not in assigned
    for n in ast.walk(tree):
        if (isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value in (r"[\w-]{1,255}", r"[\w-]+")):
            pytest.fail(f"slug regex literal at line {n.lineno}")


@pytest.mark.parametrize("mod", ["requests.py", "refutations.py"])
def test_no_validator_module_compiles_a_bucket_regex(mod):
    tree = ast.parse((PKG / mod).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "compile" and n.args \
                and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            assert "[\\w-]" not in n.args[0].value, (mod, n.lineno)


def _plant(name):
    d = config.checkpoint_dir() / name
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps({"session_id": "S"}))
    (d / "requests.jsonl").write_text("")


def test_a_planted_at_directory_is_not_listed_as_a_project(tmp_checkpoint_dir):
    from daimon_briefing.cli import projects as cli_projects
    _plant("-good")
    _plant("x@security")
    assert [b["slug"] for b in store.list_buckets()] == ["-good"]
    assert view.buckets("-good") == ("-good",)
    assert requests._bucket_slugs() == ["-good"]
    assert [r["slug"]
            for r in cli_projects.projects_rows()] == ["-good"]


# ---- #1294 round 2: a store directory is a bucket iff parse_bucket says so --


def _stray_ledger_dir(name="repo.x"):
    d = config.checkpoint_dir() / name
    d.mkdir(parents=True)
    (d / "verification.jsonl").write_text(json.dumps(
        {"ts": "2026-08-29T10:00:00Z", "check": "receipt",
         "item_ref": "o-444ddd", "reason": "receipt-invalid"}) + "\n")
    (d / "requests.jsonl").write_text("")
    (d / "latest.json").write_text("{}")
    return d


def test_internal_dirs_are_one_typed_set_in_scope():
    from daimon_briefing import scope
    assert scope.STORE_INTERNAL_DIRS == frozenset({".partials", ".chunk-cache"})
    assert scope.classify_dir(".partials") is scope.DirKind.INTERNAL
    assert scope.classify_dir(".chunk-cache") is scope.DirKind.INTERNAL
    assert scope.classify_dir("-Users-k-daimon") is scope.DirKind.BUCKET
    assert scope.classify_dir("repo.x") is scope.DirKind.STRAY
    assert scope.classify_dir(".other") is scope.DirKind.STRAY
    assert scope.classify_dir("x@security") is scope.DirKind.STRAY


def test_bucket_dirs_splits_buckets_internals_and_strays(tmp_checkpoint_dir):
    for n in ("-b", "-a", ".partials", ".chunk-cache", "repo.x", ".odd"):
        (tmp_checkpoint_dir / n).mkdir(parents=True)
    (tmp_checkpoint_dir / "S-flat.json").write_text("{}")
    found = store.bucket_dirs()
    assert [p.name for p in found.buckets] == ["-a", "-b"]
    assert found.strays == (".odd", "repo.x")


def test_the_fold_does_not_read_a_stray_dir_as_a_bucket(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    from tests.test_recall import _cp
    blob = _cp("S-dot", questions=[
        {"text": "the quokka stray bucket claim", "trust": "inferred",
         "id": "o-444ddd"}], created="2026-08-01T00:00:00Z")
    blob["author"] = "ada"
    blob["project_slug"] = "repo.x"
    tmp_checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (tmp_checkpoint_dir / "S-dot.json").write_text(json.dumps(blob))
    _stray_ledger_dir("repo.x")
    import sqlite3

    from daimon_briefing import recall
    recall.search("quokka stray bucket claim", all_projects=True)
    conn = sqlite3.connect(str(config.recall_db()))
    try:
        rows = conn.execute("SELECT invalidated_by FROM items"
                            " WHERE item_id = 'o-444ddd'").fetchall()
    finally:
        conn.close()
    assert rows
    assert all(v is None for (v,) in rows)


def test_pending_ledger_scan_skips_a_stray_dir(tmp_checkpoint_dir):
    from daimon_briefing import pending
    _stray_ledger_dir("repo.x")
    (tmp_checkpoint_dir / "-ok").mkdir()
    assert pending._ledger_bucket_slugs() == ["-ok"]


def test_a_stray_dir_is_reported_loudly_and_internals_are_not(
        tmp_checkpoint_dir):
    _plant("-good")
    for n in (".partials", ".chunk-cache"):
        (tmp_checkpoint_dir / n).mkdir()
    assert not [n for n in view.projects_notes("-good") if "not a project" in n]
    (tmp_checkpoint_dir / "repo.x").mkdir()
    (tmp_checkpoint_dir / "x@security").mkdir()
    notes = [n for n in view.projects_notes("-good") if "not a project" in n]
    assert len(notes) == 1 and "2" in notes[0]
    assert "repo.x" not in notes[0]  # a count, never a name


def test_a_stray_note_under_tenant_scope_names_no_count(
        tmp_checkpoint_dir, monkeypatch):
    _plant("-good")
    (tmp_checkpoint_dir / "repo.x").mkdir()
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    notes = [n for n in view.projects_notes("-good") if "not a project" in n]
    assert len(notes) == 1 and not any(ch.isdigit() for ch in notes[0])


def test_bucket_exists_refuses_a_stray_dir_that_holds_a_pointer(
        tmp_checkpoint_dir):
    _plant("repo.x")
    assert view.bucket_exists("repo.x", None) is False
