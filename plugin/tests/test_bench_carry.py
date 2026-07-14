"""Carry-on benchmark axis (#274): stamp, cache separation, honest scoring.

Carry folds prior unresolved items forward into later checkpoints, so a later
(non-gold) session's checkpoint can host a verbatim copy of a gold session's
item. These tests pin the three guarantees the axis needs:

- the run config stamps the carry mode truthfully (``carry: "on"/"off"``);
- carry-on and carry-off runs never share cache entries (disjoint key spaces),
  while carry-off keys stay byte-identical to pre-#274 keys;
- scoring credits a carried copy to the session that ORIGINATED the item
  (``carried_from``), never to the hosting session, deduped so a gold session
  counts at most once — and the fold is deterministic at any worker count
  because it runs in the single-threaded, listed-session-order write loop.

Deterministic LLM fake throughout (no backend needed), same pattern as
test_bench_adapter.EchoChat.
"""

import json
from pathlib import Path

from tests.bench import adapter, cache as cache_mod, metrics
from tests.bench import run as bench_run

# Distinct vocabularies on purpose: carry.merge dedups items by salient-term
# overlap, so texts sharing >=3 terms would twin-merge instead of carrying.
_OQ = {
    "goldmarker": "must retest goldmarker battery drain overnight",
    "coffeemarker": "book espresso coffeemarker tasting slot",
    "flightmarker": "renew flightmarker visa paperwork stack",
}


class CarryChat:
    """Fake serializer backend: emits a valid checkpoint whose open question is
    a fixed, marker-keyed text — a carried kind, so carry.merge folds it into
    later sessions' checkpoints."""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        blob = " ".join(str(m.get("content") or "") for m in messages)
        marker = next((w for w in blob.split() if w.endswith("marker")), "nomarker")
        return json.dumps({
            "session_id": "ignored",
            "working_context": {
                "active_topic": {"text": f"session about {marker}",
                                 "trust": "inferred"},
                "open_questions": [
                    {"text": _OQ.get(marker, f"open item on {marker}"),
                     "trust": "inferred"},
                ],
                "recent_decisions": [],
            },
            "epistemic_snapshot": {
                "strong_beliefs": [], "uncertainties": [],
                "contradictions_flagged": [],
            },
            "worker_queue": [],
        })


def _turns(marker):
    return [
        {"role": "user", "content": f"let us discuss {marker} today please"},
        {"role": "assistant", "content": f"sure, {marker} is interesting"},
        {"role": "user", "content": f"tell me more about {marker}"},
        {"role": "assistant", "content": f"here is more on {marker}"},
    ]


def _question(markers, qid="q_carry_1"):
    """Gold is always the FIRST listed session, so carry folds its open item
    into every later session's checkpoint."""
    sids = [f"sess_{m.removesuffix('marker')}" for m in markers]
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question": "goldmarker battery drain",
        "answer": "x",
        "haystack_session_ids": sids,
        "haystack_sessions": [_turns(m) for m in markers],
        "answer_session_ids": [sids[0]],
    }


class TestConfigStamp:
    def test_stamp_records_carry_on_and_off(self, tmp_path):
        ds = tmp_path / "dataset.json"
        ds.write_text("[]", encoding="utf-8")
        on = bench_run.build_parser().parse_args(["--carry"])
        off = bench_run.build_parser().parse_args([])
        assert bench_run._build_config_stamp(on, ds)["carry"] == "on"
        assert bench_run._build_config_stamp(off, ds)["carry"] == "off"


class TestCacheSeparation:
    def test_carry_modes_never_share_a_key(self):
        msgs = [{"role": "user", "content": "hello"}]
        base = dict(backend="b", model="m", prompt_version="v")
        assert cache_mod.cache_key(msgs, carry="on", **base) != \
            cache_mod.cache_key(msgs, carry="off", **base)

    def test_carry_off_key_is_backward_compatible(self):
        # Pre-#274 entries (keyed without a carry axis) must stay valid for
        # carry-off runs — the default and the explicit "off" are one key space.
        msgs = [{"role": "user", "content": "hello"}]
        base = dict(backend="b", model="m", prompt_version="v")
        assert cache_mod.cache_key(msgs, **base) == \
            cache_mod.cache_key(msgs, carry="off", **base)

    def test_carry_on_run_never_reads_carry_off_entries(self, tmp_path):
        cache = cache_mod.CheckpointCache(tmp_path / "c")
        chat = CarryChat()
        q = _question(["goldmarker", "coffeemarker"])
        adapter.run_question(q, chat=chat, cache=cache, backend="f", model="f",
                             root=tmp_path / "r1", k=5, depth=20, workers=1)
        assert chat.calls == 2
        # carry-on must MISS the carry-off entries and re-serialize...
        adapter.run_question(q, chat=chat, cache=cache, backend="f", model="f",
                             root=tmp_path / "r2", k=5, depth=20, workers=1,
                             carry_on=True)
        assert chat.calls == 4
        # ...then HIT its own lane on a re-run.
        adapter.run_question(q, chat=chat, cache=cache, backend="f", model="f",
                             root=tmp_path / "r3", k=5, depth=20, workers=1,
                             carry_on=True)
        assert chat.calls == 4


class TestEnvPinning:
    def test_carry_env_var_follows_the_mode(self, tmp_path):
        on = adapter._question_env(tmp_path, "q", "2", carry_on=True)
        off = adapter._question_env(tmp_path, "q", "2", carry_on=False)
        assert on["DAIMON_CARRY"] == "1"
        assert off["DAIMON_CARRY"] == "0"

    def test_carry_knobs_are_pinned_to_product_defaults(self, tmp_path):
        env = adapter._question_env(tmp_path, "q", "2", carry_on=True)
        # Determinism: an ambient host override must never change the fold.
        assert env["DAIMON_CARRY_FLOOR"] == "0.05"
        assert env["DAIMON_CARRY_MAX"] == "8"
        assert "DAIMON_CARRY_FLOOR" in adapter._ENV_KEYS
        assert "DAIMON_CARRY_MAX" in adapter._ENV_KEYS


