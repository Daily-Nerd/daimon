"""Foreign sources and their health (#1132 PR 10a, D10.1).

A request ledger of another bucket and a teammate's published tombstones are
read across a boundary. When one cannot be proven it is skipped with a code
(`sender-skipped`, `author-skipped`), never read as empty.
"""

import json


from daimon_briefing import (api, config, jsonl, requests, store, surfaces,
                             view)
from daimon_briefing.jsonl import Health

SENDER = "/p/sender-health"
RECIPIENT = "/p/recipient-health"
ASK = "review the retrieval bar proposal before Friday"
WHY = "it blocks the release note we owe the team"


def _open(sender=SENDER, to=RECIPIENT):
    return requests.open_request(
        to=store.project_slug(to), ask=ASK, why=WHY, channel="cli-agent",
        project_dir=sender)


def _ledger(project=SENDER):
    return (config.checkpoint_dir() / store.project_slug(project)
            / "requests.jsonl")


def _seam(monkeypatch, path, result):
    real = jsonl.read

    def read(p, *a, **k):
        return result if p == path else real(p, *a, **k)

    monkeypatch.setattr(jsonl, "read", read)


def test_a_healthy_sender_is_joined_without_a_note(tmp_checkpoint_dir):
    rid = _open()
    got = requests.inbox(RECIPIENT)
    assert [r["request_id"] for r in got.rows] == [rid]
    assert got.notes == ()


def test_inbox_listing_is_the_rows_of_the_typed_core(tmp_checkpoint_dir):
    _open()
    assert requests.inbox_listing(project_dir=RECIPIENT) == requests.inbox(
        RECIPIENT).rows


def test_a_sender_with_garbage_in_its_ledger_is_skipped_with_a_note(
        tmp_checkpoint_dir):
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    got = requests.inbox(RECIPIENT)
    assert got.rows == []
    assert got.notes == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read",)
    assert requests.inbox_listing(project_dir=RECIPIENT) == []


def test_a_transient_sender_ledger_is_skipped_too(tmp_checkpoint_dir,
                                                  monkeypatch):
    _open()
    _seam(monkeypatch, _ledger(), jsonl.Read(Health.TRANSIENT, [],
                                              detail="EBUSY"))
    got = requests.inbox(RECIPIENT)
    assert got.rows == [] and len(got.notes) == 1


def test_an_os_error_sender_ledger_is_skipped_too(tmp_checkpoint_dir,
                                                  monkeypatch):
    _open()
    _seam(monkeypatch, _ledger(), jsonl.Read(Health.UNREADABLE, [],
                                              detail="EIO"))
    assert requests.inbox(RECIPIENT).rows == []


def test_a_degraded_sender_ledger_is_read_around(tmp_checkpoint_dir):
    rid = _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b'{"event": "opened", "request_id": "q-torn')
    got = requests.inbox(RECIPIENT)
    assert [r["request_id"] for r in got.rows] == [rid]
    assert got.notes == (
        "⚠ 1 sender(s) have a requests ledger with torn lines; "
        "their asks may be incomplete",)


def test_the_note_carries_no_count_under_tenant_scope(tmp_checkpoint_dir,
                                                      monkeypatch):
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    monkeypatch.setenv("DAIMON_TENANT_SCOPED", "1")
    assert requests.inbox(RECIPIENT).notes == (
        "⚠ some senders skipped: a requests ledger cannot be read",)


def test_join_is_the_typed_core_of_recipient_join(tmp_checkpoint_dir):
    rid = _open()
    got = requests.join(RECIPIENT)
    assert set(got.by_id) == {rid}
    assert requests.recipient_join(project_dir=RECIPIENT) == got.by_id
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    assert requests.join(RECIPIENT).by_id == {}
    assert requests.join(RECIPIENT).notes


def test_two_skipped_senders_are_counted(tmp_checkpoint_dir):
    _open(sender="/p/s1-health")
    _open(sender="/p/s2-health")
    for name in ("/p/s1-health", "/p/s2-health"):
        with open(_ledger(name), "ab") as handle:
            handle.write(b"<<<<<<< HEAD\n")
    assert requests.inbox(RECIPIENT).notes == (
        "⚠ 2 sender(s) skipped: a requests ledger cannot be read",)


