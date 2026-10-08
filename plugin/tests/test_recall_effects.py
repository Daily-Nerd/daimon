"""Recall writes through `effects_commit`, and the view owns its scope
(#1132 PR 9b, D9.8).

Before this, `daimon recall`, `recall-inject`, `action-recall` and recall's
own error breadcrumb wrote usage, telemetry, cooldown and the error log as
they went. Now each records what it decided into `Effects` and the one
committer writes it after the output."""
import io
import json
import re

import pytest

from daimon_briefing import (cli, config, effects_commit, normalize,
                             recall, recall_telemetry, store, view)
from daimon_briefing.effects import (Effects, ErrorLog, Seen, Telemetry,
                                     merge)
from daimon_briefing.surfaces import Writer

PROJECT = "/repo/effects"
OTHER = "/repo/other"
BELIEF = ("argocd selfHeal reverts any manual kubectl edit to the gateway "
          "deployment in prod")
PROMPT = "argocd selfHeal reverts manual kubectl edit gateway deployment"
ACTION = "kubectl exec -it deploy/gateway -n prod -- sh"


def _checkpoint(session, text, created):
    return {
        "session_id": session, "created": created,
        "working_context": {
            "active_topic": {"text": f"scratch notes {session}",
                             "trust": "inferred"},
            "open_questions": [{
                "text": text, "trust": "verbatim", "quote": text[:40],
                "importance": 9, "first_seen": created}],
            "recent_decisions": []},
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def _seed(project=PROJECT, text=BELIEF):
    store.write_checkpoint("S-old", _checkpoint("S-old", text,
                                                "2026-06-20T00:00:00Z"),
                           project_dir=project, writer=Writer.HUMAN)
    store.write_checkpoint("S-latest", _checkpoint(
        "S-latest", "unrelated newer bookkeeping", "2026-06-28T00:00:00Z"),
        project_dir=project, writer=Writer.HUMAN)


@pytest.fixture
def commits(monkeypatch):
    """Every Effects the committer is handed. A write function reached while
    no commit is running is a write the verb made on its own: the violation
    list collects those."""
    state = {"inside": False, "fx": [], "outside": []}
    real = effects_commit.commit

    def commit(fx):
        state["fx"].append(fx)
        state["inside"] = True
        try:
            return real(fx)
        finally:
            state["inside"] = False

    monkeypatch.setattr(effects_commit, "commit", commit)

    def guard(name, owner):
        original = getattr(owner, name)

        def wrapped(*a, **k):
            if not state["inside"]:
                state["outside"].append(name)
            return original(*a, **k)
        return wrapped

    from daimon_briefing.cli import action_recall, inject
    for name, owners in (
            ("_note_usage", (cli,)),
            ("_save_seen", (cli, inject)),
            ("_save_seen_atomic", (cli, inject, action_recall))):
        wrapper = guard(name, owners[0])
        for owner in owners:
            if hasattr(owner, name):
                monkeypatch.setattr(owner, name, wrapper)
    monkeypatch.setattr(recall_telemetry, "record",
                        guard("record", recall_telemetry))
    return state


# ---- the records ----------------------------------------------------------


def test_error_log_and_seen_are_records_that_merge():
    a = Effects(error_log=(ErrorLog("recall-error.log", "2026-01-01T00:00:00Z",
                                    "search", "OSError: x"),))
    b = Effects(seen=(Seen("/tmp/x.json", {"S": 1}, frozenset({"k"}), False),))
    got = merge(a, b)
    assert len(got.error_log) == 1 and len(got.seen) == 1


# ---- the error log --------------------------------------------------------


def test_commit_writes_one_redacted_capped_line(tmp_path):
    secret = "api_key=sk-abcdefghijklmnop1234"
    detail = f"OSError: bad {secret} " + "x" * 2000 + "\nsecond line"
    effects_commit.commit(Effects(error_log=(ErrorLog(
        "recall-error.log", "2026-01-01T00:00:00Z", "search", detail),)))
    lines = (config.log_dir() / "recall-error.log").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("2026-01-01T00:00:00Z search: OSError: bad ")
    assert "sk-abcdefghijklmnop1234" not in lines[0]
    assert len(lines[0]) <= 600


def test_a_failing_error_log_write_does_not_raise(monkeypatch):
    monkeypatch.setattr(config, "log_dir",
                        lambda: (_ for _ in ()).throw(OSError("no disk")))
    effects_commit.commit(Effects(error_log=(ErrorLog(
        "recall-error.log", "2026-01-01T00:00:00Z", "x", "OSError: y"),)))


def test_note_error_hands_its_breadcrumb_to_the_committer(monkeypatch):
    got = []
    monkeypatch.setattr(effects_commit, "commit_error_log", got.append)
    recall._note_error("search", OSError("disk full"))
    assert len(got) == 1
    rec = got[0]
    assert (rec.log, rec.where) == ("recall-error.log", "search")
    assert rec.detail == "OSError: disk full"
    # nothing was written by the producer itself
    assert not (config.log_dir() / "recall-error.log").exists()


# ---- the three recall verbs commit, never write ---------------------------


def test_cli_recall_commits_usage_and_telemetry(tmp_checkpoint_dir, capsys,
                                                commits):
    _seed()
    assert cli.main(["recall", "selfHeal", "--project", PROJECT]) == 0
    assert "selfHeal" in capsys.readouterr().out
    assert commits["outside"] == []
    (fx,) = commits["fx"]
    assert fx.usage == ("recall",)
    (sample,) = fx.telemetry
    assert sample.kwargs["surface"] == "recall-search"
    assert sample.kwargs["via"] == "cli"
    assert sample.rows and sample.rows[0]["session_id"] == "S-old"
    ledger = (config.log_dir() / "recall-delivery.jsonl").read_text(
        encoding="utf-8")
    assert "recall-search" in ledger


def test_cli_recall_with_a_bad_limit_still_commits_its_usage(
        tmp_checkpoint_dir, capsys, commits):
    assert cli.main(["recall", "x", "--limit", "0"]) == 2
    assert commits["outside"] == []
    assert commits["fx"][0].usage == ("recall",)
    assert commits["fx"][0].telemetry == ()


def test_recall_inject_commits_usage_telemetry_and_cooldown(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    _seed()
    monkeypatch.setattr("sys.stdin", io.StringIO(PROMPT))
    assert cli.main(["recall-inject", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert "S-old" in capsys.readouterr().out
    assert commits["outside"] == []
    (fx,) = commits["fx"]
    assert fx.usage[0] == "recall-inject"
    assert any(u.startswith("recall-inject:age:") for u in fx.usage)
    (sample,) = fx.telemetry
    assert sample.kwargs["surface"] == "recall-inject"
    (seen,) = fx.seen
    assert seen.atomic is False
    assert seen.path.name == "S-now.json"
    assert seen.origin_counts == {"S-old": 1}
    assert (config.recall_seen_dir() / "S-now.json").exists()


def test_recall_inject_with_nothing_to_say_commits_the_placeholder(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    monkeypatch.setattr("sys.stdin", io.StringIO(PROMPT))
    assert cli.main(["recall-inject", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert commits["outside"] == []
    (fx,) = commits["fx"]
    assert fx.telemetry[0].rows == []
    assert fx.seen == ()


def test_recall_inject_skip_machine_commits_its_counter(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    monkeypatch.setattr("sys.stdin", io.StringIO(
        "<task-notification>done</task-notification>"))
    assert cli.main(["recall-inject", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert commits["outside"] == []
    assert commits["fx"][0].usage == ("recall-inject",
                                      "recall-inject:skip-machine")


def test_action_recall_commits_usage_telemetry_and_cooldown(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    _seed()
    monkeypatch.setattr("sys.stdin", io.StringIO(ACTION))
    assert cli.main(["action-recall", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert "S-old" in capsys.readouterr().out
    assert commits["outside"] == []
    (fx,) = commits["fx"]
    assert fx.usage[0] == "action-recall"
    (sample,) = fx.telemetry
    assert sample.kwargs["surface"] == "action-recall"
    (seen,) = fx.seen
    assert seen.atomic is True and seen.path.name == "S-now.action"


def test_action_recall_skip_verb_commits_its_counter(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    monkeypatch.setattr("sys.stdin", io.StringIO("ls -la"))
    assert cli.main(["action-recall", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert commits["outside"] == []
    assert commits["fx"][0].usage == ("action-recall",
                                      "action-recall:skip-verb")


def test_action_recall_no_match_commits_its_counter_and_placeholder(
        tmp_checkpoint_dir, capsys, monkeypatch, commits):
    monkeypatch.setattr("sys.stdin", io.StringIO(ACTION))
    assert cli.main(["action-recall", "--project", PROJECT,
                     "--session", "S-now"]) == 0
    assert commits["outside"] == []
    (fx,) = commits["fx"]
    assert "action-recall:no-match" in fx.usage
    assert fx.telemetry[0].rows == []


def test_the_committer_writes_a_seen_record(tmp_path):
    target = tmp_path / "S-x.json"
    atomic = tmp_path / "S-x.action"
    effects_commit.commit(Effects(seen=(
        Seen(target, {"S-o": 2}, frozenset({"k1"}), False),
        Seen(atomic, {"S-p": 1}, frozenset({"k2"}), True))))
    assert cli._load_seen(target) == ({"S-o": 2}, {"k1"})
    assert cli._load_seen(atomic) == ({"S-p": 1}, {"k2"})


# ---- cooldown files hold hashes, never words ------------------------------


def test_the_cooldown_files_hold_hashes_only(tmp_checkpoint_dir, capsys,
                                             monkeypatch):
    _seed()
    # The action surface reads the prompt surface's keys, so it goes first.
    monkeypatch.setattr("sys.stdin", io.StringIO(ACTION))
    cli.main(["action-recall", "--project", PROJECT, "--session", "S-now"])
    monkeypatch.setattr("sys.stdin", io.StringIO(PROMPT))
    cli.main(["recall-inject", "--project", PROJECT, "--session", "S-now"])
    files = sorted(config.recall_seen_dir().iterdir())
    assert {p.name for p in files} == {"S-now.json", "S-now.action"}
    for p in files:
        body = p.read_text(encoding="utf-8")
        data = json.loads(body)
        assert set(data) == {"origins", "content_keys"}
        assert all(re.fullmatch(r"[0-9a-f]{16,}", k)
                   for k in data["content_keys"]), data["content_keys"]
        assert data["content_keys"]
        for word in ("argocd", "selfHeal", "kubectl", "gateway"):
            assert word.lower() not in body.lower()


# ---- telemetry never counts a forgotten term ------------------------------


def _ledger():
    path = config.log_dir() / "recall-delivery.jsonl"
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_telemetry_count_drops_a_term_whose_key_is_forgotten(
        tmp_checkpoint_dir):
    """Pins the COUNT drop, nothing more. The ledger stores only
    `query_term_count`, never the terms, so today the filter only lowers that
    count. It compares a single term's key with whole-value tombstones, so it
    fires only when the forgotten value is itself one term. It exists so the
    shape is right if the ledger ever stores terms."""
    _seed()
    key = normalize.content_key("zebrafish")
    store.append_event("i-x", f"forgotten:{key}", kind="tombstone",
                       project_dir=PROJECT, tombstone=True, writer=Writer.HUMAN)
    effects_commit.commit(Effects(telemetry=(Telemetry([], {
        "query_terms": ["zebrafish", "pelican"],
        "surface": "recall-search", "via": "cli"}),)))
    assert _ledger()[-1]["query_term_count"] == 1


def test_telemetry_keeps_terms_when_nothing_is_forgotten(tmp_checkpoint_dir):
    effects_commit.commit(Effects(telemetry=(Telemetry([], {
        "query_terms": ["zebrafish", "pelican"],
        "surface": "recall-search", "via": "cli"}),)))
    assert _ledger()[-1]["query_term_count"] == 2


def test_an_unreadable_forgotten_set_does_not_cost_the_telemetry(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(view, "forgotten_keys",
                        lambda: (_ for _ in ()).throw(OSError("x")))
    effects_commit.commit(Effects(telemetry=(Telemetry([], {
        "query_terms": ["a", "b"], "surface": "recall-search",
        "via": "cli"}),)))
    assert _ledger()[-1]["query_term_count"] == 2


# ---- scope is the view's -------------------------------------------------


def test_read_scopes_is_own_slug_then_the_hosts_extras(monkeypatch):
    monkeypatch.setenv("DAIMON_EXTRA_READ_SLUGS", "shared-a,shared-b")
    got = view.read_scopes(PROJECT, all_projects=False)
    assert got == [store.project_slug(PROJECT), "shared-a", "shared-b"]


def test_read_scopes_for_all_projects_is_unfiltered():
    assert view.read_scopes(PROJECT, all_projects=True) is None


def test_the_tenant_rule_never_lets_all_projects_widen_a_read(monkeypatch):
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert view.read_scopes(PROJECT, all_projects=True) == [
        store.project_slug(PROJECT)]


def test_read_scopes_of_an_unknown_project_is_none(monkeypatch):
    monkeypatch.setattr(store, "project_slug", lambda _p: None)
    assert view.read_scopes(PROJECT, all_projects=False) is None


def test_a_tenant_scoped_query_cannot_read_another_projects_rows(
        tmp_checkpoint_dir, monkeypatch):
    _seed(PROJECT)
    store.write_checkpoint("S-foreign", _checkpoint(
        "S-foreign", "selfHeal belongs to the other tenant",
        "2026-06-21T00:00:00Z"), project_dir=OTHER, writer=Writer.HUMAN)
    wide = recall.query("selfHeal", project_dir=PROJECT, all_projects=True)
    assert {r["project_slug"] for r in wide.rows} >= {
        store.project_slug(PROJECT), store.project_slug(OTHER)}
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    tenant = recall.query("selfHeal", project_dir=PROJECT, all_projects=True)
    assert {r["project_slug"] for r in tenant.rows} == {
        store.project_slug(PROJECT)}


def test_a_failing_committer_never_breaks_the_swallow(monkeypatch):
    def boom(_crumb):
        raise RuntimeError("committer down")
    monkeypatch.setattr(effects_commit, "commit_error_log", boom)
    recall._note_error("search", OSError("x"))


def test_note_error_does_not_pull_in_the_cli_and_still_redacts(tmp_path):
    import subprocess
    import sys
    code = (
        "import sys\n"
        "from daimon_briefing import recall\n"
        "assert 'daimon_briefing.cli' not in sys.modules, 'precondition'\n"
        "recall._note_error('search', OSError('bad api_key=sk-abcdefghijklmnop1234'))\n"
        "assert 'daimon_briefing.cli' not in sys.modules\n")
    env = {**__import__("os").environ, "DAIMON_LOG_DIR": str(tmp_path / "logs"),
           "DAIMON_ENV_FILE": str(tmp_path / "none")}
    done = subprocess.run([sys.executable, "-I", "-c", code], env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    line = (tmp_path / "logs" / "recall-error.log").read_text(
        encoding="utf-8")
    assert "search: OSError: bad" in line
    assert "sk-abcdefghijklmnop1234" not in line


def test_commit_error_log_writes_without_flushing_stdout(monkeypatch):
    flushed = []
    monkeypatch.setattr("sys.stdout", type("S", (), {
        "flush": lambda self: flushed.append(1),
        "write": lambda self, s: len(s)})())
    effects_commit.commit_error_log(ErrorLog(
        "recall-error.log", "2026-01-01T00:00:00Z", "x", "OSError: y"))
    assert flushed == []
    assert (config.log_dir() / "recall-error.log").exists()
