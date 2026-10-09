"""The ruling verbs print what they read through `view.masked`, and a ceremony
never asks for a signature on text the person cannot read (#1132 PR 11c)."""

import json
import subprocess

from daimon_briefing import cli, refutations, trust, view
from tests import _masking as m


def _ruling(project=m.PROJECT, *, ratify=True, **kw):
    values = dict(subject="census subject", verdict=m.VISIBLE,
                  scope="census scope", evidence=["issue:693"],
                  channel="cli-tty", ratified=False, project_dir=project)
    values.update(kw)
    rid = refutations.assert_ruling(**values)
    if ratify:
        refutations.ratify(rid, channel="cli-tty", project_dir=project)
    return rid


def _row_count():
    return len(refutations.events(project_dir=m.PROJECT))


def test_show_masks_a_pending_revision_and_a_check(tmp_checkpoint_dir, capsys):
    rid = _ruling()
    refutations.revise(rid, channel="cli-agent", evidence=["issue:1"],
                       subject=m.QUARANTINED, verdict=m.FORGOTTEN,
                       project_dir=m.PROJECT)
    refutations.assert_ruling(
        subject="with a check", verdict="a rule", scope="chk",
        evidence=["issue:693"], channel="cli-agent",
        check={"match": m.QUARANTINED, "body": m.FORGOTTEN,
               "intent": "warn"}, project_dir=m.PROJECT)
    tid = m.quarantine()
    m.forget()
    rc, out, _ = m.run(capsys, "ruling", "show", rid)
    assert rc == 0 and "SECRET" not in out
    assert "Pending revision proposal" in out
    rc, out, _ = m.run(capsys, "ruling", "show", rid, "--json")
    assert rc == 0 and "SECRET" not in out
    proposal = json.loads(out)["revision_proposed"]
    assert proposal["subject"] == f"[withheld: quarantine {tid}]"
    assert proposal["verdict"] == ""
    rc, out, _ = m.run(capsys, "ruling", "list", "--json")
    assert rc == 0 and "SECRET" not in out


def test_list_judges_the_bucket_once_not_once_per_row(tmp_checkpoint_dir,
                                                       capsys, monkeypatch):
    for n in range(4):
        _ruling(subject=f"subject {n}", scope=f"scope {n}", ratify=False)
    calls = []
    real = view.judge
    monkeypatch.setattr(view, "judge",
                        lambda slug, **k: calls.append(slug) or real(slug, **k))
    rc, out, _ = m.run(capsys, "ruling", "list")
    assert rc == 0
    assert calls.count(m.SLUG) == 1, calls


def test_ratify_refuses_a_pending_proposal_it_cannot_show(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _ruling()
    refutations.revise(rid, channel="cli-agent", evidence=["issue:1"],
                       verdict=m.QUARANTINED, project_dir=m.PROJECT)
    tid = m.quarantine()
    m.human(monkeypatch)
    before = _row_count()
    rc, out, err = m.run(capsys, "ruling", "ratify", rid)
    assert rc == 2 and "SECRET" not in out + err
    assert ("error: ruling ratify refused: this record has withheld text; "
            "nothing was written") in err
    assert f"note: daimon trust show {tid} on a terminal" in err
    assert _row_count() == before


def test_a_clean_ratify_still_signs_the_stored_text(tmp_checkpoint_dir,
                                                    capsys, monkeypatch):
    rid = _ruling(ratify=False)
    m.quarantine()                         # unrelated to this ruling's text
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "ruling", "ratify", rid)
    assert rc == 0 and m.VISIBLE in out
    assert refutations.get(rid, project_dir=m.PROJECT)["state"] == "active"


