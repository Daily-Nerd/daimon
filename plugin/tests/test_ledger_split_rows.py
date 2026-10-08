"""#1138 acceptance: a ledger row holding U+2028, U+2029 or U+0085 is ONE row.

The ledgers write those characters raw (`ensure_ascii=False`) and every
rewriter used to split with `str.splitlines()`, which breaks on them, then
dropped the fragments as "unparseable": a forget of an UNRELATED value
permanently deleted the row. The same split made `scrub_event_fields` leave a
forgotten value inside such a row, the privacy audit report such a file clean,
and `buckets.migrate` re-append fragments.
"""

import json

import pytest

from daimon_briefing import (amendments, buckets, cli, normalize, privacy,
                             refutations, relations, requests, store, trust)
from daimon_briefing.surfaces import Writer

SEPS = [" ", " ", "\u0085"]
sep_param = pytest.mark.parametrize("sep", SEPS, ids=["u2028", "u2029", "u0085"])

PROJECT = "/p/ledger-split-rows"
CANARY = "zqxsplitcanary1138 the staging db password rotates on fridays"
KEY = normalize.content_key(CANARY)


def _alien(id_field, sep):
    """A row this version cannot interpret (so no reader or fold could be
    what keeps it), holding the separator raw."""
    return json.dumps({"event": "from-the-future",
                       id_field: "x-" + "c" * 12,
                       "note": f"left{sep}right"}, ensure_ascii=False)


def _append(path, line):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _rows(path):
    return path.read_text(encoding="utf-8").split("\n")


# ---- an UNRELATED forget keeps the row, in every ledger -------------------


@sep_param
def test_amendments_forget_keeps_a_row_with_a_separator(tmp_checkpoint_dir, sep):
    amendments.propose(item_id="o-1234567890ab", change="progressed",
                       evidence=CANARY, channel="cli-agent",
                       project_dir=PROJECT)
    path = amendments._path(PROJECT)
    alien = _alien("amendment_id", sep)
    _append(path, alien)
    assert amendments.forget_content_key(KEY, project_dir=PROJECT)
    assert alien in _rows(path)


@sep_param
def test_requests_forget_keeps_a_row_with_a_separator(tmp_checkpoint_dir, sep):
    requests.open_request(to="-p-recipient", ask=CANARY, why="because",
                          channel="cli-agent", project_dir=PROJECT)
    path = requests._path(PROJECT)
    alien = _alien("request_id", sep)
    _append(path, alien)
    assert requests.forget_content_key(KEY, project_dir=PROJECT)
    assert alien in _rows(path)


@sep_param
def test_relations_forget_keeps_a_row_with_a_separator(tmp_checkpoint_dir, sep):
    ends = ({"session_id": "S2", "field": "recent_decisions",
             "item_id": "r-abc123456789"},
            {"session_id": "S1", "field": "recent_decisions",
             "item_id": "r-def123456789"})
    relations.propose(type_="revision-of", from_endpoint=ends[0],
                      to_endpoint=ends[1], matched_by=["carry-absolute"],
                      matcher_version="lineage-v1", channel="lab-import",
                      project_dir=PROJECT)
    path = relations._path(PROJECT)
    alien = _alien("relation_id", sep)
    _append(path, alien)
    assert relations.forget_item_id("r-abc123456789", project_dir=PROJECT)
    assert alien in _rows(path)


@sep_param
def test_trust_forget_keeps_a_row_with_a_separator(tmp_checkpoint_dir, sep):
    trust.propose(text="a fabricated claim about deploys", kind="decision", reason=CANARY,
                  evidence=["issue:1109"], channel="cli-agent",
                  item_id="o-1234567890ab", project_dir=PROJECT)
    path = trust._path(PROJECT)
    alien = _alien("quarantine_id", sep)
    _append(path, alien)
    assert trust.redact_content_key(KEY, project_dir=PROJECT)
    assert alien in _rows(path)


