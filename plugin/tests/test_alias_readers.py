"""Slice 2 of project identity: every reader that compares a stored
`project_slug` stamp to the project's slug also accepts the project's merged-away
slugs.

A merged-away bucket G leaves an alias row `G -> B` in migrations.jsonl, and the
signed session files it wrote still carry `project_slug: G` (they are never
rewritten). Only requests.py and recall.py followed the alias; these tests pin
the readers that compared the stamp directly: `project_surfaces` (what `forget`
reaches), `_admit_payload`, the team readers (`_own_team_files`, `read_team`),
`capture._origin_on_disk` and the two privacy-audit membership tests.

Every test runs both phases on one store: with no alias row the foreign stamp is
refused exactly as before, then a planted alias row admits it. A row for an
unrelated pair changes nothing.
"""

import json

from daimon_briefing import buckets, capture, config, privacy, scope, store
from daimon_briefing.store import Admit

G = "-Users-old-machine-proj"      # the merged-away bucket (stamp on old files)
OTHER = "-Users-someone-else-x"    # a stamp that is no alias of anything


def _project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _bucket(proj):
    return store.project_bucket(proj)


def _plant_alias(src, dst, **extra):
    path = buckets.migrations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": "2026-11-01T00:00:00Z", "from_slug": src, "to_slug": dst,
           **extra}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _flat_session(sid, stamp, **extra):
    d = config.checkpoint_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{sid}.json"
    path.write_text(json.dumps({"session_id": sid, "project_slug": stamp,
                                **extra}), encoding="utf-8")
    return path


# ---- project_surfaces (what forget reaches) -----------------------------------------------


def test_project_surfaces_reaches_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    old = _flat_session("old", G)
    own = _flat_session("own", _bucket(proj))
    foreign = _flat_session("foreign", OTHER)
    assert sorted(store.project_surfaces(proj)) == [own]       # no alias row
    _plant_alias("-unrelated-from", "-unrelated-to")
    assert sorted(store.project_surfaces(proj)) == [own]       # wrong pair
    _plant_alias(G, _bucket(proj))
    assert sorted(store.project_surfaces(proj)) == sorted([old, own])
    assert foreign not in store.project_surfaces(proj)


def test_an_incomplete_migration_is_not_an_alias_for_surfaces(tmp_path):
    proj = _project(tmp_path)
    _flat_session("old", G)
    _plant_alias(G, _bucket(proj), complete=False)
    assert store.project_surfaces(proj) == []


# ---- _admit_payload -------------------------------------------------------------------------


def test_admit_payload_admits_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    slug = _bucket(proj)
    cp = {"project_slug": G, "created": "2026-10-01T00:00:00Z"}
    refused = store._admit_payload(cp, slug, Admit.OWN_OR_UNROUTED, False)
    assert refused.checkpoint is None and refused.refused.slug == G
    _plant_alias(G, slug)
    admitted = store._admit_payload(cp, slug, Admit.OWN_OR_UNROUTED, False)
    assert admitted.checkpoint is cp and admitted.refused is None


def test_admit_payload_still_refuses_a_stamp_that_is_no_alias(tmp_path):
    proj = _project(tmp_path)
    slug = _bucket(proj)
    _plant_alias(G, slug)
    got = store._admit_payload({"project_slug": OTHER}, slug,
                               Admit.OWN_OR_UNROUTED, False)
    assert got.checkpoint is None and got.refused.slug == OTHER


# ---- team readers ---------------------------------------------------------------------------


def _team_file(rel, stamp, **extra):
    path = config.team_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_slug": stamp, "author": "ada",
                                "created": "2026-10-01T00:00:00Z", **extra}),
                    encoding="utf-8")
    return path


def test_own_team_files_follow_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    own = scope.segment(config.author()) or "unknown"
    path = _team_file(f"remote/authors/{own}/s1.json", G)
    assert store._own_team_files(proj) == []
    _plant_alias(G, _bucket(proj))
    assert [p for p, _payload in store._own_team_files(proj)] == [path]


def test_read_team_flat_era_follows_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    _team_file("local/authors/ada/s1.json", G, note="old machine")
    assert store.read_team(proj) == []
    _plant_alias(G, _bucket(proj))
    got = store.read_team(proj)
    assert [(author, cp["note"]) for author, cp in got] == [
        ("ada", "old machine")]


# ---- capture: an origin checkpoint must belong to this project ------------------------------


def test_origin_on_disk_accepts_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    _flat_session("origin-1", G)
    assert capture._origin_on_disk("origin-1", proj) is False
    _plant_alias(G, _bucket(proj))
    assert capture._origin_on_disk("origin-1", proj) is True


def test_origin_on_disk_still_refuses_a_provisional_even_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    _flat_session("origin-2", G, source="introspection")
    _plant_alias(G, _bucket(proj))
    assert capture._origin_on_disk("origin-2", proj) is False


# ---- privacy audit membership ---------------------------------------------------------------


def test_json_surface_membership_follows_a_merged_away_stamp_only_through_an_alias(
        tmp_path):
    proj = _project(tmp_path)
    slug = _bucket(proj)
    path = _flat_session("old", G)
    assert privacy._scan_json_surface(path, slug, set(), "flat")[2] is False
    _plant_alias(G, slug)
    assert privacy._scan_json_surface(path, slug, set(), "flat")[2] is True


def test_team_scan_membership_follows_a_merged_away_stamp_only_through_an_alias(
        tmp_path, monkeypatch):
    proj = _project(tmp_path)
    slug = _bucket(proj)
    path = _team_file("remote/authors/ada/s1.json", G)
    seen = []

    def spy(payload, p, keys, surface, *a, **k):
        seen.append(p)
        return [], []

    monkeypatch.setattr(privacy, "_payload_findings", spy)
    privacy._scan_team_dir(slug, set(), proj, [])
    assert seen == []
    _plant_alias(G, slug)
    privacy._scan_team_dir(slug, set(), proj, [])
    assert seen == [path]


# ---- the unchanged baseline ------------------------------------------------------------------


def test_a_store_with_no_alias_rows_reads_its_own_stamps_unchanged(tmp_path):
    proj = _project(tmp_path)
    slug = _bucket(proj)
    own = _flat_session("own", slug)
    assert not buckets.migrations_path().exists()
    assert store.project_surfaces(proj) == [own]
    cp = {"project_slug": slug}
    assert store._admit_payload(cp, slug, Admit.OWN_OR_UNROUTED,
                                False).checkpoint is cp
    assert capture._origin_on_disk("own", proj) is True
    assert not buckets.migrations_path().exists()   # reading never creates it
