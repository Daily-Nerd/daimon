"""Trust-gate cost replay (#976): what the #408 lid costs `suggest` and the
briefing's section order.

#754 measured the trust gate on ONE surface (`recall.search`) and found it
free: search does not rank on trust, so reverting every downgrade left the
rankings byte-identical. That result says nothing about the two surfaces that
DO rank on trust. Both reach it through `scoring.effective_weight`, which
applies `trust_ceiling` LAST via `_soft_clip`:

  suggest   `recall.suggest` ranks bm25 relevance x `_suggest_weight`, and
            `_suggest_weight` is `effective_weight` plus the interval-slot
            demotions. limit=2 in production; measured here at 2 and 5.
  briefing  `briefing.build` sorts open_questions / strong_beliefs /
            uncertainties by `effective_weight` (`_by_weight`); decisions stay
            chronological. `briefing.render_plain` then drops whole items from
            `_DROP_ORDER` tails until the token budget holds, so a weight
            change can also decide who survives the budget.

The mechanism bounds the answer in advance. `_soft_clip` is the IDENTITY below
its knee K = C * (1 - _SOFT_CLIP_DELTA) and only compresses above it, so
reverting a downgrade (inferred lid 0.7 -> verbatim lid 3.0) changes an item's
weight only when the item's UNCAPPED accumulation already exceeds the inferred
knee, 0.63. Call such a flip LID-BITING. Every other flip is arithmetically
invisible on both surfaces.

Registered predictions (frozen before the run, vault note
`Design - Trust Gate Cost Experiment 2026-09-12`):

  P1 (mechanism): every suggest or briefing difference between arms is
  attributable to a lid-biting flipped item; where the lid-biting count is
  zero, the arms are identical.

  P2 (size): the lid-biting fraction of flipped items is small, single-digit
  percent.

Both were falsified; `measurements.json` carries the numbers. P1 failed on one
briefing comparison whose difference traces to a SECOND channel the
pre-registration did not name: `render_plain` stage 1 shortens oversized items
except verbatim ones (#30), so a downgrade makes an item truncatable and BUYS
budget, and lifting the gate costs a different item its place. See
`is_truncation_exempting`, which the run added and the record reports beside
the lid. P2 failed because the horizons anchor `now` to each checkpoint's own
`created`, which keeps items fresh enough that a large minority clear the knee.

Arms, exactly as in #754:

  A gated    the stores as written
  B ungated  every trust-gate downgrade reverted (`should_flip`, IMPORTED from
             the #754 runner so both experiments share one predicate), trust
             set back to `verbatim`, `recall.db` deleted, index rebuilt

Substrates: the 54 questions of `benchmark/results/interim-317-baseline-first54
.json` over their own `benchmark/.work/<qid>` stores (suggest), and every
checkpoint in those stores plus the local `~/.daimon/checkpoints` store
(briefing). Zero LLM calls; sessions are never re-serialized.

Copy-on-read: the originals are never written. Both arms run against copies in
a scratch dir, under an env where every DAIMON_* variable is redirected there.

Privacy: no item text, quote or prompt text is recorded. Items are identified
by their stamped id, or by a sha256 prefix of their text when a legacy
checkpoint stamped none; local-store checkpoints by a sha256 prefix of their
store-relative path.

Run from the repo root:

  cd plugin && DAIMON_BENCH_ROOT=<checkout holding benchmark/.work> \\
      uv run python ../research/experiments/trust-gate-cost-976/replay.py

`DAIMON_BENCH_ROOT` defaults to this checkout and only needs setting when the
bench substrate (untracked) lives in another worktree. `DAIMON_LOCAL_STORE`
overrides the local checkpoint store; set it empty to skip that arm.

REPRODUCIBILITY: `~/.daimon/checkpoints` is LIVE. Any session running beside
the replay appends checkpoints to it, so two passes over the live store read
two different corpora and their outputs differ for reasons that have nothing
to do with the code. The committed record was produced against a FROZEN copy:

  cp -R ~/.daimon/checkpoints /some/scratch/snapshot
  DAIMON_LOCAL_STORE=/some/scratch/snapshot ... uv run python .../replay.py

`measurements.json` carries an `inputs` block fingerprinting exactly what was
read, so a re-run can tell "the code changed" from "the store moved".
"""

import copy
import hashlib
import importlib.util
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO = _HERE.parents[3]
PLUGIN = REPO / "plugin"
sys.path.insert(0, str(PLUGIN))

from daimon_briefing import briefing, recall, schema, scoring, store  # noqa: E402
from tests.bench import dataset  # noqa: E402


