"""Quote-span cost replay (#1129): what capping the evidence quote costs, or
saves, the briefing's budget.

Before #1129 a verbatim item's evidence quote rode the brief line whole, so
the quote was part of what the item cost the #1128 byte budget. `quote_span`
caps it at 120 characters and hides it when the item's text already says it.
That is scar 0095's mechanism with a second input: the quote cap changes what
verbatim items cost, which changes which items survive `select`, and no
ordering test sees it. This runner measures the size of that effect.

  arm A  the quote rendered whole (the pre-#1129 line), by pointing
         `briefing.item_quote` at the stored quote
  arm B  the shipped rule: `display.quote_span`

Both arms run `prepare` + `build` + `select` at the default budget over the
same checkpoint copy, at each horizon after the checkpoint's own `created`.
Reported: checkpoints scanned, briefings whose kept set changed, items gained
and lost by trust class (B relative to A), and the bytes the cap saves per
briefing (the unbounded render, so the saving is not hidden by the budget).

Copy-on-read: the store is copied into a scratch dir first and every DAIMON_*
variable is redirected there; nothing is written under the real store.

Privacy: only counts are recorded. Checkpoints are named by a sha256 prefix of
their store-relative path, items by a sha256 prefix of id or text.

Run from the repo root:

  cd plugin && uv run python ../research/experiments/quote-span-1129/replay.py

`DAIMON_LOCAL_STORE` overrides the store (~/.daimon/checkpoints); the live
store moves under a running session, so `measurements.json` carries an
`inputs` fingerprint of exactly what was read.
"""

import contextlib
import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO = _HERE.parents[3]
sys.path.insert(0, str(REPO / "plugin"))

from daimon_briefing import briefing, schema, store, view  # noqa: E402

_LOCAL_RAW = os.environ.get("DAIMON_LOCAL_STORE")
LOCAL_STORE = (Path(_LOCAL_RAW).expanduser() if _LOCAL_RAW is not None
               else Path.home() / ".daimon" / "checkpoints")
OUT = _HERE.parent / "measurements.json"
DAY = 86400.0
HORIZONS = (1, 30)
KEY = "_replay_key"


def item_key(item: dict) -> str:
    stamped = item.get("id")
    domain, raw = (("id", str(stamped)) if stamped
                   else ("text", str(item.get("text") or "")))
    return hashlib.sha256(f"{domain}\0{raw}".encode()).hexdigest()[:16]


def whole_quote(item, text, full_quotes=False):
    """Arm A: the pre-#1129 rule, the stored quote on the line unchanged."""
    return str(item.get("quote") or "").strip()


def trust_class(item: dict) -> str:
    t = item.get("trust")
    return t if t in ("verbatim", "inferred") else "other"


def diff_kept(kept_a: dict, kept_b: dict) -> dict:
    """Items B gained and lost against A, by trust class. Each side maps an
    item key to its trust class."""
    gained = {k: v for k, v in kept_b.items() if k not in kept_a}
    lost = {k: v for k, v in kept_a.items() if k not in kept_b}
    out = {"gained": {}, "lost": {}}
    for name, side in (("gained", gained), ("lost", lost)):
        for cls in side.values():
            out[name][cls] = out[name].get(cls, 0) + 1
    return out


def summarize(values: list) -> dict:
    if not values:
        return {"n": 0, "median": 0, "max": 0}
    return {"n": len(values), "median": statistics.median(values),
            "max": max(values)}


def add_counts(total: dict, part: dict) -> None:
    for cls, n in part.items():
        total[cls] = total.get(cls, 0) + n


class _Env(contextlib.AbstractContextManager):
    """Redirect EVERY DAIMON_* variable, restoring on exit."""

    def __init__(self, mapping):
        self.mapping, self.saved = mapping, {}

    def __enter__(self):
        self.saved = {k: v for k, v in os.environ.items()
                      if k.startswith("DAIMON_")}
        for k in self.saved:
            os.environ.pop(k)
        os.environ.update(self.mapping)
        return self

    def __exit__(self, *exc):
        for k in [k for k in os.environ if k.startswith("DAIMON_")]:
            os.environ.pop(k)
        os.environ.update(self.saved)


def _env_for(home: Path, project_dir: Path) -> dict:
    return {
        "DAIMON_CHECKPOINT_DIR": str(home / "checkpoints"),
        "DAIMON_RECALL_DB": str(home / "recall.db"),
        "DAIMON_LOG_DIR": str(home / "logs"),
        "DAIMON_RECALL_SEEN_DIR": str(home / "recall_seen"),
        "DAIMON_TEAM_DIR": str(home / "team"),
        "DAIMON_PROJECT_DIR": str(project_dir),
        "DAIMON_ENV_FILE": str(home / "no-such-env-file"),
        "DAIMON_CARRY": "0",
        "DAIMON_TEAM": "0",
        "DAIMON_RECEIPTS": "0",
        "DAIMON_SCAR_HARVEST": "0",
        "DAIMON_SCENE_TRACES": "0",
    }


def checkpoint_files(root: Path) -> list:
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def fingerprint(root: Path) -> dict:
    digest = hashlib.sha256()
    files = checkpoint_files(root)
    for p in files:
        digest.update(str(p.relative_to(root)).encode() + b"\0")
        digest.update(hashlib.sha256(p.read_bytes()).hexdigest().encode())
    return {"files": len(files), "sha256": digest.hexdigest()}


