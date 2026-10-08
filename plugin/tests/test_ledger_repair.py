"""#1132 2c-2: `daimon ledger repair` and the single-key scrub it shares with
forget.

Ledgers here are built from raw bytes, never through the package writers, so
repair is judged against shapes the writers cannot make.
"""
import json

from daimon_briefing import jsonl
from daimon_briefing.surfaces import WritePosture, Writer

ROW_A = json.dumps({"id": "a", "note": "first"}, ensure_ascii=False)
ROW_B = json.dumps({"id": "b", "note": "second"}, ensure_ascii=False)
SPLIT = json.dumps({"id": "s", "note": "line one line two"},
                   ensure_ascii=False)
TORN = '{"id": "t", "note": "cut off'


def _text(*lines):
    return "".join(line + "\n" for line in lines)


def test_partition_separates_rows_from_torn_and_garbage():
    head, tail = SPLIT.split(" ")
    text = _text(ROW_A, head, tail, TORN, "not json at all", "[1, 2]",
                 "\udcff\udcfe raw", ROW_B)
    result = jsonl.partition(text)
    assert result.rows == [ROW_A, SPLIT, ROW_B]
    assert result.split == 1
    assert result.torn == [TORN]
    assert result.garbage == ["not json at all", "[1, 2]", "\udcff\udcfe raw"]


def test_partition_agrees_with_the_health_read(tmp_path):
    path = tmp_path / "l.jsonl"
    path.write_bytes(_text(ROW_A, TORN, "nope").encode())
    kept = jsonl.partition(path.read_text(errors="surrogateescape")).rows
    assert kept == [ROW_A]
    path.write_bytes(_text(*kept).encode())
    assert jsonl.read(path).health is jsonl.Health.OK


# ---- the shared single-key scrub -------------------------------------------

import pytest  # noqa: E402

from daimon_briefing import (amendments, cli, ledger_repair, refutations,  # noqa: E402
                             relations, requests, store, trust)

_DELETERS = (
    (store, "scrub_event_fields"), (refutations, "forget_content_key"),
    (relations, "forget_item_id"), (amendments, "forget_content_key"),
    (amendments, "forget_item_id"), (requests, "forget_content_key"),
    (trust, "redact_content_key"),
    (ledger_repair, "forget_quarantined_lines"))
_ORDER = ["store.scrub_event_fields", "refutations.forget_content_key",
          "relations.forget_item_id", "amendments.forget_content_key",
          "amendments.forget_item_id", "requests.forget_content_key",
          "trust.redact_content_key", "ledger_repair.forget_quarantined_lines"]


@pytest.fixture
def calls(monkeypatch):
    seen = []
    for module, name in _DELETERS:
        label = f"{module.__name__.rsplit('.', 1)[-1]}.{name}"

        def spy(*args, _label=label, **kwargs):
            seen.append((_label, args, kwargs))
            if _label.endswith("forget_quarantined_lines"):
                return ledger_repair.Purged(0, 0)
            return 0 if _label == "store.scrub_event_fields" else []
        monkeypatch.setattr(module, name, spy)
    return seen


def test_scrub_forgotten_key_runs_every_ledger_deleter_in_order(calls):
    ledger_repair.scrub_forgotten_key(
        "k" * 16, item_id="i-1", sibling_ids={"i-3", "i-2"},
        text="the value", project_dir="/p/x")
    assert [c[0] for c in calls] == [
        "store.scrub_event_fields", "refutations.forget_content_key",
        "relations.forget_item_id", "amendments.forget_content_key",
        "amendments.forget_item_id", "amendments.forget_item_id",
        "amendments.forget_item_id", "requests.forget_content_key",
        "trust.redact_content_key", "ledger_repair.forget_quarantined_lines"]
    by_label = {}
    for label, args, kwargs in calls:
        by_label.setdefault(label, []).append((args, kwargs))
    assert by_label["relations.forget_item_id"] == [
        (("i-1",), {"project_dir": "/p/x"})]
    assert [a[0] for a, _ in by_label["amendments.forget_item_id"]] == [
        "i-1", "i-2", "i-3"]
    assert by_label["ledger_repair.forget_quarantined_lines"] == [
        (("k" * 16,), {"text": "the value", "project_dir": "/p/x"})]


