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

from daimon_briefing import pending, requests, store

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


# The fix for the polarity bug: `daimon decide`'s request lane now sources
# from `requests.recipient_join` (see `pending._request_rows`) instead of
# the per-bucket `requests.records`, so it pays the same fleet-scan
# `_bucket_slugs` walks. On top of that, the append-order tiebreak
# (`pending._order_key`) reads each ADDRESSED bucket's `events()` a second
# time to seat the `seq` a foreign record has no local index for — one extra
# read per addressed bucket, not per fleet bucket.
#
# Budget reasoning: measured on this machine at ~3-4ms over the same
# 50-bucket/8-addressed fleet (`_seed()`) — see the printed line this test
# emits. Cheaper than the panel measurement above (~35-45ms) for a real
# reason, not a fluke: `decide` is a pure reader (#8 of the fix that added
# this test — see `pending.py`'s "WHY IT WRITES NOTHING"), so it never pays
# `stamp_surfaced`'s per-row write, and it skips the PR-3-shaped stub scan
# the panel test folds in. What it DOES pay is the same fleet walk
# (`_bucket_slugs` + one `events()` read per bucket) plus one extra
# `events()` read per ADDRESSED bucket for the append-order `seq` — bounded
# by N_ADDRESSED (8), not N_BUCKETS (50). 150ms keeps the panel's budget
# rather than tightening to the observed number: a slower disk or CI runner
# should not trip this on a healthy run, while a regression (e.g. re-reading
# every bucket, not just the addressed ones, for `seq`) still would.
_DECIDE_BUDGET_MS = 150.0


def test_decide_queue_time_cost_stays_within_budget(tmp_checkpoint_dir,
                                                     capsys):
    _seed()
    start = time.perf_counter()
    result = pending.queue(project_dir=RECIPIENT)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result["rows"], "measurement is void if nothing was addressed here"
    with capsys.disabled():
        print(f"\n#766 decide request-lane scan cost: {elapsed_ms:.2f}ms "
             f"over {N_BUCKETS} buckets ({N_ADDRESSED} addressed) — "
             f"budget {_DECIDE_BUDGET_MS}ms")
    assert elapsed_ms <= _DECIDE_BUDGET_MS, (
        f"pending.queue cost {elapsed_ms:.2f}ms exceeds the "
        f"{_DECIDE_BUDGET_MS}ms budget over {N_BUCKETS} buckets")


# #766 slice 3: `pending.foreign_counts` reads every bucket's requests ledger
# in ONE fleet-wide pass (grouped by request_id, folded once per group) plus
# one direct read of refutations.jsonl and amendments.jsonl per foreign
# bucket — deliberately NOT `pending.queue(project_dir=foreign_slug)` called
# once per foreign project, which would each pay `recipient_join`'s own
# fleet scan and turn the whole thing O(N^2) (91.4ms measured that way over
# 25 buckets against this same 150ms budget, and it only gets worse as the
# fleet grows).
#
# Budget reasoning: measured on this machine at ~4-6ms for 50 buckets — see
# the printed line this test emits; cheaper than the panel/decide numbers
# above because most of the fleet has neither a requests, refutations, nor
# amendments ledger for this test's foreign buckets, so each per-lane read
# is a single `exists()`-and-return-empty rather than a parse. Same fleet as
# the two measurements above (`_seed()`, 50 buckets), so 150ms keeps them
# comparable rather than inventing a new number tuned to look good — tight
# enough that a regression (e.g. a per-foreign-project `queue()` call
# reintroducing the O(N^2) shape this function exists to avoid) would still
# fail it well before the budget is reached.
_FOREIGN_BUDGET_MS = 150.0


def test_foreign_counts_time_cost_stays_within_budget(tmp_checkpoint_dir,
                                                       capsys):
    _seed()
    start = time.perf_counter()
    result = pending.foreign_counts(project_dir=RECIPIENT)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert result, "measurement is void if nothing waits elsewhere"
    with capsys.disabled():
        print(f"\n#766 slice 3 foreign-counts scan cost: {elapsed_ms:.2f}ms "
             f"over {N_BUCKETS} buckets — budget {_FOREIGN_BUDGET_MS}ms")
    assert elapsed_ms <= _FOREIGN_BUDGET_MS, (
        f"pending.foreign_counts cost {elapsed_ms:.2f}ms exceeds the "
        f"{_FOREIGN_BUDGET_MS}ms budget over {N_BUCKETS} buckets")
