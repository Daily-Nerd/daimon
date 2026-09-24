"""#1093: inherited layer rulings render before the project's own in the
briefing. Depends on #1092 (`config.layer_scopes`, `store.bucket_root`,
`store.record_bucket_root`, all shipped).

Fixtures are built through the shipping writers (`refutations.assert_ruling`
/ `ratify`, `checks.sync`) at real layer paths, following the #1092 pattern
in test_config_layer_scopes.py — never hand-shaped ledger rows (#962's
landmine: hand-shaped fixtures hid three data-loss holes).

conftest does NOT patch HOME (only DAIMON_CHECKPOINT_DIR and friends), so
every test here sets HOME itself, same as test_config_layer_scopes.py.
"""

import hashlib
import json
import subprocess
from pathlib import Path

from daimon_briefing import briefing, checks, config, refutations, store


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _home_work_repo(tmp_path, monkeypatch):
    """`tmp_home/work/repo`, `repo` git-inited, HOME=tmp_home. Returns
    (tmp_home, work, repo) — the same shape test_config_layer_scopes.py
    uses, so a layer written at `tmp_home` or `work` is eligible by
    construction."""
    tmp_home = tmp_path / "home"
    work = tmp_home / "work"
    repo = work / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    monkeypatch.setenv("HOME", str(tmp_home))
    return tmp_home, work, repo


def _rule_at(directory, verdict, *, subject=None, scope="tests", channel="cli-tty",
            ratified=True, **kw):
    return refutations.assert_ruling(
        subject=subject or f"subject for {verdict[:40]}", verdict=verdict,
        scope=scope, evidence=["issue:1093"], channel=channel,
        ratified=ratified, project_dir=str(directory), **kw)


def _tick_seconds(monkeypatch, n=50):
    """Steps `refutations.time.time_ns()` forward by 2 real seconds per call
    (the pattern test_rulings.py's
    test_age_and_order_survive_proposals_across_real_seconds uses) —
    `activated_at` is second-precision (`_stamp`'s `ts`), so rulings written
    inside one wall-clock second tie on it and the cap test's "oldest own row
    is hidden" assertion would depend on refutation_id order instead of
    insertion order."""
    base = refutations.time.time_ns()
    ticks = iter(range(1, n))
    monkeypatch.setattr(refutations.time, "time_ns",
                        lambda: base + next(ticks) * 2_000_000_000)


# ---- Group 2: brief read — order, tagging, unreadable, cap, dedup ----------


