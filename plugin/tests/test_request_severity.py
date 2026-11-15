"""#1348: a sender-set severity on a request.

One table (`requests.SEVERITIES`) owns the scale. The fold sets the default in
exactly one place, the sender changes it with its own event (`severity_set`,
not `revised`), and the recipient has no way to record one."""

import json
import time

import pytest

from daimon_briefing import config, requests, store
from daimon_briefing.surfaces import Writer

from ._fold import fold_rows

ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"
REASON = "the release waits on this"


@pytest.fixture
def project(tmp_checkpoint_dir):
    return "/p/sender"


def _seed_bucket(project_dir, session="S-bucket"):
    store.write_checkpoint(session, {
        "session_id": session, "created": "2026-08-16T00:00:00Z",
        "working_context": {"recent_decisions": [
            {"text": "something happened here", "trust": "inferred"}]},
    }, project_dir=project_dir, writer=Writer.HUMAN)
    return store.project_slug(project_dir)


def _sent(project, rdir="/p/sev-recipient", ask=ASK, channel="cli-agent",
          **kwargs):
    slug = _seed_bucket(rdir)
    q_id = requests.open_request(to=slug, ask=ask, why=WHY, channel=channel,
                                 project_dir=project, **kwargs)
    return q_id, rdir


def _rows(project):
    path = (config.checkpoint_dir() / store.project_slug(project)
            / "requests.jsonl")
    return [json.loads(ln) for ln in
            path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _forge(event, q_id, project_dir, *, channel="cli-tty", now_ns=None,
           **extra):
    row = requests._stamp(event, q_id, channel,
                          now_ns=now_ns or time.time_ns())
    row.update(extra)
    assert requests.append(row, project_dir=project_dir, writer=Writer.HUMAN)
    return row["event_id"]


def _sent_view(project, q_id):
    return requests.sent_record(q_id, project_dir=project)


# ---- the table --------------------------------------------------------------


def test_the_scale_is_one_ordered_table():
    assert requests.SEVERITIES == ("critical", "high", "normal", "low")
    assert requests.DEFAULT_SEVERITY == "normal"
    ranks = [requests.severity_rank(level) for level in requests.SEVERITIES]
    assert ranks == sorted(ranks) and len(set(ranks)) == 4
    with pytest.raises(KeyError):
        requests.severity_rank("urgent")
    assert requests.SEVERITY_REASON_CHARS == 80


def test_severity_set_is_a_sender_event_in_every_enumerator():
    assert "severity_set" in requests.EVENTS
    assert requests.EVENT_ROLE["severity_set"] == requests.ROLE_SENDER
    assert requests._EVENT_RANK["severity_set"] == requests._EVENT_RANK[
        "revised"]
    assert "severity_set" not in requests._PRE_ROLE_RULE_EVENTS
    for reason in ("severity_set", "severity_unreadable", "severity_settled"):
        assert reason in requests._EFFECT_REASONS


# ---- the default lives in the fold ------------------------------------------


def test_every_record_folds_with_an_explicit_normal(project):
    q_id, _ = _sent(project)
    record = _sent_view(project, q_id)
    assert record["severity"] == "normal"
    assert record["severity_reason"] == ""
    local = requests.get(q_id, project_dir=project)
    assert (local["severity"], local["severity_reason"]) == ("normal", "")


def test_a_normal_request_writes_the_same_row_bytes_as_before(project):
    _sent(project)
    row = _rows(project)[0]
    assert "severity" not in row and "severity_reason" not in row


def test_naming_normal_at_open_writes_nothing(project):
    _sent(project, severity="normal")
    row = _rows(project)[0]
    assert "severity" not in row and "severity_reason" not in row


# ---- open_request: the write boundary --------------------------------------


def test_open_with_a_severity_stores_level_and_reason(project):
    q_id, _ = _sent(project, severity="high", severity_reason=REASON)
    row = _rows(project)[0]
    assert (row["severity"], row["severity_reason"]) == ("high", REASON)
    record = _sent_view(project, q_id)
    assert (record["severity"], record["severity_reason"]) == ("high", REASON)


@pytest.mark.parametrize("level", ["critical", "high", "low"])
def test_every_raised_or_lowered_level_needs_a_reason(project, level):
    with pytest.raises(requests.RequestError, match="reason"):
        _sent(project, severity=level)
    assert not (config.checkpoint_dir() / store.project_slug(project)
                / "requests.jsonl").exists()


def test_normal_with_a_reason_is_refused(project):
    with pytest.raises(requests.RequestError, match="normal"):
        _sent(project, severity="normal", severity_reason=REASON)


def test_a_level_outside_the_table_is_refused_naming_the_table(project):
    with pytest.raises(requests.RequestError) as err:
        _sent(project, severity="urgent", severity_reason=REASON)
    for level in requests.SEVERITIES:
        assert level in str(err.value)


def test_a_reason_over_the_bound_is_refused_not_cut(project):
    with pytest.raises(requests.RequestTooLong) as err:
        _sent(project, severity="high",
              severity_reason="r" * (requests.SEVERITY_REASON_CHARS + 1))
    assert err.value.field == "severity_reason"
    assert err.value.limit == requests.SEVERITY_REASON_CHARS


def test_a_reason_at_the_bound_is_kept(project):
    reason = "r" * requests.SEVERITY_REASON_CHARS
    q_id, _ = _sent(project, severity="low", severity_reason=reason)
    assert _sent_view(project, q_id)["severity_reason"] == reason


def test_the_reason_is_redacted_before_it_is_written(project):
    secret = "AKIAIOSFODNN7EXAMPLE"
    _sent(project, severity="high", severity_reason=f"rotate {secret}")
    assert secret not in (config.checkpoint_dir()
                          / store.project_slug(project)
                          / "requests.jsonl").read_text()


def test_either_channel_may_set_it(project):
    for channel in ("cli-agent", "cli-tty"):
        q_id, _ = _sent(project, channel=channel, ask=f"ask via {channel}",
                        severity="critical", severity_reason=REASON)
        assert _sent_view(project, q_id)["severity"] == "critical"


def test_supersedes_does_not_carry_the_severity_over(project):
    first, rdir = _sent(project, severity="high", severity_reason=REASON)
    second = requests.open_request(
        to=store.project_slug(rdir), ask="the same ask, sharper", why=WHY,
        channel="cli-agent", supersedes=first, project_dir=project)
    assert _sent_view(project, second)["severity"] == "normal"


# ---- the fold reads the row defensively -------------------------------------


def _hand_rows(**opened_extra):
    q_id, base = "q-0000000000c1", 5 * 10**18
    opened = requests._stamp("opened", q_id, "cli-agent", now_ns=base)
    opened.update(to="-p-recipient", ask=ASK, why=WHY, _origin_slug="",
                  **opened_extra)
    return q_id, base, opened


@pytest.mark.parametrize("extra", [
    {"severity": "urgent", "severity_reason": REASON},
    {"severity": "high"},
    {"severity": "high", "severity_reason": ""},
    {"severity": "normal", "severity_reason": REASON},
    {"severity": ["high"], "severity_reason": REASON},
    {"severity": "high", "severity_reason": 5},
])
def test_an_unreadable_severity_lands_the_ask_as_normal_and_says_so(extra):
    q_id, _, opened = _hand_rows(**extra)
    record = fold_rows([opened])[q_id]
    assert record["state"] == "open"
    assert (record["severity"], record["severity_reason"]) == ("normal", "")
    effect = record["row_effects"][opened["event_id"]]
    assert effect["reason"] == "opened"
    assert effect["severity"] == "unreadable"


def test_a_readable_severity_on_opened_is_not_noted_as_unreadable():
    q_id, _, opened = _hand_rows(severity="low", severity_reason=REASON)
    record = fold_rows([opened])[q_id]
    assert record["severity"] == "low"
    assert "severity" not in record["row_effects"][opened["event_id"]]


def test_the_reason_is_bounded_again_on_read():
    q_id, _, opened = _hand_rows(severity="high", severity_reason="x" * 500)
    record = fold_rows([opened])[q_id]
    assert len(requests.severity_line(record)) <= 93


# ---- severity_set: the sender changes it ------------------------------------


def _set(project, q_id, level, reason="", channel="cli-agent", **kwargs):
    return requests.set_severity(q_id, level, reason=reason, channel=channel,
                                 project_dir=project, **kwargs)


def test_the_sender_raises_a_severity_and_the_fold_says_so(project):
    q_id, _ = _sent(project)
    event_id = _set(project, q_id, "high", REASON)
    record = _sent_view(project, q_id)
    assert (record["severity"], record["severity_reason"]) == ("high", REASON)
    assert record["row_effects"][event_id]["reason"] == "severity_set"
    assert requests.effect_line(q_id, record["row_effects"][event_id])


def test_normal_clears_a_raised_level_and_its_reason(project):
    q_id, _ = _sent(project, severity="critical", severity_reason=REASON)
    _set(project, q_id, "normal")
    record = _sent_view(project, q_id)
    assert (record["severity"], record["severity_reason"]) == ("normal", "")
    assert "severity_reason" not in _rows(project)[-1]


def test_a_later_set_wins_and_replaces_the_reason(project):
    q_id, _ = _sent(project)
    _set(project, q_id, "high", "first reason")
    _set(project, q_id, "low", "second reason")
    record = _sent_view(project, q_id)
    assert (record["severity"], record["severity_reason"]) == (
        "low", "second reason")


@pytest.mark.parametrize("setup", ["open", "needs-info", "accepted"])
def test_it_changes_while_the_sender_is_owed_something(project, setup):
    q_id, rdir = _sent(project)
    if setup == "needs-info":
        requests.needs_info(q_id, channel="cli-tty", note="which one?",
                            project_dir=rdir)
    if setup == "accepted":
        requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _set(project, q_id, "high", REASON)
    record = _sent_view(project, q_id)
    assert record["severity"] == "high"
    assert record["state"] == setup


def test_it_changes_a_pending_completion_claim_without_touching_the_claim(
        project):
    q_id, rdir = _sent(project)
    requests.done(q_id, channel="cli-agent", evidence="shipped",
                  project_dir=rdir)
    before = _sent_view(project, q_id)
    assert before["done_pending"] is True
    _set(project, q_id, "high", REASON)
    after = _sent_view(project, q_id)
    assert after["severity"] == "high"
    for key in ("state", "revision", "done_pending", "done_claimed",
                "updated_order", "updated_at", "superseded_claims"):
        assert after[key] == before[key], key


def test_it_moves_no_state_revision_or_recency(project):
    q_id, _ = _sent(project)
    before = _sent_view(project, q_id)
    _set(project, q_id, "critical", REASON)
    after = _sent_view(project, q_id)
    for key in ("state", "revision", "updated_order", "updated_at",
                "surfaced", "delivered", "superseded_claims"):
        assert after[key] == before[key], key


def test_it_does_not_use_up_a_revision(project):
    q_id, _ = _sent(project)
    for i in range(requests.MAX_REVISIONS + 2):
        _set(project, q_id, "high" if i % 2 == 0 else "low", f"reason {i}")
    requests.revise(q_id, channel="cli-agent", why="a sharper why",
                    project_dir=project)
    assert _sent_view(project, q_id)["revision"] == 1


@pytest.mark.parametrize("settle", ["done", "rejected", "withdrawn"])
def test_a_settled_request_keeps_the_severity_it_had(project, settle):
    q_id, rdir = _sent(project, severity="high", severity_reason=REASON)
    if settle == "done":
        requests.accept(q_id, channel="cli-tty", project_dir=rdir)
        requests.done(q_id, channel="cli-tty", evidence="shipped",
                      project_dir=rdir)
    elif settle == "rejected":
        requests.reject(q_id, channel="cli-tty", note="no",
                        project_dir=rdir)
    else:
        requests.withdraw(q_id, reason="no longer needed",
                          channel="cli-agent", project_dir=project)
    before = _rows(project)
    with pytest.raises(requests.RequestError, match="settled"):
        _set(project, q_id, "low", "now less important")
    assert _rows(project) == before
    assert _sent_view(project, q_id)["severity"] == "high"


def test_a_change_that_changes_nothing_is_refused(project):
    q_id, _ = _sent(project, severity="high", severity_reason=REASON)
    before = _rows(project)
    with pytest.raises(requests.RequestError, match="changes nothing"):
        _set(project, q_id, "high", REASON)
    assert _rows(project) == before
    plain, _ = _sent(project, ask="a plain ask")
    with pytest.raises(requests.RequestError, match="changes nothing"):
        _set(project, plain, "normal")


def test_the_same_level_with_a_new_reason_is_a_change(project):
    q_id, _ = _sent(project, severity="high", severity_reason=REASON)
    _set(project, q_id, "high", "a better reason")
    assert _sent_view(project, q_id)["severity_reason"] == "a better reason"


def test_the_write_boundary_rules_apply_to_the_verb_too(project):
    q_id, _ = _sent(project)
    with pytest.raises(requests.RequestError, match="reason"):
        _set(project, q_id, "high")
    with pytest.raises(requests.RequestError, match="normal"):
        _set(project, q_id, "normal", REASON)
    with pytest.raises(requests.RequestError):
        _set(project, q_id, "urgent", REASON)
    with pytest.raises(requests.RequestTooLong):
        _set(project, q_id, "high", "r" * 81)


def test_an_unknown_request_is_refused(project):
    with pytest.raises(requests.RequestError, match="unknown"):
        _set(project, "q-0000000000aa", "high", REASON)


def test_the_recipient_cannot_set_it(project):
    q_id, rdir = _sent(project)
    before = _rows(project)
    with pytest.raises(requests.RequestError, match="sender"):
        requests.set_severity(q_id, "critical", reason=REASON,
                              channel="cli-tty", project_dir=rdir)
    assert _rows(project) == before


# ---- the recipient records nothing ------------------------------------------


@pytest.mark.parametrize("age", ["now", "before_the_rule"])
def test_a_severity_set_row_in_the_recipients_bucket_is_inert(
        project, monkeypatch, age):
    q_id, rdir = _sent(project)
    cutoff = time.time_ns() + 3_600 * 10**9
    monkeypatch.setattr(requests, "ROLE_RULE_FROM_NS", cutoff)
    now_ns = cutoff - 1 if age == "before_the_rule" else cutoff + 1
    event_id = _forge("severity_set", q_id, rdir, now_ns=now_ns,
                      severity="critical", severity_reason="mine now")
    for record in (_sent_view(project, q_id),
                   requests.recipient_join(project_dir=rdir)[q_id]):
        assert record["severity"] == "normal"
        assert record["row_effects"][event_id]["reason"] == "not_from_sender"


def test_a_severity_key_on_a_recipient_row_changes_nothing(project):
    q_id, rdir = _sent(project)
    requests.accept(q_id, channel="cli-tty", project_dir=rdir)
    _forge("replied", q_id, rdir, note="working on it", severity="critical",
           severity_reason="sneaky")
    for record in (_sent_view(project, q_id),
                   requests.recipient_join(project_dir=rdir)[q_id]):
        assert record["severity"] == "normal"
        assert record["severity_reason"] == ""


def test_a_forged_sender_row_in_the_sender_bucket_still_lands(project):
    q_id, _ = _sent(project)
    _forge("severity_set", q_id, project, severity="high",
           severity_reason=REASON)
    assert _sent_view(project, q_id)["severity"] == "high"


@pytest.mark.parametrize("extra", [
    {}, {"severity": "urgent", "severity_reason": REASON},
    {"severity": "high"}, {"severity": "normal", "severity_reason": REASON},
])
def test_an_unreadable_severity_set_row_is_inert_and_says_why(project, extra):
    q_id, _ = _sent(project, severity="low", severity_reason="was low")
    event_id = _forge("severity_set", q_id, project, **extra)
    record = _sent_view(project, q_id)
    assert (record["severity"], record["severity_reason"]) == (
        "low", "was low")
    assert record["row_effects"][event_id]["reason"] == "severity_unreadable"


def test_a_severity_set_row_on_a_settled_request_is_inert_and_says_why(
        project):
    q_id, rdir = _sent(project)
    requests.reject(q_id, channel="cli-tty", note="no", project_dir=rdir)
    event_id = _forge("severity_set", q_id, project, severity="high",
                      severity_reason=REASON)
    record = _sent_view(project, q_id)
    assert record["severity"] == "normal"
    assert record["row_effects"][event_id]["reason"] == "severity_settled"


# ---- one renderer ------------------------------------------------------------


def test_normal_renders_nothing():
    assert requests.severity_line({"severity": "normal",
                                   "severity_reason": ""}) == ""


@pytest.mark.parametrize("level,word", [
    ("critical", "Critical"), ("high", "High"), ("low", "Low")])
def test_a_level_renders_one_line_with_its_reason(level, word):
    assert requests.severity_line(
        {"severity": level, "severity_reason": REASON}
    ) == f"  {word}: {REASON}"


def test_the_renderer_reads_the_folded_field_directly():
    with pytest.raises(KeyError):
        requests.severity_line({})


# ---- plaintext: one registry, every consumer ---------------------------------


def test_the_reason_is_scalar_prose_in_the_registry_and_the_level_is_not():
    from daimon_briefing import surfaces
    fields = surfaces.scalar_prose_fields("requests.jsonl")
    assert "severity_reason" in fields
    assert "severity" not in fields


def test_forget_on_a_reason_only_value_removes_the_record(project):
    from daimon_briefing import normalize
    q_id, _ = _sent(project, severity="high",
                    severity_reason="only-in-the-reason-7c1d")
    keep, _ = _sent(project, ask="a different ask", severity="low",
                    severity_reason="another reason")
    key = normalize.content_key("only-in-the-reason-7c1d")
    assert q_id in requests.forget_content_key(key, project_dir=project)
    text = requests._path(project).read_text()
    assert "only-in-the-reason-7c1d" not in text
    assert keep in text


def test_a_foreign_read_keeps_only_the_presence_of_a_reason():
    from daimon_briefing import pending
    row = {"severity": "high", "severity_reason": "private words"}
    out = pending._strip_plaintext(row)
    assert out == {"severity": "high", "severity_reason": "x"}
    # and the fold still reads that stripped row as a high severity
    q_id, _, opened = _hand_rows(**out)
    assert fold_rows([opened])[q_id]["severity"] == "high"


def test_a_severity_that_cannot_be_appended_is_an_error(project, monkeypatch):
    q_id, _ = _sent(project)
    monkeypatch.setattr(requests, "append", lambda *a, **k: False)
    with pytest.raises(requests.RequestError, match="not written"):
        _set(project, q_id, "high", REASON)