def test_forget_reaches_every_ledger_deleter_through_the_shared_entry(
        calls, tmp_checkpoint_dir):
    project = "/p/forget-order"
    store.write_checkpoint(
        "S1", {"session_id": "S1", "created": "2026-08-01T00:00:00Z",
               "working_context": {"recent_decisions": [
                   {"text": "forget me please", "trust": "inferred"}]}},
        project_dir=project, writer=Writer.HUMAN)
    assert cli.main(["forget", "forget me please", "--project", project]) == 0
    labels = [c[0] for c in calls]
    first_seen = [label for i, label in enumerate(labels)
                  if label not in labels[:i]]
    assert first_seen == _ORDER
    quarantine_call = [c for c in calls
                       if c[0] == "ledger_repair.forget_quarantined_lines"][0]
    assert quarantine_call[2]["text"] == "forget me please"


# ---- `daimon ledger repair` ------------------------------------------------

import errno  # noqa: E402
import re  # noqa: E402

from daimon_briefing import config, ledger_census, normalize  # noqa: E402

PROJECT = "/p/ledger-repair"
VALUE = "the vault code is alpha beta"
KEY = normalize.content_key(VALUE)
SLUG = None


def _bucket():
    d = config.checkpoint_dir() / store.project_slug(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trust_row(qid, reason, **extra):
    return json.dumps({"quarantine_id": qid, "event": "proposed",
                       "reason": reason, "evidence": ["issue:1"], **extra},
                      ensure_ascii=False)


def _write(name, *chunks):
    path = _bucket() / name
    path.write_bytes(b"".join(chunks))
    return path


def _line(text):
    return text.encode("utf-8") + b"\n"


def _tombstone(key=KEY, ref="i-gone"):
    with (_bucket() / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-05T00:00:00Z", "kind": "tombstone",
                            "item_ref": ref, "status": f"forgotten:{key}",
                            "source": "cli"}) + "\n")


def _repair(*argv):
    return cli.main(["ledger", "repair", *argv, "--project", PROJECT])


def _sidecar_rows(name="trust.quarantined-lines"):
    path = _bucket() / name
    return [json.loads(ln) for ln in path.read_text(
        encoding="utf-8").split("\n") if ln]


def _messy_trust():
    head, tail = SPLIT.split(" ")
    return _write(
        "trust.jsonl", _line(ROW_A), _line(head), _line(tail), _line(TORN),
        b"\xff\xfe broken bytes\n", _line("not json at all"), _line("[1, 2]"),
        _line(ROW_B))


def test_repair_rejoins_quarantines_and_leaves_a_readable_ledger(capsys):
    path = _messy_trust()
    assert _repair("trust") == 0
    assert jsonl.read(path).health is jsonl.Health.OK
    assert path.read_bytes() == _line(ROW_A) + _line(SPLIT) + _line(ROW_B)
    rows = _sidecar_rows()
    assert [(r["kind"], r["ledger"]) for r in rows] == [
        ("torn", "trust.jsonl"), ("garbage", "trust.jsonl"),
        ("garbage", "trust.jsonl"), ("garbage", "trust.jsonl")]
    assert rows[0]["text"] == TORN
    assert all(r["quarantined_at"].endswith("Z") for r in rows)
    undecodable = rows[1]["text"]
    assert undecodable == "\\xff\\xfe broken bytes"
    assert undecodable.encode().decode("unicode_escape").encode(
        "latin-1") == b"\xff\xfe broken bytes"
    assert [r["text"] for r in rows[2:]] == ["not json at all", "[1, 2]"]
    out = capsys.readouterr().out
    assert "rejoined 1 split row" in out
    assert "quarantined 1 torn + 3 garbage line(s)" in out


def test_repair_swaps_the_sidecar_then_the_ledger_through_jsonl_replace(
        monkeypatch):
    """Both swaps run under the ledger lock, which an append also takes."""
    seen = []
    real = jsonl.replace

    def spy(path, text, **kwargs):
        seen.append(path.name)
        return real(path, text, **kwargs)

    monkeypatch.setattr(jsonl, "replace", spy)
    _messy_trust()
    assert _repair("trust") == 0
    assert seen == ["trust.quarantined-lines", "trust.jsonl"]


