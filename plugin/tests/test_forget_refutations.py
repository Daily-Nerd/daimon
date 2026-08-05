"""#578: `daimon forget` must reach the refutation ledger.

`_cmd_forget` purged three surfaces and the ledger was not one of them, so a
value typed into a refutation subject was permanent: unreachable by its own
text, unreachable by its own id, and with no removal verb in the refute family.

The contract being satisfied is `_cmd_forget`'s own, stated twice in its
docstring: "removal means the content leaves the audit trail too". Daimon sorts
its surfaces by whether they hold PLAINTEXT, not by whether they are
append-only. `events.jsonl` is never rewritten because it holds hashes; #419
was filed as a defect the moment plaintext reached it. `refutations.jsonl`
holds plaintext by design, which puts it in the checkpoint's category: tombstone
first (#418 ordering), then rewrite the store without the value.
"""
import json

import pytest

from daimon_briefing import cli, normalize, refutations, store


PROJECT = "/repo/forget-refutations"
SUBJECT = "rewriting the account migration in a single pass"


def _refute(subject=SUBJECT, scope="migrations", project_dir=PROJECT):
    return refutations.assert_refutation(
        subject=subject, verdict="it deadlocked under concurrent writes",
        scope=scope, evidence=["measurement:deadlock-trace-1"],
        authority="human", ratified=True, project_dir=project_dir)


def _checkpoint(*texts, project_dir=PROJECT):
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": t, "trust": "inferred"} for t in texts]},
    }, project_dir=project_dir)


