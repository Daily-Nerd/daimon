# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Slice-2 Probe Rerun — n=5 re-validation of the SHIPPED chunked serializer
==========================================================================
Re-runs all 5 Track A sessions (S1-S5) through the serializer that actually
shipped in plugin/daimon_briefing/serializer.py (serialize_strict: D-007
SERIALIZE_SYS, Q-STALE MERGE_SYS, line-chunking + concurrency), then judges
the reconstructions with three passes:

    Pass 1 — recall:    GT items vs reconstruction      (probe_d007 judge)
    Pass 2 — grounding: claims vs the ORIGINAL TRANSCRIPT (probe_d007 judge;
                        never the GT answer key — .scars/0001)
    Pass 3 — staleness: each RECALLED item's pinned state vs the FULL
                        TRANSCRIPT — does a LATER in-session state supersede
                        it? (Q-STALE, rubric Pass 3; doubt resolves to stale)

The serializer prompts are NOT copied here — the whole point is exercising the
shipped code path. Judges/reconstruct are reused from probe_d007.py.

Usage:
    # Real run (plugin config: DAIMON_* env or ~/.daimon/env, falling back to
    # LITELLM_*; set DAIMON_TIMEOUT=420 — checkpoint generation has taken 248s):
    uv run rerun.py --all
    uv run rerun.py --id S3
    uv run rerun.py --all --force        # redo even if rerun score exists
    uv run rerun.py --all --rejudge      # judges only, reuse rerun artifacts
    uv run rerun.py --all --serialize-only  # serialize+reconstruct only, no judging
    uv run rerun.py --all --cycle2       # 2-cycle degradation: cycle-1 reconstruction
                                         # back through the serializer (no judging)
    uv run rerun.py --mock               # mock-LLM end-to-end self-test

Outputs (originals are the baseline — NEVER touched):
    runs/<id>/rerun/checkpoint.json        shipped-serializer checkpoint
    runs/<id>/rerun/reconstruction.md      02-reconstruct output
    runs/<id>/rerun/meta.json              provenance + metrics
    runs/<id>/session-<id>.rerun.score.json  score.py-compatible, with `stale`

Cycle 2 (--cycle2, VALIDATION.md Track A "Cycle degradation"): feeds
runs/<id>/rerun/reconstruction.md back through the shipped serializer as if it
were a new raw transcript. Cycle-1 artifacts are READ-ONLY inputs; outputs go
to runs/<id>/rerun-c2/{checkpoint.json,reconstruction.md,meta.json}. Implies
serialize-only semantics — judging is EXTERNAL (mutually exclusive with
--rejudge and --serialize-only). Skip key: rerun-c2 checkpoint.json +
reconstruction.md (--force to redo).

Aggregate afterwards:
    uv run scoring/score.py 'runs/*/session-*.rerun.score.json'

Resumability: a session is skipped when its rerun score file exists (--force
to redo); if serialize/reconstruct artifacts exist but the score is missing
(judge died), only the judge passes re-run. Under --serialize-only the skip
key is rerun/checkpoint.json + reconstruction.md instead (no score file is
ever written in that mode). WARNING (.scars/0002): the skip
check is check-then-write with no lockfile — do NOT run two rerun.py
invocations concurrently.
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))                          # probe_d007
sys.path.insert(0, str(HERE.parents[2] / "plugin"))    # daimon_briefing

import probe_d007 as probe  # noqa: E402
from daimon_briefing import config as plugin_config  # noqa: E402
from daimon_briefing import llm as plugin_llm  # noqa: E402
from daimon_briefing import serializer  # noqa: E402

sys.stdout.reconfigure(line_buffering=True)  # progress must stream when piped (multi-hour runs)

RUNS_DIR = HERE / "runs"
SESSIONS_DIR = HERE / "sessions"

RECOMMENDED_TIMEOUT_S = 420  # LOGBOOK: checkpoint generation took 248s; the
# default 120s socket timeout x 3 retries was a silent 7-minute failure.

AGGREGATE_CMD = "uv run scoring/score.py 'runs/*/session-*.rerun.score.json'"


# ---------------------------------------------------------------------------
# Plugin serializer bridge
# ---------------------------------------------------------------------------
# Two client shapes exist:
#   - probe_d007 judge/reconstruct functions want llm_mod.chat(...) ->
#     (content, usage, model) and llm_mod.extract_json
#   - serialize_strict wants an injectable chat(messages, **kw) -> str
# PluginGatewayLLM wraps the plugin's stdlib client (DAIMON_*/LITELLM_* env
# with ~/.daimon/env fallback) into the tuple shape; _chat_fn() projects any
# tuple-shaped llm_mod back down to the str shape. One client, both seams.

class PluginGatewayLLM:
    """probe_d007-compatible adapter over plugin/daimon_briefing/llm.chat."""

    @staticmethod
    def chat(messages, **kwargs):
        content = plugin_llm.chat(messages, **kwargs)
        return content, {"total_tokens": "?"}, plugin_config.llm_model() or "?"

    extract_json = staticmethod(plugin_llm.extract_json)


