"""Integrity self-check for the replay A/B harness (replay_ab.py).

Builds a fully synthetic daimon home (zero real data) through the REAL write
path (store.write_checkpoint, so project_slug/author/first_seen/id stamps are
production-shaped), replays a small synthetic dataset, and asserts the
harness's executable correctness claims:

  1. determinism   — two runs produce byte-identical analytical artifacts
  2. identity      — under the shipped `none` variant, B == A exactly: any
                     diff there is a harness bug, not a finding
  3. inert arm     — a variant parameterised so it changes nothing also
                     yields B == A (the variant hook itself is transparent)
  4. B can silence — a variant that DOES drop rows produces a_only rows, a
                     smaller B volume, and correctly emitted diff/judging
                     artifacts (this is the path a real hypothesis takes)
  5. blind files   — nothing the judge reads carries an arm label
  6. cooldown      — second prompt of one session never re-injects the origin
                     arm A already injected (per-session seen-state)
  7. snapshot      — a prompt BEFORE a checkpoint's created ts cannot see it,
                     and the run walks >= 2 snapshot equivalence classes
  8. machine skip  — host-emitted prompts are never replayed

The silencing variant used by 3/4/5 is defined HERE, against a marker that
only the synthetic fixture contains: the harness must stay testable without
any real hypothesis existing in the repo.

This substitutes for plugin-suite tests: the harness is research code, but
its correctness claims must be executable.
"""

import doctest
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import store

import replay_ab
import variants

PROJ = "/repo/fixture"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
DAY = 86400.0

# Arm-B parameters for the test variant below: the first marker exists in the
# fixture corpus (that arm silences), the second matches nothing (that arm is
# inert and must reproduce A).
MARKER = "quorblatz"
NO_MARKER = "nosuchmarkerhere"
SWEEP = [MARKER, NO_MARKER]


def marker_silencer(ctx, suggest):
    """Synthetic test variant: drop matches whose text contains `param`.

    Deliberately trivial and fixture-only — it encodes no hypothesis about
    recall. Its whole job is to exercise the output-side variant path end to
    end: a B arm that drops rows must still produce a_only diffs, a smaller
    injection volume and correct judging artifacts."""
    marker = (ctx["param"] or "").lower()
    return [m for m in suggest()
            if marker not in (m.get("text") or "").lower()]


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
        # 40 generic-vocabulary filler decisions: gives the corpus a common
        # working vocabulary ("deploy pipeline") distinct from the one-off
        # marker vocabulary, so the two prompt shapes below retrieve from
        # different sessions.
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
                          [{"text": f"zyxwvut {MARKER} recalibration rework",
                            "trust": "inferred"}]), project_dir=PROJ)
        # Padding session so S-rare/S-gen are not the project/global latest
        # (the briefing exclusion would otherwise mask them from suggest).
        store.write_checkpoint(
            "S-pad", _cp("S-pad", T0 + 4 * DAY,
                         [{"text": "unrelated aardvark bookkeeping entry",
                           "trust": "inferred"}]), project_dir=PROJ)
    rows = [
        # idx 0: BEFORE S-rare exists — snapshot boundary must hide it.
        {"prompt": f"zyxwvut {MARKER} recalibration status",
         "ts": T0 + 2.5 * DAY, "session": "sess-early", "project": PROJ},
        # idx 1: generic prompt — retrieves the common-vocabulary sessions,
        # which carry no marker, so the silencing arm leaves it untouched.
        {"prompt": "checking the deploy pipeline again",
         "ts": T0 + 5 * DAY, "session": "sess-generic", "project": PROJ},
        # idx 2+3: same session — cooldown across a session's prompts. idx 2
        # is the prompt the marker variant silences.
        {"prompt": f"zyxwvut {MARKER} recalibration status",
         "ts": T0 + 6 * DAY, "session": "sess-rare", "project": PROJ},
        {"prompt": f"revisit zyxwvut {MARKER} recalibration status",
         "ts": T0 + 6 * DAY + 3600, "session": "sess-rare", "project": PROJ},
        # idx 4: machine prompt — must be skipped, never replayed.
        {"prompt": "<task-notification> build finished on ci",
         "ts": T0 + 7 * DAY, "session": "sess-machine", "project": PROJ},
    ]
    dataset = root / "dataset.jsonl"
    dataset.write_text("".join(json.dumps(r) + "\n" for r in rows),
                       encoding="utf-8")
    return home, dataset


