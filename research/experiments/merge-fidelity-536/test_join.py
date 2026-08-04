"""Tests for the #536 join half — log attribution, key reconstruction,
predecessor selection. Pure-function pieces only; disk walking stays thin
and untested here (the run itself exercises it)."""
import hashlib

import join


SPAWN = ("2026-08-04T20:40:16Z session-end: spawned serialize for aaaa1111-2222-3333-4444-555566667777 "
         "(reason: prompt_input_exit, project: /Users/x/proj) "
         "(transcript: /Users/x/.claude/projects/-x-proj/aaaa1111-2222-3333-4444-555566667777.jsonl)")
CHUNKED = ("2026-08-04T20:40:17Z INFO daimon_briefing.serializer: "
           "chunked serialize: 2 chunks from 1256 lines")
WROTE = "wrote checkpoint: /Users/kibukx/.daimon/checkpoints/aaaa1111-2222-3333-4444-555566667777.json (took 237s)"
RETRY = ("2026-08-04T20:43:04Z session-start: retry serialize for aaaa1111-2222-3333-4444-555566667777 "
         "(prior: error: unparseable model output on chunk 1 of 2)")


class TestLogAttribution:
    def test_single_spawn_single_write_passes(self):
        runs = join.attribute_log([SPAWN, CHUNKED, WROTE])
        r = runs["aaaa1111-2222-3333-4444-555566667777"]
        assert r.spawns == 1 and r.writes == 1
        assert r.chunk_counts == [2]
        assert r.transcript_path == "/Users/x/.claude/projects/-x-proj/aaaa1111-2222-3333-4444-555566667777.jsonl"
        assert r.eligible()

    def test_retry_spawn_disqualifies(self):
        runs = join.attribute_log([SPAWN, CHUNKED, RETRY, CHUNKED, WROTE])
        r = runs["aaaa1111-2222-3333-4444-555566667777"]
        assert r.spawns == 2
        assert not r.eligible()

    def test_chunk_line_outside_spawn_write_window_ignored(self):
        # chunk line after the write belongs to some other run
        runs = join.attribute_log([SPAWN, WROTE, CHUNKED])
        assert runs["aaaa1111-2222-3333-4444-555566667777"].chunk_counts == []

    def test_two_chunk_lines_in_window_ambiguous(self):
        runs = join.attribute_log([SPAWN, CHUNKED, CHUNKED, WROTE])
        r = runs["aaaa1111-2222-3333-4444-555566667777"]
        assert not r.eligible()

    def test_single_pass_run_has_no_chunk_line(self):
        single = ("2026-08-04T20:40:17Z INFO daimon_briefing.serializer: "
                  "single-pass serialize: 300 lines")
        runs = join.attribute_log([SPAWN, single, WROTE])
        r = runs["aaaa1111-2222-3333-4444-555566667777"]
        assert r.chunk_counts == []
        assert not r.eligible()  # no chunked run -> nothing to measure

    def test_other_host_spawn_prefixes_count(self):
        codex = ("2026-08-04T10:00:00Z codex-stop: spawned serialize for bbbb1111-2222-3333-4444-555566667777 "
                 "(reason: stop, project: /Users/x/p2) "
                 "(transcript: /t/bbbb1111-2222-3333-4444-555566667777.jsonl)")
        wrote_b = "wrote checkpoint: /Users/kibukx/.daimon/checkpoints/bbbb1111-2222-3333-4444-555566667777.json (took 10s)"
        runs = join.attribute_log([codex, CHUNKED.replace("2 chunks", "3 chunks"), wrote_b])
        assert runs["bbbb1111-2222-3333-4444-555566667777"].spawns == 1
        assert runs["bbbb1111-2222-3333-4444-555566667777"].chunk_counts == [3]


