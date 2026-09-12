"""`daimon blame` — how one item got here (#975).

`why` answers "where did this come from". `blame` answers "how did it get
here": the session that first stated it, every carry since, and every event
that changed its state, in order.

Checkpoints are built with the shipping writers — store.write_checkpoint for
the id stamping and the pointer rotation, carry.merge for the carried shape,
store.append_event for the lifecycle rows (the writer resolve and reverify
both call). The one hand-shaped file is the torn pointer, which is what that
test is about.
"""

import json

from daimon_briefing import carry, cli, config, render, store
from daimon_briefing.cli import history


_PROJECT = "/p/hist-blame"
_OTHER = "/p/hist-blame-other"
_NOW = 1_800_000_000.0


def _item(text, trust="inferred"):
    return {"text": text, "trust": trust}


def _checkpoint(session_id, created, *, decisions=(), questions=()):
    return {
        "session_id": session_id,
        "created": created,
        "author": "alice",
        "working_context": {
            "active_topic": {"text": "item lineage", "trust": "inferred"},
            "open_questions": list(questions),
            "recent_decisions": list(decisions),
        },
        "epistemic_snapshot": {
            "strong_beliefs": [],
            "uncertainties": [],
            "contradictions_flagged": [],
        },
    }


def _write(checkpoint, *, project=_PROJECT):
    assert store.write_checkpoint(checkpoint["session_id"], checkpoint,
                                  project_dir=project)
    return checkpoint


def _ids(checkpoint) -> dict:
    out = {}
    for section, key in store._ITEM_LISTS:
        for item in ((checkpoint.get(section) or {}).get(key) or []):
            if isinstance(item, dict) and item.get("id"):
                out[item["text"]] = item["id"]
    return out


def _carried_run(texts, *, project=_PROJECT):
    """Write one checkpoint per entry in `texts`, folding the previous one
    forward with carry so the carried items are real carried items. `now` is
    each checkpoint's own stamp: carry expires by decayed weight, so a clock
    months ahead of the fixture would drop the items under test."""
    written = []
    prev = None
    for offset, natives in enumerate(texts):
        created = f"2026-09-0{offset + 1}T10:00:00Z"
        checkpoint = _checkpoint(f"S-{offset + 1}", created,
                                 decisions=[_item(t) for t in natives])
        if prev is not None:
            checkpoint = carry.merge(checkpoint, prev,
                                     now=store._created_epoch(created))
        written.append(_write(checkpoint, project=project))
        prev = written[-1]
    return written


# ---- lineage ----------------------------------------------------------------


def test_blame_names_the_first_session_and_every_carry(tmp_checkpoint_dir,
                                                       capsys):
    run = _carried_run([["the retry budget stays at six attempts"],
                        ["a second session fact"],
                        ["a third session fact"]])
    item_id = _ids(run[0])["the retry budget stays at six attempts"]

    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert item_id in out
    # first stated in the oldest retained generation, then carried twice
    assert "S-1" in out and "S-2" in out and "S-3" in out
    assert out.count("carried from") == 2
    assert "stated here" in out
    assert "prev-2.json" in out and "latest.json" in out


def test_blame_will_not_invent_an_origin_it_cannot_reach(tmp_checkpoint_dir,
                                                         monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_CHECKPOINT_HISTORY", "2")
    run = _carried_run([["the retry budget stays at six attempts"],
                        ["a second session fact"],
                        ["a third session fact"]])
    item_id = _ids(run[0])["the retry budget stays at six attempts"]

    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "origin beyond retained history" in out
    assert "cannot be read here" in out


def test_blame_names_the_bound_origin_of_a_native_item(tmp_checkpoint_dir,
                                                       capsys):
    first = _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                               decisions=[_item("a native fact")]))
    item_id = _ids(first)["a native fact"]
    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "first stated by S-1" in out and "alice" in out