def test_layers_render_before_own_global_first_then_workspace(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(tmp_home, "global rule one", subject="global subject one")
    _rule_at(tmp_home, "global rule two", subject="global subject two")
    _rule_at(work, "workspace rule one", subject="workspace subject one")
    _rule_at(work, "workspace rule two", subject="workspace subject two")
    _rule_at(work, "workspace rule three", subject="workspace subject three")
    _rule_at(repo, "own rule one", subject="own subject one")
    _rule_at(repo, "own rule two", subject="own subject two")

    lines = briefing.ruling_lines(str(repo))
    body = [ln for ln in lines if ln.startswith("§")]
    assert len(body) == 7
    global_lines, workspace_lines, own_lines = body[0:2], body[2:5], body[5:7]
    assert all("global rule" in ln for ln in global_lines)
    assert all("workspace rule" in ln for ln in workspace_lines)
    assert all("own rule" in ln for ln in own_lines)
    home_tag = config.home_relative(str(tmp_home))
    work_tag = config.home_relative(str(work))
    assert all(f"[from {home_tag}]" in ln for ln in global_lines)
    assert all(f"[from {work_tag}]" in ln for ln in workspace_lines)
    assert all("[from" not in ln for ln in own_lines)


def test_inherited_from_tagged_on_layer_rows_none_on_own(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "layer rule", subject="layer subject")
    _rule_at(repo, "own rule", subject="own subject")

    rows = briefing.active_rulings(str(repo))
    by_subject = {r["subject"]: r for r in rows}
    assert by_subject["layer subject"]["inherited_from"] == str(work)
    assert by_subject["own subject"]["inherited_from"] is None


def test_rulings_read_positional_shape_unchanged():
    result = briefing.RulingsRead(rows=[], state="no-bucket", path=None)
    rows, state, path = result
    assert rows == [] and state == "no-bucket" and path is None
    assert briefing.RulingsRead._fields == ("rows", "state", "path")


def test_unreadable_layer_ledger_keeps_own_rows_plus_note(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "layer rule that will vanish", subject="doomed layer subject")
    _rule_at(repo, "own rule survives", subject="own survives subject")
    refutations._path(str(work)).unlink()
    refutations._path(str(work)).mkdir()

    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    assert "§ own rule survives" in joined
    assert "doomed layer subject" not in joined
    assert f"(inherited rulings from {config.home_relative(str(work))} unreadable)" in joined


def test_cap_three_inherited_five_own_shows_seven_hides_oldest_own(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _tick_seconds(monkeypatch)
    for n in range(3):
        _rule_at(work, f"layer rule {n}", subject=f"layer subject {n}")
    for n in range(5):
        _rule_at(repo, f"own rule {n}", subject=f"own subject {n}")

    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    body = [ln for ln in lines if ln.startswith("§")]
    assert len(body) == 7
    # own rows are newest-first inside their own bucket and rendered LAST
    # (after all 3 inherited rows) — "own rule 0" was ratified first, so it
    # is the oldest own row and the one the cap slice cuts.
    assert "own rule 0" not in joined
    for n in range(1, 5):
        assert f"own rule {n}" in joined
    for n in range(3):
        assert f"layer rule {n}" in joined
    assert "+1 active ruling over cap" in joined
    assert "daimon ruling list --inherited shows all" in joined


def test_layer_ruling_renders_even_when_own_bucket_is_completely_empty(
        tmp_path, monkeypatch):
    # The motivating case from the issue body: "an agent in a child repo is
    # bound by a rule it never sees" — the project's OWN ledger has never
    # ratified anything at all, but the workspace layer has. The section
    # must still render, own-empty or not.
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "layer-only rule reaches the child", subject="layer only subject")

    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    assert briefing._RULING_HEADER in joined
    assert "§ layer-only rule reaches the child" in joined
    tag = config.home_relative(str(work))
    assert f"[from {tag}]" in joined


def test_duplicate_id_across_layer_and_project_renders_once_own_wins(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "promoted rule text", subject="promoted subject",
            scope="promoted scope")
    _rule_at(repo, "promoted rule text", subject="promoted subject",
            scope="promoted scope")

    lines = briefing.ruling_lines(str(repo))
    matches = [ln for ln in lines if "promoted rule text" in ln]
    assert len(matches) == 1
    assert "[from" not in matches[0]


# ---- Group 3: render + budget gate -----------------------------------------


def test_prose_row_gets_layer_suffix_format(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "workspace-owned rule text", subject="ws subject")
    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    tag = config.home_relative(str(work))
    assert f"§ workspace-owned rule text  [from {tag}]" in joined


def test_authored_and_layer_suffix_both_render_in_pinned_order(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    ruling_id = refutations.assert_ruling(
        subject="agent drafted at layer", verdict="agent drafted layer rule",
        scope="tests", evidence=["issue:1093"], channel="cli-agent",
        ratified=False, project_dir=str(work))
    refutations.ratify(ruling_id, channel="cli-tty", project_dir=str(work))
    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    tag = config.home_relative(str(work))
    assert (f"§ agent drafted layer rule  [agent-written]  [from {tag}]"
           ) in joined


def test_compact_enforce_row_from_a_layer_keeps_the_suffix(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    ruling_id = refutations.assert_ruling(
        subject="a public post rule", verdict="the rule for a public post rule",
        scope="publishing", evidence=["issue:1093"], channel="cli-tty",
        ratified=True,
        check={"match": "gh pr create", "body": "#!/bin/sh\nexit 0\n",
              "intent": "enforce"},
        project_dir=str(work))
    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    tag = config.home_relative(str(work))
    assert (f"§ enforced: a public post rule (daimon ruling show {ruling_id})"
           f"  [from {tag}]") in joined
    assert "the rule for a public post rule" not in joined


def test_inherited_policy_dropped_but_own_bucket_policy_still_renders(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_ruling(
        subject="layer policy subject",
        verdict="agent may open info asks toward layer-target",
        scope="cross-project requests", evidence=["issue:1093"],
        channel="cli-tty", ratified=True,
        request_policy={"to": "layer-target", "kind": "info", "verb": "open",
                        "by": "agent"},
        project_dir=str(work))
    refutations.assert_ruling(
        subject="own policy subject",
        verdict="agent may open info asks toward own-target",
        scope="cross-project requests", evidence=["issue:1093"],
        channel="cli-tty", ratified=True,
        request_policy={"to": "own-target", "kind": "info", "verb": "open",
                        "by": "agent"},
        project_dir=str(repo))
    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    assert "§ policy: agent may open info asks → layer-target" not in joined
    assert "§ policy: agent may open info asks → own-target" in joined


def test_layer_candidate_ruling_renders_nothing(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_ruling(
        subject="candidate at layer", verdict="candidate text stays out",
        scope="tests", evidence=["issue:1093"], channel="cli-agent",
        ratified=False, project_dir=str(work))
    assert briefing.ruling_lines(str(repo)) == []


def test_layer_refutation_does_not_render_as_a_ruling(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    refutations.assert_refutation(
        subject="refuted at layer", verdict="refutation text stays out",
        scope="tests", evidence=["issue:1093"], channel="cli-tty",
        ratified=True, project_dir=str(work))
    assert briefing.ruling_lines(str(repo)) == []


def test_manifest_enforce_line_for_worktree_not_in_the_ledger_walk(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "init"],
                   cwd=str(repo), check=True)
    worktrees_dir = repo / ".claude" / "worktrees"
    worktrees_dir.mkdir(parents=True)
    wt = worktrees_dir / "x"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt)],
                   cwd=str(repo), check=True)

    body = "#!/bin/sh\nexit 0\n"
    ruling_id = refutations.assert_ruling(
        subject="a public post rule",
        verdict="the rule for a public post rule in publishing",
        scope="publishing", evidence=["issue:1093"], channel="cli-agent",
        check={"match": "gh pr create", "body": body, "intent": "enforce"},
        project_dir=str(repo))
    refutations.ratify(ruling_id, channel="ui",
                       check_sha256=hashlib.sha256(body.encode()).hexdigest(),
                       project_dir=str(repo))
    checks.sync(str(repo))
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")

    lines = briefing.ruling_lines(str(wt))
    joined = "\n".join(lines)
    tag = config.home_relative(str(repo))
    assert f"§ enforced from {tag}: gh pr create  [{ruling_id}]" in joined
    assert "the rule for a public post rule" not in joined


def _arm_enforce(directory, subject, *, match="gh pr create",
                 body="#!/bin/sh\nexit 0\n"):
    ruling_id = refutations.assert_ruling(
        subject=subject, verdict=f"the rule for {subject} in publishing",
        scope="publishing", evidence=["issue:1093"], channel="cli-agent",
        check={"match": match, "body": body, "intent": "enforce"},
        project_dir=str(directory))
    refutations.ratify(ruling_id, channel="ui",
                       check_sha256=hashlib.sha256(body.encode()).hexdigest(),
                       project_dir=str(directory))
    return ruling_id


def _worktree(repo):
    subprocess.run(["git", "-c", "user.email=t@t.com", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "init"],
                   cwd=str(repo), check=True)
    worktrees_dir = repo / ".claude" / "worktrees"
    worktrees_dir.mkdir(parents=True)
    wt = worktrees_dir / "x"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt)],
                   cwd=str(repo), check=True)
    return wt


