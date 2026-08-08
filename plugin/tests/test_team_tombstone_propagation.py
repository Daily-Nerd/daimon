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


def _teammate_publishes_a_tombstone(remote="team-a", author="grace"):
    """Grace's sidecar state as it lands here after a pull: her checkpoint
    plus the tombstone row her forget published.

    A real remote, never `local`: the machine-local mirror holds only this
    machine's own writes, never syncs, and is deliberately excluded from
    the foreign set so a solo user cannot poison their own reopen."""
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


def test_own_rows_are_never_read_back_as_foreign(tmp_checkpoint_dir,
                                                 monkeypatch):
    """Adversarial finding (MAJOR): a published row has no retraction, but a
    local `reopen` lifts the local tombstone — so folding your own rows back
    in would let a value you deliberately reopened stay suppressed forever,
    and (with the opt-in on) be re-scrubbed on every sync, because reopen
    also removes it from the subtrahend."""
    monkeypatch.setenv("DAIMON_AUTHOR", "ada")
    adir = config.team_dir() / "team-a" / "authors" / "ada"
    adir.mkdir(parents=True)
    (adir / store._TOMBSTONE_NAME).write_text(
        json.dumps({"ts": "2026-08-02T00:00:00Z", "key": KEY,
                    "author": "ada"}) + "\n", encoding="utf-8")
    assert store.foreign_forgotten_content_keys() == set()


def test_the_local_mirror_is_never_a_foreign_source(tmp_checkpoint_dir):
    """A solo user with DAIMON_TEAM=1 publishes into team/local, which no
    teammate ever sees — it must not read back as someone else's deletion."""
    _teammate_publishes_a_tombstone(remote="local", author="someone")
    assert store.foreign_forgotten_content_keys() == set()


def test_an_oversized_ledger_is_bounded(tmp_checkpoint_dir, monkeypatch):
    """One ledger is read on the briefing path, so a teammate publishing a
    huge file must not make every read pay for it."""
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 200)
    adir = config.team_dir() / "team-a" / "authors" / "grace"
    adir.mkdir(parents=True)
    rows = [json.dumps({"ts": "2026-08-02T00:00:00Z", "key": f"{i:016x}",
                        "author": "grace"}) for i in range(50)]
    (adir / store._TOMBSTONE_NAME).write_text("\n".join(rows) + "\n",
                                              encoding="utf-8")
    keys = store.foreign_forgotten_content_keys()
    assert 0 < len(keys) < 50, len(keys)


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