def test_a_json_ceremony_stays_on_stderr_and_stdout_parses(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _ruling(ratify=False)
    m.human(monkeypatch)
    rc, out, err = m.run(capsys, "ruling", "ratify", rid, "--json")
    assert rc == 0 and "About to ratify" in err
    assert json.loads(out)["refutation_id"] == rid


def test_revise_refuses_when_the_record_or_the_typed_text_is_withheld(
        tmp_checkpoint_dir, capsys, monkeypatch):
    held = _ruling(subject="held", scope="held", verdict=m.QUARANTINED)
    fine = _ruling(subject="fine", scope="fine")
    m.quarantine()
    m.human(monkeypatch)
    before = _row_count()
    rc, _, err = m.run(capsys, "ruling", "revise", held, "--verdict", "x",
                       "--evidence", "issue:1")
    assert rc == 2 and "ruling revise refused" in err
    rc, out, err = m.run(capsys, "ruling", "revise", fine, "--verdict",
                         m.QUARANTINED, "--evidence", "issue:1")
    assert rc == 2 and "SECRET" not in out + err
    assert "ruling revise refused" in err
    assert _row_count() == before


def test_propose_ratify_refuses_a_withheld_typed_verdict(
        tmp_checkpoint_dir, capsys, monkeypatch):
    m.quarantine()
    m.human(monkeypatch)
    before = _row_count()
    rc, out, err = m.run(capsys, "ruling", "propose", "--subject", "s",
                         "--verdict", m.QUARANTINED, "--scope", "sc",
                         "--evidence", "issue:1", "--ratify")
    assert rc == 2 and "SECRET" not in out + err
    assert "ruling propose refused" in err
    assert _row_count() == before


def test_propose_without_a_ceremony_echoes_masked(tmp_checkpoint_dir, capsys,
                                                  monkeypatch):
    tid = m.quarantine()
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "ruling", "propose", "--subject", "s",
                       "--verdict", m.QUARANTINED, "--scope", "sc",
                       "--evidence", "issue:1", "--json")
    assert rc == 0 and "SECRET" not in out
    assert json.loads(out)["verdict"] == f"[withheld: quarantine {tid}]"


def test_retire_echoes_a_masked_record(tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _ruling(verdict=m.QUARANTINED)
    m.quarantine()
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "ruling", "retire", rid, "--evidence",
                       "issue:1", "--json")
    assert rc == 0 and "SECRET" not in out
    assert refutations.get(rid, project_dir=m.PROJECT)["state"] == "overturned"


# ---- a ruling inherited from a layer is judged by the layer's own bucket ----


def _layers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    work, repo = home / "work", home / "work" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("HOME", str(home))
    return work, repo