def load(path: Path):
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cp if isinstance(cp, dict) else None


def stamp_keys(checkpoint: dict) -> None:
    for field in schema.ITEM_FIELDS:
        block = checkpoint.get(field.section)
        if not isinstance(block, dict):
            continue
        value = block.get(field.key)
        for item in ((value,) if field.singleton else (value or [])):
            if isinstance(item, dict):
                item[KEY] = item_key(item)


def kept_map(sel) -> dict:
    return {i.get(KEY) or item_key(i): trust_class(i)
            for s in briefing._ITEM_SECTIONS for i in sel.kept.get(s, [])}


def quote_stats(b: dict) -> dict:
    """How many items carry a quote, and what the rule does to them."""
    from daimon_briefing import display
    n = hidden = capped = 0
    for s in briefing._ITEM_SECTIONS:
        for i in b.get(s) or []:
            if not isinstance(i, dict) or not str(i.get("quote") or "").strip():
                continue
            n += 1
            span = display.quote_span(i.get("quote"), i.get("text"))
            if not span:
                hidden += 1
            elif span.endswith("…"):
                capped += 1
    return {"quoted": n, "hidden": hidden, "capped": capped}


def run_arm(b: dict, now: float, whole: bool):
    original = briefing.item_quote
    if whole:
        briefing.item_quote = whole_quote
    try:
        budget = briefing.effective_budget()
        sel = briefing.select(b, budget, now)
        unbounded = len(briefing.render_selection(
            briefing.select(b, None, now)).encode())
    finally:
        briefing.item_quote = original
    return sel, unbounded


def measure_one(cp: dict, label: str, route: str) -> list:
    created = store._created_epoch(cp.get("created"))
    if created is None:
        return [{"checkpoint": label, "skipped": "no created stamp"}]
    stamp_keys(cp)
    rows = []
    for horizon in HORIZONS:
        now = created + horizon * DAY
        held = json.loads(json.dumps(cp))
        out = briefing.prepare(
            route, now, opened=view._opened(held, view.snapshot(route), True))
        b = briefing.build(out.checkpoint, now=now)
        if b is None:
            rows.append({"checkpoint": label, "horizon_days": horizon,
                         "empty": True})
            continue
        sel_a, bytes_a = run_arm(b, now, whole=True)
        sel_b, bytes_b = run_arm(b, now, whole=False)
        diff = diff_kept(kept_map(sel_a), kept_map(sel_b))
        rows.append({
            "checkpoint": label, "horizon_days": horizon,
            "changed": bool(diff["gained"] or diff["lost"]),
            "gained": diff["gained"], "lost": diff["lost"],
            "bytes_saved_unbounded": bytes_a - bytes_b,
            "over_budget_a": bool(sel_a.dropped_for("decisions", "budget")
                                  or any(sel_a.dropped_for(s, "budget")
                                         for s in briefing._ITEM_SECTIONS)),
            **quote_stats(b),
        })
    return rows


def aggregate(rows: list) -> dict:
    measured = [r for r in rows if "bytes_saved_unbounded" in r]
    out = {"rows": len(rows), "briefings_measured": len(measured),
           "skipped": sum(1 for r in rows if "skipped" in r),
           "empty": sum(1 for r in rows if r.get("empty"))}
    out["briefings_changed"] = sum(1 for r in measured if r["changed"])
    out["briefings_over_budget_whole"] = sum(
        1 for r in measured if r["over_budget_a"])
    gained: dict = {}
    lost: dict = {}
    for r in measured:
        add_counts(gained, r["gained"])
        add_counts(lost, r["lost"])
    out["items_gained_by_class"] = gained
    out["items_lost_by_class"] = lost
    out["bytes_saved_unbounded"] = summarize(
        [r["bytes_saved_unbounded"] for r in measured])
    out["bytes_saved_unbounded_when_quoted"] = summarize(
        [r["bytes_saved_unbounded"] for r in measured if r["quoted"]])
    out["briefings_changed_by_horizon"] = {
        str(h): sum(1 for r in measured
                    if r["horizon_days"] == h and r["changed"])
        for h in HORIZONS}
    for key in ("quoted", "hidden", "capped"):
        out[f"items_{key}"] = sum(r[key] for r in measured)
    return out


def main() -> int:
    if not LOCAL_STORE.is_dir():
        print(f"no store at {LOCAL_STORE}", file=sys.stderr)
        return 1
    inputs = fingerprint(LOCAL_STORE)
    rows = []
    scanned = 0
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        copy = scratch / "snapshot"
        shutil.copytree(LOCAL_STORE, copy)
        project = scratch / "project"
        project.mkdir()
        with _Env(_env_for(scratch, project)):
            for path in checkpoint_files(copy):
                cp = load(path)
                if cp is None:
                    continue
                scanned += 1
                label = hashlib.sha256(
                    str(path.relative_to(copy)).encode()).hexdigest()[:12]
                rows.extend(measure_one(cp, label, str(project)))
    record = {"issue": 1129, "arms": {"A": "quote whole", "B": "quote_span"},
              "horizons_days": list(HORIZONS), "inputs": inputs,
              "checkpoints_scanned": scanned, **aggregate(rows)}
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
