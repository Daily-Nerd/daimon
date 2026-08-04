"""#536 join half — walks serialize.log, re-chunks transcripts, loads
cached chunk partials, and feeds airtight joins to the frozen rubric in
classify.py.

Attribution rules (pre-registered on issue #536; a session enters the
population only if ALL hold, failures are counted and excluded, never
partially joined):
  1. exactly one spawn and one successful write in serialize.log
  2. every re-derived chunk has a cached partial; cached count == the
     log's chunk count for that run
  3. every partial's producer receipt matches the checkpoint's
     served-model stamp; checkpoint extraction version matches the
     cache generation
  4. the carry source (previous checkpoint) is on disk when carried
     items exist

Cache keys are reconstructed from the PRODUCING checkpoint's own stamps
(llm_backend, llm_model, extraction_version), never from live config —
the box flipped backends on 08-04 and live-config keys silently drop
7 of 12 joins.

Zero LLM calls. Reads only. The committed artifact is aggregates-only:
chunk partials are pre-redaction by design, so item texts never leave
this process except into the uncommitted local report.
"""
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import classify

DAIMON_ROOT = Path.home() / ".daimon"
LOG_PATH = DAIMON_ROOT / "logs" / "serialize.log"
CHECKPOINT_DIR = DAIMON_ROOT / "checkpoints"
CACHE_DIR = CHECKPOINT_DIR / ".chunk-cache"

_SPAWN_RE = re.compile(
    r"^\S+ [\w-]+: (?:spawned|retry) serialize for (?P<sid>[0-9a-f-]{8,})"
    r".*?(?:\(transcript: (?P<transcript>[^)]+)\))?\s*$")
_WROTE_RE = re.compile(
    r"^wrote checkpoint: (?P<path>\S+?/(?P<sid>[0-9a-f-]{8,})\.json)"
    r"(?: \(took \d+s\))?\s*$")
_CHUNKED_RE = re.compile(
    r"chunked serialize: (?P<n>\d+) chunks from \d+ lines")
_ERROR_RE = re.compile(
    r"^error: .*\(transcript: (?P<path>[^)]*?(?P<sid>[0-9a-f-]{8,})"
    r"\.jsonl?)\)")


@dataclass
class RunRecord:
    sid: str
    spawns: int = 0
    writes: int = 0
    chunk_counts: list = field(default_factory=list)
    transcript_path: str = None
    _window_open: bool = False
    _ambiguous: bool = False

    def eligible(self):
        return (self.spawns == 1 and self.writes == 1
                and len(self.chunk_counts) == 1 and not self._ambiguous)


def attribute_log(lines):
    """Fold serialize.log lines into per-session RunRecords.

    Chunk-count lines carry no session id; they attribute to the single
    session whose spawn->write window is open. Overlapping windows (two
    concurrent serializes) make attribution ambiguous for every window
    open at that moment — those runs are excluded, never guessed.

    Window hygiene, because a poisoned window poisons everything after:
    an `error:` line closes its window (sid recovered from the
    transcript path), and a window still open when a LATER-spawned run
    completes a full spawn->write cycle is a zombie (the serialize died
    silently) and is reaped. Chunk lines emit within seconds of spawn —
    before any LLM wait — so a genuinely live long run has already
    claimed its count by the time a later cycle could reap it."""
    runs = {}
    open_windows = {}  # sid -> spawn order index
    spawn_seq = 0
    for line in lines:
        m = _SPAWN_RE.match(line)
        if m:
            sid = m.group("sid")
            r = runs.setdefault(sid, RunRecord(sid=sid))
            r.spawns += 1
            if m.group("transcript"):
                r.transcript_path = m.group("transcript")
            spawn_seq += 1
            open_windows[sid] = spawn_seq
            continue
        m = _WROTE_RE.match(line)
        if m:
            sid = m.group("sid")
            r = runs.setdefault(sid, RunRecord(sid=sid))
            r.writes += 1
            born = open_windows.pop(sid, None)
            if born is not None:
                # reap zombies: windows spawned before this completed
                # run that never wrote are dead processes
                for other, other_born in list(open_windows.items()):
                    if other_born < born:
                        del open_windows[other]
            continue
        m = _ERROR_RE.match(line)
        if m:
            open_windows.pop(m.group("sid"), None)
            continue
        m = _CHUNKED_RE.search(line)
        if m:
            if len(open_windows) == 1:
                r = runs[next(iter(open_windows))]
                r.chunk_counts.append(int(m.group("n")))
                if len(r.chunk_counts) > 1:
                    r._ambiguous = True
            else:
                # zero or multiple candidate runs: attribute to nobody,
                # and poison every open window — the count cannot be
                # trusted for any of them
                for sid in open_windows:
                    runs[sid]._ambiguous = True
    return runs


