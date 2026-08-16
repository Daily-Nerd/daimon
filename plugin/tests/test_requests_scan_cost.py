"""#694 PR 2 deliverable 5: combined scan-cost measurement, with a stated
budget, for the composer + panel + stamp `daimon brief` now performs.

The inbox/panel scans every bucket's requests.jsonl (`requests._bucket_slugs`
via `recipient_join`), and PR 3's stale derivation will scan each rendered
card's session pointers (cap 3, D3) in the SAME brief cycle. PR 3 is not
built here, so this measures what PR 2 actually adds to a brief — the
composer scan, the panel's cap/sort, and the post-print surfaced stamp — over
a realistic multi-bucket store, and folds in a STAND-IN per-card read of the
same SHAPE the future stale scan will cost (one `store.list_buckets()` call
per rendered card, capped at 3), so the budget is not measuring a rosier
feature than the one that will actually ship once PR 3 lands.
"""
import time

from daimon_briefing import requests, store

RECIPIENT = "/p/scan-cost-recipient"
N_BUCKETS = 50          # "a realistic multi-bucket store" per the PR brief
N_ADDRESSED = 8         # buckets that actually address RECIPIENT

# Budget reasoning: measured on this machine at ~35-45ms for 50 buckets (8
# addressed) — see the printed line this test emits; dominated by the 50
# individual file reads (`_bucket_slugs` + one `events()` read per matching
# bucket), not by CPU work. `daimon brief` already pays several ledger reads
# per section (withhold, corroboration, worldcheck, staleness) in the same
# call, each bounded by project size rather than fleet size — this is the
# first one that scales with the NUMBER OF PROJECTS on the machine, which is
# exactly why it gets its own stated budget instead of riding along
# unmeasured. 150ms is roughly 3.5x the observed cost: enough headroom for a
# slower disk or CI runner that a healthy run never trips it, tight enough
# that a regression (e.g. an accidental full-fold-and-refold per bucket
# instead of one read) would still fail it.
_BUDGET_MS = 150.0


def _seed(n_buckets=N_BUCKETS, n_addressed=N_ADDRESSED):
    to = store.project_slug(RECIPIENT)
    for i in range(n_buckets):
        project_dir = f"/p/scan-cost-sender-{i}"
        store.write_checkpoint(f"S-scan-{i}", {
            "session_id": f"S-scan-{i}", "created": "2026-08-16T00:00:00Z",
            "working_context": {"recent_decisions": [
                {"text": "x", "trust": "inferred"}]},
        }, project_dir=project_dir)
        slug = store.project_slug(project_dir)
        if i < n_addressed:
            requests.open_request(
                to=to, ask=f"ask {i} about the release", why="because",
                channel="cli-agent", project_dir=slug)
        else:
            # A bucket with SOME request traffic, none of it addressed to
            # RECIPIENT — the composer must still open and skim it, the same
            # cost profile as an unrelated project sending elsewhere.
            requests.open_request(
                to="-p-someone-else", ask=f"unrelated ask {i}", why="why",
                channel="cli-agent", project_dir=slug)


def _d3_shaped_stub_scan(rows) -> None:
    """Stand-in for PR 3's per-card session-pointer scan (D3), capped at 3,
    same SHAPE (one directory read per card). Not built here — folded in so
    this measurement does not understate what the feature costs once PR 3
    ships the real one."""
    for _row in rows[:3]:
        store.list_buckets()


def test_combined_brief_time_cost_stays_within_budget(tmp_checkpoint_dir,
                                                       capsys):
    _seed()
    start = time.perf_counter()
    entry = requests.inbox_renderable(project_dir=RECIPIENT)
    for row in entry["rows"]:
        if requests.needs_surfaced_stamp(row):
            requests.stamp_surfaced(row["request_id"], project_dir=RECIPIENT)
    _d3_shaped_stub_scan(entry["rows"])
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert entry["rows"], "measurement is void if nothing was addressed here"
    with capsys.disabled():
        print(f"\n#694 PR 2 combined scan cost: {elapsed_ms:.2f}ms over "
             f"{N_BUCKETS} buckets ({N_ADDRESSED} addressed) — "
             f"budget {_BUDGET_MS}ms")
    assert elapsed_ms <= _BUDGET_MS, (
        f"combined composer+panel+stamp cost {elapsed_ms:.2f}ms exceeds the "
        f"{_BUDGET_MS}ms budget over {N_BUCKETS} buckets")
