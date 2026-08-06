"""#600 slice B: a teammate's forget reaches this machine.

Slice A scrubbed the author's OWN mirror copies, and because `_commit_own`
stages modifications those converge to teammates through git on the next
sync. What never travelled is the DELETION ITSELF: store's tombstone
readers walk config.checkpoint_dir() only, so a teammate's forget is
invisible here — their scrubbed file arrives eventually, but nothing tells
this machine that the value is dead, and nothing touches a copy this
machine extracted independently.

Propagation is a hash-only row published into the author's own sidecar dir
(so `_commit_own` carries it with no teamsync change, and #321 holds: the
ledger records the key, never the text).

Authority is the whole design. A foreign tombstone SUPPRESSES by default —
reads and the recall index, exactly what admit_foreign already does with
local tombstones — and never rewrites this machine's belief state. Scrubbing
local checkpoints from a teammate's hash is opt-in
(DAIMON_TEAM_APPLY_FORGET), applied during a TYPED `daimon team sync` rather
than an unattended path, because the shared branch is append-only and a
deletion that crosses a trust boundary has no undo.
"""
import json

from daimon_briefing import cli, config, normalize, store, surfaces

PROJECT = "/p/tombstone-propagation"
MINE = "a decision this machine extracted on its own"
THEIRS = "zqxsharedcanary4417 the vault root token is rotating tonight"

KEY = normalize.content_key(THEIRS)


def _cp(sid, texts, author=None):
    payload = {
        "session_id": sid,
        "created": "2026-08-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": t, "trust": "inferred"} for t in texts]},
    }
    if author:
        payload["author"] = author
        payload["project_slug"] = store.project_slug(PROJECT)
    return payload


def _teammate_publishes_a_tombstone(remote="local", author="Grace"):
    """Grace's sidecar state as it lands here after a pull: her checkpoint
    plus the tombstone row her forget published."""
    adir = config.team_dir() / remote / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "SG.json").write_text(
        json.dumps(_cp("SG", [MINE], author=author), ensure_ascii=False),
        encoding="utf-8")
    (adir / store._TOMBSTONE_NAME).write_text(
        json.dumps({"ts": "2026-08-02T00:00:00Z", "key": KEY,
                    "author": author}) + "\n", encoding="utf-8")
    return adir


# ---- publishing: hash only, in a path the sync already carries -----------


