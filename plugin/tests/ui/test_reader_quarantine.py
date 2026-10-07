"""#1109 PR 2: a value under an active human quarantine reaches none of the
viewer's surfaces: load_checkpoint, diff_checkpoints, item_biography, and the
_walk_transitions-derived project_ledger/session_events/project_grid. The
reader judges nothing itself: the view does, over `trust.records`, and these
tests drive the real trust writer to pin that every surface the reader feeds
goes through it."""
import json

from daimon_briefing import config, trust
from tests.ui.scope import scoped
from tests.ui.conftest import make_checkpoint

_QVALUE = "the deploy key rotation runbook was fabricated by the agent"


def _write(bucket, name, data):
    (bucket / name).write_text(json.dumps(data) if not isinstance(data, str) else data)


def _quarantine(bucket, text, *, kind="question", channel="cli-tty"):
    """Propose a quarantine through the real trust writer, for the bucket's
    store. A human channel activates it; an agent channel leaves a candidate."""
    with config.checkpoint_dir_override(bucket.parent):
        return trust.propose(text=text, kind=kind, reason="fabricated finding",
                             evidence=["issue:1109"], channel=channel,
                             project_dir=bucket.name)


# ---- load_checkpoint --------------------------------------------------


def test_load_checkpoint_withholds_quarantined_item(bucket):
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"},
        {"text": "a live unrelated question", "trust": "inferred",
         "id": "o-bbb222bbb222"},
    ])
    _write(bucket, "latest.json", cp)
    _quarantine(bucket, _QVALUE, kind="question")

    got = scoped(bucket.parent).load_checkpoint(bucket.name, "latest")
    assert got["ok"] is True
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE not in texts
    assert "a live unrelated question" in texts


def test_load_checkpoint_candidate_quarantine_withholds_nothing(bucket):
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _quarantine(bucket, _QVALUE, kind="question", channel="cli-agent")

    got = scoped(bucket.parent).load_checkpoint(bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE in texts


def test_load_checkpoint_quarantine_matches_quote_field(bucket):
    cp = make_checkpoint(recent_decisions=[
        {"text": "short label", "quote": _QVALUE, "trust": "verbatim",
         "id": "r-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _quarantine(bucket, _QVALUE, kind="decision")

    got = scoped(bucket.parent).load_checkpoint(bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert "short label" not in texts


def test_load_checkpoint_quarantine_scoped_by_kind(bucket):
    cp = make_checkpoint(
        recent_decisions=[{"text": _QVALUE, "trust": "inferred",
                          "id": "r-aaa111aaa111"}],
        strong_beliefs=[{"text": _QVALUE, "trust": "inferred",
                        "id": "b-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _quarantine(bucket, _QVALUE, kind="decision")

    got = scoped(bucket.parent).load_checkpoint(bucket.name, "latest")
    by_key = {s["key"]: [i["text"] for i in s["items"]] for s in got["sections"]}
    assert _QVALUE not in by_key["decisions"]
    assert _QVALUE in by_key["beliefs"]


def test_load_checkpoint_latched_against_a_fresh_resolution(bucket):
    # #1109 design §5: quarantine wins over a machine/human resolution too —
    # only `release` clears it, never a resolved event on the same id.
    cp = make_checkpoint(open_questions=[
        {"text": _QVALUE, "trust": "verbatim", "id": "o-aaa111aaa111"}])
    _write(bucket, "latest.json", cp)
    _quarantine(bucket, _QVALUE, kind="question")
    (bucket / "events.jsonl").write_text(json.dumps(
        {"ts": "2026-09-24T01:00:00Z", "kind": "resolution",
         "status": "reopened", "item_ref": "o-aaa111aaa111"}) + "\n")

    got = scoped(bucket.parent).load_checkpoint(bucket.name, "latest")
    texts = [i["text"] for s in got["sections"] for i in s["items"]]
    assert _QVALUE not in texts


# ---- item_biography -----------------------------------------------------


def _one_session(d, slug, items, sid="s1", created="2026-09-24T00:00:00Z"):
    (d / slug).mkdir(parents=True, exist_ok=True)
    cp = {"session_id": sid, "format_version": "D-019", "created": created,
          "author": "ada", "project_slug": slug,
          "working_context": {"active_topic": {"text": "t"},
                              "open_questions": items,
                              "recent_decisions": []},
          "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                                 "contradictions_flagged": []}}
    (d / f"{sid}.json").write_text(json.dumps(cp))
    (d / slug / "latest.json").write_text(json.dumps(dict(cp, project_slug=slug)))


def test_item_biography_unknown_for_a_quarantined_item(bucket):
    d = bucket.parent
    _one_session(d, bucket.name,
                 [{"id": "o-aaa111aaa111", "text": _QVALUE,
                   "trust": "verbatim"}])
    _quarantine(bucket, _QVALUE, kind="question")

    got = scoped(d).item_biography(bucket.name, "o-aaa111aaa111")
    assert got["ok"] is False
    assert _QVALUE not in json.dumps(got)


# ---- diff_checkpoints -----------------------------------------------------


def test_diff_checkpoints_omits_quarantined_item(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    _one_session(d, slug, [], sid="s1", created="2026-09-23T00:00:00Z")
    _one_session(d, slug, [
        {"id": "o-aaa111aaa111", "text": _QVALUE},
        {"id": "o-bbb222bbb222", "text": "a live unrelated question"},
    ], sid="s2", created="2026-09-24T00:00:00Z")
    _quarantine(d / slug, _QVALUE, kind="question")

    got = scoped(d).diff_checkpoints(slug, "s1", "s2")
    assert got["ok"] is True
    born_texts = [b["text"] for b in got["born"]]
    assert _QVALUE not in born_texts
    assert "a live unrelated question" in born_texts


# ---- _walk_transitions -> project_ledger / session_events / project_grid --
# All three surfaces share one walk (reader.py's own docstring: "the surfaces
# can never disagree about what happened"), so one fixture through
# project_ledger pins the choke point every one of them reads through.


def test_project_ledger_omits_quarantined_object(tmp_path):
    d = tmp_path / "checkpoints"
    slug = "-tmp-proj"
    _one_session(d, slug, [
        {"id": "o-aaa111aaa111", "text": _QVALUE},
        {"id": "o-bbb222bbb222", "text": "a live unrelated question"}])
    _quarantine(d / slug, _QVALUE, kind="question")

    got = scoped(d).project_ledger(slug)
    assert got["ok"] is True
    texts = [r["text"] for g in got["groups"] for r in g["rows"]]
    assert _QVALUE not in texts
    assert "a live unrelated question" in texts
