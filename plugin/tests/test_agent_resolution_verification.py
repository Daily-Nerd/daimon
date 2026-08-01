"""#480 slice 3: serialize-time verification of pending agent resolve
candidates.

Slice 2 (#482) gave `daimon resolve --by agent --evidence "<quote>"` a write
path: a `resolving-candidate` event, `source="agent"`, that never withholds
anything (the #14 machine-suggestion safety property). This slice closes the
loop: at serialize time, every pending candidate for the project being
serialized gets its evidence quote byte-checked against THAT session's
transcript — reusing `verify_quotes`'s own normalization/matching (#125) —
and a hit appends a confirming event that DOES withhold, credited to
source="serializer".

Three properties matter most here, each pinned by its own test:
  - the candidate never gets touched on a miss (unverified stays live, #14);
  - a pass here can never fail the serialize itself (best-effort, wrapped);
  - the pass is scoped to ONE project's events against ONE project's
    transcript — a candidate from project A must never be confirmed by
    project B's session.
"""

import json

from daimon_briefing import capture, cli, store
from tests.conftest import make_messages


def _new_session_json(session_id="S-new", quote=""):
    """A minimal, schema-valid checkpoint the fake chat returns for a NEW
    session's own extraction — independent of the pending candidate this
    module is testing. Optionally carries `quote` as its own verbatim item
    too, so a single fixture can double as evidence that verify_quotes
    (#125) and verify_agent_evidence (#480 slice 3) agree on the same
    transcript."""
    decisions = []
    if quote:
        decisions.append({"text": "recorded via this session's own extraction",
                          "trust": "verbatim", "quote": quote})
    return json.dumps({
        "session_id": session_id,
        "working_context": {
            "active_topic": {"text": "this session's own topic", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": decisions,
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    })


def _agent_candidate(project, sample_checkpoint, quote,
                     prev_session="S-prev"):
    """Write a prior checkpoint, then `resolve --by agent --evidence` its
    first open question — the standard setup every test below shares.
    Returns the target item dict (with its stable id)."""
    store.write_checkpoint(prev_session, sample_checkpoint, project_dir=project)
    written = store.read_latest(project_dir=project)
    item = written["working_context"]["open_questions"][0]
    rc = cli.main(["resolve", item["id"], "--by", "agent",
                   "--evidence", quote, "--project", project])
    assert rc == 0
    return item


QUOTE = "the user merged PR #6 from the GitHub UI"


def test_pending_candidate_confirmed_when_transcript_has_the_quote(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory, capsys):
    project = "/repo/slice3-hit"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)
    capsys.readouterr()  # discard the "claim recorded" resolve output above

    msgs = make_messages(10)
    msgs[3] = {"role": "assistant", "content": QUOTE}
    chat = fake_chat_factory(_new_session_json())
    out_path = capture.run("S-new", msgs, project=project, chat=chat, deadline=None)
    assert out_path is not None

    evt = store.resolutions(project_dir=project)[item["id"]]
    assert evt["status"] == "resolved-agent-verified"
    assert evt["source"] == "serializer"
    assert store.is_resolved(evt) is True

    rc = cli.main(["brief", "--project", project])
    assert rc == 0
    brief_out = capsys.readouterr().out
    assert item["text"] not in brief_out  # withheld now

    rc = cli.main(["loops", "--project", project])
    assert rc == 0
    loops_out = capsys.readouterr().out
    assert item["id"] not in loops_out  # absent from the open-loops listing


def test_pending_candidate_stands_when_quote_absent_from_transcript(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory, capsys):
    project = "/repo/slice3-miss"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)
    capsys.readouterr()  # discard the "claim recorded" resolve output above

    msgs = make_messages(10)  # none of these lines carry the evidence quote
    chat = fake_chat_factory(_new_session_json())
    capture.run("S-new", msgs, project=project, chat=chat, deadline=None)

    evt = store.resolutions(project_dir=project)[item["id"]]
    assert evt["status"] == "resolving-candidate"  # untouched
    assert evt["source"] == "agent"
    assert store.is_resolved(evt) is False

    rc = cli.main(["brief", "--project", project])
    assert rc == 0
    brief_out = capsys.readouterr().out
    assert item["text"] in brief_out  # still visible, unverified

    rc = cli.main(["loops", "--project", project])
    assert rc == 0
    loops_out = capsys.readouterr().out
    assert item["id"] in loops_out


def test_echoed_evidence_via_daimon_injection_never_confirms_end_to_end(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory, capsys):
    """The self-reference law, end to end: a still-pending candidate's
    evidence quote will be RENDERED by daimon itself into future sessions
    (recall lines today, slice 4's unverified-claim line next) — so a
    session whose transcript contains the quote ONLY inside daimon-injected
    spans must not confirm it, or a fabricated quote self-confirms one
    session later. The unit test covers verify_agent_evidence building its
    own haystack; THIS test pins the caller — _verify_agent_resolutions
    precomputes the haystack, and passing the unstripped render there would
    slip past every unit test while reopening the #440 echo hole."""
    project = "/repo/slice3-echo"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)
    capsys.readouterr()

    msgs = make_messages(10)
    msgs[2] = {"role": "user", "content":
               (f'daimon recall: prior work — decision from S-prev (2h ago): '
                f'"{QUOTE}" [verbatim]\nunrelated genuine user text')}
    msgs[5] = {"role": "user", "content":
               f"<system-reminder>\nagent claims resolved: \"{QUOTE}\"\n"
               f"</system-reminder>\nmore genuine text"}
    chat = fake_chat_factory(_new_session_json())
    capture.run("S-new", msgs, project=project, chat=chat, deadline=None)

    evt = store.resolutions(project_dir=project)[item["id"]]
    assert evt["status"] == "resolving-candidate"  # echo is not a witness
    assert evt["source"] == "agent"


