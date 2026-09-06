"""#943 slice 2: materializing armed checks from the ledger.

`checks.sync` is the package half of the runtime contract. It reads the
ledger and writes what a standalone hook can read without importing daimon:
a manifest and one executable body per armed check.
"""

import hashlib
import json
import os
import time

import pytest

from daimon_briefing import checks, checks_runtime, config, normalize, refutations

PROJECT = "/p/checks-sync"
OTHER = "/p/checks-other"
BODY = "#!/bin/sh\ngrep -q FORBIDDEN \"$DAIMON_CHECK_SUBJECT\" && exit 1\nexit 0\n"
MATCH = "gh pr create"


def _sha(body=BODY):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _propose(project=PROJECT, *, subject="public posts", scope="publishing",
             body=BODY, match=MATCH, check=True):
    return refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in {scope}",
        scope=scope, evidence=["issue:943"], channel="cli-agent",
        check={"match": match, "body": body, "intent": "warn"} if check
        else None,
        project_dir=project)


def _arm(project=PROJECT, **kwargs):
    body = kwargs.get("body", BODY)
    ruling_id = _propose(project, **kwargs)
    refutations.ratify(ruling_id, channel="cli-tty",
                       check_sha256=_sha(body) if kwargs.get("check", True)
                       else "",
                       project_dir=project)
    return ruling_id


def _manifest():
    return checks_runtime.load_manifest(
        config.checks_dir() / "manifest.json")


def _bodies():
    base = config.checks_dir()
    return sorted(p.name for p in base.glob("*.sh")) if base.exists() else []


# ---- what an armed check materializes into --------------------------------


def test_an_armed_check_lands_in_the_manifest_with_the_declared_fields(
        tmp_checkpoint_dir):
    ruling_id = _arm()
    report = checks.sync(PROJECT)
    assert report.ok and report.armed == 1
    entries = _manifest().entries
    assert len(entries) == 1
    assert set(entries[0]) == {"ruling_id", "project_dir", "match", "intent",
                               "sha256", "armed_at"}
    assert entries[0]["ruling_id"] == ruling_id
    assert entries[0]["match"] == MATCH
    assert entries[0]["intent"] == "warn"
    assert entries[0]["sha256"] == _sha()
    assert entries[0]["armed_at"], "an armed check must say when it was armed"


def test_the_manifest_records_the_resolved_project_directory(
        tmp_checkpoint_dir):
    """The runtime prefix-matches a realpath'd cwd against this value, so it
    has to be the same resolution the ledger routed the write through."""
    _arm()
    checks.sync(PROJECT)
    assert _manifest().entries[0]["project_dir"] == \
        config.resolve_project_dir(PROJECT)


def test_the_body_is_materialized_executable_and_byte_exact(
        tmp_checkpoint_dir):
    _arm()
    checks.sync(PROJECT)
    entry = _manifest().entries[0]
    path = config.checks_dir() / checks_runtime.body_name(entry)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == BODY
    assert path.stat().st_mode & 0o777 == 0o500


def test_the_manifest_the_writer_produces_is_one_the_runtime_can_use(
        tmp_checkpoint_dir, tmp_path):
    """The two halves are only correct together. This is the seam: sync
    writes, the runtime reads, and a cwd inside the project finds the entry."""
    _arm(project=str(tmp_path))
    checks.sync(str(tmp_path))
    loaded = _manifest()
    assert loaded.reason == ""
    armed = checks_runtime.armed_for(str(tmp_path), loaded)
    assert len(armed) == 1
    assert checks_runtime.matches(armed[0], "gh pr create --fill")


# ---- what never lands -----------------------------------------------------


def test_a_candidate_check_never_lands(tmp_checkpoint_dir):
    """`proposed` is a lifecycle, not a mode. A candidate's body is code no
    human ever confirmed, and record-only would still execute it."""
    _propose()
    report = checks.sync(PROJECT)
    assert report.ok and report.armed == 0
    assert _manifest().entries == []
    assert _bodies() == []


def test_a_ruling_with_no_check_lands_nothing(tmp_checkpoint_dir):
    _arm(check=False)
    assert checks.sync(PROJECT).armed == 0
    assert _manifest().entries == []


def test_a_retired_ruling_drops_its_entry_and_its_body(tmp_checkpoint_dir):
    ruling_id = _arm()
    checks.sync(PROJECT)
    assert len(_bodies()) == 1
    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    checks.sync(PROJECT)
    assert _manifest().entries == []
    assert _bodies() == [], "a disarmed check must not stay on disk"


def test_a_forgotten_ruling_drops_its_entry_and_its_body(tmp_checkpoint_dir):
    _arm()
    checks.sync(PROJECT)
    refutations.forget_content_key(
        normalize.content_key("the rule for public posts in publishing"),
        project_dir=PROJECT)
    checks.sync(PROJECT)
    assert _manifest().entries == []
    assert _bodies() == []


def test_a_revised_body_removes_the_one_it_replaced(tmp_checkpoint_dir):
    """The file name carries the hash, so a new body is a new file. Leaving
    the old one behind would keep a script the ruling no longer stands for."""
    ruling_id = _arm()
    checks.sync(PROJECT)
    stale = _bodies()
    new_body = BODY.replace("FORBIDDEN", "BANNED")
    refutations.revise(ruling_id, channel="cli-tty", evidence=["issue:943"],
                       check={"match": MATCH, "body": new_body,
                              "intent": "warn"},
                       project_dir=PROJECT)
    checks.sync(PROJECT)
    assert _bodies() != stale
    assert len(_bodies()) == 1
    entry = _manifest().entries[0]
    assert (config.checks_dir() / checks_runtime.body_name(entry)).read_text(
        encoding="utf-8") == new_body


