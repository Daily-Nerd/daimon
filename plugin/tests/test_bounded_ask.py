"""#1127: an ask is up to 2000 chars on one line; every surface that shows it
as a title or a skimmable line bounds it through `requests.short_ask`, and the
full text stays reachable (`detail` on the decide row, the inbox for hooks)."""

import pytest

from daimon_briefing import briefing, cli, pending, requests, store
from daimon_briefing.cli import request as cli_request

LIMIT = 160

# Shaped like a real ask: one line, ~1,000 chars, a long first sentence.
LONG_FIRST = (
    "Please publish the updated checkpoint schema to the shared docs site "
    "and confirm that the migration notes for the bucket split are linked "
    "from the changelog entry for the release that introduced the new "
    "request lifecycle states and their folded record shape. ")
REALISTIC_ASK = (LONG_FIRST + (
    "The client integration reads the schema directly from that page, so a "
    "stale copy breaks their validation step. Include the field table for "
    "the request records, the list of channels that may write each verb, "
    "and the note about which verbs are human only. If anything in the "
    "table disagrees with the code, the code wins and the table should be "
    "regenerated rather than patched by hand. Reply on this thread when "
    "the page is live so the sender can unblock the downstream work. ") * 2
).strip()
SHORT_FIRST_ASK = (
    "Publish the schema now. " + REALISTIC_ASK.replace(LONG_FIRST, ""))
NO_SPACE_ASK = "x" * 900
SHORT_ASK = "publish the schema"


def _ends_cleanly(short, full):
    return short.endswith(("…", ".", "?", "!")) and (
        short.endswith(("." , "?", "!")) or full[len(short) - 1:].startswith(
            (" ", "…")) or True)


def test_fixture_is_realistic():
    assert 900 <= len(REALISTIC_ASK) <= requests._MAX_TEXT
    assert "\n" not in REALISTIC_ASK
    assert len(REALISTIC_ASK.split(". ")[0]) > LIMIT


def test_under_limit_is_unchanged():
    assert requests.short_ask(SHORT_ASK) == SHORT_ASK


def test_whitespace_is_collapsed():
    assert requests.short_ask("a  b\n\tc ") == "a b c"


def test_short_first_sentence_wins():
    out = requests.short_ask(SHORT_FIRST_ASK)
    assert out == "Publish the schema now."


def test_long_first_sentence_cuts_on_a_word_boundary():
    out = requests.short_ask(REALISTIC_ASK)
    assert len(out) <= LIMIT
    assert out.endswith("…")
    body = out[:-1]
    assert REALISTIC_ASK.startswith(body)
    assert REALISTIC_ASK[len(body)] == " "


def test_no_whitespace_hard_cuts():
    out = requests.short_ask(NO_SPACE_ASK)
    assert len(out) == LIMIT
    assert out == "x" * 159 + "…"


def test_tiny_first_sentence_is_not_used():
    text = "Hi. " + "word " * 80
    out = requests.short_ask(text)
    assert out.endswith("…") and len(out) <= LIMIT


def test_briefing_truncation_uses_the_shared_helper():
    assert briefing._truncate_request_ask(REALISTIC_ASK) == (
        requests.short_ask(REALISTIC_ASK))
    assert briefing._REQUEST_ASK_CHARS == requests.ASK_CHARS == LIMIT


@pytest.fixture
def project(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setenv("DAIMON_PROJECT_DIR", "/p/A")
    return "/p/A"


def _open(project, ask):
    return requests.open_request(
        to=store.project_slug(project), ask=ask, why="the client needs it",
        channel="cli-agent", project_dir=project)


def test_pending_row_bounds_headline_and_keeps_detail(project):
    _open(project, REALISTIC_ASK)
    row = pending.queue(project_dir=project)["rows"][0]
    assert len(row["headline"]) <= LIMIT
    assert row["detail"] == REALISTIC_ASK


def test_pending_row_detail_is_none_for_a_short_ask(project):
    _open(project, SHORT_ASK)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["headline"] == SHORT_ASK
    assert row["detail"] is None


def test_non_request_rows_carry_detail_none(project):
    from daimon_briefing import refutations
    refutations.assert_ruling(
        subject="release", verdict="never bump without a human",
        scope="repo", evidence=["issue:766"], channel="cli-agent",
        project_dir=project)
    row = pending.queue(project_dir=project)["rows"][0]
    assert row["detail"] is None


def test_decide_card_shows_the_full_ask_wrapped(project, capsys):
    q_id = _open(project, REALISTIC_ASK)
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    header = next(ln for ln in out.splitlines() if q_id in ln)
    assert len(header) < LIMIT + 80
    flat = " ".join(out.split())
    assert " ".join(REALISTIC_ASK.split()) in flat
    assert max(len(ln) for ln in out.splitlines()
               if ln.startswith("  ") and "daimon" not in ln) <= 80


def test_decide_card_without_detail_has_no_extra_body(project, capsys):
    _open(project, SHORT_ASK)
    assert cli.main(["decide"]) == 0
    out = capsys.readouterr().out
    assert out.count(SHORT_ASK) == 1


def test_inject_lines_bound_the_ask_and_point_at_the_inbox(project):
    q_id = _open(project, REALISTIC_ASK)
    record = requests.get(q_id, project_dir=project)
    text = "\n".join(cli_request._inject_lines(record))
    assert REALISTIC_ASK not in text
    assert requests.short_ask(REALISTIC_ASK) in text
    assert "daimon request inbox" in text


def test_inject_lines_short_ask_has_no_hint(project):
    q_id = _open(project, SHORT_ASK)
    record = requests.get(q_id, project_dir=project)
    text = "\n".join(cli_request._inject_lines(record))
    assert SHORT_ASK in text
    assert "daimon request inbox" not in text


def test_verdict_and_owed_inject_lines_are_bounded(project):
    q_id = _open(project, REALISTIC_ASK)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    for fn in (cli_request._verdict_inject_lines,
               cli_request._owed_inject_lines):
        text = "\n".join(fn(record))
        assert REALISTIC_ASK not in text
        assert "daimon request inbox" in text


@pytest.mark.parametrize("abbr", ["e.g.", "i.e."])
def test_abbreviation_is_not_a_sentence_end(abbr):
    text = (f"Bump the pinned daimon, {abbr} the version in the lockfile or "
            "later, and then loosen the range in every downstream project "
            "so the next release does not need a manual edit of each one, "
            "which is what keeps going wrong today. ") * 3
    out = requests.short_ask(text)
    assert len(out) <= LIMIT
    assert not out.endswith(abbr)
    assert out.endswith("…")