@sep_param
def test_refutations_forget_keeps_a_row_with_a_separator(tmp_checkpoint_dir,
                                                         sep):
    refutations.assert_refutation(
        subject=CANARY, verdict="it deadlocked under concurrent writes",
        scope="migrations", evidence=["measurement:deadlock-trace-1"],
        channel="cli-tty", ratified=True, project_dir=PROJECT)
    path = refutations._path(PROJECT)
    alien = _alien("refutation_id", sep)
    _append(path, alien)
    assert refutations.forget_content_key(KEY, project_dir=PROJECT)
    assert alien in _rows(path)


@sep_param
def test_events_scrub_keeps_a_row_with_a_separator(tmp_checkpoint_dir, sep):
    store.append_event("i-y", "resolved", item_text=CANARY,
                       project_dir=PROJECT, writer=Writer.HUMAN)
    path = store._events_path(PROJECT)
    survivor = json.dumps({"item_ref": "i-z", "status": "resolved",
                           "note": f"left{sep}right"}, ensure_ascii=False)
    _append(path, survivor)
    assert store.scrub_event_fields(KEY, project_dir=PROJECT) == 1
    assert survivor in _rows(path)


# ---- a forgotten value INSIDE such a row is scrubbed ----------------------


@sep_param
def test_events_scrub_reaches_a_value_inside_a_separator_row(tmp_checkpoint_dir,
                                                             sep):
    path = store._events_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"item_ref": "i-y", "status": "resolved", "item_text": CANARY,
           "note": f"left{sep}right"}
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert store.scrub_event_fields(KEY, project_dir=PROJECT) == 1
    text = path.read_text(encoding="utf-8")
    assert CANARY not in text
    (only,) = [r for r in text.split("\n") if r]
    assert json.loads(only)["note"] == f"left{sep}right"  # one row, intact


def test_events_scrub_heals_a_row_an_older_scrub_already_split(
        tmp_checkpoint_dir):
    path = store._events_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    whole = json.dumps({"item_ref": "i-y", "status": "resolved",
                        "item_text": CANARY, "note": "left right"},
                       ensure_ascii=False)
    path.write_text(whole.replace(" ", "\n") + "\n", encoding="utf-8")
    assert store.scrub_event_fields(KEY, project_dir=PROJECT) == 1
    assert CANARY not in path.read_text(encoding="utf-8")


def test_events_scrub_survives_undecodable_bytes(tmp_checkpoint_dir):
    path = store._events_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    junk = b"\xff\xfe not utf-8\n"
    row = json.dumps({"item_ref": "i-y", "status": "resolved",
                      "item_text": CANARY}).encode("utf-8")
    path.write_bytes(junk + row + b"\n")
    assert store.scrub_event_fields(KEY, project_dir=PROJECT) == 1
    out = path.read_bytes()
    assert out.startswith(junk)
    assert CANARY.encode() not in out


@sep_param
def test_privacy_audit_finds_a_value_inside_a_separator_row(tmp_checkpoint_dir,
                                                            sep):
    # Tombstone only: no scrub has run, so the residue is real.
    assert store.append_event("i-t", f"forgotten:{KEY}", project_dir=PROJECT,
                              allow_disabled=True, tombstone=True, writer=Writer.HUMAN)
    path = store._events_path(PROJECT)
    row = {"item_ref": "i-y", "status": "resolved", "item_text": CANARY,
           "note": f"left{sep}right"}
    _append(path, json.dumps(row, ensure_ascii=False))
    hits = [f for f in privacy.audit_project(project_dir=PROJECT)["findings"]
            if f["content_hash"] == KEY]
    assert hits and hits[0]["surface"] == "events-note"


@sep_param
def test_privacy_audit_counts_a_separator_row_once(tmp_checkpoint_dir, sep):
    requests.open_request(to="-p-recipient", ask="an ask", why="because",
                          channel="cli-agent", project_dir=PROJECT)
    _append(requests._path(PROJECT), _alien("request_id", sep))
    result = privacy.audit_project(project_dir=PROJECT)
    assert result["requests"]["rows"] == 2  # the real row + the alien, whole