def test_manifest_enforce_lines_are_capped_and_count_toward_overflow(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    wt = _worktree(repo)
    for n in range(3):
        _arm_enforce(repo, f"cap post rule {n}")
    checks.sync(str(repo))
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")

    lines = briefing.ruling_lines(str(wt))
    joined = "\n".join(lines)
    body_lines = [ln for ln in lines if ln.startswith("§ enforced from")]
    assert len(body_lines) == 2
    assert "+1 active ruling over cap" in joined
    assert "daimon ruling list --inherited shows all" in joined


def test_manifest_enforce_lines_share_the_cap_with_own_rows(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    wt = _worktree(repo)
    _rule_at(wt, "own prose rule in the worktree bucket",
            subject="wt own subject")
    for n in range(2):
        _arm_enforce(repo, f"mixed cap rule {n}")
    checks.sync(str(repo))
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    monkeypatch.setenv("DAIMON_RULING_CAP", "2")

    lines = briefing.ruling_lines(str(wt))
    joined = "\n".join(lines)
    assert "§ own prose rule in the worktree bucket" in joined
    body_lines = [ln for ln in lines if ln.startswith("§ enforced from")]
    assert len(body_lines) == 1
    assert "+1 active ruling over cap" in joined
    assert "daimon ruling list --inherited shows all" in joined


def test_layered_enforce_lines_budget_is_measured(tmp_path, monkeypatch):
    """Pre-change baseline, measured directly on the unmodified code before
    #1093: 7 rulings, ALL in one bucket, all rendering as `enforce` compact
    lines with no suffix -> 463 bytes.

    Post-change: global 2 + workspace 3 + project 2, same shape, each
    inherited line carrying its `[from ~/...]` layer suffix -> 563 bytes,
    measured directly against this implementation. Delta: +100 bytes for 5
    layer suffixes (`[from ~]` on the 2 global lines, `[from ~/work]` on the
    3 workspace lines) across 7 lines whose bodies are otherwise identical
    in shape to the baseline."""
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    body = "#!/bin/sh\nexit 0\n"
    for target, n_range in ((tmp_home, 2), (work, 3), (repo, 2)):
        for n in range(n_range):
            refutations.assert_ruling(
                subject=f"subject {target.name}-{n}",
                verdict=f"the rule for subject {target.name}-{n} in publishing",
                scope="publishing", evidence=["issue:1093"], channel="cli-tty",
                ratified=True,
                check={"match": "gh pr create", "body": body, "intent": "enforce"},
                project_dir=str(target))

    lines = briefing.ruling_lines(str(repo))
    section = "\n".join(lines)
    actual = len(section.encode("utf-8"))
    baseline = 463
    assert actual == 563  # measured post-change
    assert actual - baseline == 100


# ---- Group 7: the ruling-echo filter reads the merged view -----------------


def test_admission_drops_an_inherited_verdict_and_its_layer_suffix(
        tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    verdict = "inherited verdict echoed forward"
    refutations.assert_ruling(
        subject="inherited echo subject", verdict=verdict, scope="tests",
        evidence=["issue:1093"], channel="cli-tty", ratified=True,
        project_dir=str(work))
    tag = config.home_relative(str(work))
    rendered = f"§ {verdict}  [from {tag}]"
    checkpoint = {
        "session_id": "S-echo-inherit",
        "working_context": {
            "active_topic": {"text": "deploy cadence", "trust": "inferred"},
            "open_questions": [], "recent_decisions": [],
        },
        "epistemic_snapshot": {
            "strong_beliefs": [
                {"text": rendered, "trust": "inferred"},
                {"text": "an unrelated belief survives", "trust": "inferred"},
            ],
            "uncertainties": [], "contradictions_flagged": [],
        },
    }
    out = store.write_checkpoint("S-echo-inherit", checkpoint,
                                 project_dir=str(repo), admit=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    beliefs = [i["text"] for i in data["epistemic_snapshot"]["strong_beliefs"]]
    assert rendered not in beliefs
    assert "an unrelated belief survives" in beliefs


# ---- slug + tenant scope: no cwd leak --------------------------------------


def test_slug_brief_shows_slug_line_and_no_caller_cwd_rulings(
        tmp_path, monkeypatch, capsys):
    from daimon_briefing import cli
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    slug_target = tmp_path / "slugtarget"
    slug_target.mkdir()
    _rule_at(slug_target, "slug bucket rule", subject="slug bucket subject")
    _rule_at(work, "layer rule must not leak into a slug brief",
            subject="layer leak subject")
    store.write_checkpoint("S0", {"session_id": "S0", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir=str(slug_target))
    slug = store.project_slug(str(slug_target))

    assert cli.main(["brief", "--slug", slug]) in (0, 1)
    out = capsys.readouterr().out
    assert "§ slug bucket rule" in out
    assert "layer rule must not leak into a slug brief" not in out
    assert "(inherited rulings not resolved for a slug)" in out


def test_mcp_brief_slug_shows_slug_line_and_no_caller_cwd_rulings(
        tmp_path, monkeypatch):
    from tests.test_mcp_server import rpc, _init, _call, _result
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    slug_target = tmp_path / "slugtarget2"
    slug_target.mkdir()
    _rule_at(slug_target, "mcp slug bucket rule", subject="mcp slug subject")
    _rule_at(work, "mcp layer rule must not leak", subject="mcp layer leak")
    store.write_checkpoint("S0", {"session_id": "S0", "working_context": {},
                                  "epistemic_snapshot": {}},
                           project_dir=str(slug_target))
    slug = store.project_slug(str(slug_target))

    _, out = rpc(_init(), _call("daimon_brief", {"slug": slug}))
    text, is_err = _result(out)
    assert is_err is False
    assert "§ mcp slug bucket rule" in text
    assert "mcp layer rule must not leak" not in text
    assert "(inherited rulings not resolved for a slug)" in text


def test_tenant_scoped_render_is_own_only_no_extra_line(tmp_path, monkeypatch):
    tmp_home, work, repo = _home_work_repo(tmp_path, monkeypatch)
    _rule_at(work, "layer rule invisible under tenant scope",
            subject="tenant layer subject")
    _rule_at(repo, "own rule under tenant scope", subject="tenant own subject")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")

    lines = briefing.ruling_lines(str(repo))
    joined = "\n".join(lines)
    assert "§ own rule under tenant scope" in joined
    assert "layer rule invisible under tenant scope" not in joined
    assert "unreadable" not in joined
    assert "not resolved for a slug" not in joined


# ---- coverage completeness: fail-open branches no real fixture reaches ----
#
# These call the private helpers directly with a monkeypatched collaborator,
# the same technique test_ruling_briefing.py already uses for
# briefing._slug_label and briefing.config.ruling_cap: the branch under test
# is a defensive fail-open guard against a collaborator's own failure, not a
# ledger shape any writer can produce, so there is no real fixture that
# reaches it.


def test_layer_rulings_read_fails_open_when_layer_scopes_raises(monkeypatch):
    def boom(project_dir):
        raise RuntimeError("layer_scopes exploded")

    monkeypatch.setattr(briefing.config, "layer_scopes", boom)
    assert briefing.layer_rulings_read("/some/project").layers == []


def test_merged_active_rulings_skips_a_row_with_no_refutation_id(monkeypatch):
    # `_add`'s defensive early return: every real writer stamps
    # refutation_id as the fold's own dict key, so this can only be
    # exercised by a collaborator handing back a malformed row.
    fake = briefing.RulingsRead(rows=[{"verdict": "no id here"}],
                                state="read", path=None)
    monkeypatch.setattr(briefing, "rulings_read",
                        lambda project_dir=None: fake)
    assert briefing.active_rulings("/fake/project") == []


def test_is_slug_input_is_false_for_falsy_input():
    assert briefing._is_slug_input(None) is False
    assert briefing._is_slug_input("") is False


class _Unstringable:
    """Truthy, but raises on str() — the one way to reach
    `_is_slug_input`'s and `_manifest_enforce_lines`'s per-value except
    branches without a real object ever behaving this way."""

    def __bool__(self):
        return True

    def __str__(self):
        raise RuntimeError("cannot stringify")


def test_is_slug_input_fails_open_to_false_on_a_str_error():
    assert briefing._is_slug_input(_Unstringable()) is False


def test_inherited_notes_fails_open_when_layer_rulings_read_raises(
        monkeypatch, tmp_path):
    def boom(project_dir=None):
        raise RuntimeError("layer read exploded")

    monkeypatch.setattr(briefing, "layer_rulings_read", boom)
    assert briefing._inherited_notes(str(tmp_path)) == []


def test_manifest_enforce_lines_fails_open_when_load_manifest_raises(
        monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")

    def boom():
        raise RuntimeError("manifest unreadable")

    monkeypatch.setattr(briefing.checks_runtime, "load_manifest", boom)
    assert briefing._manifest_enforce_lines(str(tmp_path), set()) == []


def test_manifest_enforce_lines_skips_bad_entries_and_renders_the_good_one(
        monkeypatch, tmp_path):
    monkeypatch.setenv("DAIMON_CAPTURE_HOST", "claude-code")
    monkeypatch.setattr(briefing.checks_runtime, "load_manifest",
                        lambda: object())
    entries = [
        "not-a-dict",                                              # isinstance
        {"intent": "warn", "ruling_id": "r-warn", "match": "x",
         "project_dir": "/x"},                                      # not enforce
        {"intent": "enforce", "ruling_id": "", "match": "x",
         "project_dir": "/x"},                                      # no id
        {"intent": "enforce", "ruling_id": "r-already", "match": "x",
         "project_dir": "/x"},                                      # already rendered
        {"intent": "enforce", "ruling_id": "r-nomatch", "match": "",
         "project_dir": "/x"},                                      # empty match
        {"intent": "enforce", "ruling_id": "r-noroot", "match": "x",
         "project_dir": ""},                                        # empty root
        {"intent": "enforce", "ruling_id": "r-boom", "match": _Unstringable(),
         "project_dir": "/x"},                                      # per-entry except
        {"intent": "enforce", "ruling_id": "r-good", "match": "gh pr create",
         "project_dir": str(tmp_path)},                             # renders
    ]
    monkeypatch.setattr(briefing.checks_runtime, "armed_for",
                        lambda cwd, manifest: entries)
    rendered_ids = {"r-already"}

    lines = briefing._manifest_enforce_lines(str(tmp_path), rendered_ids)

    tag = config.home_relative(str(tmp_path))
    assert lines == [f"§ enforced from {tag}: gh pr create  [r-good]"]
    assert rendered_ids == {"r-already", "r-good"}
