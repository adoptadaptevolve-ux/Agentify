"""Policy packs signed with a private key, checked with the public key alone."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from causeway.authority import Ed25519Signer, Ed25519Verifier, HmacSigner
from causeway.cli import main as cli_main
from causeway.errors import SignatureInvalid
from causeway.policy import (
    PackSignature,
    PolicyPack,
    Thresholds,
    load_pack,
    load_signed_pack,
    sign_pack,
    verify_pack,
)

ROOT = Path(__file__).resolve().parents[1]
PACK_FILE = ROOT / "policies" / "ml-platform.json"


@pytest.fixture
def signer() -> Ed25519Signer:
    return Ed25519Signer.generate("control-plane-1")


@pytest.fixture
def node(signer: Ed25519Signer) -> Ed25519Verifier:
    """An execution-plane node: it has the public key file and nothing else."""
    return Ed25519Verifier.from_public_pem({"control-plane-1": signer.public_pem()})


@pytest.fixture
def pack() -> PolicyPack:
    return load_pack(PACK_FILE)


def test_a_signed_pack_verifies_with_the_public_key_alone(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    signature = sign_pack(pack, signer)
    assert signature.algorithm == "EdDSA"
    verify_pack(pack, signature, node)


def test_a_loosened_pack_is_refused(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    """Relaxing a threshold after signing is exactly the change signing exists to catch."""
    signature = sign_pack(pack, signer)
    loosened = replace(pack, thresholds=Thresholds(injection_deny=0.99))
    with pytest.raises(SignatureInvalid, match="does not match the digest"):
        verify_pack(loosened, signature, node)


def test_a_loosened_pack_with_a_matching_digest_claim_is_still_refused(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    """Editing the signature file's digest to match does not help: the signature covers the pack."""
    loosened = replace(pack, thresholds=Thresholds(injection_deny=0.99))
    signature = replace(sign_pack(pack, signer), pack_digest=loosened.artifact_digest)
    with pytest.raises(SignatureInvalid, match="failed signature verification"):
        verify_pack(loosened, signature, node)


def test_a_signature_for_one_version_cannot_be_reused_for_another(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    signature = sign_pack(pack, signer)
    with pytest.raises(SignatureInvalid, match="presented with a signature for"):
        verify_pack(replace(pack, version="9999.01.01"), signature, node)


def test_an_hmac_signature_is_refused_by_a_node_that_expects_eddsa(
    pack: PolicyPack, node: Ed25519Verifier
) -> None:
    """What someone holding a shared secret could produce is worthless to a production node."""
    forged = sign_pack(pack, HmacSigner())
    with pytest.raises(SignatureInvalid, match="only accepts 'EdDSA'"):
        verify_pack(pack, forged, node)


def test_a_delegation_style_signature_is_not_a_pack_signature(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    """Signatures carry a context, so one made outside pack signing cannot be passed off as one."""
    raw_signature, key_id = signer.sign(pack)  # same key, no pack-signing context
    smuggled = replace(sign_pack(pack, signer), signature=raw_signature, key_id=key_id)
    with pytest.raises(SignatureInvalid, match="failed signature verification"):
        verify_pack(pack, smuggled, node)


def test_an_unknown_key_is_refused(
    pack: PolicyPack, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    signature = replace(sign_pack(pack, signer), key_id="someone-else")
    with pytest.raises(SignatureInvalid):
        verify_pack(pack, signature, node)


def test_loading_requires_a_signature_file(tmp_path: Path, node: Ed25519Verifier) -> None:
    """A node told to require signatures never falls back to an unsigned load."""
    unsigned = tmp_path / "pack.json"
    unsigned.write_bytes(PACK_FILE.read_bytes())
    with pytest.raises(SignatureInvalid, match="no signature for"):
        load_signed_pack(unsigned, node)


def test_reformatting_a_signed_pack_does_not_break_it(
    tmp_path: Path, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    """The signature covers the pack's meaning, not its whitespace or key order."""
    path = tmp_path / "pack.json"
    path.write_bytes(PACK_FILE.read_bytes())
    signature = sign_pack(load_pack(path), signer)
    (tmp_path / "pack.json.sig.json").write_text(signature.to_json())

    raw = json.loads(path.read_text())
    path.write_text(json.dumps(raw, indent=4, sort_keys=True))
    assert load_signed_pack(path, node).name == "ml-platform"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"pack_name": "x"}),
        json.dumps(
            {
                "pack_name": "x", "pack_version": "1", "pack_digest": "d",
                "algorithm": "EdDSA", "key_id": "k", "signature": "s", "extra": "y",
            }
        ),
    ],
)
def test_malformed_signature_files_are_refused(text: str) -> None:
    with pytest.raises(SignatureInvalid):
        PackSignature.from_json(text)


def test_the_cli_signs_and_verifies_and_refuses_a_tampered_pack(tmp_path: Path) -> None:
    pack_path = tmp_path / "ml-platform.json"
    pack_path.write_bytes(PACK_FILE.read_bytes())
    assert cli_main(["keygen", "--key-id", "cp-1", "--out-dir", str(tmp_path)]) == 0
    private, public = tmp_path / "cp-1.private.pem", tmp_path / "cp-1.public.pem"

    assert cli_main(["sign-pack", str(pack_path), "--private-key", str(private),
                     "--key-id", "cp-1"]) == 0
    verify = ["verify-pack", str(pack_path), "--public-key", str(public), "--key-id", "cp-1"]
    assert cli_main(verify) == 0

    raw = json.loads(pack_path.read_text())
    raw["rules"][0]["max_risk_tier"] = 4  # let a rule reach a higher risk tier
    pack_path.write_text(json.dumps(raw))
    assert cli_main(verify) == 2