# ---- bucket migration preserves such rows byte for byte -------------------


@sep_param
def test_migrate_preserves_a_separator_row_byte_for_byte(tmp_path,
                                                         tmp_checkpoint_dir,
                                                         sep):
    real = tmp_path / "real-project"
    real.mkdir()
    link = tmp_path / "link-project"
    link.symlink_to(real, target_is_directory=True)
    legacy = tmp_checkpoint_dir / (buckets.legacy_slug(str(link)) or "")
    target = tmp_checkpoint_dir / store.project_bucket(str(real))
    row = json.dumps({"event_id": "e1", "event": "asserted",
                      "note": f"left{sep}right"}, ensure_ascii=False) + "\n"
    other = json.dumps({"event_id": "e2", "event": "asserted"}) + "\n"
    for d, body in ((legacy, row), (target, other)):
        d.mkdir(parents=True, exist_ok=True)
        (d / "events.jsonl").write_text(body, encoding="utf-8")
    record = buckets.migrate(str(link))
    assert record["mode"] == "merge"
    assert record["ledgers"] == {"events.jsonl": 1}
    assert (target / "events.jsonl").read_bytes() == (other + row).encode()


# ---- an unparseable line survives a rewrite unchanged ---------------------


def test_unparseable_line_survives_every_forget_rewriter(tmp_checkpoint_dir):
    garbage = "this is not json {"
    amendments.propose(item_id="o-1234567890ab", change="progressed",
                       evidence=CANARY, channel="cli-agent",
                       project_dir=PROJECT)
    path = amendments._path(PROJECT)
    _append(path, garbage)
    assert amendments.forget_content_key(KEY, project_dir=PROJECT)
    assert garbage in _rows(path)


# ---- resolve --status forgotten: is refused -------------------------------


@pytest.mark.parametrize("status", ["forgotten:x", "Forgotten:x",
                                    "  FORGOTTEN:abc  "])
def test_resolve_refuses_a_forgotten_status(tmp_checkpoint_dir, capsys, status):
    rc = cli.main(["resolve", "some item", "--status", status,
                   "--project", PROJECT])
    assert rc != 0
    assert "daimon forget" in capsys.readouterr().out
    assert not store._events_path(PROJECT).exists()


def test_log_refuses_a_forgotten_status(tmp_checkpoint_dir, capsys):
    rc = cli.main(["log", "--text", "t", "--status", "forgotten:abc",
                   "--project", PROJECT])
    assert rc != 0
    assert "daimon forget" in capsys.readouterr().out
    assert not store._events_path(PROJECT).exists()


def test_append_event_refuses_a_forgotten_status_without_the_tombstone_flag(
        tmp_checkpoint_dir):
    assert store.append_event("i-y", "forgotten:abc", project_dir=PROJECT, writer=Writer.HUMAN) \
        is False
    assert store.append_event("i-y", " Forgotten:abc", project_dir=PROJECT, writer=Writer.HUMAN) \
        is False
    assert not store._events_path(PROJECT).exists()
    assert store.append_event("i-y", "forgotten:abc", project_dir=PROJECT,
                              tombstone=True, writer=Writer.HUMAN) is True


# ---- a rewrite that changes nothing reports nothing removed ----------------


def test_amendments_rewrite_reports_nothing_when_no_row_matches(
        tmp_checkpoint_dir):
    amendments.propose(item_id="o-1234567890ab", change="progressed",
                       evidence=CANARY, channel="cli-agent",
                       project_dir=PROJECT)
    before = amendments._path(PROJECT).read_bytes()
    assert amendments._rewrite_without({"a-" + "d" * 12},
                                       project_dir=PROJECT) == []
    assert amendments._path(PROJECT).read_bytes() == before


