"""Ed25519 separates minting from checking: a verifier holds nothing that can sign."""

from __future__ import annotations

import stat
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES, cases
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from causeway.authority import (
    DelegationIssuer,
    DelegationVerifier,
    Ed25519Signer,
    Ed25519Verifier,
    ExternalEd25519Signer,
    HmacSigner,
    SignatureVerifier,
    Signer,
)
from causeway.cli import main as cli_main
from causeway.contracts.action import ResultClass
from causeway.contracts.delegation import SignedDelegation
from causeway.contracts.identity import Principal, WorkloadIdentity
from causeway.errors import AttenuationViolation, ConfigurationError, SignatureInvalid
from causeway.policy.pack import PolicyPack
from causeway.sdk.platform import Platform

AUDIENCE = "tool-gateway"
CAPS = frozenset({"open_model_review_case", "close_model_review_case"})


def _mint(
    issuer: DelegationIssuer, engineer: Principal, workload: WorkloadIdentity
) -> SignedDelegation:
    return issuer.root(
        principal=engineer,
        workload=workload,
        run_id="run-signing",
        domain="ml-platform",
        capabilities=CAPS,
        purpose="model_review",
        audience=AUDIENCE,
        policy_version="test",
    )


@pytest.fixture
def signer() -> Ed25519Signer:
    return Ed25519Signer.generate("issuer-1")


@pytest.fixture
def envelope(
    signer: Ed25519Signer, engineer: Principal, workload: WorkloadIdentity
) -> SignedDelegation:
    return _mint(DelegationIssuer(signer), engineer, workload)


def _checking_service(signer: Ed25519Signer) -> DelegationVerifier:
    """A verifying service built from the issuer's public key file alone."""
    return DelegationVerifier(Ed25519Verifier.from_public_pem({"issuer-1": signer.public_pem()}))


def test_an_envelope_verifies_with_the_public_key_alone(
    signer: Ed25519Signer, envelope: SignedDelegation
) -> None:
    assert envelope.algorithm == "EdDSA"
    _checking_service(signer).verify(envelope, audience=AUDIENCE)


def test_a_verifier_has_no_way_to_mint(signer: Ed25519Signer) -> None:
    verifier = Ed25519Verifier.from_public_pem({"issuer-1": signer.public_pem()})
    assert not hasattr(verifier, "sign")
    assert isinstance(verifier, SignatureVerifier)
    assert not isinstance(verifier, Signer)