class TestScoringRule:
    def test_carried_copy_credits_origin_not_host(self):
        rows = [
            {"session_id": "s_later", "text": "carried gold text"},
            {"session_id": "s_gold", "text": "carried gold text"},
            {"session_id": "s_later", "text": "native later text"},
        ]
        attribution = {
            ("s_later", "carried gold text"): "s_gold",
            ("s_gold", "carried gold text"): "s_gold",
            ("s_later", "native later text"): "s_later",
        }
        # gold counted ONCE, at the carried copy's (better) rank; the hosting
        # session keeps only its own native credit.
        assert metrics.attributed_sessions(rows, attribution) == \
            ["s_gold", "s_later"]

    def test_empty_attribution_matches_naive_ranking(self):
        rows = [
            {"session_id": "b", "text": "x"},
            {"session_id": "a", "text": "y"},
            {"session_id": "b", "text": "z"},
        ]
        assert metrics.attributed_sessions(rows, {}) == \
            metrics.ranked_sessions(rows)

    def test_end_to_end_no_double_count_of_the_gold_session(self, tmp_path):
        """The live double-counting hazard: the later session's checkpoint hosts
        a verbatim carried copy of the gold item, indexed under the LATER
        session's id — and it outranks gold's own row (frontier + recency
        tiebreaks). Naive scoring credits the wrong session; the attribution
        rule credits the origin, once."""
        q = _question(["goldmarker", "coffeemarker"])
        env = adapter._question_env(tmp_path / "runs", q["question_id"], "2",
                                    carry_on=True)
        with adapter._env(env):
            _tally, attribution = adapter.serialize_question(
                q, chat=CarryChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
                backend="f", model="f", project_dir=env["DAIMON_PROJECT_DIR"],
                workers=2, carry_on=True)
            results = adapter.recall_question(
                q, project_dir=env["DAIMON_PROJECT_DIR"], depth=20)
        # the fold really ran through the store: the later checkpoint on disk
        # hosts the gold item labeled with its origin
        later = json.loads(
            (Path(env["DAIMON_CHECKPOINT_DIR"]) / "sess_coffee.json")
            .read_text(encoding="utf-8"))
        carried = [i for i in later["working_context"]["open_questions"]
                   if i.get("carried_from")]
        assert [i["carried_from"] for i in carried] == ["sess_gold"]
        assert carried[0]["text"] == _OQ["goldmarker"]
        # naive scoring ranks the HOSTING session first (the hazard is real)
        naive = metrics.ranked_sessions(results)
        assert naive[0] == "sess_coffee"
        assert "sess_gold" in naive
        # honest scoring: the carried copy is attributable to gold — gold ranks
        # first, once, and the host earns nothing for gold's evidence
        honest = metrics.attributed_sessions(results, attribution)
        assert honest == ["sess_gold"]

    def test_run_question_scores_gold_at_rank_one_under_carry(self, tmp_path):
        result = adapter.run_question(
            _question(["goldmarker", "coffeemarker"]), chat=CarryChat(),
            cache=cache_mod.CheckpointCache(tmp_path / "c"),
            backend="f", model="f", root=tmp_path / "runs", k=5, depth=20,
            workers=2, carry_on=True)
        assert result["hit_at_5"] is True
        assert result["recall_at_5"] == 1.0
        assert result["mrr"] == 1.0  # not 0.5 — the carried copy is gold's rank
        assert result["n_retrieved_sessions"] == 1


class TestDeterminism:
    def test_workers_do_not_change_a_carry_on_result(self, tmp_path):
        """Carry folds prior checkpoints forward, so the fold is order-dependent
        — but it runs in the single-threaded, listed-session-order write loop,
        not in the concurrent serialize pool. Same result at any worker count."""
        q = _question(["goldmarker", "coffeemarker", "flightmarker"])
        r1 = adapter.run_question(
            q, chat=CarryChat(), cache=cache_mod.CheckpointCache(tmp_path / "c1"),
            backend="f", model="f", root=tmp_path / "w1", k=5, depth=20,
            workers=1, carry_on=True)
        r4 = adapter.run_question(
            q, chat=CarryChat(), cache=cache_mod.CheckpointCache(tmp_path / "c4"),
            backend="f", model="f", root=tmp_path / "w4", k=5, depth=20,
            workers=4, carry_on=True)
        assert r1 == r4

    def test_chained_carry_preserves_the_origin_session(self, tmp_path):
        """A -> B -> C: C's copy of A's item must still say carried_from=A
        (carry.merge stamps with setdefault), or attribution would credit B."""
        q = _question(["goldmarker", "coffeemarker", "flightmarker"])
        env = adapter._question_env(tmp_path / "runs", q["question_id"], "2",
                                    carry_on=True)
        with adapter._env(env):
            adapter.serialize_question(
                q, chat=CarryChat(), cache=cache_mod.CheckpointCache(tmp_path / "c"),
                backend="f", model="f", project_dir=env["DAIMON_PROJECT_DIR"],
                workers=4, carry_on=True)
        last = json.loads(
            (Path(env["DAIMON_CHECKPOINT_DIR"]) / "sess_flight.json")
            .read_text(encoding="utf-8"))
        origins = {i["carried_from"]: i["text"]
                   for i in last["working_context"]["open_questions"]
                   if i.get("carried_from")}
        assert set(origins) == {"sess_gold", "sess_coffee"}
        assert origins["sess_gold"] == _OQ["goldmarker"]