def _load_ungated_arm():
    """The #754 runner, loaded BY PATH under its own module name.

    Not `sys.path.insert` + `import replay`: both experiments' runners are
    named `replay`, so a path import resolves to whichever landed in
    sys.modules first — which, when this module is the one under test, is
    this file, turning "we share the predicate" into a tautology.
    """
    path = _HERE.parent.parent / "ungated-arm" / "replay.py"
    spec = importlib.util.spec_from_file_location("ungated_arm_replay", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging
        raise SystemExit(f"cannot load the #754 runner at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["ungated_arm_replay"] = module
    spec.loader.exec_module(module)
    return module


UNGATED_ARM = _load_ungated_arm()
# ONE predicate across both experiments. #754's docstring carries the argument
# for it: `quote_verified: false` and `grounded: false` are code-owned markers
# no model output carries into a fresh item.
should_flip = UNGATED_ARM.should_flip
flip_downgrades = UNGATED_ARM.flip_downgrades

BENCH_ROOT = Path(os.environ.get("DAIMON_BENCH_ROOT") or REPO)
WORK = BENCH_ROOT / "benchmark" / ".work"
DATA = BENCH_ROOT / "benchmark" / ".data" / dataset.DATASET_FILENAME
REFERENCE = (BENCH_ROOT / "benchmark" / "results"
             / "interim-317-baseline-first54.json")
_LOCAL_RAW = os.environ.get("DAIMON_LOCAL_STORE")
LOCAL_STORE = (Path(_LOCAL_RAW).expanduser() if _LOCAL_RAW is not None
               else Path.home() / ".daimon" / "checkpoints")
OUT = _HERE.parent / "measurements.json"

DAY = 86400.0
SUGGEST_LIMITS = (2, 5)          # production is 2; 5 widens the window
BRIEFING_HORIZONS = (1, 30)      # days after the checkpoint's own `created`
MIN_MESSAGES = 2                 # the reference run's bench floor

# The sections briefing.build sorts by effective_weight. `decisions` is
# deliberately absent: it stays chronological, so trust cannot reorder it —
# it can still lose items to the budget, which the render diff covers.
SORTED_SECTIONS = ("external", "open_loops", "beliefs", "uncertainties")
_SECTION_TYPE = {
    "external": "open_question",
    "open_loops": "open_question",
    "beliefs": "strong_belief",
    "uncertainties": "uncertainty",
    "decisions": "recent_decision",
}
RENDER_SECTIONS = SORTED_SECTIONS + ("decisions",)

# Below this, _soft_clip is the identity for an inferred item, so reverting its
# downgrade cannot move its weight by even a float ulp.
LID_KNEE = scoring.trust_ceiling("inferred") * (1.0 - scoring._SOFT_CLIP_DELTA)

_KEY_FIELD = "_replay_key"   # our identity stamp, carried through render copies


# ---------------------------------------------------------------- pure logic

def item_key(item: dict) -> str:
    """One item's identity in the record: its stamped id, else a sha256 prefix
    of its text. Never the text — the bench corpus is public but the local
    store is not, and one record must be safe to commit whole."""
    stamped = item.get("id")
    if stamped:
        return str(stamped)
    text = str(item.get("text") or "")
    return "h:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def uncapped_weight(item: dict, item_type: str, now: float) -> float:
    """The item's accumulation BEFORE the trust lid: importance x recency x
    type decay x overdue boost.

    Read from `scoring.explain`'s published `factors.raw` rather than
    recomputed here. explain derives its factors independently of
    effective_weight (its own docstring says so, and a test pins the factors to
    their product), so this is the one lid-free number the shipping code
    already stands behind. Recomputing the product in the runner would make the
    diagnostic depend on a second copy of TYPE_RULES that no test guards.
    """
    return scoring.explain(item, item_type, now)["factors"]["raw"]


def is_lid_biting(item: dict, item_type: str, now: float) -> bool:
    """True when this item's accumulation clears the inferred knee, so a flip
    of its trust class actually changes its effective weight. Reads the
    uncapped product, so the answer is the same before and after the flip."""
    return uncapped_weight(item, item_type, now) > LID_KNEE


_DROP_ORDER_SECTIONS = frozenset(key for key, _end in briefing._DROP_ORDER)


def is_truncation_exempting(item: dict, section: str) -> bool:
    """True when flipping this item to verbatim changes the RENDERED LENGTH.

    The SECOND channel trust reaches the briefing by, and the one the
    pre-registration missed. `render_plain` stage 1 shortens oversized items
    in place, EXCEPT verbatim ones, which #30 froze. So an item long enough to
    be truncated buys its section budget while it is inferred and stops doing
    so the moment the gate is lifted, and stage 2 has to drop somebody else.
    This is independent of `effective_weight`: it moves no item's rank, it
    changes who survives, and it points the OTHER way — the gate makes the
    briefing hold MORE items, not fewer.

    Asks the shipped truncator rather than comparing lengths:
    `truncate_preserving_sections` may leave an over-cap text alone, and a
    length proxy would then claim an effect the render does not have.
    """
    if section not in _DROP_ORDER_SECTIONS:
        return False
    text = str(item.get("text") or "")
    return briefing.truncate_preserving_sections(
        text, briefing._ITEM_TRUNCATE_CHARS) != text


def rank_diff(gated: list, ungated: list) -> dict:
    """Ordered-list diff between the two arms' results for one prompt."""
    a_pos = {k: i for i, k in enumerate(gated)}
    b_pos = {k: i for i, k in enumerate(ungated)}
    shared = set(a_pos) & set(b_pos)
    return {
        "identical": list(gated) == list(ungated),
        # `items_in` entered under the ungated arm; `items_out` left it.
        "items_in": sorted(set(b_pos) - set(a_pos)),
        "items_out": sorted(set(a_pos) - set(b_pos)),
        # Positive = the item FELL when the gate was lifted.
        "rank_deltas": {k: b_pos[k] - a_pos[k] for k in sorted(shared)
                        if b_pos[k] != a_pos[k]},
    }


def positions_changed(gated: list, ungated: list) -> int:
    """How many section slots hold a different item between the arms. A length
    difference counts every orphan slot as changed — an item that vanished
    from a position changed that position."""
    return sum(
        1 for i in range(max(len(gated), len(ungated)))
        if (gated[i] if i < len(gated) else None)
        != (ungated[i] if i < len(ungated) else None)
    )


def cap_crossings(gated: set, ungated: set) -> dict:
    """Items that survived the render budget in one arm and not the other."""
    only_gated = sorted(set(gated) - set(ungated))
    only_ungated = sorted(set(ungated) - set(gated))
    return {
        "count": len(only_gated) + len(only_ungated),
        "only_gated": only_gated,
        "only_ungated": only_ungated,
    }


def fingerprint_files(entries) -> str:
    """One digest over a set of (relative path, content sha256) pairs.

    The local store is LIVE — other sessions append checkpoints to it while a
    replay runs — so "re-run it and the bytes match" is a claim about the
    RUNNER only once the inputs are pinned. This is what pins them: a record
    whose fingerprint differs was measured over a different corpus, and its
    numbers are not comparable, however identical the code.

    Order-independent (the caller's read order is not part of the input) and
    NUL-separated, so a rename cannot hide behind an edit. Emits one digest,
    never the paths: local checkpoint paths name the operator's projects.
    """
    digest = hashlib.sha256()
    for label, content in sorted(entries):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def fingerprint_store(root: Path) -> dict:
    entries = [(str(p.relative_to(root)),
                hashlib.sha256(p.read_bytes()).hexdigest())
               for p in _checkpoint_files(root)]
    return {"files": len(entries), "sha256": fingerprint_files(entries)}


def wilson(k: int, n: int, z: float = 1.96) -> tuple:
    """Wilson score interval for k successes in n trials.

    Not the normal approximation: every fraction this runner reports is
    expected near 0, where the Wald interval is nonsense (it collapses to a
    point at k=0 and can leave the unit interval). n=0 returns the whole unit
    interval, because no observations is no evidence — reporting (0, 0) there
    would read as a MEASURED zero rate.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (max(0.0, center - half), min(1.0, center + half))


# ------------------------------------------------------------- env / scratch

class _Env:
    """Redirect EVERY DAIMON_* variable at once, restoring on exit.

    #754's `_Env` pops a hand-listed key tuple. A list is the wrong shape
    here: this runner reads two more surfaces than #754 did (config's briefing
    budget, decision cap and extra-read slugs all reach the measurement), and
    a variable the list forgets is inherited silently from the operator's own
    shell, which is how a replay quietly measures somebody's local config
    instead of the code. The env FILE is neutralized the same way, by pointing
    DAIMON_ENV_FILE at a path inside the scratch dir that does not exist —
    config falls back to it whenever the process env has no value.
    """

    def __init__(self, mapping: dict):
        self.mapping = mapping
        self.saved: dict = {}

    def __enter__(self):
        self.saved = {k: v for k, v in os.environ.items()
                      if k.startswith("DAIMON_")}
        for k in self.saved:
            os.environ.pop(k, None)
        os.environ.update(self.mapping)
        return self

    def __exit__(self, *exc):
        for k in [k for k in os.environ if k.startswith("DAIMON_")]:
            os.environ.pop(k, None)
        os.environ.update(self.saved)


def _env_for(home: Path, project_dir: Path, extra_slugs=()) -> dict:
    return {
        "DAIMON_CHECKPOINT_DIR": str(home / "checkpoints"),
        "DAIMON_RECALL_DB": str(home / "recall.db"),
        "DAIMON_LOG_DIR": str(home / "logs"),
        "DAIMON_RECALL_SEEN_DIR": str(home / "recall_seen"),
        "DAIMON_TEAM_DIR": str(home / "team"),
        "DAIMON_PROJECT_DIR": str(project_dir),
        "DAIMON_ENV_FILE": str(home / "no-such-env-file"),
        "DAIMON_CARRY": "0",
        "DAIMON_MIN_MESSAGES": str(MIN_MESSAGES),
        "DAIMON_TEAM": "0",
        "DAIMON_DISABLE": "0",
        "DAIMON_RECEIPTS": "0",
        "DAIMON_SCAR_HARVEST": "0",
        "DAIMON_SCENE_TRACES": "0",
        "DAIMON_EXTRA_READ_SLUGS": ",".join(extra_slugs),
    }


# ------------------------------------------------------------- checkpoint io

def _checkpoint_files(root: Path) -> list:
    """Every checkpoint json under a store root, in a stable order."""
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def _load_checkpoint(path: Path):
    try:
        cp = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return cp if isinstance(cp, dict) else None


def _typed_items(checkpoint: dict):
    """Yield (item, scoring type) for every scored item in a checkpoint.

    Walks `schema.ITEM_FIELDS`, the single source both recall's index view and
    carry's view derive from, so a field added there reaches this runner
    without an edit. Fields with no scoring type (contradictions) are skipped:
    nothing ranks them, so a flip there cannot move an order.
    """
    for field in schema.ITEM_FIELDS:
        if not field.scoring_type:
            continue
        block = checkpoint.get(field.section)
        if not isinstance(block, dict):
            continue
        if field.singleton:
            item = block.get(field.key)
            if isinstance(item, dict):
                yield item, field.scoring_type
            continue
        for item in block.get(field.key) or []:
            if isinstance(item, dict):
                yield item, field.scoring_type


def stamp_keys(checkpoint: dict) -> None:
    """Stamp each item with its identity BEFORE any arm touches it.

    render_plain's stage 1 rewrites `text` on non-verbatim items, so an
    identity derived from text at render time would rename exactly the items
    the budget squeezed. Stamped up front, the key rides through the `{**i}`
    copy unchanged.
    """
    for item, _type in _typed_items(checkpoint):
        item[_KEY_FIELD] = item_key(item)


def _key_of(item: dict) -> str:
    return str(item.get(_KEY_FIELD) or item_key(item))


def flip_in_memory(checkpoint: dict) -> list:
    """Revert every trust-gate downgrade in a checkpoint copy. Returns
    [(key, scoring type, item-as-it-stood-before-the-flip)]."""
    flipped = []
    for item, item_type in _typed_items(checkpoint):
        if should_flip(item):
            flipped.append((_key_of(item), item_type, dict(item)))
            item["trust"] = "verbatim"
    return flipped


# --------------------------------------------------------------- suggest arm

def _stamped_slugs(home: Path) -> list:
    """The project slugs the copied store's checkpoints carry.

    A copied store keeps the slug stamped at capture, which names the ORIGINAL
    path; the scratch project dir slugs to something else entirely. Without
    this the ambient scope never covers the store and `suggest` returns [] on
    every prompt — a broken instrument that looks exactly like a null result.
    """
    slugs = []
    for path in _checkpoint_files(home / "checkpoints"):
        cp = _load_checkpoint(path)
        slug = (cp or {}).get("project_slug")
        if slug and slug not in slugs:
            slugs.append(slug)
    return slugs


def _newest_created(home: Path) -> float | None:
    best = None
    for path in _checkpoint_files(home / "checkpoints"):
        cp = _load_checkpoint(path)
        epoch = store._created_epoch((cp or {}).get("created"))
        if epoch is not None and (best is None or epoch > best):
            best = epoch
    return best


def _suggest_keys(rows: list) -> list:
    return [item_key({"id": r.get("item_id"), "text": r.get("text")})
            for r in rows]


def run_suggest_arm(home: Path, project_dir: Path, prompt: str,
                    now: float) -> dict:
    """One arm's suggest results for one prompt, at every measured limit."""
    with _Env(_env_for(home, project_dir, _stamped_slugs(home))):
        recall.rebuild()
        out = {}
        for limit in SUGGEST_LIMITS:
            rows = recall.suggest(prompt, project_dir=str(project_dir),
                                  limit=limit, now=now)
            out[limit] = {"keys": _suggest_keys(rows),
                          "sessions": [r["session_id"] for r in rows],
                          "kinds": [r.get("kind") for r in rows]}
    return out


def measure_suggest(scratch: Path) -> tuple:
    """The suggest surface over the 54 reference-run bench stores."""
    questions = {q["question_id"]: q for q in dataset.load(DATA)}
    with open(REFERENCE, encoding="utf-8") as fh:
        qids = [q["question_id"] for q in json.load(fh)["per_question"]]

    rows, totals = [], {
        "prompts": 0, "fired": 0, "identical": 0, "differing": 0,
        "flipped_items": 0, "lid_biting_flips": 0,
        "differences_with_a_lid_biting_flip": 0,
    }
    for n, qid in enumerate(qids, 1):
        src = WORK / qid
        if not src.is_dir():
            raise SystemExit(f"{qid}: no .work store; run the bench first")
        gated = scratch / "suggest" / qid / "gated"
        ungated = scratch / "suggest" / qid / "ungated"
        gated.parent.mkdir(parents=True, exist_ok=True)
        for dst in (gated, ungated):
            shutil.copytree(src, dst)
        project_dir = scratch / "suggest" / qid / "project"
        project_dir.mkdir(parents=True, exist_ok=True)

        created = _newest_created(gated)
        now = (created if created is not None else 0.0) + DAY
        prompt = questions[qid]["question"]

        # Classify the flips BEFORE the arm rewrites them on disk.
        flips = []
        for path in _checkpoint_files(gated / "checkpoints"):
            cp = _load_checkpoint(path)
            if cp is None:
                continue
            stamp_keys(cp)
            for item, item_type in _typed_items(cp):
                if should_flip(item):
                    flips.append({"item": _key_of(item),
                                  "lid_biting": is_lid_biting(item, item_type,
                                                              now)})
        lid_biting = {f["item"] for f in flips if f["lid_biting"]}

        a = run_suggest_arm(gated, project_dir, prompt, now)
        n_flipped = flip_downgrades(ungated)
        b = run_suggest_arm(ungated, project_dir, prompt, now)

        per_limit, differs, fired = {}, False, False
        for limit in SUGGEST_LIMITS:
            diff = rank_diff(a[limit]["keys"], b[limit]["keys"])
            fired = fired or bool(a[limit]["keys"] or b[limit]["keys"])
            differs = differs or not diff["identical"]
            per_limit[str(limit)] = {
                "n_gated": len(a[limit]["keys"]),
                "n_ungated": len(b[limit]["keys"]),
                **diff,
            }

        responsible = sorted(
            {k for limit in SUGGEST_LIMITS
             for k in (set(a[limit]["keys"]) ^ set(b[limit]["keys"]))
             | set(rank_diff(a[limit]["keys"], b[limit]["keys"])["rank_deltas"])}
        )
        totals["prompts"] += 1
        totals["fired"] += fired
        totals["identical"] += not differs
        totals["differing"] += differs
        totals["flipped_items"] += n_flipped
        totals["lid_biting_flips"] += len(lid_biting)
        if differs and lid_biting:
            totals["differences_with_a_lid_biting_flip"] += 1

        rows.append({
            "question_id": qid,
            "now": now,
            "fired": fired,
            "identical": not differs,
            "n_flipped_items": n_flipped,
            "n_lid_biting_flips": len(lid_biting),
            "by_limit": per_limit,
            # Only the items that actually moved, each with the reason the
            # mechanism allows: was it a flip that clears the knee?
            "responsible": [
                {"item": k, "flipped": k in {f["item"] for f in flips},
                 "lid_biting": k in lid_biting}
                for k in responsible
            ],
        })
        print(f"[{n:2d}/{len(qids)}] {qid} fired={fired} "
              f"identical={not differs} flips={n_flipped} "
              f"lid_biting={len(lid_biting)}", flush=True)
        shutil.rmtree(scratch / "suggest" / qid, ignore_errors=True)
    return rows, totals


# -------------------------------------------------------------- briefing arm

def _render_survivors(b: dict) -> tuple:
    """Which items survive `render_plain`'s budget, and whether it bit.

    Observed rather than re-derived: `briefing._render_parts` is wrapped so the
    LAST call render_plain makes is captured, and that call's `b` IS the
    section state behind the returned text (every `text` in render_plain is
    assigned from a _render_parts return). Reimplementing the stage-2 drop loop
    in the runner would put a second copy of the budget policy in the
    measurement, free to drift from the one being measured.
    """
    calls = []
    original = briefing._render_parts

    def spy(section_state, trimmed, *args, **kwargs):
        calls.append((section_state, dict(trimmed)))
        return original(section_state, trimmed, *args, **kwargs)

    briefing._render_parts = spy
    try:
        briefing.render_plain(b)
    finally:
        briefing._render_parts = original

    final_state, trimmed = calls[-1]
    survivors = {
        key: {_key_of(i) for i in (final_state.get(key) or [])}
        for key in RENDER_SECTIONS
    }
    return survivors, {"over_budget": len(calls) > 1,
                       "trimmed": {k: v for k, v in trimmed.items() if v}}


def measure_one_checkpoint(cp: dict, label: str, source: str) -> list:
    """Both arms of one checkpoint at every horizon. Returns the differing
    rows only; callers aggregate the rest."""
    created = store._created_epoch(cp.get("created"))
    if created is None:
        return [{"checkpoint": label, "source": source, "skipped": "no created stamp"}]

    stamp_keys(cp)
    out = []
    for horizon in BRIEFING_HORIZONS:
        now = created + horizon * DAY
        gated_cp = copy.deepcopy(cp)
        ungated_cp = copy.deepcopy(cp)
        flipped = flip_in_memory(ungated_cp)
        lid_biting = sorted({key for key, item_type, before in flipped
                             if is_lid_biting(before, item_type, now)})

        a = briefing.build(gated_cp, now=now)
        b = briefing.build(ungated_cp, now=now)
        row = {
            "checkpoint": label,
            "source": source,
            "horizon_days": horizon,
            "now": now,
            "n_flipped_items": len(flipped),
            "n_lid_biting_flips": len(lid_biting),
            "lid_biting_items": lid_biting,
        }
        if a is None and b is None:
            row["empty"] = True
            out.append(row)
            continue

        order_a = {k: [_key_of(i) for i in (a.get(k) or [])]
                   for k in SORTED_SECTIONS}
        order_b = {k: [_key_of(i) for i in (b.get(k) or [])]
                   for k in SORTED_SECTIONS}
        survivors_a, budget_a = _render_survivors(a)
        survivors_b, budget_b = _render_survivors(b)

        flipped_keys = {key for key, _t, _before in flipped}
        exempting = sorted({
            _key_of(i)
            for key in RENDER_SECTIONS if key in _DROP_ORDER_SECTIONS
            for i in (a.get(key) or [])
            if _key_of(i) in flipped_keys and is_truncation_exempting(i, key)})
        row["n_truncation_exempting_flips"] = len(exempting)
        row["truncation_exempting_items"] = exempting

        sections = {}
        for key in RENDER_SECTIONS:
            entry = {
                "positions_changed": (
                    positions_changed(order_a[key], order_b[key])
                    if key in SORTED_SECTIONS else 0),
                "cap_crossings": cap_crossings(survivors_a[key],
                                               survivors_b[key]),
            }
            if key in SORTED_SECTIONS:
                entry["rank_diff"] = rank_diff(order_a[key], order_b[key])
            sections[key] = entry

        row["over_budget"] = {"gated": budget_a["over_budget"],
                              "ungated": budget_b["over_budget"]}
        row["trimmed"] = {"gated": budget_a["trimmed"],
                          "ungated": budget_b["trimmed"]}
        row["positions_changed"] = sum(s["positions_changed"]
                                       for s in sections.values())
        row["cap_crossings"] = sum(s["cap_crossings"]["count"]
                                   for s in sections.values())
        row["differs"] = bool(row["positions_changed"] or row["cap_crossings"])
        row["sections"] = {k: v for k, v in sections.items()
                           if v["positions_changed"] or v["cap_crossings"]["count"]}
        out.append(row)
    return out


def measure_briefing(scratch: Path) -> tuple:
    """The briefing surface over the bench stores plus the local store."""
    with open(REFERENCE, encoding="utf-8") as fh:
        qids = [q["question_id"] for q in json.load(fh)["per_question"]]

    # Copy-on-read, as in #754: nothing here writes, but the originals are the
    # only copy of both substrates and a runner that reads them in place is one
    # edit away from being a runner that does not.
    sources = []
    for qid in qids:
        sources.append((f"bench:{qid}", WORK / qid / "checkpoints",
                        lambda p, root, qid=qid: f"{qid}/{p.name}"))
    if LOCAL_STORE and LOCAL_STORE.is_dir():
        sources.append((
            "local", LOCAL_STORE,
            lambda p, root: "sha256:" + hashlib.sha256(
                str(p.relative_to(root)).encode("utf-8")).hexdigest()[:16]))

    rows, totals = [], {
        "checkpoints": 0, "measured": 0, "skipped_no_created": 0,
        "empty": 0, "comparisons": 0, "differing": 0,
        "differences_with_a_lid_biting_flip": 0,
        "differences_with_a_truncation_exempting_flip": 0,
        "differences_with_neither_channel": 0,
        "flipped_items": 0, "lid_biting_flips": 0,
        "truncation_exempting_flips": 0,
        "positions_changed": 0, "cap_crossings": 0, "over_budget": 0,
    }
    by_horizon = {h: {"flips": 0, "lid_biting": 0} for h in BRIEFING_HORIZONS}
    inputs = {"bench": {"stores": len(qids), "files": 0, "sha256": ""},
              "local": {"present": False}}
    bench_digests: list = []
    project_dir = scratch / "briefing-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    home = scratch / "briefing-home"
    (home / "checkpoints").mkdir(parents=True, exist_ok=True)

    # The briefing arm only READS checkpoint dicts, but build/render still ask
    # config for the decision cap and the token budget, so it runs under the
    # same redirected env as the suggest arm: the numbers must come from the
    # shipped defaults, not from whatever the operator's shell exports.
    with _Env(_env_for(home, project_dir)):
        for source, origin, labeller in sources:
            if not origin.is_dir():
                continue
            root = scratch / "briefing-src" / source.replace(":", "-")
            root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(origin, root)
            # Fingerprint the COPY, not the origin: the local store is live,
            # and a fingerprint taken before the copy could name a corpus the
            # run never read.
            fp = fingerprint_store(root)
            if source == "local":
                inputs["local"] = {"present": True, **fp}
            else:
                bench_digests.append((source, fp["sha256"]))
                inputs["bench"]["files"] += fp["files"]
            for path in _checkpoint_files(root):
                cp = _load_checkpoint(path)
                totals["checkpoints"] += 1
                if cp is None:
                    continue
                for row in measure_one_checkpoint(cp, labeller(path, root),
                                                  source):
                    if row.get("skipped"):
                        totals["skipped_no_created"] += 1
                        continue
                    totals["comparisons"] += 1
                    totals["flipped_items"] += row["n_flipped_items"]
                    totals["lid_biting_flips"] += row["n_lid_biting_flips"]
                    horizon = by_horizon[row["horizon_days"]]
                    horizon["flips"] += row["n_flipped_items"]
                    horizon["lid_biting"] += row["n_lid_biting_flips"]
                    if row.get("empty"):
                        totals["empty"] += 1
                        continue
                    totals["measured"] += 1
                    totals["truncation_exempting_flips"] += row[
                        "n_truncation_exempting_flips"]
                    totals["over_budget"] += bool(
                        row["over_budget"]["gated"]
                        or row["over_budget"]["ungated"])
                    totals["positions_changed"] += row["positions_changed"]
                    totals["cap_crossings"] += row["cap_crossings"]
                    if row["differs"]:
                        totals["differing"] += 1
                        if row["n_lid_biting_flips"]:
                            totals[
                                "differences_with_a_lid_biting_flip"] += 1
                        if row["n_truncation_exempting_flips"]:
                            totals[
                                "differences_with_a_truncation_exempting_flip"
                            ] += 1
                        if not (row["n_lid_biting_flips"]
                                or row["n_truncation_exempting_flips"]):
                            totals["differences_with_neither_channel"] += 1
                        # Per-checkpoint rows are kept for DIFFERING
                        # comparisons only; the rest are in the totals. The
                        # record has to stay small enough to commit.
                        rows.append(row)
            shutil.rmtree(root, ignore_errors=True)
            print(f"  {source}: {totals['checkpoints']} checkpoints seen, "
                  f"{totals['differing']} differing", flush=True)
    totals["by_horizon"] = {str(h): v for h, v in by_horizon.items()}
    inputs["bench"]["sha256"] = fingerprint_files(bench_digests)
    return rows, totals, inputs


# -------------------------------------------------------------------- report

def _fraction(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n,
            "rate": round(k / n, 6) if n else None,
            "wilson95": [round(lo, 6), round(hi, 6)]}


def main():
    scratch = Path(tempfile.mkdtemp(prefix="trust-gate-cost-976-"))
    try:
        print("suggest arm:", flush=True)
        suggest_rows, suggest_totals = measure_suggest(scratch)
        print("briefing arm:", flush=True)
        briefing_rows, briefing_totals, inputs = measure_briefing(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    flips = suggest_totals["flipped_items"] + briefing_totals["flipped_items"]
    biting = (suggest_totals["lid_biting_flips"]
              + briefing_totals["lid_biting_flips"])
    suggest_diff = suggest_totals["differing"]
    briefing_diff = briefing_totals["differing"]
    p1 = (suggest_diff == suggest_totals["differences_with_a_lid_biting_flip"]
          and briefing_diff
          == briefing_totals["differences_with_a_lid_biting_flip"])
    # The registered P1 named ONE channel. Held against both measured channels
    # it is the claim that no difference is unexplained; reported separately so
    # the falsification of the registered wording stays on the record.
    unexplained = briefing_totals["differences_with_neither_channel"]
    lid_fraction = _fraction(biting, flips)
    p2 = bool(flips) and lid_fraction["wilson95"][1] < 0.10

    summary = {
        "suggest": {
            "prompts": suggest_totals["prompts"],
            "fired": _fraction(suggest_totals["fired"],
                               suggest_totals["prompts"]),
            "differing": _fraction(suggest_diff, suggest_totals["prompts"]),
            "flipped_items": suggest_totals["flipped_items"],
            "lid_biting_flips": suggest_totals["lid_biting_flips"],
            "differences_with_a_lid_biting_flip":
                suggest_totals["differences_with_a_lid_biting_flip"],
            "limits": list(SUGGEST_LIMITS),
        },
        "briefing": {
            "checkpoints": briefing_totals["checkpoints"],
            "comparisons": briefing_totals["comparisons"],
            "measured": briefing_totals["measured"],
            "empty": briefing_totals["empty"],
            "skipped_no_created": briefing_totals["skipped_no_created"],
            "over_budget": _fraction(briefing_totals["over_budget"],
                                     briefing_totals["measured"]),
            "differing": _fraction(briefing_diff,
                                   briefing_totals["measured"]),
            "positions_changed": briefing_totals["positions_changed"],
            "cap_crossings": briefing_totals["cap_crossings"],
            "flipped_items": briefing_totals["flipped_items"],
            "lid_biting_flips": briefing_totals["lid_biting_flips"],
            "differences_with_a_lid_biting_flip":
                briefing_totals["differences_with_a_lid_biting_flip"],
            "truncation_exempting_flips":
                briefing_totals["truncation_exempting_flips"],
            "differences_with_a_truncation_exempting_flip":
                briefing_totals[
                    "differences_with_a_truncation_exempting_flip"],
            "differences_with_neither_channel": unexplained,
            "horizons_days": list(BRIEFING_HORIZONS),
            # Per horizon, because a flip is counted once per horizon: the
            # pooled rate below mixes two observations of the same item.
            "by_horizon": {
                h: {**v, "lid_biting_fraction":
                    _fraction(v["lid_biting"], v["flips"])}
                for h, v in briefing_totals["by_horizon"].items()},
        },
        "lid_knee": LID_KNEE,
        "lid_biting_fraction_of_flips": lid_fraction,
        "lid_biting_fraction_by_surface": {
            "suggest": _fraction(suggest_totals["lid_biting_flips"],
                                 suggest_totals["flipped_items"]),
            "briefing": _fraction(briefing_totals["lid_biting_flips"],
                                  briefing_totals["flipped_items"]),
        },
        "P1_every_difference_traces_to_a_lid_biting_flip": p1,
        "P1_every_difference_traces_to_a_measured_channel": unexplained == 0,
        "P2_lid_biting_fraction_is_single_digit_percent": p2,
    }

    OUT.write_text(json.dumps({
        "issue": 976,
        "pre_registration": "Design - Trust Gate Cost Experiment 2026-09-12",
        # Which corpus produced these numbers. A re-run whose fingerprints
        # differ read a different store and its numbers are not comparable.
        "inputs": inputs,
        "summary": summary,
        "suggest": suggest_rows,
        "briefing": briefing_rows,
    }, indent=1, sort_keys=False), encoding="utf-8")
    print("summary:", json.dumps(summary, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