def test_forget_publishes_a_hash_only_tombstone_into_the_own_author_dir(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    assert cli.main(["forget", THEIRS, "--project", PROJECT]) == 0
    published = list(config.team_dir().rglob(store._TOMBSTONE_NAME))
    assert published, "forget published no tombstone for teammates"
    for path in published:
        assert "authors" in path.parts, "must sit in an own-author dir"
        body = path.read_text()
        assert THEIRS not in body, "#321: the key, never the text"
        row = json.loads(body.splitlines()[0])
        assert row["key"] == KEY
        assert set(row) == {"ts", "key", "author"}


def test_publishing_is_append_only_and_idempotent(tmp_checkpoint_dir,
                                                  monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    assert cli.main(["forget", THEIRS, "--project", PROJECT]) == 0
    path = next(iter(config.team_dir().rglob(store._TOMBSTONE_NAME)))
    first = path.read_text()
    store.publish_tombstone(KEY, project_dir=PROJECT)
    assert path.read_text() == first, "a repeat publish must not duplicate"


def test_publishing_is_skipped_when_team_mirroring_is_off(
        tmp_checkpoint_dir, monkeypatch):
    """Nothing is published into a team the user has not opted into."""
    monkeypatch.delenv("DAIMON_TEAM", raising=False)
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    assert cli.main(["forget", THEIRS, "--project", PROJECT]) == 0
    assert list(config.team_dir().rglob(store._TOMBSTONE_NAME)) == []


# ---- reading a teammate's tombstone --------------------------------------


def test_foreign_keys_are_read_from_every_author_dir(tmp_checkpoint_dir):
    _teammate_publishes_a_tombstone()
    assert KEY in store.foreign_forgotten_content_keys()


def test_foreign_keys_survive_a_corrupt_or_unreadable_row(tmp_checkpoint_dir):
    adir = _teammate_publishes_a_tombstone()
    with (adir / store._TOMBSTONE_NAME).open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write(json.dumps({"ts": "x"}) + "\n")      # no key
    assert KEY in store.foreign_forgotten_content_keys()


def test_no_team_dir_yields_no_foreign_keys(tmp_checkpoint_dir):
    assert store.foreign_forgotten_content_keys() == set()


# ---- default A: suppress, never rewrite ----------------------------------


def test_teammate_content_under_a_foreign_tombstone_is_withheld_on_read(
        tmp_checkpoint_dir, monkeypatch):
    """Their own copy may not have been scrubbed yet (they have not synced,
    or we have not pulled) — the value must still not reach a briefing.

    Seeded under a real remote, not `local`: the machine-local mirror is
    ungated by design (it holds this machine's own writes), so a teammate
    can only ever arrive through a synced clone."""
    # Same fixture shape as test_store's foreign-read tests: a .git marker
    # makes the dir a real synced remote, and the env grant is honored with
    # a single clone.
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/x")
    remote = config.team_dir() / "team-a"
    (remote / ".git").mkdir(parents=True, exist_ok=True)
    adir = remote / "projects" / "core" / "x" / "authors" / "grace"
    adir.mkdir(parents=True, exist_ok=True)
    blob = _cp("SG", [THEIRS, MINE], author="grace")
    blob["team_project"] = "core/x"
    (adir / "SG.json").write_text(json.dumps(blob, ensure_ascii=False),
                                  encoding="utf-8")
    (adir / store._TOMBSTONE_NAME).write_text(
        json.dumps({"ts": "2026-08-02T00:00:00Z", "key": KEY,
                    "author": "grace"}) + "\n", encoding="utf-8")
    seen = store.read_team(project_dir=PROJECT)   # [(author, checkpoint), …]
    texts = [i.get("text") for _author, cp in seen
             for section, key in store._ITEM_LISTS
             for i in ((cp or {}).get(section) or {}).get(key) or []]
    assert texts, "the teammate's checkpoint must be read at all"
    assert THEIRS not in texts


def test_default_never_rewrites_this_machines_own_checkpoints(
        tmp_checkpoint_dir):
    """The authority rule: a teammate writing a hash cannot delete local
    belief state. The plaintext stays until the user opts in."""
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    _teammate_publishes_a_tombstone()
    before = [p.read_text() for p in store.project_surfaces(PROJECT)]
    assert store.apply_foreign_tombstones(project_dir=PROJECT) == []
    after = [p.read_text() for p in store.project_surfaces(PROJECT)]
    assert after == before


# ---- opt-in B: scrub, only when switched on ------------------------------


def test_opt_in_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DAIMON_TEAM_APPLY_FORGET", raising=False)
    assert config.team_apply_forget() is False


def test_opt_in_scrubs_local_copies(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    _teammate_publishes_a_tombstone()
    rewritten = store.apply_foreign_tombstones(project_dir=PROJECT)
    assert rewritten, "opt-in must reach local surfaces"
    residue = [str(p) for p in store.project_surfaces(PROJECT)
               if THEIRS in p.read_text()]
    assert residue == [], f"plaintext survives in {residue}"
    kept = store.read_latest(project_dir=PROJECT, fallback=False)
    texts = [i["text"] for i in
             kept["working_context"]["recent_decisions"]]
    assert texts == [MINE], "only the tombstoned value goes"


def test_opt_in_applies_during_a_typed_sync_not_an_unattended_path(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """`daimon team sync` is typed by a person. heal runs detached from
    session start on some hosts, and a cross-trust deletion must never
    arrive unattended."""
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    _teammate_publishes_a_tombstone()
    assert cli.main(["heal"]) == 0
    assert any(THEIRS in p.read_text()
               for p in store.project_surfaces(PROJECT)), \
        "heal must not apply a teammate's deletion"


# ---- the registry knows about the new file -------------------------------


def test_registry_declares_the_tombstone_ledger():
    entry = surfaces.match(f"team/{{remote}}/authors/ada/{store._TOMBSTONE_NAME}")
    assert entry is not None
    assert entry.plaintext is False
    assert entry.delete == "exempt-no-plaintext"