# ---- one directory, many projects -----------------------------------------


def test_another_project_s_entries_and_bodies_survive(tmp_checkpoint_dir):
    """The checks directory is global and the sync is per project. A sync
    here must never disarm a ruling made somewhere else."""
    _arm(project=OTHER, subject="release notes", scope="publishing")
    checks.sync(OTHER)
    theirs = _manifest().entries
    assert len(theirs) == 1

    _arm(project=PROJECT)
    report = checks.sync(PROJECT)
    assert report.armed == 1, "the report counts THIS project only"
    entries = _manifest().entries
    assert len(entries) == 2
    assert theirs[0] in entries
    assert len(_bodies()) == 2


def test_retiring_here_leaves_the_other_project_armed(tmp_checkpoint_dir):
    ruling_id = _arm(project=PROJECT)
    _arm(project=OTHER, subject="release notes", scope="publishing")
    checks.sync(OTHER)
    checks.sync(PROJECT)
    assert len(_manifest().entries) == 2

    refutations.retire(ruling_id, channel="cli-tty", project_dir=PROJECT)
    checks.sync(PROJECT)
    entries = _manifest().entries
    assert [e["project_dir"] for e in entries] == \
        [config.resolve_project_dir(OTHER)]
    assert len(_bodies()) == 1


def test_two_projects_sharing_a_body_keep_it_when_one_disarms(
        tmp_checkpoint_dir):
    """Ruling ids hash subject and scope, so two projects can name the same
    body file. Removing it because THIS project stopped wanting it would
    disarm the other one silently."""
    here = _arm(project=PROJECT)
    _arm(project=OTHER)
    checks.sync(OTHER)
    checks.sync(PROJECT)
    assert len(_bodies()) == 1, "the fixture must actually share a name"

    refutations.retire(here, channel="cli-tty", project_dir=PROJECT)
    checks.sync(PROJECT)
    assert len(_manifest().entries) == 1
    assert len(_bodies()) == 1, "the other project still needs that body"


# ---- idempotence ----------------------------------------------------------


def test_a_repeat_sync_changes_neither_bytes_nor_mtimes(tmp_checkpoint_dir):
    """Every ledger writer calls this. A sync that rewrote identical files
    would churn the disk on every ratify in the tree."""
    _arm()
    checks.sync(PROJECT)
    base = config.checks_dir()
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in base.iterdir()}
    time.sleep(0.01)
    checks.sync(PROJECT)
    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in base.iterdir()}
    assert after == before


def test_the_manifest_is_replaced_atomically_and_leaves_no_temp_file(
        tmp_checkpoint_dir):
    _arm()
    checks.sync(PROJECT)
    assert [p.name for p in config.checks_dir().glob("*.tmp")] == []


# ---- never raises ---------------------------------------------------------


def test_sync_reports_a_failure_instead_of_raising(
        tmp_checkpoint_dir, monkeypatch):
    """Sync runs after a ledger write that already landed. Raising here
    would turn a bookkeeping failure into a failed ratify."""
    _arm()

    def boom(*args, **kwargs):
        raise OSError("the disk said no")

    monkeypatch.setattr(checks.store, "_atomic_write", boom)
    report = checks.sync(PROJECT)
    assert report.ok is False
    assert "the disk said no" in report.reason


def test_sync_reports_a_failure_for_a_project_that_names_no_bucket(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(checks.store, "project_slug", lambda *a, **k: "")
    report = checks.sync(PROJECT)
    assert report.ok is False
    assert report.reason


def test_a_manifest_that_cannot_be_read_does_not_erase_it(
        tmp_checkpoint_dir):
    """A corrupt manifest is not permission to drop every other project's
    entries. Sync refuses rather than rebuilding from what it can see."""
    _arm(project=OTHER, subject="release notes", scope="publishing")
    checks.sync(OTHER)
    path = config.checks_dir() / "manifest.json"
    path.write_text("{ not json", encoding="utf-8")
    _arm(project=PROJECT)
    report = checks.sync(PROJECT)
    assert report.ok is False
    assert path.read_text(encoding="utf-8") == "{ not json"


# ---- the surfaces the sync owns -------------------------------------------


@pytest.mark.parametrize("shape", ["checks/manifest.json", "checks/*.sh"])
def test_the_sync_s_files_are_declared_in_the_surface_registry(shape):
    """Both carry authored text: the manifest holds `check.match` and the
    body IS `check.body`, and refutations already declares that pair
    plaintext for forget's reach. So the honest declaration is plaintext with
    a real deletion story, not an exemption."""
    from daimon_briefing import surfaces
    entry = [s for s in surfaces.SURFACES if s.shape == shape]
    assert entry, f"{shape} is a new file under ~/.daimon"
    assert entry[0].owner == "checks.sync"
    assert entry[0].plaintext is True
    assert entry[0].delete == "rewrite"


@pytest.mark.parametrize("shape", ["checks/manifest.json", "checks/*.sh"])
def test_the_sync_s_files_appear_in_the_foreign_apply_gap(shape):
    """A foreign tombstone apply walks the bucket json and nothing else, so
    it does not reach these. The gap is registry-derived, and a new plaintext
    surface widening it on its own is the safe direction (#620)."""
    from daimon_briefing import surfaces
    assert shape in surfaces.foreign_apply_gap()


def test_the_manifest_is_json_a_reader_outside_python_can_parse(
        tmp_checkpoint_dir):
    _arm()
    checks.sync(PROJECT)
    raw = (config.checks_dir() / "manifest.json").read_text(encoding="utf-8")
    assert isinstance(json.loads(raw), list)
    assert os.linesep or True
