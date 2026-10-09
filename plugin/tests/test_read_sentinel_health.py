"""The read census, health axis (#1132 PR 10a, D10.9).

`test_read_sentinel.py` drives every read surface against a store with
quarantined and forgotten values. This drives the same surfaces under a
damaged ledger and asserts two things for each damage: no surface leaks a
sentinel it did not already leak (`KNOWN_LEAKS` is unchanged), and the
surfaces that carry a note say it, in the one set of words.

The damage is a seam on `jsonl.read` (TRANSIENT and OS errors, which bytes
cannot express) or planted bytes (a garbage line in a teammate's ledger).
"""

import shutil

import pytest

from daimon_briefing import config, jsonl, requests, store
from daimon_briefing.jsonl import Health
from tests import _sentinel_drive as drive, _sentinel_world as sw
from tests import test_read_sentinel as rs
from daimon_briefing.surfaces import Writer

OTHER = "other-census-project"


@pytest.fixture(scope="module")
def health_runs(tmp_path_factory):
    """The drive of every case under every damage, run while the world's
    environment is live (the module scope), judged by the tests below."""
    tmp = tmp_path_factory.mktemp("sentinel-health")
    with pytest.MonkeyPatch.context() as m:
        world = sw.build_world(tmp, m)
        other = str(tmp / OTHER)
        (tmp / OTHER).mkdir()
        # An innocuous checkpoint: a quarantine is per bucket, so the world's
        # sentinel values would be this bucket's own, visible values.
        store.write_checkpoint("O-1", {
            "session_id": "O-1", "created": "2026-08-04T00:00:00Z",
            "working_context": {"active_topic": {
                "text": "the other project ships on friday",
                "trust": "inferred"}, "recent_decisions": [],
                "open_questions": []},
            "epistemic_snapshot": {}}, project_dir=other, writer=Writer.HUMAN)
        store.append_event("o-other-1", "resolved", project_dir=other, writer=Writer.HUMAN)
        requests.open_request(
            to=store.project_slug(world.project), ask="please review this",
            why="it blocks us", channel="cli-agent", project_dir=other)
        world.other = other
        clean = drive.Pristine(tmp, tmp.parent / (tmp.name + "-clean"))
        out = {}
        for name, (apply, _expected) in CONDITIONS.items():
            clean.restore()
            keep = tmp.parent / (tmp.name + "-keep-" + name)
            with pytest.MonkeyPatch.context() as damage:
                apply(damage, world)
                pristine = drive.Pristine(tmp, keep)
                leaks, shown = set(), {}
                for key in rs.CASES:
                    if key[0] in rs.NO_ITEMS:
                        continue
                    found, results = rs.drive_case(key[0], key[1], world,
                                                   pristine)
                    leaks |= {(s, kind) for s, _t, kind in found}
                    shown[key] = "\n".join(res.text()
                                           for _label, res in results)
            shutil.rmtree(keep, ignore_errors=True)
            out[name] = (leaks, shown)
        clean.restore()
        yield out


def _bucket(project):
    return config.checkpoint_dir() / store.project_slug(project)


def _seam(m, target, result):
    real = jsonl.read

    def read(path, *a, **k):
        return result if path == target else real(path, *a, **k)

    m.setattr(jsonl, "read", read)


def _refuse_admissions(m, w):
    """Own events ledger unproven (an OS error) and one session of this
    project waiting in serialize.log as a refused admission."""
    _seam(m, _bucket(w.project) / "events.jsonl",
          jsonl.Read(Health.UNREADABLE, [], detail="EIO"))
    log_dir = config.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "serialize.log").write_text(
        "2026-09-01T00:00:00Z session-end: spawned serialize for S-refused "
        f"(reason: x, project: {w.project}) (transcript: /t/S-refused.jsonl)\n"
        "error: admission refused: events.jsonl is unreadable; check "
        "permissions (EIO); run: daimon status (transcript: "
        "/t/S-refused.jsonl) after 0s\n")


def _grace_sidecar_dir():
    found = [p for p in config.team_dir().rglob("authors/grace") if p.is_dir()]
    assert found, "the world has no teammate sidecar"
    return found[0]


