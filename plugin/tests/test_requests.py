"""Cross-project request ledger (#694, PR 1 — the object).

A request is a record in the SENDER's bucket: an ask addressed to another
project by slug. Verdicts are human-only, rejection is sticky, revision is
capped, and forget reaches the prose by value. The join across buckets (the
inbox, the briefing panel, the surfaced stamps) is PR 2/3 — what ships here
is the object, its fold, and its deletion contract.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from daimon_briefing import config, normalize, redact, requests, store


def _iso(offset_seconds=0):
    return (datetime.now(timezone.utc)
            + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize(project_dir, session_id, created):
    store.write_checkpoint(session_id, {
        "session_id": session_id, "created": created,
        "working_context": {"recent_decisions": [
            {"text": "x", "trust": "inferred"}]},
    }, project_dir=project_dir)


@pytest.fixture
def project(tmp_checkpoint_dir):
    # conftest's autouse fixture already isolates DAIMON_CHECKPOINT_DIR.
    return "/p/sender"


RECIPIENT = "-p-recipient"
ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"


def _open(project, *, channel="cli-agent", to=RECIPIENT, ask=ASK, why=WHY,
          **kwargs):
    return requests.open_request(to=to, ask=ask, why=why, channel=channel,
                                 project_dir=project, **kwargs)


def _rows(project):
    path = (config.checkpoint_dir() / store.project_slug(project)
            / "requests.jsonl")
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---- the object -----------------------------------------------------------


def test_open_lands_as_open_with_its_fields(project):
    q_id = _open(project, blocking=True, to_human=True,
                 evidence="issue:694")
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "open"
    assert record["to"] == RECIPIENT
    assert record["to_human"] is True
    assert record["blocking"] is True
    assert record["ask"] == ASK
    assert record["why"] == WHY
    assert record["evidence"] == "issue:694"
    assert record["opened_by"] == "agent"
    assert record["revision"] == 0
    assert record["suppressed"] is False


def test_open_records_the_sender_label_and_supersedes(project):
    first = _open(project)
    second = _open(project, ask="review it again, with the new numbers",
                   supersedes=first)
    record = requests.get(second, project_dir=project)
    assert record["supersedes"] == first
    # D4: the slug is irreversible, so the sender's own label rides the row.
    assert record["from_label"] == "sender"


def test_ids_differ_across_senders_with_identical_ask_why_and_ts(project):
    """D0: the inbox joins rows from every bucket on id alone, so two
    projects sending textually identical asks in the same second must never
    mint the same id."""
    stamp = "2026-08-16T09:00:00Z"
    mine = requests.make_id("-p-a", ASK, WHY, stamp)
    theirs = requests.make_id("-p-b", ASK, WHY, stamp)
    assert mine != theirs
    assert mine == requests.make_id("-p-a", ASK, WHY, stamp)
    # A deliberate re-ask at a later second is a NEW record.
    assert mine != requests.make_id("-p-a", ASK, WHY,
                                    "2026-08-16T09:00:01Z")


def test_id_is_minted_over_post_redaction_bytes(project):
    secret = f"{ASK} using token=abcdefghijkl"
    q_id = _open(project, ask=secret)
    record = requests.get(q_id, project_dir=project)
    scrubbed, _ = redact.redact_text(secret)
    assert record["ask"] == scrubbed
    assert "abcdefghijkl" not in json.dumps(_rows(project))
    assert q_id == requests.make_id(store.project_slug(project),
                                    scrubbed, WHY, record["created_at"])


def test_text_cap_is_rechecked_after_redaction(project):
    """Placeholder expansion can grow the persisted bytes past what the raw
    text passed: `token=abcdefgh` (14 chars) persists as 24."""
    raw = " ".join(["token=abcdefgh"] * 120)
    assert len(raw) <= requests._MAX_TEXT
    with pytest.raises(requests.RequestError):
        _open(project, ask=raw)


def test_ask_and_why_are_required_and_capped(project):
    with pytest.raises(requests.RequestError):
        _open(project, ask="")
    with pytest.raises(requests.RequestError):
        _open(project, why="")
    with pytest.raises(requests.RequestError):
        _open(project, ask="x" * (requests._MAX_TEXT + 1))


def test_unresolvable_project_writes_nothing(project):
    assert requests._path(None) is None
    assert requests.append({"event": "opened"}, project_dir=None) is False


def test_unknown_event_refused_at_the_write_boundary(project):
    with pytest.raises(requests.RequestError):
        requests._stamp("escalated", "q-0123456789ab", "cli-tty")


# ---- authority ------------------------------------------------------------


@pytest.mark.parametrize("verb", ["accept", "reject", "needs_info",
                                  "suppress"])
def test_verdict_verbs_refuse_the_agent_channel(project, verb):
    q_id = _open(project)
    with pytest.raises(requests.RequestError):
        getattr(requests, verb)(q_id, channel="cli-agent",
                                project_dir=project)
    assert requests.get(q_id, project_dir=project)["state"] == "open"


@pytest.mark.parametrize("event", ["accepted", "rejected", "needs_info",
                                   "suppressed"])
def test_fold_rechecks_authority_on_a_forged_row(project, event):
    """The write boundary is not the boundary that matters: a row appended
    off-path (or edited on disk) claiming an agent channel must be inert."""
    q_id = _open(project)
    row = requests._stamp(event, q_id, "cli-agent")
    row["note"] = "landing my own verdict"
    assert requests.append(row, project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "open"
    assert record["suppressed"] is False


def test_human_verdicts_move_the_record(project):
    q_id = _open(project)
    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=project)
    assert requests.get(q_id, project_dir=project)["state"] == "needs-info"
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "accepted"
    assert record["verdict_by"] == "human"


def test_rejection_is_sticky(project):
    """D6: a human verdict may never be buried by a later sender event."""
    q_id = _open(project)
    requests.reject(q_id, channel="cli-tty", note="wrong project",
                    project_dir=project)
    for verb in ("accept", "needs_info"):
        with pytest.raises(requests.RequestError):
            getattr(requests, verb)(q_id, channel="cli-tty",
                                    project_dir=project)
    with pytest.raises(requests.RequestError):
        requests.done(q_id, channel="cli-tty", evidence="shipped it",
                      project_dir=project)
    with pytest.raises(requests.RequestError):
        requests.revise(q_id, channel="cli-agent", ask="softer ask",
                        project_dir=project)
    # …and the fold is inert too, for rows that arrive off-path.
    for event in ("accepted", "needs_info", "done", "revised"):
        row = requests._stamp(event, q_id, "cli-tty")
        row["evidence"] = "it is finished"
        row["ask"] = "softer ask"
        requests.append(row, project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "rejected"
    assert record["ask"] == ASK
    assert record["note"] == "wrong project"


# ---- revise: bounded loop -------------------------------------------------


def test_revise_returns_a_needs_info_record_to_open(project):
    q_id = _open(project)
    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="review it for 0.32.0",
                    project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "open"
    assert record["ask"] == "review it for 0.32.0"
    assert record["revision"] == 1


def test_revise_normalises_only_the_keys_the_caller_set(project):
    """Scar 0042: in an append-only stream the ABSENCE of a key is data."""
    q_id = _open(project, evidence="issue:694")
    requests.revise(q_id, channel="cli-agent", why="the release moved",
                    project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["ask"] == ASK           # untouched, not cleared
    assert record["evidence"] == "issue:694"
    assert record["why"] == "the release moved"


def test_revise_is_capped_and_the_fourth_is_inert_in_the_fold(project):
    q_id = _open(project)
    for n in range(requests.MAX_REVISIONS):
        requests.revise(q_id, channel="cli-agent", ask=f"revision {n}",
                        project_dir=project)
    with pytest.raises(requests.RequestError):
        requests.revise(q_id, channel="cli-agent", ask="revision 3",
                        project_dir=project)
    # Off-path row: the CAP is the fold's, not the CLI's.
    row = requests._stamp("revised", q_id, "cli-agent")
    row["ask"] = "revision 3"
    assert requests.append(row, project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["revision"] == requests.MAX_REVISIONS
    assert record["ask"] == "revision 2"


def test_revise_needs_something_to_change(project):
    q_id = _open(project)
    with pytest.raises(requests.RequestError):
        requests.revise(q_id, channel="cli-agent", project_dir=project)


# ---- done -----------------------------------------------------------------


def test_done_requires_evidence_on_either_channel(project):
    q_id = _open(project)
    with pytest.raises(requests.RequestError):
        requests.done(q_id, channel="cli-agent", evidence="",
                      project_dir=project)
    requests.done(q_id, channel="cli-agent", evidence="merged in PR #712",
                  project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "done"
    # D8: an agent's completion claim is labeled until the byte-check.
    assert record["done_by"] == "agent"
    assert record["done_claimed"] is True
    assert record["done_evidence"] == "merged in PR #712"


def test_human_done_is_not_a_claim(project):
    q_id = _open(project)
    requests.done(q_id, channel="cli-tty", evidence="I did it myself",
                  project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["done_by"] == "human"
    assert record["done_claimed"] is False


def test_done_without_evidence_is_inert_in_the_fold(project):
    q_id = _open(project)
    assert requests.append(requests._stamp("done", q_id, "cli-tty"),
                           project_dir=project)
    assert requests.get(q_id, project_dir=project)["state"] == "open"


# ---- #694 PR 3: done_verified — the session-end byte-check ----------------


def test_verify_done_writes_a_mechanical_channel_row(project):
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="merged in PR #712",
                 project_dir=project)
    assert requests.get(q_id, project_dir=project)["done_claimed"] is True
    assert requests.verify_done(q_id, role="assistant",
                                project_dir=project) is True
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "done"
    assert record["done_claimed"] is False
    assert record["done_verified_at"]


def test_verify_done_role_is_capped_and_recorded(project):
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="shipped it",
                 project_dir=project)
    requests.verify_done(q_id, role="x" * 200, project_dir=project)
    rows = [r for r in requests.events(project_dir=project)
            if r.get("event") == "done_verified"]
    assert len(rows[0]["evidence_role"]) <= 64


def test_done_verified_is_inert_on_a_human_done(project):
    """A human `done` is never a claim (D8) — nothing to verify, and a
    stray/duplicate `done_verified` row must not fabricate a verified flag
    on a record that never carried the unverified qualifier."""
    q_id = _open(project)
    requests.done(q_id, channel="cli-tty", evidence="I did it myself",
                 project_dir=project)
    assert requests.get(q_id, project_dir=project)["done_claimed"] is False
    requests.verify_done(q_id, role="human", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["done_claimed"] is False
    assert record["done_verified_at"] is None


def test_done_verified_is_inert_without_a_done_first(project):
    q_id = _open(project)
    requests.verify_done(q_id, role="assistant", project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "open"
    assert record["done_verified_at"] is None


def test_done_verified_on_an_orphan_id_is_written_but_inert(project):
    """D8/PR 3: a `done` an agent completed for a FOREIGN ask is an orphan in
    THIS bucket's own per-bucket fold (its `opened` row lives elsewhere) —
    `verify_done` still writes the row here (where the `done` row lives),
    and the composer's read-through is what makes it meaningful."""
    foreign = "q-0123456789ab"
    assert requests.verify_done(foreign, role="assistant",
                                project_dir=project) is True
    assert requests.records(project_dir=project) == {}
    assert any(r.get("event") == "done_verified"
              for r in requests.events(project_dir=project))


