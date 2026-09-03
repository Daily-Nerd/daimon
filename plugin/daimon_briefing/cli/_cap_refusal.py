"""Shared formatter for the #916 over-cap destination sentence (#920).

Three ledgers — requests, refutations/rulings, and amendments — each cap a
handful of text fields at 2000 characters and want the SAME shape of
refusal when a caller trips that cap: name where the long form belongs,
then remind the caller that durable facts belong in a checkpoint, never in
the ledger record. This module holds only that formatting. It imports
nothing from any ledger module, so using it from more than one ledger's CLI
boundary creates no import between the ledgers themselves — each ledger
still owns its own `TooLong` exception type and its own
`_DESTINATION_BY_FIELD` mapping; this is just the one place that turns
`(prefix, exc, ...)` into the same sentence shape everywhere.

`request.py` predates this module (#916) and was left as its own inline
copy rather than retrofitted onto this — the smallest change for #920 was
adding this helper for the two ledgers it covers, not going back to touch a
working, tested surface that was never part of this request.
"""

_CHECKPOINT_HINT = (
    " Durable facts learned while composing this belong in a checkpoint "
    "(the daimon-end skill, `daimon write-checkpoint`), not in the record."
)


def format_cap_refusal(prefix: str, exc: Exception, too_long_type: type,
                       destination_by_field: dict) -> str:
    """`prefix: str(exc)`, plus a destination sentence when `exc` is an
    instance of `too_long_type` on a field present in
    `destination_by_field`. Every other error on the ledger (a
    required-but-empty field, a state refusal, an unknown id shape...)
    keeps exactly its old plain message — this only ever appends text, it
    never replaces `str(exc)`."""
    dest = ""
    if isinstance(exc, too_long_type):
        dest = destination_by_field.get(getattr(exc, "field", ""), "")
        if dest:
            dest += _CHECKPOINT_HINT
    return f"{prefix}: {exc}{dest}"
