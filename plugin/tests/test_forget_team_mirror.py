"""#600 slice A: forget scrubs the author's OWN team-mirror copies.

The mirror under ~/.daimon/team holds full plaintext checkpoint copies, and
forget never walked it — store.project_surfaces is rooted at the checkpoint
dir, a sibling. The one mirror file that DID get scrubbed was the live
session's, and only because forget's rewrite happens to dual-write while
DAIMON_TEAM is on — a coincidence, not a contract.

Scope discipline: own-author files only (the author dir name is the reliable
discriminator — store._dual_write_team and teamsync._own_pathspecs derive it
identically), and only for this project (nested segments in
teamproject.read_candidates, or the payload's own project_slug stamp).
Teammates' copies and upstream git history are the remaining #600 gap
(slice B): the sync protocol must carry the tombstone; no local scrub can
reach a remote. The spike verdict ("deletes race appends") forbids pointer
files and cross-author deletes — a rewrite inside the disjoint own-author
dir is non-racing by the same construction that makes appends safe.

Each rewrite passes through policy.admit_checkpoint — re-admission under the
project's forgotten keys — so the write-audit guard binds the bytes to a
real admission (the scrub_event_fields precedent), not to the correlation
blind spot.
"""
import json

from daimon_briefing import cli, config, normalize, privacy, store

PROJECT = "/p/team-mirror-forget"
CANARY = "zqxteamcanary5527 the payroll export token lives in vault七"
KEEPER = "an unrelated decision that must survive"

KEY = normalize.content_key(CANARY)


def _cp(sid, items):
    return {
        "session_id": sid,
        "created": "2026-08-01T00:00:00Z",
        "working_context": {
            "recent_decisions": [{"text": t, "trust": "inferred"}
                                 for t in items]},
    }


def _mirror_files():
    return sorted(p for p in config.team_dir().rglob("*.json"))


def _forget(monkeypatch=None):
    assert cli.main(["forget", CANARY, "--project", PROJECT]) == 0


def _seed_mirrored_sessions(monkeypatch):
    monkeypatch.setenv("DAIMON_TEAM", "1")
    monkeypatch.setenv("DAIMON_AUTHOR", "Ada")
    store.write_checkpoint("S1", _cp("S1", [CANARY, KEEPER]),
                           project_dir=PROJECT)
    store.write_checkpoint("S2", _cp("S2", [KEEPER]), project_dir=PROJECT)


def test_forget_scrubs_every_own_mirror_copy(tmp_checkpoint_dir, monkeypatch):
    _seed_mirrored_sessions(monkeypatch)
    assert any(CANARY in p.read_text() for p in _mirror_files())
    _forget()
    residue = [str(p) for p in _mirror_files() if CANARY in p.read_text()]
    assert residue == [], f"mirror plaintext survives in {residue}"


def test_forget_scrubs_mirror_even_when_team_is_off_at_forget_time(
        tmp_checkpoint_dir, monkeypatch):
    """The mirror was written while DAIMON_TEAM was on; the flag being off
    LATER must not orphan the plaintext — deletion parallels the kill-switch
    exemption, it does not honor the mirroring toggle."""
    _seed_mirrored_sessions(monkeypatch)
    monkeypatch.delenv("DAIMON_TEAM")
    _forget()
    residue = [str(p) for p in _mirror_files() if CANARY in p.read_text()]
    assert residue == [], f"mirror plaintext survives in {residue}"


def test_forget_never_touches_a_teammate_copy(tmp_checkpoint_dir, monkeypatch):
    """A teammate's file is not ours to rewrite (disjoint author dirs are
    what make the sync conflict-free); their copy converges via slice B's
    tombstone propagation, and the audit keeps reporting it meanwhile."""
    _seed_mirrored_sessions(monkeypatch)
    mate = (config.team_dir() / "local" / "authors" / "Grace" / "SX.json")
    mate.parent.mkdir(parents=True, exist_ok=True)
    mate_payload = _cp("SX", [CANARY])
    mate_payload["author"] = "Grace"
    mate_payload["project_slug"] = store.project_slug(PROJECT)
    mate.write_text(json.dumps(mate_payload), encoding="utf-8")
    before = mate.read_text()
    _forget()
    assert mate.read_text() == before


def test_forget_team_scrub_is_project_scoped(tmp_checkpoint_dir, monkeypatch):
    """An own-author copy stamped with a DIFFERENT project's slug is another
    project's belief state — this project's tombstone must not reach it."""
    _seed_mirrored_sessions(monkeypatch)
    other = (config.team_dir() / "local" / "authors"
             / store.project_slug("Ada") / "SO.json")
    other.parent.mkdir(parents=True, exist_ok=True)
    other_payload = _cp("SO", [CANARY])
    other_payload["author"] = "Ada"
    other_payload["project_slug"] = store.project_slug("/p/other-project")
    other.write_text(json.dumps(other_payload), encoding="utf-8")
    before = other.read_text()
    _forget()
    assert other.read_text() == before


def test_audit_is_clean_after_forget_when_only_own_copies_exist(
        tmp_checkpoint_dir, monkeypatch):
    _seed_mirrored_sessions(monkeypatch)
    _forget()
    result = privacy.audit_project(project_dir=PROJECT)
    hits = [f for f in result["findings"] if f["content_hash"] == KEY]
    assert hits == [], f"audit still finds residue: {hits}"
