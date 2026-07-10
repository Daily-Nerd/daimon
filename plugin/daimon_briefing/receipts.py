"""Signed provenance receipts for checkpoints via vitni (#204).

Everything that touches vitni lives here. A checkpoint's `transcript_hash` (#125)
binds it to its source bytes, but nothing proved the checkpoint ON DISK was the
one the serializer wrote — a post-hoc edit was undetectable. An opt-in
`vitni/0.2` `local`-binding receipt closes that: `outputs_hash` covers the exact
final checkpoint blob, `inputs_hash` the raw transcript, the whole thing signed
Ed25519 via the vitni CLI and dropped in a sidecar.

FAIL-OPEN IS THE CONTRACT. Any failure — gate off, no seed, no openssl, no CLI,
timeout, nonzero rc, garbage output — logs one line (this package's logger
reaches serialize.log via #194's handler) and proceeds WITHOUT a receipt. A
receipts failure must never fail or block a serialize or a briefing.

Split of concerns at brief time (documented so it stays deliberate): the
briefing does a CHEAP tamper check only (sidecar present + outputs_hash byte
match, no subprocess) — enough to catch an edited or removed artifact. The FULL
cryptographic verification (signature, structure, policy binding) lives in the
on-demand `daimon verify-receipt`, which shells out to the vitni CLI.
"""

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import config

log = logging.getLogger(__name__)  # child of `daimon_briefing` → serialize.log

# --- protocol constants (§2/§10/§15, validated against the conformance vectors) ---
_PROTOCOL = "vitni/0.2"
_BINDING = "local"
METHOD = "local:daimon.serialize"
KID = "daimon-1"                     # fixed key id for v1
_MULTIHASH_SHA256 = bytes([0x12, 0x20])  # multicodec sha2-256 + 32-byte length

# The fixed 16-byte ASN.1/PKCS8 prefix for a raw Ed25519 seed (RFC 8410) — the
# same trick vitni's sign.ts documents: prepend it to the 32-byte seed to get a
# DER PKCS8 private key openssl/node can import.
_PKCS8_ED25519_PREFIX = bytes.fromhex("302e020100300506032b657004220420")

_SEED_FILE = "signing.seed"
_PUB_FILE = "signing.pub.json"
_CLI_TIMEOUT = 10                   # seconds; a signer that hangs must not hang us
_SIDECAR_SUFFIX = ".receipt"        # NOT .json — see _sidecar_path


# ---- hashes (pure stdlib) --------------------------------------------------


def _multibase_wrap(raw32: bytes) -> str:
    """vitni hash/nonce encoding: multibase base64url (`u`) of a multihash
    sha2-256 wrapper (0x12 0x20) around 32 bytes. Confirmed byte-for-byte
    against the receipt-id-local conformance vector."""
    return "u" + base64.urlsafe_b64encode(
        _MULTIHASH_SHA256 + raw32).decode("ascii").rstrip("=")


def _multibase_sha256(data: bytes) -> str:
    return _multibase_wrap(hashlib.sha256(data).digest())


def _wrap_hex_sha256(hex_digest) -> str | None:
    """Wrap an already-computed hex sha256 (transcript_hash is stored this way,
    #125) into the vitni multihash string WITHOUT re-hashing. None on anything
    that is not a 32-byte hex digest."""
    if not isinstance(hex_digest, str):
        return None
    try:
        raw = bytes.fromhex(hex_digest)
    except ValueError:
        return None
    return _multibase_wrap(raw) if len(raw) == 32 else None


def _nonce() -> str:
    """A fresh per-mint nonce: 32 CSPRNG bytes in the same multihash wrapper the
    conformance vectors show for the receipt `nonce` field."""
    return _multibase_wrap(os.urandom(32))


# ---- key material ----------------------------------------------------------