def _ledger_text(project_dir=PROJECT):
    path = refutations._path(project_dir)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_forget_removes_a_refutation_by_its_own_text(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0

    assert SUBJECT not in _ledger_text()
    assert refutations.get(ref_id, project_dir=PROJECT) is None


def test_forget_removes_a_refutation_by_its_id(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()

    assert cli.main(["forget", ref_id, "--project", PROJECT]) == 0

    assert SUBJECT not in _ledger_text()
    assert refutations.get(ref_id, project_dir=PROJECT) is None


def test_forget_removes_every_row_of_the_record_not_only_the_matching_one(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # A revision rewrites the subject, so an OLD subject survives in an earlier
    # row that the folded record no longer shows. Removal is content removal:
    # the whole history of the record goes, or the forgotten text stays on disk
    # in a row nothing renders.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()
    refutations.revise(ref_id, authority="human", ratified=True,
                       subject="a differently worded restatement",
                       evidence=["measurement:deadlock-trace-2"],
                       project_dir=PROJECT)

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0

    text = _ledger_text()
    assert SUBJECT not in text
    assert "a differently worded restatement" not in text
    assert refutations.get(ref_id, project_dir=PROJECT) is None


def test_forget_leaves_unrelated_refutations_intact(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    doomed = _refute()
    keeper = _refute(subject="sharding the audit table by tenant",
                     scope="storage")

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0

    assert refutations.get(doomed, project_dir=PROJECT) is None
    survivor = refutations.get(keeper, project_dir=PROJECT)
    assert survivor is not None
    assert survivor["state"] == "active"
    assert survivor["evidence"] == ["measurement:deadlock-trace-1"]


def test_forget_takes_the_checkpoint_item_and_the_matching_refutation_together(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # Value-oriented removal (#418): a checkpoint forget already splices every
    # sibling id folding to the same content key. A refutation whose subject
    # folds to that key is the same value in another store, so it goes too.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint(SUBJECT)
    ref_id = _refute()

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0

    stored = store.read_latest(project_dir=PROJECT, fallback=False)
    assert not stored["working_context"]["recent_decisions"]
    assert SUBJECT not in _ledger_text()
    assert refutations.get(ref_id, project_dir=PROJECT) is None


def test_forget_refuses_when_an_id_matches_a_decision_and_a_refutation(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # Checkpoint `recent_decisions` ids and refutation ids share the namespace
    # `r-<12 hex>`. forget's never-guess contract must survive the collision:
    # picking either surface silently would delete the wrong thing.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    stored = store.read_latest(project_dir=PROJECT, fallback=False)
    decision_id = stored["working_context"]["recent_decisions"][0]["id"]
    assert decision_id.startswith("r-")

    ref_id = _refute()
    monkeypatch.setattr(refutations, "make_id", lambda *a, **k: decision_id)
    collided = refutations.assert_refutation(
        subject="a subject whose id was forced to collide", verdict="refuted",
        scope="collision", evidence=["measurement:x"], authority="human",
        ratified=True, project_dir=PROJECT)
    assert collided == decision_id

    assert cli.main(["forget", decision_id, "--project", PROJECT]) == 1
    out = capsys.readouterr().out
    assert "ambiguous" in out

    # Nothing was removed from either surface.
    assert refutations.get(collided, project_dir=PROJECT) is not None
    assert refutations.get(ref_id, project_dir=PROJECT) is not None
    stored = store.read_latest(project_dir=PROJECT, fallback=False)
    assert stored["working_context"]["recent_decisions"]


def test_forget_tombstones_the_refutation_before_rewriting_the_ledger(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #418 ordering, mirrored: the audit record of a removal must land before
    # the removal. Failing between the two costs the value's presence, never
    # its receipt.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()

    order = []
    real_append_event = store.append_event
    real_forget = refutations.forget_content_key

    def spy_append_event(*args, **kwargs):
        if kwargs.get("kind") == "tombstone":
            order.append("tombstone")
        return real_append_event(*args, **kwargs)

    def spy_forget(*args, **kwargs):
        order.append("rewrite")
        return real_forget(*args, **kwargs)

    monkeypatch.setattr(store, "append_event", spy_append_event)
    monkeypatch.setattr(refutations, "forget_content_key", spy_forget)

    assert cli.main(["forget", ref_id, "--project", PROJECT]) == 0
    assert order == ["tombstone", "rewrite"]

    # The tombstone carries the HASH, never the text (#321).
    raw = (tmp_checkpoint_dir / store.project_slug(PROJECT) / "events.jsonl")
    rows = [json.loads(line) for line in
            raw.read_text(encoding="utf-8").splitlines() if line.strip()]
    tombstones = [e for e in rows if e.get("kind") == "tombstone"]
    assert tombstones
    assert normalize.content_key(SUBJECT) in tombstones[-1]["status"]
    assert SUBJECT not in json.dumps(tombstones)


def test_forget_reaches_the_ledger_while_daimon_is_disabled(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #421: forget is the ratified deletion exemption to the kill switch. The
    # rewrite that makes the deletion real must run while disabled, or the
    # guarantee has an off switch.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()
    monkeypatch.setenv("DAIMON_DISABLE", "1")

    assert cli.main(["forget", ref_id, "--project", PROJECT]) == 0
    assert SUBJECT not in _ledger_text()


def test_ledger_rewrite_is_atomic_under_a_failed_write(
        tmp_checkpoint_dir, monkeypatch):
    # The ledger has only ever been appended to. A rewrite that dies partway
    # would truncate history, which is strictly worse than the value surviving
    # (scars 0025/0042 live in this writer/fold family). Fail whole, not half.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _refute()
    before = _ledger_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(refutations.os, "replace", boom)
    removed = refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT)

    assert removed == []
    assert _ledger_text() == before


def test_forget_reports_the_ledger_removal(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    _refute()

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "refutation" in out.lower()


def test_rewrite_preserves_rows_this_version_cannot_interpret(
        tmp_checkpoint_dir, monkeypatch):
    # `events()` is deliberately tolerant: it drops rows whose `event` it does
    # not recognise. Rewriting from its output would delete every row a FUTURE
    # daimon wrote, on a command the user ran to remove one value. Scar 0025 is
    # this exact shape, one file over.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    doomed = _refute()
    keeper = _refute(subject="sharding the audit table by tenant", scope="storage")
    path = refutations._path(PROJECT)
    future = json.dumps({"event": "sealed-by-a-later-version", "v": 2,
                         "refutation_id": keeper, "note": "keep me"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(future + "\n")

    assert refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT) == [doomed]

    text = path.read_text(encoding="utf-8")
    assert future in text, "an uninterpretable row was destroyed by forget"
    assert SUBJECT not in text


def test_rewrite_drops_a_future_row_belonging_to_the_forgotten_record(
        tmp_checkpoint_dir, monkeypatch):
    # The mirror of the above: forward compatibility must not become a hole in
    # the deletion contract. An unrecognised row on a DOOMED record still goes.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    doomed = _refute()
    path = refutations._path(PROJECT)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "sealed-by-a-later-version",
                                 "refutation_id": doomed,
                                 "subject": SUBJECT}) + "\n")

    refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT)

    assert SUBJECT not in path.read_text(encoding="utf-8")


def test_rewrite_never_writes_the_readers_private_line_marker(
        tmp_checkpoint_dir, monkeypatch):
    # `events()` stamps `_line` onto every row it returns. Writing its output
    # back would persist a reader's bookkeeping into the ledger format.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _refute()
    keeper = _refute(subject="sharding the audit table by tenant", scope="storage")

    refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT)

    text = refutations._path(PROJECT).read_text(encoding="utf-8")
    assert "_line" not in text
    assert refutations.get(keeper, project_dir=PROJECT) is not None


def test_forget_content_key_is_a_noop_when_nothing_matches(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _refute()
    before = _ledger_text()

    assert refutations.forget_content_key(
        normalize.content_key("a value never written"),
        project_dir=PROJECT) == []
    assert _ledger_text() == before


def test_forget_content_key_tolerates_a_missing_ledger(tmp_checkpoint_dir):
    assert refutations.forget_content_key(
        normalize.content_key("anything"), project_dir=PROJECT) == []


@pytest.mark.parametrize("target", [SUBJECT, "r-000000000000"])
def test_forget_still_refuses_an_unmatched_target(
        tmp_checkpoint_dir, monkeypatch, capsys, target):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")

    assert cli.main(["forget", target, "--project", PROJECT]) == 1
    assert "no item matches" in capsys.readouterr().out
