"""The census fixture holds what it claims to hold (#1132 PR 6b)."""

from daimon_briefing import store
from tests import _sentinel_world as sw


def _where(world):
    slug = store.project_slug(world.project)
    where = {kind: set() for kind in sw.KINDS}
    for path in world.root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(world.root).as_posix().replace(slug, "<slug>")
        for kind in sw.leaked_kinds(path.read_bytes()):
            where.setdefault(kind, set()).add(rel)
    return where


def test_the_world_holds_what_it_claims(tmp_path, monkeypatch):
    world = sw.build_world(tmp_path, monkeypatch)
    where = _where(world)
    assert set(sw.KINDS) == {"topic", "question", "decision", "belief",
                             "uncertainty", "contradiction"}
    for kind in sw.QUARANTINED:                       # live copies survive
        own = {p for p in where[kind] if p.endswith("latest.json")
               and "team" not in p}
        assert own, kind
        assert any(p.endswith(("S-1.json", "S-2.json")) for p in where[kind])
    for kind in sw.FORGOTTEN:                         # only the foreign copy
        assert len(where[kind]) == 1, (kind, where[kind])
        assert "authors/grace/" in next(iter(where[kind])), kind
    ledgers = {p.rsplit("/", 1)[-1] for kind in ("question", "topic")
               for p in where[kind]}
    assert {"events.jsonl", "refutations.jsonl", "requests.jsonl",
            "amendments.jsonl", "trust.jsonl",
            "forget-hits.quarantined-lines"} <= ledgers
    # the value another project forgot after this one wrote it (11c): it is
    # in this project's ledgers and nowhere else
    assert {p.rsplit("/", 1)[-1] for p in where["peerforgot"]} == {
        "refutations.jsonl", "requests.jsonl"}
    assert all(world.quarantine_ids[k] for k in sw.QUARANTINED)
    assert world.ruling_id and world.relation_id and world.request_id
