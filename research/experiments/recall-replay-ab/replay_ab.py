"""#470 stage-1 replay A/B harness: IDF mass gate OFF (arm A) vs ON (arm B).

Deterministic offline replay of historical prompts through recall.suggest()
plus a faithful replica of cli._cmd_recall_inject's downstream post-filter
(briefing-session exclusion, per-session seen-file cooldown, #451 content-key
dedup, #452 age gate), so both arms approximate the user-felt injection
surface. B is compared against A ONLY — identical harness both arms, no
external baseline. Protocol: see README.md (pre-registered) and issue #470.

Snapshot semantics: per prompt, only checkpoints WRITTEN at-or-before the
prompt's timestamp participate (store._file_recency: `created` stamp, mtime
fallback). Frozen `now` = the prompt's ts, current_session = the prompt's own
historical session id. Prompts are replayed in global ts order; files qualify
monotonically, so snapshot equivalence classes are contiguous and the index
is rebuilt only when the class changes.

Research code: lives outside the plugin suite on purpose; correctness claims
are executable via `--verify` (see verify.py).
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import cli, config, normalize, recall, store, teamproject

_DEFAULT_SWEEP = "6,8,10,12"
_ORIG_MASS = recall._IDF_MIN_MASS

# Env keys the harness pins (snapshot world) or clears (read-behavior knobs).
_PINNED_KEYS = (
    "DAIMON_ENV_FILE", "DAIMON_CHECKPOINT_DIR", "DAIMON_TEAM_DIR",
    "DAIMON_RECALL_DB", "DAIMON_LOG_DIR", "DAIMON_RECALL_SEEN_DIR",
    "DAIMON_AUTHOR",
)
_CLEARED_KEYS = (
    "DAIMON_TEAM", "DAIMON_TEAM_PROJECT", "DAIMON_TEAM_RETENTION_DAYS",
    "DAIMON_DISABLE", "DAIMON_RECALL_IDF_GATE",
)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(v) -> float:
    """Epoch (number / numeric string) or ISO-8601 ('Z' ok, naive = UTC)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        pass
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _load_json(path: Path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _load_dataset(path: Path, default_project) -> list[dict]:
    """JSONL rows -> [{idx, prompt, ts, session, project}], sorted by ts.
    `idx` is the row's position in the FILE (stable across runs — ids and
    diffs reference it)."""
    rows = []
    with path.open(encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            rows.append({
                "idx": idx,
                "prompt": str(obj["prompt"]),
                "ts": _parse_ts(obj["ts"]),
                "session": str(obj.get("session") or ""),
                "project": (obj.get("project") or obj.get("project_dir")
                            or obj.get("cwd") or default_project),
            })
    rows.sort(key=lambda r: (r["ts"], r["session"], r["idx"]))
    return rows


@contextmanager
def _pinned_env(base: Path, author: str):
    """Point every daimon read path at `base` (snapshot world) and neutralize
    host env-file/env leaks. config._get is uncached (checked: env wins per
    call, env-file re-read per call), but DAIMON_ENV_FILE must point at a
    nonexistent file or ~/.daimon/env values leak in as fallbacks. The
    teamproject resolver DOES cache per (project_dir, team_dir, env) — cleared
    on entry and exit, same as the plugin test conftest."""
    saved = {k: os.environ.get(k) for k in _PINNED_KEYS + _CLEARED_KEYS}
    try:
        os.environ["DAIMON_ENV_FILE"] = str(base / "no-such-env-file")
        os.environ["DAIMON_CHECKPOINT_DIR"] = str(base / "checkpoints")
        os.environ["DAIMON_TEAM_DIR"] = str(base / "team")
        os.environ["DAIMON_RECALL_DB"] = str(base / "recall.db")
        os.environ["DAIMON_LOG_DIR"] = str(base / "logs")
        os.environ["DAIMON_RECALL_SEEN_DIR"] = str(base / "recall_seen")
        os.environ["DAIMON_AUTHOR"] = author
        for k in _CLEARED_KEYS:
            os.environ.pop(k, None)
        teamproject._cache.clear()
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        teamproject._cache.clear()
        recall._IDF_MIN_MASS = _ORIG_MASS


def _set_arm(threshold) -> None:
    """Arm toggle. threshold None = arm A (gate off). For arm B the sweep
    value is set by assigning recall._IDF_MIN_MASS directly — a
    research-script monkeypatch, acceptable HERE only: the constant is
    module-level by design and its value is owned by this pre-registered
    sweep (#470)."""
    if threshold is None:
        os.environ.pop("DAIMON_RECALL_IDF_GATE", None)
        recall._IDF_MIN_MASS = _ORIG_MASS
    else:
        os.environ["DAIMON_RECALL_IDF_GATE"] = "1"
        recall._IDF_MIN_MASS = float(threshold)


class _Snapshot:
    """Filtered view of the source flat store: checkpoints written
    at-or-before the current prompt's ts, copied (read-only on the source)
    into the pinned DAIMON_CHECKPOINT_DIR. Pointer files are SYNTHESIZED per
    class from the qualifying set — the source's real pointers reflect
    today's rotation, not the prompt-time state cli's briefing exclusion
    (store.read_latest project + global) would have seen.

    Deliberate deviation: each project bucket's events.jsonl is copied WHOLE
    (today's full ledger), not time-filtered. Forget tombstones are removal —
    replaying a pre-forget prompt must not resurface forgotten content into
    the output files. Privacy-safe direction, identical in both arms."""

    def __init__(self, source_dir: Path, snap_dir: Path):
        self.source = source_dir
        self.snap = snap_dir
        self.snap.mkdir(parents=True, exist_ok=True)
        self.entries = []  # (recency, name, path, slug) sorted by recency
        for p in sorted(source_dir.iterdir()):
            if not (p.is_file() and p.suffix == ".json"):
                continue
            if store._POINTER_RE.match(p.name):
                continue
            cp = _load_json(p)
            slug = cp.get("project_slug") if cp else None
            self.entries.append((store._file_recency(p), p.name, p, slug))
        self.entries.sort(key=lambda e: (e[0], e[1]))
        self.i = 0
        self.slugs: set[str] = set()
        self.classes = 0

    def advance(self, ts: float) -> bool:
        """Admit files with recency <= ts. True when the class changed
        (caller rebuilds the index)."""
        changed = False
        while self.i < len(self.entries) and self.entries[self.i][0] <= ts:
            _rec, name, path, slug = self.entries[self.i]
            shutil.copy2(path, self.snap / name)
            if slug and slug not in self.slugs:
                self.slugs.add(slug)
                events = self.source / slug / "events.jsonl"
                if events.is_file():
                    bucket = self.snap / slug
                    bucket.mkdir(exist_ok=True)
                    shutil.copy2(events, bucket / "events.jsonl")
            self.i += 1
            changed = True
        if changed:
            self._write_pointers()
            self.classes += 1
        return changed

    def _write_pointers(self) -> None:
        qualified = self.entries[:self.i]
        best = max(qualified, key=lambda e: (e[0], e[1]))
        shutil.copy2(best[2], self.snap / "latest.json")
        by_slug: dict[str, tuple] = {}
        for e in qualified:
            slug = e[3]
            if slug and (slug not in by_slug
                         or (e[0], e[1]) > (by_slug[slug][0], by_slug[slug][1])):
                by_slug[slug] = e
        for slug, e in by_slug.items():
            bucket = self.snap / slug
            bucket.mkdir(exist_ok=True)
            shutil.copy2(e[2], bucket / "latest.json")


def _session_seen_ok(session: str) -> bool:
    # Mirror of cli._seen_path's validity rule: an unusable id means cli never
    # persists cooldown state for the session (every prompt starts cold).
    return bool(session) and "/" not in session and "\\" not in session \
        and ".." not in session


def _postfilter(matches, seen_keys, now):
    """Replica of cli._cmd_recall_inject's chosen-loop (cli.py ~1194-1238):
    #451 content-key dedup (within the injection AND across the session),
    #452 age gate with open-question/pinned exemptions, _INJECT_BUDGET slots,
    a suppressed/gated candidate yields its slot to the next distinct one."""
    chosen, chosen_keys = [], set()
    suppressed = age_gated = False
    for m in matches:
        key = normalize.content_key(m.get("text") or "")
        if key in seen_keys or key in chosen_keys:
            suppressed = True
            continue
        epoch = store._created_epoch(m.get("first_seen"))
        age_days = ((now - epoch) / 86400.0
                    if epoch is not None and epoch <= now else None)
        hits = m.get("term_hits")
        exempt = ((m.get("kind") == "question"
                   and not m.get("superseded_by"))
                  or bool(m.get("pinned")))
        if (age_days is not None and age_days > cli._AGE_GATE_DAYS
                and not exempt
                and isinstance(hits, int) and hits < cli._STALE_MIN_HITS):
            age_gated = True
            continue
        chosen_keys.add(key)
        chosen.append(m)
        if len(chosen) >= cli._INJECT_BUDGET:
            break
    return chosen, chosen_keys, suppressed, age_gated


def _stratum(n_terms: int) -> str:
    if n_terms < 2:
        return "<2"
    return "2-3" if n_terms <= 3 else "4+"


def run(dataset_path, daimon_home, sweep_tokens, out_dir,
        default_project=None, seed=470, holdout_min=20) -> dict:
    """Replay the dataset, write diffs/judging/key/summary into out_dir,
    return the in-memory results (used by verify.py's assertions)."""
    t0 = time.time()
    dataset_path = Path(dataset_path).expanduser()
    source = Path(daimon_home).expanduser() / "checkpoints"
    if not source.is_dir():
        sys.exit(f"replay-ab: no checkpoint store at {source}")
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    rows = _load_dataset(dataset_path, default_project)
    # Resolve the author identity BEFORE pinning env: legacy author-less
    # checkpoints fall back to config.author() during the index scan, and the
    # historical scans ran under the maintainer's real identity.
    author = config.author()

    variants = [("A", None)] + [(f"B@{t}", float(t)) for t in sweep_tokens]
    prompts: list[dict] = []
    counters = {"n_prompts": len(rows), "n_replayed": 0,
                "n_machine_skipped": 0, "n_below_min_terms": 0,
                "n_no_project": 0}
    with tempfile.TemporaryDirectory(prefix="daimon-replay-ab-") as wd:
        base = Path(wd)
        (base / "team").mkdir()
        with _pinned_env(base, author):
            snap = _Snapshot(source, base / "checkpoints")
            _set_arm(None)
            recall.rebuild()  # empty-class baseline so the db always exists
            n_rebuilds = 1
            # Per (variant, session) cooldown state — arms diverge in what
            # they inject, so each variant owns its own seen accumulation.
            seen_state = {label: {} for label, _ in variants}
            for row in rows:
                if snap.advance(row["ts"]):
                    _set_arm(None)
                    recall.rebuild()
                    n_rebuilds += 1
                if recall.is_machine_prompt(row["prompt"]):
                    counters["n_machine_skipped"] += 1
                    prompts.append({**row, "machine": True, "arms": {}})
                    continue
                counters["n_replayed"] += 1
                terms = recall.salient_terms(row["prompt"])
                if len(terms) < 2:
                    counters["n_below_min_terms"] += 1
                if store.project_slug(row["project"]) is None:
                    counters["n_no_project"] += 1
                # Briefing-session exclusion, from the SNAPSHOT's synthesized
                # pointers — what read_latest would have returned at prompt ts.
                exclude = set()
                for cp in (store.read_latest(row["project"]),
                           store.read_latest()):
                    sid = (cp or {}).get("session_id")
                    if sid:
                        exclude.add(str(sid))
                seen_ok = _session_seen_ok(row["session"])
                arms = {}
                flags = {}
                for label, threshold in variants:  # fixed order: A then B
                    _set_arm(threshold)
                    state = (seen_state[label].setdefault(
                        row["session"], {"origins": set(), "keys": set()})
                        if seen_ok else {"origins": set(), "keys": set()})
                    matches = recall.suggest(
                        row["prompt"], project_dir=row["project"],
                        current_session=row["session"],
                        exclude_sessions=exclude | state["origins"],
                        limit=cli._INJECT_FETCH, now=row["ts"])
                    chosen, chosen_keys, suppressed, age_gated = _postfilter(
                        matches, state["keys"], row["ts"])
                    if seen_ok and chosen:  # cli saves only when it injected
                        state["origins"] |= {str(m["session_id"])
                                             for m in chosen}
                        state["keys"] |= chosen_keys
                    arms[label] = [{
                        "session_id": str(m["session_id"]),
                        "content_key": normalize.content_key(
                            m.get("text") or ""),
                        "kind": m.get("kind"),
                        "line": cli._suggest_line(m, terms, row["ts"]),
                    } for m in chosen]
                    flags[label] = {"dedup": suppressed, "age_gate": age_gated}
                prompts.append({**row, "machine": False,
                                "stratum": _stratum(len(terms)),
                                "arms": arms, "flags": flags})
            _set_arm(None)

    results = _build_outputs(prompts, list(sweep_tokens), counters, snap,
                             n_rebuilds, seed, holdout_min, out)
    runtime = time.time() - t0
    # Runtime lives OUTSIDE summary.json so the determinism check (--verify)
    # can byte-compare every analytical artifact.
    (out / "run-meta.json").write_text(json.dumps({
        "runtime_seconds": round(runtime, 3),
        "started": _iso(t0), "finished": _iso(t0 + runtime),
        "dataset": str(dataset_path), "daimon_home": str(Path(daimon_home)),
        "n_rebuilds": n_rebuilds,
    }, indent=2) + "\n", encoding="utf-8")
    results["prompts"] = prompts
    results["runtime_seconds"] = runtime
    return results


def _inj_ids(injs) -> set:
    return {(i["session_id"], i["content_key"]) for i in injs}


def _build_outputs(prompts, sweep_tokens, counters, snap, n_rebuilds,
                   seed, holdout_min, out: Path) -> dict:
    replayed = [p for p in prompts if not p["machine"]]
    # ---- per-threshold aggregates + diff detection ----
    thresholds = {}
    diff_rows = []
    for p in replayed:
        a = _inj_ids(p["arms"]["A"])
        p["_diff"] = any(_inj_ids(p["arms"][f"B@{t}"]) != a
                         for t in sweep_tokens)
        if p["_diff"]:
            diff_rows.append(p)
    for t in sweep_tokens:
        label = f"B@{t}"
        agg = {"a_injections": 0, "b_injections": 0, "diff_prompts": 0,
               "a_only": 0, "b_only": 0,
               "a_dedup_prompts": 0, "b_dedup_prompts": 0,
               "a_agegate_prompts": 0, "b_agegate_prompts": 0,
               "strata": {}}
        for p in replayed:
            a, b = _inj_ids(p["arms"]["A"]), _inj_ids(p["arms"][label])
            s = agg["strata"].setdefault(
                p["stratum"], {"a_injections": 0, "b_injections": 0,
                               "diff_prompts": 0})
            agg["a_injections"] += len(p["arms"]["A"])
            agg["b_injections"] += len(p["arms"][label])
            s["a_injections"] += len(p["arms"]["A"])
            s["b_injections"] += len(p["arms"][label])
            agg["a_only"] += len(a - b)
            agg["b_only"] += len(b - a)
            if a != b:
                agg["diff_prompts"] += 1
                s["diff_prompts"] += 1
            agg["a_dedup_prompts"] += int(p["flags"]["A"]["dedup"])
            agg["b_dedup_prompts"] += int(p["flags"][label]["dedup"])
            agg["a_agegate_prompts"] += int(p["flags"]["A"]["age_gate"])
            agg["b_agegate_prompts"] += int(p["flags"][label]["age_gate"])
        thresholds[t] = agg

    # ---- diffs.jsonl (private: carries prompt text) ----
    diff_idxs = sorted(p["idx"] for p in diff_rows)
    with (out / "diffs.jsonl").open("w", encoding="utf-8") as f:
        for p in sorted(diff_rows, key=lambda r: r["idx"]):
            a_map = {(i["session_id"], i["content_key"]): i
                     for i in p["arms"]["A"]}
            per_t = {}
            for t in sweep_tokens:
                b_map = {(i["session_id"], i["content_key"]): i
                         for i in p["arms"][f"B@{t}"]}
                per_t[t] = {
                    "a_only": [a_map[k] for k in sorted(a_map.keys() - b_map.keys())],
                    "b_only": [b_map[k] for k in sorted(b_map.keys() - a_map.keys())],
                    "common": [a_map[k] for k in sorted(a_map.keys() & b_map.keys())],
                }
            f.write(json.dumps({
                "idx": p["idx"], "prompt": p["prompt"], "ts": p["ts"],
                "session": p["session"], "stratum": p["stratum"],
                "thresholds": per_t}) + "\n")

    # ---- judging.jsonl / judging-holdout.jsonl / key.jsonl ----
    # One unit per (diff prompt, injection) over the union of all arms.
    # Side-blind: the judge sees prompt + injection line only; the arm
    # mapping lives in key.jsonl, which the judge never opens.
    holdout_rng = random.Random(f"holdout|{seed}")
    holdout = (set(holdout_rng.sample(diff_idxs, len(diff_idxs) // 2))
               if len(diff_idxs) >= holdout_min else set())
    units = []
    for p in sorted(diff_rows, key=lambda r: r["idx"]):
        by_id = {}
        for label in ["A"] + [f"B@{t}" for t in sweep_tokens]:
            for i in p["arms"][label]:
                by_id.setdefault((i["session_id"], i["content_key"]), i)
        a = _inj_ids(p["arms"]["A"])
        for k in sorted(by_id):
            uid = hashlib.sha256(
                f"{seed}|{p['idx']}|{k[0]}|{k[1]}".encode()).hexdigest()[:12]
            arms_of = {}
            for t in sweep_tokens:
                b = _inj_ids(p["arms"][f"B@{t}"])
                arms_of[t] = ("common" if k in a and k in b
                              else "a-only" if k in a
                              else "b-only" if k in b else "absent")
            units.append({
                "id": uid, "idx": p["idx"], "prompt": p["prompt"],
                "ts": p["ts"], "session": p["session"],
                "session_id": k[0], "content_key": k[1],
                "line": by_id[k]["line"], "arms": arms_of,
                "holdout": p["idx"] in holdout,
            })
    shuffle_rng = random.Random(f"judging|{seed}")
    order = sorted(units, key=lambda u: u["id"])
    shuffle_rng.shuffle(order)
    with (out / "judging.jsonl").open("w", encoding="utf-8") as fj, \
            (out / "judging-holdout.jsonl").open("w", encoding="utf-8") as fh:
        for u in order:
            blind = {"id": u["id"], "prompt": u["prompt"],
                     "ts": _iso(u["ts"]), "injection": u["line"]}
            (fh if u["holdout"] else fj).write(json.dumps(blind) + "\n")
    with (out / "key.jsonl").open("w", encoding="utf-8") as f:
        for u in sorted(units, key=lambda u: (u["idx"], u["session_id"],
                                              u["content_key"])):
            f.write(json.dumps({
                "id": u["id"], "prompt_idx": u["idx"],
                "session": u["session"], "session_id": u["session_id"],
                "content_key": u["content_key"], "holdout": u["holdout"],
                "arms": u["arms"]}) + "\n")

    # ---- summary.json (aggregates only — no prompt text, safe to share) ----
    summary = {
        "issue": 470,
        "corpus": {
            **counters,
            "n_source_files": len(snap.entries),
            "n_snapshot_classes": snap.classes,
            "n_rebuilds": n_rebuilds,
            "sweep": sweep_tokens,
            "seed": seed,
            "n_diff_prompts_total": len(diff_idxs),
            "holdout": {"applied": bool(holdout), "min": holdout_min,
                        "n_holdout": len(holdout)},
        },
        "thresholds": thresholds,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return {"summary": summary, "diff_idxs": diff_idxs, "units": units}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", help="JSONL: prompt, ts, session"
                    " (+optional project) per row")
    ap.add_argument("--daimon-home", default="~/.daimon",
                    help="flat checkpoint store to snapshot from (READ-ONLY)")
    ap.add_argument("--sweep", default=_DEFAULT_SWEEP,
                    help="comma-separated _IDF_MIN_MASS values for arm B")
    ap.add_argument("--out", help="output directory (PRIVATE except summary.json)")
    ap.add_argument("--project", default=None,
                    help="default project dir for rows without one")
    ap.add_argument("--seed", type=int, default=470)
    ap.add_argument("--holdout-min", type=int, default=20,
                    help="min diff prompts before a seeded half is held out")
    ap.add_argument("--verify", action="store_true",
                    help="run the synthetic-fixture integrity self-check")
    args = ap.parse_args(argv)
    if args.verify:
        import verify
        return verify.main()
    if not args.dataset or not args.out:
        ap.error("--dataset and --out are required (or use --verify)")
    tokens = [t.strip() for t in args.sweep.split(",") if t.strip()]
    for t in tokens:
        float(t)  # fail fast on a bad sweep value
    res = run(args.dataset, args.daimon_home, tokens, args.out,
              default_project=args.project, seed=args.seed,
              holdout_min=args.holdout_min)
    c = res["summary"]["corpus"]
    print(f"replayed {c['n_replayed']}/{c['n_prompts']} prompts "
          f"({c['n_machine_skipped']} machine-skipped), "
          f"{c['n_snapshot_classes']} snapshot classes, "
          f"{c['n_diff_prompts_total']} diff prompts, "
          f"{res['runtime_seconds']:.1f}s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
