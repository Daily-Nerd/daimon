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
    mate.write_text(json.dumps(mate_payload, ensure_ascii=False),
                    encoding="utf-8")
    before = mate.read_text()
    assert CANARY in before
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
    other.write_text(json.dumps(other_payload, ensure_ascii=False),
                     encoding="utf-8")
    before = other.read_text()
    assert CANARY in before
    _forget()
    assert other.read_text() == before


def test_audit_is_clean_after_forget_when_only_own_copies_exist(
        tmp_checkpoint_dir, monkeypatch):
    _seed_mirrored_sessions(monkeypatch)
    _forget()
    result = privacy.audit_project(project_dir=PROJECT)
    hits = [f for f in result["findings"] if f["content_hash"] == KEY]
    assert hits == [], f"audit still finds residue: {hits}"


def test_team_scrub_edge_guards(tmp_checkpoint_dir, monkeypatch):
    """Guard branches: empty key, garbage / non-dict payloads, a half-torn
    file whose good section scrubs but whose torn sibling section trips the
    stricter re-admission (skipped, not fatal), and an unreadable team
    dir."""
    _seed_mirrored_sessions(monkeypatch)
    assert store.scrub_team_copies("", project_dir=PROJECT) == []
    own = store.project_slug("Ada")
    author_dir = config.team_dir() / "local" / "authors" / own
    (author_dir / "garbage.json").write_text("not json", encoding="utf-8")
    (author_dir / "list.json").write_text('["a", "list"]', encoding="utf-8")
    half = author_dir / "half-torn.json"
    half.write_text(json.dumps({
        "session_id": "H", "author": "Ada",
        "project_slug": store.project_slug(PROJECT),
        "working_context": {"recent_decisions": [
            {"text": CANARY, "trust": "inferred"}]},
        "epistemic_snapshot": "torn",
    }, ensure_ascii=False), encoding="utf-8")
    before = half.read_text()
    _forget()
    assert half.read_text() == before, \
        "a file re-admission cannot process is left byte-identical"
    # a resolver crash degrades to slug-only scoping, never aborts (belt —
    # read_candidates documents it never raises)
    from daimon_briefing import teamproject
    monkeypatch.setattr(teamproject, "read_candidates",
                        lambda project_dir: (_ for _ in ()).throw(OSError()))
    assert store.scrub_team_copies("deadbeef", project_dir=PROJECT) == []
    monkeypatch.setattr(type(config.team_dir()), "iterdir",
                        lambda self: (_ for _ in ()).throw(OSError()))
    assert store.scrub_team_copies("deadbeef", project_dir=PROJECT) == []


def test_forget_scrubs_nested_copy_written_from_another_checkout(
        tmp_checkpoint_dir, monkeypatch):
    """Refuter BLOCKER: the nested-layout logical-path check was dead code
    (`parts[1:-2]` kept the `authors` segment, so it never matched) — a
    mirror copy of the SAME logical project written from a different
    checkout path (worktree, second machine, moved repo) carries that
    path's project_slug and was silently missed while the audit correctly
    flagged it forever."""
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/finance")
    _seed_mirrored_sessions(monkeypatch)
    own = store.project_slug("Ada")
    other = (config.team_dir() / "local" / "projects" / "core" / "finance"
             / "authors" / own / "SA.json")
    other.parent.mkdir(parents=True, exist_ok=True)
    payload = _cp("SA", [CANARY])
    payload["author"] = "Ada"
    payload["project_slug"] = store.project_slug("/p/another-checkout")
    # ensure_ascii=False: CANARY carries a non-ASCII char, and the default
    # \uXXXX escaping made the residue assertion below pass vacuously.
    other.write_text(json.dumps(payload, ensure_ascii=False),
                     encoding="utf-8")
    assert CANARY in other.read_text()
    _forget()
    assert CANARY not in other.read_text(), \
        "same logical project, different checkout — must be scrubbed"


def test_one_torn_mirror_file_never_aborts_the_forget(
        tmp_checkpoint_dir, monkeypatch):
    """Refuter MAJOR: an unguarded re-admission crashed on a torn payload
    (working_context as a string trips redact_checkpoint's `or {}`) AFTER
    the tombstone landed but BEFORE the event scrub and cache purge — and
    a second forget of the same value refuses ('no item matches'), so the
    residue was stranded. Best-effort per file must mean it."""
    _seed_mirrored_sessions(monkeypatch)
    own = store.project_slug("Ada")
    torn = config.team_dir() / "local" / "authors" / own / "AA-torn.json"
    torn.parent.mkdir(parents=True, exist_ok=True)
    torn.write_text(json.dumps({
        "session_id": "T", "author": "Ada",
        "project_slug": store.project_slug(PROJECT),
        "working_context": "not a dict",
    }), encoding="utf-8")
    before = torn.read_text()
    _forget()          # asserts rc 0 — the torn file must not abort
    assert torn.read_text() == before
    residue = [str(p) for p in _mirror_files()
               if p.name != "AA-torn.json" and CANARY in p.read_text()]
    assert residue == [], f"files after the torn one unscrubbed: {residue}"
