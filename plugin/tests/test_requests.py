"""Cross-project request ledger (#694, PR 1 — the object).

A request is a record in the SENDER's bucket: an ask addressed to another
project by slug. Verdicts are human-only, rejection is sticky, revision is
capped, and forget reaches the prose by value. The join across buckets (the
inbox, the briefing panel, the surfaced stamps) is PR 2/3 — what ships here
is the object, its fold, and its deletion contract.
"""

import json

import pytest

from daimon_briefing import config, normalize, redact, requests, store


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
    assert after["verdict_surfaced_at"]


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
    an older reader drops are rows it silently re-renders as undecided."""
    assert set(requests.EVENTS) == {
        "opened", "revised", "surfaced", "verdict_surfaced",
        "needs_info", "accepted", "rejected", "done", "suppressed"}


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
