"""#1377: the one builder of what a person signs, and the one size check.

Three parts. The builder (`authority.build_decision`) and the count
(`authority.rendered_lines`, `authority.signable_problem`) are pure. The
creation tests show the same refusal where a ruling or an ask first enters
daimon.

The count is a copy of the signer's request-block rule at handsal tag v0.4.2,
commit 09dc029c2d738c4346d05ea7bf2d9f57b338eb61 (the pinned `HANDSAL_PIN`).
`VECTORS` below reproduces the content of handsal's own render vectors under
`conformance/vectors/` at that commit, and the verdict each one pins.
"""
import base64
import json
import re

import pytest

from daimon_briefing import (amendments, authority, cli, refutations,
                             relations, requests, store, trust)
from daimon_briefing.surfaces import Writer

# handsal grammars at the pinned commit, spec/profile.md lines 86-88.
CONSUMER_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
SUBJECT_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
ACTION_RE = re.compile(r"[a-z]+( [a-z]+)*")


def _grammar(pattern, value, *, longest=None):
    return pattern.fullmatch(value) is not None and (
        longest is None or 1 <= len(value) <= longest)


# ---- the count against handsal's render vectors -----------------------------

_EXACT_2048 = "\n".join(["x" * 63] * 31 + ["y" * 64])

# (vector file under conformance/vectors/ at the pinned commit, the content,
# the lines handsal renders, whether handsal renders it or answers
# render_refused). Each content is the decoded `request_b64` or `decision_b64`
# of the vector.
VECTORS = [
    # rnd.request-exactly-2048-ok and rnd.decision-exactly-2048-ok: the same
    # 2048 bytes in 32 lines (31 of 63 and one of 64), rendered.
    ("rnd.request-exactly-2048-ok", _EXACT_2048, 32, True),
    ("rnd.decision-exactly-2048-ok", _EXACT_2048, 32, True),
    # rnd.request-over-2048-refused and rnd.decision-over-2048-refused: 2049
    # bytes on one line, refused for the byte limit alone (22 lines).
    ("rnd.request-over-2048-refused", "x" * 2049, 22, False),
    ("rnd.decision-over-2048-refused", "x" * 2049, 22, False),
    # rnd.request-33-lines-refused and rnd.decision-33-lines-refused: 33
    # source lines of one byte, refused for the line limit alone.
    ("rnd.request-33-lines-refused", "\n".join(["x"] * 33), 33, False),
    ("rnd.decision-33-lines-refused", "\n".join(["x"] * 33), 33, False),
    # rnd.request-32-lines-cap: despite its name it holds 40 lines of one
    # byte and is refused.
    ("rnd.request-32-lines-cap", "\n".join(["x"] * 40), 40, False),
    # rnd.request-long-line-continuation: one line of 300 bytes renders as
    # `| ` plus three `|+` lines (96, 96, 96, 12).
    ("rnd.request-long-line-continuation", "x" * 300, 4, True),
    # rnd.truncate-not-inside-escape: 95 bytes and one accented letter. The
    # first line stops at 95 because the four-character escape would pass 96;
    # the escape goes whole to the next line.
    ("rnd.truncate-not-inside-escape", "x" * 95 + "é", 2, True),
    # rnd.empty-request and rnd.decision-empty: zero bytes, no content line.
    ("rnd.empty-request", "", 0, True),
    ("rnd.decision-empty", "", 0, True),
]


@pytest.mark.parametrize("name,text,lines,rendered", VECTORS,
                         ids=[v[0] for v in VECTORS])
def test_the_count_agrees_with_handsals_render_vectors(
        name, text, lines, rendered):
    assert authority.rendered_lines(authority.content_bytes(text)) == lines
    assert (authority.signable_problem(text) is None) is rendered


# ---- the count ---------------------------------------------------------------

def test_2048_bytes_fit_and_2049_do_not():
    assert authority.signable_problem("x" * 2048) is None
    problem = authority.signable_problem("x" * 2049)
    assert "2049 bytes" in problem and "2048" in problem
    assert "lines" not in problem


def test_32_rendered_lines_fit_and_33_do_not():
    assert authority.signable_problem("\n".join(["x"] * 32)) is None
    problem = authority.signable_problem("\n".join(["x"] * 33))
    assert "33 lines" in problem and "32" in problem
    assert "bytes" not in problem


def test_a_line_of_97_escaped_bytes_counts_two():
    assert authority.rendered_lines(b"x" * 96) == 1
    assert authority.rendered_lines(b"x" * 97) == 2


