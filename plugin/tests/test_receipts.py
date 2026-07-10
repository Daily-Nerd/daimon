"""Signed provenance receipts (#204).

CI has NO node and NO vitni CLI, so every test that needs the signer stubs it
with a fake executable (a python script echoing canned JSON, capturing the
stdin it received so the input contract can be asserted). openssl-dependent key
derivation is exercised in one real test (skipped where openssl lacks Ed25519,
e.g. macOS LibreSSL) plus a subprocess-monkeypatched test that proves the SPKI
byte-slice logic without any real openssl.

Ground-truth fixtures (validated end-to-end against the real vitni CLI):
  seed  = bytes(range(32))
  pub x = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
  SPKI  = 302a300506032b657003210003a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8
The multihash wrapper is checked against the receipt-id-local conformance vector.
"""

import base64
import hashlib
import json
import stat
import subprocess
from pathlib import Path

import pytest

from daimon_briefing import briefing, cli, config, receipts, store

_SEED = bytes(range(32))
_SEED_B64 = base64.b64encode(_SEED).decode("ascii")
_PUB_X = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
_SPKI_HEX = ("302a300506032b657003210003a107bff3ce10be1d70dd18e74bc09967"
             "e4d6309ba50d5f1ddc8664125531b8")

# A real sha256 hex digest (of b"") for transcript_hash wiring.
_TSCRIPT_HEX = hashlib.sha256(b"hello transcript").hexdigest()


# ---- fake vitni CLI --------------------------------------------------------

