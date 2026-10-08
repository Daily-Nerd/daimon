"""#1129 PR C: the hand-copied display cuts now delegate to `display.shorten`.

Each helper is pinned two ways: single-line input comes out byte-identical to
the legacy expression (the migration changes nothing visible), and multi-line
input now lands on one line (the defect the migration exists to fix)."""
import pytest

from daimon_briefing import briefing, cli, pending, render, requests


def _legacy_hard(text, limit):
    text = text.strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


SINGLE_LINE = [
    "short",
    "x" * 120,
    "x" * 121,
    "word " * 60,
    ("a" * 119) + " tail that is long enough to be cut" * 5,
    "y" * 400,
]


@pytest.mark.parametrize("text", SINGLE_LINE)
def test_agent_claim_single_line_bytes_unchanged(text):
    assert briefing._truncate_agent_claim(text) == _legacy_hard(
        text, briefing._AGENT_CLAIM_EVIDENCE_CHARS)


def test_agent_claim_none_is_empty_and_multiline_is_one_line():
    assert briefing._truncate_agent_claim(None) == ""
    out = briefing._truncate_agent_claim("first line\n\nsecond   line\tthird")
    assert out == "first line second line third"
    long_out = briefing._truncate_agent_claim("alpha\nbeta " * 40)
    assert "\n" not in long_out and len(long_out) <= (
        briefing._AGENT_CLAIM_EVIDENCE_CHARS + 1)


def _loop_with(monkeypatch, text):
    monkeypatch.setattr(pending.recall, "find",
                        lambda item_id, project_dir=None, slug=None:
                        pending.recall.Found({"text": text}))
    return pending._loop_text("item-1", "slug-x")


def _legacy_loop(text):
    text = text.strip()
    cap = pending._LOOP_TEXT_CAP
    return text if len(text) <= cap else text[:cap - 1].rstrip() + "…"


@pytest.mark.parametrize("text", SINGLE_LINE + [
    "z" * pending._LOOP_TEXT_CAP,
    "z" * (pending._LOOP_TEXT_CAP + 1),
    ("w" * 118) + " " + "tail" * 10,
])
def test_loop_text_single_line_bytes_unchanged(monkeypatch, text):
    assert _loop_with(monkeypatch, text) == _legacy_loop(text)


def test_loop_text_multiline_is_one_line(monkeypatch):
    out = _loop_with(monkeypatch, "line one\nline two\n\n" + "q" * 200)
    assert "\n" not in out and len(out) <= pending._LOOP_TEXT_CAP
    assert _loop_with(monkeypatch, "a\nb") == "a b"


def _reply(note):
    return requests.latest_reply_line(
        {"replies": [{"note": note, "authority": "human"}], "revision": 0})


@pytest.mark.parametrize("text", SINGLE_LINE + ["r" * 160, "r" * 161])
def test_reply_line_single_line_bytes_unchanged(text):
    assert _reply(text) == "Reply: " + _legacy_hard(
        text, requests._REPLY_LINE_MAX)


def test_reply_line_multiline_is_one_line():
    assert _reply("ok\nthen\n\ndone") == "Reply: ok then done"
    out = _reply("para one\n\npara two " * 30)
    assert "\n" not in out


def _legacy_cause(text):
    return text if len(text) <= 160 else text[:160].rstrip() + "…"


@pytest.mark.parametrize("body", ["boom", "e" * 160, "e" * 161,
                                  "word " * 80])
def test_failure_cause_single_line_bytes_unchanged(body):
    out = render._failure_cause("error: " + body)
    assert out == _legacy_cause(" ".join(body.split()))


def test_failure_cause_multiline_stays_one_line():
    out = render._failure_cause("error: a\nb\n" + "c" * 300)
    assert "\n" not in out


def _legacy_teaser(topic):
    topic = topic.strip()
    return topic if len(topic) <= 60 else topic[:59] + "…"


@pytest.mark.parametrize("topic", [
    "", "short", "t" * 60, "t" * 61, "word " * 30,
    ("t" * 58) + " " + "u" * 20,   # space at the cut: legacy keeps it
])
def test_topic_teaser_single_line_bytes_unchanged(topic):
    assert cli._topic_teaser(topic) == _legacy_teaser(topic)


def test_topic_teaser_multiline_is_one_line():
    assert cli._topic_teaser("fix\nthe thing") == "fix the thing"
    assert "\n" not in cli._topic_teaser("a\n" * 100)
