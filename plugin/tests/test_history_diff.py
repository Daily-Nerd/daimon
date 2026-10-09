"""`daimon diff` — what changed between two retained checkpoints (#975).

Fixtures are built with the shipping writers (store.write_checkpoint stamps
the ids and rotates the pointers, carry.merge produces the carried/twin
shapes). Nothing here hand-shapes a checkpoint except the tests that are
specifically about a TORN file.
"""

import json

import pytest

from daimon_briefing import (carry, cli, config, normalize, render, schema,
                             store, trust, view)
from daimon_briefing.cli import history
from daimon_briefing.surfaces import Writer


_PROJECT = "/p/hist-diff"
_OTHER = "/p/hist-other"


def _item(text, trust="inferred"):
    return {"text": text, "trust": trust}


def _checkpoint(session_id, created, *, decisions=(), questions=()):
    return {
        "session_id": session_id,
        "created": created,
        "author": "alice",
        "working_context": {
            "active_topic": {"text": "pointer history", "trust": "inferred"},
            "open_questions": list(questions),
            "recent_decisions": list(decisions),
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


def _write(session_id, created, *, project=_PROJECT, **kw):
    """Write through the real writer so ids, first_seen and rotation are real."""
    checkpoint = _checkpoint(session_id, created, **kw)
    assert store.write_checkpoint(session_id, checkpoint, project_dir=project, writer=Writer.HUMAN)
    return checkpoint


def _ids(checkpoint) -> dict:
    """text -> stamped id, read back off the dict the writer mutated."""
    out = {}
    for section, key in schema.ITEM_LISTS:
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(item, dict) and item.get("id"):
                out[item["text"]] = item["id"]
    return out


# ---- the default pair -------------------------------------------------------


def test_diff_default_pair_names_added_and_dropped(tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z",
                   decisions=[_item("pin the serializer model", "verbatim")])
    second = _write("S-2", "2026-09-01T11:00:00Z",
                    decisions=[_item("ship the rotation fix", "verbatim")])
    gone = _ids(first)["pin the serializer model"]
    added = _ids(second)["ship the rotation fix"]

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "prev-1.json" in out and "latest.json" in out
    assert "added" in out and added in out
    assert "dropped" in out and gone in out
    # the trust tag rides on every change line, same bracket shape as recall
    assert "[verbatim]" in out


def test_diff_with_no_changes_says_so(tmp_checkpoint_dir, capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("one fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("one fact")])
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    assert "no changes" in capsys.readouterr().out


# ---- explicit endpoints -----------------------------------------------------


def test_diff_explicit_from_and_to(tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    third = _write("S-3", "2026-09-01T12:00:00Z", decisions=[_item("gamma fact")])

    assert cli.main(["diff", "--from", "2", "--to", "0",
                     "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "prev-2.json" in out and "latest.json" in out
    assert _ids(first)["alpha fact"] in out
    assert _ids(third)["gamma fact"] in out
    # the middle checkpoint is not an endpoint, so beta never appears
    assert "beta fact" not in out


def test_diff_refuses_an_out_of_range_endpoint(tmp_checkpoint_dir, capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    assert cli.main(["diff", "--from", "7", "--to", "0",
                     "--project", _PROJECT]) == 2
    err = capsys.readouterr().err
    assert "no checkpoint 7 generations back" in err


def test_diff_refuses_endpoints_that_do_not_run_backwards(tmp_checkpoint_dir,
                                                          capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    assert cli.main(["diff", "--from", "0", "--to", "1",
                     "--project", _PROJECT]) == 2
    assert "--from must be older than --to" in capsys.readouterr().err


# ---- change classes ---------------------------------------------------------


def test_diff_reports_a_resolved_item_as_resolved(tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z",
                   questions=[_item("does the pointer chain expire")])
    item_id = _ids(first)["does the pointer chain expire"]
    assert store.append_event(item_id, "resolved", source="cli-tty",
                              project_dir=_PROJECT, writer=Writer.HUMAN)
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("chain expires")])

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "resolved" in out and item_id in out


def test_diff_reports_a_superseded_item_with_the_naming_id(tmp_checkpoint_dir,
                                                           capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z",
                   decisions=[_item("retry budget is six")])
    item_id = _ids(first)["retry budget is six"]
    assert store.append_event(item_id, "superseded-by:d-0123456789ab",
                              source="cli-tty", project_dir=_PROJECT, writer=Writer.HUMAN)
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("retry budget is ten")])

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "superseded" in out and "d-0123456789ab" in out


def test_there_is_no_forgotten_change_class():
    """A listing never names a forgotten value (R2): after a real forget the
    item is in neither body, so no row can say it was forgotten."""
    assert "forgotten" not in history._CHANGE_ORDER
    assert history._CHANGE_ORDER[-1] == "withheld"


def test_gone_reason_will_not_call_an_open_candidate_closed():
    """A machine's supersede guess keeps the item live (store.is_resolved says
    so), so the absence has to read as unexplained, never as a decision."""
    event = view.Event("2026-09-01T10:00:00Z", "resolution", "d-0123456789ab",
                       "supersede-candidate:d-0123456789ab", "agent", None,
                       None)
    reason, detail = history._gone_reason("active", event)
    assert reason == "dropped"
    assert "does not close it" in detail


def test_gone_reason_with_no_event_is_an_unexplained_drop():
    assert history._gone_reason("active", None) == (
        "dropped", "no lifecycle event recorded")


def test_gone_reason_never_names_a_tombstone_as_a_reason():
    event = view.Event("2026-09-01T10:00:00Z", "tombstone", "d-0123456789ab",
                       "forgotten", "cli", None, None, True)
    reason, detail = history._gone_reason("forgotten", event)
    assert reason == "dropped" and "forgot" not in detail


def test_diff_reports_a_retagged_item(tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z",
                   decisions=[_item("pin the serializer model", "verbatim")])
    item_id = _ids(first)["pin the serializer model"]
    second = _checkpoint("S-2", "2026-09-01T11:00:00Z",
                         decisions=[_item("pin the serializer model", "inferred")])
    assert store.write_checkpoint("S-2", second, project_dir=_PROJECT, writer=Writer.HUMAN)
    assert _ids(second)["pin the serializer model"] == item_id

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "retagged" in out and item_id in out
    assert "was verbatim" in out


def test_diff_reports_a_restated_item(tmp_checkpoint_dir, capsys):
    """carry's twin path is the one writer that changes an item's text under a
    stable id — build the fixture with carry.merge, never by hand."""
    first = _write(
        "S-1", "2026-09-01T10:00:00Z",
        decisions=[_item("the retry budget stays at six attempts")])
    item_id = _ids(first)["the retry budget stays at six attempts"]
    native = _checkpoint(
        "S-2", "2026-09-01T11:00:00Z",
        decisions=[_item("the retry budget stays at six attempts overall")])
    merged = carry.merge(native, first, now=1_800_000_000.0)
    assert store.write_checkpoint("S-2", merged, project_dir=_PROJECT, writer=Writer.HUMAN)
    texts = _ids(merged)
    assert texts["the retry budget stays at six attempts overall"] == item_id

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "restated" in out and item_id in out


# ---- thin and broken chains -------------------------------------------------


def test_diff_with_one_checkpoint_has_nothing_to_compare(tmp_checkpoint_dir,
                                                         capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("only fact")])
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    assert "only one readable checkpoint" in capsys.readouterr().out


def test_diff_with_no_bucket_at_all_exits_one(tmp_checkpoint_dir, capsys):
    assert cli.main(["diff", "--project", _PROJECT]) == 1
    assert "no checkpoints" in capsys.readouterr().err


def test_diff_skips_a_torn_pointer_and_says_so(tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    third = _write("S-3", "2026-09-01T12:00:00Z", decisions=[_item("gamma fact")])
    slug = store.project_slug(_PROJECT)
    (config.checkpoint_dir() / slug / "prev-1.json").write_text(
        "{not json", encoding="utf-8")

    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "prev-1.json" in out and "unreadable" in out
    # it fell through to the next readable generation instead of crashing
    assert "prev-2.json" in out
    assert _ids(first)["alpha fact"] in out
    assert _ids(third)["gamma fact"] in out


def test_diff_refuses_a_torn_pointer_named_explicitly(tmp_checkpoint_dir,
                                                      capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    slug = store.project_slug(_PROJECT)
    (config.checkpoint_dir() / slug / "prev-1.json").write_text(
        "{not json", encoding="utf-8")
    assert cli.main(["diff", "--from", "1", "--to", "0",
                     "--project", _PROJECT]) == 1
    assert "unreadable" in capsys.readouterr().err


def test_diff_notes_a_truncated_chain(tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    _write("S-3", "2026-09-01T12:00:00Z", decisions=[_item("gamma fact")])
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    assert "cannot be read here" in capsys.readouterr().out


# ---- scope ------------------------------------------------------------------


def test_diff_never_reads_another_projects_checkpoints(tmp_checkpoint_dir,
                                                       capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    other = _write("S-X", "2026-09-01T13:00:00Z", project=_OTHER,
                   decisions=[_item("a foreign secret fact")])
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "a foreign secret fact" not in out
    assert _ids(other)["a foreign secret fact"] not in out


def test_the_window_of_an_unnameable_project_is_empty(tmp_checkpoint_dir):
    """No slug means no bucket to attribute a pointer to, and the GLOBAL
    latest.json is never a substitute — it may hold any project's session."""
    assert view.pointers("") == ()
    assert view.pointers("   ") == ()


def test_diff_skips_a_bare_string_contradiction(tmp_checkpoint_dir, capsys):
    """contradictions_flagged may hold plain strings, which carry no id."""
    checkpoint = _checkpoint("S-1", "2026-09-01T10:00:00Z",
                             decisions=[_item("alpha fact")])
    checkpoint["epistemic_snapshot"]["contradictions_flagged"] = [
        "a legacy bare-string contradiction"]
    assert store.write_checkpoint("S-1", checkpoint, project_dir=_PROJECT,
                                  writer=Writer.HUMAN)
    second = _checkpoint("S-2", "2026-09-01T11:00:00Z",
                         decisions=[_item("alpha fact"), _item("beta fact")])
    second["epistemic_snapshot"]["contradictions_flagged"] = [
        "a legacy bare-string contradiction"]
    assert store.write_checkpoint("S-2", second, project_dir=_PROJECT,
                                  writer=Writer.HUMAN)
    assert cli.main(["diff", "--project", _PROJECT, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["changes"]
    assert [r["change"] for r in rows] == ["added"]
    assert rows[0]["item_id"] == _ids(second)["beta fact"]


def test_diff_refuses_slug_and_project_together(tmp_checkpoint_dir, capsys):
    assert cli.main(["diff", "--slug=-p-hist-diff", "--project", _PROJECT]) == 2
    assert "two answers" in capsys.readouterr().err


def test_diff_refuses_a_caller_chosen_scope_when_tenant_scoped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert cli.main(["diff", "--slug=-p-hist-diff"]) == 2
    assert config.TENANT_SCOPE_REFUSAL in capsys.readouterr().err


# ---- machine surface --------------------------------------------------------


def test_diff_json_key_order_is_the_documented_contract(tmp_checkpoint_dir,
                                                        capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    assert cli.main(["diff", "--project", _PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == [
        "schema_version", "project_slug", "history", "retained", "truncated",
        "from", "to", "skipped", "changes",
    ]
    assert list(payload["from"]) == ["index", "pointer", "session_id", "created"]
    assert list(payload["changes"][0]) == [
        "change", "item_id", "kind", "trust", "previous_trust", "text", "detail",
        "where",
    ]
    assert payload["changes"][0]["where"] is None


def test_diff_json_carries_a_refusal_instead_of_stderr(tmp_checkpoint_dir,
                                                       capsys):
    """A machine caller must never have to parse stderr to learn it said no."""
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    assert cli.main(["diff", "--from", "7", "--to", "0",
                     "--project", _PROJECT, "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "no checkpoint 7 generations back" in json.loads(
        captured.out)["refused"]


def test_diff_json_still_answers_a_thin_chain(tmp_checkpoint_dir, capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("only fact")])
    assert cli.main(["diff", "--project", _PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["retained"] == 1
    assert payload["changes"] == []
    assert payload["to"] is None


# ---- rendering --------------------------------------------------------------


def test_diff_renders_plain_off_a_terminal(tmp_checkpoint_dir, monkeypatch,
                                           capsys):
    """Non-TTY must be the bare print loop: no rich, no escape codes."""
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    calls = []
    monkeypatch.setattr(render, "_isatty", lambda: calls.append(1) or False)
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    assert calls, "the tty probe never fired — the render path was not exercised"
    assert "\x1b[" not in capsys.readouterr().out


def test_diff_documents_that_rollback_is_a_non_goal():
    """#975 settled it once: a checkpoint is a record of what a session did.
    The refusal lives in the help text so it is not re-litigated."""
    assert "rollback" in history._cmd_diff.__doc__.lower()
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["diff", "--restore", "1"])


# ---- withheld values (#1132 PR 11a) -------------------------------------------

OLD = "the retry budget stays at six attempts per request"
NEW = "the retry budget stays six attempts per request overall"


def _quarantine(text, kind="decision"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=_PROJECT)


def _restated_pair():
    """One id, two wordings: carry's twin path is the only writer that
    changes an item's text under a stable id."""
    first = _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item(OLD)])
    native = _checkpoint("S-2", "2026-09-01T11:00:00Z",
                         decisions=[_item(NEW)])
    merged = carry.merge(native, first, now=1_800_000_000.0)
    assert store.write_checkpoint("S-2", merged, project_dir=_PROJECT,
                                  writer=Writer.HUMAN)
    return _ids(first)[OLD]


def _diff_json(capsys, *extra):
    assert cli.main(["diff", "--project", _PROJECT, "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def test_a_quarantined_old_wording_is_withheld_from_never_added(
        tmp_checkpoint_dir, capsys):
    item_id = _restated_pair()
    qid = _quarantine(OLD)
    payload = _diff_json(capsys)
    (row,) = payload["changes"]
    assert (row["change"], row["where"], row["item_id"]) == (
        "withheld", "from", item_id)
    assert row["text"] == NEW                       # the visible side only
    assert qid in row["detail"] and OLD not in json.dumps(payload)


def test_a_quarantined_new_wording_is_withheld_to_never_gone(
        tmp_checkpoint_dir, capsys):
    item_id = _restated_pair()
    _quarantine(NEW)
    (row,) = _diff_json(capsys)["changes"]
    assert (row["change"], row["where"], row["item_id"]) == (
        "withheld", "to", item_id)
    assert row["text"] == OLD and NEW not in json.dumps(row)


def test_a_new_item_quarantined_at_once_is_withheld_to(tmp_checkpoint_dir,
                                                       capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("one fact")])
    second = _write("S-2", "2026-09-01T11:00:00Z",
                    decisions=[_item("one fact"), _item(OLD)])
    _quarantine(OLD)
    (row,) = _diff_json(capsys)["changes"]
    assert (row["change"], row["where"], row["text"]) == ("withheld", "to",
                                                          None)
    assert row["item_id"] == _ids(second)[OLD]


def test_an_item_withheld_in_both_generations_says_both(tmp_checkpoint_dir,
                                                        capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item(OLD)])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item(OLD)])
    _quarantine(OLD)
    (row,) = _diff_json(capsys)["changes"]
    assert (row["change"], row["where"], row["text"]) == ("withheld", "both",
                                                          None)


def test_a_withheld_row_prints_its_marker_and_never_the_value(
        tmp_checkpoint_dir, capsys):
    _restated_pair()
    qid = _quarantine(OLD)
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert f"[withheld: quarantine {qid}]" in out
    assert OLD not in out and "added" not in out


def test_every_row_always_has_every_key(tmp_checkpoint_dir, capsys):
    _restated_pair()
    _write("S-3", "2026-09-01T12:00:00Z", decisions=[_item("a third fact")])
    for row in _diff_json(capsys)["changes"]:
        assert list(row) == ["change", "item_id", "kind", "trust",
                             "previous_trust", "text", "detail", "where"]


def test_a_real_forget_leaves_nothing_in_either_body_or_the_listing(
        tmp_checkpoint_dir, monkeypatch, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z",
                   decisions=[_item("keep this one"), _item(OLD)])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("keep this one")])
    item_id = _ids(first)[OLD]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.main(["forget", item_id, "--project", _PROJECT]) == 0
    capsys.readouterr()
    payload = _diff_json(capsys)
    assert payload["changes"] == []
    blob = json.dumps(payload)
    assert item_id not in blob and normalize.content_key(OLD) not in blob
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert item_id not in out and "forgot" not in out.lower()


def test_a_residue_copy_withheld_by_the_forget_set_is_absent_on_both_sides(
        tmp_checkpoint_dir, capsys):
    """The value was forgotten through another ref: this bucket still holds
    its copies, and the listing treats them as absent, not as a change."""
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item(OLD)])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("a later fact")])
    store.append_event("o-elsewhere", "forgotten:" + normalize.content_key(OLD),
                       kind="tombstone", tombstone=True, project_dir=_PROJECT,
                       writer=Writer.HUMAN)
    payload = _diff_json(capsys)
    assert [r["change"] for r in payload["changes"]] == ["added"]
    assert OLD not in json.dumps(payload)


def test_a_restated_rows_old_wording_is_masked_when_the_prose_is_withheld(
        tmp_checkpoint_dir, capsys):
    """The old wording is quarantined as a belief: the decision copy is still
    visible to the view (a quarantine is scoped to its kind), but the whole
    string is a withheld value, so `was` does not print it."""
    _restated_pair()
    qid = _quarantine(OLD, kind="belief")
    (row,) = _diff_json(capsys)["changes"]
    assert row["change"] == "restated" and row["text"] == NEW
    assert row["detail"] == f"was [withheld: quarantine {qid}]"
    assert OLD not in json.dumps(row)


def test_a_restated_rows_forgotten_old_wording_is_not_named(
        tmp_checkpoint_dir, capsys):
    _restated_pair()
    store.append_event("o-elsewhere", "forgotten:" + normalize.content_key(OLD),
                       kind="tombstone", tombstone=True, project_dir=_PROJECT,
                       writer=Writer.HUMAN)
    payload = _diff_json(capsys)
    blob = json.dumps(payload)
    assert OLD not in blob and normalize.content_key(OLD) not in blob
    assert "forgotten" not in blob


def test_a_closed_trust_ledger_withholds_every_item_and_says_why(
        tmp_checkpoint_dir, capsys):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item(OLD)])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item(OLD)])
    (config.checkpoint_dir() / store.project_slug(_PROJECT)
     / "trust.jsonl").write_bytes(b"not json\n")
    payload = _diff_json(capsys)
    assert payload["changes"]
    assert {r["where"] for r in payload["changes"]} == {"both"}
    assert all("trust ledger unreadable" in r["detail"]
               for r in payload["changes"])
    assert OLD not in json.dumps(payload)


