"""Unit tests for the LongMemEval dataset adapter (#267). No network."""

import hashlib
import json

import pytest

from tests.bench import dataset


def _question(qid="q1", gold=("s2",), abstention=False):
    """A minimal LongMemEval-S question with two haystack sessions."""
    return {
        "question_id": qid + ("_abs" if abstention else ""),
        "question_type": "single-session-user",
        "question": "What laptop did I buy?",
        "answer": "a ThinkPad",
        "question_date": "2023/05/20",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_dates": ["2023/05/01", "2023/05/10"],
        "haystack_sessions": [
            [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}],
            [{"role": "user", "content": "I bought a ThinkPad", "has_answer": True},
             {"role": "assistant", "content": "nice"}],
        ],
        "answer_session_ids": [] if abstention else list(gold),
    }


def _write_dataset(path, questions):
    path.write_text(json.dumps(questions), encoding="utf-8")


class TestLoad:
    def test_load_returns_list_of_questions(self, tmp_path):
        p = tmp_path / "ds.json"
        _write_dataset(p, [_question("q1"), _question("q2")])
        qs = dataset.load(p)
        assert [q["question_id"] for q in qs] == ["q1", "q2"]


class TestSample:
    def test_sample_is_deterministic_for_a_seed(self, tmp_path):
        qs = [_question(f"q{i}") for i in range(20)]
        a = dataset.sample(qs, 5, seed=42)
        b = dataset.sample(qs, 5, seed=42)
        assert [q["question_id"] for q in a] == [q["question_id"] for q in b]
        assert len(a) == 5

    def test_different_seeds_differ(self, tmp_path):
        qs = [_question(f"q{i}") for i in range(50)]
        a = dataset.sample(qs, 10, seed=1)
        b = dataset.sample(qs, 10, seed=2)
        assert [q["question_id"] for q in a] != [q["question_id"] for q in b]

    def test_sample_larger_than_pool_returns_all(self):
        qs = [_question("q1"), _question("q2")]
        assert len(dataset.sample(qs, 10, seed=0)) == 2

    def test_zero_or_negative_sample_returns_all(self):
        qs = [_question(f"q{i}") for i in range(5)]
        assert len(dataset.sample(qs, 0, seed=0)) == 5


class TestAccessors:
    def test_gold_sessions(self):
        assert dataset.gold_sessions(_question(gold=("s2", "s5"))) == {"s2", "s5"}

    def test_is_abstention_by_empty_gold(self):
        assert dataset.is_abstention(_question(abstention=True)) is True
        assert dataset.is_abstention(_question()) is False

    def test_is_abstention_by_id_suffix(self):
        q = _question()
        q["question_id"] = "abc_abs"
        q["answer_session_ids"] = ["s2"]  # id suffix still marks abstention
        assert dataset.is_abstention(q) is True

    def test_sessions_of_pairs_ids_with_messages(self):
        sessions = dataset.sessions_of(_question())
        assert [sid for sid, _ in sessions] == ["s1", "s2"]
        sid, msgs = sessions[1]
        # has_answer and any non-role/content keys are stripped to OpenAI shape
        assert msgs == [
            {"role": "user", "content": "I bought a ThinkPad"},
            {"role": "assistant", "content": "nice"},
        ]

    def test_sessions_of_tolerates_length_mismatch(self):
        q = _question()
        q["haystack_session_ids"] = ["s1"]  # fewer ids than sessions
        sessions = dataset.sessions_of(q)
        # only the pairs that line up are returned; no crash
        assert [sid for sid, _ in sessions] == ["s1"]


class TestChecksum:
    def test_verify_matching_digest_passes(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_bytes(b"payload")
        digest = hashlib.sha256(b"payload").hexdigest()
        assert dataset.verify_sha256(p, digest) is True

    def test_verify_mismatch_raises(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_bytes(b"payload")
        with pytest.raises(dataset.ChecksumError):
            dataset.verify_sha256(p, "0" * 64)

    def test_verify_none_expected_returns_false_without_raising(self, tmp_path):
        p = tmp_path / "f.json"
        p.write_bytes(b"payload")
        # no pinned checksum yet (trust-on-first-use): reports unverified, no raise
        assert dataset.verify_sha256(p, None) is False
