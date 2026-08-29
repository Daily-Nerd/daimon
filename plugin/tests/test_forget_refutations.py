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
from pathlib import Path

import pytest

from daimon_briefing import cli, normalize, refutations, store


PROJECT = "/repo/forget-refutations"
SUBJECT = "rewriting the account migration in a single pass"


def _refute(subject=SUBJECT, scope="migrations", project_dir=PROJECT):
    return refutations.assert_refutation(
        subject=subject, verdict="it deadlocked under concurrent writes",
        scope=scope, evidence=["measurement:deadlock-trace-1"],
        channel="cli-tty", ratified=True, project_dir=project_dir)


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


def test_forget_reaches_a_ledger_only_project_with_no_checkpoint(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # The ledger is a SECOND plaintext store, so a value can live there with no
    # checkpoint at all — bailing on a missing checkpoint would leave that value
    # permanently unreachable. Every other test here writes a checkpoint first,
    # which left the whole `checkpoint is None` arm of `_cmd_forget` unexercised
    # end to end: exactly the arm that gained `isinstance(checkpoint, dict)`
    # guards, so a resolution that dropped one would still pass the suite.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    ref_id = _refute()

    assert cli.main(["forget", SUBJECT, "--project", PROJECT]) == 0

    assert SUBJECT not in _ledger_text()
    assert refutations.get(ref_id, project_dir=PROJECT) is None
    # The report must name the ledger, not fall through to "no store".
    assert "refutation" in capsys.readouterr().out.lower()


_FUZZY_TARGET = "the retry budget must be per-tenant not global"
_FUZZY_QUERY = "retry budget per-tenant"
_NOISE = ("the retry policy was wrong for tenants",
          "a per-tenant budget cap does not hold",
          "budget accounting by retry count is refuted")


def _fuzzy_arm(project, with_refutations):
    # A project per arm: the control's tombstone would otherwise scrub the
    # treatment's checkpoint at write time (#418's forget gate), so the two
    # arms would not differ only by the refutations.
    _checkpoint(_FUZZY_TARGET, "an unrelated decision about logging",
                project_dir=project)
    if with_refutations:
        for index, subject in enumerate(_NOISE):
            _refute(subject=subject, scope=f"scope-{index}",
                    project_dir=project)
    rc = cli.main(["forget", _FUZZY_QUERY, "--project", project])
    stored = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                    admit=store.Admit.ANY) or {}
    survivors = [i.get("text") for i in
                 stored.get("working_context", {}).get("recent_decisions", [])]
    return rc, survivors