def test_an_escape_is_never_split_across_lines():
    # 95 + 8 + 89 = 192 escaped bytes would be two lines if the escapes could
    # be cut; kept whole they leave a gap and take three.
    content = authority.content_bytes("x" * 95 + "é" + "x" * 89)
    assert authority.rendered_lines(content) == 3
    # twelve accented letters are exactly 96 escaped bytes, thirteen are not
    assert authority.rendered_lines(authority.content_bytes("é" * 12)) == 1
    assert authority.rendered_lines(authority.content_bytes("é" * 13)) == 2


def test_multi_byte_text_is_counted_in_bytes():
    assert authority.content_bytes("é") == b"\xc3\xa9"
    problem = authority.signable_problem("é" * 1025)
    assert "2050 bytes" in problem


def test_control_bytes_cost_four_each():
    # 768 bytes of 0x01 render in 32 lines, 769 in 33
    assert authority.signable_problem("\x01" * 768) is None
    assert "33 lines" in authority.signable_problem("\x01" * 769)


def test_an_empty_source_line_takes_a_line_and_a_final_lf_starts_none():
    assert authority.rendered_lines(b"") == 0
    assert authority.rendered_lines(b"\n") == 1
    assert authority.rendered_lines(b"a\n") == 1
    assert authority.rendered_lines(b"a\n\nb") == 3
    assert authority.rendered_lines(b"a\n\n") == 2


def test_both_limits_are_named_when_both_are_passed():
    problem = authority.signable_problem("é" * 1025)
    assert "bytes" in problem and "lines" in problem


def test_text_that_cannot_be_encoded_is_not_signable():
    problem = authority.signable_problem("bad \ud800 text")
    assert problem is not None and "UTF-8" in problem


def test_the_text_is_never_changed_for_counting():
    text = "  keep\tthe  spaces\r\n and the é  "
    assert authority.content_bytes(text) == text.encode("utf-8")


# ---- the builder -------------------------------------------------------------

RULING = {"refutation_id": "r-0123456789ab",
          "verdict": "never push to main", "subject": "main branch"}
REQUEST = {"request_id": "q-0123456789ab", "ask": "review the bar",
           "why": "blocks the note", "title": "bar"}


def test_the_consumer_is_the_literal_daimon():
    assert authority.CONSUMER == "daimon"
    assert _grammar(CONSUMER_RE, authority.CONSUMER)


def test_the_verb_list_is_closed_and_four_long():
    assert set(authority.VERBS) == {"ratify", "accept", "reject", "done"}


@pytest.mark.parametrize("verb", sorted(authority.VERBS))
def test_each_verb_fits_the_action_grammar(verb):
    assert _grammar(ACTION_RE, verb, longest=24)


@pytest.mark.parametrize("verb", ["forget", "seal", "needs info", "", "Ratify",
                                  "ratify "])
def test_a_verb_outside_the_list_is_refused(verb):
    with pytest.raises(authority.NotSignable, match="verb"):
        authority.build_decision(verb, REQUEST)


def test_a_ruling_signs_its_text():
    decision = authority.build_decision("ratify", RULING)
    assert decision == {
        "consumer": "daimon", "action": "ratify",
        "subject": "r-0123456789ab",
        "decision_b64": base64.b64encode(b"never push to main").decode(),
    }


@pytest.mark.parametrize("verb", ["accept", "reject", "done"])
def test_every_request_verdict_signs_the_ask(verb):
    decision = authority.build_decision(verb, REQUEST)
    assert decision["subject"] == "q-0123456789ab"
    assert base64.b64decode(decision["decision_b64"]) == b"review the bar"
    assert decision["action"] == verb


def test_text_typed_with_a_verdict_is_not_part_of_what_is_signed():
    row = dict(REQUEST, note="because the bar is wrong",
               reason="because the bar is wrong", evidence="a commit")
    decision = authority.build_decision("reject", row)
    assert decision == authority.build_decision("reject", REQUEST)
    assert b"because" not in base64.b64decode(decision["decision_b64"])
    # nor the why and the title of the request
    assert b"blocks" not in base64.b64decode(decision["decision_b64"])


def test_the_content_is_the_stored_string_untrimmed_and_unnormalised():
    text = " two  spacesé "
    decision = authority.build_decision(
        "accept", dict(REQUEST, ask=text))
    assert base64.b64decode(decision["decision_b64"]) == text.encode("utf-8")


