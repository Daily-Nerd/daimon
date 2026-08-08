---
sidebar_position: 4
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
| Fresh verbatim claims fail the quote check at a measurable, published rate | **12.2%** lifetime (450 of 3703 claims, 160 sessions, snapshot 2026-08-07) — [dated row + caveats](./backends-tested.md) | snippet below |
| Every stored verbatim quote stays re-checkable after the fact | exit `0` proven / `1` mismatch found / `3` cannot prove | `daimon audit quotes` |
| A forgotten value is provably gone from every declared surface | same exit contract, hash-only reporting | `daimon audit privacy` |
| Deletion survives re-serializing the original transcript | tombstone suppresses the item on re-capture | `daimon forget <id>`, then `daimon serialize <transcript>`, then `daimon recall` for the value |
| A checkpoint's provenance is offline-checkable (opt-in) | ed25519 signature binding exact bytes to the source transcript | `daimon verify-receipt` |

## Re-running the downgrade rate

The rate is derived from your own checkpoint store — no network, no tooling
beyond Python. Rotated pointer copies duplicate sessions on disk, so
deduplication by session id is mandatory (a naive glob double-counts):

```python
python3 - <<'EOF'
import json, glob, os
def items(o):
    if isinstance(o, dict):
        if "quote_verified" in o: yield o
        for v in o.values(): yield from items(v)
    elif isinstance(o, list):
        for v in o: yield from items(v)
seen=set(); checked=downgraded=0
for p in glob.glob(os.path.expanduser("~/.daimon/checkpoints/**/*.json"), recursive=True):
    try: cp=json.load(open(p, encoding="utf-8"))
    except Exception: continue
    if not isinstance(cp, dict): continue
    sid=cp.get("session_id")
    if not sid or sid in seen: continue
    seen.add(sid)
    for it in items(cp):
        checked+=1
        if it.get("quote_verified") is False: downgraded+=1
print(f"{downgraded}/{checked} downgraded across {len(seen)} sessions"
      + (f" = {downgraded/checked:.1%}" if checked else ""))
EOF
```

What it counts: every item that carries a `quote_verified` stamp — `false`
means the quote failed re-verification at capture time and the item was
demoted to `inferred`. The published 12.2% is a **dated snapshot**; the same
command on the same store four sessions later already reads 11.9% (460/3869,
164 sessions). Your store will read your number, from day one.

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
