"""#694 PR 2: the recipient-side request panel — asks addressed to this
project, surfaced in its own briefing. Mirrors test_ruling_briefing.py's
shape: fail-open, always present, never budget-dropped. The CLI-only gate
(worldcheck_project) is exercised in test_cli_brief_requests.py, not here —
this file drives briefing.request_panel_lines/render directly.
"""

import json

from daimon_briefing import briefing, config, requests, store

RECIPIENT = "/p/req-brief-recipient"
SENDER = "/p/req-brief-sender"


def _seed_sender(session="S-req-sender"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "the sender shipped something", "trust": "inferred"}]},
    }, project_dir=SENDER)
    return store.project_slug(SENDER)


def _ask(sender_slug, ask="publish the schema", channel="cli-agent", **kw):
    return requests.open_request(
        to=store.project_slug(RECIPIENT), ask=ask,
        why="the client needs it", channel=channel,
        project_dir=sender_slug, **kw)


def test_panel_lists_asks_addressed_to_this_project(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert any("publish the schema" in ln for ln in lines)


def test_panel_names_the_foreign_sender(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert any("req-brief-sender" in ln for ln in lines)


def test_panel_marks_blocking(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender, blocking=True)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert any("blocking" in ln.lower() for ln in lines)


def test_panel_marks_an_info_ask(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender, channel="cli-tty", kind="info")
    lines = briefing.request_panel_lines(RECIPIENT)
    assert any("[info]" in ln for ln in lines)


def test_panel_no_marker_for_a_work_ask(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert not any("[info]" in ln for ln in lines)


def test_panel_marks_a_pending_completion_claim(tmp_checkpoint_dir):
    """#978: an agent's completion claim on a work ask nobody accepted must
    still surface in the recipient's own panel — placed after the truncated
    ask like the info/blocking markers, id untouched."""
    sender = _seed_sender()
    q_id = _ask(sender)
    requests.done(q_id, channel="cli-agent", evidence="shipped in abc123",
                  project_dir=RECIPIENT)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert any("[done claimed]" in ln for ln in lines)
    assert any(q_id in ln for ln in lines)


def test_panel_no_claim_marker_for_an_undecided_ask(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender)
    lines = briefing.request_panel_lines(RECIPIENT)
    assert not any("[done claimed]" in ln for ln in lines)


def test_panel_legacy_row_renders_without_a_kind_marker(tmp_checkpoint_dir):
    """A record minted before #961 carries no `kind` field at all; the panel
    must render it exactly like an ordinary `work` ask — no marker, no
    crash."""
    sender = _seed_sender()
    q_id = _ask(sender, channel="cli-tty", kind="info")
    path = (config.checkpoint_dir() / store.project_slug(sender)
            / "requests.jsonl")
    rows = [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    del rows[0]["kind"]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                    encoding="utf-8")
    lines = briefing.request_panel_lines(RECIPIENT)
    # Positive anchor: the record is actually rendered here, not just absent
    # from a panel that happens to have nothing in it.
    assert any(q_id in ln for ln in lines)
    assert not any("[info]" in ln for ln in lines)


def test_panel_never_reads_kind_off_a_raw_row(tmp_checkpoint_dir):
    """THE FOLD RULE THROUGH THE SURFACE: a raw `opened` row appended on a
    non-human channel with `kind="info"` folds to `work`, and the panel must
    show exactly what the fold says, never the forged raw value. Appended
    directly because `open_request` refuses this combination at the write
    boundary."""
    sender = _seed_sender()
    row = requests._stamp("opened", "q-0123456789ab", "cli-agent")
    row.update({"to": store.project_slug(RECIPIENT),
               "ask": "publish the schema", "why": "the client needs it",
               "kind": "info"})
    assert requests.append(row, project_dir=sender)
    lines = briefing.request_panel_lines(RECIPIENT)
    # Positive anchor: an empty line list would also satisfy "no marker".
    assert any("q-0123456789ab" in ln for ln in lines)
    assert not any("[info]" in ln for ln in lines)


def test_panel_never_silently_truncates_over_cap(tmp_checkpoint_dir):
    sender = _seed_sender()
    for n in range(requests.RENDER_CAP + 2):
        _ask(sender, ask=f"ask number {n} about the release")
    lines = briefing.request_panel_lines(RECIPIENT)
    body = "\n".join(lines)
    assert "+2 more waiting" in body
    assert "daimon request inbox" in body


def test_panel_excludes_suppressed(tmp_checkpoint_dir):
    sender = _seed_sender()
    q_id = _ask(sender)
    requests.suppress(q_id, channel="cli-tty", project_dir=RECIPIENT)
    assert briefing.request_panel_lines(RECIPIENT) == []


def test_panel_empty_when_no_project_dir(tmp_checkpoint_dir):
    sender = _seed_sender()
    _ask(sender)
    assert briefing.request_panel_lines(None) == []


def test_panel_empty_with_nothing_addressed(tmp_checkpoint_dir):
    assert briefing.request_panel_lines(RECIPIENT) == []


def test_panel_fails_open_on_a_broken_composer(tmp_checkpoint_dir, monkeypatch):
    def boom(project_dir=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(requests, "inbox_renderable", boom)
    sender = _seed_sender()
    _ask(sender)
    assert briefing.request_panel_lines(RECIPIENT) == []


def test_panel_survives_budget_pressure(tmp_checkpoint_dir, sample_checkpoint,
                                        monkeypatch):
    sender = _seed_sender()
    _ask(sender)
    monkeypatch.setenv("DAIMON_BRIEF_MAX_TOKENS", "40")
    out = briefing.render(sample_checkpoint, project_dir=RECIPIENT,
                          worldcheck_project=RECIPIENT)
    assert "publish the schema" in out


def test_panel_rides_the_llm_opt_in_render_path(tmp_checkpoint_dir,
                                                sample_checkpoint,
                                                monkeypatch):
    # #694: the request panel, like #693's rulings, is prepended verbatim
    # OUTSIDE the LLM's narrated text on the opt-in path — never re-narrated,
    # never trusted to a generative pass. Mirrors test_briefing.py's
    # _llm_briefing_env + faithful-quote idiom for the rulings equivalent.
    from daimon_briefing import llm
    sender = _seed_sender()
    _ask(sender)
    monkeypatch.setenv("DAIMON_LLM_BRIEFING", "1")
    monkeypatch.setenv("DAIMON_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("DAIMON_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("DAIMON_LLM_MODEL", "test-model")
    faithful = (
        'Verify: "I\'ll merge it myself later from the GitHub UI". '
        'Open: "do we chunk below 1200 lines or single-pass?". '
        'Decided: "we adopt the D-007 prompt for the serializer".'
    )
    monkeypatch.setattr(llm, "chat", lambda *a, **k: faithful)
    out = briefing.render(sample_checkpoint, project_dir=RECIPIENT,
                          worldcheck_project=RECIPIENT)
    assert "publish the schema" in out
    assert faithful in out


def test_render_with_no_checkpoint_still_shows_the_panel(tmp_checkpoint_dir):
    # #693's rulings precedent: a day-one ratification must not wait for a
    # session to end. Same posture here — the FIRST thing this project ever
    # gets might be an incoming ask, before it has serialized anything.
    sender = _seed_sender()
    _ask(sender)
    out = briefing.render(None, project_dir=RECIPIENT,
                          worldcheck_project=RECIPIENT)
    assert out is not None
    assert "publish the schema" in out


def test_full_cap_worst_case_stays_within_budget_share(tmp_checkpoint_dir):
    # Worst case pinned by test: RENDER_CAP asks at the max text length must
    # still cost only a small, bounded share of the default budget.
    sender = _seed_sender()
    for n in range(requests.RENDER_CAP):
        _ask(sender, ask=f"n{n} " + "x" * (requests._MAX_TEXT - 4))
    lines = briefing.request_panel_lines(RECIPIENT)
    section = "\n".join(lines)
    assert briefing.estimate_tokens(section) <= 300  # 10% of the 3000 default
    assert briefing.estimate_tokens(section) >= 100  # the share is real