def test_blame_reports_an_origin_the_record_never_named(tmp_checkpoint_dir,
                                                        capsys):
    """An item written before #268's origin binding carries no origin_session.
    Absent means unknown, and the verb says so rather than substituting the
    session it happens to sit in. The legacy SHAPE is what this test is about,
    so the stamp is stripped off the pointer the real writer produced."""
    first = _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                               decisions=[_item("a native fact")]))
    item_id = _ids(first)["a native fact"]
    path = config.checkpoint_dir() / store.project_slug(_PROJECT) / "latest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for item in payload["working_context"]["recent_decisions"]:
        item.pop("origin_session", None)
        item.pop("origin_author", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    assert "origin not recorded" in capsys.readouterr().out


# ---- events -----------------------------------------------------------------


def test_blame_lists_events_in_order_with_their_channel(tmp_checkpoint_dir,
                                                        capsys):
    first = _write(_checkpoint(
        "S-1", "2026-09-01T10:00:00Z",
        questions=[_item("does the pointer chain expire")]))
    item_id = _ids(first)["does the pointer chain expire"]
    assert store.append_event(item_id, "resolved", source="cli-tty",
                              project_dir=_PROJECT)
    assert store.append_event(item_id, "reopened", note="checked the rotation",
                              source="cli-tty", project_dir=_PROJECT)

    assert cli.main(["blame", item_id, "--project", _PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [e["status"] for e in payload["events"]] == ["resolved", "reopened"]
    assert {e["source"] for e in payload["events"]} == {"cli-tty"}
    assert payload["events"][1]["note"] == "checked the rotation"
    # the latest event wins the lifecycle word, exactly as the fold does
    assert payload["lifecycle"] == "active"


def test_blame_never_answers_for_a_corroboration_row(tmp_checkpoint_dir,
                                                     capsys):
    """Corroboration rows land on a namespaced ref so they can never address
    the item. blame binds on the bare ref, so they must not appear here."""
    first = _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                               decisions=[_item("a native fact")]))
    item_id = _ids(first)["a native fact"]
    assert store.append_event(store.corroboration_ref(item_id),
                              "corroborated-by:S-9", source="serialize",
                              project_dir=_PROJECT)
    assert cli.main(["blame", item_id, "--project", _PROJECT, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_item_events_answers_nothing_without_a_bucket_or_a_ref():
    """No slug means no ledger to read, and an empty ref addresses nothing.
    Both are an empty answer, never a walk over every row."""
    assert store.item_events("d-abcdef123456", project_dir="") == []
    assert store.item_events("", project_dir=_PROJECT) == []


def test_item_events_survives_a_corrupt_ledger_line(tmp_checkpoint_dir):
    """One bad line must never cost the reader the rest of the log, and an
    unstamped row sorts oldest rather than displacing a stamped one."""
    first = _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                               decisions=[_item("a native fact")]))
    item_id = _ids(first)["a native fact"]
    assert store.append_event(item_id, "resolved", source="cli-tty",
                              project_dir=_PROJECT)
    path = config.checkpoint_dir() / store.project_slug(_PROJECT) / "events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"item_ref": item_id, "status": "reopened"})
                     + "\n")

    rows = store.item_events(item_id, project_dir=_PROJECT)
    assert [r["status"] for r in rows] == ["reopened", "resolved"]


def test_blame_does_not_reprint_a_forgotten_items_text(tmp_checkpoint_dir,
                                                       monkeypatch, capsys):
    first = _write(_checkpoint(
        "S-1", "2026-09-01T10:00:00Z",
        decisions=[_item("the client is northwind logistics")]))
    item_id = _ids(first)["the client is northwind logistics"]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.main(["forget", item_id, "--project", _PROJECT]) == 0
    capsys.readouterr()

    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "northwind" not in out
    assert "forgotten" in out


# ---- refusals ---------------------------------------------------------------


def test_blame_refuses_an_id_that_is_not_one(tmp_checkpoint_dir, capsys):
    assert cli.main(["blame", "not-an-id", "--project", _PROJECT]) == 2
    assert "invalid item id" in capsys.readouterr().err


