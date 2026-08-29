import base64
import hashlib
import json
from pathlib import Path

import pytest

from daimon_ui import reader

def test_multihash_matches_the_vitni_format():
    """outputs_hash is multibase base64url-nopad ("u") over multihash sha2-256
    (0x12 0x20 + 32 bytes). Computed independently here rather than by calling
    the implementation, so the test fails if the implementation drifts."""
    raw = b'{"session_id": "abc"}'
    want = "u" + base64.urlsafe_b64encode(
        bytes([0x12, 0x20]) + hashlib.sha256(raw).digest()
    ).decode().rstrip("=")
    assert reader._multihash_b64(raw) == want

def test_multihash_has_no_padding_and_the_u_prefix():
    got = reader._multihash_b64(b"x")
    assert got.startswith("u")
    assert "=" not in got

def _write_pair(root: Path, sid: str, claims: bool, sidecar: str | None):
    """Write a checkpoint and optionally a sidecar whose outputs_hash covers it.
    Returns the checkpoint path. sidecar: None = no file, "match" = true hash,
    "wrong" = a different hash, "garbage" = unparseable, "nokey" = no outputs_hash."""
    cp = {"session_id": sid, "format_version": "D-019"}
    if claims:
        cp["receipts"] = True
    p = root / f"{sid}.json"
    p.write_text(json.dumps(cp), encoding="utf-8")
    if sidecar == "garbage":
        (root / f"{sid}.receipt").write_text("{not json", encoding="utf-8")
    elif sidecar == "nokey":
        (root / f"{sid}.receipt").write_text(json.dumps({"receipt": {}}), encoding="utf-8")
    elif sidecar in ("match", "wrong"):
        h = reader._multihash_b64(p.read_bytes()) if sidecar == "match" else "uEiAwrongwrongwrong"
        (root / f"{sid}.receipt").write_text(
            json.dumps({"receipt": {"outputs_hash": h}}), encoding="utf-8")
    return p

@pytest.mark.parametrize("claims,sidecar,expected", [
    (True,  "match",   "match"),
    (True,  None,      "missing"),
    (True,  "garbage", "missing"),
    (True,  "nokey",   "missing"),
    (True,  "wrong",   "mismatch"),
    (False, None,      "unsigned"),
    (False, "match",   "unsigned"),   # sidecar with no claim: unexplained, stays quiet
])
def test_receipt_state_table(tmp_path, claims, sidecar, expected):
    p = _write_pair(tmp_path, "aaaa-bbbb", claims, sidecar)
    data = json.loads(p.read_text())
    assert reader.receipt_state(tmp_path, data)["state"] == expected

def test_sidecar_missing_only_the_hash_key_is_not_called_unreadable(tmp_path):
    """A sidecar that parses as JSON fine but lacks outputs_hash IS readable — the
    detail must not claim otherwise. Only genuine parse failures get that wording."""
    p = _write_pair(tmp_path, "aaaa-bbbb", True, "nokey")
    got = reader.receipt_state(tmp_path, json.loads(p.read_text()))
    assert got["state"] == "missing"
    assert got["detail"] is None

def test_non_dict_data_is_unsigned_not_a_crash(tmp_path):
    """Docstring says 'Never raises.' A non-dict data payload used to reach
    data.get() unguarded; it must degrade to the quiet 'unsigned' state instead."""
    assert reader.receipt_state(tmp_path, "not a dict")["state"] == "unsigned"
    assert reader.receipt_state(tmp_path, None)["state"] == "unsigned"
    assert reader.receipt_state(tmp_path, [])["state"] == "unsigned"

def test_tampering_one_byte_turns_match_into_mismatch(tmp_path):
    """Injection proof: build a genuinely matching pair, then edit the checkpoint.
    A state machine that reported "match" from the claim alone would stay green."""
    p = _write_pair(tmp_path, "aaaa-bbbb", True, "match")
    assert reader.receipt_state(tmp_path, json.loads(p.read_text()))["state"] == "match"
    p.write_text(p.read_text().replace("D-019", "D-020"), encoding="utf-8")
    assert reader.receipt_state(tmp_path, json.loads(p.read_text()))["state"] == "mismatch"

