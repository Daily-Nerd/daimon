"""`view.judge`, the per-bucket verdict the recall index is built and queried
with, and the id rule in `view.classify` (#1132 PR 9a, D9.1 and D9.2).

Stores are built by the real writers. Reads are counted by wrapping
`jsonl.read`, the one reader a ledger's health comes from."""

import json
import time

from daimon_briefing import config, jsonl, normalize, schema, store, trust, view
from daimon_briefing.surfaces import Writer

TOPIC = next(f for f in schema.ITEM_FIELDS if f.kind == "topic")
DECISION = next(f for f in schema.ITEM_FIELDS if f.kind == "decision")


def _write(project, decisions, sid="S-1"):
    cp = {"session_id": sid, "created": "2026-08-01T00:00:00Z",
          "working_context": {"recent_decisions": decisions},
          "epistemic_snapshot": {}}
    store.write_checkpoint(sid, cp, project_dir=project, writer=Writer.HUMAN)
    return store.project_slug(project)


def _forget_id(project, item_id, text="the text at forget time"):
    store.append_event(item_id, f"forgotten:{normalize.content_key(text)}",
                       kind="tombstone", tombstone=True, project_dir=project, writer=Writer.HUMAN)


def _quarantine(project, text, kind="decision"):
    return trust.propose(text=text, kind=kind, reason="fabricated finding",
                         evidence=["issue:1"], channel="cli-tty",
                         project_dir=project)


class _Reads:
    """Counts `jsonl.read` per file name."""

    def __init__(self, monkeypatch):
        self.names = []
        real = jsonl.read

        def counting(path, *a, **k):
            self.names.append(path.name)
            return real(path, *a, **k)

        monkeypatch.setattr(jsonl, "read", counting)

    def count(self, name):
        return self.names.count(name)


def test_a_bucket_with_no_ledgers_is_empty(tmp_checkpoint_dir):
    slug = _write("/p/j-empty", [{"text": "plain", "id": "d-aaaaaa"}])
    judge = view.judge(slug)
    assert judge.empty is True
    assert judge.snap.forgotten_ids == frozenset()


def test_a_forgotten_id_is_in_the_snapshot_and_judge(tmp_checkpoint_dir):
    slug = _write("/p/j-id", [{"text": "kept", "id": "d-aaaaaa"},
                              {"text": "other words", "id": "d-bbbbbb"}])
    _forget_id("/p/j-id", "d-bbbbbb")
    judge = view.judge(slug)
    assert judge.snap.forgotten_ids == frozenset({"d-bbbbbb"})
    assert judge.empty is False
    assert view.snapshot(slug).forgotten_ids == frozenset({"d-bbbbbb"})


def test_a_later_reopen_lifts_the_forgotten_id(tmp_checkpoint_dir):
    slug = _write("/p/j-reopen", [{"text": "other words", "id": "d-bbbbbb"}])
    _forget_id("/p/j-reopen", "d-bbbbbb")
    time.sleep(1.1)
    store.append_event("d-bbbbbb", "reopened", project_dir="/p/j-reopen", writer=Writer.HUMAN)
    assert view.judge(slug).snap.forgotten_ids == frozenset()


def test_classify_withholds_by_id_when_the_text_differs(tmp_checkpoint_dir):
    slug = _write("/p/j-cls", [{"text": "text that was redacted since",
                                "id": "d-bbbbbb"}])
    _forget_id("/p/j-cls", "d-bbbbbb")
    snap = view.judge(slug).snap
    verdict = view.classify(DECISION, {"text": "a different wording",
                                       "id": "d-bbbbbb"}, snap)
    assert isinstance(verdict, view.Withheld)
    assert verdict.reason == "forgotten"
    assert verdict.item_id == "d-bbbbbb"
    other = view.classify(DECISION, {"text": "a different wording",
                                     "id": "d-cccccc"}, snap)
    assert isinstance(other, view.Visible)


def test_the_id_rule_reaches_every_view_projection(tmp_checkpoint_dir):
    slug = _write("/p/j-open", [{"text": "stays visible", "id": "d-aaaaaa"},
                                {"text": "goes away", "id": "d-bbbbbb"}])
    _forget_id("/p/j-open", "d-bbbbbb")
    opened = view.open(slug, live=False)
    texts = [d["text"] for d in
             opened.checkpoint["working_context"]["recent_decisions"]]
    assert texts == ["stays visible"]
    assert isinstance(view.lookup(slug, "d-bbbbbb"), view.Withheld)
    assert view.match(slug, "d-bbbbbb").hits == ()


def test_the_id_is_per_bucket_never_machine_wide(tmp_checkpoint_dir):
    a = _write("/p/j-a", [{"text": "one thing", "id": "d-bbbbbb"}])
    b = _write("/p/j-b", [{"text": "another thing", "id": "d-bbbbbb"}])
    _forget_id("/p/j-a", "d-bbbbbb")
    assert view.judge(a).snap.forgotten_ids == frozenset({"d-bbbbbb"})
    assert view.judge(b).snap.forgotten_ids == frozenset()


def test_judge_is_memoized_until_a_ledger_changes(tmp_checkpoint_dir,
                                                  monkeypatch):
    slug = _write("/p/j-memo", [{"text": "plain", "id": "d-aaaaaa"}])
    first = view.judge(slug)
    reads = _Reads(monkeypatch)
    assert view.judge(slug) is first
    assert reads.names == []
    _forget_id("/p/j-memo", "d-aaaaaa")
    changed = view.judge(slug)
    assert changed is not first
    assert changed.snap.forgotten_ids == frozenset({"d-aaaaaa"})