def test_an_append_racing_the_repair_is_not_lost(monkeypatch):
    """The read, the partition and both swaps run under one lock hold, so an
    appender that arrives mid-repair waits and lands after the swap."""
    import threading
    path = _messy_trust()
    late = {"quarantine_id": "tr-late", "event": "proposed", "reason": "late"}
    real = ledger_repair._quarantine
    appended = []

    def racing(*args, **kwargs):
        worker = threading.Thread(
            target=lambda: appended.append(jsonl.append(path, late, posture=WritePosture.PROCEED)))
        worker.start()
        worker.join(0.2)        # the appender had its chance to land first
        try:
            return real(*args, **kwargs)
        finally:
            racing.worker = worker

    monkeypatch.setattr(ledger_repair, "_quarantine", racing)
    assert _repair("trust") == 0
    racing.worker.join(5)
    assert appended == [1]
    assert late in jsonl.read(path).rows


def _counting_dir_lock(monkeypatch):
    real = jsonl.dir_lock
    state = {"depth": 0, "max": 0, "dirs": []}

    class Wrapped:
        def __init__(self, d):
            self._inner = real(d)
            state["dirs"].append(d)

        def __enter__(self):
            state["depth"] += 1
            state["max"] = max(state["max"], state["depth"])
            return self._inner.__enter__()

        def __exit__(self, *exc):
            state["depth"] -= 1
            return self._inner.__exit__(*exc)

    monkeypatch.setattr(jsonl, "dir_lock", Wrapped)
    return state


def test_a_repair_never_nests_the_ledger_lock(monkeypatch):
    state = _counting_dir_lock(monkeypatch)
    _messy_trust()
    assert _repair("trust") == 0
    assert state["dirs"], "the repair took no lock"
    assert state["max"] == 1


def test_a_dry_run_locks_the_scratch_copy_never_the_real_bucket(monkeypatch):
    state = _counting_dir_lock(monkeypatch)
    _messy_trust()
    real = _bucket()
    assert _repair("trust", "--dry-run") == 0
    assert state["dirs"]
    assert all(d != real for d in state["dirs"])
    assert not (real / jsonl.LOCK_NAME).exists()
    assert state["max"] == 1


def test_repair_rescrubs_a_forgotten_value_a_split_row_still_carried(capsys):
    head, tail = _trust_row("tr-aaa", VALUE).split(" ")
    path = _write("trust.jsonl", _line(head), _line(tail),
                  _line(_trust_row("tr-bbb", "an unrelated reason")))
    _tombstone()
    slug = store.project_slug(PROJECT)
    before = ledger_census.census_bucket(slug)["ledgers"]["trust.jsonl"]
    assert before["tombstoned_present"] == 1
    assert _repair("trust") == 0
    text = path.read_text(encoding="utf-8")
    assert "vault code" not in text
    assert f"[forgotten:{KEY}]" in text and "an unrelated reason" in text
    after = ledger_census.census_bucket(slug)["ledgers"]["trust.jsonl"]
    assert after["tombstoned_present"] == 0 and after["state"] == "ok"
    assert "re-scrubbed 1 forgotten key(s)" in capsys.readouterr().out


def test_repair_stamps_the_census_marker_with_the_repaired_state():
    _messy_trust()
    store._record_ledger_census(store.project_slug(PROJECT))
    marker = _bucket() / ".ledger-census"
    assert json.loads(marker.read_text())["ledgers"]["trust.jsonl"][
        "state"] == "unreadable"
    assert _repair("trust") == 0
    assert json.loads(marker.read_text())["ledgers"]["trust.jsonl"][
        "state"] == "ok"


def test_a_second_repair_changes_nothing_and_says_so(capsys):
    _messy_trust()
    _tombstone()
    assert _repair("trust") == 0
    ledger_bytes = (_bucket() / "trust.jsonl").read_bytes()
    side_bytes = (_bucket() / "trust.quarantined-lines").read_bytes()
    capsys.readouterr()
    assert _repair("trust") == 0
    assert (_bucket() / "trust.jsonl").read_bytes() == ledger_bytes
    assert (_bucket() / "trust.quarantined-lines").read_bytes() == side_bytes
    assert "nothing to repair" in capsys.readouterr().out


