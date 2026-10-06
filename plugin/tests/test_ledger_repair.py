"""#1132 2c-2: `daimon ledger repair` and the single-key scrub it shares with
forget.

Ledgers here are built from raw bytes, never through the package writers, so
repair is judged against shapes the writers cannot make.
"""
import json

from daimon_briefing import jsonl

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
