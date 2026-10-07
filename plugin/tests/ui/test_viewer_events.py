"""The viewer's activity, ledger, grid, session and biography routes read
through the view (#1132 PR 8b-2).

Their facts come from `view.events`, `view.verifications`, `view.sessions`,
`view.open_sessions` and the snapshot's resolution fold: a quarantined or
forgotten value is in no payload, the key a tombstone names is in none either,
a resolution is the kernel's (the rule `daimon diff` applies), and a trust
ledger that cannot be read hides items, keeps notes readable and says so in
`notes`. Stores are built by the real writers and served by the real server."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from daimon_briefing import normalize, store, trust, view
from daimon_ui import server

PROJECT = "/p/viewer-events"
SLUG = store.project_slug(PROJECT)
HIDE = "the plan that was never reviewed by anybody"
KEEP = "an open question that stays visible to all"
TOPIC = "the weekly sync cadence for the viewer"
OLD_TOPIC = "first topic of the viewer events"
A, B, C = "o-aaaaaaaaaaaa", "o-bbbbbbbbbbbb", "o-dddddddddddd"


def _cp(sid, created, *, topic, questions):
    return {"session_id": sid, "created": created, "author": "ada",
            "format_version": "D-019",
            "working_context": {
                "active_topic": {"text": topic, "trust": "inferred"},
                "open_questions": list(questions), "recent_decisions": []},
            "epistemic_snapshot": {}}


def _q(text, iid):
    return {"text": text, "id": iid, "trust": "inferred"}


def _quarantine(text, kind="question"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=PROJECT)


def _forget(text):
    store.append_event("i-gone", f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=PROJECT)


def _break_trust(root):
    with open(root / SLUG / "trust.jsonl", "ab") as fh:
        fh.write(b"<<<<<<< HEAD\n")


@pytest.fixture
def two(tmp_checkpoint_dir):
    store.write_checkpoint("S-1", _cp(
        "S-1", "2026-08-01T00:00:00Z", topic=OLD_TOPIC,
        questions=[_q(KEEP, B), _q(HIDE, C), _q("a goal that closes", A)]),
        project_dir=PROJECT)
    store.write_checkpoint("S-2", _cp(
        "S-2", "2026-08-02T00:00:00Z", topic=TOPIC,
        questions=[_q(KEEP, B), _q(HIDE, C)]), project_dir=PROJECT)
    return tmp_checkpoint_dir


@pytest.fixture
def base(two):
    s = server.make_server(two, SLUG, "viewer", port=0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}"
    s.shutdown()
    s.server_close()


def _get(base, path):
    with urllib.request.urlopen(base + path) as resp:
        return json.loads(resp.read())


# ---- /api/activity ----------------------------------------------------------


def test_a_forgotten_value_and_its_key_are_in_no_activity_row(base):
    store.append_event(A, "resolved", note=HIDE, item_text=HIDE,
                       project_dir=PROJECT)
    _forget(HIDE)
    got = _get(base, "/api/activity")
    blob = json.dumps(got)
    assert HIDE not in blob and normalize.content_key(HIDE) not in blob
    [tomb] = [r for r in got["rows"] if r["kind"] == "tombstone"]
    assert tomb["extra"]["status"] == "forgotten"
    assert tomb["extra"]["item_text"] is None and tomb["detail"] is None
    [res] = [r for r in got["rows"] if r["kind"] == "resolution"]
    assert res["detail"] is None and res["extra"]["item_text"] is None


def test_a_quarantined_note_and_item_text_read_as_the_marker(base):
    store.append_event(A, "resolved", note=HIDE, item_text=HIDE,
                       project_dir=PROJECT)
    rec = _quarantine(HIDE)
    got = _get(base, "/api/activity")
    [res] = [r for r in got["rows"] if r["kind"] == "resolution"]
    marker = f"[withheld: quarantine {rec}]"
    assert res["detail"] == marker and res["extra"]["item_text"] == marker
    assert HIDE not in json.dumps(got)


def test_a_session_row_takes_its_topic_from_the_judged_read(base):
    _forget(OLD_TOPIC)
    got = _get(base, "/api/activity")
    sessions = {r["session_id"]: r for r in got["rows"] if r["kind"] == "session"}
    assert sessions["S-2"]["detail"] == TOPIC
    assert sessions["S-1"]["detail"] is None
    assert OLD_TOPIC not in json.dumps(got)


def test_an_unreadable_trust_ledger_keeps_notes_and_masks_item_text(base, two):
    store.append_event(A, "resolved", note=KEEP, item_text="a closed goal",
                       project_dir=PROJECT)
    _break_trust(two)
    got = _get(base, "/api/activity")
    assert any("trust.jsonl" in n for n in got["notes"])
    [res] = [r for r in got["rows"] if r["kind"] == "resolution"]
    assert res["detail"] == KEEP
    assert res["extra"]["item_text"] == "[withheld: trust ledger unreadable]"
    assert all(r["detail"] is None for r in got["rows"]
               if r["kind"] == "session")


def test_a_clean_store_has_no_notes(base):
    assert _get(base, "/api/activity")["notes"] == []


# ---- /api/ledger ------------------------------------------------------------


@pytest.mark.parametrize("status", ["resolved", "resolved-agent-verified",
                                    "done"])
def test_ledger_follows_the_kernels_resolution_fold(base, status):
    store.append_event(A, status, note="closed", project_dir=PROJECT)
    got = _get(base, "/api/ledger")
    [row] = [r for g in got["groups"] for r in g["rows"] if r["id"] == A]
    assert row["last_event"]["kind"] == "resolved"


def test_ledger_totals_count_one_resolution_per_resolved_ref(base):
    walk_events = _get(base, "/api/ledger")["totals"]["events"]
    store.append_event(A, "resolved-agent-verified", project_dir=PROJECT)
    store.append_event(A, "resolved", project_dir=PROJECT)
    assert _get(base, "/api/ledger")["totals"]["events"] == walk_events + 1


def test_ledger_reopened_item_is_not_resolved(base):
    store.append_event(A, "resolved", project_dir=PROJECT)
    store.append_event(A, "reopened", project_dir=PROJECT)
    got = _get(base, "/api/ledger")
    [row] = [r for g in got["groups"] for r in g["rows"] if r["id"] == A]
    assert row["last_event"]["kind"] != "resolved"


def test_ledger_never_shows_a_withheld_value_or_topic(base):
    _quarantine(HIDE)
    _forget(OLD_TOPIC)
    got = _get(base, "/api/ledger")
    blob = json.dumps(got)
    assert HIDE not in blob and OLD_TOPIC not in blob
    assert {g["session_id"]: g["active_topic"] for g in got["groups"]}.get(
        "S-1", None) is None


def test_ledger_says_why_when_the_trust_ledger_is_unreadable(base, two):
    _break_trust(two)
    got = _get(base, "/api/ledger")
    assert got["groups"] == [] and got["totals"]["objects"] == 0
    assert any("trust.jsonl" in n for n in got["notes"])


# ---- /api/session and /api/grid ---------------------------------------------


def test_session_and_grid_hide_withheld_items_and_name_the_notes(base, two):
    _quarantine(HIDE)
    sess = _get(base, "/api/session?sid=S-2")
    grid = _get(base, "/api/grid")
    assert HIDE not in json.dumps(sess) + json.dumps(grid)
    assert sess["notes"] == [] and grid["notes"] == []
    _break_trust(two)
    for got in (_get(base, "/api/session?sid=S-2"), _get(base, "/api/grid")):
        assert any("trust.jsonl" in n for n in got["notes"])
    assert _get(base, "/api/session?sid=S-2")["objects"] == []
    assert _get(base, "/api/grid")["rows"] == []


def test_session_page_topic_is_the_judged_one(base):
    _forget(TOPIC)
    got = _get(base, "/api/session?sid=S-2")
    assert got["session"]["active_topic"] is None
    assert TOPIC not in json.dumps(got)


def test_session_page_carries_a_receipt_state_only_for_an_opted_in_project(
        base, two):
    assert "receipt" not in _get(base, "/api/session?sid=S-2")["session"]
    for name in ("latest.json", "S-2.json"):
        path = two / SLUG / name if name == "latest.json" else two / name
        data = json.loads(path.read_text())
        data["receipts"] = True
        path.write_text(json.dumps(data))
    got = _get(base, "/api/session?sid=S-2")
    assert got["session"]["receipt"]["state"] == "missing"


def test_grid_check_rows_come_through_the_view(base):
    store.append_verification(B, "quote", "not-in-transcript",
                              project_dir=PROJECT)
    rows = {r["id"]: r for r in _get(base, "/api/grid")["rows"]}
    [check] = rows[B]["checks"]
    assert check["detail"] == "quote: not-in-transcript"


# ---- /api/biography ---------------------------------------------------------


def test_biography_resolution_is_the_kernels_with_a_judged_note(base):
    store.append_event(A, "resolved-agent-verified", note=HIDE,
                       project_dir=PROJECT)
    rec = _quarantine(HIDE)
    got = _get(base, f"/api/biography?id={A}")
    [res] = [e for e in got["events"] if e["kind"] == "resolved"]
    assert res["detail"] == f"[withheld: quarantine {rec}]"
    assert HIDE not in json.dumps(got)


def test_biography_forgotten_resolution_note_reads_as_absent(base):
    store.append_event(A, "resolved", note=HIDE, project_dir=PROJECT)
    _forget(HIDE)
    [res] = [e for e in _get(base, f"/api/biography?id={A}")["events"]
             if e["kind"] == "resolved"]
    assert res["detail"] is None


def test_biography_origin_is_on_disk_when_the_session_is_listed(base):
    got = _get(base, f"/api/biography?id={B}")
    assert got["trust_anatomy"]["checks"]["origin_on_disk"] is True
    assert got["notes"] == []


def test_biography_names_the_unreadable_trust_ledger(base, two):
    _break_trust(two)
    got = _get(base, f"/api/biography?id={B}")
    assert got["ok"] is False
    assert "trust.jsonl" in got["error"]["why"]


# ---- the page shows the notes -----------------------------------------------


@pytest.mark.parametrize("fn", [
    "renderSections", "renderDiffView", "renderActivityView", "renderBioPanel",
    "renderLedgerView", "renderSessionView", "renderStripView",
    "renderPrintView"])
def test_every_view_that_can_come_back_empty_shows_the_notes(fn):
    """An empty view says why: each renderer puts `data.notes` on the same
    banner as `data.partial` (a bio panel on its own note line)."""
    js = (Path(server.__file__).parent / "static" / "render.js").read_text(
        encoding="utf-8")
    start = js.index(f"export function {fn}(")
    end = js.find("export function ", start + 1)
    assert "data.notes" in js[start:end if end != -1 else None]


# ---- one failure shape ------------------------------------------------------


@pytest.mark.parametrize("name, path", [
    ("sessions", "/api/activity"), ("events", "/api/activity"),
    ("verifications", "/api/activity"), ("sessions", "/api/ledger"),
    ("open_sessions", "/api/ledger"), ("verifications", "/api/ledger"),
    ("open_sessions", "/api/grid"), ("verifications", "/api/grid"),
    ("open_sessions", "/api/session?sid=S-2"), ("pointers",
                                                 "/api/session?sid=S-2"),
    ("open_sessions", f"/api/biography?id={B}"),
    ("verifications", f"/api/biography?id={B}")])
def test_a_view_that_raises_is_a_generic_500(base, monkeypatch, name, path):
    def boom(*_a, **_k):
        raise RuntimeError(f"{HIDE} at /private/path")

    monkeypatch.setattr(view, name, boom)
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(base + path)
    assert err.value.code == 500
    body = err.value.read()
    assert HIDE.encode() not in body and b"/private" not in body


# ---- the reader reads no file -----------------------------------------------


def test_the_reader_opens_no_file_and_parses_no_json():
    """Code shape, by AST: no `open`, no `json`, no `.read_text`/`.splitlines`
    and no `Path` in the reader (scar 0112 splits ledger text only through
    `jsonl.split_rows`, and the reader holds no ledger text at all)."""
    import ast

    from daimon_ui import reader
    tree = ast.parse(Path(reader.__file__).read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [a.name for a in node.names]
            mod = getattr(node, "module", None)
            if "json" in names or mod == "json" or "pathlib" in (mod or ""):
                bad.append(node.lineno)
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                bad.append(node.lineno)
            if isinstance(f, ast.Attribute) and f.attr in (
                    "read_text", "read_bytes", "splitlines", "iterdir",
                    "exists", "is_dir", "is_file", "loads", "load"):
                bad.append(node.lineno)
    assert bad == []


def test_the_reader_names_no_store_directory():
    """One scope: the server's runner sets the store per request, so no reader
    function takes a data dir or sets the override itself."""
    import ast

    from daimon_ui import reader
    tree = ast.parse(Path(reader.__file__).read_text(encoding="utf-8"))
    args = {a.arg for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
            for a in n.args.args}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "data_dir" not in args and "bucket" not in args
    assert not attrs & {"checkpoint_dir_override", "checkpoint_dir"}