class GatewayLLM:
    """Instance adapter over plugin llm.chat. model=None -> use config default.

    Allows routing generation and judge calls to different models while
    duck-typing identically to PluginGatewayLLM for .chat/.extract_json.
    plugin_llm.chat already honors an explicit model= override
    (daimon_briefing/llm.py: mdl = model or config.llm_model()).
    """

    def __init__(self, model=None):
        self._model = model

    def chat(self, messages, **kwargs):
        if self._model is not None:
            kwargs.setdefault("model", self._model)
        content = plugin_llm.chat(messages, **kwargs)
        return content, {"total_tokens": "?"}, (self._model or plugin_config.llm_model() or "?")

    extract_json = staticmethod(plugin_llm.extract_json)


def _chat_fn(llm_mod):
    """serialize_strict-compatible chat callable from a tuple-shaped llm_mod."""
    def chat(messages, **kwargs):
        content, _usage, _model = llm_mod.chat(messages, **kwargs)
        return content
    return chat


_TURN_PREFIXES = (("User:", "user"), ("Assistant:", "assistant"))


def parse_transcript(text: str) -> list[dict]:
    """Session .txt ('User: ... / Assistant: ...') -> serialize_strict messages.

    serialize_strict renders messages back to 'role: content' lines, so the
    rendered text the shipped chunker sees stays line-equivalent to the file.
    """
    messages: list[dict] = []
    role, buf = None, []
    for line in text.splitlines():
        matched = None
        for prefix, r in _TURN_PREFIXES:
            if line.startswith(prefix):
                matched = (r, line[len(prefix):].lstrip())
                break
        if matched:
            if role is not None:
                messages.append({"role": role, "content": "\n".join(buf).strip()})
            role, buf = matched[0], [matched[1]]
        elif role is not None:
            buf.append(line)
        elif line.strip():  # preamble before any marker — keep, attribute to user
            role, buf = "user", [line]
    if role is not None:
        messages.append({"role": role, "content": "\n".join(buf).strip()})
    return messages


def serialize_checkpoint(sid: str, transcript_text: str, llm_mod) -> dict:
    """Run the SHIPPED serializer (chunk threshold, concurrency, Q-STALE merge,
    validation — all from plugin config) on a raw transcript."""
    messages = parse_transcript(transcript_text)
    return serializer.serialize_strict(sid, messages, chat=_chat_fn(llm_mod))


def serialize_cycle2_checkpoint(sid: str, reconstruction_text: str, llm_mod) -> dict:
    """Cycle 2: serialize a cycle-1 RECONSTRUCTION (prose, not a
    'User:/Assistant:' transcript). parse_transcript is bypassed on purpose —
    prefix-less prose would be lumped into one mis-attributed preamble turn —
    so the whole reconstruction is wrapped as exactly ONE user message.

    min_messages is a raw-transcript gate (default 10); n=1 here is by
    construction, so the gate is lowered for THIS call only and restored."""
    import os
    messages = [{"role": "user", "content": reconstruction_text}]
    prev = os.environ.get("DAIMON_MIN_MESSAGES")
    os.environ["DAIMON_MIN_MESSAGES"] = "1"
    try:
        return serializer.serialize_strict(sid, messages, chat=_chat_fn(llm_mod))
    finally:
        if prev is None:
            del os.environ["DAIMON_MIN_MESSAGES"]
        else:
            os.environ["DAIMON_MIN_MESSAGES"] = prev


# ---------------------------------------------------------------------------
# Pass 3 — staleness judge (Q-STALE, rubric Pass 3)
# ---------------------------------------------------------------------------

STALENESS_JUDGE_SYS = """You are a staleness judge for a cognitive-state reconstruction experiment (Q-STALE, scoring rubric Pass 3).

Some facts EVOLVE within a session: an early value is re-measured, a decision is revised, a result is corrected. A reconstruction can pin such a fact to a state that genuinely appears in the transcript but was SUPERSEDED later in the same session. Grounding misses this (the pinned state is real), so it gets this dedicated pass.

Given:
1. RECALLED ITEMS (JSON list of {id, text}) — identifiers for facts the reconstruction recalled. They only tell you WHICH facts to check; they are NOT evidence and NOT ground truth.
2. RECONSTRUCTION TEXT — the reconstruction whose pinned states you are judging.
3. FULL TRANSCRIPT — the complete original session transcript. This is your ONLY source of truth.

For each item:
  a. Locate the state the RECONSTRUCTION pins for this fact: the concrete value, quote, outcome, or status it asserts.
  b. Scan the FULL TRANSCRIPT chronologically for that fact's evolution and find its LATEST in-session state.
  c. Decide:
     stale: false — the reconstruction's pinned state matches the fact's FINAL in-session state.
     stale: true  — the transcript shows a LATER state that supersedes the pinned one.
     stale: null  — the item has no pinnable concrete state to check (no value/quote/outcome asserted).

Rules:
- Ground every decision ONLY in the FULL TRANSCRIPT. The item texts are labels, not evidence.
- When in doubt between "final" and "superseded" -> stale: true. Harshness is the point.
- A stale item is still recalled; you grade staleness only — never recall.

Output ONLY a JSON object (no prose):
{"items": [{"id": "<id>", "stale": true|false|null}, ...]}"""


