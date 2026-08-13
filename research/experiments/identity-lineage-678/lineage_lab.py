"""Read-only identity-lineage laboratory for daimon issue #678.

This is research instrumentation, not a product write path. It snapshots source
checkpoint bytes into a temporary directory, proposes typed relationships over
adjacent checkpoints, and writes private review artifacts only to ``--out``.
No proposal is allowed to mutate identity or affect a Daimon consumer.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from daimon_briefing import carry, schema, store


POINTER_NAMES = {"latest.json", "prev-1.json", "prev-2.json"}


@dataclass(frozen=True)
class Occurrence:
    session_id: str
    created: str
    section: str
    field: str
    kind: str
    item_id: str
    text: str
    item: dict

    @property
    def ref(self) -> str:
        return f"{self.session_id}:{self.field}:{self.item_id}"


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _created_epoch(value) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _content_key(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _anchor_key(item: dict) -> str:
    anchor = item.get("anchored_to")
    if not isinstance(anchor, dict) or not anchor:
        return ""
    return json.dumps(anchor, sort_keys=True, separators=(",", ":"))


def _source_ids(item: dict) -> frozenset[str]:
    values = item.get("source_message_ids")
    if not isinstance(values, list):
        return frozenset()
    return frozenset(str(value) for value in values if str(value).strip())


def _quote_is_bound(item: dict) -> bool:
    if item.get("quote_verified") is True:
        return True
    receipt = item.get("quote_provenance")
    return isinstance(receipt, dict) and bool(receipt.get("binding"))


def source_manifest(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discover_checkpoints(root: Path, project_slug: str) -> list[Path]:
    """Find canonical per-session files only; pointer copies are excluded."""
    found = []
    for path in sorted(root.glob("*.json")):
        if path.name in POINTER_NAMES or path.name.startswith("prev-"):
            continue
        checkpoint = _load_json(path)
        if checkpoint and checkpoint.get("project_slug") == project_slug:
            found.append(path)
    return found


def copy_snapshot(paths: list[Path], destination: Path) -> list[dict]:
    checkpoints = []
    for source in paths:
        target = destination / source.name
        shutil.copyfile(source, target)
        checkpoint = _load_json(target)
        if checkpoint:
            checkpoints.append(checkpoint)
    checkpoints.sort(key=lambda cp: (
        _created_epoch(cp.get("created")), str(cp.get("session_id") or "")))
    return checkpoints


def iter_occurrences(checkpoint: dict) -> list[Occurrence]:
    occurrences = []
    sid = str(checkpoint.get("session_id") or "")
    created = str(checkpoint.get("created") or "")
    for field in schema.ITEM_FIELDS:
        if field.singleton:
            continue
        values = (checkpoint.get(field.section) or {}).get(field.key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            item_id = str(item.get("id") or "").strip()
            if not text or not item_id:
                continue
            occurrences.append(Occurrence(
                sid, created, field.section, field.key, field.kind,
                item_id, text, item))
    return occurrences


def _typed_supersedes(previous: Occurrence, current: Occurrence) -> bool:
    links = current.item.get("links")
    if not isinstance(links, list):
        return False
    for link in links:
        if not isinstance(link, dict) or link.get("type") != "supersedes":
            continue
        target = str(link.get("target") or "")
        if target == previous.item_id or _content_key(target) == _content_key(previous.text):
            return True
    return False


def _evidence(previous: Occurrence, current: Occurrence,
              generic: frozenset[str]) -> list[str]:
    methods = []
    if _content_key(previous.text) == _content_key(current.text):
        methods.append("exact-text")
    quote_a = str(previous.item.get("quote") or "").strip()
    quote_b = str(current.item.get("quote") or "").strip()
    if (quote_a and quote_a == quote_b and _quote_is_bound(previous.item)
            and _quote_is_bound(current.item)):
        methods.append("bound-exact-quote")
    if _source_ids(previous.item) & _source_ids(current.item):
        methods.append("shared-source-message")
    anchor_a, anchor_b = _anchor_key(previous.item), _anchor_key(current.item)
    if anchor_a and anchor_a == anchor_b:
        methods.append("exact-anchor")
    rail = carry._match_path(previous.text, current.text, generic)
    if rail:
        methods.append(f"carry-{rail}")
    if _typed_supersedes(previous, current):
        methods.append("typed-supersedes")
    return methods


def _candidate(previous: Occurrence, current: Occurrence, methods: list[str],
               relation: str, state: str = "candidate") -> dict:
    raw = f"{previous.ref}\0{current.ref}\0{relation}".encode("utf-8")
    return {
        "candidate_id": "rel-" + hashlib.sha256(raw).hexdigest()[:16],
        "relation": relation,
        "state": state,
        "matched_by": methods,
        "ambiguous": False,
        "previous": {
            "session_id": previous.session_id,
            "created": previous.created,
            "kind": previous.kind,
            "item_id": previous.item_id,
            "text": previous.text,
        },
        "current": {
            "session_id": current.session_id,
            "created": current.created,
            "kind": current.kind,
            "item_id": current.item_id,
            "text": current.text,
        },
    }


def compare_transition(previous_cp: dict, current_cp: dict) -> tuple[list[dict], dict]:
    previous = iter_occurrences(previous_cp)
    current = iter_occurrences(current_cp)
    prev_ids = {item.item_id for item in previous}
    curr_ids = {item.item_id for item in current}
    previous_by_kind = defaultdict(list)
    current_by_kind = defaultdict(list)
    for item in previous:
        previous_by_kind[item.kind].append(item)
    for item in current:
        current_by_kind[item.kind].append(item)

    candidates = []
    for kind in sorted(set(previous_by_kind) | set(current_by_kind)):
        old_items = previous_by_kind[kind]
        new_items = current_by_kind[kind]
        generic = carry._generic_terms(
            [item.text for item in old_items] + [item.text for item in new_items])
        for old in old_items:
            for new in new_items:
                if old.item_id == new.item_id:
                    if _content_key(old.text) != _content_key(new.text):
                        candidates.append(_candidate(
                            old, new, ["legacy-shared-id"], "revision-of"))
                    continue
                methods = _evidence(old, new, generic)
                if methods:
                    relation = "supersedes" if "typed-supersedes" in methods else "revision-of"
                    candidates.append(_candidate(old, new, methods, relation))

    # Cross-kind transition worth testing: a decision may answer a prior
    # question. It is always a candidate; surface similarity cannot prove the
    # speech act. Strong evidence or the existing absolute carry rail is enough
    # to put it in the review queue, never enough to confirm it.
    old_questions = previous_by_kind["question"]
    new_decisions = current_by_kind["decision"]
    generic = carry._generic_terms(
        [item.text for item in old_questions] + [item.text for item in new_decisions])
    existing = {(c["previous"]["item_id"], c["current"]["item_id"], c["relation"])
                for c in candidates}
    for old in old_questions:
        for new in new_decisions:
            methods = _evidence(old, new, generic)
            strong = {"bound-exact-quote", "shared-source-message", "exact-anchor",
                      "carry-absolute", "typed-supersedes"}
            if not (set(methods) & strong):
                continue
            key = (old.item_id, new.item_id, "answers")
            if key not in existing:
                candidates.append(_candidate(old, new, methods, "answers"))

    endpoint_counts = Counter()
    for candidate in candidates:
        endpoint_counts[("previous", candidate["previous"]["session_id"],
                         candidate["previous"]["item_id"])] += 1
        endpoint_counts[("current", candidate["current"]["session_id"],
                         candidate["current"]["item_id"])] += 1
    for candidate in candidates:
        candidate["ambiguous"] = any((
            endpoint_counts[("previous", candidate["previous"]["session_id"],
                             candidate["previous"]["item_id"])] > 1,
            endpoint_counts[("current", candidate["current"]["session_id"],
                             candidate["current"]["item_id"])] > 1,
        ))

    changed = 0
    old_by_id = {item.item_id: item for item in previous}
    new_by_id = {item.item_id: item for item in current}
    for item_id in prev_ids & curr_ids:
        if _content_key(old_by_id[item_id].text) != _content_key(new_by_id[item_id].text):
            changed += 1
    metrics = {
        "previous_items": len(previous),
        "current_items": len(current),
        "shared_ids": len(prev_ids & curr_ids),
        "added_ids": len(curr_ids - prev_ids),
        "dropped_ids": len(prev_ids - curr_ids),
        "shared_id_text_changes": changed,
    }
    return candidates, metrics


def analyse(checkpoints: list[dict], project_slug: str) -> tuple[list[dict], dict]:
    all_candidates = []
    transition_totals = Counter()
    occurrences = []
    for checkpoint in checkpoints:
        occurrences.extend(iter_occurrences(checkpoint))
    for previous, current in zip(checkpoints, checkpoints[1:]):
        candidates, metrics = compare_transition(previous, current)
        all_candidates.extend(candidates)
        transition_totals.update(metrics)

    by_relation = Counter(c["relation"] for c in all_candidates)
    by_method = Counter(method for c in all_candidates for method in c["matched_by"])
    id_counts = Counter(item.item_id for item in occurrences)
    summary = {
        "issue": 678,
        "mode": "read-only-shadow",
        "project_slug": project_slug,
        "privacy": "aggregate-only summary; candidates.jsonl and review.html are private",
        "checkpoints": len(checkpoints),
        "transitions": max(0, len(checkpoints) - 1),
        "item_occurrences": len(occurrences),
        "unique_item_ids": len(id_counts),
        "single_occurrence_ids": sum(count == 1 for count in id_counts.values()),
        "transition_totals": dict(sorted(transition_totals.items())),
        "candidate_count": len(all_candidates),
        "ambiguous_candidates": sum(c["ambiguous"] for c in all_candidates),
        "candidates_by_relation": dict(sorted(by_relation.items())),
        "evidence_rail_counts": dict(sorted(by_method.items())),
        "consumer_effects": {
            "checkpoint_writes": 0,
            "events_writes": 0,
            "recall_writes": 0,
            "viewer_changes": 0,
        },
    }
    return all_candidates, summary


def _review_html(candidates: list[dict], summary: dict) -> str:
    payload = json.dumps(candidates, ensure_ascii=False).replace("<", "\\u003c")
    safe_summary = html.escape(json.dumps(summary, sort_keys=True))
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Daimon identity review</title>
<style>
:root{{--paper:#090c10;--surface:#0d1117;--raised:#161b22;--border:#30363d;
--muted:#8b949e;--ink:#e6edf3;--accent:#00d4aa;--amber:#e0b466;--danger:#d98f86;
--mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
--sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,system-ui,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);
font:14px/1.5 var(--sans)}}header{{position:sticky;top:0;z-index:2;display:flex;
gap:16px;align-items:center;padding:12px 24px;background:var(--paper);
border-bottom:1px solid #21262d}}h1{{font-size:18px;margin:0}}header p{{margin:0;color:var(--muted)}}
button,select{{font:inherit;color:inherit;background:var(--surface);border:1px solid var(--border);
border-radius:6px;padding:7px 10px}}button{{cursor:pointer}}button:hover{{border-color:var(--muted)}}
button:focus-visible,select:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.toolbar{{margin-left:auto;display:flex;gap:8px;align-items:center}}main{{max-width:1100px;margin:auto;padding:24px}}
.safety{{border-left:3px solid var(--amber);background:var(--surface);padding:12px 16px;margin-bottom:16px}}
.summary{{display:flex;gap:24px;color:var(--muted);padding:10px 0 18px;border-bottom:1px solid #21262d}}
.summary strong{{color:var(--ink);font-family:var(--mono)}}#queue{{display:grid;gap:12px;margin-top:16px}}
.pair{{border:1px solid #21262d;background:var(--surface);border-radius:8px;overflow:hidden}}
.pair.active{{border-color:var(--accent)}}.meta{{display:flex;gap:10px;align-items:center;padding:10px 14px;
border-bottom:1px solid #21262d;color:var(--muted);font-family:var(--mono);font-size:12px}}
.meta .relation{{color:var(--accent)}}.meta .ambiguous{{color:var(--amber)}}.texts{{display:grid;
grid-template-columns:1fr 1fr}}.item{{padding:16px}}.item+ .item{{border-left:1px solid #21262d}}
.item .where{{color:var(--muted);font:12px var(--mono);margin-bottom:8px}}.item p{{margin:0}}
.actions{{display:flex;gap:8px;padding:10px 14px;border-top:1px solid #21262d}}
.actions button.selected{{background:#12372f;border-color:var(--accent)}}.actions button[data-v=\"different\"].selected{{background:#3b2020;border-color:var(--danger)}}
.empty{{padding:40px;text-align:center;color:var(--muted)}}@media(max-width:720px){{header{{align-items:flex-start;flex-wrap:wrap}}
.toolbar{{margin-left:0;width:100%}}.texts{{grid-template-columns:1fr}}.item+.item{{border-left:0;border-top:1px solid #21262d}}
.summary{{flex-wrap:wrap}}}}
</style></head><body>
<header><h1>Daimon identity review</h1><p>Issue #678 · private shadow output</p>
<div class=\"toolbar\"><select id=\"filter\" aria-label=\"Filter candidates\"><option value=\"all\">All candidates</option>
<option value=\"unreviewed\">Unreviewed</option><option value=\"ambiguous\">Ambiguous</option>
<option value=\"revision-of\">revision-of</option><option value=\"answers\">answers</option><option value=\"supersedes\">supersedes</option></select>
<button id=\"export\">Export reviews</button></div></header><main>
<div class=\"safety\"><strong>Nothing here changes Daimon.</strong> These are review candidates only. They do not merge IDs, resolve loops, alter recall, propagate corroboration, or widen deletion.</div>
<div class=\"summary\"><span><strong id=\"visible\">0</strong> visible</span><span><strong id=\"done\">0</strong> reviewed</span>
<span>Keys: <strong>J/K</strong> move · <strong>1</strong> same thread · <strong>2</strong> different · <strong>3</strong> uncertain</span></div>
<div id=\"queue\"></div><script id=\"data\" type=\"application/json\">{payload}</script>
<script>
const rows=JSON.parse(document.getElementById('data').textContent),key='daimon-lineage-{summary.get('source_manifest_before','')[:12]}';
let reviews=JSON.parse(localStorage.getItem(key)||'{{}}'),active=0;
const esc=s=>String(s).replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
function filtered(){{const f=document.getElementById('filter').value;return rows.filter(r=>f==='all'||(f==='unreviewed'&&!reviews[r.candidate_id])||(f==='ambiguous'&&r.ambiguous)||r.relation===f)}}
function render(){{const list=filtered();active=Math.min(active,Math.max(0,list.length-1));document.getElementById('visible').textContent=list.length;
document.getElementById('done').textContent=Object.keys(reviews).length;const q=document.getElementById('queue');if(!list.length){{q.innerHTML='<div class=\"empty\">No candidates in this view.</div>';return}}
q.innerHTML=list.map((r,i)=>`<article class=\"pair ${{i===active?'active':''}}\" data-id=\"${{r.candidate_id}}\"><div class=\"meta\"><span class=\"relation\">${{esc(r.relation)}}</span><span>${{esc(r.matched_by.join(' + '))}}</span>${{r.ambiguous?'<span class=\"ambiguous\">ambiguous endpoints</span>':''}}</div><div class=\"texts\"><section class=\"item\"><div class=\"where\">previous · ${{esc(r.previous.kind)}} · ${{esc(r.previous.session_id)}}</div><p>${{esc(r.previous.text)}}</p></section><section class=\"item\"><div class=\"where\">current · ${{esc(r.current.kind)}} · ${{esc(r.current.session_id)}}</div><p>${{esc(r.current.text)}}</p></section></div><div class=\"actions\">${{[['same','Same thread'],['different','Different'],['uncertain','Uncertain']].map(([v,l])=>`<button data-v=\"${{v}}\" class=\"${{reviews[r.candidate_id]===v?'selected':''}}\">${{l}}</button>`).join('')}}</div></article>`).join('');
q.querySelectorAll('button[data-v]').forEach(b=>b.onclick=()=>{{const id=b.closest('.pair').dataset.id;reviews[id]=b.dataset.v;localStorage.setItem(key,JSON.stringify(reviews));render()}});q.querySelector('.active')?.scrollIntoView({{block:'nearest'}})}}
document.getElementById('filter').onchange=()=>{{active=0;render()}};document.addEventListener('keydown',e=>{{if(['SELECT','BUTTON'].includes(e.target.tagName))return;const list=filtered();if(e.key.toLowerCase()==='j')active=Math.min(active+1,list.length-1);else if(e.key.toLowerCase()==='k')active=Math.max(0,active-1);else if(['1','2','3'].includes(e.key)){{const r=list[active];if(r)reviews[r.candidate_id]={{'1':'same','2':'different','3':'uncertain'}}[e.key],localStorage.setItem(key,JSON.stringify(reviews))}}else return;render()}});
document.getElementById('export').onclick=()=>{{const blob=new Blob([JSON.stringify({{generated_at:new Date().toISOString(),reviews}},null,2)],{{type:'application/json'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='lineage-reviews.json';a.click();URL.revokeObjectURL(a.href)}};render();
</script><details hidden><summary>Aggregate run metadata</summary><pre>{safe_summary}</pre></details></main></body></html>"""


