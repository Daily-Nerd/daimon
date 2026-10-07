"""One multihash helper (#1132 PR 8b-2): `receipts` mints with it and `view`
reads with it, and neither holds a copy."""

import ast
import base64
import hashlib
from pathlib import Path

from daimon_briefing import multihash, receipts, view


def test_the_hash_is_the_vitni_multibase_sha256():
    """Computed independently of the implementation: "u" + base64url-nopad
    over 0x12 0x20 + the sha2-256 digest."""
    raw = b'{"session_id": "abc"}'
    want = "u" + base64.urlsafe_b64encode(
        bytes([0x12, 0x20]) + hashlib.sha256(raw).digest()).decode().rstrip("=")
    assert multihash.sha256(raw) == want
    assert multihash.wrap(hashlib.sha256(raw).digest()) == want


def test_mint_and_read_use_the_one_helper():
    assert receipts._multibase_sha256 is multihash.sha256
    assert receipts._multibase_wrap is multihash.wrap
    tree = ast.parse(Path(view.__file__).read_text(encoding="utf-8"))
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_multibase_sha256" not in defined
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    assert "hashlib" not in imported