def test_done_verified_is_deterministic_under_reorder(project):
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="shipped it",
                 project_dir=project)
    requests.verify_done(q_id, role="assistant", project_dir=project)
    rows = requests.events(project_dir=project)
    forward = requests.fold(rows)[q_id]
    backward = requests.fold(list(reversed(rows)))[q_id]
    assert forward == backward
    assert forward["done_claimed"] is False


# ---- #694 PR 3: _verify_agent_request_done() — the session-end pass -------


def test_session_end_verifies_agent_request_done_quote(project):
    from daimon_briefing import capture
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent",
                 evidence="the follow-up issue is filed", project_dir=project)
    messages = [{"role": "user", "content": "ok, the follow-up issue is filed now"}]
    confirmed = capture._verify_agent_request_done(project, messages)
    assert confirmed == 1
    record = requests.get(q_id, project_dir=project)
    assert record["done_claimed"] is False
    assert record["done_verified_at"]


def test_session_end_leaves_unfound_done_quote_as_claimed(project):
    from daimon_briefing import capture
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="something never said",
                 project_dir=project)
    confirmed = capture._verify_agent_request_done(
        project, [{"role": "user", "content": "entirely different words"}])
    assert confirmed == 0
    assert requests.get(q_id, project_dir=project)["done_claimed"] is True


def test_session_end_pass_skips_human_done(project):
    from daimon_briefing import capture
    q_id = _open(project)
    requests.done(q_id, channel="cli-tty", evidence="the deploy target moved",
                 project_dir=project)
    confirmed = capture._verify_agent_request_done(
        project, [{"role": "user", "content": "the deploy target moved"}])
    assert confirmed == 0
    record = requests.get(q_id, project_dir=project)
    assert record["done_claimed"] is False   # never claimed to begin with
    assert record["done_verified_at"] is None


def test_session_end_verifies_a_foreign_asks_done_row_in_this_bucket(project):
    """The recipient-side case (D8): a `done` row this project wrote
    answering a FOREIGN ask is an orphan in its OWN per-bucket fold — the
    pass reads raw events, not `records()`, so it still finds and verifies
    it. The sender's join is what makes the flag meaningful."""
    from daimon_briefing import capture
    foreign = "q-0123456789ab"
    requests.done(foreign, channel="cli-agent", evidence="the fix shipped",
                 project_dir=project)
    confirmed = capture._verify_agent_request_done(
        project, [{"role": "assistant", "content": "the fix shipped in v2"}])
    assert confirmed == 1
    rows = [r for r in requests.events(project_dir=project)
           if r.get("event") == "done_verified"]
    assert rows and rows[0]["request_id"] == foreign


def test_session_end_pass_is_idempotent(project):
    from daimon_briefing import capture
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="shipped it",
                 project_dir=project)
    msgs = [{"role": "user", "content": "shipped it"}]
    n1 = capture._verify_agent_request_done(project, msgs)
    n2 = capture._verify_agent_request_done(project, msgs)
    assert n1 == 1
    assert n2 == 0  # already verified — no duplicate done_verified row


def test_session_end_pass_survives_a_failing_verify(project, monkeypatch):
    from daimon_briefing import capture
    q_id = _open(project)
    requests.done(q_id, channel="cli-agent", evidence="shipped it",
                 project_dir=project)
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    confirmed = capture._verify_agent_request_done(
        project, [{"role": "user", "content": "shipped it"}])
    assert confirmed == 0


def test_capture_run_survives_a_broken_request_done_pass(
        project, sample_checkpoint, fake_chat_factory, monkeypatch):
    from tests.conftest import make_messages
    from daimon_briefing import capture

    def boom(proj, messages):
        raise RuntimeError("pass broke")

    monkeypatch.setattr(capture, "_verify_agent_request_done", boom)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=project)
    chat = fake_chat_factory(json.dumps({
        "session_id": "S-new",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [], "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }))
    out = capture.run("S-new", make_messages(10), project=project,
                      chat=chat, deadline=None)
    assert out is not None  # a broken pass never costs the checkpoint


def test_capture_run_wires_the_request_done_pass(
        project, sample_checkpoint, fake_chat_factory, monkeypatch):
    """Belt for the hand-wiring itself (#694 PR 3's named checklist item):
    capture.run must actually CALL the pass, not just tolerate its absence."""
    from tests.conftest import make_messages
    from daimon_briefing import capture

    calls = []

    def spy(proj, messages):
        calls.append((proj, messages))
        return 0

    monkeypatch.setattr(capture, "_verify_agent_request_done", spy)
    store.write_checkpoint("S-prev", sample_checkpoint, project_dir=project)
    chat = fake_chat_factory(json.dumps({
        "session_id": "S-new",
        "working_context": {
            "active_topic": {"text": "t", "trust": "inferred"},
            "open_questions": [], "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": []},
    }))
    capture.run("S-new", make_messages(10), project=project, chat=chat,
               deadline=None)
    assert len(calls) == 1


# ---- suppress: attention, never state -------------------------------------


def test_suppress_hides_from_the_panel_but_never_from_the_list(project):
    q_id = _open(project)
    requests.suppress(q_id, channel="cli-tty", note="not this quarter",
                      project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["suppressed"] is True
    assert record["state"] == "open"       # a marker, never a state
    assert q_id in {r["request_id"]
                    for r in requests.listing(project_dir=project)}
    assert q_id not in {r["request_id"]
                        for r in requests.renderable(project_dir=project)["rows"]}


def test_a_later_verdict_reverses_the_suppression(project):
    """D5: suppress is attention, not state — a verdict lands normally and
    supersedes it. That is the reversal path; no `unsuppress` verb exists."""
    q_id = _open(project)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "needs-info"
    assert record["suppressed"] is False


# ---- fold mechanics -------------------------------------------------------


def test_fold_is_deterministic_under_reorder(project):
    q_id = _open(project)
    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="review it for 0.32.0",
                    project_dir=project)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    rows = requests.events(project_dir=project)
    forward = requests.fold(rows)[q_id]
    backward = requests.fold(list(reversed(rows)))[q_id]
    assert forward == backward
    assert forward["state"] == "accepted"


def test_same_instant_rejection_beats_an_acceptance(project):
    """The rank tie-break fails toward the human verdict that closes the
    loop: a same-`order` accept must not bury a reject."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    instant = rows[0]["order"] + 10 ** 9
    for event in ("accepted", "rejected"):
        rows.append(requests._stamp(event, q_id, "cli-tty", now_ns=instant))
    assert requests.fold(rows)[q_id]["state"] == "rejected"


def test_orphan_lifecycle_rows_are_inert_but_kept(project):
    """D0 orphan rule: a verdict row whose origin is not in this bucket is
    inert in the fold and visible in the raw audit."""
    orphan = "q-0123456789ab"
    assert requests.append(requests._stamp("accepted", orphan, "cli-tty"),
                           project_dir=project)
    assert requests.records(project_dir=project) == {}
    assert len(_rows(project)) == 1


def test_surfaced_rows_fold_per_revision_epoch(project):
    """D1: the stamp is write-once per (request id, revision epoch); the fold
    takes the earliest row of each epoch, and a revise opens a new epoch."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    base = rows[0]["order"]

    def at(event, seconds, **extra):
        row = requests._stamp(event, q_id, "mechanical",
                              now_ns=base + seconds * 10 ** 9)
        row.update(extra)
        return row

    rows.append(at("surfaced", 10))
    rows.append(at("surfaced", 20))
    assert set(requests.fold(rows)[q_id]["surfaced"]) == {0}
    revised = requests._stamp("revised", q_id, "cli-agent",
                              now_ns=base + 30 * 10 ** 9)
    revised["ask"] = "second ask"
    rows.append(revised)
    rows.append(at("surfaced", 40))
    record = requests.fold(rows)[q_id]
    assert set(record["surfaced"]) == {0, 1}
    # Earliest row of the epoch wins: the 20s stamp never overwrote the 10s.
    assert record["surfaced"][0] == requests._ts(base + 10 * 10 ** 9)
    assert record["surfaced"][1] == requests._ts(base + 40 * 10 ** 9)


def test_attention_rows_never_move_the_records_age(project):
    q_id = _open(project)
    before = requests.get(q_id, project_dir=project)["updated_at"]
    for event in ("surfaced", "verdict_surfaced"):
        requests.append(requests._stamp(event, q_id, "mechanical",
                                        now_ns=9 * 10**18),
                        project_dir=project)
    after = requests.get(q_id, project_dir=project)
    assert after["updated_at"] == before
    assert after["verdict_surfaced"][after["revision"]]


def test_renderable_caps_and_counts_the_overflow(project):
    for n in range(requests.RENDER_CAP + 2):
        _open(project, ask=f"ask number {n} about the release")
    entry = requests.renderable(project_dir=project)
    assert len(entry["rows"]) == requests.RENDER_CAP
    assert entry["overflow"] == 2


def test_renderable_drops_settled_records(project):
    open_id = _open(project)
    done_id = _open(project, ask="the second ask, already handled")
    requests.done(done_id, channel="cli-tty", evidence="done last week",
                  project_dir=project)
    ids = {r["request_id"] for r in requests.renderable(project_dir=project)["rows"]}
    assert ids == {open_id}


def test_torn_last_line_is_repaired_by_the_next_append(project):
    q_id = _open(project)
    path = requests._path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "torn"')
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    assert requests.get(q_id, project_dir=project)["suppressed"] is True


# ---- forget ---------------------------------------------------------------


def test_forget_by_value_removes_every_row_of_the_record(project):
    doomed = _open(project)
    kept = _open(project, ask="an unrelated ask about the docs site")
    requests.needs_info(doomed, channel="cli-tty", note="which release?",
                        project_dir=project)
    gone = requests.forget_content_key(normalize.content_key(ASK),
                                       project_dir=project)
    assert gone == [doomed]
    assert set(requests.records(project_dir=project)) == {kept}
    blob = json.dumps(_rows(project))
    assert ASK not in blob
    assert "which release?" not in blob   # the verdict note went with it


def test_forget_reaches_every_plaintext_field(project):
    for field, kwargs in (("why", {"why": "the value to remove"}),
                          ("evidence", {"evidence": "the value to remove"})):
        q_id = _open(project, ask=f"ask carrying {field}", **kwargs)
        assert requests.forget_content_key(
            normalize.content_key("the value to remove"),
            project_dir=project) == [q_id]


def test_plaintext_declaration_has_one_reader(project):
    q_id = _open(project, evidence="issue:694")
    requests.reject(q_id, channel="cli-tty", note="a human note",
                    project_dir=project)
    keys = set()
    values = []
    rows = requests.events(project_dir=project)
    for row in rows:
        keys |= requests.row_content_keys(row)
        values += requests.plaintext_values(row)
    assert "a human note" in values
    assert normalize.content_key(ASK) in keys
    # `author` is a person's name, never item text (the refutations rule):
    # matching a tombstone against it would let one forgotten value delete
    # every record a given author ever wrote.
    author = rows[0]["author"]
    assert author and author not in values


def test_forget_rewrite_reads_raw_lines_not_the_tolerant_reader(project):
    """A forgiving read feeding a write is the scar 0025/0042 shape: rows a
    FUTURE daimon added must survive a deletion aimed at another record."""
    doomed = _open(project)
    future = requests._stamp("opened", "q-abcdefabcdef", "cli-tty")
    future.update({"to": RECIPIENT, "ask": "a future ask", "why": "because",
                   "event": "escalated"})
    path = requests._path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(future) + "\n")
    requests.forget_content_key(normalize.content_key(ASK),
                                project_dir=project)
    assert any(r.get("event") == "escalated" for r in _rows(project))
    assert doomed not in requests.records(project_dir=project)


# ---- the CLI --------------------------------------------------------------


OTHER = "/p/recipient"


@pytest.fixture
def recipient(project):
    """A second project with a real bucket, so `--to` validation has a
    target that exists."""
    store.write_checkpoint("S-r", {
        "session_id": "S-r", "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "the recipient shipped something", "trust": "inferred"}]},
    }, project_dir=OTHER)
    return store.project_slug(OTHER)


def _cli_open(project, recipient, *extra):
    from daimon_briefing import cli
    return cli.main(["request", "open", "--to", OTHER, "--ask", ASK,
                     "--why", WHY, "--by", "agent", "--project", project,
                     *extra])


def test_cli_open_records_the_request(project, recipient, capsys):
    assert _cli_open(project, recipient, "--blocking") == 0
    records = list(requests.records(project_dir=project).values())
    assert len(records) == 1
    assert records[0]["to"] == recipient and records[0]["blocking"] is True
    assert records[0]["request_id"] in capsys.readouterr().out


def test_cli_open_takes_the_slug_spelling_too(project, recipient, capsys):
    """A real slug begins with '-', so `--to <slug>` is unusable — argparse
    reads it as an option. The `--to=<slug>` form is the one that works, and
    it must land on the same bucket the directory form does."""
    from daimon_briefing import cli
    rc = cli.main(["request", "open", f"--to={recipient}", "--ask", ASK,
                   "--why", WHY, "--by", "agent", "--project", project])
    assert rc == 0
    assert next(iter(requests.records(
        project_dir=project).values()))["to"] == recipient


def test_cli_open_refuses_an_unknown_recipient_with_near_matches(
        project, recipient, capsys):
    typo = recipient[:-1] + "x"
    from daimon_briefing import cli
    rc = cli.main(["request", "open", f"--to={typo}", "--ask", ASK,
                   "--why", WHY, "--by", "agent", "--project", project])
    assert rc == 1
    assert not requests.records(project_dir=project)
    out = capsys.readouterr().out
    assert recipient in out and "--anyway" in out


def test_cli_open_anyway_records_it_with_a_loud_warning(project, recipient,
                                                        capsys):
    from daimon_briefing import cli
    rc = cli.main(["request", "open", "--to", "/p/not-yet-a-project",
                   "--ask", ASK, "--why", WHY, "--by", "agent",
                   "--anyway", "--project", project])
    assert rc == 0
    assert len(requests.records(project_dir=project)) == 1
    out = capsys.readouterr().out
    assert "warning:" in out and "never surfaced" in out


def test_cli_open_human_path_requires_a_terminal(project, recipient,
                                                 monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    rc = cli.main(["request", "open", "--to", OTHER, "--ask", ASK,
                   "--why", WHY, "--project", project])
    assert rc == 1
    assert not requests.records(project_dir=project)


@pytest.mark.parametrize("verb", ["accept", "reject", "needs-info",
                                  "suppress"])
def test_cli_verdict_verbs_refuse_the_agent_channel(project, recipient, verb,
                                                    capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.records(project_dir=project)))
    rc = cli.main(["request", verb, q_id, "--by", "agent",
                   "--project", project])
    assert rc == 1
    assert requests.get(q_id, project_dir=project)["state"] == "open"


def test_cli_human_verdicts_and_done(project, recipient, monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.records(project_dir=project)))
    assert cli.main(["request", "needs-info", q_id, "--note", "which release?",
                     "--project", project]) == 0
    assert requests.get(q_id, project_dir=project)["state"] == "needs-info"
    assert cli.main(["request", "revise", q_id, "--ask", "review it for 0.32.0",
                     "--by", "agent", "--project", project]) == 0
    assert cli.main(["request", "accept", q_id, "--project", project]) == 0
    assert cli.main(["request", "done", q_id, "--evidence", "merged in #712",
                     "--by", "agent", "--project", project]) == 0
    record = requests.get(q_id, project_dir=project)
    assert record["state"] == "done" and record["done_claimed"] is True
    assert "claimed, unverified" in capsys.readouterr().out


def test_cli_revise_refuses_past_the_cap(project, recipient, capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.records(project_dir=project)))
    for n in range(requests.MAX_REVISIONS):
        assert cli.main(["request", "revise", q_id, "--ask", f"ask {n}",
                         "--by", "agent", "--project", project]) == 0
    rc = cli.main(["request", "revise", q_id, "--ask", "one more",
                   "--by", "agent", "--project", project])
    assert rc == 1
    out = capsys.readouterr().out
    assert "--supersedes" in out
    assert requests.get(q_id, project_dir=project)["revision"] == \
        requests.MAX_REVISIONS


