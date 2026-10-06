"""#1132 2c-2: the quarantine sidecar `<stem>.quarantined-lines`.

Repair moves a ledger's torn and garbage lines here, one envelope row per
line. The envelope `text` is a plaintext carrier, so it is a declared surface:
forget reaches it, the census lists it, the audit scans it and a bucket
migration carries it.
"""
import json

from daimon_briefing import (config, ledger_census, ledger_repair,
                             normalize, privacy, store, surfaces)

PROJECT = "/p/quarantined-lines"
VALUE = "zqxsidecarcanary the vault passphrase is the dog's name"
KEY = normalize.content_key(VALUE)
OTHER = "an unrelated fragment that must stay in the sidecar"


def _bucket():
    d = config.checkpoint_dir() / store.project_slug(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _envelope(text, kind="torn", ledger="trust.jsonl"):
    return {"ledger": ledger, "quarantined_at": "2026-10-05T00:00:00Z",
            "kind": kind, "text": text}


def _write_sidecar(name="trust.quarantined-lines", *envelopes):
    path = _bucket() / name
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n"
                            for e in envelopes), encoding="utf-8")
    return path


def _texts(path):
    return [json.loads(ln)["text"] for ln in path.read_text(
        encoding="utf-8").split("\n") if ln]


def _tombstone(key=KEY, ref="i-gone"):
    with (_bucket() / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-05T00:00:00Z", "kind": "tombstone",
                            "item_ref": ref, "status": f"forgotten:{key}",
                            "source": "cli"}) + "\n")


def test_the_sidecar_is_a_declared_plaintext_surface():
    assert surfaces.quarantine_sidecar("events.jsonl") == \
        "events.quarantined-lines"
    s = surfaces.match("checkpoints/{slug}/events.quarantined-lines")
    assert s is not None
    assert s.plaintext and s.delete == "rewrite" and s.walker == "forget"
    assert [fp.path for fp in s.prose] == [("text",)]
    assert s.deleter == "ledger_repair.forget_quarantined_lines"


def test_forget_with_text_purges_only_the_matching_envelope_rows():
    path = _write_sidecar(
        "trust.quarantined-lines",
        _envelope('{"note": "' + VALUE + '", "tr'),
        _envelope(OTHER, kind="garbage"))
    result = ledger_repair.forget_quarantined_lines(
        KEY, text=VALUE, project_dir=PROJECT)
    assert result == ledger_repair.Purged(1, 1)
    assert _texts(path) == [OTHER]
    assert VALUE not in path.read_text(encoding="utf-8")


def test_text_match_sees_the_value_as_json_escaped_inside_a_torn_line():
    quoted = 'she said "stop" and left'
    line = '{"note": ' + json.dumps(quoted) + ', "tr'
    path = _write_sidecar("trust.quarantined-lines", _envelope(line),
                          _envelope(OTHER))
    ledger_repair.forget_quarantined_lines(
        normalize.content_key(quoted), text=quoted, project_dir=PROJECT)
    assert _texts(path) == [OTHER]


def test_key_only_purge_matches_a_json_string_in_the_fragment():
    path = _write_sidecar(
        "events.quarantined-lines",
        _envelope('{"id": "x", "note": "' + VALUE + '", "cut', ledger="events"),
        _envelope(VALUE, kind="garbage", ledger="events"),
        _envelope('{"id": "y", "note": "' + OTHER + '", "cut', ledger="events"))
    result = ledger_repair.forget_quarantined_lines(KEY, project_dir=PROJECT)
    assert result == ledger_repair.Purged(2, 1)
    assert _texts(path) == ['{"id": "y", "note": "' + OTHER + '", "cut']


def test_every_sidecar_in_the_bucket_is_reached():
    a = _write_sidecar("trust.quarantined-lines", _envelope(VALUE, "garbage"))
    b = _write_sidecar("events.quarantined-lines", _envelope(VALUE, "garbage"),
                       _envelope(OTHER, "garbage"))
    result = ledger_repair.forget_quarantined_lines(KEY, project_dir=PROJECT)
    assert result == ledger_repair.Purged(2, 1)
    assert _texts(a) == [] and _texts(b) == [OTHER]


def test_a_forget_that_matches_nothing_leaves_the_sidecar_bytes_alone():
    path = _write_sidecar("trust.quarantined-lines", _envelope(OTHER))
    before = path.read_bytes()
    assert ledger_repair.forget_quarantined_lines(
        KEY, project_dir=PROJECT) == ledger_repair.Purged(0, 1)
    assert path.read_bytes() == before


def test_the_census_lists_the_sidecar_and_never_as_undeclared():
    _write_sidecar("trust.quarantined-lines", _envelope(OTHER))
    result = ledger_census.census_bucket(store.project_slug(PROJECT))
    entry = result["ledgers"]["trust.quarantined-lines"]
    assert entry["state"] == "ok"
    assert result["undeclared"] == []


def test_the_census_counts_a_sidecar_row_still_carrying_a_forgotten_value():
    _tombstone()
    _write_sidecar("trust.quarantined-lines",
                   _envelope('{"note": "' + VALUE + '", "cut'),
                   _envelope(OTHER))
    result = ledger_census.census_bucket(store.project_slug(PROJECT))
    assert result["ledgers"]["trust.quarantined-lines"][
        "tombstoned_present"] == 1
    assert VALUE not in json.dumps(result)


def test_the_audit_finds_a_value_in_the_sidecar_and_is_clean_after_forget():
    _tombstone()
    path = _write_sidecar("trust.quarantined-lines",
                          _envelope('{"note": "' + VALUE + '", "cut'),
                          _envelope(OTHER))
    found = privacy.audit_project(PROJECT)
    hits = [f for f in found["findings"] if f["path"] == str(path)]
    assert hits and hits[0]["content_hash"] == KEY
    assert str(path) not in found["unscannable"]
    ledger_repair.forget_quarantined_lines(KEY, project_dir=PROJECT)
    again = privacy.audit_project(PROJECT)
    assert not [f for f in again["findings"] if f["path"] == str(path)]
