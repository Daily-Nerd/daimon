"""Integrity self-check for the #470 replay A/B harness (replay_ab.py).

Builds a fully synthetic daimon home (zero real data) through the REAL write
path (store.write_checkpoint, so project_slug/author/first_seen/id stamps are
production-shaped), replays a small synthetic dataset, and asserts the
harness's executable correctness claims:

  1. determinism   — two runs produce byte-identical analytical artifacts
  2. inert gate    — threshold 0: mass < 0 can never fire, so B == A exactly
  3. B-silence     — threshold 1e9: every non-exempt session gates, so the
                     generic and rare prompts A-inject but B-inject nothing
  4. cooldown      — second prompt of one session never re-injects the origin
                     arm A already injected (per-session seen-state)
  5. snapshot      — a prompt BEFORE a checkpoint's created ts cannot see it,
                     and the run walks >= 2 snapshot equivalence classes

This substitutes for plugin-suite tests: the harness is research code, but
its correctness claims must be executable (issue #470).
"""

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import store

import replay_ab

PROJ = "/repo/fixture"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
DAY = 86400.0
SWEEP = ["0", "8", "1000000000"]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _cp(sid, created, decisions):
    return {
        "session_id": sid,
        "created": _iso(created),
        "working_context": {
            "active_topic": {"text": "fixture synthetic work",
                             "trust": "inferred"},
            "open_questions": [],
            "recent_decisions": decisions,
        },
        "epistemic_snapshot": {"strong_beliefs": [], "uncertainties": [],
                               "contradictions_flagged": []},
    }


def build_fixture_home(root: Path) -> tuple[Path, Path]:
    """Synthetic flat store + dataset. Written through store.write_checkpoint
    under a pinned env so nothing can touch the real ~/.daimon."""
    home = root / "home"
    with replay_ab._pinned_env(home, author="fixture"):
        # 40 generic-vocabulary filler decisions: makes "deploy pipeline" a
        # low-idf pair (df ~ 41 of ~47 items), same shape as the plugin's
        # test_recall_idf_gate corpus.
        filler = [{"text": f"deploy pipeline filler{i:02d} note",
                   "trust": "inferred"} for i in range(40)]
        store.write_checkpoint(
            "S-hist", _cp("S-hist", T0 + 1 * DAY, filler), project_dir=PROJ)
        store.write_checkpoint(
            "S-gen", _cp("S-gen", T0 + 2 * DAY,
                         [{"text": "deploy pipeline rework",
                           "trust": "inferred"}]), project_dir=PROJ)
        store.write_checkpoint(
            "S-rare", _cp("S-rare", T0 + 3 * DAY,
                          [{"text": "zyxwvut quorblatz recalibration rework",
                            "trust": "inferred"}]), project_dir=PROJ)
        # Padding session so S-rare/S-gen are not the project/global latest
        # (the briefing exclusion would otherwise mask them from suggest).
        store.write_checkpoint(
            "S-pad", _cp("S-pad", T0 + 4 * DAY,
                         [{"text": "unrelated aardvark bookkeeping entry",
                           "trust": "inferred"}]), project_dir=PROJ)
    rows = [
        # idx 0: BEFORE S-rare exists — snapshot boundary must hide it.
        {"prompt": "zyxwvut quorblatz recalibration status",
         "ts": T0 + 2.5 * DAY, "session": "sess-early", "project": PROJ},
        # idx 1: generic 2-3-term prompt — A injects, B silences at high t.
        {"prompt": "checking the deploy pipeline again",
         "ts": T0 + 5 * DAY, "session": "sess-generic", "project": PROJ},
        # idx 2+3: same session — cooldown across a session's prompts.
        {"prompt": "zyxwvut quorblatz recalibration status",
         "ts": T0 + 6 * DAY, "session": "sess-rare", "project": PROJ},
        {"prompt": "revisit zyxwvut quorblatz recalibration status",
         "ts": T0 + 6 * DAY + 3600, "session": "sess-rare", "project": PROJ},
        # idx 4: machine prompt — must be skipped, never replayed.
        {"prompt": "<task-notification> build finished on ci",
         "ts": T0 + 7 * DAY, "session": "sess-machine", "project": PROJ},
    ]
    dataset = root / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(r) + "\n" for r in rows),
                       encoding="utf-8")
    return home, dataset


