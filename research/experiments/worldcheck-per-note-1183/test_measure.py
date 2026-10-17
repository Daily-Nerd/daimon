"""Tests for measure.py (#1183), on synthetic data only. Run:

  cd plugin && uv run --extra dev pytest ../research/experiments/worldcheck-per-note-1183/ -q
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugin"))

import measure  # noqa: E402
from daimon_briefing import worldcheck  # noqa: E402

SECRET_BUCKET = "zzbucket-secret-slug"
SECRET_ID = "d-secretid9"
SECRET_TEXT = "PR #12 merged (secret note words)"
FILE_TEXT = "the file src/secretpath/main.py exists"
BRANCH_TEXT = "branch `feat/secret-branch` is deleted"
NOW = "2026-10-16T12:00:00Z"
SINCE = "2026-09-25T00:00:00Z"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    for name in ("HOME", "DAIMON_CHECKPOINT_DIR", "DAIMON_LOG_DIR"):
        sub = tmp_path / ("env-" + name.lower())
        sub.mkdir()
        monkeypatch.setenv(name, str(sub))


def key_for(text):
    cls, claim = worldcheck.claim_for(text)
    return worldcheck._claim_key(cls, claim)


def ledger_row(ref, check, ts, text=SECRET_TEXT, reason="claim-contradicted"):
    return {"ts": ts, "check": check, "item_ref": ref, "reason": reason,
            "claim_key": key_for(text)}


def item(text, ident=None, carried=True):
    out = {"text": text}
    if ident is not None:
        out["id"] = ident
    if carried:
        out["carried_from"] = "s-prev"
    return out


def checkpoint(created, *items):
    return {"created": created,
            "working_context": {"open_questions": list(items)}}


def make_bucket(root, name=SECRET_BUCKET, ledger=(), files=None):
    bucket = root / name
    bucket.mkdir(parents=True, exist_ok=True)
    if ledger:
        lines = [r if isinstance(r, str) else json.dumps(r) for r in ledger]
        (bucket / "verification.jsonl").write_text("\n".join(lines) + "\n")
    for fname, body in (files or {}).items():
        (bucket / fname).write_text(
            body if isinstance(body, str) else json.dumps(body))
    return bucket


def write_usage(path, lines):
    path.write_text("\n".join(lines) + "\n")


def run(tmp_path, since=SINCE, now=NOW, usage=()):
    log = tmp_path / "usage.log"
    write_usage(log, list(usage))
    return measure.scan(tmp_path / "cps", log,
                        since=measure.parse_epoch(since) if since else None,
                        now=measure.parse_epoch(now))


@pytest.fixture
def cps(tmp_path):
    root = tmp_path / "cps"
    root.mkdir()
    return root


def test_parse_epoch_accepts_seconds_and_microseconds_and_rejects_junk():
    whole = measure.parse_epoch("2026-10-01T00:00:00Z")
    micro = measure.parse_epoch("2026-10-01T00:00:00.500000Z")
    assert micro == whole + 0.5
    assert measure.parse_epoch("nope") is None
    assert measure.parse_epoch(None) is None
    assert measure.parse_epoch(12) is None


def test_unique_note_dedupes_repeat_rows_and_counts_every_row(tmp_path, cps):
    rows = [ledger_row(SECRET_ID, "pr-state", f"2026-10-01T00:00:0{i}.100000Z")
            for i in range(3)]
    rows.append(ledger_row("d-other", "pr-state", "2026-10-02T00:00:00.100000Z"))
    make_bucket(cps, ledger=rows)
    c = run(tmp_path)["classes"]["pr-state"]
    assert c["ledger_contradiction_rows_window"] == 4
    assert c["notes_contradicted"] == 2
    assert c["max_rows_on_one_note"] == 3
    assert c["repeat_histogram"] == {"1": 1, "2-4": 1, "5-9": 0, "10+": 0}


def test_same_item_and_claim_in_two_buckets_is_two_notes(tmp_path, cps):
    row = ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")
    make_bucket(cps, "bucket-a", ledger=[row])
    make_bucket(cps, "bucket-b", ledger=[row])
    assert run(tmp_path)["classes"]["pr-state"]["notes_contradicted"] == 2


def test_same_item_with_two_claims_is_two_notes(tmp_path, cps):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z"),
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:01.100000Z",
                   text="PR #99 merged")])
    assert run(tmp_path)["classes"]["pr-state"]["notes_contradicted"] == 2


def test_window_filter_compares_epochs_across_mixed_stamp_forms(tmp_path, cps):
    # Lexically "...00:00:00.500000Z" < "...00:00:00Z" ('.' < 'Z'): a string
    # compare would call the half-second row older than the whole-second since.
    make_bucket(cps, ledger=[
        ledger_row("d-in", "pr-state", "2026-09-25T00:00:00.500000Z"),
        ledger_row("d-out", "pr-state", "2026-09-24T23:59:59.900000Z"),
        ledger_row("d-in2", "pr-state", "2026-09-25T00:00:00Z")])
    c = run(tmp_path)["classes"]["pr-state"]
    assert c["ledger_contradiction_rows_window"] == 2
    assert c["notes_contradicted"] == 2


def test_default_since_is_the_earliest_world_row_across_buckets(tmp_path, cps):
    make_bucket(cps, "bucket-a", ledger=[
        ledger_row("d-1", "pr-state", "2026-10-03T00:00:00.100000Z")])
    make_bucket(cps, "bucket-b", ledger=[
        ledger_row("d-2", "pr-state", "2026-10-01T00:00:00.100000Z"),
        # a confirmation row is a world row too, so it can open the window
        ledger_row("d-3", "pr-state-ok", "2026-09-28T00:00:00.100000Z",
                   reason="claim-confirmed")])
    result = run(tmp_path, since=None)
    assert result["window"]["since"] == "2026-09-28T00:00:00Z"
    assert result["classes"]["pr-state"]["ledger_contradiction_rows_window"] == 2


def test_no_world_rows_and_no_since_means_an_empty_window(tmp_path, cps):
    make_bucket(cps, files={"latest.json": checkpoint(
        "2026-10-05T00:00:00Z", item(SECRET_TEXT, SECRET_ID))})
    result = run(tmp_path, since=None, usage=[
        "2026-10-05T00:00:00Z worldcheck:pr-state:contradicted"])
    c = result["classes"]["pr-state"]
    assert result["window"]["since"] is None
    assert c["checks_contradicted_all"] == 1
    assert c["checks_contradicted_window"] == 0
    assert c["carried_claim_notes_window"] == 0


def test_torn_and_non_object_lines_are_counted_and_skipped(tmp_path, cps):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z"),
        '{"ts": "2026-10-01T00:00:00Z", "check": "pr-st',
        "[1, 2]",
        '"just a string"'])
    result = run(tmp_path)
    assert result["inputs"]["torn_lines"] == 3
    assert result["inputs"]["ledger_rows"] == 1
    assert result["classes"]["pr-state"]["notes_contradicted"] == 1


def test_only_valid_contradiction_rows_count(tmp_path, cps):
    good = ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")
    bad_key = dict(good, claim_key="abc")
    bad_reason = dict(good, reason="claim-confirmed")
    cure = ledger_row(SECRET_ID, "pr-state-ok", "2026-10-01T00:00:00.100000Z",
                      reason="claim-confirmed")
    other_check = dict(good, check="receipt")
    bad_ts = dict(good, ts="garbage")
    no_ref = {k: v for k, v in good.items() if k != "item_ref"}
    make_bucket(cps, ledger=[good, bad_key, bad_reason, cure, other_check,
                             bad_ts, no_ref])
    c = run(tmp_path)["classes"]["pr-state"]
    assert c["ledger_contradiction_rows_window"] == 1
    assert c["notes_contradicted"] == 1


def test_carried_claim_notes_are_distinct_across_checkpoint_files(tmp_path, cps):
    same = item(SECRET_TEXT, SECRET_ID)
    make_bucket(cps, files={
        "latest.json": checkpoint("2026-10-05T00:00:00Z", same,
                                  item(FILE_TEXT, "d-file")),
        "prev-1.json": checkpoint("2026-10-04T00:00:00Z", same),
        "session_abc.json": checkpoint("2026-10-03T00:00:00Z", same)})
    classes = run(tmp_path)["classes"]
    assert classes["pr-state"]["carried_claim_notes_window"] == 1
    assert classes["file-exists"]["carried_claim_notes_window"] == 1
    assert classes["branch-state"]["carried_claim_notes_window"] == 0


def test_carried_notes_exclude_native_non_claim_old_and_dependency_items(
        tmp_path, cps):
    make_bucket(cps, files={
        "latest.json": checkpoint(
            "2026-10-05T00:00:00Z",
            item(SECRET_TEXT, "d-native", carried=False),
            item("nothing checkable here", "d-plain"),
            item("lodash 4.17.21 is pinned", "d-dep")),
        "prev-1.json": checkpoint(
            "2026-09-01T00:00:00Z", item(SECRET_TEXT, "d-old")),
        "prev-2.json": {"working_context": {"open_questions": [
            item(SECRET_TEXT, "d-nostamp")]}}})
    assert run(tmp_path)["classes"]["pr-state"]["carried_claim_notes_window"] == 0


def test_union_denominator_adds_ledger_notes_no_checkpoint_still_holds(
        tmp_path, cps):
    make_bucket(cps,
                ledger=[
                    ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z"),
                    ledger_row("d-gone", "pr-state", "2026-10-01T00:00:00.100000Z",
                               text="PR #77 merged")],
                files={"latest.json": checkpoint(
                    "2026-10-05T00:00:00Z", item(SECRET_TEXT, SECRET_ID),
                    item("PR #5 merged", "d-quiet"))})
    c = run(tmp_path)["classes"]["pr-state"]
    assert c["notes_contradicted"] == 2
    # SECRET_ID is in both sets; d-quiet is carried only; d-gone is ledger only
    assert c["carried_claim_notes_window"] == 3
    assert c["notes_contradicted"] <= c["carried_claim_notes_window"]
    assert c["k_over_u"] == pytest.approx(2 / 3)


def test_idless_carried_notes_are_sized_and_kept_out_of_the_union(tmp_path, cps):
    make_bucket(cps, files={"latest.json": checkpoint(
        "2026-10-05T00:00:00Z", item(SECRET_TEXT), item(SECRET_TEXT),
        item("PR #6 merged"), item(FILE_TEXT, "d-file"))},
        ledger=[ledger_row("d-x", "pr-state", "2026-10-01T00:00:00.100000Z")])
    classes = run(tmp_path)["classes"]
    pr = classes["pr-state"]
    assert pr["idless_carried_claim_notes_window"] == 2
    assert pr["carried_claim_notes_window"] == 1  # the ledger note only
    assert pr["notes_contradicted"] == 1
    assert classes["file-exists"]["idless_carried_claim_notes_window"] == 0


def test_zero_row_class_says_none_recorded_and_never_a_ratio(tmp_path, cps):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")],
        files={"latest.json": checkpoint(
            "2026-10-05T00:00:00Z", item(BRANCH_TEXT, "d-branch"))})
    result = run(tmp_path)
    branch = result["classes"]["branch-state"]
    for field in ("notes_contradicted", "max_rows_on_one_note", "k_over_u"):
        assert branch[field] == "none recorded"
    assert branch["carried_claim_notes_window"] == 1
    assert "none recorded" in measure.render(result)
    assert result["classes"]["pr-state"]["k_over_u"] == 1.0


def test_check_counts_are_anchored_and_ignore_tagged_skips(tmp_path, cps):
    make_bucket(cps)
    usage = [
        "2026-10-01T00:00:00Z worldcheck:pr-state:contradicted",
        "2026-10-01T00:00:00Z worldcheck:pr-state:confirmed",
        "2026-10-01T00:00:00Z worldcheck:pr-state:skipped",
        "2026-10-01T00:00:00Z worldcheck:pr-state:skipped-cap",
        "2026-10-01T00:00:00Z worldcheck:pr-state:contradicted-extra",
        "2026-10-01T00:00:00Z worldcheck:contradicted",
        "2026-10-01T00:00:00Z status worldcheck:pr-state:contradicted",
        "2026-08-01T00:00:00Z worldcheck:pr-state:contradicted",
        "2026-08-01T00:00:00Z worldcheck:pr-state:confirmed",
        "2026-08-01T00:00:00Z worldcheck:receipt-validity:confirmed",
        "2026-10-02T00:00:00Z worldcheck:file-exists:contradicted"]
    result = run(tmp_path, usage=usage)
    pr = result["classes"]["pr-state"]
    assert pr["checks_contradicted_all"] == 2
    assert pr["checks_confirmed_all"] == 2
    assert pr["checks_contradicted_window"] == 1
    assert pr["checks_confirmed_window"] == 1
    assert result["classes"]["file-exists"]["checks_contradicted_window"] == 1
    assert result["inputs"]["usage_lines"] == len(usage)


def test_unattributed_is_window_checks_minus_ledger_rows(tmp_path, cps):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")])
    usage = ["2026-10-01T00:00:00Z worldcheck:pr-state:contradicted"] * 4
    assert run(tmp_path, usage=usage)["classes"]["pr-state"][
        "unattributed_window"] == 3


def test_confirmed_per_note_is_declared_absent_not_estimated(tmp_path, cps):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")])
    result = run(tmp_path)
    assert "confirmed_per_note" in result
    text = measure.render(result)
    assert "not in the stored data" in text
    for cls in result["classes"].values():
        assert not any("confirmed" in k and "note" in k for k in cls)


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns,
                                       hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_main_changes_no_file_it_reads_and_writes_only_out(tmp_path, cps, capsys):
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z"),
        '{"torn": '],
        files={"latest.json": checkpoint("2026-10-05T00:00:00Z",
                                         item(SECRET_TEXT, SECRET_ID)),
               "prev-1.json": "{not json"})
    log = tmp_path / "usage.log"
    write_usage(log, ["2026-10-01T00:00:00Z worldcheck:pr-state:contradicted"])
    out = tmp_path / "out" / "measurements.json"
    out.parent.mkdir()
    before = (snapshot(cps), snapshot(tmp_path / "out"),
              log.stat().st_mtime_ns, log.read_bytes())
    rc = measure.main(["--checkpoint-dir", str(cps), "--usage-log", str(log),
                       "--since", SINCE, "--out", str(out)])
    assert rc == 0
    assert snapshot(cps) == before[0]
    assert (log.stat().st_mtime_ns, log.read_bytes()) == before[2:]
    assert json.loads(out.read_text())["classes"]["pr-state"][
        "notes_contradicted"] == 1
    assert capsys.readouterr().out.strip()


def test_main_refuses_to_write_out_over_an_input(tmp_path, cps):
    make_bucket(cps)
    log = tmp_path / "usage.log"
    write_usage(log, [])
    for target in (log, cps / SECRET_BUCKET / "out.json"):
        with pytest.raises(SystemExit) as stop:
            measure.main(["--checkpoint-dir", str(cps), "--usage-log", str(log),
                          "--out", str(target)])
        assert stop.value.code == 2
    assert log.read_text() == "\n"
    assert not (cps / SECRET_BUCKET / "out.json").exists()


def test_output_holds_no_slug_id_claim_key_or_note_text(tmp_path, cps, capsys):
    make_bucket(cps,
                ledger=[ledger_row(SECRET_ID, "pr-state",
                                   "2026-10-01T00:00:00.100000Z"),
                        ledger_row("d-f", "file-exists",
                                   "2026-10-01T00:00:00.100000Z", text=FILE_TEXT),
                        ledger_row("d-b", "branch-state",
                                   "2026-10-01T00:00:00.100000Z", text=BRANCH_TEXT)],
                files={"latest.json": checkpoint(
                    "2026-10-05T00:00:00Z", item(SECRET_TEXT, SECRET_ID),
                    item(FILE_TEXT), item(BRANCH_TEXT, "d-b"))})
    log = tmp_path / "usage.log"
    write_usage(log, ["2026-10-01T00:00:00Z worldcheck:pr-state:contradicted"])
    out = tmp_path / "m.json"
    assert measure.main(["--checkpoint-dir", str(cps), "--usage-log", str(log),
                         "--since", SINCE, "--out", str(out)]) == 0
    blob = capsys.readouterr().out + out.read_text()
    forbidden = [SECRET_BUCKET, SECRET_ID, "secret note", "secretpath",
                 "secret-branch", "d-f", "d-b", "d-file", "s-prev", str(tmp_path),
                 key_for(SECRET_TEXT), key_for(FILE_TEXT), key_for(BRANCH_TEXT)]
    assert [f for f in forbidden if f in blob] == []
    assert not any(len(tok) == 64 and set(tok) <= set("0123456789abcdef")
                   and tok != json.loads(out.read_text())["inputs"]["sha256"]
                   for tok in blob.replace('"', " ").split())


def test_fingerprint_is_stable_and_blind_to_bucket_names(tmp_path):
    def build(root, name):
        root.mkdir()
        make_bucket(root, name, ledger=[
            ledger_row(SECRET_ID, "pr-state", "2026-10-01T00:00:00.100000Z")],
            files={"latest.json": checkpoint("2026-10-05T00:00:00Z",
                                             item(SECRET_TEXT, SECRET_ID))})
        log = root.parent / (name + ".log")
        write_usage(log, ["2026-10-01T00:00:00Z worldcheck:pr-state:confirmed"])
        return measure.scan(root, log, since=measure.parse_epoch(SINCE),
                            now=measure.parse_epoch(NOW))["inputs"]

    a = build(tmp_path / "one", "bucket-aaa")
    b = build(tmp_path / "two", "bucket-bbb")
    assert a["sha256"] == b["sha256"]
    assert (a["buckets"], a["checkpoint_files"], a["readable"],
            a["ledger_files"], a["ledger_rows"]) == (1, 1, 1, 1, 1)
    assert a["usage_lines"] == 1
    (tmp_path / "one" / "bucket-aaa" / "prev-1.json").write_text("{}")
    c = measure.scan(tmp_path / "one", tmp_path / "bucket-aaa.log",
                     since=measure.parse_epoch(SINCE),
                     now=measure.parse_epoch(NOW))["inputs"]
    assert c["sha256"] != a["sha256"]
    assert c["checkpoint_files"] == 2


def test_unreadable_checkpoints_are_counted_not_fatal(tmp_path, cps):
    make_bucket(cps, files={"latest.json": "{broken", "prev-1.json": "[]",
                            "prev-2.json": checkpoint("2026-10-05T00:00:00Z")})
    inputs = run(tmp_path)["inputs"]
    assert inputs["checkpoint_files"] == 3
    assert inputs["readable"] == 1


def test_histogram_buckets(tmp_path, cps):
    rows = []
    for ident, n in (("d-a", 1), ("d-b", 3), ("d-c", 5), ("d-d", 12)):
        rows += [ledger_row(ident, "file-exists",
                            f"2026-10-01T00:00:{i:02d}.100000Z", text=FILE_TEXT)
                 for i in range(n)]
    make_bucket(cps, ledger=rows)
    c = run(tmp_path)["classes"]["file-exists"]
    assert c["repeat_histogram"] == {"1": 1, "2-4": 1, "5-9": 1, "10+": 1}
    assert c["max_rows_on_one_note"] == 12
    assert c["ledger_contradiction_rows_window"] == 21


def test_class_names_match_the_ledger_the_store_reads():
    from daimon_briefing import store
    assert set(measure.CLASSES) == store.WORLD_CHECKS
    assert {c + measure.CURE_SUFFIX for c in measure.CLASSES} == (
        store.WORLD_CURE_CHECKS)


def test_usage_line_in_the_first_ledger_second_is_inside_the_window(tmp_path, cps):
    # usage stamps are floored to the second, ledger stamps are not: the
    # window opens at the whole second of the earliest world row, or the
    # check that wrote that very row would fall one fraction outside it.
    make_bucket(cps, ledger=[
        ledger_row(SECRET_ID, "file-exists", "2026-10-01T06:07:01.900000Z",
                   text=FILE_TEXT)])
    result = run(tmp_path, since=None, usage=[
        "2026-10-01T06:07:01Z worldcheck:file-exists:contradicted"])
    c = result["classes"]["file-exists"]
    assert result["window"]["since"] == "2026-10-01T06:07:01Z"
    assert c["checks_contradicted_window"] == 1
    assert c["unattributed_window"] == 0
