"""An append-only log is judged on its tail (#1132 PR 10b fix round):
`jsonl.posture(..., tail_bytes=)` reads the last N bytes from the first line
boundary, so a delivery log that grows without bound does not cost a parse of
the whole file on every write."""

import errno


from daimon_briefing import config, jsonl, recall_telemetry
from daimon_briefing.jsonl import Health
from daimon_briefing.surfaces import WritePosture as W
from daimon_briefing.surfaces import Writer

NAME = "recall-delivery.jsonl"
ROW = b'{"at": "2026-10-01T00:00:00Z", "surface": "recall-inject"}\n'
TAIL = 64 * 1024


def _log(tmp_path, head=b"", rows=30000):
    path = tmp_path / NAME
    path.write_bytes(head + ROW * rows)
    assert path.stat().st_size > 1_500_000
    return path


def _spy_reads(monkeypatch):
    sizes = []
    real = jsonl._read_tail

    def spy(path, n):
        data = real(path, n)
        sizes.append(len(data))
        return data
    monkeypatch.setattr(jsonl, "_read_tail", spy)
    return sizes


def test_a_big_log_with_a_clean_tail_proceeds_and_reads_only_the_tail(
        tmp_path, monkeypatch):
    # Garbage far above the window is not judged: the window is the question.
    path = _log(tmp_path, head=b"<<<<<<< conflict\n")
    sizes = _spy_reads(monkeypatch)
    got = jsonl.posture(path, NAME, Writer.EMITTER, tail_bytes=TAIL)
    assert (got.health, got.write) == (Health.OK, W.PROCEED)
    assert sizes and max(sizes) <= TAIL


def test_a_torn_last_line_is_judged_on_the_window(tmp_path):
    path = _log(tmp_path)
    path.write_bytes(path.read_bytes() + b'{"torn": ')
    got = jsonl.posture(path, NAME, Writer.EMITTER, tail_bytes=TAIL)
    assert (got.health, got.write) == (Health.DEGRADED, W.PROCEED)


def test_garbage_in_the_window_skips_the_emitter(tmp_path):
    path = _log(tmp_path)
    path.write_bytes(path.read_bytes() + b"<<<<<<< conflict\n")
    got = jsonl.posture(path, NAME, Writer.EMITTER, tail_bytes=TAIL)
    assert (got.health, got.write) == (Health.UNREADABLE, W.SKIP)


def test_the_cut_starts_at_a_line_boundary(tmp_path):
    path = _log(tmp_path)
    data = jsonl._read_tail(path, 1000)
    assert data.startswith(b'{"at"') and len(data) <= 1000


def test_a_small_file_is_read_whole(tmp_path):
    path = tmp_path / NAME
    path.write_bytes(ROW * 3)
    assert jsonl._read_tail(path, TAIL) == ROW * 3


def test_a_transient_or_os_failure_is_still_judged(tmp_path, monkeypatch):
    path = _log(tmp_path)
    monkeypatch.setattr(jsonl.time, "sleep", lambda _s: None)

    def boom(p, n):
        raise OSError(errno.EAGAIN, "busy")
    monkeypatch.setattr(jsonl, "_read_tail", boom)
    got = jsonl.posture(path, NAME, Writer.EMITTER, tail_bytes=TAIL)
    assert (got.health, got.write) == (Health.TRANSIENT, W.SKIP)


def test_the_recall_delivery_log_is_judged_on_its_tail_by_default(
        tmp_checkpoint_dir, monkeypatch):
    path = config.recall_delivery_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"<<<<<<< conflict\n" + ROW * 30000)
    sizes = _spy_reads(monkeypatch)
    from datetime import datetime, timezone
    recall_telemetry.record(
        [{"item_id": "o-1", "match_score": 0.5}], query_terms=["gateway"],
        surface="recall-inject", now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert sizes and max(sizes) <= TAIL
    assert path.read_bytes().endswith(b"\n") and b'"o-1"' in path.read_bytes()


def test_a_registry_ledger_under_checkpoints_keeps_the_full_read(
        tmp_checkpoint_dir, monkeypatch):
    sizes = _spy_reads(monkeypatch)
    path = tmp_checkpoint_dir / "slug" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<<<<<<< conflict\n" + ROW * 30000)
    got = jsonl.posture(path, "events.jsonl", Writer.EMITTER)
    assert got.write is W.SKIP and sizes == []