def test_cli_list_renders_records_and_json(project, recipient, monkeypatch,
                                           capsys):
    from daimon_briefing import cli
    assert cli.main(["request", "list", "--project", project]) == 0
    assert "no requests" in capsys.readouterr().out
    assert _cli_open(project, recipient, "--blocking") == 0
    q_id = next(iter(requests.records(project_dir=project)))
    assert cli.main(["request", "list", "--project", project]) == 0
    out = capsys.readouterr().out
    assert q_id in out and "open" in out and "Blocking" in out
    assert cli.main(["request", "list", "--json", "--project", project]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["request_id"] for r in payload] == [q_id]
    assert payload[0]["state"] == "open"


def test_cli_list_keeps_suppressed_records_visible(project, recipient,
                                                   monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.records(project_dir=project)))
    assert cli.main(["request", "suppress", q_id, "--project", project]) == 0
    capsys.readouterr()
    assert cli.main(["request", "list", "--project", project]) == 0
    out = capsys.readouterr().out
    assert q_id in out and "Suppressed" in out


def test_request_id_headers_get_their_own_spans():
    """#711's three-span header contract, extended to the q- id space: the
    id a human copies stays the bold-cyan span."""
    from daimon_briefing import render
    spans = render._ledger_header_spans(
        "[→ open · agent-asked] q-0123456789ab  review the proposal")
    assert spans == ("[→ open · agent-asked]", "q-0123456789ab",
                     "review the proposal")


# ---- registration, forget, audit ------------------------------------------


def test_the_request_stream_is_a_declared_surface():
    from daimon_briefing import surfaces
    s = surfaces.match("checkpoints/{slug}/requests.jsonl")
    assert s is not None
    assert s.plaintext is True and s.delete == "rewrite"
    # It must win over the generic per-bucket entries that follow it.
    assert s.owner == "requests.append"


def test_cli_forget_reaches_the_request_ledger_by_value(project, capsys):
    from daimon_briefing import cli
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-08-16T00:00:00Z",
        "working_context": {"open_questions": [
            {"text": "does the recipient own this", "trust": "inferred"}]},
    }, project_dir=project)
    q_id = _open(project)
    rc = cli.main(["forget", ASK, "--project", project])
    assert rc == 0
    assert requests.get(q_id, project_dir=project) is None
    assert "request" in capsys.readouterr().out


def test_audit_privacy_scans_the_request_ledger(project):
    from daimon_briefing import cli, privacy
    store.write_checkpoint("S-1", {
        "session_id": "S-1", "created": "2026-08-16T00:00:00Z",
        "working_context": {"open_questions": [
            {"text": "does the recipient own this", "trust": "inferred"}]},
    }, project_dir=project)
    q_id = _open(project)
    assert cli.main(["forget", ASK, "--project", project]) == 0
    assert requests.get(q_id, project_dir=project) is None
    # A row landing AFTER the forget carries the forgotten value: the audit
    # must see it (this is what proves the scanner works).
    row = requests._stamp("opened", "q-abcabcabcabc", "cli-agent")
    row.update({"to": "-p-recipient", "ask": ASK, "why": "residue"})
    assert requests.append(row, project_dir=project)
    path = requests._path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("junk line\n[1, 2]\n")
    result = privacy.audit_project(project_dir=project)
    assert "request-ledger" in {f["surface"] for f in result["findings"]}
    assert result["requests"]["rows"] >= 1


def test_audit_privacy_no_slug_shape_includes_requests():
    from daimon_briefing import privacy
    result = privacy.audit_project(project_dir="")
    assert result["requests"] == {"records": 0, "rows": 0, "bytes": 0}


# ---- the frozen vocabularies ----------------------------------------------


def test_the_event_vocabulary_is_frozen():
    """PR 1 freezes the format so PR 2/3 cannot widen it by accident: rows
    an older reader drops are rows it silently re-renders as undecided.
    #694 PR 3 widens it ONCE, deliberately, with `done_verified` (the
    design's own implementation note 2: an older reader drops the unknown
    row and keeps rendering "done (claimed, unverified)" — the safe
    direction). #756 widens it a second time, on the same terms, with
    `delivered`: an older reader drops the row, reads the record as
    undelivered, and at worst repeats a nudge. #801 widens it a third time,
    on identical terms, with `verdict_delivered`: the sender-side counterpart,
    where an older reader drops the row, reads the verdict as undelivered, and
    at worst repeats a nudge. Every widening so far fails the same safe way,
    which is the property this test exists to keep true."""
    assert set(requests.EVENTS) == {
        "opened", "revised", "surfaced", "verdict_surfaced", "delivered",
        "verdict_delivered",
        "needs_info", "accepted", "rejected", "done", "suppressed",
        "done_verified"}


def test_the_fold_reaches_every_render_state_but_never_stale(project):
    """`stale` is DERIVED from consumption (PR 3) and never appended, so the
    fold must produce every OTHER declared state and that one never."""
    seen = set()
    open_id = _open(project)
    seen.add(requests.get(open_id, project_dir=project)["state"])
    info_id = _open(project, ask="the second ask, awaiting an answer")
    requests.needs_info(info_id, channel="cli-tty", note="which release?",
                        project_dir=project)
    accepted = _open(project, ask="the third ask, taken on")
    requests.accept(accepted, channel="cli-tty", project_dir=project)
    rejected = _open(project, ask="the fourth ask, declined")
    requests.reject(rejected, channel="cli-tty", project_dir=project)
    finished = _open(project, ask="the fifth ask, handled")
    requests.done(finished, channel="cli-tty", evidence="shipped on Tuesday",
                  project_dir=project)
    seen |= {r["state"] for r in requests.records(project_dir=project).values()}
    assert seen == set(requests.RENDER_STATES) - {"stale"}


# ---- the PR-1 interim window (design r2-G) --------------------------------


def test_a_verdict_on_an_id_this_bucket_cannot_see_is_written_and_inert(
        project):
    """A request lives in the SENDER's bucket, so a recipient answering one
    has nothing local to resolve against. The answer is written here and
    stays an orphan until PR 2's read-time join pairs it with its origin —
    refusing it would make the recipient side unimplementable."""
    foreign = "q-0123456789ab"
    requests.accept(foreign, channel="cli-tty", note="on it",
                    project_dir=project)
    requests.done(foreign, channel="cli-tty", evidence="shipped it",
                  project_dir=project)
    requests.suppress(foreign, channel="cli-tty", project_dir=project)
    assert requests.records(project_dir=project) == {}   # inert…
    assert len(_rows(project)) == 3                      # …but on disk