def test_root_session_file_governs_even_when_a_pointer_copy_differs(tmp_path):
    """Real-world shape: daimon writes several pointer snapshots during one session
    but only binds the receipt to the FINAL root session file's bytes
    (<data_dir>/<session_id>.json). A mid-session pointer copy (e.g. bucket's
    latest.json) legitimately has different bytes and must not shadow the root
    verdict with a false mismatch."""
    sid = "aaaa-bbbb"
    slug = "-proj"
    bucket = tmp_path / slug
    bucket.mkdir()

    root_data = {"session_id": sid, "format_version": "D-019", "receipts": True,
                 "working_context": {}, "epistemic_snapshot": {}}
    root = tmp_path / f"{sid}.json"
    root.write_text(json.dumps(root_data), encoding="utf-8")
    h = reader._multihash_b64(root.read_bytes())
    (tmp_path / f"{sid}.receipt").write_text(
        json.dumps({"receipt": {"outputs_hash": h}}), encoding="utf-8")

    # Pointer copy of the SAME session with DIFFERENT bytes (mid-session snapshot).
    pointer_data = dict(root_data, active_topic_marker="pointer-snapshot-differs")
    (bucket / "latest.json").write_text(json.dumps(pointer_data), encoding="utf-8")

    got = reader.load_checkpoint(tmp_path, slug, "latest")
    assert got["ok"] is True
    assert got["meta"]["receipt"]["state"] == "match"

def test_session_id_cannot_escape_the_data_dir(tmp_path):
    """session_id comes from file content and is joined into a path — twice now
    (sidecar AND root session file). Prove the SESSION_ID_RE guard actually does
    something: point a traversal at files that genuinely EXIST outside data_dir
    and would resolve to a false "match" if the guard were removed. (Verified by
    injection: temporarily deleting the guard makes this test fail; restoring it
    makes it pass again.)"""
    data_dir = tmp_path / "checkpoints"
    data_dir.mkdir()

    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"top": "secret"}), encoding="utf-8")
    h = reader._multihash_b64(secret.read_bytes())
    (tmp_path / "secret.receipt").write_text(
        json.dumps({"receipt": {"outputs_hash": h}}), encoding="utf-8")

    data = {"session_id": "../secret", "receipts": True}
    assert reader.receipt_state(data_dir, data)["state"] != "match"

def test_unreadable_sidecar_names_the_file_in_detail(tmp_path):
    p = _write_pair(tmp_path, "aaaa-bbbb", True, "garbage")
    got = reader.receipt_state(tmp_path, json.loads(p.read_text()))
    assert got["state"] == "missing"
    assert "aaaa-bbbb.receipt" in got["detail"]

def _bucket_with(root: Path, slug: str, claims_by_ref: dict):
    b = root / slug
    b.mkdir(parents=True, exist_ok=True)
    for ref, claims in claims_by_ref.items():
        cp = {"session_id": f"sid-{ref}", "format_version": "D-019",
              "working_context": {}, "epistemic_snapshot": {}}
        if claims:
            cp["receipts"] = True
        (b / f"{ref}.json").write_text(json.dumps(cp), encoding="utf-8")
    return b

def test_gate_opens_when_any_pointer_claims_receipts(tmp_path):
    b = _bucket_with(tmp_path, "-proj", {"latest": False, "prev-1": True})
    assert reader.receipts_enabled(b) is True

def test_gate_stays_shut_when_no_pointer_claims_receipts(tmp_path):
    b = _bucket_with(tmp_path, "-proj", {"latest": False, "prev-1": False})
    assert reader.receipts_enabled(b) is False

def test_gate_ignores_an_unreadable_pointer(tmp_path):
    b = _bucket_with(tmp_path, "-proj", {"latest": True})
    (b / "prev-1.json").write_text("{torn", encoding="utf-8")
    assert reader.receipts_enabled(b) is True

def test_closed_gate_omits_the_receipt_key_entirely(tmp_path):
    _bucket_with(tmp_path, "-proj", {"latest": False})
    got = reader.load_checkpoint(tmp_path, "-proj", "latest")
    assert got["ok"] is True
    assert "receipt" not in got["meta"]

def test_open_gate_reports_state_in_meta(tmp_path):
    _bucket_with(tmp_path, "-proj", {"latest": True})
    got = reader.load_checkpoint(tmp_path, "-proj", "latest")
    assert got["meta"]["receipt"]["state"] == "missing"

def test_gate_survives_a_non_dict_pointer_string(tmp_path):
    """A pointer file holding valid JSON that isn't an object (e.g. a bare
    string) must not crash the scan — .get() would raise AttributeError on it."""
    b = tmp_path / "-proj"
    b.mkdir()
    (b / "latest.json").write_text(json.dumps({"session_id": "a"}), encoding="utf-8")
    (b / "prev-1.json").write_text(json.dumps("just a string"), encoding="utf-8")
    assert reader.receipts_enabled(b) is False

def test_gate_survives_a_non_dict_pointer_list(tmp_path):
    """Same as above but for a JSON list, the other common non-object shape."""
    b = tmp_path / "-proj"
    b.mkdir()
    (b / "latest.json").write_text(json.dumps({"session_id": "a"}), encoding="utf-8")
    (b / "prev-1.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert reader.receipts_enabled(b) is False