def test_relations_forget_reports_nothing_when_no_row_is_dropped(
        tmp_checkpoint_dir, monkeypatch):
    path = relations._path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("junk\n", encoding="utf-8")
    ghost = {"relation_id": "rel-" + "e" * 16,
             "from": {"item_id": "r-abc123456789"}, "to": {}}
    monkeypatch.setattr(relations, "events", lambda **kw: [ghost])
    assert relations.forget_item_id("r-abc123456789",
                                    project_dir=PROJECT) == []
    assert path.read_text(encoding="utf-8") == "junk\n"


def test_refutations_forget_survives_an_unreadable_ledger(
        tmp_checkpoint_dir, monkeypatch):
    refutations.assert_refutation(
        subject=CANARY, verdict="it deadlocked under concurrent writes",
        scope="migrations", evidence=["measurement:deadlock-trace-1"],
        channel="cli-tty", ratified=True, project_dir=PROJECT)
    real = type(refutations._path(PROJECT)).read_text

    def flaky(self, *args, **kwargs):
        if self.name == "refutations.jsonl":
            raise OSError("io error")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(refutations._path(PROJECT)), "read_text", flaky)
    assert refutations.forget_content_key(KEY, project_dir=PROJECT) == []


# ---- forget finds a value inside a separator row (readers, #1138) ----------


def _inject_sep(path, sep):
    """Add a field holding the separator raw to every row on disk."""
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").split("\n")
            if ln]
    for row in rows:
        row["x_note"] = f"left{sep}right"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")


@sep_param
def test_amendments_forget_deletes_a_separator_row_by_value(tmp_checkpoint_dir,
                                                            sep):
    amendments.propose(item_id="o-1234567890ab", change="progressed",
                       evidence=CANARY, channel="cli-agent",
                       project_dir=PROJECT)
    path = amendments._path(PROJECT)
    _inject_sep(path, sep)
    keeper = amendments.propose(item_id="o-1234567890ab", change="blocked",
                                evidence="an unrelated piece of evidence",
                                channel="cli-agent", project_dir=PROJECT)
    assert amendments.forget_content_key(KEY, project_dir=PROJECT)
    assert CANARY not in path.read_text(encoding="utf-8")
    assert amendments.get(keeper, project_dir=PROJECT) is not None


@sep_param
def test_requests_forget_deletes_a_separator_row_by_value(tmp_checkpoint_dir,
                                                          sep):
    requests.open_request(to="-p-recipient", ask=CANARY, why="because",
                          channel="cli-agent", project_dir=PROJECT)
    path = requests._path(PROJECT)
    _inject_sep(path, sep)
    keeper = requests.open_request(to="-p-recipient", ask="an unrelated ask",
                                   why="because", channel="cli-agent",
                                   project_dir=PROJECT)
    assert requests.forget_content_key(KEY, project_dir=PROJECT)
    assert CANARY not in path.read_text(encoding="utf-8")
    assert keeper in requests.records(project_dir=PROJECT)


@sep_param
def test_relations_forget_deletes_a_separator_row_by_item(tmp_checkpoint_dir,
                                                          sep):
    def end(session, item):
        return {"session_id": session, "field": "recent_decisions",
                "item_id": item}

    def propose(frm, to):
        return relations.propose(
            type_="revision-of", from_endpoint=frm, to_endpoint=to,
            matched_by=["carry-absolute"], matcher_version="lineage-v1",
            channel="lab-import", project_dir=PROJECT)

    doomed = propose(end("S2", "r-abc123456789"), end("S1", "r-def123456789"))
    path = relations._path(PROJECT)
    _inject_sep(path, sep)
    keeper = propose(end("S2", "r-aaa111222333"), end("S1", "r-bbb444555666"))
    assert relations.forget_item_id("r-abc123456789", project_dir=PROJECT) \
        == [doomed]
    assert "r-abc123456789" not in path.read_text(encoding="utf-8")
    assert keeper in relations.records(project_dir=PROJECT)
