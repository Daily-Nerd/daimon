"""#1109 PR 2: daimon_ui/reader.py is a SECOND, independent implementation of
resolution/withhold logic — it reads checkpoint JSON and trust.jsonl directly,
never through daimon_briefing.store/briefing/trust (module docstring: "No
daimon imports — files are the seam"). A gate built only into the main
package would silently not apply here, so this file pins the reader's own
value-keyed quarantine fold and every surface it feeds: load_checkpoint,
diff_checkpoints, item_biography, and the _walk_transitions-derived
project_ledger/session_events/project_grid.
"""
import json

from daimon_ui import reader
from tests.ui.conftest import make_checkpoint

_QVALUE = "the deploy key rotation runbook was fabricated by the agent"


def _write(bucket, name, data):
    (bucket / name).write_text(json.dumps(data) if not isinstance(data, str) else data)


def _trust_row(event, quarantine_id, *, channel="cli-tty", kind="question",
               value_key=None, ratified=None):
    row = {"version": 1, "ts": "2026-09-24T00:00:00Z", "order": 1,
           "event_id": f"e-{event}", "event": event,
           "quarantine_id": quarantine_id, "channel": channel,
           "author": "ada"}
    if event == "quarantined":
        row.update({"kind": kind, "value_key": value_key, "scope_slug": "p",
                    "item_id": "", "reason": "fabricated, no matching PR",
                    "evidence": ["issue:1109"]})
        if ratified is not None:
            row["ratified"] = ratified
    return row


def _write_active_quarantine(bucket, text, *, kind="question"):
    key = reader._content_key(text)
    row = _trust_row("quarantined", "tr-aaaaaaaaaaaa", channel="cli-tty",
                     kind=kind, value_key=key, ratified=True)
    (bucket / "trust.jsonl").write_text(json.dumps(row) + "\n")
    return key


# ---- sync lock: the duplicated algorithm must never drift -----------------


def test_reader_content_key_stays_in_sync_with_normalize():
    from daimon_briefing import normalize

    samples = (
        "hello world", "Héllo  wörld", "аоТ test", "x" * 5000, "  ", "",
        None, "café​shop", "MiXeD CaSe\twith\ttabs",
    )
    for s in samples:
        assert reader._content_key(s) == normalize.content_key(s)


def test_reader_human_channels_match_channels_base_authority():
    from daimon_briefing import channels

    human = {c for c, tier in channels.BASE_CHANNEL_AUTHORITY.items()
            if tier == "human"}
    assert reader._TRUST_HUMAN_CHANNELS == human


# ---- fold correctness -------------------------------------------------


def test_active_quarantine_keys_empty_for_missing_ledger(tmp_path):
    assert reader._active_quarantine_keys(tmp_path / "nope") == set()


def test_active_quarantine_keys_candidate_withholds_nothing(tmp_path):
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    row = _trust_row("quarantined", "tr-bbbbbbbbbbbb", channel="cli-agent",
                     kind="question", value_key=key)  # ratified absent
    (bucket / "trust.jsonl").write_text(json.dumps(row) + "\n")
    assert reader._active_quarantine_keys(bucket) == set()