def test_the_api_exports_the_typed_cores_and_the_health_map():
    assert api.inbox is requests.inbox
    assert api.join is requests.join
    assert "inbox_listing" in api.__all__ and "inbox" in api.__all__
    assert "join" in api.__all__ and "ledger_health" in api.__all__


def test_ledger_health_maps_each_ledger_file_to_its_state(tmp_checkpoint_dir):
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    got = api.ledger_health(SENDER)
    assert set(got) == set(surfaces.bucket_ledger_names())
    assert got["requests.jsonl"] == "unreadable"
    assert got["events.jsonl"] == "absent"
    assert all(isinstance(v, str) for v in got.values())


def test_the_foreign_column_is_what_the_join_applies(tmp_checkpoint_dir):
    assert view.posture("requests.jsonl", Health.TRANSIENT,
                        foreign=True) is surfaces.ReadPosture.SKIP_SOURCE
    assert view.posture("requests.jsonl", Health.DEGRADED,
                        foreign=True) is surfaces.ReadPosture.NOTE


# ---- the panels and the verbs render the code -------------------------------


def test_the_decision_panel_carries_the_sender_note(tmp_checkpoint_dir):
    from daimon_briefing import briefing
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    lines, _cards = briefing.request_panel(RECIPIENT)
    assert lines == [
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read"]


def test_request_inbox_prints_the_note_and_json_sends_it_to_stderr(
        tmp_checkpoint_dir, capsys, monkeypatch):
    from daimon_briefing import cli
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    assert cli.main(["request", "inbox", "--project", RECIPIENT]) == 0
    out = capsys.readouterr()
    assert "1 sender(s) skipped" in out.out
    assert cli.main(["request", "inbox", "--project", RECIPIENT,
                     "--json"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out) == []
    assert "1 sender(s) skipped" in out.err


def test_the_mcp_inbox_carries_the_note_in_tool_notes(tmp_checkpoint_dir):
    from daimon_briefing import mcp_tools
    _open()
    with open(_ledger(), "ab") as handle:
        handle.write(b"<<<<<<< HEAD\n")
    got = mcp_tools.HANDLERS["requests_inbox"]({"project": RECIPIENT})
    assert json.loads(got.text) == []
    assert got.notes == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read",)


# ---- a teammate's tombstones (O3) -------------------------------------------


def _sidecar(author="alice", data=b""):
    root = config.team_dir() / "remote-a" / "authors" / author
    root.mkdir(parents=True, exist_ok=True)
    path = root / "tombstones.jsonl"
    with open(path, "ab") as handle:
        handle.write(data)
    return path


def _row(key="k1" * 8):
    return (json.dumps({"ts": "2026-10-01T00:00:00Z", "key": key,
                        "author": "alice"}) + "\n").encode()


def test_a_healthy_foreign_sidecar_contributes_keys_and_no_author_note(
        tmp_checkpoint_dir):
    _sidecar(data=_row())
    assert "k1" * 8 in store.foreign_forgotten_content_keys()
    assert store.foreign_tombstones().unproven == frozenset()
    assert view.team_notes() == ()


def test_a_garbage_line_makes_the_author_unproven(tmp_checkpoint_dir):
    _sidecar(data=_row() + b"<<<<<<< HEAD\n")
    assert store.foreign_tombstones().unproven == frozenset({"alice"})
    # The good line's key is still used: the set only grows.
    assert "k1" * 8 in store.foreign_forgotten_content_keys()
    assert view.team_notes() == (
        "⚠ a teammate's tombstones cannot be read; "
        "their checkpoints are not admitted",)


def test_a_torn_line_degrades_the_author_without_skipping_them(
        tmp_checkpoint_dir):
    _sidecar(data=_row() + b'{"ts": "x", "key": "tor')
    assert store.foreign_tombstones().unproven == frozenset()
    assert view.team_notes() == (
        "⚠ a teammate's tombstones ledger has torn lines; "
        "their forgets may be incomplete",)


def test_an_over_cap_sidecar_is_unproven(tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 60)
    _sidecar(data=_row() + _row("k2" * 8) + _row("k3" * 8))
    assert store.foreign_tombstones().unproven == frozenset({"alice"})


def test_a_transient_sidecar_is_unproven(tmp_checkpoint_dir, monkeypatch):
    path = _sidecar(data=_row())
    _seam(monkeypatch, path, jsonl.Read(Health.TRANSIENT, [], detail="EBUSY"))
    assert store.foreign_tombstones().unproven == frozenset({"alice"})


def test_the_tombstone_reader_returns_keys_plus_health(tmp_checkpoint_dir):
    path = _sidecar(data=_row())
    got = store._tombstone_keys(path)
    assert got.keys == {"k1" * 8} and got.health is Health.OK
    assert got.unproven is False
    assert store._tombstone_keys(path.with_name("none.jsonl")).health \
        is Health.ABSENT


def _teammate(monkeypatch, author="grace"):
    """A synced clone with one teammate checkpoint of RECIPIENT's project."""
    monkeypatch.setenv("DAIMON_TEAM_PROJECT", "core/x")
    remote = config.team_dir() / "team-a"
    (remote / ".git").mkdir(parents=True, exist_ok=True)
    adir = remote / "projects" / "core" / "x" / "authors" / author
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "S1.json").write_text(json.dumps({
        "session_id": "S1", "created": "2026-10-01T00:00:00Z",
        "author": author, "team_project": "core/x",
        "project_slug": store.project_slug(RECIPIENT),
        "working_context": {"recent_decisions": [
            {"text": "a teammate decision", "trust": "inferred"}]},
    }), encoding="utf-8")
    return adir


def test_read_team_admits_a_teammate_whose_tombstones_are_proven(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(_row("zz" * 8))
    assert [a for a, _cp in store.read_team(project_dir=RECIPIENT)] == [
        "grace"]


def test_read_team_does_not_admit_an_author_with_unproven_tombstones(
        tmp_checkpoint_dir, monkeypatch):
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(_row("zz" * 8) + b"<<<<<<< HEAD\n")
    assert store.read_team(project_dir=RECIPIENT) == []
    assert view.team(RECIPIENT, live=True) == ()
    assert view.team_notes()


def test_recall_does_not_index_an_author_with_unproven_tombstones(
        tmp_checkpoint_dir, monkeypatch):
    from daimon_briefing import recall
    adir = _teammate(monkeypatch)
    (adir / "tombstones.jsonl").write_bytes(_row("zz" * 8))
    assert [a for _sid, a, *_ in recall._scan_sources()] == ["grace"]
    (adir / "tombstones.jsonl").write_bytes(_row("zz" * 8) + b"<<<<<<< HEAD\n")
    assert list(recall._scan_sources()) == []


def test_request_list_skips_a_recipient_whose_ledger_is_unproven(
        tmp_checkpoint_dir, capsys):
    _open()
    recipient = _ledger(RECIPIENT)
    recipient.parent.mkdir(parents=True, exist_ok=True)
    recipient.write_bytes(b"<<<<<<< HEAD\n")
    got = requests.listed(SENDER)
    assert len(got.rows) == 1                  # the sender's own ask stays
    assert got.notes == (
        "⚠ 1 sender(s) skipped: a requests ledger cannot be read",)
    assert requests.listing(project_dir=SENDER) == got.rows
    from daimon_briefing import cli
    assert cli.main(["request", "list", "--project", SENDER]) == 0
    assert "1 sender(s) skipped" in capsys.readouterr().out
    assert cli.main(["request", "list", "--project", SENDER, "--json"]) == 0
    out = capsys.readouterr()
    assert len(json.loads(out.out)) == 1
    assert "1 sender(s) skipped" in out.err


def test_the_tombstone_reader_survives_a_path_it_cannot_stat(
        tmp_checkpoint_dir):
    blocker = config.team_dir() / "blocker"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where a directory should be")
    got = store._tombstone_keys(blocker / "tombstones.jsonl")
    assert got.keys == set() and got.unproven


def test_the_tombstone_reader_survives_an_over_cap_file_it_cannot_open(
        tmp_checkpoint_dir, monkeypatch):
    monkeypatch.setattr(store, "_MAX_TOMBSTONE_BYTES", 0)
    directory = config.team_dir() / "dir-in-the-files-place"
    directory.mkdir(parents=True)
    got = store._tombstone_keys(directory)
    assert got.keys == set() and got.over_cap and got.unproven