def _seed_for_apply(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    _teammate_publishes_a_tombstone(remote="team-a", author="grace")
    assert any(THEIRS in p.read_text()
               for p in store.project_surfaces(PROJECT)), "seed failed"


def _local_plaintext_present():
    return any(THEIRS in p.read_text()
               for p in store.project_surfaces(PROJECT))


def test_a_bare_sync_never_applies_it(tmp_checkpoint_dir, monkeypatch):
    """THE authority test. `daimon team sync` with no flag is what
    lib.spawn_team_sync fires DETACHED at SessionStart with stdout to
    DEVNULL — the same shape as heal. If the setting alone were enough, a
    teammate's hash would delete local belief state unattended and
    silently, which is the whole failure this design exists to prevent."""
    _seed_for_apply(monkeypatch)
    assert cli.main(["team", "sync"]) == 0
    assert _local_plaintext_present(), \
        "a bare (hook-spawned) sync must never apply a teammate's deletion"


def test_heal_never_applies_it(tmp_checkpoint_dir, monkeypatch):
    _seed_for_apply(monkeypatch)
    assert cli.main(["heal"]) == 0
    assert _local_plaintext_present()


def test_the_typed_flag_applies_it(tmp_checkpoint_dir, monkeypatch, capsys):
    """The positive half: the flag is the deliberate act, and it reports."""
    _seed_for_apply(monkeypatch)
    assert cli.main(["team", "sync", "--apply-forget"]) == 0
    assert not _local_plaintext_present()
    assert "applied teammates' forget tombstones" in capsys.readouterr().out


def test_the_flag_refuses_without_the_standing_consent(
        tmp_checkpoint_dir, monkeypatch, capsys):
    _seed_for_apply(monkeypatch)
    monkeypatch.delenv("DAIMON_TEAM_APPLY_FORGET")
    assert cli.main(["team", "sync", "--apply-forget"]) == 0
    assert _local_plaintext_present()
    assert "DAIMON_TEAM_APPLY_FORGET" in capsys.readouterr().err


# ---- a broken sidecar degrades, it never aborts ---------------------------
#
# Every path here runs on the briefing/forget hot path against files another
# machine wrote and git delivered. None of them may raise, and none may cost
# more than the broken thing itself.


def test_a_failed_publish_never_costs_the_local_deletion(
        tmp_checkpoint_dir, tmp_path, monkeypatch):
    """Publishing runs AFTER the local forget and is best-effort. If the
    sidecar cannot be written at all — a read-only mount, or a stale file
    sitting where the tree belongs — the teammate simply does not learn.
    The deletion this machine was actually asked for still happened."""
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    blocked = tmp_path / "team-is-a-file"
    blocked.write_text("a file where the mirror root should be",
                       encoding="utf-8")
    monkeypatch.setenv("DAIMON_TEAM_DIR", str(blocked))
    store.write_checkpoint("S1", _cp("S1", [THEIRS, MINE]),
                           project_dir=PROJECT)
    assert _local_plaintext_present(), \
        "seed failed — the canary must be on disk BEFORE we assert it is gone"
    assert cli.main(["forget", THEIRS, "--project", PROJECT]) == 0
    assert store.publish_tombstone(KEY, project_dir=PROJECT) == [], \
        "an unwritable sidecar must report nothing published, not raise"
    assert not _local_plaintext_present(), \
        "the local forget must not be undone by a failed publish"


def test_an_unreadable_ledger_never_hides_the_other_authors(
        tmp_checkpoint_dir):
    """A ledger that cannot be READ — here a directory left where the file
    belongs, the shape a half-applied pull leaves behind — is skipped, not
    fatal. One broken author's dir must not blind this machine to every
    other teammate's deletion."""
    _teammate_publishes_a_tombstone(remote="team-a", author="grace")
    broken = (config.team_dir() / "team-a" / "authors" / "kay"
              / store._TOMBSTONE_NAME)
    broken.mkdir(parents=True)
    assert store.foreign_forgotten_content_keys() == {KEY}


def test_a_remote_whose_walk_explodes_costs_only_its_own_keys(
        tmp_checkpoint_dir, monkeypatch):
    """`foreign_forgotten_content_keys` documents that it never raises, and
    it is called on the read path, so a sidecar that cannot be walked at all
    must cost only ITS keys — the briefing still suppresses everything the
    reachable remotes published. Injected rather than staged on disk:
    pathlib swallows scandir errors during a walk, so no filesystem state
    reaches this branch."""
    _teammate_publishes_a_tombstone(remote="team-a", author="grace")
    other = normalize.content_key("zqxothercanary9182 staging db password")
    hopper = config.team_dir() / "team-b" / "authors" / "hopper"
    hopper.mkdir(parents=True)
    (hopper / store._TOMBSTONE_NAME).write_text(
        json.dumps({"ts": "2026-08-02T00:00:00Z", "key": other,
                    "author": "hopper"}) + "\n", encoding="utf-8")
    assert store.foreign_forgotten_content_keys() == {KEY, other}, \
        "seed failed — both remotes must be readable before one is broken"

    real_rglob = type(config.team_dir()).rglob

    def explode_on_team_a(self, pattern):
        if self.name == "team-a":
            raise OSError("EIO reading the sidecar")
        return real_rglob(self, pattern)

    monkeypatch.setattr(type(config.team_dir()), "rglob", explode_on_team_a)
    assert store.foreign_forgotten_content_keys() == {other}


def test_apply_across_all_projects_survives_a_missing_checkpoint_dir(
        tmp_checkpoint_dir, monkeypatch):
    """`--apply-forget` is machine-wide, and a machine can have pulled the
    sidecar before it ever wrote a checkpoint of its own. Nothing to rewrite
    is not an error."""
    monkeypatch.setenv("DAIMON_TEAM_APPLY_FORGET", "1")
    _teammate_publishes_a_tombstone()
    assert store.foreign_forgotten_content_keys() == {KEY}, \
        "seed failed — an empty foreign set would make the result vacuous"
    assert not tmp_checkpoint_dir.exists(), \
        "nothing has been written here, so there is no store to walk"
    assert store.apply_foreign_tombstones(all_projects=True) == []


# ---- the registry knows about the new file -------------------------------


def test_registry_declares_the_tombstone_ledger():
    entry = surfaces.match(f"team/{{remote}}/authors/ada/{store._TOMBSTONE_NAME}")
    assert entry is not None
    assert entry.plaintext is False
    assert entry.delete == "exempt-no-plaintext"