def test_an_envelope_forged_with_any_shared_secret_is_refused(
    signer: Ed25519Signer, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """What an attacker with a stolen HMAC secret could mint today is now worthless."""
    forged = _mint(DelegationIssuer(HmacSigner()), engineer, workload)
    with pytest.raises(SignatureInvalid, match="only accepts 'EdDSA'"):
        _checking_service(signer).verify(forged, audience=AUDIENCE)


def test_relabelling_a_forgery_as_eddsa_does_not_help(
    signer: Ed25519Signer, engineer: Principal, workload: WorkloadIdentity
) -> None:
    forged = replace(_mint(DelegationIssuer(HmacSigner()), engineer, workload), algorithm="EdDSA")
    with pytest.raises(SignatureInvalid, match="failed signature verification"):
        _checking_service(signer).verify(forged, audience=AUDIENCE)


def test_an_eddsa_envelope_is_refused_by_an_hmac_verifier(envelope: SignedDelegation) -> None:
    """The algorithm check runs in both directions."""
    with pytest.raises(SignatureInvalid, match="only accepts 'HS256'"):
        DelegationVerifier(HmacSigner()).verify(envelope, audience=AUDIENCE)


def test_a_tampered_body_fails(signer: Ed25519Signer, envelope: SignedDelegation) -> None:
    widened = replace(envelope, body=replace(envelope.body, capabilities=frozenset({"*"})))
    with pytest.raises(SignatureInvalid):
        _checking_service(signer).verify(widened, audience=AUDIENCE)


@pytest.mark.parametrize(
    "change",
    [
        {"key_id": "someone-else"},
        {"signature": "not-base64!!"},
        {"signature": "c2hvcnQ"},  # valid base64, wrong length
        {"signature": ""},
    ],
)
def test_malformed_signatures_and_unknown_keys_fail_closed(
    signer: Ed25519Signer, envelope: SignedDelegation, change: dict[str, str]
) -> None:
    with pytest.raises(SignatureInvalid):
        _checking_service(signer).verify(replace(envelope, **change), audience=AUDIENCE)


def test_rotation_keeps_old_envelopes_valid(
    signer: Ed25519Signer, envelope: SignedDelegation, engineer: Principal,
    workload: WorkloadIdentity,
) -> None:
    signer.add_key("issuer-2", Ed25519PrivateKey.generate())
    signer.rotate_to("issuer-2")
    fresh = _mint(DelegationIssuer(signer), engineer, workload)
    checker = DelegationVerifier(signer.verifier())

    assert fresh.key_id == "issuer-2"
    checker.verify(envelope, audience=AUDIENCE)  # signed before the rotation
    checker.verify(fresh, audience=AUDIENCE)


def test_private_keys_round_trip_through_pem(
    signer: Ed25519Signer, envelope: SignedDelegation
) -> None:
    reloaded = Ed25519Signer.from_private_pem({"issuer-1": signer.private_pem()}, "issuer-1")
    assert reloaded.verify(envelope.body, envelope.signature, envelope.key_id)


def test_an_external_vault_can_hold_the_key(
    engineer: Principal, workload: WorkloadIdentity
) -> None:
    """The plug-in point for a cloud KMS: one function that signs bytes."""
    vault_key = Ed25519PrivateKey.generate()  # stands in for a key that never leaves the vault
    vault = Ed25519Signer({"kms-1": vault_key}, "kms-1")
    external = ExternalEd25519Signer(
        key_id="kms-1", sign_bytes=vault_key.sign, public_pem=vault.public_pem()
    )
    envelope = _mint(DelegationIssuer(external), engineer, workload)
    DelegationVerifier(external.verifier()).verify(envelope, audience=AUDIENCE)


def test_a_vault_returning_a_bad_signature_is_caught_at_mint(
    engineer: Principal, workload: WorkloadIdentity
) -> None:
    good = Ed25519Signer.generate("kms-1")
    wrong_key = Ed25519PrivateKey.generate()
    external = ExternalEd25519Signer(
        key_id="kms-1", sign_bytes=wrong_key.sign, public_pem=good.public_pem()
    )
    with pytest.raises(ConfigurationError, match="public key rejects"):
        _mint(DelegationIssuer(external), engineer, workload)


def test_the_whole_platform_runs_on_ed25519(
    pack: PolicyPack, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """Attenuation, policy and execution all work with split keys, and use the public half."""
    signer = Ed25519Signer.generate("issuer-1")
    platform = Platform.local(pack=pack, capabilities=list(SPARRING_CAPABILITIES), signer=signer)
    assert isinstance(platform.delegation_verifier._signer, Ed25519Verifier)

    run = platform.begin(
        principal=engineer, workload=workload, capabilities=CAPS,
        purpose="model_review", domain="ml-platform",
    )
    receipt = run.invoke_tool(
        "open_model_review_case",
        {"model_id": "m1", "summary": "s", "severity": "advisory"},
        idempotency_key="case:m1",
    )
    assert receipt.result_class is ResultClass.SUCCESS
    assert len(cases()) == 1
    with pytest.raises(AttenuationViolation):
        run.spawn(capabilities=frozenset({"promote_model_to_production"}), purpose="model_review")


def test_keygen_writes_a_private_key_only_its_owner_can_read(tmp_path: Path) -> None:
    assert cli_main(["keygen", "--key-id", "prod-1", "--out-dir", str(tmp_path)]) == 0
    private = tmp_path / "prod-1.private.pem"
    public = tmp_path / "prod-1.public.pem"
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    signer = Ed25519Signer.from_private_pem({"prod-1": private.read_bytes()}, "prod-1")
    assert signer.public_pem() == public.read_bytes()
    assert cli_main(["keygen", "--key-id", "prod-1", "--out-dir", str(tmp_path)]) == 1


def test_a_verifier_needs_at_least_one_key() -> None:
    with pytest.raises(ConfigurationError, match="at least one public key"):
        Ed25519Verifier({})