@pytest.mark.parametrize("verb", ["accept", "done", "suppress"])
def test_a_malformed_id_is_still_refused(project, verb):
    kwargs = {"evidence": "x"} if verb == "done" else {}
    with pytest.raises(requests.RequestError):
        getattr(requests, verb)("not-a-request-id", channel="cli-tty",
                                project_dir=project, **kwargs)


def test_revise_still_requires_a_record_it_can_read(project):
    """The sender-side verb is the asymmetric one: a revision replaces text
    it must read and is bounded by a count it must know."""
    with pytest.raises(requests.RequestError):
        requests.revise("q-0123456789ab", channel="cli-agent", ask="anything",
                        project_dir=project)


def test_cli_says_so_when_the_answered_request_is_not_in_this_bucket(
        project, monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    rc = cli.main(["request", "accept", "q-0123456789ab",
                   "--project", project])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no matching request in this bucket" in out


# ---- refusal, degradation, and tolerance edges ----------------------------


def test_stamp_refuses_a_malformed_id_and_an_unknown_channel(project):
    """Nothing reaches the ledger through a hand-built row either: the stamp
    is where an id shape and a channel name are checked, and a channel it
    does not know has no authority to derive."""
    with pytest.raises(requests.RequestError):
        requests._stamp("opened", "not-an-id", "cli-tty")
    with pytest.raises(requests.RequestError):
        requests._stamp("opened", "q-0123456789ab", "cli-ui")
    assert not _rows_or_empty(project)


def _rows_or_empty(project):
    path = requests._path(project)
    return [] if path is None or not path.exists() else _rows(project)


def test_a_missing_ledger_is_not_torn(project, tmp_path):
    """`_is_torn` answers for a file it cannot stat: the first append to a
    fresh bucket must not prepend a repair newline."""
    assert requests._is_torn(tmp_path / "never-written.jsonl") is False
    _open(project)
    assert requests._path(project).read_text(
        encoding="utf-8").startswith("{")


def test_the_kill_switch_stops_the_ledger(project, monkeypatch):
    """#421 posture: with daimon disabled the write path is a no-op, and the
    verbs say so instead of reporting a record nobody wrote."""
    q_id = _open(project)
    before = len(_rows(project))
    monkeypatch.setenv("DAIMON_DISABLE", "1")
    assert requests.append(requests._stamp("suppressed", q_id, "cli-tty"),
                           project_dir=project) is False
    for call in (
            lambda: _open(project, ask="an ask written while disabled"),
            lambda: requests.revise(q_id, channel="cli-agent", ask="nope",
                                    project_dir=project),
            lambda: requests.accept(q_id, channel="cli-tty",
                                    project_dir=project),
            lambda: requests.suppress(q_id, channel="cli-tty",
                                      project_dir=project),
            lambda: requests.done(q_id, channel="cli-tty", evidence="x",
                                  project_dir=project)):
        with pytest.raises(requests.RequestError):
            call()
    assert len(_rows(project)) == before


def test_an_unwritable_bucket_degrades_instead_of_raising(project):
    """A bucket path occupied by a FILE cannot hold a ledger. `append` is the
    layer that must not explode — it reports failure, and the verbs above it
    turn that into a refusal."""
    bucket = config.checkpoint_dir() / store.project_slug(project)
    bucket.parent.mkdir(parents=True, exist_ok=True)
    bucket.write_text("not a directory", encoding="utf-8")
    assert requests.append(requests._stamp("opened", "q-0123456789ab",
                                           "cli-tty"),
                           project_dir=project) is False
    with pytest.raises(requests.RequestError):
        _open(project)


def test_an_unreadable_ledger_reads_empty_and_deletes_nothing(project):
    """Undecodable bytes are a read failure, not a licence to rewrite: the
    tolerant reader returns nothing and the deleter touches nothing."""
    q_id = _open(project)
    path = requests._path(project)
    before = path.read_bytes()
    path.write_bytes(b"\xff\xfe not utf-8 at all\n")
    assert requests.events(project_dir=project) == []
    assert requests.records(project_dir=project) == {}
    assert requests._rewrite_without({q_id}, project_dir=project) == []
    assert path.read_bytes() != before        # still the corrupt bytes
    assert path.read_bytes() == b"\xff\xfe not utf-8 at all\n"


def test_fold_tolerates_garbage_ordering_values(project):
    """`order` and `_line` come off disk; a non-integer must fall back to the
    default rather than sink the whole fold."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    rows[0]["order"] = "not-a-number"
    rows[0]["_line"] = [1]
    assert requests.fold(rows)[q_id]["state"] == "open"


def test_a_second_opened_row_never_rewrites_the_first(project):
    """Duplicate logical opens: first writer wins, so a replayed row cannot
    quietly change what the recipient was asked."""
    q_id = _open(project)
    replay = requests._stamp("opened", q_id, "cli-tty")
    replay.update({"to": RECIPIENT, "ask": "something else entirely",
                   "why": WHY})
    assert requests.append(replay, project_dir=project)
    assert requests.get(q_id, project_dir=project)["ask"] == ASK


@pytest.mark.parametrize("field,value", [("to", ""), ("to", "not/a/slug"),
                                         ("ask", "   ")])
def test_an_opened_row_missing_its_addressing_never_founds_a_record(
        project, field, value):
    """The read-boundary shape check: a row edited on disk into something
    unaddressable is inert, not a record addressed to nobody."""
    row = requests._stamp("opened", "q-0123456789ab", "cli-tty")
    row.update({"to": RECIPIENT, "ask": ASK, "why": WHY, field: value})
    assert requests.append(row, project_dir=project)
    assert requests.records(project_dir=project) == {}


def test_open_refuses_a_malformed_recipient_or_supersedes_id(project):
    with pytest.raises(requests.RequestError):
        _open(project, to="not/a/slug")
    with pytest.raises(requests.RequestError):
        _open(project, supersedes="q-nope")


def test_open_refuses_when_the_sender_project_is_unknown():
    with pytest.raises(requests.RequestError):
        requests.open_request(to=RECIPIENT, ask=ASK, why=WHY,
                              channel="cli-agent", project_dir=None)


def test_the_same_ask_twice_in_one_second_is_refused_not_collided(
        project, monkeypatch):
    """The id hashes the second, so a re-ask inside one second would land on
    the record already open. It is refused instead of overwriting it."""
    monkeypatch.setattr(requests.time, "time_ns", lambda: 1_786_000_000 * 10 ** 9)
    first = _open(project)
    with pytest.raises(requests.RequestError):
        _open(project)
    assert list(requests.records(project_dir=project)) == [first]


def test_revise_can_replace_the_evidence_alone(project):
    q_id = _open(project, evidence="issue:694")
    requests.revise(q_id, channel="cli-agent", evidence="issue:712",
                    project_dir=project)
    record = requests.get(q_id, project_dir=project)
    assert record["evidence"] == "issue:712"
    assert record["ask"] == ASK and record["revision"] == 1


# ---- forget: the rewrite edges -------------------------------------------


def test_a_deletion_aimed_at_a_bucket_with_no_ledger_removes_nothing(project):
    assert requests._rewrite_without({"q-0123456789ab"},
                                     project_dir=project) == []
    assert requests._rewrite_without({"q-0123456789ab"},
                                     project_dir=None) == []


def test_the_rewrite_drops_blank_and_torn_lines_it_passes_over(project):
    """Torn rows are expendable — keeping one would leave bytes behind that
    the deletion was asked to remove."""
    doomed = _open(project)
    kept = _open(project, ask="an unrelated ask about the docs site")
    path = requests._path(project)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write('{"request_id": "q-abcabcabcabc", "ask": "half a r')
    assert requests.forget_content_key(normalize.content_key(ASK),
                                       project_dir=project) == [doomed]
    text = path.read_text(encoding="utf-8")
    assert "half a r" not in text
    assert "" not in text.splitlines()
    assert set(requests.records(project_dir=project)) == {kept}


def test_a_failed_swap_leaves_the_ledger_whole_and_no_tmp_behind(
        project, monkeypatch):
    """Atomic or nothing: if the replace fails, the deletion reports that it
    removed nothing rather than leaving a half-written ledger."""
    q_id = _open(project)
    path = requests._path(project)
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("no space left on device")

    monkeypatch.setattr(requests.os, "replace", boom)
    assert requests.forget_content_key(normalize.content_key(ASK),
                                       project_dir=project) == []
    assert path.read_text(encoding="utf-8") == before
    assert requests.get(q_id, project_dir=project) is not None
    assert not list(path.parent.glob("*.forget-tmp"))


def test_a_failed_swap_survives_an_undeletable_tmp(project, monkeypatch):
    """The cleanup of the temp file is best-effort; a failure there must not
    turn a degraded deletion into a crash."""
    _open(project)
    monkeypatch.setattr(requests.os, "replace",
                        lambda src, dst: (_ for _ in ()).throw(OSError()))
    real_unlink = type(requests._path(project)).unlink

    def deny(self, missing_ok=False):
        if self.name.endswith(".forget-tmp"):
            raise OSError("EPERM")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(requests._path(project)), "unlink", deny)
    assert requests.forget_content_key(normalize.content_key(ASK),
                                       project_dir=project) == []


# ---- CLI: the remaining refusal and card branches -------------------------


def test_cli_list_renders_evidence_and_supersedes(project, recipient, capsys):
    from daimon_briefing import cli
    first = _open(project, evidence="issue:694")
    second = _open(project, ask="review it again, with the new numbers",
                   supersedes=first)
    assert cli.main(["request", "list", "--project", project]) == 0
    out = capsys.readouterr().out
    assert "Evidence: issue:694" in out
    assert f"Supersedes: {first}" in out
    assert second in out


def test_cli_done_on_a_rejected_request_refuses(project, recipient,
                                                monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    q_id = _open(project)
    assert cli.main(["request", "reject", q_id, "--note", "out of scope",
                     "--project", project]) == 0
    capsys.readouterr()
    rc = cli.main(["request", "done", q_id, "--evidence", "I did it anyway",
                   "--by", "agent", "--project", project])
    assert rc == 1
    assert "request done refused" in capsys.readouterr().out
    assert requests.get(q_id, project_dir=project)["state"] == "rejected"


# ---- PR 2: the join composer -----------------------------------------------
#
# Verdict/suppress/done rows land in the RECIPIENT's own bucket, referencing a
# foreign request id — orphans in that bucket's per-bucket fold (PR 1's
# `records()`). The composer is the read-time join that pairs them with their
# origin (D0), in both directions: sender_join (a sender's asks, joined with
# whatever the recipient decided) and recipient_join (everything addressed to
# this project, scanning every other bucket for the origin row).


def _seed_bucket(project_dir, session="S-bucket"):
    """A real checkpoint bucket, so store.list_buckets() can discover it —
    recipient_join's scan enumerates buckets the same way `daimon projects`
    does. Only SENDER buckets need this; a recipient need not have a
    checkpoint of its own to read its own inbox."""
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir)
    return store.project_slug(project_dir)


def test_sender_join_pulls_in_the_recipients_verdict(project):
    recipient_slug = _seed_bucket("/p/req-recipient-a")
    q_id = _open(project, to=recipient_slug)
    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=recipient_slug)
    joined = requests.sender_join(project_dir=project)
    assert joined[q_id]["state"] == "needs-info"
    # The sender's OWN per-bucket fold never sees it — PR 1 shape, unchanged:
    # the verdict row lives in a different bucket entirely.
    assert requests.get(q_id, project_dir=project)["state"] == "open"


def test_sender_join_filters_suppression_d5(project):
    """D5: to the sender, a suppressed request reads "surfaced, undecided" —
    the recipient's own panel-attention housekeeping never crosses the join."""
    recipient_slug = _seed_bucket("/p/req-recipient-b")
    q_id = _open(project, to=recipient_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=recipient_slug)
    joined = requests.sender_join(project_dir=project)
    assert joined[q_id]["suppressed"] is False
    assert joined[q_id]["state"] == "open"


def test_sender_join_a_later_verdict_still_lands_after_suppression(project):
    recipient_slug = _seed_bucket("/p/req-recipient-c")
    q_id = _open(project, to=recipient_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    joined = requests.sender_join(project_dir=project)
    assert joined[q_id]["state"] == "accepted"


def test_sender_join_on_an_unknown_project_is_empty(project):
    assert requests.sender_join(project_dir=None) == {}


def test_sender_join_folds_a_self_addressed_request_without_a_foreign_fetch(
        project, monkeypatch):
    """A self-addressed request (`to == sender_slug`) is entirely local — no
    SECOND bucket exists to fetch, and `sender_join` must skip that fetch
    rather than re-reading its own bucket a second time under a different
    name."""
    my_slug = store.project_slug(project)
    q_id = requests.open_request(to=my_slug, ask=ASK, why=WHY,
                                 channel="cli-agent", project_dir=project)
    real_events = requests.events
    calls = []

    def _tracking(*a, **k):
        calls.append((a, k))
        return real_events(*a, **k)

    monkeypatch.setattr(requests, "events", _tracking)
    joined = requests.sender_join(project_dir=project)
    assert joined[q_id]["ask"] == ASK
    assert joined[q_id]["state"] == "open"
    assert len(calls) == 1  # only the sender's own bucket, read once


def test_recipient_join_finds_asks_addressed_to_this_project(project):
    sender_slug = _seed_bucket("/p/req-sender-a")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    joined = requests.recipient_join(project_dir=project)
    assert joined[q_id]["ask"] == ASK
    assert joined[q_id]["state"] == "open"
    assert joined[q_id]["from_slug"] == sender_slug


def test_recipient_join_excludes_requests_this_project_sent(project):
    """A project's own OUTGOING asks — opened rows in its own bucket whose
    `to` points elsewhere — must never leak into its own inbox. Only
    requests addressed BACK to this project belong there (D0's join is
    directional: `to == my_slug`, not "any row this bucket ever wrote)."""
    recipient_slug = _seed_bucket("/p/req-recipient-leak")
    requests.open_request(to=recipient_slug, ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=project)
    assert requests.recipient_join(project_dir=project) == {}
    assert requests.inbox_renderable(project_dir=project)["rows"] == []
    assert requests.inbox_listing(project_dir=project) == []


def test_recipient_join_keeps_self_addressed_requests(project):
    """A request addressed TO the sender's own slug (self-addressed — the
    write-audit guard's drive recipes exercise exactly this) is a legitimate
    inbox entry; only requests addressed ELSEWHERE are excluded."""
    my_slug = store.project_slug(project)
    q_id = requests.open_request(to=my_slug, ask=ASK, why=WHY,
                                 channel="cli-agent", project_dir=project)
    joined = requests.recipient_join(project_dir=project)
    assert joined[q_id]["ask"] == ASK
    assert joined[q_id]["state"] == "open"


def test_sender_join_ignores_verdict_rows_this_project_wrote_as_a_recipient(
        project):
    """The symmetric case: verdict/attention rows this project wrote in its
    OWN bucket while answering a FOREIGN ask (no local `opened` row for that
    id) must stay inert in `sender_join` too — the fold's orphan rule
    already guarantees this (no `opened` row, no record), pinned here so a
    future refactor of the row-gathering loop cannot regress it silently."""
    foreign = "q-0123456789ab"
    requests.accept(foreign, channel="cli-tty", project_dir=project)
    requests.done(foreign, channel="cli-tty", evidence="shipped it",
                 project_dir=project)
    joined = requests.sender_join(project_dir=project)
    assert foreign not in joined
    assert joined == {}
    # ...but the rows are still on disk, per the orphan rule's other half.
    assert any(r.get("request_id") == foreign
              for r in requests.events(project_dir=project))


def test_recipient_join_merges_local_verdict_rows(project):
    sender_slug = _seed_bucket("/p/req-sender-b")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=project)
    joined = requests.recipient_join(project_dir=project)
    assert joined[q_id]["state"] == "accepted"


def test_recipient_join_orphan_rule_when_sender_bucket_is_gone(project):
    """D0: a verdict/attention row whose origin bucket is unreadable or gone
    is inert in the join but stays visible in the raw audit."""
    foreign = "q-0123456789ab"
    requests.accept(foreign, channel="cli-tty", project_dir=project)
    joined = requests.recipient_join(project_dir=project)
    assert foreign not in joined
    assert any(r.get("request_id") == foreign
              for r in requests.events(project_dir=project))


def test_recipient_join_ignores_asks_addressed_elsewhere(project):
    sender_slug = _seed_bucket("/p/req-sender-c")
    requests.open_request(to="-p-someone-else", ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=sender_slug)
    assert requests.recipient_join(project_dir=project) == {}


def test_recipient_join_on_an_unknown_project_is_empty(project):
    assert requests.recipient_join(project_dir=None) == {}


def test_inbox_listing_keeps_suppressed_visible_with_a_state(project):
    sender_slug = _seed_bucket("/p/req-sender-d")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    rows = requests.inbox_listing(project_dir=project)
    assert rows[0]["request_id"] == q_id
    assert rows[0]["suppressed"] is True
    assert rows[0]["state"] == "open"


def test_inbox_renderable_drops_suppressed_and_settled(project):
    sender_slug = _seed_bucket("/p/req-sender-e")
    to = store.project_slug(project)
    open_id = requests.open_request(to=to, ask=ASK, why=WHY,
                                    channel="cli-agent", project_dir=sender_slug)
    suppressed_id = requests.open_request(
        to=to, ask="a second ask about the release", why=WHY,
        channel="cli-agent", project_dir=sender_slug)
    requests.suppress(suppressed_id, channel="cli-tty", project_dir=project)
    done_id = requests.open_request(
        to=to, ask="a third ask, already handled", why=WHY,
        channel="cli-agent", project_dir=sender_slug)
    requests.done(done_id, channel="cli-tty", evidence="shipped",
                 project_dir=project)
    entry = requests.inbox_renderable(project_dir=project)
    ids = {r["request_id"] for r in entry["rows"]}
    assert ids == {open_id}


def test_inbox_renderable_caps_and_counts_the_overflow(project):
    sender_slug = _seed_bucket("/p/req-sender-f")
    to = store.project_slug(project)
    for n in range(requests.RENDER_CAP + 2):
        requests.open_request(to=to, ask=f"ask {n} about the release",
                              why=WHY, channel="cli-agent",
                              project_dir=sender_slug)
    entry = requests.inbox_renderable(project_dir=project)
    assert len(entry["rows"]) == requests.RENDER_CAP
    assert entry["overflow"] == 2


def test_needs_surfaced_stamp_and_dedup(project):
    sender_slug = _seed_bucket("/p/req-sender-g")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_surfaced_stamp(record) is True
    assert requests.stamp_surfaced(q_id, project_dir=project) is True
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_surfaced_stamp(record) is False


def test_stamp_dedup_earliest_wins_under_a_duplicate_write(project):
    """Concurrent-brief TOCTOU may at worst duplicate a stamp row; the fold
    already takes the earliest — pin it."""
    sender_slug = _seed_bucket("/p/req-sender-h")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    assert requests.stamp_surfaced(q_id, project_dir=project) is True
    assert requests.stamp_surfaced(q_id, project_dir=project) is True
    record = requests.recipient_join(project_dir=project)[q_id]
    assert set(record["surfaced"]) == {0}


def test_stamp_dedup_resets_on_a_new_revision_epoch(project):
    sender_slug = _seed_bucket("/p/req-sender-i")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.stamp_surfaced(q_id, project_dir=project)
    requests.revise(q_id, channel="cli-agent", ask="a sharper ask",
                    project_dir=sender_slug)
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_surfaced_stamp(record) is True  # new epoch, unstamped


def test_supersedes_label_names_a_forgotten_lineage(project):
    sender_slug = _seed_bucket("/p/req-sender-j")
    to = store.project_slug(project)
    first = requests.open_request(to=to, ask=ASK, why=WHY,
                                  channel="cli-agent", project_dir=sender_slug)
    second = requests.open_request(
        to=to, ask="review it again, with the new numbers", why=WHY,
        channel="cli-agent", project_dir=sender_slug, supersedes=first)
    record = requests.recipient_join(project_dir=project)[second]
    assert requests.supersedes_label(record) == first
    requests.forget_content_key(normalize.content_key(ASK),
                                project_dir=sender_slug)
    record = requests.recipient_join(project_dir=project)[second]
    assert requests.supersedes_label(record) == f"{first} (record forgotten)"


def test_supersedes_label_is_empty_when_there_is_no_lineage(project):
    sender_slug = _seed_bucket("/p/req-sender-k")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.supersedes_label(record) == ""


# ---- #694 PR 3: D3 stale derivation (recipient side, K=3) ------------------


def test_is_stale_never_surfaced_never_decays(project):
    sender_slug = _seed_bucket("/p/req-sender-stale-a")
    recipient = "/p/req-recipient-stale-a"
    q_id = requests.open_request(to=store.project_slug(recipient), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert record["surfaced"] == {}
    for n in range(5):
        _serialize(recipient, f"S-never-{n}", _iso(n + 1))
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.is_stale(record, project_dir=recipient) is False


def test_is_stale_after_three_recipient_sessions_past_the_anchor(project):
    sender_slug = _seed_bucket("/p/req-sender-stale-b")
    recipient = "/p/req-recipient-stale-b"
    q_id = requests.open_request(to=store.project_slug(recipient), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.stamp_surfaced(q_id, project_dir=recipient)
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.is_stale(record, project_dir=recipient) is False
    for n in range(requests.STALE_AFTER_SESSIONS - 1):
        _serialize(recipient, f"S-b{n}", _iso(n + 1))
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.is_stale(record, project_dir=recipient) is False  # 2 of 3
    _serialize(recipient, "S-b-last",
              _iso(requests.STALE_AFTER_SESSIONS + 1))
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.is_stale(record, project_dir=recipient) is True


def test_is_stale_never_applies_to_a_decided_state(project):
    sender_slug = _seed_bucket("/p/req-sender-stale-c")
    recipient = "/p/req-recipient-stale-c"
    q_id = requests.open_request(to=store.project_slug(recipient), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.stamp_surfaced(q_id, project_dir=recipient)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(recipient, f"S-c{n}", _iso(n + 1))
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert record["state"] == "accepted"
    assert requests.is_stale(record, project_dir=recipient) is False


def test_render_state_labels_stale_without_writing_it(project):
    sender_slug = _seed_bucket("/p/req-sender-stale-d")
    recipient = "/p/req-recipient-stale-d"
    q_id = requests.open_request(to=store.project_slug(recipient), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.stamp_surfaced(q_id, project_dir=recipient)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(recipient, f"S-d{n}", _iso(n + 1))
    before = requests.events(project_dir=recipient)
    record = requests.recipient_join(project_dir=recipient)[q_id]
    assert requests.render_state(record, project_dir=recipient) == "stale"
    assert record["state"] == "open"  # never written to disk
    assert requests.events(project_dir=recipient) == before  # no new row


def test_inbox_renderable_drops_stale_but_inbox_listing_keeps_it(project):
    sender_slug = _seed_bucket("/p/req-sender-stale-e")
    recipient = "/p/req-recipient-stale-e"
    q_id = requests.open_request(to=store.project_slug(recipient), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    requests.stamp_surfaced(q_id, project_dir=recipient)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(recipient, f"S-e{n}", _iso(n + 1))
    assert requests.inbox_renderable(project_dir=recipient)["rows"] == []
    rows = requests.inbox_listing(project_dir=recipient)
    assert rows[0]["request_id"] == q_id


# ---- #694 PR 3: sender-side verdict panel (D2/D3, K=2) ---------------------


def test_needs_verdict_surfaced_stamp_and_dedup(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-a")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record) is True
    assert requests.stamp_verdict_surfaced(q_id, project_dir=project) is True
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record) is False
    assert record["verdict_surfaced"][record["revision"]]


def test_stamp_verdict_surfaced_is_write_once_earliest_wins(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-b")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    assert requests.stamp_verdict_surfaced(q_id, project_dir=project) is True
    assert requests.stamp_verdict_surfaced(q_id, project_dir=project) is True
    rows = [r for r in requests.events(project_dir=project)
            if r.get("event") == "verdict_surfaced"]
    assert len(rows) == 2  # both written…
    record = requests.sender_join(project_dir=project)[q_id]
    assert record["verdict_surfaced"][record["revision"]] == rows[0]["ts"]  # …earliest


def test_verdict_panel_expired_after_two_sender_sessions(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-c")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    requests.stamp_verdict_surfaced(q_id, project_dir=project)
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.verdict_panel_expired(record, project_dir=project) is False
    _serialize(project, "S-vp-0", _iso(1))
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.verdict_panel_expired(record, project_dir=project) is False
    _serialize(project, "S-vp-1", _iso(requests.VERDICT_PANEL_SESSIONS + 1))
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.verdict_panel_expired(record, project_dir=project) is True


def test_verdict_panel_never_expires_before_the_first_stamp(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-d")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    record = requests.sender_join(project_dir=project)[q_id]
    assert record["verdict_surfaced"] == {}
    assert requests.verdict_panel_expired(record, project_dir=project) is False


def test_verdict_renderable_shows_decided_requests_only(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-e")
    open_id = _open(project, to=recipient_slug, ask="still open, no verdict")
    accepted_id = _open(project, to=recipient_slug,
                        ask="accepted, should show")
    requests.accept(accepted_id, channel="cli-tty", project_dir=recipient_slug)
    entry = requests.verdict_renderable(project_dir=project)
    ids = {r["request_id"] for r in entry["rows"]}
    assert ids == {accepted_id}
    assert open_id not in ids


def test_verdict_renderable_caps_and_counts_the_overflow(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-f")
    for n in range(requests.RENDER_CAP + 2):
        q_id = _open(project, to=recipient_slug,
                     ask=f"ask {n} about the release")
        requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    entry = requests.verdict_renderable(project_dir=project)
    assert len(entry["rows"]) == requests.RENDER_CAP
    assert entry["overflow"] == 2


def test_verdict_renderable_excludes_expired_verdicts(project):
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-g")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    requests.stamp_verdict_surfaced(q_id, project_dir=project)
    for n in range(requests.VERDICT_PANEL_SESSIONS + 1):
        _serialize(project, f"S-vpe-{n}", _iso(n + 1))
    entry = requests.verdict_renderable(project_dir=project)
    assert entry["rows"] == []


def test_verdict_renderable_still_visible_in_sender_join(project):
    """D3: only ambient panel attention decays — the record itself stays
    fully readable through the composer regardless of expiry."""
    recipient_slug = _seed_bucket("/p/req-recipient-verdict-h")
    q_id = _open(project, to=recipient_slug)
    requests.accept(q_id, channel="cli-tty", project_dir=recipient_slug)
    requests.stamp_verdict_surfaced(q_id, project_dir=project)
    for n in range(requests.VERDICT_PANEL_SESSIONS + 1):
        _serialize(project, f"S-vpv-{n}", _iso(n + 1))
    assert requests.verdict_renderable(project_dir=project)["rows"] == []
    record = requests.sender_join(project_dir=project)[q_id]
    assert record["state"] == "accepted"


# ---- #694 PR 3: status counts ----------------------------------------------


def test_status_counts_open_sent_and_awaiting_you(project):
    recipient_slug = _seed_bucket("/p/req-status-recipient-a")
    _open(project, to=recipient_slug, ask="still open")
    accepted_id = _open(project, to=recipient_slug, ask="decided already")
    requests.accept(accepted_id, channel="cli-tty", project_dir=recipient_slug)
    sender_slug = _seed_bucket("/p/req-status-sender-b")
    requests.open_request(to=store.project_slug(project), ask="please look",
                          why="because", channel="cli-agent",
                          project_dir=sender_slug)
    counts = requests.status_counts(project_dir=project)
    assert counts == {"open_sent": 1, "awaiting_you": 1}


def test_status_counts_zero_with_nothing_recorded(project):
    assert requests.status_counts(project_dir=project) == \
        {"open_sent": 0, "awaiting_you": 0}


def test_status_counts_unknown_project_is_zero():
    assert requests.status_counts(project_dir=None) == \
        {"open_sent": 0, "awaiting_you": 0}


def test_status_counts_include_suppressed_and_stale(project):
    """Status is a full honest count, not an attention-filtered display:
    suppressed/stale requests are still awaiting a decision."""
    recipient_slug = _seed_bucket("/p/req-status-recipient-c")
    q_id = _open(project, to=recipient_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=recipient_slug)
    assert requests.status_counts(project_dir=project) == \
        {"open_sent": 1, "awaiting_you": 0}


# ---- PR 2: `request inbox` -------------------------------------------------


def test_cli_request_inbox_shows_addressed_asks(project, recipient, capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient) == 0
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    out = capsys.readouterr().out
    assert ASK in out
    assert "From:" in out


def test_cli_request_inbox_renders_evidence(project, recipient, capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient, "--evidence", "issue:694") == 0
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Evidence: issue:694" in out


def test_cli_request_inbox_json_matches_inbox_listing(project, recipient,
                                                       capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient) == 0
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--json", "--project", OTHER])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == requests.inbox_listing(project_dir=OTHER)


def test_cli_request_inbox_empty_says_so(recipient, capsys):
    from daimon_briefing import cli
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    assert "no requests addressed to this project" in capsys.readouterr().out


def test_cli_request_inbox_shows_the_suppressed_marker(project, recipient,
                                                        monkeypatch, capsys):
    from daimon_briefing import cli
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.recipient_join(project_dir=OTHER)))
    assert cli.main(["request", "suppress", q_id, "--project", OTHER]) == 0
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    out = capsys.readouterr().out
    assert q_id in out and "suppressed" in out.lower()


def test_cli_request_inbox_shows_the_stale_label(project, recipient, capsys):
    from daimon_briefing import cli
    assert _cli_open(project, recipient) == 0
    q_id = next(iter(requests.recipient_join(project_dir=OTHER)))
    requests.stamp_surfaced(q_id, project_dir=OTHER)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(OTHER, f"S-cli-stale-{n}", _iso(n + 1))
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    out = capsys.readouterr().out
    assert "stale" in out
    assert q_id in out


def test_cli_request_list_shows_the_stale_label_when_self_addressed(
        project, capsys):
    from daimon_briefing import cli
    my_slug = store.project_slug(project)
    rc = cli.main(["request", "open", "--to", project, "--ask", ASK,
                   "--why", WHY, "--by", "agent", "--anyway",
                   "--project", project])
    assert rc == 0
    q_id = next(iter(requests.records(project_dir=project)))
    requests.stamp_surfaced(q_id, project_dir=my_slug)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(project, f"S-list-stale-{n}", _iso(n + 1))
    capsys.readouterr()
    rc = cli.main(["request", "list", "--project", project])
    assert rc == 0
    out = capsys.readouterr().out
    assert "stale" in out


def test_cli_request_inbox_renders_the_forgotten_supersedes_lineage(
        project, recipient, capsys):
    from daimon_briefing import cli, normalize
    assert _cli_open(project, recipient) == 0
    first = next(iter(requests.recipient_join(project_dir=OTHER)))
    second = requests.open_request(
        to=store.project_slug(OTHER), ask="review it again, new numbers",
        why=WHY, channel="cli-agent", project_dir=project, supersedes=first)
    requests.forget_content_key(normalize.content_key(ASK),
                                project_dir=project)
    capsys.readouterr()
    rc = cli.main(["request", "inbox", "--project", OTHER])
    assert rc == 0
    out = capsys.readouterr().out
    assert second in out
    assert f"{first} (record forgotten)" in out


# ---- live delivery (#756, PR 1 — the ledger layer) -------------------------
#
# The design's invariant: the ledger stays store-and-forward and only the
# render surface gains a second door. Delivery adds zero assertion power, so
# `delivered` is an attention row exactly like `surfaced` — mechanical
# channel, never moves the record's age, never touches its state. What it
# does NOT share with `surfaced` is the dedup key: a brief renders a request
# once per revision epoch, but delivery renders it once per epoch PER LIVE
# SESSION, because two sessions running side by side each need the ask.


def test_delivered_is_in_the_event_vocabulary():
    """The second deliberate widening (after #694 PR 3's `done_verified`).
    An older reader drops the unknown row and keeps rendering the request as
    undelivered — a duplicate nudge, never a swallowed ask."""
    assert "delivered" in requests.EVENTS


def test_delivered_rows_fold_per_session_within_a_revision_epoch(project):
    """Write-once per (request id, revision epoch, session id): two live
    sessions each receive the ask, and a second stamp for a session already
    delivered to is absorbed earliest-wins."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    base = rows[0]["order"]

    def at(seconds, session):
        row = requests._stamp("delivered", q_id, "mechanical",
                              now_ns=base + seconds * 10 ** 9)
        row["session"] = session
        return row

    rows.append(at(10, "S-alpha"))
    rows.append(at(20, "S-alpha"))
    rows.append(at(30, "S-beta"))
    record = requests.fold(rows)[q_id]
    assert set(record["delivered"]) == {0}
    assert set(record["delivered"][0]) == {"S-alpha", "S-beta"}
    # Earliest row for a session wins: the 20s stamp never overwrote the 10s.
    assert record["delivered"][0]["S-alpha"] == requests._ts(base + 10 * 10 ** 9)


def test_a_revise_reopens_delivery_for_every_session(project):
    """D3: a revise clears the stamp so the sharpened ask re-delivers — the
    same epoch mechanism `surfaced` uses, so a session that already saw the
    old ask sees the new one."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    base = rows[0]["order"]

    def at(seconds, session):
        row = requests._stamp("delivered", q_id, "mechanical",
                              now_ns=base + seconds * 10 ** 9)
        row["session"] = session
        return row

    rows.append(at(10, "S-alpha"))
    revised = requests._stamp("revised", q_id, "cli-agent",
                              now_ns=base + 20 * 10 ** 9)
    revised["ask"] = "the sharpened ask, after a revise"
    rows.append(revised)
    rows.append(at(30, "S-alpha"))
    record = requests.fold(rows)[q_id]
    assert set(record["delivered"]) == {0, 1}
    assert record["delivered"][1]["S-alpha"] == requests._ts(base + 30 * 10 ** 9)


def test_a_delivered_row_without_a_session_is_inert(project):
    """The session id IS the dedup key. A row that lost it (hand-edited, or
    written by a future path that forgot) must not silently dedup every
    session at once — it folds away instead."""
    q_id = _open(project)
    rows = requests.events(project_dir=project)
    rows.append(requests._stamp("delivered", q_id, "mechanical"))
    assert requests.fold(rows)[q_id]["delivered"] == {}


def test_delivered_never_moves_the_records_age(project):
    """An attention row, like `surfaced`: a session that merely received the
    nudge must not make an untouched ask sort as freshly updated."""
    q_id = _open(project)
    before = requests.get(q_id, project_dir=project)["updated_at"]
    assert requests.stamp_delivered(q_id, "S-alpha", project_dir=project)
    after = requests.get(q_id, project_dir=project)
    assert after["updated_at"] == before
    assert after["state"] == "open"


def test_needs_delivered_stamp_is_per_session(project):
    sender_slug = _seed_bucket("/p/req-sender-deliver")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender_slug)
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_delivered_stamp(record, "S-alpha") is True
    assert requests.stamp_delivered(q_id, "S-alpha", project_dir=project)
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_delivered_stamp(record, "S-alpha") is False
    # A second live session still owes the ask.
    assert requests.needs_delivered_stamp(record, "S-beta") is True


def test_stamp_delivered_carries_the_session_and_stays_mechanical(project):
    q_id = _open(project)
    assert requests.stamp_delivered(q_id, "S-alpha", project_dir=project)
    row = [r for r in _rows(project) if r["event"] == "delivered"][0]
    assert row["session"] == "S-alpha"
    assert row["channel"] == "mechanical"
    assert row["authority"] == requests.CHANNEL_AUTHORITY["mechanical"]


def test_stamp_delivered_refuses_a_blank_session(project):
    q_id = _open(project)
    with pytest.raises(requests.RequestError):
        requests.stamp_delivered(q_id, "   ", project_dir=project)


# ---- live delivery (#756, PR 2 — selection and the hook backend) ----------
#
# What may be delivered is exactly what the brief panel would have rendered:
# undecided, unsuppressed, not stale. The design's whole claim is that
# delivery is a second door onto the same record, so the day the panel's
# filter and delivery's filter disagree, one of them is showing an ask the
# other decided was not worth attention.


def test_deliverable_is_the_panel_filter_plus_this_session(project):
    sender = _seed_bucket("/p/deliver-sender-a")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender)
    rows = requests.deliverable("S-alpha", project_dir=project)["rows"]
    assert [r["request_id"] for r in rows] == [q_id]
    # Delivered once, gone for THIS session, still owed to the next one.
    assert requests.stamp_delivered(q_id, "S-alpha", project_dir=project)
    assert requests.deliverable("S-alpha", project_dir=project)["rows"] == []
    assert [r["request_id"] for r in
            requests.deliverable("S-beta", project_dir=project)["rows"]] == [q_id]


def test_deliverable_excludes_decided_suppressed_and_stale(project):
    sender = _seed_bucket("/p/deliver-sender-b")

    def ask(text):
        return requests.open_request(to=store.project_slug(project), ask=text,
                                     why=WHY, channel="cli-agent",
                                     project_dir=sender)

    decided = ask("the decided ask, already answered")
    requests.accept(decided, channel="cli-tty", project_dir=project)
    suppressed = ask("the suppressed ask, hidden from the panel")
    requests.suppress(suppressed, channel="cli-tty", project_dir=project)
    stale = ask("the stale ask, whose attention decayed")
    requests.stamp_surfaced(stale, project_dir=project)
    for n in range(requests.STALE_AFTER_SESSIONS + 1):
        _serialize(project, f"S-deliver-stale-{n}", _iso(n + 1))
    live = ask("the live ask, still owed a decision")
    ids = {r["request_id"] for r in
           requests.deliverable("S-alpha", project_dir=project)["rows"]}
    assert ids == {live}


def test_deliverable_re_offers_a_revised_ask_to_a_delivered_session(project):
    sender = _seed_bucket("/p/deliver-sender-c")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender)
    assert requests.stamp_delivered(q_id, "S-alpha", project_dir=project)
    assert requests.deliverable("S-alpha", project_dir=project)["rows"] == []
    requests.revise(q_id, channel="cli-agent", ask="the sharpened ask",
                    project_dir=sender)
    assert [r["request_id"] for r in
            requests.deliverable("S-alpha", project_dir=project)["rows"]] == [q_id]


def test_deliverable_caps_at_the_render_budget(project):
    sender = _seed_bucket("/p/deliver-sender-d")
    for n in range(requests.RENDER_CAP + 2):
        requests.open_request(to=store.project_slug(project),
                              ask=f"ask number {n} about the release",
                              why=WHY, channel="cli-agent", project_dir=sender)
    assert len(requests.deliverable("S-alpha", project_dir=project)["rows"]) \
        == requests.RENDER_CAP


def test_deliverable_never_offers_this_projects_own_outgoing_ask(project):
    _open(project)
    assert requests.deliverable("S-alpha", project_dir=project)["rows"] == []


def test_deliverable_without_a_session_offers_nothing(project):
    """The session id is half the write-once key. With no session there is
    nothing to dedup against, so delivering would re-nudge forever."""
    sender = _seed_bucket("/p/deliver-sender-e")
    requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=sender)
    assert requests.deliverable("", project_dir=project)["rows"] == []


# ---- the hook backend -----------------------------------------------------


def _inject(project, session="S-alpha"):
    from daimon_briefing import cli
    return cli.main(["request-inject", "--project", project,
                     "--session", session])


def test_request_inject_is_silent_while_the_flag_is_off(
        project, capsys, monkeypatch):
    """Default OFF: briefing-only is the correct posture for short sessions
    and the noise budget errs toward silence."""
    monkeypatch.delenv("DAIMON_LIVE_DELIVERY", raising=False)
    sender = _seed_bucket("/p/deliver-sender-f")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender)
    capsys.readouterr()
    assert _inject(project) == 0
    assert capsys.readouterr().out == ""
    # Nothing stamped either: an undelivered ask must stay undelivered.
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_delivered_stamp(record, "S-alpha") is True


def test_request_inject_renders_the_ask_and_stamps_it(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-sender-g")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK,
                                 why=WHY, channel="cli-agent",
                                 project_dir=sender)
    capsys.readouterr()
    assert _inject(project) == 0
    out = capsys.readouterr().out
    assert q_id in out
    assert ASK in out
    record = requests.recipient_join(project_dir=project)[q_id]
    assert requests.needs_delivered_stamp(record, "S-alpha") is False


def test_request_inject_delivers_once_per_session(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-sender-h")
    requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=sender)
    capsys.readouterr()
    assert _inject(project) == 0
    assert capsys.readouterr().out.strip()
    assert _inject(project) == 0
    assert capsys.readouterr().out == ""
    # A different live session still receives it.
    assert _inject(project, session="S-beta") == 0
    assert capsys.readouterr().out.strip()


def test_request_inject_says_how_to_act_on_the_ask(
        project, capsys, monkeypatch):
    """The nudge is useless without the verb that answers it, and the verbs
    that decide are human-only — the line must not imply the agent can."""
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-sender-i")
    requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=sender)
    capsys.readouterr()
    assert _inject(project) == 0
    assert "daimon request inbox" in capsys.readouterr().out


def test_request_inject_returns_zero_when_the_ledger_is_unreadable(
        project, capsys, monkeypatch):
    """It sits on the user's per-prompt critical path: a broken ledger must
    cost a nudge, never the prompt (fail-open, like recall-inject)."""
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    monkeypatch.setattr(requests, "deliverable",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
    capsys.readouterr()
    assert _inject(project) == 0
    assert capsys.readouterr().out == ""


def test_request_inject_needs_no_session_to_stay_quiet(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-sender-j")
    requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                          channel="cli-agent", project_dir=sender)
    from daimon_briefing import cli
    capsys.readouterr()
    assert cli.main(["request-inject", "--project", project]) == 0
    assert capsys.readouterr().out == ""


# ---- #798: `request list` must fold the sender lane, like the panels do ------
#
# A request's rows span two buckets: `opened` in the sender's, the verdict in the
# recipient's. `inbox` joins across buckets (recipient_join) and the verdict panel
# joins across buckets (sender_join), but `listing` folded only the local bucket, so
# every ask a project SENT read `open` forever after it was decided.


def _listed(project, q_id):
    return {r["request_id"]: r for r in requests.listing(project_dir=project)}[q_id]


def test_listing_reports_the_recipients_verdict(project):
    """#798: the surface the verdict panel's overflow line points at
    ("+N more decided — daimon request list") must be able to show them."""
    recipient_slug = _seed_bucket("/p/req-list-verdict")
    q_id = _open(project, to=recipient_slug)
    requests.done(q_id, channel="cli-agent", evidence="shipped in abc1234",
                  project_dir=recipient_slug)
    assert _listed(project, q_id)["state"] == "done"


def test_listing_reports_a_rejection_too(project):
    recipient_slug = _seed_bucket("/p/req-list-reject")
    q_id = _open(project, to=recipient_slug)
    requests.reject(q_id, channel="cli-tty", note="not this quarter",
                    project_dir=recipient_slug)
    assert _listed(project, q_id)["state"] == "rejected"


def test_listing_does_not_leak_the_recipients_suppression_d5(project):
    """D5 runs the OTHER way here. Joining the recipient's rows must not teach the
    sender that its ask was muted from the recipient's panel: it reads undecided and
    goes stale on the normal schedule. The filter belongs on FOREIGN rows only."""
    recipient_slug = _seed_bucket("/p/req-list-suppressed")
    q_id = _open(project, to=recipient_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=recipient_slug)
    listed = _listed(project, q_id)
    assert listed["suppressed"] is False
    assert listed["state"] == "open"


def test_listing_keeps_the_sent_lane_out_of_the_inbox(project):
    """The property most likely to regress when the data source moves. `listing` is
    the SENT lane: an ask addressed TO this project, opened elsewhere, has never
    appeared here (verified against the pre-change behaviour) and must not start
    appearing once foreign rows are joined in."""
    sender_slug = _seed_bucket("/p/req-list-inbound")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                                 channel="cli-agent", project_dir=sender_slug)
    requests.suppress(q_id, channel="cli-tty", project_dir=project)
    assert q_id not in {r["request_id"] for r in requests.listing(project_dir=project)}
    # ...and it is still reachable where it belongs, the inbox.
    assert requests.recipient_join(project_dir=project)[q_id]["suppressed"] is True


# ---- #800: the live nudge must report what its cap withheld ----------------
#
# `renderable()` returns {"rows", "overflow"} because, in its own words,
# silently dropping an addressed ask is the one failure this feature cannot
# have. `deliverable()` inherited that ordering and cap and dropped the
# overflow signal that made the cap safe.


def _addressed(project, sender, n):
    return [requests.open_request(to=store.project_slug(project), ask=f"ask {i}",
                                  why=WHY, channel="cli-agent", project_dir=sender)
            for i in range(n)]


def test_deliverable_reports_what_the_cap_withheld(project):
    sender = _seed_bucket("/p/deliver-overflow-a")
    _addressed(project, sender, requests.RENDER_CAP + 2)
    entry = requests.deliverable("S-alpha", project_dir=project)
    assert len(entry["rows"]) == requests.RENDER_CAP
    assert entry["overflow"] == 2


def test_deliverable_overflow_is_zero_when_nothing_is_withheld(project):
    sender = _seed_bucket("/p/deliver-overflow-b")
    _addressed(project, sender, 1)
    entry = requests.deliverable("S-alpha", project_dir=project)
    assert len(entry["rows"]) == 1 and entry["overflow"] == 0


def test_deliverable_overflow_counts_after_dedup_not_before(project):
    """The dedup filter runs BEFORE the cap, deliberately, so already-delivered
    asks cannot hide an unseen one. The count has to be taken after that same
    filter or it reports asks this session has already been shown."""
    sender = _seed_bucket("/p/deliver-overflow-c")
    ids = _addressed(project, sender, requests.RENDER_CAP + 2)
    # Show this session everything except the two most recent.
    for q in ids[:requests.RENDER_CAP]:
        requests.stamp_delivered(q, "S-alpha", project_dir=project)
    entry = requests.deliverable("S-alpha", project_dir=project)
    assert len(entry["rows"]) == 2, "only the undelivered ones remain"
    assert entry["overflow"] == 0, "nothing is withheld once dedup has run"


def test_inject_names_the_ask_it_withheld(project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-overflow-d")
    _addressed(project, sender, requests.RENDER_CAP + 2)
    assert _inject(project) == 0
    out = capsys.readouterr().out
    assert "+2 more waiting" in out, out


def test_inject_adds_no_overflow_line_when_it_withheld_nothing(
        project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    sender = _seed_bucket("/p/deliver-overflow-e")
    _addressed(project, sender, 1)
    assert _inject(project) == 0
    out = capsys.readouterr().out
    assert "more waiting" not in out, out


# ---- #803: a second verdict must get its own first look --------------------
#
# The sender stamp was write-once per RECORD, on the premise that "a verdict is
# terminal once it lands". needs-info is a verdict state and is NOT terminal:
# answering it with `revise` and receiving a real verdict afterwards is the
# ordinary negotiation path. The decay clock started on the needs-info and kept
# running, so the acceptance that followed expired before it was ever shown.


def _sender_session(project, sid, created):
    store.write_checkpoint(sid, {
        "session_id": sid, "created": created,
        "working_context": {"recent_decisions": [
            {"text": "something happened", "trust": "inferred"}]},
    }, project_dir=project)


def _fut(minutes):
    import datetime
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_a_verdict_after_a_revise_gets_its_own_first_look(project):
    """#803: needs-info, answered, then accepted. The acceptance must still be
    renderable after VERDICT_PANEL_SESSIONS later sender sessions, because its
    own epoch has never been surfaced."""
    recipient = _seed_bucket("/p/verdict-epoch-a")
    _sender_session(project, "S-before", _fut(-60))
    q_id = _open(project, to=recipient)

    requests.needs_info(q_id, channel="cli-tty", note="which release?",
                        project_dir=recipient)
    assert requests.stamp_verdict_surfaced(q_id, project_dir=project)

    requests.revise(q_id, channel="cli-agent", why="clarified",
                    project_dir=project)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)

    for n in range(requests.VERDICT_PANEL_SESSIONS + 1):
        _sender_session(project, f"S-after-{n}", _fut(10 * (n + 1)))

    record = requests.sender_join(project_dir=project)[q_id]
    assert record["state"] == "accepted"
    assert not requests.verdict_panel_expired(record, project_dir=project)
    assert [r["request_id"] for r in
            requests.verdict_renderable(project_dir=project)["rows"]] == [q_id]


def test_the_new_epoch_is_owed_a_stamp_even_though_an_older_one_exists(project):
    recipient = _seed_bucket("/p/verdict-epoch-b")
    q_id = _open(project, to=recipient)
    requests.needs_info(q_id, channel="cli-tty", note="?", project_dir=recipient)
    requests.stamp_verdict_surfaced(q_id, project_dir=project)
    record = requests.sender_join(project_dir=project)[q_id]
    assert not requests.needs_verdict_surfaced_stamp(record), "epoch 0 is stamped"

    requests.revise(q_id, channel="cli-agent", why="clarified",
                    project_dir=project)
    requests.reject(q_id, channel="cli-tty", note="no", project_dir=recipient)
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record), \
        "the rejection is a new epoch and is owed its own first look"


def test_the_same_epoch_is_still_stamped_only_once(project):
    """The write-once property that made the original shape correct must
    survive: within one epoch, a second stamp is not owed."""
    recipient = _seed_bucket("/p/verdict-epoch-c")
    q_id = _open(project, to=recipient)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)
    requests.stamp_verdict_surfaced(q_id, project_dir=project)
    record = requests.sender_join(project_dir=project)[q_id]
    assert not requests.needs_verdict_surfaced_stamp(record)


# ---- #801: the sender learns a verdict at its next turn boundary ------------
#
# Live delivery was one-directional. A recipient learned about a new ask on its
# next prompt turn; a sender learned the verdict only at its next SessionStart,
# because `deliverable()` reads recipient_join and a verdict on an ask this
# project SENT is structurally unreachable from that path.


def test_verdict_deliverable_offers_a_verdict_on_an_ask_we_sent(project):
    recipient = _seed_bucket("/p/verdict-live-a")
    q_id = _open(project, to=recipient)
    requests.reject(q_id, channel="cli-tty", note="not this quarter",
                    project_dir=recipient)
    entry = requests.verdict_deliverable("S-alpha", project_dir=project)
    assert [r["request_id"] for r in entry["rows"]] == [q_id]
    assert entry["rows"][0]["state"] == "rejected"


def test_verdict_deliverable_is_write_once_per_session(project):
    recipient = _seed_bucket("/p/verdict-live-b")
    q_id = _open(project, to=recipient)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)
    assert requests.verdict_deliverable("S-alpha", project_dir=project)["rows"]
    assert requests.stamp_verdict_delivered(q_id, "S-alpha", project_dir=project)
    assert requests.verdict_deliverable("S-alpha", project_dir=project)["rows"] == []
    # …and still owed to a session that never saw it.
    assert [r["request_id"] for r in
            requests.verdict_deliverable("S-beta", project_dir=project)["rows"]] == [q_id]


def test_verdict_deliverable_re_offers_after_a_revise_opens_a_new_epoch(project):
    """The #803 key, reused: needs-info delivered, answered, then rejected. The
    rejection is a new epoch and is owed its own delivery."""
    recipient = _seed_bucket("/p/verdict-live-c")
    q_id = _open(project, to=recipient)
    requests.needs_info(q_id, channel="cli-tty", note="?", project_dir=recipient)
    requests.stamp_verdict_delivered(q_id, "S-alpha", project_dir=project)
    assert requests.verdict_deliverable("S-alpha", project_dir=project)["rows"] == []
    requests.revise(q_id, channel="cli-agent", why="clarified", project_dir=project)
    requests.reject(q_id, channel="cli-tty", note="no", project_dir=recipient)
    assert [r["request_id"] for r in
            requests.verdict_deliverable("S-alpha", project_dir=project)["rows"]] == [q_id]


def test_verdict_deliverable_never_offers_an_ask_addressed_to_us(project):
    """The sender lane is the sender lane: an inbound ask, even once decided
    here, belongs to the recipient path."""
    sender = _seed_bucket("/p/verdict-live-d")
    q_id = requests.open_request(to=store.project_slug(project), ask=ASK, why=WHY,
                                 channel="cli-agent", project_dir=sender)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=project)
    assert requests.verdict_deliverable("S-alpha", project_dir=project)["rows"] == []