def write_outputs(out_dir: Path, candidates: list[dict], summary: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates),
        encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "review.html").write_text(
        _review_html(candidates, summary), encoding="utf-8")


def run(checkpoint_dir: Path, project: str, out_dir: Path) -> dict:
    project_slug = store.project_slug(project)
    paths = discover_checkpoints(checkpoint_dir, project_slug)
    if len(paths) < 2:
        raise SystemExit(f"need at least 2 checkpoints for {project_slug}; found {len(paths)}")
    before = source_manifest(paths, checkpoint_dir)
    with tempfile.TemporaryDirectory(prefix="daimon-lineage-shadow-") as raw:
        checkpoints = copy_snapshot(paths, Path(raw))
        candidates, summary = analyse(checkpoints, project_slug)
    after = source_manifest(paths, checkpoint_dir)
    summary["source_manifest_before"] = before
    summary["source_manifest_after"] = after
    summary["source_byte_integrity"] = "verified" if before == after else "FAILED"
    if before != after:
        raise RuntimeError("source checkpoint bytes changed during shadow replay")
    write_outputs(out_dir, candidates, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-dir", type=Path,
                        default=Path.home() / ".daimon" / "checkpoints")
    parser.add_argument("--project", default=str(Path.cwd()))
    parser.add_argument("--out", type=Path, default=Path("out-current"))
    args = parser.parse_args()
    summary = run(args.checkpoint_dir.expanduser(), args.project, args.out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

