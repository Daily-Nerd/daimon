import json

import pytest

from daimon_briefing import cli, refutations


PROJECT = "/p/refutations"


def _assert(*, authority="agent", ratified=False, **overrides):
    values = {
        "subject": "original #502 receipt design",
        "verdict": "whole-file hashes do not prove span-level claims",
        "scope": "carried-item receipt tiers",
        "evidence": ["measurement:566/623 origin misses"],
        "anchors": ["#502", "command:daimon-why"],
        "revisit_when": "receipts bind the originating message span",
        "authority": authority,
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
    candidate = _assert(authority="human")
    assert refutations.get(candidate, project_dir=PROJECT)["state"] == "candidate"

    refutations.ratify(candidate, note="scope accepted", project_dir=PROJECT)
    record = refutations.get(candidate, project_dir=PROJECT)
    assert record["state"] == "active"
    assert record["activation"] == "human-ratified"


def test_human_can_assert_and_ratify_in_one_explicit_action(tmp_checkpoint_dir):
    ref_id = _assert(authority="human", ratified=True)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "active"


def test_agent_cannot_self_ratify(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="only --by human"):
        _assert(authority="agent", ratified=True)
    assert refutations.records(project_dir=PROJECT) == {}


def test_untyped_evidence_is_refused(tmp_checkpoint_dir):
    with pytest.raises(refutations.RefutationError, match="typed source"):
        _assert(evidence=["trust me, this failed"])
    assert refutations.records(project_dir=PROJECT) == {}


def test_revision_resets_active_record_until_reratified(tmp_checkpoint_dir):
    ref_id = _assert(authority="human", ratified=True)
    refutations.revise(
        ref_id, authority="agent",
        verdict="hashes prove files, never individual claims",
        evidence=["artifact:research/receipt-study.json"],
        project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert record["state"] == "candidate"
    assert record["revision"] == 2
    assert record["activation"] is None


def test_agent_overturn_proposal_does_not_disable_active_guard(tmp_checkpoint_dir):
    ref_id = _assert(authority="human", ratified=True)
    event = refutations.overturn(
        ref_id, authority="agent", evidence=["measurement:new corpus"],
        project_dir=PROJECT)

    record = refutations.get(ref_id, project_dir=PROJECT)
    assert event == "overturn-proposed"
    assert record["state"] == "active"
    assert record["overturn_proposed"]["by"] == "agent"

    refutations.overturn(
        ref_id, authority="human", evidence=["measurement:accepted rerun"],
        project_dir=PROJECT)
    assert refutations.get(ref_id, project_dir=PROJECT)["state"] == "overturned"


def test_guard_fires_on_exact_issue_anchor_not_broad_topic(tmp_checkpoint_dir):
    ref_id = _assert(authority="human", ratified=True)

    assert refutations.guard(
        "should we revisit #502?", project_dir=PROJECT)[0]["refutation_id"] == ref_id
    assert refutations.guard(
        "rank all of our open issues", project_dir=PROJECT) == []


def test_guard_excludes_candidates_and_overturned_records(tmp_checkpoint_dir):
    ref_id = _assert()
    assert refutations.guard("revisit #502", project_dir=PROJECT) == []
    refutations.ratify(ref_id, project_dir=PROJECT)
    refutations.overturn(
        ref_id, authority="human", evidence=["measurement:valid replacement"],
        project_dir=PROJECT)
    assert refutations.guard("revisit #502", project_dir=PROJECT) == []


def test_malformed_and_orphan_rows_are_inert(tmp_checkpoint_dir):
    path = tmp_checkpoint_dir / refutations.store.project_slug(PROJECT) / "refutations.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "not json\n" + json.dumps({
            "event": "ratified", "refutation_id": "r-0123456789ab",
            "authority": "human", "order": 1,
        }) + "\n",
        encoding="utf-8")
    assert refutations.records(project_dir=PROJECT) == {}


def test_malformed_order_does_not_sink_the_ledger(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")
    row = {
        "event": "asserted", "refutation_id": ref_id,
        "authority": "agent", "order": "not-an-integer",
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
    }
    assert refutations.fold([row])[ref_id]["state"] == "candidate"


def test_tampered_agent_ratification_flag_cannot_activate(tmp_checkpoint_dir):
    ref_id = refutations.make_id("bad gate", "current replay")
    row = refutations._stamp(
        "asserted", ref_id, "agent", now_ns=1, event_id="a")
    row.update({
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
        "ratified": True,
    })
    assert refutations.fold([row])[ref_id]["state"] == "candidate"


def test_same_order_fold_is_deterministic_and_fails_toward_candidate():
    ref_id = refutations.make_id("bad gate", "current replay")
    asserted = refutations._stamp(
        "asserted", ref_id, "human", now_ns=1, event_id="z")
    asserted.update({
        "subject": "bad gate", "verdict": "does not work",
        "scope": "current replay", "evidence": ["measurement:run-1"],
    })
    ratified = refutations._stamp(
        "ratified", ref_id, "human", now_ns=1, event_id="y")
    revised = refutations._stamp(
        "revised", ref_id, "agent", now_ns=1, event_id="x")
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
        evidence=[f"artifact:{secret}"], authority="human", ratified=True)

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


def test_cli_refute_rejects_agent_self_ratification(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    rc = cli.main([
        "refute", "add", "--subject", "bad gate",
        "--verdict", "does not work", "--scope", "current replay",
        "--evidence", "measurement:run-1", "--by", "agent", "--ratify",
    ])
    assert rc == 1
    assert "only --by human" in capsys.readouterr().out


def test_disabled_refutation_write_fails_visibly(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", PROJECT)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    rc = cli.main([
        "refute", "add", "--subject", "bad gate",
        "--verdict", "does not work", "--scope", "current replay",
        "--evidence", "measurement:run-1", "--by", "human", "--ratify",
    ])
    assert rc == 1
    assert "not written" in capsys.readouterr().out
