"""#1031: `action-recall` — recall keyed on the shell command, not the prompt.

The miss this surface answers: a prompt that says "recommended improvements"
carries no words about deploys, so the per-prompt recall queries on that tail
and returns nothing, and the agent then writes to a pod. The command string
itself carries the signal the prompt does not.

Three things are deliberately NOT shared with `recall-inject` and are pinned
here rather than assumed:

  - the verb allowlist runs FIRST, so `ls` costs one usage line and no index
    read, and there is no machine-prompt classifier on this surface at all
    (a shell command is never a host-emitted notification);
  - the query text stops at the first heredoc marker, because the body of a
    `<<EOF` manifest is payload, not a question;
  - the cooldown state is its OWN file per session, written atomically, and
    the prompt surface's file is read and never written.
"""

import io
import json

import pytest

from daimon_briefing import cli, store


@pytest.fixture
def tmp_log_dir(tmp_path):
    # The autouse fixture already points DAIMON_LOG_DIR here; expose the path.
    return tmp_path / ".daimon" / "logs"


PROJECT = "/repo/k8s"
BELIEF = ("argocd selfHeal reverts any manual kubectl edit to the gateway "
          "deployment in prod")
ACTION = "kubectl exec -it deploy/gateway -n prod -- sh"


def _checkpoint(session, text, created, *, kind="open_questions"):
    body = {
        "session_id": session,
        "created": created,
        "working_context": {
            "active_topic": {"text": "cluster work", "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": [],
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }
    body["working_context"][kind] = [{
        "text": text, "trust": "verbatim", "quote": text[:40],
        "importance": 9, "first_seen": created}]
    return body


def _seed(text=BELIEF, project=PROJECT, session="S-old",
          created="2026-06-20T00:00:00Z"):
    """One matchable prior session plus a newer, unrelated one.

    The newer one is not decoration: the injection path excludes whatever the
    SessionStart briefing already carried, which is this project's LATEST
    checkpoint. A single seeded session would be that latest and would be
    excluded from its own test.
    """
    store.write_checkpoint(session, _checkpoint(session, text, created),
                           project_dir=project)
    store.write_checkpoint(
        "S-latest",
        _checkpoint("S-latest", "unrelated newer bookkeeping",
                    "2026-06-28T00:00:00Z"),
        project_dir=project)


def _run(monkeypatch, capsys, command, session="S-now", project=PROJECT,
         record_only=False):
    monkeypatch.setattr("sys.stdin", io.StringIO(command))
    argv = ["action-recall", "--project", project, "--session", session]
    if record_only:
        argv.append("--record-only")
    rc = cli.main(argv)
    return rc, capsys.readouterr().out


def _usage(tmp_log_dir):
    path = tmp_log_dir / "usage.log"
    if not path.exists():
        return []
    return [line.split()[1] for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ledger(tmp_log_dir):
    path = tmp_log_dir / "recall-delivery.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _quoted(line):
    """The item-text span of an injection line, between the age colon and the
    trust tag. Read off the shipped line rather than recomputed, so the test
    measures what a host actually receives."""
    return line.split(': "', 1)[1].rsplit('" [', 1)[0]


# ---- the verb allowlist, which runs before anything reads the index -------


def test_an_allowlisted_verb_surfaces_the_belief_the_prompt_never_named(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0
    assert "S-old" in out and "selfHeal" in out
    assert "daimon recall" in out  # points at the deep-dive command


@pytest.mark.parametrize("command", ["ls -la", "python3 x.py"])
def test_a_verb_outside_the_allowlist_is_silent_and_counted(
        command, tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # The skip is counted apart from the denominator, so the fire rate per 100
    # shell actions stays readable from `daimon stats` (#450's convention).
    _seed()
    rc, out = _run(monkeypatch, capsys, command)
    assert rc == 0 and out == ""
    tags = _usage(tmp_log_dir)
    assert tags[0] == "action-recall"  # the denominator fires first
    assert "action-recall:skip-verb" in tags


@pytest.mark.parametrize("command", ["git push origin main", "git merge x"])
def test_the_two_word_git_verbs_proceed_past_the_allowlist(
        command, tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # `git push` and `git merge` are the only pairs on the list, so the match
    # has to read two tokens deep and not just the leading one.
    _seed()
    rc, out = _run(monkeypatch, capsys, command)
    assert rc == 0 and out == ""
    tags = _usage(tmp_log_dir)
    assert "action-recall:skip-verb" not in tags
    assert "action-recall:no-match" in tags


def test_a_git_subcommand_outside_the_pair_is_skipped(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # The negative control for the pair above: `git` alone is not the verb,
    # or every status call would pay an index read.
    _seed()
    rc, out = _run(monkeypatch, capsys, "git status")
    assert rc == 0 and out == ""
    assert "action-recall:skip-verb" in _usage(tmp_log_dir)


def test_leading_environment_assignments_do_not_hide_the_verb(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, f"KUBECONFIG=/tmp/kc {ACTION}")
    assert rc == 0
    assert "S-old" in out


# ---- the heredoc cut -----------------------------------------------------


HEREDOC_BODY = "flamingo topiary quorint ledger reconciliation"


def test_a_heredoc_body_is_payload_and_never_the_query(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # Only the manifest body would match this item. If the body reached the
    # query, this would suggest — and it would be suggesting against text the
    # agent is about to write, not against the action it is about to take.
    _seed(text=HEREDOC_BODY, project="/repo/manifests")
    rc, out = _run(monkeypatch, capsys,
                   f"kubectl apply -f - <<EOF\n{HEREDOC_BODY}\nEOF",
                   project="/repo/manifests")
    assert rc == 0 and out == ""
    assert "action-recall:no-match" in _usage(tmp_log_dir)


def test_the_same_words_outside_a_heredoc_do_match(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # The control that keeps the test above honest: the item IS reachable, so
    # the silence there is the cut and not an unmatchable fixture.
    _seed(text=HEREDOC_BODY, project="/repo/manifests")
    rc, out = _run(monkeypatch, capsys,
                   f"kubectl apply -f {HEREDOC_BODY}",
                   project="/repo/manifests")
    assert rc == 0
    assert "S-old" in out


@pytest.mark.parametrize("marker", ["<<EOF", "<<'EOF'", "<<-EOF"])
def test_every_heredoc_spelling_cuts_at_the_same_place(
        marker, tmp_checkpoint_dir, capsys, monkeypatch):
    _seed(text=HEREDOC_BODY, project="/repo/manifests")
    rc, out = _run(monkeypatch, capsys,
                   f"kubectl apply -f - {marker}\n{HEREDOC_BODY}\nEOF",
                   project="/repo/manifests")
    assert rc == 0 and out == ""


# ---- one slot, at the lead width ----------------------------------------


WIDE = ("argocd selfHeal reverts any manual kubectl edit to the gateway "
        "deployment in prod, and the reconciliation loop restores the "
        "committed manifest within ninety seconds, so a hotfix applied by "
        "hand disappears without a log line anyone reads, which is how the "
        "last outage lasted four hours longer than it had to and why the "
        "runbook now says to open a pull request instead of touching the "
        "cluster directly during an incident")


def test_the_action_surface_spends_exactly_one_slot_at_the_lead_width(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # One action, one line: the budget is not the prompt surface's two. And
    # the single slot IS the lead, so it carries the full lead width (#1030).
    assert len(WIDE) > cli._LEAD_WIDTH
    _seed(text=WIDE)
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0
    lines = out.splitlines()
    assert len(lines) == 1
    assert len(_quoted(lines[0])) == cli._LEAD_WIDTH


def test_the_delivery_ledger_names_the_action_surface_and_its_rendering(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    _seed(text=WIDE)
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    rows = _ledger(tmp_log_dir)
    assert len(rows) == 1
    assert rows[0]["surface"] == "action-recall"
    assert rows[0]["rendered_chars"] == cli._LEAD_WIDTH
    assert rows[0]["truncated"] is True


def test_an_untruncated_delivery_reports_what_it_rendered(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    row = _ledger(tmp_log_dir)[0]
    assert row["truncated"] is False
    assert row["rendered_chars"] == len(_quoted(out.splitlines()[0]))


def test_the_query_terms_reaching_the_ledger_are_the_commands_own(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    assert _ledger(tmp_log_dir)[0]["query_term_count"] == \
        len(cli.recall.salient_terms(ACTION))


# ---- record-only: the ladder's middle rung -------------------------------


def test_record_only_writes_the_ledger_row_and_says_nothing(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # The soak rung. The measurement has to be identical to what delivery
    # would have measured, or the two-week read compares nothing.
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION, record_only=True)
    assert rc == 0
    assert out == ""
    rows = _ledger(tmp_log_dir)
    assert len(rows) == 1 and rows[0]["surface"] == "action-recall"


def test_no_match_is_silent_and_counted(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, "kubectl get flamingo topiary")
    assert rc == 0 and out == ""
    assert "action-recall:no-match" in _usage(tmp_log_dir)
    assert _ledger(tmp_log_dir) == []


def test_without_a_session_id_the_surface_stays_silent(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # The session id keys the cooldown. Without it every action in a session
    # would repeat the same line, so silence is the honest answer.
    _seed()
    monkeypatch.setattr("sys.stdin", io.StringIO(ACTION))
    rc = cli.main(["action-recall", "--project", PROJECT])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_the_surface_never_fails(tmp_checkpoint_dir, capsys, monkeypatch):
    # No history, no index, unknown project — still rc 0, still silent. This
    # sits in front of every shell action; a suggestion is never worth one.
    monkeypatch.setattr("sys.stdin", io.StringIO("kubectl get pods -n prod"))
    rc = cli.main(["action-recall", "--session", "S-x"])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_the_denominator_fires_once_on_every_path(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # Three invocations, three different outcomes, three denominator lines:
    # the flip condition is a RATE and needs an attempt count that counts
    # attempts nobody served.
    _seed()
    _run(monkeypatch, capsys, "ls -la")
    _run(monkeypatch, capsys, "kubectl get flamingo topiary")
    _run(monkeypatch, capsys, ACTION)
    assert _usage(tmp_log_dir).count("action-recall") == 3


# ---- cooldown: its own file, atomic, and read-only across the surface ----


def _seen_dir():
    from daimon_briefing import config
    return config.recall_seen_dir()


def test_the_same_action_twice_in_one_session_speaks_once(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed()
    rc1, out1 = _run(monkeypatch, capsys, ACTION)
    rc2, out2 = _run(monkeypatch, capsys, ACTION)
    assert rc1 == 0 and out1 != ""
    assert rc2 == 0 and out2 == ""


def test_the_cooldown_lands_in_its_own_file_beside_the_prompt_surfaces(
        tmp_checkpoint_dir, capsys, monkeypatch):
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    assert (_seen_dir() / "S-now.action").is_file()
    assert not (_seen_dir() / "S-now.json").exists()


def test_the_prompt_surfaces_cooldown_file_is_never_written(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # Shared state between two surfaces on one session is a lost update
    # waiting to happen. This surface reads that file and writes its own.
    _seed()
    prompt_file = cli._seen_path("S-now")
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(json.dumps({"origins": {}, "content_keys": []}),
                           encoding="utf-8")
    before = prompt_file.read_bytes()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    assert prompt_file.read_bytes() == before


def test_a_claim_the_prompt_surface_already_delivered_is_skipped(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # Cross-surface suppression: the agent has already been told this in this
    # session, and hearing it again before the action is noise, not emphasis.
    from daimon_briefing import normalize

    _seed()
    prompt_file = cli._seen_path("S-now")
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(json.dumps(
        {"origins": {}, "content_keys": [normalize.content_key(BELIEF)]}),
        encoding="utf-8")
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out == ""


def test_the_cooldown_write_leaves_no_temporary_file_behind(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # Written temp-then-rename so a reader never sees half a file. The
    # tolerated cost is a LOST UPDATE between two parallel shell actions,
    # which repeats at most one line; a torn read is not tolerated.
    _seed()
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out
    names = sorted(p.name for p in _seen_dir().iterdir())
    assert names == ["S-now.action"]
    assert json.loads((_seen_dir() / "S-now.action").read_text(
        encoding="utf-8"))["content_keys"]


def test_an_unwritable_cooldown_still_suggests(
        tmp_checkpoint_dir, capsys, monkeypatch):
    # Cooldown is best-effort: a write that cannot land costs one repeated
    # suggestion, never the suggestion itself.
    _seed()
    monkeypatch.setattr(cli, "_save_seen_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    rc, out = _run(monkeypatch, capsys, ACTION)
    assert rc == 0 and out != ""


def test_a_failed_rename_swallows_the_error_and_leaves_no_debris(
        tmp_path, monkeypatch):
    # Driven at the writer rather than through the command, because patching
    # os.replace globally also breaks the index write the query depends on and
    # the surface would go silent for the wrong reason.
    target = tmp_path / "S-x.action"
    monkeypatch.setattr(cli.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    cli._save_seen_atomic(target, {"S-old": 1}, {"k"})
    assert not target.exists()
    # The temp file goes with it, or a full disk fills the seen dir with the
    # evidence of having tried.
    assert list(tmp_path.iterdir()) == []


def test_a_failed_cleanup_after_a_failed_rename_is_still_silent(
        tmp_path, monkeypatch):
    # The second failure in a row. Nothing about a cooldown write is worth
    # raising out of a surface that runs before every shell action.
    target = tmp_path / "S-x.action"
    monkeypatch.setattr(cli.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(cli.os, "unlink",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("also")))
    cli._save_seen_atomic(target, {}, set())
    assert not target.exists()


def test_the_writer_round_trips_through_the_reader(tmp_path):
    # The positive control for the two failure tests above: what this writes
    # is what `_load_seen` reads, so a surface's own cooldown survives a
    # restart of the process that wrote it.
    target = tmp_path / "S-x.action"
    cli._save_seen_atomic(target, {"S-old": 2}, {"key-a", "key-b"})
    assert cli._load_seen(target) == ({"S-old": 2}, {"key-a", "key-b"})


def test_a_command_that_is_only_environment_assignments_is_skipped(
        tmp_checkpoint_dir, tmp_log_dir, capsys, monkeypatch):
    # Stepping over `FOO=bar` can step over the whole command. There is no
    # verb left to match, and no verb means silence.
    _seed()
    rc, out = _run(monkeypatch, capsys, "KUBECONFIG=/tmp/kc")
    assert rc == 0 and out == ""
    assert "action-recall:skip-verb" in _usage(tmp_log_dir)