def test_a_row_of_the_other_ledger_has_no_text_for_the_verb():
    with pytest.raises(authority.NotSignable, match="request_id"):
        authority.build_decision("accept", RULING)
    with pytest.raises(authority.NotSignable, match="refutation_id"):
        authority.build_decision("ratify", REQUEST)
    with pytest.raises(authority.NotSignable, match="no ask"):
        authority.build_decision("accept", {"request_id": "q-0123456789ab"})


def test_a_stored_row_over_the_bound_cannot_be_built_but_is_a_clear_error():
    row = dict(REQUEST, ask="x" * 2049)
    with pytest.raises(authority.NotSignable) as exc_info:
        authority.build_decision("accept", row)
    assert "2049 bytes" in str(exc_info.value)
    assert "ask" in str(exc_info.value)


def test_a_row_without_an_id_cannot_be_built():
    with pytest.raises(authority.NotSignable, match="id"):
        authority.build_decision("ratify", {"verdict": "x"})


def test_every_id_generator_produces_a_subject_that_fits_the_grammar():
    ids = [
        refutations.make_id("a subject", "a scope"),
        refutations.make_id("é subject ☃", "scope"),
        requests.make_id("-p-sender", "an ask", "a why", "2026-11-21T00:00:00Z"),
        requests.make_id("-p-sénder", "é", "☃", "t"),
        amendments.make_id("r-0123456789ab", "a change", "evidence"),
        trust.make_id("fact", "-p-scope", "a key"),
        trust.make_id("fact", "-p-scope", "é key"),
    ]
    ids.append(relations.make_id(
        "same-arc", {"session_id": "s1", "item_id": "i1"},
        {"session_id": "s2", "item_id": "i2"}))
    assert len(ids) == 8
    for value in ids:
        assert _grammar(SUBJECT_RE, value), value
        assert len(value) <= 64


# ---- where text enters -------------------------------------------------------

BIG_ASK = "x" * 2049
LINE_HEAVY_RULING = "é" * 100      # 100 chars, 800 escaped bytes, 9 lines
TOO_WIDE_RULING = "中" * 100        # 300 bytes, 1200 escaped, 13 lines
SLUG = "/p/signsize"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return SLUG