_FAKE_CLI_SRC = r'''#!/usr/bin/env python3
import sys, json, os
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
raw = sys.stdin.read()
cap = os.environ.get("FAKE_VITNI_CAPTURE")
if cap:
    with open(cap, "a") as f:
        f.write(json.dumps({"cmd": cmd, "stdin": raw}) + "\n")
mode = os.environ.get("FAKE_VITNI_MODE", "ok")
if mode == "garbage":
    sys.stdout.write("not json at all")
    sys.exit(0)
if mode == "rc1":
    sys.stderr.write("boom")
    sys.exit(1)
if mode == "hang":
    import time
    time.sleep(30)
try:
    data = json.loads(raw)
except ValueError:
    print(json.dumps({"error": "invalid_json"})); sys.exit(0)
if cmd == "sign":
    print(json.dumps({"signed_receipt": os.environ.get("FAKE_VITNI_JWS", "aaa.bbb.ccc")}))
elif cmd == "verify":
    verdict = os.environ.get("FAKE_VITNI_VERDICT", "ok")
    if verdict == "ok":
        print(json.dumps({"valid": True, "reason": "ok"}))
    else:
        print(json.dumps({"valid": False, "reason": verdict}))
else:
    print(json.dumps({"error": "unknown_command"}))
sys.exit(0)
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Install a fake vitni CLI on DAIMON_VITNI_CLI + capture file. Returns the
    capture-path so a test can assert what daimon actually sent on stdin."""
    script = tmp_path / "fake-vitni"
    script.write_text(_FAKE_CLI_SRC)
    script.chmod(0o755)
    capture = tmp_path / "vitni-capture.jsonl"
    monkeypatch.setenv("DAIMON_VITNI_CLI", str(script))
    monkeypatch.setenv("FAKE_VITNI_CAPTURE", str(capture))
    return capture


@pytest.fixture
def keys_ready(tmp_path, monkeypatch):
    """Pre-seed the keys dir with the ground-truth seed + pubkey so mint/verify
    tests never touch real openssl (macOS LibreSSL lacks Ed25519)."""
    kdir = tmp_path / "keys"
    kdir.mkdir()
    (kdir / "signing.seed").write_text(_SEED_B64)
    (kdir / "signing.seed").chmod(0o600)
    (kdir / "signing.pub.json").write_text(json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _PUB_X, "alg": "EdDSA",
         "status": "active"}))
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(kdir))
    return kdir


def _capture_lines(capture: Path):
    return [json.loads(x) for x in capture.read_text().splitlines() if x.strip()]


# ---- hash wrappers ---------------------------------------------------------

def test_multibase_wrapper_matches_conformance_vector():
    # receipt-id/local-binding vector: sha256 over the canonical bytes, wrapped.
    canon_hex = (
        "7b22616374696f6e5f726566223a6e756c6c2c2262696e64696e67223a226c6f63"
        "616c222c22636f7374223a7b227261696c5f726566223a6e756c6c2c22746f6b656e"
        "73223a223130222c227573645f6d6963726f73223a2230222c2277616c6c5f6d7322"
        "3a2233227d2c22696e707574735f68617368223a227545694173386b32365837436a"
        "4469626f4f79724675654b654778596558422d6e516c357a42444e696b3475594a41"
        "222c226c6f675f706f6c696379223a22626573745f6566666f7274222c226d657468"
        "6f64223a226c6f63616c3a6461696d6f6e2e73657269616c697a65222c226e6f6e63"
        "65223a22754569446a734d52436d507763464a7237394d695a62376b6b4a36354235"
        "4753626b30796b6c5a6b6265464b345651222c226f7574707574735f68617368223a"
        "227545694173386b32365837436a4469626f4f79724675654b654778596558422d6e"
        "516c357a42444e696b3475594a41222c22706172656e745f726563656970745f6861"
        "7368223a6e756c6c2c22706572666f726d65725f6964223a227372762d64656d6f22"
        "2c22726561736f6e223a6e756c6c2c227265717565737465725f6964223a6e756c6c"
        "2c22737461747573223a224f4b222c227473223a22323032362d30352d3238543030"
        "3a30303a30305a222c2276223a227669746e692f302e32227d")
    canon = bytes.fromhex(canon_hex)
    assert receipts._multibase_sha256(canon) == (
        "uEiAYMKB9HXrG3NWrXdWzPAFmwDQrVKxjAczQzFVi_DdWJw")


def test_wrap_hex_sha256_roundtrip():
    hexd = hashlib.sha256(b"x").hexdigest()
    wrapped = receipts._wrap_hex_sha256(hexd)
    assert wrapped == receipts._multibase_sha256(b"x")
    assert wrapped.startswith("uEi")


def test_wrap_hex_sha256_rejects_non_digest():
    assert receipts._wrap_hex_sha256("not-hex") is None
    assert receipts._wrap_hex_sha256("ab") is None  # too short
    assert receipts._wrap_hex_sha256(None) is None


def test_nonce_is_wrapped_32_random_bytes():
    n1, n2 = receipts._nonce(), receipts._nonce()
    assert n1 != n2
    assert n1.startswith("uEi")
    # decode and confirm 0x12 0x20 multihash prefix + 32 bytes
    raw = base64.urlsafe_b64decode(n1[1:] + "=" * (-len(n1[1:]) % 4))
    assert raw[:2] == bytes([0x12, 0x20]) and len(raw) == 34


# ---- key derivation --------------------------------------------------------

def test_derive_pubkey_slice_logic(monkeypatch):
    """SPKI byte-slice + b64url encoding is correct, proven WITHOUT real openssl
    by feeding the known SPKI DER back as openssl's stdout."""
    def fake_run(cmd, **kw):
        assert cmd[0] == "openssl"
        return subprocess.CompletedProcess(cmd, 0, stdout=bytes.fromhex(_SPKI_HEX),
                                           stderr=b"")
    monkeypatch.setattr(receipts.subprocess, "run", fake_run)
    assert receipts._derive_pubkey_x(_SEED) == _PUB_X


def test_derive_pubkey_real_openssl():
    """Real openssl round-trip when available (skips on Ed25519-less LibreSSL)."""
    x = receipts._derive_pubkey_x(_SEED)
    if x is None:
        pytest.skip("openssl lacks Ed25519 (e.g. macOS LibreSSL)")
    assert x == _PUB_X


def test_derive_pubkey_openssl_absent_returns_none(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("openssl")
    monkeypatch.setattr(receipts.subprocess, "run", boom)
    assert receipts._derive_pubkey_x(_SEED) is None


def test_ensure_seed_creates_0600(tmp_path, monkeypatch):
    kdir = tmp_path / "keys"
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(kdir))
    seed = receipts._ensure_seed(config.keys_dir())
    assert isinstance(seed, bytes) and len(seed) == 32
    seed_file = kdir / "signing.seed"
    assert seed_file.exists()
    assert stat.S_IMODE(seed_file.stat().st_mode) == 0o600
    # idempotent — a second call returns the same seed, no regen
    assert receipts._ensure_seed(config.keys_dir()) == seed


# ---- plan_mint gating ------------------------------------------------------

def test_plan_mint_none_when_gate_off(keys_ready, fake_cli):
    cp = {"transcript_hash": _TSCRIPT_HEX, "author": "alice"}
    assert receipts.plan_mint(cp) is None  # DAIMON_RECEIPTS not set


def test_plan_mint_none_without_transcript_hash(keys_ready, fake_cli, monkeypatch):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    assert receipts.plan_mint({"author": "alice"}) is None


def test_plan_mint_none_when_cli_missing(keys_ready, monkeypatch):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("DAIMON_VITNI_CLI", "definitely-not-on-path-xyz")
    cp = {"transcript_hash": _TSCRIPT_HEX, "author": "alice"}
    assert receipts.plan_mint(cp) is None


def test_plan_mint_ok(keys_ready, fake_cli, monkeypatch):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    cp = {"transcript_hash": _TSCRIPT_HEX, "author": "alice"}
    plan = receipts.plan_mint(cp)
    assert plan is not None
    assert plan["performer_id"] == "alice"
    assert plan["inputs_hash"] == receipts._wrap_hex_sha256(_TSCRIPT_HEX)


# ---- mint ------------------------------------------------------------------

def _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-r", extra=None):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    cp = {"session_id": session, "transcript_hash": _TSCRIPT_HEX,
          "created": "2026-07-09T00:00:00Z",
          "working_context": {"recent_decisions": [
              {"text": "d1", "trust": "verbatim", "quote": "q1"}]}}
    if extra:
        cp.update(extra)
    path = store.write_checkpoint(session, cp)
    return path


def test_mint_writes_sidecar_and_era_marker(tmp_checkpoint_dir, monkeypatch,
                                             keys_ready, fake_cli):
    monkeypatch.setenv("FAKE_VITNI_JWS", "hdr.pay.sig")
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli)
    # era marker is inside the written checkpoint (signed bytes)
    written = json.loads(path.read_text())
    assert written["receipts"] is True
    # sidecar exists, NO .json suffix
    sidecar = path.with_suffix(".receipt")
    assert sidecar.exists()
    sc = json.loads(sidecar.read_text())
    assert sc["jws"] == "hdr.pay.sig"
    assert sc["kid"] == "daimon-1"
    assert sc["performer_id"]
    # receipt fields
    r = sc["receipt"]
    assert r["v"] == "vitni/0.2"
    assert r["binding"] == "local"
    assert r["method"] == "local:daimon.serialize"
    assert r["inputs_hash"] == receipts._wrap_hex_sha256(_TSCRIPT_HEX)
    assert r["outputs_hash"] == receipts._multibase_sha256(path.read_bytes())
    assert r["ts"] == "2026-07-09T00:00:00Z"
    assert r["cost"]["tokens"] == "0"
    assert r["status"] == "OK"


def test_mint_sign_stdin_contract(tmp_checkpoint_dir, monkeypatch, keys_ready,
                                  fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli)
    lines = _capture_lines(fake_cli)
    signs = [x for x in lines if x["cmd"] == "sign"]
    assert len(signs) == 1
    payload = json.loads(signs[0]["stdin"])
    assert set(payload) == {"receipt", "kid", "private_key_b64"}
    assert payload["kid"] == "daimon-1"
    assert payload["private_key_b64"] == _SEED_B64
    assert payload["receipt"]["binding"] == "local"


def test_gate_off_no_side_effects(tmp_checkpoint_dir, keys_ready, fake_cli):
    # DAIMON_RECEIPTS unset — no marker, no sidecar, no CLI call
    cp = {"session_id": "S-off", "transcript_hash": _TSCRIPT_HEX,
          "created": "2026-07-09T00:00:00Z"}
    path = store.write_checkpoint("S-off", cp)
    assert "receipts" not in json.loads(path.read_text())
    assert not path.with_suffix(".receipt").exists()
    assert not fake_cli.exists()  # capture file never created -> CLI never ran


def test_absent_transcript_hash_skips_mint(tmp_checkpoint_dir, monkeypatch,
                                           keys_ready, fake_cli):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    cp = {"session_id": "S-noh", "created": "2026-07-09T00:00:00Z"}
    path = store.write_checkpoint("S-noh", cp)
    assert "receipts" not in json.loads(path.read_text())
    assert not path.with_suffix(".receipt").exists()


def test_cli_garbage_is_fail_open(tmp_checkpoint_dir, monkeypatch, keys_ready,
                                  fake_cli):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("FAKE_VITNI_MODE", "garbage")
    cp = {"session_id": "S-g", "transcript_hash": _TSCRIPT_HEX,
          "created": "2026-07-09T00:00:00Z"}
    path = store.write_checkpoint("S-g", cp)  # must NOT raise
    assert path.exists()  # serialize/write still succeeded
    assert not path.with_suffix(".receipt").exists()  # no sidecar minted


def test_cli_rc1_is_fail_open(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("FAKE_VITNI_MODE", "rc1")
    cp = {"session_id": "S-e", "transcript_hash": _TSCRIPT_HEX,
          "created": "2026-07-09T00:00:00Z"}
    path = store.write_checkpoint("S-e", cp)
    assert path.exists()
    assert not path.with_suffix(".receipt").exists()


def test_openssl_absent_no_mint_write_succeeds(tmp_checkpoint_dir, monkeypatch,
                                               fake_cli, tmp_path):
    # Fresh keys dir (no pre-seed) + openssl broken -> no pubkey -> no mint.
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("DAIMON_KEYS_DIR", str(tmp_path / "freshkeys"))
    monkeypatch.setattr(receipts.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    cp = {"session_id": "S-noss", "transcript_hash": _TSCRIPT_HEX,
          "created": "2026-07-09T00:00:00Z"}
    path = store.write_checkpoint("S-noss", cp)
    assert path.exists()
    assert "receipts" not in json.loads(path.read_text())
    assert not path.with_suffix(".receipt").exists()


# ---- GC interaction --------------------------------------------------------

def test_gc_never_eats_receipts_and_they_dont_count(tmp_checkpoint_dir,
                                                    monkeypatch, keys_ready,
                                                    fake_cli):
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    monkeypatch.setenv("DAIMON_CHECKPOINT_KEEP", "1")
    # Two checkpoints; KEEP=1 GCs the older per-session .json but must keep BOTH
    # .receipt sidecars invisible to GC, and the sidecars must not count as
    # checkpoint files toward the window.
    for i, sid in enumerate(("S-a", "S-b")):
        cp = {"session_id": sid, "transcript_hash": _TSCRIPT_HEX,
              "created": f"2026-07-0{i + 1}T00:00:00Z"}
        store.write_checkpoint(sid, cp)
    d = config.checkpoint_dir()
    # _session_files must exclude .receipt entirely
    names = {p.name for p in store._session_files(d)}
    assert not any(n.endswith(".receipt") for n in names)
    # both receipt sidecars survive GC
    assert (d / "S-a.receipt").exists()
    assert (d / "S-b.receipt").exists()


# ---- verify-receipt --------------------------------------------------------

def test_verify_receipt_verified(tmp_checkpoint_dir, monkeypatch, keys_ready,
                                 fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-v")
    rc, lines = receipts.verify_receipt("S-v")
    assert rc == 0, lines
    assert any("verified" in x for x in lines)
    # verify stdin contract
    verifies = [x for x in _capture_lines(fake_cli) if x["cmd"] == "verify"]
    assert verifies
    vin = json.loads(verifies[-1]["stdin"])
    assert vin["policy"]["expected_binding"] == "local"
    assert vin["policy"]["expected_method"] == "local:daimon.serialize"
    performer = list(vin["keys"])[0]
    assert "daimon-1" in vin["keys"][performer]


def test_verify_receipt_tampered_file(tmp_checkpoint_dir, monkeypatch, keys_ready,
                                      fake_cli):
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                           session="S-t")
    # edit the checkpoint file after signing
    data = json.loads(path.read_text())
    data["tampered"] = True
    path.write_text(json.dumps(data, indent=2))
    rc, lines = receipts.verify_receipt("S-t")
    assert rc == 1
    assert any("outputs_hash" in x or "edited" in x for x in lines)


def test_verify_receipt_missing_sidecar_pre_receipt(tmp_checkpoint_dir):
    # ordinary checkpoint, no marker, no sidecar -> unable, calm (rc 2)
    store.write_checkpoint("S-pre", {"session_id": "S-pre"})
    rc, lines = receipts.verify_receipt("S-pre")
    assert rc == 2
    assert any("pre-receipt" in x for x in lines)


def test_verify_receipt_marked_but_sidecar_gone(tmp_checkpoint_dir, monkeypatch,
                                                 keys_ready, fake_cli):
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                           session="S-del")
    path.with_suffix(".receipt").unlink()  # someone removed the receipt
    rc, lines = receipts.verify_receipt("S-del")
    assert rc == 1
    assert any("missing" in x.lower() for x in lines)


def test_verify_receipt_no_cli(tmp_checkpoint_dir, monkeypatch, keys_ready,
                               fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-nocli")
    monkeypatch.setenv("DAIMON_VITNI_CLI", "definitely-not-on-path-xyz")
    rc, lines = receipts.verify_receipt("S-nocli")
    assert rc == 2  # unable — bytes match but no CLI for full crypto


def test_verify_receipt_signature_rejected(tmp_checkpoint_dir, monkeypatch,
                                            keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-badsig")
    monkeypatch.setenv("FAKE_VITNI_VERDICT", "bad_signature")
    rc, lines = receipts.verify_receipt("S-badsig")
    assert rc == 1
    assert any("bad_signature" in x for x in lines)


# ---- brief-time degrade ----------------------------------------------------

def test_verbatim_degraded_false_pre_receipt(tmp_checkpoint_dir):
    store.write_checkpoint("S-p", {"session_id": "S-p"})
    cp = store.read_checkpoint("S-p")
    assert receipts.verbatim_degraded(cp) is False


def test_verbatim_degraded_false_when_intact(tmp_checkpoint_dir, monkeypatch,
                                             keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-ok")
    cp = store.read_checkpoint("S-ok")
    assert receipts.verbatim_degraded(cp) is False


def test_verbatim_degraded_true_sidecar_gone(tmp_checkpoint_dir, monkeypatch,
                                             keys_ready, fake_cli):
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                           session="S-gone")
    path.with_suffix(".receipt").unlink()
    cp = store.read_checkpoint("S-gone")
    assert receipts.verbatim_degraded(cp) is True


def test_verbatim_degraded_true_on_hash_mismatch(tmp_checkpoint_dir, monkeypatch,
                                                 keys_ready, fake_cli):
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                           session="S-mm")
    data = json.loads(path.read_text())
    data["x"] = "edited"
    path.write_text(json.dumps(data, indent=2))
    cp = store.read_checkpoint("S-mm")
    assert receipts.verbatim_degraded(cp) is True


def test_verbatim_degraded_never_subprocesses(tmp_checkpoint_dir, monkeypatch,
                                              keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-ns")
    # Missing CLI must NOT matter to the brief-time check.
    monkeypatch.setenv("DAIMON_VITNI_CLI", "definitely-not-on-path-xyz")
    calls = []
    monkeypatch.setattr(receipts.subprocess, "run",
                        lambda *a, **k: calls.append(a) or None)
    cp = store.read_checkpoint("S-ns")
    assert receipts.verbatim_degraded(cp) is False
    assert calls == []


# ---- status line -----------------------------------------------------------

def test_status_line_none_when_off(tmp_checkpoint_dir):
    assert receipts.status_line() is None


def test_status_line_signed(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-sl")
    line = receipts.status_line()
    assert line and "signed" in line


# ---- briefing render integration -------------------------------------------


def test_briefing_intact_keeps_verbatim_mark(tmp_checkpoint_dir, monkeypatch,
                                              keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-bi")
    cp = store.read_checkpoint("S-bi")
    text = briefing.render(cp)
    assert "✓ verbatim" in text
    assert briefing.DEGRADE_NOTE not in text


def test_briefing_degrades_when_receipt_gone(tmp_checkpoint_dir, monkeypatch,
                                             keys_ready, fake_cli):
    path = _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                           session="S-bd")
    path.with_suffix(".receipt").unlink()
    cp = store.read_checkpoint("S-bd")
    text = briefing.render(cp)
    assert briefing.DEGRADE_NOTE in text
    assert "✓ verbatim" not in text  # every verbatim label degraded
    assert "unverified" in text


def test_briefing_pre_receipt_never_degrades(tmp_checkpoint_dir):
    cp = {"session_id": "S-bp", "working_context": {"recent_decisions": [
        {"text": "d", "trust": "verbatim", "quote": "q"}]}}
    store.write_checkpoint("S-bp", cp)
    loaded = store.read_checkpoint("S-bp")
    text = briefing.render(loaded)
    assert briefing.DEGRADE_NOTE not in text
    assert "✓ verbatim" in text


def test_briefing_missing_cli_never_degrades(tmp_checkpoint_dir, monkeypatch,
                                             keys_ready, fake_cli):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-bc")
    cp = store.read_checkpoint("S-bc")
    monkeypatch.setenv("DAIMON_VITNI_CLI", "definitely-not-on-path-xyz")
    text = briefing.render(cp)
    assert briefing.DEGRADE_NOTE not in text  # intact sidecar + bytes match
    assert "✓ verbatim" in text


# ---- CLI verify-receipt / status -------------------------------------------


def test_cli_verify_receipt_verified(tmp_checkpoint_dir, monkeypatch, keys_ready,
                                     fake_cli, capsys):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-cv")
    rc = cli.main(["verify-receipt", "S-cv"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "verified" in out


def test_cli_verify_receipt_no_checkpoint(tmp_checkpoint_dir, capsys):
    rc = cli.main(["verify-receipt", "does-not-exist"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "no checkpoint" in out or "nothing to verify" in out


def test_cli_status_shows_receipts_line(tmp_checkpoint_dir, monkeypatch,
                                        keys_ready, fake_cli, capsys):
    _mint_via_store(tmp_checkpoint_dir, monkeypatch, keys_ready, fake_cli,
                    session="S-cs")
    monkeypatch.setenv("DAIMON_RECEIPTS", "1")
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "receipts: on" in out
