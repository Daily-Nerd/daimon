import json
from pathlib import Path

import pytest

from daimon_briefing import cli, refutations


PROJECT = "/p/refutations"


@pytest.fixture(autouse=True)
def _interactive(monkeypatch):
    """Every CLI call in this module is a person at a terminal unless it says
    `--by agent`. The human path is now the ABSENCE of the flag plus an
    observed channel, so the terminal has to be observable for those calls to
    mean what they meant when they were written."""
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)


def _assert(*, channel="cli-agent", ratified=False, **overrides):
    values = {
        "subject": "original #502 receipt design",
        "verdict": "whole-file hashes do not prove span-level claims",
        "scope": "carried-item receipt tiers",
        "evidence": ["measurement:566/623 origin misses"],
        "anchors": ["#502", "command:daimon-why"],
        "revisit_when": "receipts bind the originating message span",
        "channel": channel,
        "ratified": ratified,
        "project_dir": PROJECT,
    }
    values.update(overrides)
    return refutations.assert_refutation(**values)


def test_agent_assertion_stays_candidate_and_normalizes_anchors(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    ref_id = _assert()
    record = refutations.get(ref_id, project_dir=PROJECT)

    assert record["state"] == "candidate"
    assert record["activation"] is None
    assert record["asserted_by"] == "agent"
    assert record["asserted_author"] == "Ada"
    assert record["anchors"] == ["issue:502", "command:daimon-why"]
    assert (tmp_checkpoint_dir / refutations.store.project_slug(PROJECT)
            / "refutations.jsonl").exists()


def test_human_must_explicitly_ratify_for_active_state(tmp_checkpoint_dir):
    candidate = _assert(channel="cli-tty")
    assert refutations.get(candidate, project_dir=PROJECT)["state"] == "candidate"

    refutations.ratify(candidate, channel="cli-tty", note="scope accepted",
                       project_dir=PROJECT)
    record = refutations.get(candidate, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activation"] == "ratified (interactive)"


def test_human_can_assert_and_ratify_in_one_explicit_action(tmp_checkpoint_dir):
    ref_id = _assert(channel="cli-tty", ratified=True)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "active"


def test_agent_cannot_self_ratify(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="requires a human channel"):
        _assert(channel="cli-agent", ratified=True)
    assert refutations.records(project_dir=PROJECT) == {}


def test_untyped_evidence_is_refused(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="typed source"):
        _assert(evidence=["trust me, this failed"])
    assert refutations.records(project_dir=PROJECT) == {}


def test_revision_resets_active_record_until_reratified(tmp_checkpoint_dir):
    ref_id = _assert(channel="cli-tty", ratified=True)
    refutations.revise(
        ref_id, channel="cli-agent",
        verdict="hashes prove files, never individual claims",
        evidence=["artifact:research/receipt-study.json"],
        project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["revision"] == 2
    assert record["activation"] is None


def test_agent_overturn_proposal_does_not_disable_active_guard(tmp_checkpoint_dir):
    ref_id = _assert(channel="cli-tty", ratified=True)
    event = refutations.overturn(
        ref_id, channel="cli-agent", evidence=["measurement:new corpus"],
        project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert event == "overturn-proposed"
    assert record["state"] == "active"
    assert record["overturn_proposed"]["by"] == "agent"

    refutations.overturn(
        ref_id, channel="cli-tty", evidence=["measurement:accepted rerun"],
        project_dir=PROJECT)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "overturned"


def test_guard_fires_on_exact_issue_anchor_not_broad_topic(tmp_checkpoint_dir):
    ref_id = _assert(channel="cli-tty", ratified=True)

    assert refutations.guard(
        "should we revisit #502?", project_dir=PROJECT)[0]["refutation_id"] == ref_id
    assert refutations.guard(
        "rank all of our open issues", project_dir=PROJECT) == []


def test_guard_excludes_candidates_and_overturned_records(tmp_checkpoint_dir):
    ref_id = _assert()
    assert refutations.guard("revisit #502", project_dir=PROJECT) == []
    refutations.ratify(ref_id, channel="cli-tty", project_dir=PROJECT)
    refutations.overturn(
        ref_id, channel="cli-tty", evidence=["measurement:valid replacement"],
        project_dir=PROJECT)
    assert refutations.guard("revisit #502", project_dir=PROJECT) == []


def test_malformed_and_orphan_rows_are_inert(tmp_checkpoint_dir):
    path = tmp_checkpoint_dir / refutations.store.project_slug(PROJECT) / "refutations.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "not json\n" + json.dumps({
            "event": "ratified", "refutation_id": "r-0123456789ab",
            "channel": "cli-tty", "order": 1,
        }) + "\n",
        encoding="utf-8")
    assert refutations.records(project_dir=PROJECT) == {}


def test_malformed_order_does_not_sink_the_ledger(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")
    row = {
        "event": "asserted", "refutation_id": ref_id,
        "channel": "cli-agent", "order": "not-an-integer",
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
    }
    assert refutations.fold([row])[ref_id]["state"] == "candidate"


def test_tampered_agent_ratification_flag_cannot_activate(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")
    row = refutations._stamp(
        "asserted", ref_id, "cli-agent", now_ns=1, event_id="a")
    row.update({
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
        "ratified": True,
    })
    assert refutations.fold([row])[ref_id]["state"] == "candidate"


def test_same_order_fold_is_deterministic_and_fails_toward_candidate():
    ref_id = refutations.make_id("bad gate", "current replay")
    asserted = refutations._stamp(
        "asserted", ref_id, "cli-tty", now_ns=1, event_id="z")
    asserted.update({
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
    })
    ratified = refutations._stamp(
        "ratified", ref_id, "cli-tty", now_ns=1, event_id="y")
    revised = refutations._stamp(
        "revised", ref_id, "cli-agent", now_ns=1, event_id="x")
    revised.update({
        "verdict": "fails only in the old replay",
        "evidence": ["measurement:run-2"],
    })

    forward = refutations.fold([asserted, ratified, revised])
    reverse = refutations.fold([revised, ratified, asserted])
    assert forward == reverse
    assert forward[ref_id]["state"] == "candidate"


def test_append_redacts_nested_evidence_and_flat_claims(tmp_checkpoint_dir):
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    ref_id = _assert(
        subject=f"receipt design using {secret}",
        evidence=[f"artifact:{secret}"], channel="cli-tty", ratified=True)

    path = (tmp_checkpoint_dir / refutations.store.project_slug(PROJECT)
            / "refutations.jsonl")
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    record = refutations.get(ref_id, project_dir=PROJECT)
    assert "[redacted:openai-key]" in record["subject"]
    assert "[redacted:openai-key]" in record["evidence"][0]


def test_cli_refute_lifecycle_and_json_output(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    add = [
        "refute", "add",
        "--subject", "original #502 receipt design",
        "--verdict", "whole-file hashes do not prove spans",
        "--scope", "carried receipt tiers",
        "--evidence", "measurement:566/623 misses",
        "--anchor", "#502", "--by", "agent", "--json",
    ]
    assert cli.main(add) == 0
    candidate = json.loads(capsys.readouterr().out)
    assert candidate["state"] == "candidate"

    ref_id = candidate["refutation_id"]
    assert cli.main(["refute", "ratify", ref_id, "--json"]) == 0
    active = json.loads(capsys.readouterr().out)
    assert active["state"] == "active"

    assert cli.main(["refute", "guard", "revisit", "#502", "--json"]) == 0
    guarded = json.loads(capsys.readouterr().out)
    assert guarded[0]["refutation_id"] == ref_id


def test_every_json_surface_labels_evidence_as_cited(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # #576: the text renderer says "recorded as cited; daimon does not verify
    # them", but --json shipped a bare `evidence` list.  A machine consumer saw
    # sourced-looking strings under a key called evidence with nothing to say
    # daimon never resolved them.  Every JSON surface carries the status the
    # human surface prints.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    add = [
        "refute", "add", "--subject", "original #502 receipt design",
        "--verdict", "whole-file hashes do not prove spans",
        "--scope", "carried receipt tiers",
        "--evidence", "measurement:566/623 misses",
        "--anchor", "#502", "--ratify", "--json",
    ]
    assert cli.main(add) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["evidence_status"] == "cited"
    ref_id = record["refutation_id"]

    for argv in (["refute", "show", ref_id, "--json"],
                 ["refute", "list", "--json"],
                 ["refute", "search", "receipt", "--json"],
                 ["refute", "guard", "revisit", "#502", "--json"]):
        assert cli.main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        rows = payload if isinstance(payload, list) else [payload]
        assert rows, f"{argv} returned nothing to check"
        for row in rows:
            assert row["evidence_status"] == "cited", argv


def test_cli_refute_rejects_agent_self_ratification(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    rc = cli.main([
        "refute", "add", "--subject", "bad gate",
        "--verdict", "does not work", "--scope", "current replay",
        "--evidence", "measurement:run-1", "--by", "agent", "--ratify",
    ])
    assert rc == 1
    assert "requires a human channel" in capsys.readouterr().out


def test_disabled_refutation_write_fails_visibly(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    rc = cli.main([
        "refute", "add", "--subject", "bad gate",
        "--verdict", "does not work", "--scope", "current replay",
        "--evidence", "measurement:run-1", "--ratify",
    ])
    assert rc == 1
    assert "not written" in capsys.readouterr().out


def test_validation_limits_and_stamp_contracts(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")

    assert refutations._path(None) is None
    assert refutations.canonical_anchor(
        "https://github.com/Daily-Nerd/daimon/issues/573") == "issue:573"

    with pytest.raises(refutations.RefutationError, match="subject is required"):
        refutations._text("subject", "")
    with pytest.raises(refutations.RefutationError, match="too long"):
        refutations._text("subject", "x" * 2001)
    with pytest.raises(refutations.RefutationError, match="too many anchors"):
        refutations._anchors([f"command:trial-{i}" for i in range(25)])
    with pytest.raises(refutations.RefutationError, match="at least one"):
        refutations._evidence([])
    with pytest.raises(refutations.RefutationError, match="too many evidence"):
        refutations._evidence([f"measurement:trial-{i}" for i in range(25)])
    with pytest.raises(refutations.RefutationError, match="unknown refutation event"):
        refutations._stamp("deleted", ref_id, "cli-tty")
    with pytest.raises(refutations.RefutationError, match="invalid refutation id"):
        refutations._stamp("asserted", "not-an-id", "cli-tty")
    with pytest.raises(refutations.RefutationError, match="channel must be"):
        refutations._stamp("asserted", ref_id, "narrator")


def test_append_and_read_fail_soft_on_missing_scope_and_io_errors(
        tmp_checkpoint_dir, monkeypatch):
    row = {"anchors": [], "evidence": []}
    assert refutations.append(row, project_dir=None) is False

    def fail_open(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "open", fail_open)
    assert refutations.append(row, project_dir=PROJECT) is False


def test_events_fail_soft_on_unreadable_ledger(tmp_checkpoint_dir, monkeypatch):
    path = refutations._path(PROJECT)
    path.parent.mkdir(parents=True)
    path.touch()

    def fail_read(*_args, **_kwargs):
        raise UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")

    monkeypatch.setattr(Path, "read_text", fail_read)
    assert refutations.events(project_dir=PROJECT) == []


def test_events_ignore_structurally_invalid_rows(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")
    valid = refutations._stamp(
        "asserted", ref_id, "cli-agent", now_ns=1, event_id="valid")
    path = refutations._path(PROJECT)
    path.parent.mkdir(parents=True)
    path.write_text("\n".join((
        json.dumps([]),
        json.dumps({"event": "deleted", "refutation_id": ref_id}),
        json.dumps({"event": "asserted", "refutation_id": "bad"}),
        json.dumps(valid),
    )) + "\n", encoding="utf-8")

    assert refutations.events(project_dir=PROJECT) == [dict(valid, _line=3)]


def test_fold_ignores_duplicate_assertions_and_accepts_mechanical_activation():
    ref_id = refutations.make_id("bad gate", "current replay")
    asserted = refutations._stamp(
        "asserted", ref_id, "cli-agent", now_ns=1, event_id="asserted")
    asserted.update({
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
    })
    duplicate = dict(asserted, order=2, event_id="duplicate",
                     verdict="a duplicate must not replace the first assertion")
    activated = refutations._stamp(
        "activated", ref_id, "mechanical", now_ns=3, event_id="activated")

    record = refutations.fold([duplicate, activated, asserted])[ref_id]
    assert record["verdict"] == "does not work"
    assert record["state"] == "active"
    assert record["activation"] == "mechanically-activated"


def test_duplicate_assertion_requires_revision(tmp_checkpoint_dir):
    ref_id = _assert()
    with pytest.raises(refutations.RefutationError, match="already exists"):
        _assert()
    assert refutations.get(ref_id, project_dir=PROJECT)["revision"] == 1


def test_a_subject_changing_revise_leaves_no_room_for_a_duplicate(
        tmp_checkpoint_dir):
    """#646: `make_id` hashes the subject at ASSERT time and is never
    re-derived, so after a subject-changing revision the live record's id
    still encodes the ORIGINAL subject. Checking only the derived id then
    queries an id nothing holds, and a second record for the same subject is
    minted — `guard` matches both while the human render prints `rows[0]`
    plus a count, dropping a ratified verdict."""
    ref_id = _assert(channel="cli-tty", ratified=True)
    revised = "whole file hashes prove span claims"
    refutations.revise(ref_id, channel="cli-tty",
                       evidence=["measurement:566/623 origin misses"],
                       subject=revised, project_dir=PROJECT)

    with pytest.raises(refutations.RefutationError, match="already exists"):
        _assert(subject=revised)
    assert len(refutations.records(project_dir=PROJECT)) == 1


def test_a_revision_cannot_take_over_another_records_identity(
        tmp_checkpoint_dir):
    """The mirror: the same hole reached from the revise side. Two records
    with byte-identical subjects are the defect, whichever command mints the
    second one."""
    first = _assert()
    second = _assert(subject="sharding the audit table by tenant")

    with pytest.raises(refutations.RefutationError, match="already exists"):
        refutations.revise(second, channel="cli-tty",
                           evidence=["measurement:566/623 origin misses"],
                           subject="original #502 receipt design",
                           project_dir=PROJECT)
    subjects = [r["subject"]
                for r in refutations.records(project_dir=PROJECT).values()]
    assert len(subjects) == len(set(subjects))
    assert refutations.get(first, project_dir=PROJECT) is not None


def test_a_record_too_damaged_to_name_an_identity_claims_none():
    """`_identity_id` runs over folded rows the reader was tolerant about, so
    it meets records with a missing or blank subject. Such a record must claim
    NO identity — returning a hash of "" would make every damaged record
    collide with every other one and refuse honest assertions."""
    assert refutations._identity_id({"subject": "", "scope": "storage"}) == ""
    assert refutations._identity_id({"scope": "storage"}) == ""
    assert refutations._identity_id({}) == ""
    assert refutations._identity_id(
        {"subject": "a real subject", "scope": "storage"}) \
        == refutations.make_id("a real subject", "storage")


def test_an_unstattable_ledger_is_not_reported_as_torn(
        tmp_checkpoint_dir, monkeypatch):
    """`_is_torn` decides whether to prefix a newline before appending. It
    cannot answer over a file it cannot stat, and guessing True there would
    inject a blank line into a healthy ledger on every append."""
    _assert()
    path = refutations._path(PROJECT)

    def boom(*args, **kwargs):
        raise OSError("no stat for you")

    monkeypatch.setattr(Path, "stat", boom)
    assert refutations._is_torn(path) is False


def test_a_revision_that_keeps_its_own_identity_is_still_allowed(
        tmp_checkpoint_dir):
    """Anti-overreach: a record always collides with ITSELF under any
    identity check that reads current subjects, so a verdict-only revision
    must not be refused by the duplicate guard."""
    ref_id = _assert()
    refutations.revise(ref_id, channel="cli-tty",
                       evidence=["measurement:566/623 origin misses"],
                       verdict="a sharper statement of the same finding",
                       project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["verdict"] == "a sharper statement of the same finding"
    assert record["revision"] == 2


def test_unknown_and_terminal_lifecycle_transitions_are_refused(
        tmp_checkpoint_dir):
    unknown = "r-000000000000"
    with pytest.raises(refutations.RefutationError, match="unknown refutation"):
        refutations.ratify(unknown, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="unknown refutation"):
        refutations.revise(
            unknown, channel="cli-agent", verdict="new verdict",
            evidence=["measurement:run-2"], project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="unknown refutation"):
        refutations.overturn(
            unknown, channel="cli-tty", evidence=["measurement:run-2"],
            project_dir=PROJECT)

    ref_id = _assert(channel="cli-tty", ratified=True)
    refutations.overturn(
        ref_id, channel="cli-tty", evidence=["measurement:run-2"],
        project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="cannot be ratified"):
        refutations.ratify(ref_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="already overturned"):
        refutations.overturn(
            ref_id, channel="cli-tty", evidence=["measurement:run-3"],
            project_dir=PROJECT)


def test_revision_authority_fields_and_noop_contract(tmp_checkpoint_dir):
    ref_id = _assert()
    with pytest.raises(refutations.RefutationError, match="requires a human channel"):
        refutations.revise(
            ref_id, channel="cli-agent", verdict="new verdict",
            evidence=["measurement:run-2"], ratified=True,
            project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="changes nothing"):
        refutations.revise(
            ref_id, channel="cli-agent", evidence=["measurement:run-2"],
            project_dir=PROJECT)

    refutations.revise(
        ref_id, channel="cli-tty", subject="narrow bad gate",
        scope="new replay only", anchors=["issue:573"],
        revisit_when="the replay engine changes",
        evidence=["measurement:run-3"], project_dir=PROJECT)
    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["subject"] == "narrow bad gate"
    assert record["scope"] == "new replay only"
    assert record["anchors"] == ["issue:573"]
    assert record["revisit_when"] == "the replay engine changes"


def test_lifecycle_write_failures_are_visible(tmp_checkpoint_dir, monkeypatch):
    ref_id = _assert()
    monkeypatch.setattr(refutations, "append", lambda *_args, **_kwargs: False)

    with pytest.raises(refutations.RefutationError, match="ratification not written"):
        refutations.ratify(ref_id, channel="cli-tty", project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="revision not written"):
        refutations.revise(
            ref_id, channel="cli-agent", verdict="new verdict",
            evidence=["measurement:run-2"], project_dir=PROJECT)
    with pytest.raises(refutations.RefutationError, match="overturn event not written"):
        refutations.overturn(
            ref_id, channel="cli-agent", evidence=["measurement:run-2"],
            project_dir=PROJECT)


def test_search_rejects_unknown_state_and_skips_nonmatches(tmp_checkpoint_dir):
    _assert()
    with pytest.raises(refutations.RefutationError, match="unknown state"):
        refutations.search("receipt", states={"deleted"}, project_dir=PROJECT)
    assert refutations.search(
        "receipt", states={"active"}, project_dir=PROJECT) == []
    assert refutations.search("cosmic platypus", project_dir=PROJECT) == []


@pytest.mark.parametrize(("args", "message"), (
    (["refute", "ratify", "r-000000000000", ], "not ratified"),
    (["refute", "revise", "r-000000000000", "--verdict", "new verdict",
      "--evidence", "measurement:run-2", "--by", "agent"], "not revised"),
    (["refute", "overturn", "r-000000000000", "--evidence",
      "measurement:run-2", ], "not overturned"),
    (["refute", "show", "r-000000000000"], "unknown refutation"),
))
def test_cli_refute_reports_lifecycle_errors(
        tmp_checkpoint_dir, monkeypatch, capsys, args, message):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    assert cli.main(args) == 1
    assert message in capsys.readouterr().out


def test_cli_refute_covers_json_empty_and_candidate_surfaces(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    ref_id = _assert()

    assert cli.main(["refute", "show", ref_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["refutation_id"] == ref_id

    assert cli.main(["refute", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["refutation_id"] == ref_id

    assert cli.main(["refute", "search", "receipt", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["refutation_id"] == ref_id

    assert cli.main([
        "refute", "revise", ref_id, "--verdict", "narrower verdict",
        "--evidence", "measurement:run-2", "--by", "agent", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["revision"] == 2

    assert cli.main([
        "refute", "revise", ref_id, "--scope", "new replay only",
        "--evidence", "measurement:run-3", "--by", "agent",
    ]) == 0
    assert "not load-bearing" in capsys.readouterr().out

    assert cli.main([
        "refute", "overturn", ref_id, "--evidence", "measurement:run-4",
        "--by", "agent", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["refutation_id"] == ref_id

    assert cli.main([
        "refute", "list", "--project", "/p/empty-refutations",
    ]) == 0
    assert "no refutations" in capsys.readouterr().out

    assert cli.main(["refute", "search", "cosmic", "platypus"]) == 0
    assert "no matching refutations" in capsys.readouterr().out


def test_cli_refute_reports_search_and_guard_errors(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)

    def fail_search(*_args, **_kwargs):
        raise refutations.RefutationError("search contract failed")

    monkeypatch.setattr(refutations, "search", fail_search)
    assert cli.main(["refute", "search", "receipt"]) == 1
    assert "search contract failed" in capsys.readouterr().out

    assert cli.main(["refute", "guard", "receipt", "--anchor", ""]) == 1
    assert "anchor is required" in capsys.readouterr().out


def test_cli_guard_renders_revisit_condition_and_multiple_exact_matches(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    first = _assert(channel="cli-tty", ratified=True)
    second = _assert(
        subject="second rejected receipt design", scope="second receipt tier",
        evidence=["measurement:run-2"], anchors=["issue:502"],
        revisit_when="the second verifier changes", channel="cli-tty",
        ratified=True)

    assert cli.main(["refute", "guard", "revisit", "#502"]) == 0
    output = capsys.readouterr().out
    assert "Revisit when:" in output
    assert "+ 1 more exact match(es)" in output
    assert first in output or second in output


def _usage_tags():
    from daimon_briefing import config
    path = config.log_dir() / "usage.log"
    if not path.exists():
        return []
    return [parts[1] for parts in
            (line.split() for line in path.read_text(encoding="utf-8").splitlines())
            if len(parts) == 2]


def test_guard_records_hit_and_miss_separately(tmp_checkpoint_dir, monkeypatch):
    # #581: a single `refute:guard` tag makes a hit and a miss
    # indistinguishable, so the false-veto rate the design named as its own
    # gate cannot be computed from field data.  `resolve` already splits its
    # tags by outcome; this follows it.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _assert(channel="cli-tty", ratified=True)

    assert cli.main(["refute", "guard", "revisit", "#502"]) == 0
    assert cli.main(["refute", "guard", "an unrelated question about colours"]) == 0

    tags = _usage_tags()
    assert "refute:guard:hit:anchor" in tags
    assert "refute:guard:miss" in tags


def test_guard_attributes_the_subject_rail(tmp_checkpoint_dir, monkeypatch):
    # The two rails have different precision, so an aggregate hit count cannot
    # tell you which one is generating the noise.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    _assert(subject="the abandoned receipt redesign", scope="receipt tiers",
            channel="cli-tty", ratified=True)

    assert cli.main(
        ["refute", "guard", "should we do the abandoned receipt redesign"]) == 0
    assert "refute:guard:hit:subject" in _usage_tags()


def test_show_states_that_evidence_is_unverified(tmp_checkpoint_dir, monkeypatch, capsys):
    # #576: the evidence line sits in the same Label: value register as
    # Provenance and Authority, which ARE derived from recorded lifecycle
    # facts.  A reader must be able to tell that this one was never checked.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    ref_id = _assert(channel="cli-tty", ratified=True)

    assert cli.main(["refute", "show", ref_id]) == 0
    output = capsys.readouterr().out
    assert "Evidence: measurement:566/623 origin misses" in output
    assert "daimon does not verify" in output


def test_evidence_help_does_not_claim_verification():
    # The word "supporting" reads as a checked property.  Shape validation
    # cannot establish that a source supports anything.
    parser = cli.build_parser()
    help_text = parser.format_help()
    assert "evidence-bound" not in help_text


@pytest.mark.parametrize(
    "sub", ["add", "revise", "overturn", "show", "list", "search", "guard"])
def test_no_refute_subcommand_help_claims_a_verified_relationship(sub, capsys):
    # #576 first pass only reached the top-level parser, which never renders a
    # nested subparser's own help — so `refute overturn` went on offering
    # "contrary evidence" and "evidence contradicting the active verdict".
    # Both name a relationship between source and verdict that daimon has not
    # established and, for entailment, never can.  A caller may CITE evidence
    # against a verdict; daimon may not say the evidence contradicts it.
    with pytest.raises(SystemExit):
        cli.main(["refute", sub, "--help"])
    help_text = capsys.readouterr().out.lower()
    for claim in ("evidence-bound", "contrary evidence", "contradicting",
                  "supporting evidence", "verified evidence"):
        assert claim not in help_text, f"`refute {sub} --help` claims: {claim}"


def test_revision_without_anchors_keeps_the_guard_rail(tmp_checkpoint_dir):
    # A revision that never mentions anchors must not disarm the guard: the
    # `is not None` sentinel in revise() means "unchanged", and append() must
    # not forge the key into the row and make fold() read it as a replacement.
    ref_id = _assert(channel="cli-tty", ratified=True)
    assert refutations.guard("should we revisit #502?", project_dir=PROJECT)

    refutations.revise(
        ref_id, channel="cli-tty", ratified=True,
        revisit_when="a second receipt corpus exists",
        evidence=["measurement:second corpus"], project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["anchors"] == ["issue:502", "command:daimon-why"]
    assert refutations.guard("should we revisit #502?", project_dir=PROJECT)


def test_revision_replaces_the_evidence_it_supersedes(tmp_checkpoint_dir):
    # The fold is a snapshot of what is believed NOW, so `evidence` names the
    # citations backing the CURRENT verdict.  Accrual was tried and reverted:
    # it made the snapshot a union of every citation ever filed, which is what
    # the append-only stream is already for.  Nothing is lost — `events()`
    # still carries the founding row verbatim.
    ref_id = _assert(channel="cli-tty", ratified=True)

    refutations.revise(
        ref_id, channel="cli-tty", ratified=True,
        verdict="whole-file hashes prove files, never individual claims",
        evidence=["measurement:second corpus"], project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["evidence"] == ["measurement:second corpus"]

    founding = [e for e in refutations.events(project_dir=PROJECT)
                if e.get("event") == "asserted"]
    assert founding[0]["evidence"] == ["measurement:566/623 origin misses"]


def test_revived_record_does_not_carry_the_discredited_citation(
        tmp_checkpoint_dir):
    # Reviving an overturned record is the sanctioned path (`ratify` refuses
    # and points here).  Under accrual the citation whose invalidity WAS the
    # stated reason for the overturn came back on the newly active record,
    # indistinguishable from the fresh one — an active verdict advertising
    # support that had already been withdrawn.
    ref_id = _assert(channel="cli-tty", ratified=True)
    refutations.overturn(
        ref_id, channel="cli-tty", note="that corpus was mis-sampled",
        evidence=["measurement:566/623 was mis-sampled"], project_dir=PROJECT)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "overturned"

    refutations.revise(
        ref_id, channel="cli-tty", ratified=True,
        verdict="refuted again, on a corpus that was sampled correctly",
        evidence=["measurement:third corpus"], project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["evidence"] == ["measurement:third corpus"]
    assert "measurement:566/623 origin misses" not in record["evidence"]


def test_folded_evidence_cannot_exceed_the_per_row_cap(tmp_checkpoint_dir):
    # `_evidence` caps a single row at _MAX_EVIDENCE, so a fold that merged
    # across revisions let any number of revisions walk straight through it.
    # Replacement makes the cap true of the folded record by construction.
    ref_id = _assert(channel="cli-tty", ratified=True)
    for revision in range(3):
        refutations.revise(
            ref_id, channel="cli-tty", ratified=True,
            verdict=f"restated, revision {revision}",
            evidence=[f"measurement:r{revision}-{i}"
                      for i in range(refutations._MAX_EVIDENCE)],
            project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert len(record["evidence"]) == refutations._MAX_EVIDENCE


def test_ratify_requires_declared_human_authority(tmp_checkpoint_dir):
    # `ratify` is the transition that creates load-bearing state, so it must
    # carry the same declared authority every other mutation does.
    ref_id = _assert(channel="cli-agent")
    with pytest.raises(refutations.RefutationError, match="requires a human channel"):
        refutations.ratify(ref_id, channel="cli-agent", project_dir=PROJECT)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "candidate"


def test_cli_ratify_without_by_needs_an_observed_terminal(
        tmp_checkpoint_dir, monkeypatch, capsys):
    # `--by` is no longer required, because requiring it was what let a caller
    # answer the question about itself. Omitting it IS the human claim, and the
    # claim now has to be observable: no terminal, no activation.
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    ref_id = _assert(channel="cli-agent")
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False, raising=False)

    assert cli.main(["refute", "ratify", ref_id]) == 1
    assert "no interactive terminal" in capsys.readouterr().out.lower()
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "candidate"


def test_a_torn_trailing_line_never_swallows_the_next_event(tmp_checkpoint_dir):
    # An append interrupted before its terminator must cost exactly the torn
    # row.  Concatenating onto it would fuse two rows into one unparseable
    # line, and events() drops malformed lines silently — so a human overturn
    # would report success while the guard stayed armed.
    ref_id = _assert(channel="cli-tty", ratified=True)
    path = refutations._path(PROJECT)
    path.write_text(path.read_text(encoding="utf-8")[:-1], encoding="utf-8")

    refutations.overturn(
        ref_id, channel="cli-tty",
        evidence=["measurement:contrary replay"], project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record is not None, "the torn line swallowed the overturn"
    assert record["state"] == "overturned"