def build_stance_fixture(root: Path) -> tuple[Path, Path]:
    """Synthetic flat store + dataset for the #483 `stance_gate` variant
    (variants.py): one checkpoint recent enough to be injectable, replayed
    against two prompts that share ITS vocabulary but differ only in
    grammatical stance — question-shaped vs statement-shaped — so any
    difference between arm A and the stance_gate arm is attributable to the
    gate, not to term overlap."""
    home = root / "stance-home"
    with replay_ab._pinned_env(home, author="fixture"):
        filler = [{"text": f"deploy pipeline filler{i:02d} note",
                   "trust": "inferred"} for i in range(40)]
        store.write_checkpoint(
            "T-hist", _cp("T-hist", T0 + 1 * DAY, filler), project_dir=PROJ)
        store.write_checkpoint(
            "T-src", _cp("T-src", T0 + 2 * DAY,
                         [{"text": "widget calibration procedure documented",
                           "trust": "inferred"}]), project_dir=PROJ)
        store.write_checkpoint(
            "T-pad", _cp("T-pad", T0 + 3 * DAY,
                         [{"text": "unrelated aardvark bookkeeping entry",
                           "trust": "inferred"}]), project_dir=PROJ)
    rows = [
        # idx 0: question-shaped — must pass through unchanged (B == A).
        {"prompt": "how do I run the widget calibration procedure?",
         "ts": T0 + 4 * DAY, "session": "sess-stance-q", "project": PROJ},
        # idx 1: statement-shaped — must be fully suppressed (B == []).
        {"prompt": "ran the widget calibration procedure and it passed",
         "ts": T0 + 4 * DAY + 3600, "session": "sess-stance-s",
         "project": PROJ},
    ]
    dataset = root / "stance-dataset.jsonl"
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
        print("verify: fixture home built, running replay ...")
        # The silencing variant runs twice: determinism is checked on
        # NON-EMPTY artifacts, which an all-identity run would not produce.
        res1 = replay_ab.run(dataset, home, SWEEP, root / "out1", seed=470,
                             variant=marker_silencer,
                             variant_name="test:marker_silencer")
        replay_ab.run(dataset, home, SWEEP, root / "out2", seed=470,
                      variant=marker_silencer,
                      variant_name="test:marker_silencer")
        res_id = replay_ab.run(dataset, home, [None], root / "out3", seed=470,
                               variant=variants.none, variant_name="none")

        # 1. determinism: byte-identical analytical artifacts.
        for name in _ARTIFACTS:
            same = ((root / "out1" / name).read_bytes()
                    == (root / "out2" / name).read_bytes())
            ok &= _check(f"determinism: {name} byte-identical", same)

        # 2. shipped `none` variant: B is A, exactly.
        id_arm = res_id["summary"]["arms"]["B"]
        ok &= _check(
            "identity variant `none`: B == A, zero diff prompts",
            id_arm["diff_prompts"] == 0
            and id_arm["b_injections"] == id_arm["a_injections"]
            and id_arm["a_only"] == 0 and id_arm["b_only"] == 0,
            f"a={id_arm['a_injections']} b={id_arm['b_injections']}")
        ok &= _check(
            "identity variant `none`: A actually injected something",
            id_arm["a_injections"] > 0,
            f"a={id_arm['a_injections']}")

        # 2b. #495: the rig must state what it can RESOLVE. A delta smaller
        #     than the minimum detectable effect is unreadable no matter how
        #     confidently it is reported, and that has to be visible in the
        #     artifact people share rather than rediscovered afterwards.
        res_block = id_arm.get("resolution")
        ok &= _check(
            "resolution: summary.json states the minimum detectable effect",
            isinstance(res_block, dict)
            and isinstance(res_block.get("mde_pp"), (int, float))
            and res_block["mde_pp"] > 0,
            f"block={res_block}")
        ok &= _check(
            "resolution: states n required to resolve 5pp, and it exceeds n",
            isinstance(res_block, dict)
            and isinstance(res_block.get("n_per_arm_for_5pp"), int)
            and res_block["n_per_arm_for_5pp"] > 0
            and res_block["n_per_arm_for_5pp"] > id_arm["a_injections"],
            f"need={(res_block or {}).get('n_per_arm_for_5pp')} "
            f"have={id_arm['a_injections']}")

        # 2c. #495: the null control. A placebo suppressing nothing must be
        #     the identity; a placebo suppressing rows must only ever REMOVE
        #     (it cannot invent a row suggest() did not return).
        res_p0 = replay_ab.run(dataset, home, ["0.0"], root / "out-p0",
                               seed=470, variant=variants.placebo,
                               variant_name="placebo")
        p0 = res_p0["summary"]["arms"]["B@0.0"]
        ok &= _check(
            "placebo at p=0 is the identity: B == A",
            p0["diff_prompts"] == 0 and p0["a_only"] == 0
            and p0["b_only"] == 0,
            f"a={p0['a_injections']} b={p0['b_injections']}")

        res_p1 = replay_ab.run(dataset, home, ["1.0"], root / "out-p1",
                               seed=470, variant=variants.placebo,
                               variant_name="placebo")
        p1 = res_p1["summary"]["arms"]["B@1.0"]
        ok &= _check(
            "placebo at p=1 suppresses everything and adds nothing",
            p1["b_injections"] == 0 and p1["b_only"] == 0
            and p1["a_injections"] > 0,
            f"a={p1['a_injections']} b={p1['b_injections']} "
            f"b_only={p1['b_only']}")

        res_p1b = replay_ab.run(dataset, home, ["1.0"], root / "out-p1b",
                                seed=470, variant=variants.placebo,
                                variant_name="placebo")
        ok &= _check(
            "placebo is deterministic for a given (seed, param)",
            (root / "out-p1" / "summary.json").read_bytes()
            == (root / "out-p1b" / "summary.json").read_bytes())

        prompts = {p["idx"]: p for p in res1["prompts"]}
        summ = res1["summary"]
        arms = summ["arms"]
        silenced, inert = arms[f"B@{MARKER}"], arms[f"B@{NO_MARKER}"]

        # 3. a variant that matches nothing changes nothing: the hook itself
        #    is transparent, so any diff below is the variant, not the rig.
        ok &= _check(
            "inert arm (variant matches nothing): B == A",
            inert["diff_prompts"] == 0
            and inert["b_injections"] == inert["a_injections"]
            and inert["a_only"] == 0 and inert["b_only"] == 0,
            f"a={inert['a_injections']} b={inert['b_injections']}")

        # 4. the silencing path: B drops rows, and the drop is visible in
        #    the aggregates and in the per-prompt arms.
        p1, p2, p3 = prompts[1], prompts[2], prompts[3]
        ok &= _check(
            "silencing arm: marked session is A-only, B empty for its prompt",
            any(i["session_id"] == "S-rare" for i in p2["arms"]["A"])
            and p2["arms"][f"B@{MARKER}"] == [],
            f"A={len(p2['arms']['A'])}")
        ok &= _check(
            "silencing arm: unmarked generic prompt is untouched",
            len(p1["arms"]["A"]) >= 1
            and p1["arms"][f"B@{MARKER}"] == p1["arms"]["A"],
            f"A={len(p1['arms']['A'])}")
        ok &= _check(
            "silencing arm: b_injections < a_injections, a_only > 0",
            silenced["b_injections"] < silenced["a_injections"]
            and silenced["a_only"] > 0 and silenced["b_only"] == 0,
            f"a={silenced['a_injections']} b={silenced['b_injections']} "
            f"a_only={silenced['a_only']}")

        # diff + judging artifacts are actually emitted for that disagreement.
        diffs = [json.loads(ln) for ln in
                 (root / "out1" / "diffs.jsonl").read_text().splitlines() if ln]
        ok &= _check(
            "diffs.jsonl emitted with per-arm a_only/b_only/common",
            len(diffs) >= 1
            and set(diffs[0]["arms"]) == {f"B@{MARKER}", f"B@{NO_MARKER}"}
            and len(diffs[0]["arms"][f"B@{MARKER}"]["a_only"]) >= 1,
            f"diff_prompts={len(diffs)}")
        judged = [ln for name in ("judging.jsonl", "judging-holdout.jsonl")
                  for ln in (root / "out1" / name).read_text().splitlines()
                  if ln]
        keyed = [ln for ln in
                 (root / "out1" / "key.jsonl").read_text().splitlines() if ln]
        ok &= _check(
            "judging units emitted, one key row each",
            len(judged) >= 1 and len(keyed) == len(judged),
            f"judging={len(judged)} key={len(keyed)}")

        # 5. blind-file hygiene: no arm vocabulary in what the judge reads.
        blind = ((root / "out1" / "judging.jsonl").read_text()
                 + (root / "out1" / "judging-holdout.jsonl").read_text())
        ok &= _check(
            "judging files carry no arm labels",
            "a-only" not in blind and "b-only" not in blind
            and '"arms"' not in blind and "B@" not in blind)

        # 6. seen-state cooldown across two prompts of one session.
        ok &= _check(
            "cooldown: prompt 2 of sess-rare re-injects nothing in arm A",
            len(p2["arms"]["A"]) >= 1 and p3["arms"]["A"] == [],
            f"first={len(p2['arms']['A'])} second={len(p3['arms']['A'])}")

        # 7. snapshot boundary + class walking.
        p0 = prompts[0]
        ok &= _check(
            "snapshot: pre-creation prompt cannot see S-rare",
            p0["arms"]["A"] == [])
        ok &= _check(
            "snapshot: >= 2 equivalence classes walked",
            summ["corpus"]["n_snapshot_classes"] >= 2,
            f"classes={summ['corpus']['n_snapshot_classes']}")

        # 8. machine prompts are skipped, never replayed.
        ok &= _check(
            "machine prompt skipped",
            summ["corpus"]["n_machine_skipped"] == 1
            and prompts[4]["machine"])

        # 9. classify_stance's own doctests (variants.py) — the classifier's
        #    worked examples must pass as executable claims, not just prose.
        dt = doctest.testmod(variants, verbose=False)
        ok &= _check(
            "classify_stance: doctests pass",
            dt.attempted > 0 and dt.failed == 0,
            f"attempted={dt.attempted} failed={dt.failed}")

        # 10. classify_stance edge cases beyond the doctests: case
        #     insensitivity and the "first ~2 sentences only" opener/phrase
        #     boundary (the trailing '?' check alone is whole-prompt).
        ok &= _check(
            "classify_stance: case-insensitive opener",
            variants.classify_stance("HOW DO I FIX THIS") == "question")
        ok &= _check(
            "classify_stance: opener beyond first 2 sentences does not "
            "count",
            variants.classify_stance(
                "Shipped the fix. Tests are green. How about that.")
            == "statement")
        ok &= _check(
            "classify_stance: phrase marker beyond first 2 sentences does "
            "not count",
            variants.classify_stance(
                "Shipped the fix. Tests are green. Still trying to land "
                "the follow-up.") == "statement")

        # 11. stance_gate variant (#483), exercised end to end through the
        #     real harness on a synthetic fixture — same pattern as the
        #     marker_silencer checks above, for the real hypothesis instead
        #     of a fixture-only stand-in.
        print("verify: stance-gate fixture built, running replay ...")
        stance_home, stance_dataset = build_stance_fixture(root)
        res_stance = replay_ab.run(
            stance_dataset, stance_home, [None], root / "out-stance",
            seed=470, variant=variants.stance_gate, variant_name="stance_gate")
        sp = {p["idx"]: p for p in res_stance["prompts"]}
        pq, ps = sp[0], sp[1]
        ok &= _check(
            "stance_gate fixture: prompts classify as intended",
            variants.classify_stance(pq["prompt"]) == "question"
            and variants.classify_stance(ps["prompt"]) == "statement")
        ok &= _check(
            "stance_gate: question-shaped prompt passes through, B == A",
            len(pq["arms"]["A"]) >= 1 and pq["arms"]["B"] == pq["arms"]["A"],
            f"A={len(pq['arms']['A'])} B={len(pq['arms']['B'])}")
        ok &= _check(
            "stance_gate: statement-shaped prompt fully suppressed, "
            "B == []",
            len(ps["arms"]["A"]) >= 1 and ps["arms"]["B"] == [],
            f"A={len(ps['arms']['A'])} B={len(ps['arms']['B'])}")
        stance_agg = res_stance["summary"]["arms"]["B"]
        ok &= _check(
            "stance_gate: aggregate a_only > 0 (the suppression), "
            "b_only == 0 (gate never adds a row suggest() didn't return)",
            stance_agg["a_only"] > 0 and stance_agg["b_only"] == 0,
            f"a_only={stance_agg['a_only']} b_only={stance_agg['b_only']}")

    print("VERIFY " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