def _numbers(out):
    return re.findall(r"\d+", re.sub(r"dry run.*", "", out))


def test_dry_run_reports_the_same_counts_and_writes_nothing(capsys):
    path = _messy_trust()
    _tombstone()
    (_bucket() / "trust.quarantined-lines").write_text(
        json.dumps({"ledger": "trust.jsonl", "quarantined_at": "t",
                    "kind": "torn", "text": "older"}) + "\n")
    files = [path, _bucket() / "trust.quarantined-lines",
             _bucket() / "events.jsonl"]
    before = [p.read_bytes() for p in files]
    assert _repair("trust", "--dry-run") == 0
    dry = capsys.readouterr().out
    assert [p.read_bytes() for p in files] == before
    assert not (_bucket() / ".ledger-census").exists()
    assert "dry run" in dry and "would" in dry
    assert _repair("trust") == 0
    real = capsys.readouterr().out
    assert _numbers(dry) == _numbers(real)


def test_repair_says_unrelated_fragments_stay_in_an_existing_sidecar(capsys):
    _messy_trust()
    (_bucket() / "trust.quarantined-lines").write_text(
        json.dumps({"ledger": "trust.jsonl", "quarantined_at": "t",
                    "kind": "torn", "text": "an older fragment"}) + "\n")
    assert _repair("trust") == 0
    assert "unrelated fragments are kept" in capsys.readouterr().out
    texts = [r["text"] for r in _sidecar_rows()]
    assert texts[0] == "an older fragment" and len(texts) == 5


def test_repair_rescrubs_the_sidecar_by_key(capsys):
    _write("trust.jsonl", _line(ROW_A))
    _tombstone()
    (_bucket() / "trust.quarantined-lines").write_text(
        json.dumps({"ledger": "trust.jsonl", "quarantined_at": "t",
                    "kind": "torn",
                    "text": '{"reason": ' + json.dumps(VALUE) + ', "cut'})
        + "\n" + json.dumps({"ledger": "trust.jsonl", "quarantined_at": "t",
                             "kind": "torn", "text": "keep me"}) + "\n")
    assert _repair("trust") == 0
    assert [r["text"] for r in _sidecar_rows()] == ["keep me"]


def test_trust_repair_is_the_same_verb_as_ledger_repair_trust():
    _messy_trust()
    assert cli.main(["trust", "repair", "--project", PROJECT]) == 0
    assert (_bucket() / "trust.jsonl").read_bytes() == (
        _line(ROW_A) + _line(SPLIT) + _line(ROW_B))
    assert len(_sidecar_rows()) == 4


def test_a_short_or_full_ledger_name_is_accepted():
    _write("events.jsonl", _line(ROW_A), _line(TORN))
    assert _repair("events.jsonl") == 0
    assert (_bucket() / "events.quarantined-lines").exists()


def test_an_unknown_ledger_name_is_refused_with_exit_2(capsys):
    assert _repair("nonsense") == 2
    out = capsys.readouterr().out
    assert "unknown ledger 'nonsense'" in out and "trust" in out


def test_an_absent_ledger_is_nothing_to_repair(capsys):
    _bucket()
    assert _repair("trust") == 0
    assert "nothing to repair" in capsys.readouterr().out
    assert not (_bucket() / "trust.quarantined-lines").exists()


def test_a_transient_ledger_is_never_touched(monkeypatch, capsys):
    path = _messy_trust()
    before = path.read_bytes()

    def stuck(_p):
        raise OSError(errno.EAGAIN, "try again")
    monkeypatch.setattr(jsonl, "_read_bytes", stuck)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)
    assert _repair("trust") == 1
    out = capsys.readouterr().out
    assert "transient" in out and "EAGAIN" in out
    assert path.read_bytes() == before
    assert not (_bucket() / "trust.quarantined-lines").exists()