def test_blame_exits_one_on_an_id_this_project_never_held(tmp_checkpoint_dir,
                                                          capsys):
    _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                       decisions=[_item("a native fact")]))
    assert cli.main(["blame", "d-abcdef123456", "--project", _PROJECT]) == 1
    assert "no item" in capsys.readouterr().err


def test_blame_refuses_slug_and_project_together(tmp_checkpoint_dir, capsys):
    assert cli.main(["blame", "d-abcdef123456", "--slug=-p-hist-blame",
                     "--project", _PROJECT]) == 2
    assert "two answers" in capsys.readouterr().err


def test_blame_refuses_a_caller_chosen_scope_when_tenant_scoped(
        tmp_checkpoint_dir, monkeypatch, capsys):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert cli.main(["blame", "d-abcdef123456", "--slug=-p-hist-blame"]) == 2
    assert config.TENANT_SCOPE_REFUSAL in capsys.readouterr().err


def test_blame_json_carries_its_refusal(tmp_checkpoint_dir, capsys):
    assert cli.main(["blame", "d-abcdef123456", "--project", _PROJECT,
                     "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "no item" in json.loads(captured.out)["refused"]


# ---- scope and torn files ---------------------------------------------------


def test_blame_never_reaches_into_another_project(tmp_checkpoint_dir, capsys):
    other = _write(_checkpoint("S-X", "2026-09-01T10:00:00Z",
                               decisions=[_item("a foreign secret fact")]),
                   project=_OTHER)
    item_id = _ids(other)["a foreign secret fact"]
    _write(_checkpoint("S-1", "2026-09-01T11:00:00Z",
                       decisions=[_item("a local fact")]))
    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 1
    assert "a foreign secret fact" not in capsys.readouterr().out


def test_blame_skips_a_torn_pointer_and_says_so(tmp_checkpoint_dir, capsys):
    run = _carried_run([["the retry budget stays at six attempts"],
                        ["a second session fact"],
                        ["a third session fact"]])
    item_id = _ids(run[0])["the retry budget stays at six attempts"]
    slug = store.project_slug(_PROJECT)
    (config.checkpoint_dir() / slug / "prev-1.json").write_text(
        "{not json", encoding="utf-8")

    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    out = capsys.readouterr().out
    assert "skipped prev-1.json (unreadable)" in out
    assert "prev-2.json" in out and "latest.json" in out


# ---- machine surface and rendering -----------------------------------------


def test_blame_json_key_order_is_the_documented_contract(tmp_checkpoint_dir,
                                                         capsys):
    run = _carried_run([["the retry budget stays at six attempts"],
                        ["a second session fact"]])
    item_id = _ids(run[0])["the retry budget stays at six attempts"]
    assert cli.main(["blame", item_id, "--project", _PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert list(payload) == [
        "schema_version", "project_slug", "item_id", "kind", "trust", "text",
        "lifecycle", "origin", "history", "retained", "truncated",
        "appearances", "events", "skipped",
    ]
    assert list(payload["origin"]) == [
        "session_id", "author", "first_seen", "retained"]
    assert list(payload["appearances"][0]) == [
        "index", "pointer", "session_id", "created", "trust", "carried_from",
        "native"]
    # oldest generation first, so a reader walks the lineage forwards
    assert [a["index"] for a in payload["appearances"]] == [1, 0]


def test_blame_renders_plain_off_a_terminal(tmp_checkpoint_dir, monkeypatch,
                                            capsys):
    monkeypatch.delenv("DAIMON_PLAIN", raising=False)
    calls = []
    monkeypatch.setattr(render, "_isatty", lambda: calls.append(1) or False)
    first = _write(_checkpoint("S-1", "2026-09-01T10:00:00Z",
                               decisions=[_item("a native fact")]))
    item_id = _ids(first)["a native fact"]
    assert cli.main(["blame", item_id, "--project", _PROJECT]) == 0
    assert calls, "the tty probe never fired — the render path was not exercised"
    assert "\x1b[" not in capsys.readouterr().out


def test_blame_documents_that_rollback_is_a_non_goal():
    assert "rollback" in history._cmd_blame.__doc__.lower()