def test_active_quarantine_keys_dismissed_and_released_withhold_nothing(tmp_path):
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    rows = [
        _trust_row("quarantined", "tr-cccccccccccc", channel="cli-agent",
                   kind="question", value_key=key),
        _trust_row("dismissed", "tr-cccccccccccc", channel="cli-tty"),
        _trust_row("quarantined", "tr-dddddddddddd", channel="cli-tty",
                   kind="question", value_key=key, ratified=True),
        _trust_row("released", "tr-dddddddddddd", channel="cli-tty"),
    ]
    (bucket / "trust.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    assert reader._active_quarantine_keys(bucket) == set()


def test_active_quarantine_keys_agent_confirm_never_activates(tmp_path):
    # Structural pin, this module's own version of PR 1's guard: even a
    # hand-edited ledger where an agent-channel row claims "confirmed" must
    # not move a candidate to active — authority is derived from the
    # channel, never trusted from the row's own event name.
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    rows = [
        _trust_row("quarantined", "tr-eeeeeeeeeeee", channel="cli-agent",
                   kind="question", value_key=key),
        _trust_row("confirmed", "tr-eeeeeeeeeeee", channel="cli-agent"),
    ]
    (bucket / "trust.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    assert reader._active_quarantine_keys(bucket) == set()


def test_active_quarantine_keys_tolerates_malformed_lines(tmp_path):
    # Append-log tolerance (same posture as resolutions()): a blank line, an
    # unparseable line, a non-dict JSON value, and a row missing its id are
    # each skipped rather than raising or aborting the whole fold.
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    good = _trust_row("quarantined", "tr-1111aaaa1111", channel="cli-tty",
                      kind="question", value_key=key, ratified=True)
    lines = [
        "",                       # blank line
        "{not json",              # malformed JSON
        json.dumps([1, 2, 3]),    # valid JSON, not a dict
        json.dumps({"event": "quarantined", "channel": "cli-tty"}),  # no id
        json.dumps(good),
    ]
    (bucket / "trust.jsonl").write_text("\n".join(lines) + "\n")
    assert reader._active_quarantine_keys(bucket) == {("question", key)}


def test_active_quarantine_keys_second_quarantined_row_is_first_writer_wins(tmp_path):
    # A duplicate `quarantined` row for a tid already candidate/active (not
    # dismissed/released) is a stray write, not a reopen — the ORIGINAL
    # record's kind/value_key wins, mirroring trust.fold's own doctrine.
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    other_key = reader._content_key("a completely different quarantined value")
    rows = [
        _trust_row("quarantined", "tr-2222bbbb2222", channel="cli-tty",
                   kind="question", value_key=key, ratified=True),
        _trust_row("quarantined", "tr-2222bbbb2222", channel="cli-tty",
                   kind="decision", value_key=other_key, ratified=True),
    ]
    (bucket / "trust.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    assert reader._active_quarantine_keys(bucket) == {("question", key)}


def test_active_quarantine_keys_two_step_propose_then_confirm(tmp_path):
    # The other path to active: an agent proposes (candidate), then a
    # SEPARATE human `confirmed` row promotes it — distinct from the
    # direct-active-on-propose path every other fixture here uses.
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = reader._content_key(_QVALUE)
    rows = [
        _trust_row("quarantined", "tr-3333cccc3333", channel="cli-agent",
                   kind="question", value_key=key),
        _trust_row("confirmed", "tr-3333cccc3333", channel="cli-tty"),
    ]
    (bucket / "trust.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))
    assert reader._active_quarantine_keys(bucket) == {("question", key)}


def test_active_quarantine_keys_scoped_by_kind(tmp_path):
    bucket = tmp_path / "-tmp-proj"
    bucket.mkdir(parents=True)
    key = _write_active_quarantine(bucket, _QVALUE, kind="decision")
    assert reader._active_quarantine_keys(bucket) == {("decision", key)}


# ---- load_checkpoint --------------------------------------------------


def test_load_checkpoint_withholds_quarantined_item(bucket):
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"},
        {"text": "a live unrelated question", "trust": "inferred",
         "id": "o-bbb222bbb222"},
    ])
    _write(bucket, "latest.json", cp)
    _write_active_quarantine(bucket, _QVALUE, kind="question")

    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    assert got["ok"] is True
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE not in texts
    assert "a live unrelated question" in texts


def test_load_checkpoint_candidate_quarantine_withholds_nothing(bucket):
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    key = reader._content_key(_QVALUE)
    row = _trust_row("quarantined", "tr-ffffffffffff", channel="cli-agent",
                     kind="question", value_key=key)
    (bucket / "trust.jsonl").write_text(json.dumps(row) + "\n")

    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE in texts


def test_load_checkpoint_quarantine_matches_quote_field(bucket):
    cp = make_checkpoint(recent_decisions=[
        {"text": "short label", "quote": _QVALUE, "trust": "verbatim",
         "id": "r-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _write_active_quarantine(bucket, _QVALUE, kind="decision")

    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert "short label" not in texts


def test_load_checkpoint_quarantine_scoped_by_kind(bucket):
    cp = make_checkpoint(
        recent_decisions=[{"text": _QVALUE, "trust": "inferred",
                          "id": "r-aaa111aaa111"}],
        strong_beliefs=[{"text": _QVALUE, "trust": "inferred",
                        "id": "b-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _write_active_quarantine(bucket, _QVALUE, kind="decision")

    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    by_key = {s["key"]: [i["text"] for i in s["items"]] for s in got["sections"]}
    assert _QVALUE not in by_key["decisions"]
    assert _QVALUE in by_key["beliefs"]


def test_load_checkpoint_latched_against_a_fresh_resolution(bucket):
    # #1109 design §5: quarantine wins over a machine/human resolution too —
    # only `release` clears it, never a resolved event on the same id.
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _write_active_quarantine(bucket, _QVALUE, kind="question")
    (bucket / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-09-24T01:00:00Z", "kind": "resolution",
         "status": "reopened", "item_ref": "o-aaa111aaa111"}) + "\n")

    got = reader.load_checkpoint(bucket.parent, bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE not in texts


# ---- item_biography -----------------------------------------------------


def test_item_biography_unknown_for_a_quarantined_item(bucket):
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _write_active_quarantine(bucket, _QVALUE, kind="question")

    got = reader.item_biography(bucket.parent, bucket.name, "o-aaa111aaa111")
    assert got["ok"] is False
    assert _QVALUE not in json.dumps(got)


# ---- diff_checkpoints -----------------------------------------------------


def test_diff_checkpoints_omits_quarantined_item(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)

    def _cp(sid, created, items):
        return {"session_id": sid, "format_version": "D-019", "created": created,
                "author": "ada", "project_slug": slug,
                "working_context": {"active_topic": {"text": "t"},
                                    "open_questions": items,
                                    "recent_decisions": []},
                "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                       "contradictions_flagged": []}}

    (d / "s1.json").write_text(json.dumps(_cp("s1", "2026-09-23T00:00:00Z", [])))
    (d / "s2.json").write_text(json.dumps(_cp("s2", "2026-09-24T00:00:00Z", [
        {"id": "o-aaa111aaa111", "text": _QVALUE},
        {"id": "o-bbb222bbb222", "text": "a live unrelated question"},
    ])))
    (d / slug / "latest.json").write_text(json.dumps(_cp(
        "s2", "2026-09-24T00:00:00Z", [])))
    _write_active_quarantine(d / slug, _QVALUE, kind="question")

    got = reader.diff_checkpoints(d, slug, "s1", "s2")
    assert got["ok"] is True
    born_texts = [b["text"] for b in got["born"]]
    assert _QVALUE not in born_texts
    assert "a live unrelated question" in born_texts


# ---- _walk_transitions -> project_ledger / session_events / project_grid --
# All three surfaces share one walk (reader.py's own docstring: "the surfaces
# can never disagree about what happened"), so one fixture through
# project_ledger pins the choke point every one of them reads through.


def test_project_ledger_omits_quarantined_object(tmp_path):
    from tests.ui.test_reader_ledger import _cp, _item

    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    (d / slug).mkdir(parents=True)
    (d / "s1.json").write_text(json.dumps(_cp(
        "s1", "2026-09-24T00:00:00Z", slug,
        [_item("o-aaa111aaa111", _QVALUE),
         _item("o-bbb222bbb222", "a live unrelated question")])))
    (d / slug / "latest.json").write_text(json.dumps(
        _cp("s1", "2026-09-24T00:00:00Z", slug, [])))
    _write_active_quarantine(d / slug, _QVALUE, kind="question")

    got = reader.project_ledger(d, slug)
    assert got["ok"] is True
    texts = [r["text"] for g in got["groups"] for r in g["rows"]]
    assert _QVALUE not in texts
    assert "a live unrelated question" in texts