def test_role_recorded_in_confirming_note_when_determinable(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory):
    project = "/repo/slice3-role"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)

    msgs = make_messages(10)
    msgs[5] = {"role": "user", "content": QUOTE}
    chat = fake_chat_factory(_new_session_json())
    capture.run("S-new", msgs, project=project, chat=chat, deadline=None)

    evt = store.resolutions(project_dir=project)[item["id"]]
    assert evt["note"] == "verified agent evidence (role: user)"


def test_role_recorded_as_unknown_when_not_determinable(
        tmp_checkpoint_dir, sample_checkpoint):
    # Direct unit call — a message carrying the quote with no usable role
    # ("unknown" is honest, per the design doc's labeling-not-gating stance).
    project = "/repo/slice3-role-unknown"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)
    n = capture._verify_agent_resolutions(project, [{"content": QUOTE}])
    assert n == 1
    evt = store.resolutions(project_dir=project)[item["id"]]
    assert evt["note"] == "verified agent evidence (role: unknown)"


def test_candidate_superseded_by_human_reopen_is_not_touched(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory):
    project = "/repo/slice3-reopened"
    item = _agent_candidate(project, sample_checkpoint, QUOTE)

    rc = cli.main(["reverify", item["id"], "--evidence", "actually still open",
                   "--project", project])
    assert rc == 0
    before = store.resolutions(project_dir=project)[item["id"]]
    assert before["status"] == "reopened"

    msgs = make_messages(10)
    msgs[2] = {"role": "assistant", "content": QUOTE}  # quote IS present
    chat = fake_chat_factory(_new_session_json())
    capture.run("S-new", msgs, project=project, chat=chat, deadline=None)

    after = store.resolutions(project_dir=project)[item["id"]]
    assert after == before  # nothing appended — the human reopen stands


def test_verify_agent_resolutions_is_idempotent(tmp_checkpoint_dir, sample_checkpoint):
    project = "/repo/slice3-idempotent"
    _agent_candidate(project, sample_checkpoint, QUOTE)
    msgs = [{"role": "assistant", "content": QUOTE}]

    n1 = capture._verify_agent_resolutions(project, msgs)
    assert n1 == 1
    n2 = capture._verify_agent_resolutions(project, msgs)
    assert n2 == 0  # already confirmed — not pending anymore, nothing re-appended

    slug = store.project_slug(project)
    lines = (tmp_checkpoint_dir / slug / "events.jsonl").read_text().splitlines()
    confirming = [ln for ln in lines
                 if json.loads(ln).get("status") == "resolved-agent-verified"]
    assert len(confirming) == 1


def test_verification_pass_exception_does_not_fail_serialize(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory, monkeypatch):
    project = "/repo/slice3-exception"
    _agent_candidate(project, sample_checkpoint, QUOTE)

    def boom(*a, **k):
        raise RuntimeError("verification pass exploded")
    monkeypatch.setattr(capture, "_verify_agent_resolutions", boom)

    msgs = make_messages(10)
    chat = fake_chat_factory(_new_session_json())
    out_path = capture.run("S-new", msgs, project=project, chat=chat, deadline=None)
    assert out_path is not None  # serialize still succeeded despite the raise


def test_cross_project_candidate_not_confirmed_by_other_project_serialize(
        tmp_checkpoint_dir, sample_checkpoint, fake_chat_factory):
    project_a = "/repo/slice3-cross-a"
    project_b = "/repo/slice3-cross-b"
    item_a = _agent_candidate(project_a, sample_checkpoint, QUOTE)

    # Project B's own session carries the SAME quote bytes — the pass must
    # scope to project B's own events.jsonl (empty of candidates) and never
    # cross into project A's.
    msgs = make_messages(10)
    msgs[1] = {"role": "assistant", "content": QUOTE}
    chat = fake_chat_factory(_new_session_json(session_id="S-b"))
    capture.run("S-b", msgs, project=project_b, chat=chat, deadline=None)

    evt_a = store.resolutions(project_dir=project_a)[item_a["id"]]
    assert evt_a["status"] == "resolving-candidate"  # untouched by B's serialize
    assert store.is_resolved(evt_a) is False


def test_pending_agent_candidates_filters_source_and_status():
    # Unit-level: only status=="resolving-candidate" AND source=="agent"
    # with a non-empty note count as pending — everything else (a human
    # resolve, a #14 supersede-candidate, an evidence-less/blank row) is
    # excluded.
    events = {
        "o-a": {"status": "resolving-candidate", "source": "agent", "note": "the quote"},
        "o-b": {"status": "resolved", "source": "cli", "note": ""},
        "o-c": {"status": "supersede-candidate:o-x", "source": "serializer"},
        "o-d": {"status": "resolving-candidate", "source": "agent", "note": "   "},
        "o-e": {"status": "resolving-candidate", "source": "agent"},  # no note at all
        "not-a-dict": "garbage",
    }
    out = capture._pending_agent_candidates(events)
    assert out == {"o-a": "the quote"}