def llm_staleness_judge(recalled_items: list[dict], reconstruction_text: str,
                        transcript: str, llm_mod, verbose: bool = False) -> dict:
    """Pass 3: recalled items' pinned states vs the FULL transcript.

    recalled_items carry ONLY {id, text} — never the GT quote/answer key
    (.scars/0001 applies to staleness as much as grounding).
    Returns {id: bool} for graded items; null/non-bool grades are omitted
    (no pinnable state -> no `stale` key in the score doc).
    """
    if not recalled_items:
        return {}
    user_content = (
        f"RECALLED ITEMS (JSON):\n{json.dumps(recalled_items, indent=2)}\n\n"
        f"RECONSTRUCTION TEXT:\n{reconstruction_text}\n\n"
        f"FULL TRANSCRIPT:\n{transcript}"
    )
    content, usage, model = llm_mod.chat([
        {"role": "system", "content": STALENESS_JUDGE_SYS},
        {"role": "user", "content": user_content},
    ])
    raw = llm_mod.extract_json(content)
    entries = raw.get("items", raw) if isinstance(raw, dict) else raw
    grades: dict = {}
    n_stale = 0
    for entry in entries or []:
        val = entry.get("stale")
        if isinstance(val, bool):
            grades[entry.get("id")] = val
            n_stale += val
    if verbose:
        print(f"    [stale-judge] {n_stale}/{len(grades)} graded stale "
              f"({len(recalled_items) - len(grades)} no-pin) "
              f"tokens={usage.get('total_tokens', '?')} model={model}")
    return grades


# ---------------------------------------------------------------------------
# Per-session pipeline
# ---------------------------------------------------------------------------