def test_a_forget_in_another_bucket_drops_the_memo(tmp_checkpoint_dir):
    a = _write("/p/j-x", [{"text": "shared sentence", "id": "d-aaaaaa"}])
    _write("/p/j-y", [{"text": "shared sentence", "id": "d-bbbbbb"}])
    assert view.judge(a).empty is True
    store.append_event(
        "d-bbbbbb", f"forgotten:{normalize.content_key('shared sentence')}",
        kind="tombstone", tombstone=True, project_dir="/p/j-y", writer=Writer.HUMAN)
    judge = view.judge(a)
    assert normalize.content_key("shared sentence") in judge.snap.forgotten
    assert judge.empty is False


def test_a_quarantine_written_after_the_memo_is_seen(tmp_checkpoint_dir):
    slug = _write("/p/j-q", [{"text": "a claim that was fabricated", "id": "d-aaaaaa"}])
    assert view.judge(slug).empty is True
    _quarantine("/p/j-q", "a claim that was fabricated")
    judge = view.judge(slug)
    assert judge.empty is False
    verdict = judge.verdict(DECISION, {"text": "a claim that was fabricated",
                                       "id": "d-aaaaaa"})
    assert isinstance(verdict, view.Withheld)
    assert verdict.reason == "quarantine"


def test_an_unreadable_trust_ledger_closes_and_is_never_memoized(
        tmp_checkpoint_dir, monkeypatch):
    slug = _write("/p/j-closed", [{"text": "claim", "id": "d-aaaaaa"}])
    ledger = config.checkpoint_dir() / slug / "trust.jsonl"
    ledger.write_bytes(b"this is not json\n")
    reads = _Reads(monkeypatch)
    first = view.judge(slug)
    second = view.judge(slug)
    assert first.snap.closed and second.snap.closed
    assert first is not second
    assert reads.count("trust.jsonl") == 2
    verdict = second.verdict(DECISION, {"text": "claim", "id": "d-aaaaaa"})
    assert isinstance(verdict, view.Withheld) and verdict.reason == "closed"


def test_a_cold_judge_reads_the_trust_ledger_once(tmp_checkpoint_dir,
                                                  monkeypatch):
    slug = _write("/p/j-once", [{"text": "claim", "id": "d-aaaaaa"}])
    _quarantine("/p/j-once", "another claim nobody made")
    reads = _Reads(monkeypatch)
    view.judge(slug)
    assert reads.count("trust.jsonl") == 1
    view.judge(slug)
    assert reads.count("trust.jsonl") == 1


def test_judge_of_no_bucket_judges_the_forgotten_set_only(tmp_checkpoint_dir):
    slug = _write("/p/j-set", [{"text": "kept words", "id": "d-aaaaaa"}])
    store.append_event(
        "d-aaaaaa", f"forgotten:{normalize.content_key('kept words')}",
        kind="tombstone", tombstone=True, project_dir="/p/j-set", writer=Writer.HUMAN)
    for nobody in (None, "no-such-bucket"):
        judge = view.judge(nobody)
        assert judge.snap.closed is False
        assert judge.snap.forgotten_ids == frozenset()
        verdict = judge.verdict(DECISION, {"text": "kept words"})
        assert isinstance(verdict, view.Withheld)
        assert verdict.reason == "forgotten"
    assert slug


def test_peek_judges_through_the_same_id_rule(tmp_checkpoint_dir):
    slug = _write("/p/j-peek", [{"text": "stays", "id": "d-aaaaaa"},
                                {"text": "leaves", "id": "d-bbbbbb"}])
    _forget_id("/p/j-peek", "d-bbbbbb")
    got = store.read_latest_body(project_dir=slug, route=store.Route.OWN,
                                 admit=store.Admit.ANY)
    assert view.peek(got, slug, forgotten=view.forgotten_keys()
                     ).visible_items == 1


def test_a_warm_judge_of_three_buckets_is_fast(tmp_checkpoint_dir):
    slugs = [_write(f"/p/j-fast-{i}", [{"text": f"item {i}",
                                        "id": f"d-aaaa0{i}"}])
             for i in range(3)]
    for slug in slugs:
        view.judge(slug)
    start = time.perf_counter()
    for _ in range(20):
        for slug in slugs:
            view.judge(slug)
    per_round = (time.perf_counter() - start) / 20
    assert per_round < 0.005, per_round


def test_the_forgotten_stamp_follows_a_foreign_tombstone(tmp_checkpoint_dir,
                                                        monkeypatch):
    ledger = config.team_dir() / "remote" / "authors" / "other" / "tombstones.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("")
    before = store.forgotten_stamp()
    ledger.write_text(json.dumps({"key": "k" * 16}) + "\n")
    assert store.forgotten_stamp() != before
    monkeypatch.setattr(store, "_foreign_tombstone_paths",
                        lambda include_own=False: [config.team_dir() / "gone.jsonl"])
    assert store.forgotten_stamp()[2][0][1:] == (None, None, None)


def test_a_stamp_that_is_not_a_bucket_name_never_reaches_a_bucket(
        tmp_checkpoint_dir, monkeypatch):
    project = "/p/j-path"
    slug = _write(project, [{"text": "a claim that was fabricated",
                             "id": "d-aaaaaa"}])
    _quarantine(project, "a claim that was fabricated")
    monkeypatch.setenv("DAIMON_PROJECT_DIR", project)
    for stamp in (project, "../x", "a\\b", "..", ""):
        judge = view.judge(stamp)
        assert judge.snap.quarantined == frozenset(), stamp
    assert view.judge(slug).snap.quarantined