class TestLogHygiene:
    SID_A = "aaaa1111-2222-3333-4444-555566667777"
    SID_B = "bbbb1111-2222-3333-4444-555566667777"

    def test_old_wrote_line_without_took_suffix_closes_window(self):
        old_wrote = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                     f"{self.SID_A}.json")
        runs = join.attribute_log([SPAWN, CHUNKED, old_wrote])
        r = runs[self.SID_A]
        assert r.writes == 1
        assert r.eligible()

    def test_error_line_closes_window_via_transcript_stem(self):
        err = (f"error: backend timeout (transcript: /t/{self.SID_A}.jsonl) "
               "after 420s")
        spawn_b = SPAWN.replace(self.SID_A, self.SID_B)
        chunk3 = CHUNKED.replace("2 chunks", "3 chunks")
        wrote_b = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                   f"{self.SID_B}.json (took 10s)")
        # A spawns, errors out; B then runs alone — B's chunk line must
        # attribute cleanly, not be poisoned by A's dead window
        runs = join.attribute_log([SPAWN, err, spawn_b, chunk3, wrote_b])
        assert runs[self.SID_B].chunk_counts == [3]
        assert runs[self.SID_B].eligible()

    def test_zombie_window_reaped_after_full_later_cycle(self):
        # A spawns and dies silently (no write, no error). B runs a full
        # spawn->write cycle. C's chunk line after that must attribute to
        # C alone: A's zombie window is reaped by B's completed cycle.
        sid_c = "cccc1111-2222-3333-4444-555566667777"
        spawn_b = SPAWN.replace(self.SID_A, self.SID_B)
        wrote_b = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                   f"{self.SID_B}.json (took 5s)")
        spawn_c = SPAWN.replace(self.SID_A, sid_c)
        chunk4 = CHUNKED.replace("2 chunks", "4 chunks")
        wrote_c = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                   f"{sid_c}.json (took 5s)")
        runs = join.attribute_log(
            [SPAWN, spawn_b, wrote_b, spawn_c, chunk4, wrote_c])
        assert runs[sid_c].chunk_counts == [4]
        assert runs[sid_c].eligible()

    def test_true_overlap_still_poisons(self):
        # A and B both open when the chunk line appears: no guess
        spawn_b = SPAWN.replace(self.SID_A, self.SID_B)
        wrote_a = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                   f"{self.SID_A}.json (took 5s)")
        wrote_b = ("wrote checkpoint: /Users/kibukx/.daimon/checkpoints/"
                   f"{self.SID_B}.json (took 5s)")
        runs = join.attribute_log([SPAWN, spawn_b, CHUNKED, wrote_a, wrote_b])
        assert not runs[self.SID_A].eligible()
        assert not runs[self.SID_B].eligible()


class TestKeyReconstruction:
    def test_stamp_uses_checkpoint_fields_not_live_config(self):
        key = join.cache_key_for(
            chunk_text="hello world",
            backend="litellm", model="claude-haiku-4-5", temperature=0.0,
            extraction_version=2, scene=True, lane="default")
        stamp = ("v2\x00litellm\x00claude-haiku-4-5\x000.0\x002"
                 "\x00scene\x00default\x00")
        expected = hashlib.sha256(
            stamp.encode("utf-8") + b"hello world").hexdigest()[:32]
        assert key == expected

    def test_missing_extraction_version_defaults_to_2(self):
        assert join.checkpoint_extraction_version({}) == 2
        assert join.checkpoint_extraction_version({"extraction_version": 3}) == 3


class TestServedModelRule:
    def test_both_null_matches(self):
        assert join.served_model_ok(None, None)

    def test_named_must_equal(self):
        assert join.served_model_ok("m1", "m1")
        assert not join.served_model_ok("m1", "m2")

    def test_null_envelope_into_stamped_checkpoint_fails(self):
        assert not join.served_model_ok(None, "m1")
        assert not join.served_model_ok("m1", None)


class TestPredecessor:
    def test_prev_selected_by_carried_from(self):
        cp = {"session_id": "B",
              "working_context": {"recent_decisions": [
                  {"text": "x", "carried_from": "A"}]},
              "epistemic_snapshot": {}}
        assert join.predecessor_id(cp) == "A"

    def test_no_carried_items_means_no_predecessor_required(self):
        cp = {"session_id": "B",
              "working_context": {"recent_decisions": [{"text": "x"}]},
              "epistemic_snapshot": {}}
        assert join.predecessor_id(cp) is None

    def test_mixed_hops_takes_majority(self):
        cp = {"session_id": "C",
              "working_context": {"recent_decisions": [
                  {"text": "1", "carried_from": "B"},
                  {"text": "2", "carried_from": "B"},
                  {"text": "3", "carried_from": "A"}]},
              "epistemic_snapshot": {}}
        assert join.predecessor_id(cp) == "B"


class TestItemPools:
    def test_native_pool_excludes_carried(self):
        cp = {"working_context": {
                  "open_questions": [{"text": "q1", "trust": "verbatim"}],
                  "recent_decisions": [{"text": "d1", "carried_from": "A"}]},
              "epistemic_snapshot": {
                  "strong_beliefs": [{"text": "b1", "trust": "inferred"}]}}
        pool = join.native_items(cp)
        assert sorted(i.text for i in pool) == ["b1", "q1"]

    def test_union_pool_tags_chunk_index(self):
        partials = [
            {"working_context": {"open_questions": [{"text": "u1"}]},
             "epistemic_snapshot": {}},
            {"working_context": {},
             "epistemic_snapshot": {"uncertainties": [{"text": "u2",
                                                       "trust": "verbatim"}]}},
        ]
        pool = join.union_items(partials)
        assert {(i.text, i.chunk) for i in pool} == {("u1", 0), ("u2", 1)}

    def test_prev_verbatim_pool_only_verbatim(self):
        prev = {"working_context": {
                    "recent_decisions": [
                        {"text": "keep", "trust": "verbatim"},
                        {"text": "drop", "trust": "inferred"}]},
                "epistemic_snapshot": {}}
        assert join.prev_verbatim_pool(prev) == ["keep"]