@dataclass
class RerunResult:
    sid: str
    status: str  # "done" | "rejudged" | "skipped" | "failed:<stage>:<reason>"
    rr: float | None = None
    fmr: float | None = None
    omr: float | None = None
    staleness: float | None = None
    error: str = ""


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def run_session(sid: str, llm_mod, runs_dir=None, sessions_dir=None,
                force: bool = False, rejudge: bool = False,
                serialize_only: bool = False,
                judge_chunk_lines: int = 1200, judge_overlap_lines: int = 100,
                verbose: bool = True, *, judge_llm=None) -> RerunResult:
    """One session end-to-end: shipped serialize -> reconstruct -> 3 judge
    passes -> score doc. Originals in runs/<sid>/ are never written.

    judge_llm: optional separate adapter for judge passes (recall, grounding,
    staleness). When None, judge_llm falls back to llm_mod (back-compat: all
    existing callers that pass a single mock behave exactly as before)."""
    # Back-compat: no split -> judge uses the same adapter as generation.
    if judge_llm is None:
        judge_llm = llm_mod
    runs_dir = Path(runs_dir) if runs_dir else RUNS_DIR
    sessions_dir = Path(sessions_dir) if sessions_dir else SESSIONS_DIR
    session_dir = runs_dir / sid
    rerun_dir = session_dir / "rerun"
    score_path = session_dir / f"session-{sid}.rerun.score.json"
    cp_path = rerun_dir / "checkpoint.json"
    recon_path = rerun_dir / "reconstruction.md"

    # Resumability under --serialize-only: existing serialize artifacts =
    # session done (no score file is ever written in this mode).
    if serialize_only and not force:
        if cp_path.exists() and recon_path.exists():
            if verbose:
                print(f"  [{_ts()}] [{sid}] rerun artifacts exist — skipping "
                      f"(--serialize-only; --force to redo)")
            return RerunResult(sid, "skipped")

    # Resumability: existing score file = session fully done (.scars/0002:
    # check-then-write, do not run two rerun.py concurrently).
    if score_path.exists() and not force and not rejudge and not serialize_only:
        if verbose:
            print(f"  [{_ts()}] [{sid}] rerun score exists — skipping "
                  f"(--force to redo, --rejudge for judges only)")
        try:
            ss = probe.get_score_session()(json.loads(score_path.read_text()))
            return RerunResult(sid, "skipped", ss.rr, ss.fmr, ss.omr, ss.staleness)
        except Exception as e:
            return RerunResult(sid, "skipped", error=str(e))

    transcript_path = sessions_dir / f"{sid}.txt"
    gt_path = session_dir / "ground-truth.json"
    if not transcript_path.exists():
        return RerunResult(sid, f"failed:input:transcript not found: {transcript_path}",
                           error=str(transcript_path))
    # Ground truth is consumed only by the judge passes; --serialize-only must
    # run without it (holdout flow: serialize first, human authors GT blind).
    if not gt_path.exists() and not serialize_only:
        return RerunResult(sid, f"failed:input:ground-truth not found: {gt_path}",
                           error=str(gt_path))
    transcript = transcript_path.read_text()
    gt_items = []
    if gt_path.exists():
        gt_doc = json.loads(gt_path.read_text())
        gt_items = gt_doc.get("ground_truth_items", gt_doc) if isinstance(gt_doc, dict) else gt_doc

    if rejudge:
        if not (cp_path.exists() and recon_path.exists()):
            msg = f"missing rerun checkpoint.json/reconstruction.md in {rerun_dir}"
            return RerunResult(sid, f"failed:rejudge:{msg}", error=msg)
        if verbose:
            print(f"  [{_ts()}] [{sid}] rejudge — reusing rerun artifacts")
        recon_text = recon_path.read_text()
        serialize_note = "rejudge: reused existing rerun artifacts"
    else:
        rerun_dir.mkdir(parents=True, exist_ok=True)
        # --- serialize via the SHIPPED plugin path (the expensive part) ---
        regenerated = False
        if cp_path.exists() and not force:
            if verbose:
                print(f"  [{_ts()}] [{sid}] reusing existing rerun/checkpoint.json")
            checkpoint = json.loads(cp_path.read_text())
            serialize_note = "resumed: reused existing rerun checkpoint"
        else:
            n_lines = len(transcript.splitlines())
            if verbose:
                print(f"  [{_ts()}] [{sid}] serializing via shipped "
                      f"serialize_strict ({n_lines} lines, threshold "
                      f"{plugin_config.chunk_lines()}) ...")
            t0 = time.monotonic()
            try:
                checkpoint = serialize_checkpoint(sid, transcript, llm_mod)
            except serializer.SerializeError as e:
                return RerunResult(sid, f"failed:serialize:{type(e).__name__}: {e}",
                                   error=str(e))
            except Exception as e:
                return RerunResult(sid, f"failed:serialize:{e}", error=str(e))
            cp_path.write_text(json.dumps(checkpoint, indent=2))
            regenerated = True
            serialize_note = "serialized via plugin serialize_strict"
            if verbose:
                print(f"  [{_ts()}] [{sid}] serialize done in "
                      f"{time.monotonic() - t0:.0f}s")
        # --- reconstruct (02-reconstruct prompt, via probe loader) ---
        if recon_path.exists() and not force and not regenerated:
            if verbose:
                print(f"  [{_ts()}] [{sid}] reusing existing rerun/reconstruction.md")
            recon_text = recon_path.read_text()
        else:
            try:
                recon_text, _ = probe._run_reconstruct(checkpoint, llm_mod, verbose=verbose)
            except Exception as e:
                return RerunResult(sid, f"failed:reconstruct:{e}", error=str(e))
            recon_path.write_text(recon_text)

    # --- serialize-only: artifacts + meta written, judging skipped ---
    if serialize_only:
        meta = {
            "session_id": sid,
            "timestamp": probe._iso_now(),
            "serializer": "plugin/daimon_briefing/serializer.py::serialize_strict",
            "prompt_version": serializer.PROMPT_VERSION,
            "serialize_note": serialize_note,
            "plugin_config": {
                "chunk_lines": plugin_config.chunk_lines(),
                "chunk_overlap": plugin_config.chunk_overlap(),
                "chunk_concurrency": plugin_config.chunk_concurrency(),
                "timeout_seconds": plugin_config.timeout_seconds(),
                "model": plugin_config.llm_model() or "mock/unset",
                "base_url": plugin_config.llm_base_url(),
            },
            "serialize_only": True,
        }
        (rerun_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        if verbose:
            print(f"  [{_ts()}] [{sid}] serialized — judging skipped "
                  f"(--serialize-only)")
        return RerunResult(sid, "serialized")

    # --- Pass 1 (recall vs GT) + Pass 2 (grounding vs TRANSCRIPT) ---
    if verbose:
        print(f"  [{_ts()}] [{sid}] judging — recall + transcript grounding ...")
    try:
        score_doc, judge_meta = probe._build_score_doc(
            sid=sid, gt_items=gt_items, reconstruction_text=recon_text,
            transcript=transcript, llm_mod=judge_llm,
            judge_chunk_lines=judge_chunk_lines,
            judge_overlap_lines=judge_overlap_lines, verbose=verbose,
        )
    except Exception as e:
        return RerunResult(sid, f"failed:judge:{e}", error=str(e))

    # --- Pass 3 (staleness vs FULL transcript) — only id+text leave the key ---
    gt_text_by_id = {i.get("id"): i.get("text", "") for i in gt_items}
    recalled = [{"id": i["id"], "text": gt_text_by_id.get(i["id"], "")}
                for i in score_doc["ground_truth_items"] if i.get("recalled")]
    if verbose:
        print(f"  [{_ts()}] [{sid}] judging — staleness on {len(recalled)} "
              f"recalled items (full transcript) ...")
    try:
        stale_grades = llm_staleness_judge(recalled, recon_text, transcript,
                                           judge_llm, verbose=verbose)
    except Exception as e:
        return RerunResult(sid, f"failed:staleness-judge:{e}", error=str(e))
    for item in score_doc["ground_truth_items"]:
        if item.get("recalled") and isinstance(stale_grades.get(item["id"]), bool):
            item["stale"] = stale_grades[item["id"]]

    # --- metrics + outputs (rerun namespace only) ---
    try:
        ss = probe.get_score_session()(score_doc)
    except Exception as e:
        return RerunResult(sid, f"failed:score:{e}", error=str(e))

    score_path.write_text(json.dumps(score_doc, indent=2))
    rerun_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "session_id": sid,
        "timestamp": probe._iso_now(),
        "serializer": "plugin/daimon_briefing/serializer.py::serialize_strict",
        "prompt_version": serializer.PROMPT_VERSION,
        "serialize_note": serialize_note,
        "plugin_config": {
            "chunk_lines": plugin_config.chunk_lines(),
            "chunk_overlap": plugin_config.chunk_overlap(),
            "chunk_concurrency": plugin_config.chunk_concurrency(),
            "timeout_seconds": plugin_config.timeout_seconds(),
            "model": plugin_config.llm_model() or "mock/unset",
            "base_url": plugin_config.llm_base_url(),
        },
        "judge_model": getattr(judge_llm, "_model", None) or plugin_config.llm_model() or "mock/unset",
        "judge": {**judge_meta,
                  "staleness_pass": "recalled items vs FULL transcript; "
                                    "doubt -> stale; null -> no stale key",
                  "transcript_ref": f"sessions/{sid}.txt"},
        "rejudged": rejudge,
        "rr": ss.rr, "fmr": ss.fmr, "omr": ss.omr,
        "staleness": ss.staleness, "n_pinnable": ss.n_pinnable, "n_stale": ss.n_stale,
        "n_gt": ss.n_gt, "n_claims": ss.n_claims, "n_false": ss.n_false,
    }
    (rerun_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    status = "rejudged" if rejudge else "done"
    if verbose:
        stale_s = "n/a" if ss.staleness is None else f"{ss.staleness * 100:.1f}%"
        print(f"  [{_ts()}] [{sid}] {status} — RR={ss.rr * 100:.1f}% "
              f"FMR={ss.fmr * 100:.1f}% stale={stale_s}")
    return RerunResult(sid, status, ss.rr, ss.fmr, ss.omr, ss.staleness)


def run_session_cycle2(sid: str, llm_mod, runs_dir=None, force: bool = False,
                       verbose: bool = True) -> RerunResult:
    """One session, cycle 2: runs/<sid>/rerun/reconstruction.md (cycle-1
    output, READ-ONLY) -> shipped serialize -> reconstruct -> rerun-c2/.
    Serialize-only semantics: no judging, no score file — judging is external.
    No fallback to sessions/<sid>.txt: missing cycle-1 input is a named failure."""
    runs_dir = Path(runs_dir) if runs_dir else RUNS_DIR
    session_dir = runs_dir / sid
    c2_dir = session_dir / "rerun-c2"
    cp_path = c2_dir / "checkpoint.json"
    recon_path = c2_dir / "reconstruction.md"
    input_path = session_dir / "rerun" / "reconstruction.md"

    # Resumability: existing cycle-2 serialize artifacts = session done
    # (.scars/0002: check-then-write, do not run two rerun.py concurrently).
    if not force and cp_path.exists() and recon_path.exists():
        if verbose:
            print(f"  [{_ts()}] [{sid}] rerun-c2 artifacts exist — skipping "
                  f"(--cycle2; --force to redo)")
        return RerunResult(sid, "skipped")

    if not input_path.exists():
        msg = (f"cycle-1 artifacts missing: {input_path} — run cycle 1 first "
               f"(e.g. --serialize-only); NOT falling back to sessions/{sid}.txt")
        if verbose:
            print(f"  [{_ts()}] [{sid}] SKIPPED — {msg}")
        return RerunResult(sid, "failed:cycle2:cycle-1 artifacts missing", error=msg)
    cycle1_text = input_path.read_text()

    c2_dir.mkdir(parents=True, exist_ok=True)
    # --- serialize the cycle-1 reconstruction via the SHIPPED plugin path ---
    regenerated = False
    if cp_path.exists() and not force:
        if verbose:
            print(f"  [{_ts()}] [{sid}] reusing existing rerun-c2/checkpoint.json")
        checkpoint = json.loads(cp_path.read_text())
        serialize_note = "resumed: reused existing rerun-c2 checkpoint"
    else:
        n_lines = len(cycle1_text.splitlines())
        if verbose:
            print(f"  [{_ts()}] [{sid}] cycle-2 serializing rerun/reconstruction.md "
                  f"via shipped serialize_strict ({n_lines} lines, threshold "
                  f"{plugin_config.chunk_lines()}) ...")
        t0 = time.monotonic()
        try:
            checkpoint = serialize_cycle2_checkpoint(sid, cycle1_text, llm_mod)
        except serializer.SerializeError as e:
            return RerunResult(sid, f"failed:serialize:{type(e).__name__}: {e}",
                               error=str(e))
        except Exception as e:
            return RerunResult(sid, f"failed:serialize:{e}", error=str(e))
        cp_path.write_text(json.dumps(checkpoint, indent=2))
        regenerated = True
        serialize_note = ("cycle-2: serialized cycle-1 reconstruction via "
                          "plugin serialize_strict (single wrapped user message)")
        if verbose:
            print(f"  [{_ts()}] [{sid}] serialize done in "
                  f"{time.monotonic() - t0:.0f}s")
    # --- reconstruct (02-reconstruct prompt, via probe loader) ---
    if recon_path.exists() and not force and not regenerated:
        if verbose:
            print(f"  [{_ts()}] [{sid}] reusing existing rerun-c2/reconstruction.md")
    else:
        try:
            recon_text, _ = probe._run_reconstruct(checkpoint, llm_mod, verbose=verbose)
        except Exception as e:
            return RerunResult(sid, f"failed:reconstruct:{e}", error=str(e))
        recon_path.write_text(recon_text)

    meta = {
        "session_id": sid,
        "cycle": 2,
        "input": str(input_path),
        "timestamp": probe._iso_now(),
        "serializer": "plugin/daimon_briefing/serializer.py::serialize_strict",
        "prompt_version": serializer.PROMPT_VERSION,
        "serialize_note": serialize_note,
        "plugin_config": {
            "chunk_lines": plugin_config.chunk_lines(),
            "chunk_overlap": plugin_config.chunk_overlap(),
            "chunk_concurrency": plugin_config.chunk_concurrency(),
            "timeout_seconds": plugin_config.timeout_seconds(),
            "model": plugin_config.llm_model() or "mock/unset",
            "base_url": plugin_config.llm_base_url(),
        },
        "serialize_only": True,
    }
    (c2_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    if verbose:
        print(f"  [{_ts()}] [{sid}] cycle-2 serialized — judging is external "
              f"(--cycle2 implies serialize-only)")
    return RerunResult(sid, "serialized")


# ---------------------------------------------------------------------------
# Mock self-test (--mock): full pipeline, mock LLM, isolated runs dir
# ---------------------------------------------------------------------------

# 12 turns (>= DAIMON_MIN_MESSAGES default 10); contains an in-session
# superseded value (40% -> 71%) so Pass 3 has something real to look at.
SYNTHETIC_TRANSCRIPT = """\
User: Let's tune the cache TTL for the API gateway service today.

Assistant: Measured baseline: cache hit rate is 40%. Decision noted: raise TTL to 60s.

User: Run the benchmark again with the new TTL.

Assistant: New measurement: cache hit rate is 71% with TTL 60s, revised from 40%.

User: Should we also bump the connection pool size?

Assistant: Open: verify pool size 20 under load. Not tested yet.

User: What about PR #9?

Assistant: PR #9 is open, merge when CI is green.

User: Anything else pending before we wrap?

Assistant: Decision: keep Redis eviction policy allkeys-lru.

User: Ok wrap it up.

Assistant: Done for today. Next step: run the load test tomorrow morning.
"""

SYNTHETIC_GT = [
    {"id": "gt1", "type": "decision", "trust": "verbatim",
     "text": "Verify the plan: pool size 20 under load",
     "quote": "Open: verify pool size 20 under load."},
    {"id": "gt2", "type": "open_question", "trust": "verbatim",
     "text": "PR #9 open, merge when CI is green",
     "quote": "PR #9 is open, merge when CI is green."},
]


class RerunMockLLM:
    """probe_d007.MockLLM + a deterministic Pass-3 staleness branch."""

    @staticmethod
    def chat(messages, **kwargs):
        user = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user"), "")
        if "RECALLED ITEMS (JSON):" in user:
            start = user.index("RECALLED ITEMS (JSON):") + len("RECALLED ITEMS (JSON):")
            end = user.index("\n\nRECONSTRUCTION TEXT:")
            items = json.loads(user[start:end])
            out = [{"id": it.get("id"), "stale": False} for it in items]
            return json.dumps({"items": out}), {"total_tokens": 10}, "mock-model"
        return probe.MockLLM.chat(messages, **kwargs)

    extract_json = staticmethod(probe.MockLLM.extract_json)


def run_mock_self_test() -> int:
    """End-to-end pipeline on a synthetic session, mock LLM, no network.
    Forces the chunked path (tiny DAIMON_CHUNK_LINES) so the shipped
    chunk->merge->validate machinery runs. Writes runs/_self_test_rerun/."""
    import os
    print("=== rerun self-test (mock LLM, no network) ===")
    os.environ["DAIMON_CHUNK_LINES"] = "8"
    os.environ["DAIMON_CHUNK_OVERLAP"] = "2"
    os.environ["DAIMON_CHUNK_CONCURRENCY"] = "2"

    base = RUNS_DIR / "_self_test_rerun"
    runs_dir = base / "runs"
    sessions_dir = base / "sessions"
    sid = "S_mock"
    (runs_dir / sid).mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / f"{sid}.txt").write_text(SYNTHETIC_TRANSCRIPT)
    (runs_dir / sid / "ground-truth.json").write_text(
        json.dumps({"session_id": sid, "ground_truth_items": SYNTHETIC_GT}, indent=2))

    result = run_session(sid, RerunMockLLM, runs_dir=runs_dir,
                         sessions_dir=sessions_dir, force=True,
                         judge_chunk_lines=8, judge_overlap_lines=2)
    if result.status != "done":
        print(f"SELF-TEST FAILED: {result.status}")
        return 1
    doc = json.loads((runs_dir / sid / f"session-{sid}.rerun.score.json").read_text())
    ss = probe.get_score_session()(doc)
    assert ss.n_gt == len(SYNTHETIC_GT)
    assert all("recalled" in i for i in doc["ground_truth_items"])
    assert all("grounded" in c for c in doc["reconstruction_claims"])
    print(f"\n=== self-test PASSED — artifacts in {base} ===")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Surface the plugin's progress heartbeat (chunk/merge INFO, retry WARNING)
    # on stderr — a doomed multi-hour run must be distinguishable from a healthy
    # one. print-based progress on stdout is untouched.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    ap = argparse.ArgumentParser(
        description="Slice-2 rerun — shipped chunked serializer + Pass 3 "
                    "staleness judge on S1-S5",
    )
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Run every session with a ground-truth.json")
    group.add_argument("--id", dest="sid", metavar="SN", help="Run a single session (e.g. S3)")
    ap.add_argument("--force", action="store_true",
                    help="Redo sessions even if their rerun score file exists")
    ap.add_argument("--rejudge", action="store_true",
                    help="Re-run ONLY the judge passes on existing rerun artifacts "
                         "(no re-serialize — that is the expensive part)")
    ap.add_argument("--serialize-only", action="store_true",
                    help="Serialize + reconstruct only; SKIP all judge passes and "
                         "do not write the score file")
    ap.add_argument("--cycle2", action="store_true",
                    help="2-cycle degradation: feed runs/<id>/rerun/reconstruction.md "
                         "back through the shipped serializer into runs/<id>/rerun-c2/ "
                         "(implies serialize-only semantics; judging is external)")
    ap.add_argument("--mock", action="store_true",
                    help="Mock-LLM end-to-end self-test on a synthetic session (no network)")
    ap.add_argument("--judge-chunk-lines", type=int, default=1200)
    ap.add_argument("--judge-overlap-lines", type=int, default=100)
    ap.add_argument("--judge-model", metavar="MODEL", default=None,
                    help="Model for judge passes (recall, grounding, staleness). "
                         "Overrides env DAIMON_JUDGE_MODEL. When unset or equal to "
                         "the generation model, judge self-judges on the gen model "
                         "(WARNING is emitted).")
    args = ap.parse_args()

    if args.rejudge and args.serialize_only:
        ap.error("--rejudge and --serialize-only are mutually exclusive")
    if args.cycle2 and args.rejudge:
        ap.error("--cycle2 and --rejudge are mutually exclusive "
                 "(cycle 2 never judges — judging is external)")
    if args.cycle2 and args.serialize_only:
        ap.error("--cycle2 and --serialize-only are mutually exclusive "
                 "(--cycle2 already implies serialize-only semantics)")
    if args.mock:
        return run_mock_self_test()
    if not args.all and not args.sid:
        ap.error("one of --all / --id SN is required (or --mock)")

    if args.all:
        sids = sorted(p.stem for p in SESSIONS_DIR.glob("S?.txt")
                      if (RUNS_DIR / p.stem / "ground-truth.json").exists())
    else:
        sids = [args.sid]
    if not sids:
        print("No sessions found (need sessions/SN.txt + runs/SN/ground-truth.json).",
              file=sys.stderr)
        return 2

    # Real run — plugin config (DAIMON_* / LITELLM_* / ~/.daimon/env)
    model = plugin_config.llm_model()
    if not plugin_config.llm_api_key() or not model:
        print("ERROR: no LLM credentials. Set DAIMON_LLM_API_KEY/DAIMON_LLM_MODEL "
              "(or LITELLM_*) in env or ~/.daimon/env.", file=sys.stderr)
        return 2

    # Judge-model resolution (research-local — never touches plugin/daimon_briefing/config.py).
    # Priority: --judge-model flag > DAIMON_JUDGE_MODEL env > None (falls back to gen model).
    import os as _os
    judge_model = args.judge_model or _os.environ.get("DAIMON_JUDGE_MODEL")
    if not judge_model or judge_model == model:
        print(
            f"  WARNING:  judge model unset or equals generation model ({model!r}) — "
            "judge is SELF-JUDGING on the generation model. FMR scores are not "
            "comparable across runs that use different judge models (.scars/landmine-4). "
            "Set --judge-model or DAIMON_JUDGE_MODEL to a stronger model (e.g. "
            "claude-sonnet-4-6-via-meridian) for independent judging.",
            file=sys.stderr,
        )
        judge_model = model  # explicit fallback: use gen model

    gen_llm = GatewayLLM()                  # model=None -> plugin config default
    judge_llm = GatewayLLM(judge_model)     # explicit model for all judge passes

    timeout = plugin_config.timeout_seconds()
    print("Slice-2 Probe Rerun — shipped chunked serializer, n="
          f"{len(sids)} ({', '.join(sids)})")
    print(f"  Model:    {model}  Base: {plugin_config.llm_base_url()}")
    print(f"  Judge:    {judge_model}")
    print(f"  Chunks:   {plugin_config.chunk_lines()} lines / "
          f"{plugin_config.chunk_overlap()} overlap / "
          f"concurrency {plugin_config.chunk_concurrency()}")
    print(f"  Timeout:  {timeout}s per call")
    if timeout < RECOMMENDED_TIMEOUT_S:
        print(f"  WARNING:  DAIMON_TIMEOUT={timeout} < {RECOMMENDED_TIMEOUT_S}s — "
              f"checkpoint generation has taken 248s; set DAIMON_TIMEOUT="
              f"{RECOMMENDED_TIMEOUT_S} (~/.daimon/env) to avoid silent retry death")
    mode = ("REJUDGE (judges only)" if args.rejudge
            else "CYCLE-2 (degradation; serialize-only, judging external)" if args.cycle2
            else "SERIALIZE-ONLY (no judging)" if args.serialize_only else "full")
    print(f"  Mode:     {mode}  Force: {args.force}")
    print(f"  Do NOT run a second rerun.py concurrently (.scars/0002).\n")

    results: list[RerunResult] = []
    for sid in sids:
        print(f"--- {sid} [{_ts()}] ---")
        try:
            if args.cycle2:
                results.append(run_session_cycle2(
                    sid, gen_llm, force=args.force))
            else:
                results.append(run_session(
                    sid, gen_llm, judge_llm=judge_llm, force=args.force,
                    rejudge=args.rejudge,
                    serialize_only=args.serialize_only,
                    judge_chunk_lines=args.judge_chunk_lines,
                    judge_overlap_lines=args.judge_overlap_lines,
                ))
        except Exception as e:  # never let one session kill the batch
            print(f"  [{sid}] UNEXPECTED ERROR: {e}")
            results.append(RerunResult(sid, f"failed:unexpected:{e}", error=str(e)))

    # --- summary ---
    def fmt(x):
        return "  n/a" if x is None else f"{x * 100:5.1f}%"

    header = f"{'session':<10}{'RR':>8}{'FMR':>8}{'stale':>8}  status"
    print(f"\n{'=' * 64}\nRerun summary (shipped serializer)\n{'-' * 64}\n{header}\n{'-' * 64}")
    for r in results:
        print(f"{r.sid:<10}{fmt(r.rr):>8}{fmt(r.fmr):>8}{fmt(r.staleness):>8}  {r.status}")
    print("-" * 64)

    done = [r for r in results if r.status in ("done", "rejudged", "skipped") and r.rr is not None]
    failed = [r for r in results if r.status.startswith("failed")]
    if args.cycle2:
        print("\nCycle-2 artifacts (judging is EXTERNAL — same as the "
              "--serialize-only flow; no score files written):")
        for r in results:
            d = RUNS_DIR / r.sid / "rerun-c2"
            ok = (d / "checkpoint.json").exists() and (d / "reconstruction.md").exists()
            mark = "OK " if ok else "MISSING"
            print(f"  [{mark}] {d}/{{checkpoint.json,reconstruction.md,meta.json}}")
    elif args.serialize_only:
        print("\nJudging SKIPPED (--serialize-only) — no score files written; "
              "re-run without --serialize-only to judge.")
    else:
        print("\nScore files:")
        for r in results:
            p = RUNS_DIR / r.sid / f"session-{r.sid}.rerun.score.json"
            mark = "OK " if p.exists() else "MISSING"
            print(f"  [{mark}] {p}")
        print(f"\nAggregate ({len(done)} scored):\n  {AGGREGATE_CMD}")
        print("Baseline comparison: originals are untouched at "
              "runs/SN/session-SN.score.json — score both globs side by side.")
    if failed:
        print(f"\nWARNING: {len(failed)} session(s) failed — re-invoke the same "
              f"command; finished work is skipped, partial serialize artifacts are reused.")
        for r in failed:
            print(f"  {r.sid}: {r.status}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
