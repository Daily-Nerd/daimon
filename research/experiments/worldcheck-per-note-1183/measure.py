"""Worldcheck contradictions per unique note (#1183).

The per-class worldcheck rates quoted so far count CHECKS. A carried note is
checked again on every brief, so one stale note can count many times. This
script counts, per class, in the window the verification ledger covers:

  K  distinct notes contradicted at least once (ledger rows, deduped by the
     ledger's own fold key: bucket, item ref, claim key)
     and the contradiction rows they account for
  U  distinct carried notes that made that kind of claim (id-bearing carried
     claim notes plus the ledger's own notes, so K <= U)

next to the per-check counts from usage.log for the same window. A class with
no ledger rows prints "none recorded", never a ratio.

What it cannot give: confirmed per note. Confirmations are counted without
saying which note they belong to (store.py writes cures only), so that half is
not in the stored data. The script says so and never estimates it.

Read-only: every input is opened for reading and nothing else is touched; the
only file written is --out. Counts only: no bucket name, item id, claim key or
note text is printed or stored.

Run (read-only against the real store):

  cd research/experiments/worldcheck-per-note-1183
  uv run --project ../../../plugin python measure.py

Tests:

  cd plugin && uv run --extra dev pytest ../research/experiments/worldcheck-per-note-1183/ -q

The live store moves under a running session, so `measurements.json` carries
an `inputs` fingerprint of exactly what was read.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3] / "plugin"))

from daimon_briefing import worldcheck  # noqa: E402
from daimon_briefing.normalize import content_key  # noqa: E402
from daimon_briefing.serializer import iter_items  # noqa: E402

ISSUE = 1183
CLASSES = (worldcheck.PR_STATE, worldcheck.FILE_EXISTS, worldcheck.BRANCH_STATE)
CURE_SUFFIX = "-ok"
NONE_RECORDED = "none recorded"
HEX = frozenset("0123456789abcdef")
# One stamp and one worldcheck token. The token may end in the project slug
# (#1184); `worldcheck.parse_usage_token` owns that grammar, so both forms count.
USAGE_RE = re.compile(r"(\S+) (worldcheck:\S+)")
HISTOGRAM = (("1", 1, 1), ("2-4", 2, 4), ("5-9", 5, 9), ("10+", 10, None))
CONFIRMED_PER_NOTE = (
    "not in the stored data: confirmations are counted per class without the "
    "note they belong to, so confirmed per note is not estimated here")


def parse_epoch(value):
    """Epoch seconds for a whole-second or microsecond UTC stamp, else None.
    World rows carry microseconds, usage lines and checkpoints do not, so
    every comparison goes through epochs, never strings."""
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, fmt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _valid_claim_key(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def _read(path):
    """(bytes, size) of a file, or (None, 0) when it cannot be read."""
    try:
        data = path.read_bytes()
    except OSError:
        return None, 0
    return data, len(data)


def _ledger(path):
    """(rows, torn, size): every JSON object line, the count of lines that are
    not one, and the bytes read."""
    data, size = _read(path)
    rows, torn = [], 0
    for line in (data or b"").decode("utf-8", "replace").split("\n"):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            torn += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            torn += 1
    return rows, torn, size


def _world_row(row):
    """(class, is_contradiction, epoch) for a well-formed world ledger row,
    else None. A cure is a world row too: it opens the window the ledger
    covers but is not a contradiction."""
    check = row.get("check")
    if not isinstance(check, str):
        return None
    cls = check.removesuffix(CURE_SUFFIX)
    if cls not in CLASSES or not _valid_claim_key(row.get("claim_key")):
        return None
    epoch = parse_epoch(row.get("ts"))
    if epoch is None:
        return None
    return cls, check == cls and row.get("reason") == "claim-contradicted", epoch


def _carried_claims(checkpoint):
    """(class, item id or None, claim key, target, text key) for every carried
    item in a checkpoint whose text makes a ledgered claim."""
    for item in iter_items(checkpoint):
        if not item.get("carried_from"):
            continue
        found = worldcheck.claim_for(item.get("text"))
        if found is None or found[0] not in CLASSES:
            continue
        cls, claim = found
        ident = item.get("id")
        yield (cls, ident if isinstance(ident, str) and ident else None,
               worldcheck._claim_key(cls, claim), worldcheck._target(cls, claim),
               content_key(item.get("text")))


def _usage(path, since, now):
    """Per-class check counts from anchored usage lines, all time and window,
    plus (lines read, bytes read). Tagged skips never match the anchor."""
    data, size = _read(path)
    lines = (data or b"").decode("utf-8", "replace").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    counts = {c: {"all": {"confirmed": 0, "contradicted": 0},
                  "window": {"confirmed": 0, "contradicted": 0}}
              for c in CLASSES}
    for line in lines:
        m = USAGE_RE.fullmatch(line)
        if m is None:
            continue
        stamp, command = m.groups()
        token = worldcheck.parse_usage_token(command)
        if (token is None or token.cls not in counts
                or token.outcome not in ("confirmed", "contradicted")):
            continue
        cls, outcome = token.cls, token.outcome
        counts[cls]["all"][outcome] += 1
        epoch = parse_epoch(stamp)
        if epoch is not None and _in_window(epoch, since, now):
            counts[cls]["window"][outcome] += 1
    return counts, len(lines), size


def _in_window(epoch, since, now):
    return since is not None and since <= epoch <= now


def _bucket_dirs(root):
    try:
        return sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def scan(checkpoint_dir, usage_log, since=None, now=None):
    """Read the store and the usage log, return the counts. `since` is an
    epoch; None means the earliest world ledger row across buckets, and with
    no world row at all the window is empty."""
    now = time.time() if now is None else now
    buckets = _bucket_dirs(Path(checkpoint_dir))
    pairs = []
    ledgers = []  # (bucket ordinal, rows)
    ledger_files = ledger_rows = torn = 0
    checkpoint_files = readable = 0
    checkpoint_bytes = ledger_bytes = 0
    docs = []  # (bucket ordinal, parsed checkpoint)
    for ordinal, bucket in enumerate(buckets):
        for path in sorted(bucket.glob("*.json")):
            if not path.is_file():
                continue
            data, size = _read(path)
            checkpoint_files += 1
            checkpoint_bytes += size
            pairs.append([ordinal, path.name, size])
            try:
                doc = json.loads(data) if data is not None else None
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                readable += 1
                docs.append((ordinal, doc))
        ledger_path = bucket / "verification.jsonl"
        if ledger_path.is_file():
            rows, bad, size = _ledger(ledger_path)
            ledger_files += 1
            ledger_rows += len(rows)
            torn += bad
            ledger_bytes += size
            pairs.append([ordinal, ledger_path.name, size])
            ledgers.append((ordinal, rows))

    world = [(ordinal, w, row) for ordinal, rows in ledgers for row in rows
             if (w := _world_row(row)) is not None]
    if since is None and world:
        since = min(w[2] for _o, w, _r in world)
    if since is not None:
        # Usage and checkpoint stamps are whole seconds; the window opens on
        # the whole second so a check stamped inside it stays inside.
        since = float(int(since))

    notes = {c: {} for c in CLASSES}  # class -> {(bucket, ref, key): rows}
    for ordinal, (cls, is_contradiction, epoch), row in world:
        ref = row.get("item_ref")
        if (is_contradiction and _in_window(epoch, since, now)
                and isinstance(ref, str) and ref):
            key = (ordinal, ref, row["claim_key"])
            notes[cls][key] = notes[cls].get(key, 0) + 1
    ledger_hits = {c: sum(notes[c].values()) for c in CLASSES}

    carried = {c: set() for c in CLASSES}
    idless = {c: set() for c in CLASSES}
    for ordinal, doc in docs:
        if not _in_window(parse_epoch(doc.get("created")) or -1.0, since, now):
            continue
        for cls, ident, claim_key, target, text_key in _carried_claims(doc):
            if ident is not None:
                carried[cls].add((ordinal, ident, claim_key))
            else:
                idless[cls].add((ordinal, cls, target, text_key))

    usage, usage_lines, usage_bytes = _usage(Path(usage_log), since, now)
    pairs.append([-1, "usage.log", usage_bytes])

    classes = {}
    for cls in CLASSES:
        k = len(notes[cls])
        u = len(carried[cls] | set(notes[cls]))
        recorded = ledger_hits[cls] > 0
        histogram = {label: 0 for label, _lo, _hi in HISTOGRAM}
        for rows in notes[cls].values():
            for label, lo, hi in HISTOGRAM:
                if rows >= lo and (hi is None or rows <= hi):
                    histogram[label] += 1
        window = usage[cls]["window"]
        classes[cls] = {
            "checks_confirmed_all": usage[cls]["all"]["confirmed"],
            "checks_contradicted_all": usage[cls]["all"]["contradicted"],
            "checks_confirmed_window": window["confirmed"],
            "checks_contradicted_window": window["contradicted"],
            "ledger_contradiction_rows_window": ledger_hits[cls],
            "unattributed_window": window["contradicted"] - ledger_hits[cls],
            "notes_contradicted": k if recorded else NONE_RECORDED,
            "max_rows_on_one_note": (max(notes[cls].values())
                                     if recorded else NONE_RECORDED),
            "carried_claim_notes_window": u,
            "k_over_u": k / u if recorded and u else NONE_RECORDED,
            "idless_carried_claim_notes_window": len(idless[cls]),
            "repeat_histogram": histogram,
        }
    fingerprint = hashlib.sha256(
        json.dumps(sorted(pairs), separators=(",", ":")).encode()).hexdigest()
    return {
        "issue": ISSUE,
        "window": {"since": iso(since), "until": iso(now)},
        "inputs": {
            "buckets": len(buckets), "checkpoint_files": checkpoint_files,
            "readable": readable, "checkpoint_bytes": checkpoint_bytes,
            "ledger_files": ledger_files, "ledger_rows": ledger_rows,
            "ledger_bytes": ledger_bytes, "torn_lines": torn,
            "usage_lines": usage_lines, "usage_bytes": usage_bytes,
            "sha256": fingerprint},
        "classes": classes,
        "confirmed_per_note": CONFIRMED_PER_NOTE,
    }


def render(result):
    w, i = result["window"], result["inputs"]
    out = [f"window: {w['since'] or NONE_RECORDED} .. {w['until']} UTC",
           "inputs: " + " ".join(f"{k}={v}" for k, v in i.items()), ""]
    cols = [("class", None),
            ("checks confirmed, all time", "checks_confirmed_all"),
            ("checks contradicted, all time", "checks_contradicted_all"),
            ("checks confirmed, window", "checks_confirmed_window"),
            ("checks contradicted, window", "checks_contradicted_window"),
            ("ledger contradiction rows, window",
             "ledger_contradiction_rows_window"),
            ("unattributed contradiction checks, window", "unattributed_window"),
            ("unique notes contradicted >=1 (K)", "notes_contradicted"),
            ("max rows on one note", "max_rows_on_one_note"),
            ("unique carried claim notes, window (U)",
             "carried_claim_notes_window"),
            ("K / U", "k_over_u"),
            ("id-less carried claim notes, window",
             "idless_carried_claim_notes_window")]
    out.append("| " + " | ".join(name for name, _f in cols) + " |")
    out.append("|" + "---|" * len(cols))
    for cls, row in result["classes"].items():
        cells = [cls] + [_cell(row[f]) for _n, f in cols[1:]]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    out.append("repeat histogram per class (unique contradicted notes by row "
               "count): " + ", ".join(label for label, _l, _h in HISTOGRAM))
    for cls, row in result["classes"].items():
        out.append(f"  {cls}: " + ", ".join(
            str(row["repeat_histogram"][label]) for label, _l, _h in HISTOGRAM))
    out += ["", "confirmed per note: " + result["confirmed_per_note"]]
    return "\n".join(out)


def _cell(value):
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _inside(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def main(argv=None):
    home = Path.home() / ".daimon"
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--checkpoint-dir", type=Path, default=home / "checkpoints")
    p.add_argument("--usage-log", type=Path, default=home / "logs" / "usage.log")
    p.add_argument("--since", help="UTC stamp; default: earliest world ledger row")
    p.add_argument("--out", type=Path, default=_HERE.parent / "measurements.json")
    args = p.parse_args(argv)
    since = parse_epoch(args.since) if args.since else None
    if args.since and since is None:
        p.error("--since must look like 2026-09-25T00:00:00Z")
    out = args.out.resolve()
    if (_inside(out, args.checkpoint_dir.resolve())
            or out == args.usage_log.resolve()):
        p.error("--out must not be a path the script reads")
    result = scan(args.checkpoint_dir, args.usage_log, since=since)
    print(render(result))
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