def test_an_already_clean_ledger_is_left_byte_identical(capsys):
    path = _write("trust.jsonl", _line(ROW_A), _line(ROW_B))
    before = path.read_bytes()
    assert _repair("trust") == 0
    assert path.read_bytes() == before
    assert "nothing to repair" in capsys.readouterr().out


def test_a_ledger_that_cannot_be_rewritten_is_reported_and_kept(
        monkeypatch, capsys):
    path = _messy_trust()
    before = path.read_bytes()

    def refuse(*_a, **_k):
        raise OSError(errno.EROFS, "read-only")
    monkeypatch.setattr(store, "_atomic_write", refuse)
    assert _repair("trust") == 1
    assert "cannot repair trust.jsonl" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_repair_without_a_readable_events_ledger_skips_the_rescrub(capsys):
    _write("trust.jsonl", _line(ROW_A), _line(TORN))
    _write("events.jsonl", b"\xff\xfe not an event\n")
    assert _repair("trust") == 1
    out = capsys.readouterr().out
    assert "re-scrub skipped" in out and "events" in out
    assert jsonl.read(_bucket() / "trust.jsonl").health is jsonl.Health.OK


def test_repairing_events_keeps_the_tombstones_and_redacts_missed_rows():
    event = {"ts": "2026-10-04T00:00:00Z", "kind": "resolution",
             "item_ref": "i-1", "status": "resolved", "source": "cli",
             "item_text": VALUE}
    head, tail = json.dumps(event, ensure_ascii=False).split(" ")
    path = _write("events.jsonl", _line(head), _line(tail), _line(TORN))
    _tombstone()
    assert _repair("events") == 0
    text = path.read_text(encoding="utf-8")
    assert "vault code" not in text and f"[forgotten:{KEY}]" in text
    assert store.forgotten_content_keys(PROJECT) == {KEY}
    assert jsonl.read(path).health is jsonl.Health.OK
    assert [r["text"] for r in _sidecar_rows("events.quarantined-lines")] == [
        TORN]


# ---- the ruling gate (#693) -------------------------------------------------

RULING_VERDICT = "internal numbers never appear in public posts"


def _active_ruling():
    rid = refutations.assert_ruling(
        subject="public posts", verdict=RULING_VERDICT, scope="publishing",
        evidence=["issue:693"], channel="cli-tty", ratified=True,
        project_dir=PROJECT)
    _tombstone(normalize.content_key(RULING_VERDICT))
    _write("trust.jsonl", _line(ROW_A), _line(TORN))
    return rid


def _terminal(monkeypatch, tty, answer=None):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: tty, raising=False)
    if answer is not None:
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)


def test_a_non_interactive_repair_fixes_the_file_but_refuses_to_drop_a_ruling(
        monkeypatch, capsys):
    rid = _active_ruling()
    ledger = config.checkpoint_dir() / store.project_slug(PROJECT)
    before = (ledger / "refutations.jsonl").read_bytes()
    _terminal(monkeypatch, False)
    assert _repair("trust") == 1
    out = capsys.readouterr().out
    assert rid in out and "ruling retire" in out and "refused" in out
    assert (ledger / "refutations.jsonl").read_bytes() == before
    assert jsonl.read(ledger / "trust.jsonl").health is jsonl.Health.OK


def test_an_interactive_yes_lets_the_rescrub_remove_the_ruling(
        monkeypatch, capsys):
    rid = _active_ruling()
    _terminal(monkeypatch, True, "y")
    assert _repair("trust") == 0
    assert "WARNING" in capsys.readouterr().out
    assert refutations.get(rid, project_dir=PROJECT) is None


def test_an_interactive_no_keeps_the_ruling_and_still_repairs_the_file(
        monkeypatch, capsys):
    rid = _active_ruling()
    _terminal(monkeypatch, True, "n")
    assert _repair("trust") == 1
    assert refutations.get(rid, project_dir=PROJECT) is not None
    path = config.checkpoint_dir() / store.project_slug(PROJECT) / "trust.jsonl"
    assert jsonl.read(path).health is jsonl.Health.OK