def cache_key_for(chunk_text, backend, model, temperature,
                  extraction_version, scene, lane="default"):
    """Reconstruct serializer._chunk_cache_key with explicit stamp inputs
    (serializer.py:1613 — v2 stamp, NUL-separated), so keys come from the
    producing checkpoint's fields, not this process's config."""
    scene_s = "scene" if scene else ""
    stamp = (f"v2\x00{backend}\x00{model or ''}"
             f"\x00{temperature}\x00{extraction_version}"
             f"\x00{scene_s}\x00{lane}\x00")
    return hashlib.sha256(
        stamp.encode("utf-8") + chunk_text.encode("utf-8")).hexdigest()[:32]


def checkpoint_extraction_version(cp):
    """Absent extraction_version means pre-#514 stamp era; the cache
    generation that hits for those checkpoints is 2 (pre-#527)."""
    return cp.get("extraction_version", 2)


def served_model_ok(envelope_model, checkpoint_served):
    """Rule 3: producer receipt vs the checkpoint's llm_model_served.
    Both-null is the honest-absence command-backend case (scar 0032);
    any one-sided null is unattributable."""
    return envelope_model == checkpoint_served


_SECTIONS = (("working_context", ("open_questions", "recent_decisions")),
             ("epistemic_snapshot", ("strong_beliefs", "uncertainties",
                                     "contradictions_flagged")))


def _iter_items(cp):
    for section, kinds in _SECTIONS:
        for kind in kinds:
            for item in (cp.get(section) or {}).get(kind, []) or []:
                if isinstance(item, dict) and item.get("text"):
                    yield item


def native_items(cp):
    return [classify.NativeItem(text=i["text"],
                                trust=i.get("trust", "untagged"))
            for i in _iter_items(cp) if not i.get("carried_from")]


def union_items(partials):
    out = []
    for idx, p in enumerate(partials):
        for i in _iter_items(p):
            out.append(classify.UnionItem(text=i["text"], chunk=idx,
                                          trust=i.get("trust", "untagged")))
    return out


def prev_verbatim_pool(prev_cp):
    return [i["text"] for i in _iter_items(prev_cp)
            if i.get("trust") == "verbatim"]


def predecessor_id(cp):
    hops = Counter(i["carried_from"] for i in _iter_items(cp)
                   if i.get("carried_from"))
    if not hops:
        return None
    return hops.most_common(1)[0][0]


# ---- disk walking (thin, untested — the run itself is the test) ----------