def _read_seed(seed_path: Path) -> bytes | None:
    try:
        raw = base64.b64decode(seed_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return raw if len(raw) == 32 else None


def _ensure_seed(keys_dir: Path) -> bytes | None:
    """Load the Ed25519 seed, creating it (32 CSPRNG bytes, base64, mode 0600) on
    first mint. O_EXCL so two racing first-mints can never write divergent seeds
    — the loser reads the winner's. None on any failure (fail-open)."""
    seed_path = keys_dir / _SEED_FILE
    raw = _read_seed(seed_path)
    if raw is not None:
        return raw
    try:
        keys_dir.mkdir(parents=True, exist_ok=True)
        raw = os.urandom(32)
        fd = os.open(str(seed_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, base64.b64encode(raw))
        finally:
            os.close(fd)
        os.chmod(seed_path, 0o600)  # umask-independent guarantee
        return raw
    except FileExistsError:
        return _read_seed(seed_path)  # lost the create race — use the winner's
    except OSError as exc:
        log.warning("daimon receipts: cannot create signing seed (%s); no receipt", exc)
        return None


def _derive_pubkey_x(seed: bytes) -> str | None:
    """Raw Ed25519 public key (base64url, no pad) for `seed`, via openssl. The
    seed is wrapped into a PKCS8 DER, `openssl pkey -pubout` emits the 44-byte
    SPKI DER whose last 32 bytes ARE the raw public key. None on any failure —
    e.g. macOS LibreSSL lacks Ed25519 in `openssl pkey` (fail-open to no receipt)."""
    der = _PKCS8_ED25519_PREFIX + seed
    try:
        proc = subprocess.run(
            ["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"],
            input=der, capture_output=True, timeout=_CLI_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("daimon receipts: openssl unavailable (%s); no receipt", exc)
        return None
    if proc.returncode != 0 or len(proc.stdout) < 32:
        log.warning("daimon receipts: openssl pubkey derivation failed (rc=%s); no receipt",
                    proc.returncode)
        return None
    return base64.urlsafe_b64encode(proc.stdout[-32:]).decode("ascii").rstrip("=")


def _pubkey_jwk(x: str) -> dict:
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "alg": "EdDSA", "status": "active"}


def _ensure_pubkey(keys_dir: Path, seed: bytes) -> dict | None:
    """Load the cached public JWK, deriving + caching it once on first mint.
    None when derivation is impossible (no openssl) — no receipt this run."""
    pub_path = keys_dir / _PUB_FILE
    try:
        jwk = json.loads(pub_path.read_text(encoding="utf-8"))
        if isinstance(jwk, dict) and jwk.get("x"):
            return jwk
    except (OSError, json.JSONDecodeError):
        pass
    x = _derive_pubkey_x(seed)
    if not x:
        return None
    jwk = _pubkey_jwk(x)
    try:
        keys_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(pub_path, json.dumps(jwk))
    except OSError:
        pass  # derivation succeeded; a cache-write failure is non-fatal
    return jwk


def _load_pubkey(keys_dir: Path) -> dict | None:
    try:
        jwk = json.loads((keys_dir / _PUB_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return jwk if isinstance(jwk, dict) and jwk.get("x") else None


# ---- CLI plumbing ----------------------------------------------------------


def _resolve_cli() -> str | None:
    """Absolute path to the vitni CLI, or None when it is not resolvable —
    DAIMON_VITNI_CLI (a path or a name) then PATH. shutil.which handles both."""
    return shutil.which(config.vitni_cli())


def _run_cli(cli: str, command: str, stdin_json: str) -> dict | None:
    """Run `<cli> <command>` with JSON on stdin; parse the one JSON line back.
    None on any failure (spawn error, timeout, nonzero rc, non-JSON, {"error"})."""
    try:
        proc = subprocess.run(
            [cli, command], input=stdin_json.encode("utf-8"),
            capture_output=True, timeout=_CLI_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("daimon receipts: vitni %s failed to run (%s)", command, exc)
        return None
    if proc.returncode != 0:
        log.warning("daimon receipts: vitni %s exited rc=%s: %s", command,
                    proc.returncode, proc.stderr.decode("utf-8", "replace")[:200])
        return None
    try:
        out = json.loads(proc.stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log.warning("daimon receipts: vitni %s produced non-JSON output", command)
        return None
    if not isinstance(out, dict) or "error" in out:
        log.warning("daimon receipts: vitni %s returned %r", command, out)
        return None
    return out


# ---- atomic sidecar write --------------------------------------------------


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _sidecar_path(checkpoint_path: Path) -> Path:
    """`<session>.receipt` beside the checkpoint. Deliberately NOT `.json`: the
    sidecar lives in the flat store dir, and every GC / session-file / recall /
    pointer scan filters on `suffix == ".json"` or the pointer regex — a
    `.receipt` file is invisible to all of them, so GC never eats it and it never
    counts toward DAIMON_CHECKPOINT_KEEP (#204)."""
    return checkpoint_path.with_suffix(_SIDECAR_SUFFIX)


def _checkpoint_file(session_id: str) -> Path:
    """The per-session checkpoint file the receipt binds. Lazy store import
    avoids a store<->receipts cycle (store imports this module at top)."""
    from . import store
    return config.checkpoint_dir() / f"{store._safe_name(session_id)}.json"


# ---- mint (called from store.write_checkpoint) -----------------------------


def plan_mint(checkpoint: dict) -> dict | None:
    """Decide whether to mint for `checkpoint` and prepare key material, WITHOUT
    touching the checkpoint or writing anything. Returns a context dict when a
    mint should proceed (caller then stamps the era marker before serializing the
    blob), else None: gate off, no/invalid transcript_hash (a receipt binding
    nothing is noise), no CLI, no openssl, or no seed. Fail-open — None means
    'serialize proceeds receiptless', never an error."""
    if not config.receipts_enabled():
        return None
    inputs_hash = _wrap_hex_sha256(checkpoint.get("transcript_hash"))
    if inputs_hash is None:
        log.info("daimon receipts: no usable transcript_hash; skipping receipt mint")
        return None
    cli = _resolve_cli()
    if cli is None:
        log.info("daimon receipts: vitni CLI %r not found; skipping receipt mint",
                 config.vitni_cli())
        return None
    keys_dir = config.keys_dir()
    seed = _ensure_seed(keys_dir)
    if seed is None:
        return None
    if _ensure_pubkey(keys_dir, seed) is None:
        return None
    return {
        "cli": cli,
        "seed_b64": base64.b64encode(seed).decode("ascii"),
        "inputs_hash": inputs_hash,
        "performer_id": str(checkpoint.get("author") or "unknown"),
    }


def mint(plan: dict, checkpoint: dict, blob: str, checkpoint_path: Path) -> bool:
    """Sign a local-binding receipt over the final checkpoint `blob` and write the
    sidecar. Called AFTER the checkpoint file is written, so outputs_hash covers
    the exact bytes on disk. Fail-open: returns False + logs on any failure, never
    raises. (A sign failure here leaves the era marker without a sidecar, which a
    later brief/verify surfaces loudly — the correct direction for a signer that
    was present at plan time but then broke.)"""
    try:
        receipt = {
            "v": _PROTOCOL,
            "binding": _BINDING,
            "action_ref": None,
            "performer_id": plan["performer_id"],
            "requester_id": None,
            "method": METHOD,
            "inputs_hash": plan["inputs_hash"],
            "outputs_hash": _multibase_sha256(blob.encode("utf-8")),
            "cost": {"tokens": "0", "usd_micros": "0", "wall_ms": "0",
                     "rail_ref": None},
            "status": "OK",
            "reason": None,
            "parent_receipt_hash": None,
            "log_policy": "best_effort",
            "ts": checkpoint.get("created"),
            "nonce": _nonce(),
        }
        out = _run_cli(plan["cli"], "sign", json.dumps(
            {"receipt": receipt, "kid": KID, "private_key_b64": plan["seed_b64"]}))
        jws = out.get("signed_receipt") if out else None
        if not isinstance(jws, str) or jws.count(".") != 2:
            log.warning("daimon receipts: sign produced no usable JWS; no sidecar")
            return False
        _atomic_write_text(_sidecar_path(checkpoint_path), json.dumps(
            {"jws": jws, "receipt": receipt, "kid": KID,
             "performer_id": plan["performer_id"]}, indent=2, ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001 — a receipts failure must never fail a write
        # Type+message only, no traceback: the signing seed is in scope in this
        # frame, and a locals-capturing log handler must never see it (#204).
        log.warning("daimon receipts: mint failed; checkpoint written without "
                    "receipt (%s: %s)", type(exc).__name__, exc)
        return False


# ---- verify (daimon verify-receipt) ----------------------------------------


def verify_receipt(session_id: str) -> tuple[int, list[str]]:
    """Full verification for one session's receipt. Returns (rc, lines):
      0 verified — signature valid, structure canonical, binding=local, AND the
                   checkpoint bytes still match the signed outputs_hash;
      1 failed   — file edited after signing, receipt removed from a receipt-era
                   checkpoint, corrupt sidecar, or the vitni CLI rejected it;
      2 unable   — pre-receipt checkpoint (nothing to verify), no vitni CLI, or no
                   local public key.
    Never raises."""
    try:
        return _verify_receipt(session_id)
    except Exception:  # noqa: BLE001 — a user command, but still never crash
        log.warning("daimon receipts: verify-receipt crashed", exc_info=True)
        return 2, ["unable to verify: an unexpected error occurred (see serialize.log)"]


def _verify_receipt(session_id: str) -> tuple[int, list[str]]:
    cp_file = _checkpoint_file(session_id)
    if not cp_file.exists():
        return 2, [f"no checkpoint on disk for session {session_id}"]
    try:
        checkpoint = json.loads(cp_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        checkpoint = {}
    marked = isinstance(checkpoint, dict) and checkpoint.get("receipts") is True
    sidecar_path = _sidecar_path(cp_file)
    if not sidecar_path.exists():
        if marked:
            return 1, [
                f"FAILED: session {session_id} is marked receipt-era but its receipt is missing",
                "  the receipt was removed or lost — provenance cannot be confirmed"]
        return 2, [f"pre-receipt checkpoint (session {session_id}) — nothing to verify"]
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        receipt = sidecar["receipt"]
        jws = sidecar["jws"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return 1, [f"FAILED: receipt sidecar for {session_id} is unreadable or corrupt"]

    # 1) byte match — proves THIS file is the one that was signed. The CLI proves
    # the signature/structure; only this proves the artifact wasn't edited after.
    expected = receipt.get("outputs_hash")
    if not expected or _multibase_sha256(cp_file.read_bytes()) != expected:
        return 1, [
            f"FAILED: session {session_id} bytes do not match the receipt's outputs_hash",
            "  the checkpoint was edited after it was signed"]

    # 2) full crypto via the vitni CLI.
    cli = _resolve_cli()
    if cli is None:
        return 2, [
            f"unable to verify: vitni CLI {config.vitni_cli()!r} not found",
            "  (checkpoint bytes DO match the receipt; run again with the CLI on PATH)"]
    pubkey = _load_pubkey(config.keys_dir())
    if pubkey is None:
        return 2, ["unable to verify: no local public key (signing.pub.json missing)"]
    performer = sidecar.get("performer_id") or receipt.get("performer_id")
    kid = sidecar.get("kid") or KID
    if not jws or not performer:
        return 1, [f"FAILED: receipt for {session_id} is missing its signature or performer"]
    out = _run_cli(cli, "verify", json.dumps({
        "signed_receipt": jws,
        "keys": {performer: {kid: pubkey}},
        "policy": {"expected_binding": _BINDING, "expected_method": METHOD},
    }))
    if out is None:
        return 2, ["unable to verify: the vitni CLI did not return a usable result"]
    if out.get("valid") is True:
        return 0, [
            f"verified: session {session_id} — signature valid, bytes match, binding=local",
            f"  performer={performer} kid={kid}"]
    return 1, [f"FAILED: vitni rejected the receipt ({out.get('reason')})"]


# ---- brief-time tamper check (cheap, no subprocess) ------------------------


def verbatim_degraded(checkpoint) -> bool:
    """True when a receipt-era checkpoint's provenance can't be locally confirmed
    at brief time — the sidecar is missing (removed/lost) or the checkpoint file's
    bytes no longer match the signed outputs_hash (edited). CHEAP by design: only
    a file read + a sha256, NEVER the vitni CLI (full crypto is verify-receipt).

    Pre-receipt checkpoints (no `receipts` marker) never degrade — no retroactive
    downgrades. A missing CLI is irrelevant here, so it never degrades. Fail-open:
    any error returns False (don't punish labels over our own bug)."""
    try:
        if not isinstance(checkpoint, dict) or checkpoint.get("receipts") is not True:
            return False
        session_id = checkpoint.get("session_id")
        if not session_id:
            return False
        cp_file = _checkpoint_file(str(session_id))
        sidecar_path = _sidecar_path(cp_file)
        if not sidecar_path.exists():
            return True  # receipt-era but the receipt is gone — degrade loudly
        try:
            expected = json.loads(sidecar_path.read_text(encoding="utf-8")) \
                .get("receipt", {}).get("outputs_hash")
            blob = cp_file.read_bytes()
        except (OSError, json.JSONDecodeError, AttributeError):
            return False  # can't read one side — fail-open, don't degrade
        if not expected:
            return False
        return _multibase_sha256(blob) != expected
    except Exception:  # noqa: BLE001
        return False


# ---- status line (daimon status) -------------------------------------------


def status_line(project_dir=None) -> str | None:
    """One `daimon status` line about receipts, or None when the feature is off.
    Reflects the latest checkpoint's mint result via the sidecar's existence."""
    if not config.receipts_enabled():
        return None
    from . import store
    checkpoint = store.read_latest(project_dir=project_dir)
    if not isinstance(checkpoint, dict) or not checkpoint.get("session_id"):
        return "receipts: on — no checkpoint to sign yet"
    sid = str(checkpoint["session_id"])
    sidecar = _sidecar_path(_checkpoint_file(sid))
    if sidecar.exists():
        return f"receipts: on — latest checkpoint {sid} is signed"
    if checkpoint.get("receipts") is True:
        return (f"receipts: on — latest checkpoint {sid} is marked receipt-era but "
                "its receipt is MISSING (see serialize.log)")
    return f"receipts: on — latest checkpoint {sid} predates receipts (unsigned)"
