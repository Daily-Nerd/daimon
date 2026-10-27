---
sidebar_position: 4
description: "Every number daimon publishes about itself ships with the command to reproduce it on your own store. A claim you cannot re-run is a testimonial."
---

# Claims, with re-run methods

Every number daimon publishes about itself ships with the method to reproduce
it. The numbers below describe **our own store** — two months of dogfooding on
one machine. Running the same commands on your install measures **your** store;
that is the point. A claim you cannot re-run is a testimonial, and this page
does not carry testimonials.

One definition rides with everything here: **verified means daimon found this
exact quote in your transcript** — matched after folding case, whitespace, and
formatting, with elided spans allowed. It is a mechanical check, not a truth
claim.

## The claims

| Claim | Published number / guarantee | Re-run with |
| --- | --- | --- |
| Fresh verbatim claims fail the quote check at a measurable, published rate | **11.3%**, fresh items only, over every session the store held on 2026-10-26 (1819 of 16118 across 500 sessions after dedup). Month split below, as context. [Dated row + caveats](./backends-tested.md) | snippet below |
| Every stored verbatim quote stays re-checkable after the fact | exit `0` proven / `1` mismatch found / `3` cannot prove | `daimon audit quotes` |
| A forgotten value is provably gone from every declared surface | same exit contract, hash-only reporting | `daimon audit privacy` |
| Deletion survives re-serializing the original transcript | tombstone suppresses the item on re-capture | `daimon forget <id>`, then `daimon serialize <transcript>`, then `daimon recall` for the value |
| A checkpoint's provenance is offline-checkable (opt-in) | ed25519 signature binding exact bytes to the source transcript | `daimon verify-receipt` |

## Re-running the downgrade rate

The rate is derived from your own checkpoint store — no network, no tooling
beyond Python. Two filters are mandatory. Rotated pointer copies duplicate
sessions on disk, so deduplicate by session id (a naive glob double-counts).
And a checkpoint holds two kinds of item: fresh ones, extracted from this
session's transcript, and carried copies (`carried_from`), brought forward
from an earlier session. A carried copy keeps a `true` stamp it earned when
it was fresh, but carry removes a `false` one. So counting carried copies
adds verified items to the total and almost no failures, which lowers the
rate without measuring anything new. Only fresh items answer the question
the claim asks:

```python
python3 - <<'EOF'
import json, glob, os
from collections import defaultdict
def items(o):
    if isinstance(o, dict):
        if "quote_verified" in o: yield o
        for v in o.values(): yield from items(v)
    elif isinstance(o, list):
        for v in o: yield from items(v)
seen=set(); checked=downgraded=0; month=defaultdict(lambda: [0, 0])
for p in glob.glob(os.path.expanduser("~/.daimon/checkpoints/**/*.json"), recursive=True):
    try: cp=json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if not isinstance(cp, dict): continue
    sid=cp.get("session_id")
    if not sid or sid in seen: continue
    seen.add(sid)
    m=(cp.get("created") or "")[:7]
    for it in items(cp):
        if it.get("carried_from"): continue  # a carried copy keeps a true stamp and loses a false one
        checked+=1; month[m][0]+=1
        if it.get("quote_verified") is False: downgraded+=1; month[m][1]+=1
print(f"{downgraded}/{checked} fresh items downgraded across {len(seen)} sessions"
      + (f" = {downgraded/checked:.1%}" if checked else ""))
for m in sorted(month):
    c, d = month[m]; print(f"  {m or 'undated'}: {d}/{c} = {d/c:.1%}")
EOF
```

What it counts: every fresh item that carries a `quote_verified` stamp.
`false` means the quote failed re-verification at capture time and the item
was demoted to `inferred`. On our store, on 2026-10-26, this reads 11.3%:
1819 of 16118 fresh items across 500 sessions. That is every session the
store still held that day, not a lifetime: by default daimon keeps the newest
100 per-session checkpoints (`DAIMON_CHECKPOINT_KEEP`), plus any a pointer
still references or an item of importance 9 or more pins, so an older store
reads a retained window and its older months are the sessions that survived
pruning. The same store counted the old way, carried copies included, reads
7.3% (1827 of 25035). That is the old method's number, diluted by the carried
copies. A fresh restatement of something an earlier session also
held keeps that earlier session as its `origin_session`; excluding those too
moves it to 11.4% (1770 of 15567), so that filter changes nothing worth a
line in the snippet.

The month split is published as context. It makes no trend claim. July 17.1%
(n=1697), August 12.9% (n=3658), September 12.7% (n=5669), October 6.5%
(n=5084). Hosts, backends and the kind of work differ month to month, and the
split does not separate those from any change in the verifier or the models.
The headline is the number over the whole store. Your store will read your
number, from day one.

## What the audits prove

`daimon audit quotes` re-checks every stored verbatim quote against its source
transcript, read-only. `daimon audit privacy` hashes every plaintext field on
every declared surface and intersects with the deletion ledger. Both share one
[exit contract](./cli.md#check): `0` is proven clean, `1` names the residue by
surface and hash, and `3` means a surface could not be read — never treat `3`
as clean. That last code exists because "could not check" reported as "all
clean" is how audit theater works.

## What is deliberately not on this page

Anything measured once, on a sample too small to state, or not yet
reproducible by a command you can run. When a number graduates to a method,
it moves here with its date.
