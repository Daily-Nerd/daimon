import json
import time
from daimon_ui import reader
from tests.ui.conftest import make_checkpoint

BENCH_ID = "o-beeecc0beeecc0"


def test_flat_scan_cost_60_sessions_with_cross_project_noise(tmp_path):
    """Spec o-7b14d1fb03e7: measure the flat-scan cost of item_biography.
    Real deployment shape: the shared flat dir holds many projects' files
    (162 today, only 3 ours) and project_history parses every one to filter
    by slug — so noise files dominate wall time and MUST be in the fixture."""
    d = tmp_path / "checkpoints"
    d.mkdir()
    slug = "-tmp-proj"
    (d / slug).mkdir()

    for i in range(60):
        cp = make_checkpoint(
            created=f"2026-06-{(i % 28) + 1:02d}T10:00:00Z",
            topic=f"session {i}", session_id=f"sess-{i:04d}",
            open_questions=[
                {"text": "benched item", "id": BENCH_ID, "trust": "verbatim",
                 "quote": "measured, not guessed"}] + [
                {"text": f"filler {j}", "id": f"o-{j:06d}{i:06d}"} for j in range(20)],
        )
        cp["project_slug"] = slug
        (d / f"sess-{i:04d}.json").write_text(json.dumps(cp))

    for i in range(100):
        cp = make_checkpoint(created="2026-07-01T10:00:00Z",
                             topic=f"noise {i}", session_id=f"noise-{i:04d}",
                             open_questions=[
                                 {"text": f"noise q {j}", "id": f"o-n{j:05d}{i:05d}"}
                                 for j in range(20)])
        cp["project_slug"] = f"-other-{i % 10}"
        (d / f"noise-{i:04d}.json").write_text(json.dumps(cp))

    t0 = time.perf_counter()
    got = reader.item_biography(d, slug, BENCH_ID)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert got["ok"] is True
    assert len(got["trust_anatomy"]["chain"]) == 60
    print(f"\nflat-scan cost: {elapsed_ms:.1f}ms "
          f"(160 files, 60 target sessions, 100 cross-project noise)")
    assert elapsed_ms < 200, f"flat scan took {elapsed_ms:.1f}ms, budget 200ms"
