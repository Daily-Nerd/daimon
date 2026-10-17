"""A ruling row says whether every enforcement value it carries is shown (#1206).

A ruling enforces, so a silently shorter anchor set would fail open. The row
says `as_ratified: false` when an enforcement value was withheld for any
reason, and still never names, counts or locates it. Refutation rows carry no
such key.
"""

import json
import subprocess

from daimon_briefing import cli, refutations, surfaces
from daimon_briefing.cli import _ledger
from tests import _masking as m

TEXT_LINE = "not as ratified: an enforcement value is withheld"


def _ruling(project=m.PROJECT, **kw):
    values = dict(subject="landing on main", verdict=m.VISIBLE,
                  scope="census", evidence=["issue:1"], channel="cli-agent",
                  project_dir=project)
    values.update(kw)
    return refutations.assert_ruling(**values)


def _rows(capsys, *argv):
    rc, out, _ = m.run(capsys, *argv)
    assert rc == 0
    return json.loads(out)


def test_a_forgotten_anchor_clears_the_bit_in_list_and_show(
        tmp_checkpoint_dir, capsys):
    full = _ruling(anchors=["issue:1", "issue:2"])
    short = _ruling(subject="second", anchors=["issue:1", m.FORGOTTEN])
    m.forget()
    rows = {r["refutation_id"]: r
            for r in _rows(capsys, "ruling", "list", "--json")}
    assert rows[full]["as_ratified"] is True
    assert rows[short]["as_ratified"] is False
    assert rows[short]["anchors"] == ["issue:1"]
    shown = _rows(capsys, "ruling", "show", short, "--json")
    assert shown["as_ratified"] is False
    assert _rows(capsys, "ruling", "show", full, "--json")["as_ratified"]
    for argv in (("ruling", "list"), ("ruling", "show", short)):
        rc, out, _ = m.run(capsys, *argv)
        assert rc == 0 and TEXT_LINE in out
        assert "forgotten" not in out and "SECRET" not in out
    rc, out, _ = m.run(capsys, "ruling", "show", full)
    assert rc == 0 and TEXT_LINE not in out
    rc, out, _ = m.run(capsys, "ruling", "list")
    assert out.count(TEXT_LINE) == 1


def test_a_quarantined_anchor_clears_the_bit(tmp_checkpoint_dir, capsys):
    rid = _ruling(anchors=["issue:1", m.QUARANTINED.casefold()])
    m.quarantine()
    row = _rows(capsys, "ruling", "show", rid, "--json")
    assert row["as_ratified"] is False
    assert "SECRET" not in json.dumps(row)


def test_a_forgotten_check_match_clears_the_bit(tmp_checkpoint_dir, capsys):
    rid = _ruling(check={"match": m.FORGOTTEN, "body": "a body",
                         "intent": "warn"})
    m.forget()
    assert _rows(capsys, "ruling", "show", rid, "--json")[
        "as_ratified"] is False


def test_a_forgotten_check_body_clears_the_bit(tmp_checkpoint_dir, capsys):
    rid = _ruling(check={"match": "a match", "body": m.FORGOTTEN,
                         "intent": "warn"})
    m.forget()
    assert _rows(capsys, "ruling", "show", rid, "--json")[
        "as_ratified"] is False


def test_a_withheld_guard_match_anchor_clears_the_bit(tmp_checkpoint_dir):
    rid = _ruling(anchors=["issue:1"])
    row = dict(refutations.records(project_dir=m.PROJECT)[rid])
    row["guard_match"] = {"rail": "anchor", "anchors": [m.FORGOTTEN]}
    m.forget()
    shown = _ledger._masked_records(m.PROJECT, [row])[0]
    assert shown["as_ratified"] is False
    assert shown["guard_match"]["anchors"] == []


def test_a_clean_ruling_is_as_ratified(tmp_checkpoint_dir, capsys):
    rid = _ruling(anchors=["issue:1", "issue:2"])
    assert _rows(capsys, "ruling", "show", rid, "--json")[
        "as_ratified"] is True


def test_a_refutation_row_carries_no_such_key(tmp_checkpoint_dir, capsys):
    rid = refutations.assert_refutation(
        subject="a refuted idea", verdict=m.VISIBLE, scope="census",
        evidence=["issue:1"], channel="cli-agent",
        anchors=["issue:1", m.FORGOTTEN], project_dir=m.PROJECT)
    m.forget()
    rc, out, _ = m.run(capsys, "refute", "show", rid, "--json")
    assert rc == 0 and "as_ratified" not in json.loads(out)


def test_a_forgotten_non_enforcement_field_keeps_the_bit(
        tmp_checkpoint_dir, capsys):
    rid = _ruling(scope=m.FORGOTTEN, anchors=["issue:1"])
    m.forget()
    row = _rows(capsys, "ruling", "show", rid, "--json")
    assert row["as_ratified"] is True and row["scope"] == ""
    rc, out, _ = m.run(capsys, "ruling", "show", rid)
    assert "forgotten" not in out and TEXT_LINE not in out


def test_an_inherited_ruling_carries_the_bit(
        tmp_checkpoint_dir, tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    work, repo = home / "work", home / "work" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("HOME", str(home))
    rid = _ruling(str(work), anchors=["issue:1", m.FORGOTTEN])
    refutations.ratify(rid, channel="cli-tty", project_dir=str(work))
    other = _ruling(str(work), subject="clean", anchors=["issue:1"])
    refutations.ratify(other, channel="cli-tty", project_dir=str(work))
    _ruling(str(repo), subject="child", scope="child")
    m.forget()  # the forgotten set is machine wide, the layer's row is judged
    capsys.readouterr()
    assert cli.main(["ruling", "list", "--inherited", "--json",
                     "--project", str(repo)]) == 0
    rows = {r["refutation_id"]: r for r in json.loads(capsys.readouterr().out)}
    assert rows[rid]["as_ratified"] is False
    assert rows[other]["as_ratified"] is True


def test_enforcement_paths_are_declared_prose_paths():
    declared = set()
    s = surfaces.bucket_ledger("refutations.jsonl")
    for fp in s.prose + s.folded_prose:
        declared.add(surfaces.path_string(fp))
    assert refutations.ENFORCEMENT_PATHS <= declared
    assert refutations.ENFORCEMENT_PATHS == {
        "anchors[]", "guard_match.anchors[]", "check.match", "check.body"}