def test_a_superseded_reason_comes_from_the_judged_status(
        tmp_checkpoint_dir, capsys):
    first = _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item(OLD)])
    item_id = _ids(first)[OLD]
    assert store.append_event(item_id, "superseded-by:d-0123456789ab",
                              source="cli-tty", project_dir=_PROJECT,
                              writer=Writer.HUMAN)
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("a later fact")])
    rows = {r["change"]: r for r in _diff_json(capsys)["changes"]}
    assert rows["superseded"]["detail"] == (
        "superseded by d-0123456789ab (via cli-tty)")


def test_diff_pays_one_snapshot(tmp_checkpoint_dir, monkeypatch):
    _write("S-1", "2026-09-01T10:00:00Z", decisions=[_item("alpha fact")])
    _write("S-2", "2026-09-01T11:00:00Z", decisions=[_item("beta fact")])
    calls = []
    real = view.snapshot
    monkeypatch.setattr(view, "snapshot",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    assert cli.main(["diff", "--project", _PROJECT]) == 0
    assert calls == [1]


def test_was_masks_a_wording_that_is_a_withheld_value_without_naming_why():
    snap = view.Snapshot.empty()
    assert history._was("plain old wording", snap) == (
        'was "plain old wording"')
    key = normalize.content_key("an old wording that was forgotten")
    forgotten = view.dataclasses.replace(snap, forgotten=frozenset({key}))
    assert history._was("an old wording that was forgotten", forgotten) == (
        "was (previous wording not shown)")
