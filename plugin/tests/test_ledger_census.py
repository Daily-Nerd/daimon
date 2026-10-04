"""The read-only ledger census (#1132 PR 2b).

Fixtures are plain byte writes, never the package's own appenders, so the
census is judged against files it did not write. The census must report
names, states and counts only: every test that plants a forgotten value also
asserts the value (and its key) is absent from the output.
"""

import json

from daimon_briefing import config, ledger_census, normalize, surfaces

SLUG = "-census-proj"
SECRET = "the forgotten sentence nobody may read back"


def _bucket():
    d = config.checkpoint_dir() / SLUG
    d.mkdir(parents=True, exist_ok=True)
    return d


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False).encode("utf-8") + b"\n"


def _forget(key):
    (_bucket() / "events.jsonl").write_bytes(_line(
        ts="2026-01-01T00:00:00Z", kind="resolution", item_ref="i-gone",
        status=f"forgotten:{key}", source="cli"))


def test_an_empty_bucket_reports_every_declared_ledger_absent():
    result = ledger_census.census_bucket(SLUG)
    assert set(result["ledgers"]) == set(surfaces.bucket_ledger_names())
    for entry in result["ledgers"].values():
        assert entry == {"state": "absent", "torn": 0, "split": 0,
                         "garbage": 0, "tombstoned_present": 0}
    assert result["undeclared"] == []


def test_each_ledger_reports_its_own_health_and_counts():
    d = _bucket()
    (d / "trust.jsonl").write_bytes(_line(a=1) + b'{"cut\n')
    (d / "amendments.jsonl").write_bytes(b"<<<<<<< HEAD\n" + _line(a=1))
    (d / "requests.jsonl").write_bytes(_line(a=1))
    ledgers = ledger_census.census_bucket(SLUG)["ledgers"]
    assert ledgers["trust.jsonl"]["state"] == "degraded"
    assert ledgers["trust.jsonl"]["torn"] == 1
    assert ledgers["amendments.jsonl"]["state"] == "unreadable"
    assert ledgers["amendments.jsonl"]["garbage"] == 1
    assert ledgers["requests.jsonl"]["state"] == "ok"
    assert ledgers["refutations.jsonl"]["state"] == "absent"


def test_a_row_still_carrying_a_forgotten_value_is_counted_not_shown():
    _forget(normalize.content_key(SECRET))
    d = _bucket()
    (d / "trust.jsonl").write_bytes(
        _line(reason=SECRET, evidence=["fine"]) + _line(reason="other"))
    (d / "refutations.jsonl").write_bytes(
        _line(subject="clean", anchors=[SECRET]))
    result = ledger_census.census_bucket(SLUG)
    assert result["ledgers"]["trust.jsonl"]["tombstoned_present"] == 1
    assert result["ledgers"]["refutations.jsonl"]["tombstoned_present"] == 1
    assert result["ledgers"]["amendments.jsonl"]["tombstoned_present"] == 0
    blob = json.dumps(result)
    assert SECRET not in blob and normalize.content_key(SECRET) not in blob


def test_no_forgotten_value_means_nothing_is_counted():
    (_bucket() / "trust.jsonl").write_bytes(_line(reason=SECRET))
    result = ledger_census.census_bucket(SLUG)
    assert result["ledgers"]["trust.jsonl"]["tombstoned_present"] == 0


def test_the_census_follows_the_registry_prose_column_not_its_own_list():
    # `evidence` is declared prose for trust.jsonl but `unlisted` is not:
    # a forgotten value in an undeclared field is not this census's business.
    _forget(normalize.content_key(SECRET))
    (_bucket() / "trust.jsonl").write_bytes(_line(unlisted=SECRET))
    result = ledger_census.census_bucket(SLUG)
    assert result["ledgers"]["trust.jsonl"]["tombstoned_present"] == 0


def test_a_jsonl_the_registry_does_not_declare_is_named_not_skipped():
    d = _bucket()
    (d / "mystery.jsonl").write_bytes(_line(reason=SECRET))
    (d / "notes.txt").write_bytes(b"ignored, not a jsonl")
    result = ledger_census.census_bucket(SLUG)
    assert result["undeclared"] == ["mystery.jsonl"]
    assert SECRET not in json.dumps(result)


def test_a_checkpoint_surface_still_holding_a_forgotten_value_is_counted():
    from daimon_briefing import store
    section, key = store._ITEM_LISTS[0]
    _forget(normalize.content_key(SECRET))
    payload = {"project_slug": SLUG,
               section: {key: [{"id": "i-1", "text": SECRET},
                               {"id": "i-2", "text": "fine"}]}}
    (_bucket() / "S1.json").write_text(json.dumps(payload), encoding="utf-8")
    result = ledger_census.census_bucket(SLUG)
    assert result["checkpoints"] == {"tombstoned_present": 1}
    assert SECRET not in json.dumps(result)


def test_checkpoint_scan_can_be_skipped():
    result = ledger_census.census_bucket(SLUG, checkpoints=False)
    assert "checkpoints" not in result


def test_machine_files_report_health_only():
    cp = config.checkpoint_dir()
    cp.mkdir(parents=True, exist_ok=True)
    (cp / "migrations.jsonl").write_bytes(_line(a=1) + b'{"cut\n')
    logs = config.log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "checks.jsonl").write_bytes(b"not json\n")
    tomb = config.team_dir() / "remote-a" / "sub" / "tombstones.jsonl"
    tomb.parent.mkdir(parents=True)
    tomb.write_bytes(_line(key="abc"))
    result = ledger_census.census_machine()
    assert result["checkpoints/migrations.jsonl"]["state"] == "degraded"
    assert result["logs/checks.jsonl"]["state"] == "unreadable"
    assert result["logs/recall-delivery.jsonl"]["state"] == "absent"
    assert result["team/remote-a/sub/tombstones.jsonl"]["state"] == "ok"
    for entry in result.values():
        assert set(entry) == {"state", "torn", "split", "garbage"}


def test_machine_census_survives_a_missing_team_dir():
    result = ledger_census.census_machine()
    assert not any(k.startswith("team/") for k in result)


def test_a_missing_bucket_dir_is_all_absent_not_an_error():
    result = ledger_census.census_bucket("-never-written")
    assert {e["state"] for e in result["ledgers"].values()} == {"absent"}