def test_verdict_deliverable_offers_nothing_while_undecided(project):
    recipient = _seed_bucket("/p/verdict-live-e")
    _open(project, to=recipient)
    assert requests.verdict_deliverable("S-alpha", project_dir=project)["rows"] == []


def test_verdict_deliverable_without_a_session_offers_nothing(project):
    recipient = _seed_bucket("/p/verdict-live-f")
    q_id = _open(project, to=recipient)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)
    assert requests.verdict_deliverable("", project_dir=project)["rows"] == []


def test_verdict_deliverable_reports_what_the_cap_withheld(project):
    recipient = _seed_bucket("/p/verdict-live-g")
    for i in range(requests.RENDER_CAP + 2):
        q = requests.open_request(to=recipient, ask=f"ask {i}", why=WHY,
                                  channel="cli-agent", project_dir=project)
        requests.accept(q, channel="cli-tty", note="ok", project_dir=recipient)
    entry = requests.verdict_deliverable("S-alpha", project_dir=project)
    assert len(entry["rows"]) == requests.RENDER_CAP
    assert entry["overflow"] == 2


def test_the_live_stamp_does_not_suppress_the_brief_stamp(project):
    """Two surfaces, two stamps. A live nudge must not make the brief think it
    already rendered the verdict card, or the sender loses the fuller view."""
    recipient = _seed_bucket("/p/verdict-live-h")
    q_id = _open(project, to=recipient)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)
    requests.stamp_verdict_delivered(q_id, "S-alpha", project_dir=project)
    record = requests.sender_join(project_dir=project)[q_id]
    assert requests.needs_verdict_surfaced_stamp(record), \
        "the brief still owes this verdict its card"