_ARTIFACTS = ("diffs.jsonl", "judging.jsonl", "judging-holdout.jsonl",
              "key.jsonl", "summary.json")


def _check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory(prefix="replay-ab-verify-") as td:
        root = Path(td)
        home, dataset = build_fixture_home(root)
        print("verify: fixture home built, running replay twice ...")
        res1 = replay_ab.run(dataset, home, SWEEP, root / "out1", seed=470)
        res2 = replay_ab.run(dataset, home, SWEEP, root / "out2", seed=470)

        # 1. determinism: byte-identical analytical artifacts.
        for name in _ARTIFACTS:
            same = ((root / "out1" / name).read_bytes()
                    == (root / "out2" / name).read_bytes())
            ok &= _check(f"determinism: {name} byte-identical", same)

        prompts = {p["idx"]: p for p in res1["prompts"]}
        summ = res1["summary"]
        thr = summ["thresholds"]

        # 2. threshold 0: gate can never fire (mass < 0 impossible) -> B == A.
        ok &= _check(
            "inert gate at threshold 0: B == A, zero diff prompts",
            thr["0"]["diff_prompts"] == 0
            and thr["0"]["b_injections"] == thr["0"]["a_injections"]
            and thr["0"]["a_only"] == 0 and thr["0"]["b_only"] == 0,
            f"a={thr['0']['a_injections']} b={thr['0']['b_injections']}")

        # 3. known B-silence at the high threshold.
        hi = thr["1000000000"]
        p1, p2 = prompts[1], prompts[2]
        ok &= _check(
            "B-silence at 1e9: generic prompt A-injects, B empty",
            len(p1["arms"]["A"]) >= 1 and p1["arms"]["B@1000000000"] == [],
            f"A={len(p1['arms']['A'])}")
        ok &= _check(
            "B-silence at 1e9: rare prompt a_only carries S-rare",
            any(i["session_id"] == "S-rare" for i in p2["arms"]["A"])
            and p2["arms"]["B@1000000000"] == [],
        )
        ok &= _check(
            "B-silence at 1e9: b_injections < a_injections in summary",
            hi["b_injections"] < hi["a_injections"],
            f"a={hi['a_injections']} b={hi['b_injections']}")

        # (moderate threshold sanity: rare-term mass rides through at 8)
        ok &= _check(
            "rare-term session passes at threshold 8",
            any(i["session_id"] == "S-rare"
                for i in p2["arms"]["B@8"]))

        # 4. seen-state cooldown across two prompts of one session.
        p3 = prompts[3]
        ok &= _check(
            "cooldown: prompt 2 of sess-rare re-injects nothing in arm A",
            len(p2["arms"]["A"]) >= 1 and p3["arms"]["A"] == [],
            f"first={len(p2['arms']['A'])} second={len(p3['arms']['A'])}")

        # 5. snapshot boundary + class walking.
        p0 = prompts[0]
        ok &= _check(
            "snapshot: pre-creation prompt cannot see S-rare",
            p0["arms"]["A"] == [])
        ok &= _check(
            "snapshot: >= 2 equivalence classes walked",
            summ["corpus"]["n_snapshot_classes"] >= 2,
            f"classes={summ['corpus']['n_snapshot_classes']}")
        ok &= _check(
            "machine prompt skipped",
            summ["corpus"]["n_machine_skipped"] == 1
            and prompts[4]["machine"])

        # blind-file hygiene: no arm vocabulary in what the judge reads.
        blind = ((root / "out1" / "judging.jsonl").read_text()
                 + (root / "out1" / "judging-holdout.jsonl").read_text())
        ok &= _check(
            "judging files carry no arm labels",
            "a-only" not in blind and "b-only" not in blind
            and '"arms"' not in blind)

    print("VERIFY " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