def _seed(project_dir, session="S-seed"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _open_request(project, ask, **kwargs):
    return requests.open_request(
        to=_seed("/p/signsize-recipient"), ask=ask, why="because",
        channel="cli-agent", project_dir=project, **kwargs)


def test_an_over_size_ask_is_refused_by_the_library_and_names_the_limits(
        project):
    # 2049 bytes pass the character cap (2000) only if they are multi-byte
    # free, so use text under 2000 characters that is over 2048 bytes
    text = "é" * 1500
    with pytest.raises(requests.RequestError) as exc_info:
        _open_request(project, text)
    message = str(exc_info.value)
    assert "ask" in message and "3000 bytes" in message and "2048" in message
    assert "lines" in message
    assert requests.listing(project) == []


def test_an_ask_over_the_line_limit_alone_is_refused_with_the_lines(project):
    # 33 lines of 96 plain bytes cannot be made from one collapsed line, so
    # use accented text: 400 letters are 800 bytes and 34 lines
    text = "é" * 400
    with pytest.raises(requests.RequestError) as exc_info:
        _open_request(project, text)
    message = str(exc_info.value)
    assert "34 lines" in message and "32" in message
    assert "800 bytes" not in message


def test_an_ask_at_the_limit_is_accepted(project):
    q_id = _open_request(project, "x" * 2000)
    assert requests.get(q_id, project_dir=project)["ask"] == "x" * 2000
    at_lines = "é" * 384       # 768 bytes, exactly 32 lines
    q_id = _open_request(project, at_lines)
    assert requests.get(q_id, project_dir=project)["ask"] == at_lines


def test_a_revised_ask_is_held_to_the_same_bound(project):
    q_id = _open_request(project, "a short ask")
    with pytest.raises(requests.RequestError) as exc_info:
        requests.revise(q_id, channel="cli-agent", ask="é" * 400,
                        project_dir=project)
    assert "34 lines" in str(exc_info.value)
    assert requests.get(q_id, project_dir=project)["ask"] == "a short ask"


def test_the_character_limit_still_governs_the_other_fields(project):
    with pytest.raises(requests.RequestTooLong):
        _open_request(project, "ok", evidence="x" * 2001)
    # the why is not signed, so a why over 2048 bytes but under 2000
    # characters is still accepted
    q_id = requests.open_request(
        to=_seed("/p/signsize-recipient"), ask="ok", why="é" * 1500,
        channel="cli-agent", project_dir=project)
    assert requests.get(q_id, project_dir=project)["why"] == "é" * 1500


def _ruling(project, verdict):
    return refutations.assert_ruling(
        subject="a subject", verdict=verdict, scope="repo",
        evidence=["issue:1"], channel="cli-agent", project_dir=project)


def test_an_over_size_ruling_is_refused_by_the_library(project):
    # the ruling cap is 280 characters, so only wide text reaches the bound
    with pytest.raises(refutations.RefutationError) as exc_info:
        _ruling(project, TOO_WIDE_RULING * 2 + "中" * 80)
    message = str(exc_info.value)
    assert "verdict" in message and "lines" in message and "32" in message
    assert refutations.listing(polarity="ruling", project_dir=project) == []


def test_a_ruling_at_the_limit_is_accepted(project):
    verdict = "é" * 270      # 2160 escaped bytes: 23 lines
    ruling_id = _ruling(project, verdict)
    assert refutations.get(ruling_id, project_dir=project)["verdict"] == verdict


def test_a_revised_ruling_is_held_to_the_same_bound(project):
    ruling_id = _ruling(project, "a short rule")
    wide = "中" * 280          # 840 bytes, 3360 escaped bytes: 35 lines
    with pytest.raises(refutations.RefutationError) as exc_info:
        refutations.revise(ruling_id, verdict=wide, channel="cli-agent",
                           evidence=["issue:1"], project_dir=project)
    assert "35 lines" in str(exc_info.value)


def test_the_cli_refuses_an_over_size_ask_with_the_library_wording(
        project, capsys):
    slug = _seed("/p/signsize-recipient")
    text = "é" * 400
    rc = cli.main(["request", "open", f"--to={slug}", "--ask", text, "--why",
                   "w", "--by", "agent", "--project", project])
    out = capsys.readouterr().out
    assert rc == 1
    assert "request not recorded" in out
    with pytest.raises(requests.RequestError) as exc_info:
        _open_request(project, text)
    assert str(exc_info.value) in out


def test_the_cli_refuses_an_over_size_ruling_with_the_library_wording(
        project, capsys):
    wide = "中" * 280
    rc = cli.main(["ruling", "propose", "--subject", "a subject", "--verdict",
                   wide, "--scope", "repo", "--evidence", "issue:1", "--by",
                   "agent", "--project", project])
    out = capsys.readouterr().out
    assert rc == 1
    assert "35 lines" in out and "verdict" in out
    with pytest.raises(refutations.RefutationError) as exc_info:
        _ruling(project, wide)
    assert str(exc_info.value) in out


def test_the_ask_and_the_ruling_are_refused_in_the_same_words(project):
    with pytest.raises(requests.RequestError) as ask_error:
        _open_request(project, "é" * 400)
    with pytest.raises(refutations.RefutationError) as ruling_error:
        _ruling(project, "中" * 280)
    ask_tail = str(ask_error.value).split(": ", 1)[1]
    ruling_tail = str(ruling_error.value).split(": ", 1)[1]
    def shape(tail):
        return re.sub(r"\d+", "N", re.sub(r"^(ask|verdict)", "FIELD", tail))

    assert shape(ask_tail) == shape(ruling_tail)


def test_a_stored_row_over_the_bound_stays_readable_and_listed(project):
    q_id = _open_request(project, "a short ask")
    # a row written before the bound existed: appended behind the boundary
    row = requests._stamp("revised", q_id, "cli-agent")
    row["ask"] = "x" * 2049
    assert requests.append(row, project_dir=project, writer=Writer.HUMAN)
    record = requests.get(q_id, project_dir=project)
    assert record["ask"] == "x" * 2049
    assert [r["request_id"] for r in requests.listing(project)] == [q_id]
    with pytest.raises(authority.NotSignable, match="2049 bytes"):
        authority.build_decision("accept", record)
    json.dumps(record)


def test_the_four_id_generators_are_still_the_only_subjects():
    # a guard for the grammar test above: a new generator must be added to
    # it. Every `def make_id` in the package is one of these.
    import ast
    from pathlib import Path

    import daimon_briefing
    found = set()
    for path in Path(daimon_briefing.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found |= {path.name for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "make_id"}
    assert found == {"refutations.py", "requests.py", "amendments.py",
                     "trust.py", "relations.py"}
