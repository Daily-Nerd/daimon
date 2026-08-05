"""`daimon forget` must not leave the value behind in pointer history.

Two defects, one visible only with a byte scan of the whole store:

1. `forget` rewrites the checkpoint through `write_checkpoint`, which rotates
   `latest.json` into `prev-1.json` BEFORE writing. The pre-forget bytes are
   the ones rotated, so the deletion manufactures a fresh copy of the value it
   was asked to remove — in a file that did not exist when the user asked.
2. `prev-N` written by ordinary earlier serializes was never scrubbed at all,
   so any value that was ever superseded survived every `forget` regardless.

#419 settled the house rule: a surface holding PLAINTEXT is inside the deletion
contract, and being an append-only or historical file is not an exemption.
`prev-N` is neither append-only nor hashed. It was simply missed.
"""
import json

import pytest

from daimon_briefing import cli, store


PROJECT = "/p/forget-pointers"
CANARY = "zqxcanary7788 the account migration in a single pass"
KEEPER = "an unrelated decision that must survive the deletion"


def _write(session_id, *texts, project_dir=PROJECT):
    store.write_checkpoint(session_id, {
        "session_id": session_id,
        "created": f"2026-07-0{session_id[-1]}T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": t, "trust": "inferred"} for t in texts]},
    }, project_dir=project_dir)


def _files(root):
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _holding(root, needle):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file() and needle.encode() in p.read_bytes())


def test_forget_leaves_no_plaintext_in_pointer_history(tmp_checkpoint_dir):
    # Both sessions carry the canary, so it is present in the LATEST checkpoint
    # and target resolution finds it. What this pins is the scrub: prev-1 and
    # the older session file hold the same value and are equally plaintext.
    #
    # The harder case — a value present ONLY in superseded state, which forget
    # currently reports as "no item matches" while it sits on disk — is stage 2
    # and is pinned separately below.
    _write("S1", CANARY, KEEPER)
    _write("S2", CANARY, KEEPER)
    assert len(_holding(tmp_checkpoint_dir, CANARY)) > 2, "fixture is too weak"

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0

    assert _holding(tmp_checkpoint_dir, CANARY) == [], (
        "forget reported success while the value survived on disk")


def test_forget_reaches_a_value_only_in_superseded_state(tmp_checkpoint_dir):
    _write("S1", CANARY, KEEPER)
    _write("S2", KEEPER)
    assert _holding(tmp_checkpoint_dir, CANARY), "fixture did not plant residue"

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert _holding(tmp_checkpoint_dir, CANARY) == []


def test_forget_does_not_create_new_files_holding_the_value(tmp_checkpoint_dir):
    # The sharper half: whatever forget fails to clean, it must at least not
    # MANUFACTURE. A deletion that writes a fresh copy of its own target is
    # worse than one that misses an old file.
    _write("S1", CANARY, KEEPER)
    before = _files(tmp_checkpoint_dir)

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0

    created = _files(tmp_checkpoint_dir) - before
    holding = set(_holding(tmp_checkpoint_dir, CANARY))
    assert not (created & holding), (
        f"forget created new files holding the forgotten value: "
        f"{sorted(created & holding)}")


def test_forget_preserves_unrelated_items_in_pointer_history(tmp_checkpoint_dir):
    # Scrubbing history must be surgical. #418's contract is content removal of
    # the targeted value, never truncation of the surfaces that held it.
    _write("S1", CANARY, KEEPER)
    _write("S2", CANARY, KEEPER)

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0

    survivors = _holding(tmp_checkpoint_dir, KEEPER)
    assert survivors, "forget destroyed unrelated history it did not target"
    for rel in survivors:
        payload = json.loads((tmp_checkpoint_dir / rel).read_text())
        assert isinstance(payload, dict), f"{rel} is no longer a valid pointer"


@pytest.mark.parametrize("pointer", ["latest.json", "prev-1.json"])
def test_pointer_files_stay_readable_after_a_scrub(tmp_checkpoint_dir, pointer):
    _write("S1", CANARY, KEEPER)
    _write("S2", CANARY, KEEPER)
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0

    slug = store.project_slug(PROJECT)
    path = tmp_checkpoint_dir / slug / pointer
    if not path.exists():
        pytest.skip(f"{pointer} not present in this layout")
    payload = json.loads(path.read_text())
    assert payload.get("session_id"), f"{pointer} lost its session_id"


OTHER = "zqxcanary7788 a different sentence that also mentions migration"


def test_one_value_across_two_surfaces_is_not_ambiguous(tmp_checkpoint_dir):
    # The hazard of widening the candidate pool. The SAME value in latest and
    # in prev-1 is one value in two places, not two candidates to choose
    # between. Counting hits would turn the ordinary case into a refusal.
    _write("S1", CANARY, KEEPER)
    _write("S2", CANARY, KEEPER)

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0
    assert _holding(tmp_checkpoint_dir, CANARY) == []


def test_two_distinct_values_across_surfaces_still_refuse(tmp_checkpoint_dir):
    # never-guess (#418) has to survive the wider pool: distinct VALUES that
    # both match the query are still ambiguous, and forget must refuse rather
    # than pick one.
    _write("S1", CANARY, KEEPER)
    _write("S2", OTHER, KEEPER)

    rc = cli.main(["forget", "zqxcanary7788", "--project", PROJECT])

    assert rc == 1
    assert _holding(tmp_checkpoint_dir, CANARY), "refusal must change nothing"
    assert _holding(tmp_checkpoint_dir, OTHER), "refusal must change nothing"


def test_exact_id_resolves_from_a_superseded_surface(tmp_checkpoint_dir):
    import json as _json
    _write("S1", CANARY, KEEPER)
    _write("S2", KEEPER)
    old = _json.loads((tmp_checkpoint_dir / "S1.json").read_text())
    target = next(i["id"] for i in old["working_context"]["recent_decisions"]
                  if i["text"] == CANARY)

    assert cli.main(["forget", target, "--project", PROJECT]) == 0
    assert _holding(tmp_checkpoint_dir, CANARY) == []


OTHER_PROJECT = "/p/forget-pointers-neighbour"


def test_scrub_never_crosses_a_project_boundary(tmp_checkpoint_dir):
    # forget is project-scoped: the tombstone is written to one project's
    # ledger. A scrub that walked the whole store would delete an identical
    # sentence out of an unrelated project that never asked for it.
    _write("S1", CANARY, KEEPER)
    _write("N1", CANARY, KEEPER, project_dir=OTHER_PROJECT)

    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0

    neighbour = tmp_checkpoint_dir / store.project_slug(OTHER_PROJECT)
    survived = [p for p in neighbour.rglob("*")
                if p.is_file() and CANARY.encode() in p.read_bytes()]
    assert survived, "forget deleted an unrelated project's data"