def test_inject_delivers_a_verdict_to_the_sender(project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    recipient = _seed_bucket("/p/verdict-live-i")
    q_id = _open(project, to=recipient)
    requests.reject(q_id, channel="cli-tty", note="not this quarter",
                    project_dir=recipient)
    assert _inject(project) == 0
    out = capsys.readouterr().out
    assert q_id in out and "rejected" in out, out
    # write-once: the same session is not told twice
    assert _inject(project) == 0
    assert q_id not in capsys.readouterr().out


def test_stamp_verdict_delivered_requires_a_session_id(project):
    """The session id is half the dedup key. A stamp without one would read as
    "delivered" to sessions that never saw the verdict, so it refuses rather
    than writing a row that silences everybody."""
    recipient = _seed_bucket("/p/verdict-live-j")
    q_id = _open(project, to=recipient)
    requests.accept(q_id, channel="cli-tty", note="ok", project_dir=recipient)
    with pytest.raises(requests.RequestError):
        requests.stamp_verdict_delivered(q_id, "", project_dir=project)
    # …and nothing was recorded, so the verdict is still owed.
    assert [r["request_id"] for r in
            requests.verdict_deliverable("S-alpha", project_dir=project)["rows"]] == [q_id]


def test_inject_names_the_verdict_it_withheld(project, capsys, monkeypatch):
    monkeypatch.setenv("DAIMON_LIVE_DELIVERY", "1")
    recipient = _seed_bucket("/p/verdict-live-k")
    for i in range(requests.RENDER_CAP + 2):
        q = requests.open_request(to=recipient, ask=f"ask {i}", why=WHY,
                                  channel="cli-agent", project_dir=project)
        requests.accept(q, channel="cli-tty", note="ok", project_dir=recipient)
    assert _inject(project) == 0
    out = capsys.readouterr().out
    assert "+2 more decided" in out, out