def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def join_session(sid, run, config, serializer, transcript_mod):
    """Attempt one airtight join. Returns ("joined", payload) or
    ("excluded:<rule>", detail)."""
    cp = _load_json(CHECKPOINT_DIR / f"{sid}.json")
    if cp is None:
        return "excluded:no-flat-checkpoint", sid
    if not run.eligible():
        if run.spawns != 1:
            reason = "excluded:rule1-multi-spawn"
        elif run.writes != 1:
            reason = "excluded:rule1-no-write"
        elif run._ambiguous:
            reason = "excluded:rule1-ambiguous-window"
        else:
            # no chunked-serialize line: single-pass run, out of scope
            # (no merge happened) — reported apart from log failures
            reason = "excluded:single-pass"
        return reason, (run.spawns, run.writes, run.chunk_counts)

    tpath = run.transcript_path
    if not tpath or not Path(tpath).exists():
        hits = list(Path.home().glob(f".claude/projects/*/{sid}.jsonl"))
        if len(hits) != 1:
            return "excluded:no-transcript", sid
        tpath = str(hits[0])
    if transcript_mod.file_sha256(tpath) != cp.get("transcript_hash"):
        return "excluded:transcript-mutated", sid

    messages = transcript_mod.from_file(tpath)
    text = serializer._render_transcript(messages)
    chunks = serializer.chunk_transcript(text, config.chunk_lines(),
                                         config.chunk_overlap())
    if len(chunks) != run.chunk_counts[0]:
        return "excluded:rule2-chunk-count", (len(chunks),
                                              run.chunk_counts[0])

    partials = []
    for chunk in chunks:
        key = cache_key_for(
            chunk_text=chunk,
            backend=cp.get("llm_backend", "unknown"),
            model=cp.get("llm_model"),
            temperature=config.llm_temperature(),
            extraction_version=checkpoint_extraction_version(cp),
            scene=config.scene_traces_enabled())
        entry = _load_json(CACHE_DIR / f"{key}.json")
        if entry is None or "partial" not in entry:
            return "excluded:rule2-cache-miss", sid
        if not served_model_ok(entry.get("served_model"),
                               cp.get("llm_model_served")):
            return "excluded:rule3-producer", (entry.get("served_model"),
                                               cp.get("llm_model_served"))
        partials.append(entry["partial"])

    prev_sid = predecessor_id(cp)
    prev_pool = []
    if prev_sid is not None:
        prev_cp = _load_json(CHECKPOINT_DIR / f"{prev_sid}.json")
        if prev_cp is None:
            return "excluded:rule4-no-predecessor", prev_sid
        prev_pool = prev_verbatim_pool(prev_cp)

    result = classify.classify(native=native_items(cp),
                               union=union_items(partials),
                               prev_verbatim=prev_pool)
    return "joined", (cp, result)


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                           / "plugin"))
    from daimon_briefing import config, serializer, transcript as transcript_mod

    runs = attribute_log(LOG_PATH.read_text(encoding="utf-8").splitlines())
    exclusions = Counter()
    joins = []
    for sid, run in sorted(runs.items()):
        status, payload = join_session(sid, run, config, serializer,
                                       transcript_mod)
        if status == "joined":
            joins.append((sid, *payload))
        else:
            exclusions[status] += 1
        print(f"{sid[:8]}  {status}")

    print(f"\njoined: {len(joins)}   excluded: {dict(exclusions)}")

    # pooled rates
    tot = Counter()
    per_session = []
    for sid, cp, r in joins:
        tot.update(survived=r.survived, reworded=r.reworded,
                   freeze_explained=r.freeze_explained,
                   emitted_new=r.emitted_new, true_lost=r.true_lost,
                   union=r.union_total, native=r.native_total,
                   twin_items=r.twin_items,
                   containment_flags=r.containment_flags)
        for k, v in r.true_lost_by_trust.items():
            tot[f"lost_trust_{k}"] += v
        for k, v in r.emitted_new_by_trust.items():
            tot[f"new_trust_{k}"] += v
        per_session.append({
            "session": sid[:8],
            "native": r.native_total, "union": r.union_total,
            "survived": r.survived, "reworded": r.reworded,
            "freeze_explained": r.freeze_explained,
            "emitted_new": r.emitted_new, "true_lost": r.true_lost,
            "twin_items": r.twin_items,
            "containment_flags": r.containment_flags,
        })

    n_union = tot["union"]
    lost_ci = classify.wilson(tot["true_lost"], n_union)
    twin_ci = classify.wilson(tot["twin_items"], n_union)
    summary = {
        "joined_sessions": len(joins),
        "exclusions": dict(exclusions),
        "pooled": dict(tot),
        "true_lost_rate": tot["true_lost"] / n_union if n_union else None,
        "true_lost_wilson95": lost_ci,
        "twin_rate": tot["twin_items"] / n_union if n_union else None,
        "twin_wilson95": twin_ci,
        "per_session": per_session,
    }
    out = Path(__file__).parent / "run-output.local.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "per_session"}, indent=2))
    print(f"\nfull per-session detail: {out} (LOCAL ONLY, not committed)")


if __name__ == "__main__":
    main()