CONDITIONS = {
    "trust-transient": (
        lambda m, w: _seam(m, _bucket(w.project) / "trust.jsonl",
                           jsonl.Read(Health.TRANSIENT, [], detail="EBUSY")),
        {("cli:brief", "default"): "⚠ trust.jsonl is transient (EBUSY); retry",
         ("mcp:daimon_brief", "default"):
             "⚠ trust.jsonl is transient (EBUSY); retry"}),
    "own-events-os-error": (
        lambda m, w: _seam(m, _bucket(w.project) / "events.jsonl",
                           jsonl.Read(Health.UNREADABLE, [], detail="EIO")),
        {("cli:brief", "default"):
             "⚠ events.jsonl is unreadable (EIO); check permissions (EIO)",
         ("mcp:daimon_recall", "query"): "the forget set is incomplete",
         ("cli:status", "default"): "forget set incomplete",
         ("cli:brief", "team"): "teammates' checkpoints are not shown"}),
    "foreign-events-os-error": (
        lambda m, w: _seam(m, _bucket(w.other) / "events.jsonl",
                           jsonl.Read(Health.UNREADABLE, [], detail="EIO")),
        {("cli:brief", "default"): "the forget set is incomplete",
         ("cli:projects", "default"): "the forget set is incomplete",
         ("http:/api/projects", "default"): "the forget set is incomplete"}),
    "admission-refused": (
        _refuse_admissions,
        {("cli:brief", "default"):
             "1 session(s) of this project not serialized: events.jsonl is "
             "unreadable; check permissions (EIO); run: daimon status, then "
             "daimon heal",
         ("cli:loops", "default"): "1 session(s) of this project not "
                                   "serialized",
         ("mcp:daimon_brief", "default"): "1 session(s) of this project not "
                                          "serialized",
         ("hook:pre_llm_call", "first"): "1 session(s) of this project not "
                                         "serialized"}),
    "foreign-requests-garbage": (
        lambda m, w: _seam(m, _bucket(w.other) / "requests.jsonl",
                           jsonl.Read(Health.UNREADABLE, [], detail="garbage",
                                      garbage=1)),
        {("mcp:requests_inbox", "default"): "1 sender(s) skipped",
         ("cli:request inbox", "default"): "1 sender(s) skipped"}),
    "foreign-author-unproven": (
        lambda m, w: (_grace_sidecar_dir() / "tombstones.jsonl").write_bytes(
            b"<<<<<<< HEAD\n"),
        {("cli:brief", "team"): "a teammate's tombstones cannot be read"}),
}


# What a damaged ledger lets through that the healthy world does not, by
# condition. Equality, not a subset: an entry that stops leaking must be
# deleted, and nothing else may start.
#
# trust-transient: the snapshot is CLOSED (every item is withheld), but 7a
# renders the human-ratified prose surfaces with `closed_masks=False`: a
# standing ruling, a request panel's ask and a verdict note keep showing even
# when they equal a quarantined value, because nothing can be proven
# quarantined and the human ratified that text. The same lines show under an
# UNREADABLE trust ledger today. `blame` prints the judged events of one id,
# and an event note is human prose with the same policy (`view.events`), so
# a note that equals a quarantined value shows while the ledger is closed.
ALLOWED = {
    "trust-transient": {
        ("cli:blame", "contradiction"),
        ("cli:brief", "contradiction"), ("cli:brief", "question"),
        ("cli:brief", "topic"), ("hook:pre_llm_call", "question"),
        ("http:/api/activity", "contradiction"),
        ("http:/api/activity", "topic"), ("mcp:daimon_brief", "question")},
}


@pytest.mark.parametrize("name", sorted(CONDITIONS))
def test_a_damaged_ledger_leaks_nothing_new_and_says_so(health_runs, name):
    leaks, shown = health_runs[name]
    assert leaks - rs.KNOWN_LEAKS == ALLOWED.get(name, set()), sorted(
        leaks - rs.KNOWN_LEAKS)
    for key, text in CONDITIONS[name][1].items():
        assert text in shown[key], (name, key)


def test_the_known_leak_list_is_unchanged_by_this_axis():
    assert len(rs.KNOWN_LEAKS) == 84