def test_an_inherited_ruling_is_masked_by_the_layer_it_came_from(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    work, repo = _layers(tmp_path, monkeypatch)
    _ruling(str(repo), subject="child", scope="child")
    _ruling(str(work), subject="layer", scope="layer", verdict=m.QUARANTINED)
    # the quarantine belongs to the LAYER's bucket, so the child's own bucket
    # (which has none) must not be what judges the layer's text
    tid = trust.propose(text=m.QUARANTINED, kind="decision", reason="r",
                        evidence=["issue:1"], channel="cli-tty",
                        project_dir=str(work))
    for argv in (("ruling", "list", "--inherited"),
                 ("ruling", "list", "--inherited", "--json")):
        capsys.readouterr()
        assert cli.main([*argv, "--project", str(repo)]) == 0
        out = capsys.readouterr().out
        assert "SECRET" not in out, argv
        assert "withheld: quarantine" in out
    assert tid in out


def test_the_ceremony_lists_inherited_rules_masked(tmp_checkpoint_dir,
                                                   tmp_path, monkeypatch,
                                                   capsys):
    work, repo = _layers(tmp_path, monkeypatch)
    _ruling(str(work), subject="layer", scope="layer", verdict=m.QUARANTINED)
    trust.propose(text=m.QUARANTINED, kind="decision", reason="r",
                  evidence=["issue:1"], channel="cli-tty",
                  project_dir=str(work))
    rid = _ruling(str(repo), subject="child", scope="child", ratify=False)
    m.human(monkeypatch)
    capsys.readouterr()
    assert cli.main(["ruling", "ratify", rid, "--project", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Inherited rulings in force here:" in out
    assert "SECRET" not in out and "withheld: quarantine" in out


# ---- a clean record still goes through every ceremony branch ---------------


def test_propose_ratify_shows_a_clean_typed_text_and_its_check(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    body = tmp_path / "check.sh"
    body.write_text("echo hello\nexit 0\n")
    m.human(monkeypatch)
    rc, out, _ = m.run(
        capsys, "ruling", "propose", "--subject", "typed subject",
        "--verdict", "typed verdict", "--scope", "typed scope", "--evidence",
        "issue:1", "--ratify", "--check-match", "hello", "--check-body-file",
        str(body))
    assert rc == 0 and "About to found and ratify" in out
    assert "typed verdict" in out and "Check:" in out
    rc, out, err = m.run(
        capsys, "ruling", "propose", "--subject", "typed two", "--verdict",
        "typed verdict two", "--scope", "typed scope two", "--evidence",
        "issue:1", "--ratify", "--json")
    assert rc == 0 and "typed verdict two" in err         # ceremony on stderr
    assert json.loads(out)["state"] == "active"


def test_ratify_accepts_a_clean_pending_revision_in_text_and_json(
        tmp_checkpoint_dir, capsys, monkeypatch):
    rid = _ruling()
    refutations.revise(rid, channel="cli-agent", evidence=["issue:1"],
                       subject="a new subject", verdict="a new verdict",
                       project_dir=m.PROJECT)
    m.human(monkeypatch)
    rc, out, _ = m.run(capsys, "ruling", "ratify", rid)
    assert rc == 0 and "New text: a new verdict" in out
    assert "New governs: a new subject" in out
    rid2 = _ruling(subject="other", scope="other scope")
    refutations.revise(rid2, channel="cli-agent", evidence=["issue:1"],
                       verdict="another verdict", project_dir=m.PROJECT)
    rc, out, err = m.run(capsys, "ruling", "ratify", rid2, "--json")
    assert rc == 0 and "another verdict" in err
    assert json.loads(out)["verdict"] == "another verdict"


def test_revise_shows_a_clean_typed_change_and_its_check(
        tmp_checkpoint_dir, capsys, monkeypatch, tmp_path):
    rid = _ruling()
    body = tmp_path / "check.sh"
    body.write_text("echo hello\nexit 0\n")
    m.human(monkeypatch)
    rc, out, _ = m.run(
        capsys, "ruling", "revise", rid, "--verdict", "typed change",
        "--subject", "typed governs", "--evidence", "issue:1",
        "--check-match", "hello", "--check-body-file", str(body))
    assert rc == 0 and "About to change the ACTIVE text" in out
    assert "New text: typed change" in out and "New governs: typed governs" in out
    assert "New check:" in out


def test_an_inherited_rule_whose_text_was_forgotten_is_left_out(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    from daimon_briefing import normalize, store
    from daimon_briefing.surfaces import Writer
    work, repo = _layers(tmp_path, monkeypatch)
    _ruling(str(work), subject="layer", scope="layer", verdict=m.FORGOTTEN)
    _ruling(str(work), subject="layer two", scope="two", verdict=m.VISIBLE)
    assert store.append_event(
        "o-x", "forgotten:" + normalize.content_key(m.FORGOTTEN),
        kind="tombstone", tombstone=True, project_dir=str(work),
        writer=Writer.HUMAN)
    rid = _ruling(str(repo), subject="child", scope="child", ratify=False)
    m.human(monkeypatch)
    capsys.readouterr()
    assert cli.main(["ruling", "ratify", rid, "--project", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "Inherited rulings in force here:" in out
    assert m.VISIBLE in out and "SECRET-F" not in out


def test_ruling_revise_and_propose_refuse_on_every_channel_with_the_cure(
        tmp_checkpoint_dir, capsys, monkeypatch):
    held = _ruling(subject="held", scope="held", verdict=m.QUARANTINED)
    tid = m.quarantine()
    cure = (f"note: daimon trust show {tid} on a terminal, "
            f"then daimon trust release {tid}")
    for tty, extra in ((True, ()), (False, ("--by", "agent"))):
        m.human(monkeypatch, tty=tty)
        rc, _, err = m.run(capsys, "ruling", "revise", held, "--verdict", "x",
                           "--evidence", "issue:1", *extra)
        assert rc == 2 and "ruling revise refused" in err and cure in err, tty
    m.human(monkeypatch, tty=True)
    rc, _, err = m.run(capsys, "ruling", "propose", "--subject", "s",
                       "--verdict", m.QUARANTINED, "--scope", "sc",
                       "--evidence", "issue:1", "--ratify")
    assert rc == 2 and cure in err
    cand = _ruling(subject="cand", scope="cand", verdict=m.QUARANTINED,
                   ratify=False)
    rc, _, err = m.run(capsys, "ruling", "ratify", cand)
    assert rc == 2 and "ruling ratify refused" in err and cure in err


def test_a_forgotten_ruling_field_names_the_forgotten_cure(
        tmp_checkpoint_dir, capsys, monkeypatch):
    held = _ruling(subject="held2", scope="held2", verdict=m.FORGOTTEN,
                   ratify=False)
    m.forget()
    m.human(monkeypatch, tty=True)
    rc, _, err = m.run(capsys, "ruling", "ratify", held)
    assert rc == 2 and ("note: the withheld text was forgotten; the record "
                        "cannot be revised") in err
