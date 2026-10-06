"""The five lifecycle ledgers read per line through `jsonl.read` (#1132 3b).

Fixtures are raw bytes, never a package appender. One undecodable byte costs
its own line and no more, a row holding a raw U+2028/U+2029/U+0085 is one row,
and `_line` counts the rows that were read.
"""

import json

import pytest

from daimon_briefing import amendments, refutations, relations, requests, trust

PROJECT = "/p/ledger-readers"
SEPARATORS = [" ", " ", "\u0085"]
BAD = b"\xff\xfe not utf-8\n"


def _refutation(n, **extra):
    return {"event": "asserted", "refutation_id": f"r-{n:012x}", **extra}


def _request(n, **extra):
    return {"event": "opened", "request_id": f"q-{n:012x}", **extra}


def _trust(n, **extra):
    return {"event": "quarantined", "quarantine_id": f"tr-{n:012x}", **extra}


def _relation(n, **extra):
    return {"event": "proposed", "relation_id": f"rel-{n:016x}", "order": n,
            **extra}


def _amendment(n, **extra):
    return {"event": "proposed", "amendment_id": f"a-{n:012x}", **extra}


# (module, row builder, the id field)
LEDGERS = [
    pytest.param(refutations, _refutation, "refutation_id", id="refutations"),
    pytest.param(requests, _request, "request_id", id="requests"),
    pytest.param(trust, _trust, "quarantine_id", id="trust"),
    pytest.param(relations, _relation, "relation_id", id="relations"),
    pytest.param(amendments, _amendment, "amendment_id", id="amendments"),
]


def _line(row):
    return json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n"


def _put(module, *chunks):
    path = module._path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))
    return path


@pytest.mark.parametrize("module,build,idf", LEDGERS)
def test_events_read_the_good_rows_around_an_undecodable_line(
        module, build, idf, tmp_checkpoint_dir):
    _put(module, _line(build(1)), BAD, _line(build(2)))
    got = module.events(project_dir=PROJECT)
    assert [r[idf] for r in got] == [build(1)[idf], build(2)[idf]]


@pytest.mark.parametrize("sep", SEPARATORS)
@pytest.mark.parametrize("module,build,idf", LEDGERS)
def test_events_keep_a_row_holding_a_raw_line_separator(
        module, build, idf, sep, tmp_checkpoint_dir):
    _put(module, _line(build(1, note=f"before{sep}after")), _line(build(2)))
    got = module.events(project_dir=PROJECT)
    assert [r[idf] for r in got] == [build(1)[idf], build(2)[idf]]
    assert got[0]["note"] == f"before{sep}after"


@pytest.mark.parametrize("module,build,idf", LEDGERS)
def test_line_counts_the_rows_that_were_read_not_the_lines_around_them(
        module, build, idf, tmp_checkpoint_dir):
    _put(module, _line(build(1)), b'{"cut": "half a row', b"\n",
         _line(build(2)))
    got = module.events(project_dir=PROJECT)
    assert [r["_line"] for r in got] == [0, 1]


@pytest.mark.parametrize("module,build,idf", LEDGERS)
def test_a_directory_in_the_ledgers_place_reads_empty(
        module, build, idf, tmp_checkpoint_dir):
    module._path(PROJECT).mkdir(parents=True)
    assert module.events(project_dir=PROJECT) == []


@pytest.mark.parametrize("module,build,idf", LEDGERS)
def test_a_missing_ledger_reads_empty(module, build, idf, tmp_checkpoint_dir):
    assert module.events(project_dir=PROJECT) == []


# ---- refutations: strict, and the policy tombstones ----------------------

def test_strict_events_raise_for_an_undecodable_byte(tmp_checkpoint_dir):
    _put(refutations, _line(_refutation(1)), BAD)
    with pytest.raises(ValueError):          # UnicodeDecodeError is one
        refutations.events(PROJECT, strict=True)


def test_strict_events_skip_a_stray_text_line_and_keep_the_good_rows(
        tmp_checkpoint_dir):
    _put(refutations, b"<<<<<<< HEAD\n", _line(_refutation(1)),
         b"not json at all\n", _line(_refutation(2)))
    got = refutations.events(PROJECT, strict=True)
    assert [r["refutation_id"] for r in got] == [
        "r-000000000001", "r-000000000002"]


def test_default_events_read_around_an_undecodable_byte(tmp_checkpoint_dir):
    _put(refutations, _line(_refutation(1)), BAD)
    assert len(refutations.events(PROJECT)) == 1


def _tombstone_row(n, **extra):
    return {"sender": "s", "to": "t", "kind": "info", "verb": "v", "by": "b",
            "ruling_id": f"r-{n:012x}", "policy_sha256": "x",
            "active_from": n, "active_until": n + 1, **extra}


def test_policy_tombstones_read_around_bad_bytes_and_a_line_separator(
        tmp_checkpoint_dir):
    path = refutations._tombstone_path(PROJECT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_line(_tombstone_row(1, note="a b")) + BAD
                     + _line(_tombstone_row(2)))
    got = refutations._read_policy_tombstones(PROJECT)
    assert {e[5] for e in got} == {"r-000000000001", "r-000000000002"}
