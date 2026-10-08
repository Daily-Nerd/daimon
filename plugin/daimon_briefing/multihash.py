"""The vitni outputs_hash encoding, stdlib only: multibase base64url-nopad
("u") over a sha2-256 multihash (multicodec 0x12, length 0x20, digest).
`receipts` mints with it and `view` reads with it, so the two cannot disagree
about what a receipt binds."""

import base64
import hashlib

_MULTIHASH_SHA256 = bytes([0x12, 0x20])  # multicodec sha2-256 + 32-byte length


def wrap(raw32: bytes) -> str:
    """vitni hash/nonce encoding of 32 bytes. Confirmed byte-for-byte against
    the receipt-id-local conformance vector."""
    return "u" + base64.urlsafe_b64encode(
        _MULTIHASH_SHA256 + raw32).decode("ascii").rstrip("=")


def sha256(data: bytes) -> str:
    return wrap(hashlib.sha256(data).digest())