def test_refutations_never_make_a_checkpoint_item_unreachable_by_text(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """`carry._generic_terms` is a DOCUMENT-FREQUENCY statistic: terms carried
    by >= _GENERIC_DF texts of ONE KIND are that kind's shared vocabulary and
    are subtracted from the matcher. Counting the checkpoint and the ledger in
    one pool mixes two corpora, inflates the frequency, and strips the query's
    own terms — so recording refutations made `daimon forget "<text>"` answer
    `no item matches` about plaintext demonstrably on disk, for the command
    that IS the deletion contract. It worsened as the ledger grew (#648).

    The invariant is REACHABILITY, not exit 0. A query that genuinely matches
    several distinct values SHOULD be refused as ambiguous — never-guess is
    the contract, and the user recovers by exact id. What may never happen is
    the store claiming nothing matched while the value sits in it."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)

    control_rc, control_left = _fuzzy_arm("/repo/fuzzy-control", False)
    assert control_rc == 0
    assert _FUZZY_TARGET not in control_left, "control never forgot the item"
    capsys.readouterr()

    treatment_rc, treatment_left = _fuzzy_arm("/repo/fuzzy-treatment", True)
    out = capsys.readouterr().out
    assert "no item matches" not in out, (
        "forget denied a value it holds, because unrelated refutations "
        f"contaminated the matcher:\n{out}")
    if treatment_rc != 0:
        # Refused as ambiguous: acceptable, but only if the item is VISIBLE in
        # the candidate list the user is told to pick from.
        assert _FUZZY_TARGET in out, (
            f"refused without offering the item as a candidate:\n{out}")
    else:
        assert _FUZZY_TARGET not in treatment_left


def test_a_contaminating_ledger_still_leaves_exact_id_forget_working(
        tmp_checkpoint_dir, monkeypatch, capsys):
    """The recovery path the ambiguous refusal points at must actually work."""
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    project = "/repo/fuzzy-recovery"
    _checkpoint(_FUZZY_TARGET, "an unrelated decision about logging",
                project_dir=project)
    for index, subject in enumerate(_NOISE):
        _refute(subject=subject, scope=f"scope-{index}", project_dir=project)
    stored = store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    item_id = stored["working_context"]["recent_decisions"][0]["id"]

    assert cli.main(["forget", item_id, "--project", project]) == 0

    left = [i.get("text") for i in
            (store.read_latest_body(project_dir=project, route=store.Route.OWN,
                                    admit=store.Admit.ANY) or {})
            .get("working_context", {}).get("recent_decisions", [])]
    assert _FUZZY_TARGET not in left


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
    refutations.revise(ref_id, channel="cli-tty", ratified=True,
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

    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
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
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    decision_id = stored["working_context"]["recent_decisions"][0]["id"]
    assert decision_id.startswith("r-")

    ref_id = _refute()
    # Force ONE record's id onto the decision id, not every record's: the
    # duplicate check reads what records currently SAY (#646), so a blanket
    # patch would make the first refutation collide with the second and the
    # fixture would be asserting its own contradiction rather than forget's
    # never-guess contract.
    real_make_id = refutations.make_id
    monkeypatch.setattr(
        refutations, "make_id",
        lambda subject, scope: (decision_id if scope == "collision"
                                else real_make_id(subject, scope)))
    collided = refutations.assert_refutation(
        subject="a subject whose id was forced to collide", verdict="refuted",
        scope="collision", evidence=["measurement:x"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)
    assert collided == decision_id

    assert cli.main(["forget", decision_id, "--project", PROJECT]) == 1
    out = capsys.readouterr().out
    assert "ambiguous" in out

    # Nothing was removed from either surface.
    assert refutations.get(collided, project_dir=PROJECT) is not None
    assert refutations.get(ref_id, project_dir=PROJECT) is not None
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
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


def test_rewrite_bails_when_the_ledger_cannot_be_read(
        tmp_checkpoint_dir, monkeypatch):
    # `doomed` is computed from `events()` but the rewrite re-reads the RAW
    # bytes, so the two reads can disagree — a ledger that turns unreadable
    # between them must cost nothing rather than truncate the file to the rows
    # this version happened to parse.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _refute()
    path = refutations._path(PROJECT)
    rows = refutations.events(project_dir=PROJECT)
    unreadable = b"\xff\xfe not utf-8 at all"
    path.write_bytes(unreadable)
    monkeypatch.setattr(refutations, "events", lambda **kwargs: rows)

    assert refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT) == []
    assert path.read_bytes() == unreadable


def test_rewrite_drops_blank_and_unparseable_lines_and_keeps_the_rest(
        tmp_checkpoint_dir, monkeypatch):
    # A torn append leaves bytes no read path can see. `_is_torn` establishes
    # such a row is expendable, so the rewrite drops it rather than preserving
    # forgotten bytes for no reachable benefit — but a KEEPER row on the same
    # pass must survive byte-identical.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    doomed = _refute()
    keeper = _refute(subject="sharding the audit table by tenant",
                     scope="storage")
    path = refutations._path(PROJECT)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")                        # blank
        handle.write("{not json at all\n")        # unparseable

    assert refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT) == [doomed]

    text = path.read_text(encoding="utf-8")
    assert "{not json at all" not in text
    assert "\n\n" not in text
    assert refutations.get(keeper, project_dir=PROJECT) is not None


def test_a_failed_rewrite_reports_nothing_even_when_the_tmp_survives(
        tmp_checkpoint_dir, monkeypatch):
    # Atomic or nothing, with no second failure mode: if the staged replacement
    # cannot be swapped in AND cannot be cleaned up, the answer is still "no id
    # removed" rather than a traceback out of a deletion command.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _refute()
    before = _ledger_text()

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(refutations.os, "replace", boom)
    monkeypatch.setattr(Path, "unlink", boom)

    assert refutations.forget_content_key(
        normalize.content_key(SUBJECT), project_dir=PROJECT) == []
    assert _ledger_text() == before


@pytest.mark.parametrize("target", [SUBJECT, "r-000000000000"])
def test_forget_still_refuses_an_unmatched_target(
        tmp_checkpoint_dir, monkeypatch, capsys, target):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")

    assert cli.main(["forget", target, "--project", PROJECT]) == 1
    assert "no item matches" in capsys.readouterr().out


VERDICT = "it deadlocked under concurrent writes"


def test_plaintext_values_walks_the_declared_scalar_fields():
    # #698: the forget TARGETING pool must read the module's own declaration,
    # exactly as the amendment pool does (amendments.plaintext_values). Scalars
    # only: anchors/evidence are bounded typed tokens shared across records,
    # the reasoning that keeps `author` out of the declared set.
    row = {
        "subject": "receipt design", "verdict": "CANARY must never ship",
        "scope": "receipts", "revisit_when": "when the audit lands",
        "note": "a human note", "anchors": ["issue:698"],
        "evidence": ["measurement:trace-1"], "author": "someone",
    }
    values = refutations.plaintext_values(row)
    assert "receipt design" in values
    assert "CANARY must never ship" in values
    assert "receipts" in values
    assert "when the audit lands" in values
    assert "a human note" in values
    assert "issue:698" not in values
    assert "measurement:trace-1" not in values
    assert "someone" not in values


def test_forget_removes_a_refutation_by_its_verdict_text(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698: the natural deletion gesture for ledger prose is the text itself.
    # Before the fix the pool hand-read `subject` only, so a verdict value
    # answered "no item matches" while the audit promised reach by value.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ref_id = _refute()

    assert cli.main(["forget", VERDICT, "--project", PROJECT]) == 0

    assert VERDICT not in _ledger_text()
    assert refutations.get(ref_id, project_dir=PROJECT) is None


def test_forget_selector_never_offers_a_shared_anchor_token(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 scoping: a typed anchor token is shared across records; offering it
    # as a by-value target would show ONE record in the dry-run while the
    # deleter removes every record carrying the token.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    kept = refutations.assert_refutation(
        subject="a distinct subject about caching",
        verdict="the cache thrashed", scope="caching",
        evidence=["measurement:deadlock-trace-1"],
        channel="cli-tty", ratified=True, project_dir=PROJECT)
    _refute()  # shares evidence token "measurement:deadlock-trace-1"

    assert cli.main(
        ["forget", "measurement:deadlock-trace-1", "--project", PROJECT]) == 1

    # The REASON must be the pool excluding the token, never the ambiguity
    # gate masking an over-reach: with the token in the pool both records
    # would hit and the refusal would read "ambiguous — matches" instead.
    assert "no item matches" in capsys.readouterr().out
    assert refutations.get(kept, project_dir=PROJECT) is not None


def test_forget_reaches_a_subject_whose_own_fields_share_its_terms(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review: one record's subject/verdict/scope restate each other by
    # construction. Counting each field as its own document pushed the
    # record's OWN terms over the generic threshold and the record became
    # unreachable by its exact subject. Frequency is per record, and an
    # exact canonical match must always bind.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    refutations.assert_refutation(
        subject="the account migration rewrite",
        verdict="the account migration rewrite deadlocked",
        scope="account migration",
        evidence=["measurement:deadlock-trace-1"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)

    assert cli.main(
        ["forget", "the account migration rewrite", "--project", PROJECT]) == 0

    assert "the account migration rewrite" not in _ledger_text()


def test_forget_fuzzy_reach_to_a_subject_survives_the_widened_pool(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review: a partial query that reached the subject before the pool
    # widened must still reach it after.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    refutations.assert_refutation(
        subject="the retry budget must be per-tenant and never global",
        verdict="a global retry budget starved the busiest tenant under load",
        scope="retry budget policy",
        revisit_when="when the retry budget becomes per-tenant everywhere",
        evidence=["measurement:starvation-trace"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)

    assert cli.main(
        ["forget", "retry budget must be per-tenant", "--project",
         PROJECT]) == 0

    assert "retry budget" not in _ledger_text()


def test_two_records_matched_on_different_verdicts_refuse_ambiguous(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review: never-guess is about distinct MATCHED values. Two records
    # sharing a display subject but matched on different verdicts are two
    # things; collapsing them over the display text silently under-deleted.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    kept_a = refutations.assert_refutation(
        subject="the account migration rewrite", scope="alpha",
        verdict="the migration corrupted tenant rows silently",
        evidence=["measurement:corruption-a"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)
    kept_b = refutations.assert_refutation(
        subject="the account migration rewrite", scope="beta",
        verdict="the migration corrupted tenant rows completely",
        evidence=["measurement:corruption-b"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)

    assert cli.main(
        ["forget", "the migration corrupted tenant rows silently",
         "--project", PROJECT]) == 1

    assert refutations.get(kept_a, project_dir=PROJECT) is not None
    assert refutations.get(kept_b, project_dir=PROJECT) is not None


def test_dry_run_names_every_record_the_deleter_would_reach(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review round 2: forget is irreversible and dry-run is the only
    # pre-deletion check. Three records share a scope token; the deleter
    # removes all three, so the preview must name all three.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    ids = [
        refutations.assert_refutation(
            subject=f"a distinct subject number {n}",
            verdict=f"a distinct verdict number {n}",
            scope="the account migrations area",
            evidence=[f"measurement:trace-{n}"], channel="cli-tty",
            ratified=True, project_dir=PROJECT)
        for n in range(3)
    ]

    assert cli.main(["forget", "the account migrations area",
                     "--dry-run", "--project", PROJECT]) == 0

    out = capsys.readouterr().out
    for ref_id in ids:
        assert ref_id in out
    for ref_id in ids:
        assert refutations.get(ref_id, project_dir=PROJECT) is not None


def test_ambiguous_refusal_shows_the_values_that_differ(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review round 2: a never-guess refusal is only useful if the user
    # can make the choice. Two candidates separated by their verdicts must
    # show those verdicts, not two identical subject lines.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("an unrelated decision about logging")
    refutations.assert_refutation(
        subject="the account migration rewrite", scope="alpha",
        verdict="the migration corrupted tenant rows silently",
        evidence=["measurement:corruption-a"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)
    refutations.assert_refutation(
        subject="the account migration rewrite", scope="beta",
        verdict="the migration corrupted tenant rows completely",
        evidence=["measurement:corruption-b"], channel="cli-tty",
        ratified=True, project_dir=PROJECT)

    assert cli.main(
        ["forget", "the migration corrupted tenant rows silently",
         "--project", PROJECT]) == 1

    out = capsys.readouterr().out
    assert "the migration corrupted tenant rows silently" in out
    assert "the migration corrupted tenant rows completely" in out


def test_dry_run_names_amendments_keyed_to_the_doomed_item(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review round 3: amendments die with their item by item id and
    # carry prose that is NOT the forgotten value. Zero preview meant
    # unrelated evidence quotes and notes vanished with no warning.
    from daimon_briefing import amendments
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _checkpoint("we ship the queue rewrite before the freeze")
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    item_id = stored["working_context"]["recent_decisions"][0]["id"]
    a_id = amendments.propose(
        item_id=item_id, change="progressed",
        evidence="the user said only the consumer half ships",
        channel="cli-agent", project_dir=PROJECT)

    assert cli.main(["forget", "we ship the queue rewrite before the freeze",
                     "--dry-run", "--project", PROJECT]) == 0

    out = capsys.readouterr().out
    assert a_id in out
    assert any(str(r.get("amendment_id")) == a_id
               for r in amendments.events(project_dir=PROJECT))


def test_dry_run_names_spliced_checkpoint_siblings(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review round 3: the checkpoint splice removes every item holding
    # the value, whatever its id. The preview must name the siblings.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    shared = "the shared sentence across two sections"
    store.write_checkpoint("S1", {
        "session_id": "S1", "created": "2026-07-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": shared, "trust": "inferred"}],
            "open_questions": [{"text": shared, "trust": "inferred"}]},
    }, project_dir=PROJECT)
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    ids = {stored["working_context"]["recent_decisions"][0]["id"],
           stored["working_context"]["open_questions"][0]["id"]}

    assert cli.main(["forget", shared, "--dry-run", "--project", PROJECT]) == 0

    out = capsys.readouterr().out
    for item_id in ids:
        assert item_id in out


def test_dry_run_previews_amendments_of_a_superseded_item(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #698 review round 4: a value superseded out of the live checkpoint is
    # still reachable (#419), and the destructive path still removes the
    # amendments keyed on its id. The preview's splice set must be seeded
    # with the target id or exactly those amendments die unpreviewed.
    from daimon_briefing import amendments
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    doomed_text = "we ship the queue rewrite before the freeze"
    _checkpoint(doomed_text)
    stored = store.read_latest_body(project_dir=PROJECT, route=store.Route.OWN,
                                    admit=store.Admit.ANY)
    item_id = stored["working_context"]["recent_decisions"][0]["id"]
    a_id = amendments.propose(
        item_id=item_id, change="progressed",
        evidence="the user said only the consumer half ships",
        channel="cli-agent", project_dir=PROJECT)
    store.write_checkpoint("S2", {
        "session_id": "S2", "created": "2026-07-02T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "a completely different later decision",
             "trust": "inferred"}]},
    }, project_dir=PROJECT)

    assert cli.main(["forget", doomed_text,
                     "--dry-run", "--project", PROJECT]) == 0

    out = capsys.readouterr().out
    assert a_id in out
    assert any(str(r.get("amendment_id")) == a_id
               for r in amendments.events(project_dir=PROJECT))