def test_a_dry_run_names_the_ruling_it_would_remove_and_writes_nothing(
        monkeypatch, capsys):
    rid = _active_ruling()
    bucket = config.checkpoint_dir() / store.project_slug(PROJECT)
    files = sorted(p for p in bucket.iterdir())
    before = [p.read_bytes() for p in files]
    _terminal(monkeypatch, False)
    assert _repair("trust", "--dry-run") == 0
    assert rid in capsys.readouterr().out
    assert sorted(bucket.iterdir()) == files
    assert [p.read_bytes() for p in files] == before


# ---- edges of the repair engine ---------------------------------------------


def test_repair_does_not_duplicate_what_a_crashed_run_already_parked(capsys):
    path = _write("trust.jsonl", _line(ROW_A), _line(TORN))
    (_bucket() / "trust.quarantined-lines").write_text(
        json.dumps({"ledger": "trust.jsonl", "quarantined_at": "t",
                    "kind": "torn", "text": TORN}) + "\n"
        + "{a torn fragment of its own\n[1]\n", encoding="utf-8")
    assert _repair("trust") == 0
    body = (_bucket() / "trust.quarantined-lines").read_text(encoding="utf-8")
    assert body.count(json.dumps(TORN)) == 1
    assert "{a torn fragment of its own\n[1]\n" in body
    assert path.read_bytes() == _line(ROW_A)


def test_an_unreadable_ledger_that_is_not_garbage_is_reported(
        monkeypatch, capsys):
    path = _messy_trust()
    before = path.read_bytes()

    def denied(_p):
        raise PermissionError(errno.EACCES, "denied")
    monkeypatch.setattr(jsonl, "_read_bytes", denied)
    assert _repair("trust") == 1
    assert "unreadable (EACCES)" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_a_project_with_no_bucket_has_nothing_to_repair(capsys):
    assert _repair("trust") == 0
    assert _repair("trust", "--dry-run") == 0
    out = capsys.readouterr().out
    assert out.count("nothing to repair") == 2
    assert not (config.checkpoint_dir() / store.project_slug(PROJECT)).exists()


def test_repair_without_a_resolvable_project_reports_it(monkeypatch, capsys):
    monkeypatch.setattr(store, "project_slug", lambda _p: None)
    assert _repair("trust") == 1
    assert "no project to address" in capsys.readouterr().out


def test_a_dry_run_that_cannot_stage_its_copy_says_so_and_writes_nothing(
        monkeypatch, capsys):
    path = _messy_trust()
    before = path.read_bytes()

    def full(*_a, **_k):
        raise OSError(errno.ENOSPC, "no space")
    monkeypatch.setattr(ledger_repair.shutil, "copy2", full)
    assert _repair("trust", "--dry-run") == 1
    assert "ENOSPC" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_the_scratch_store_never_touches_the_environment(
        tmp_path, monkeypatch):
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    (bucket / "trust.jsonl").write_text(ROW_A + "\n", encoding="utf-8")
    import os
    env = os.environ
    before = env["DAIMON_CHECKPOINT_DIR"]
    with ledger_repair._scratch_store(bucket):
        assert env["DAIMON_CHECKPOINT_DIR"] == before
        assert config.checkpoint_dir() != config.Path(before)
    assert env["DAIMON_CHECKPOINT_DIR"] == before
    monkeypatch.delenv("DAIMON_CHECKPOINT_DIR")
    with ledger_repair._scratch_store(bucket):
        assert "DAIMON_CHECKPOINT_DIR" not in env
    assert "DAIMON_CHECKPOINT_DIR" not in env


def test_a_concurrent_reader_during_a_dry_run_still_sees_the_real_store(
        monkeypatch):
    import threading
    _messy_trust()
    real = config.checkpoint_dir()
    seen = {}

    def reader():
        seen["other"] = config.checkpoint_dir()

    original = ledger_repair._run

    def run_with_a_reader(*args):
        seen["own"] = config.checkpoint_dir()
        thread = threading.Thread(target=reader)
        thread.start()
        thread.join()
        return original(*args)

    monkeypatch.setattr(ledger_repair, "_run", run_with_a_reader)
    assert _repair("trust", "--dry-run") == 0
    assert seen["own"] != real
    assert seen["other"] == real
