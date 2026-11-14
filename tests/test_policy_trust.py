"""Signed policy can be made mandatory, and then nothing runs on unverified policy."""

from __future__ import annotations

import shutil
from enum import Enum
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES

from causeway.authority import Ed25519Signer, Ed25519Verifier
from causeway.canonical import CanonicalEncodingError, canonical_json
from causeway.cli import main as cli_main
from causeway.contracts.risk import RiskTier
from causeway.errors import ConfigurationError, SignatureInvalid
from causeway.policy import (
    REQUIRE_SIGNED_ENV,
    load_pack,
    load_policy,
    sign_pack,
    verify_pack,
)
from causeway.sdk.platform import Platform

ROOT = Path(__file__).resolve().parents[1]
PACKS = ("platform-baseline.json", "ml-platform.json")


@pytest.fixture(autouse=True)
def _not_strict_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REQUIRE_SIGNED_ENV, raising=False)


@pytest.fixture
def signer() -> Ed25519Signer:
    return Ed25519Signer.generate("cp-1")


@pytest.fixture
def node(signer: Ed25519Signer) -> Ed25519Verifier:
    return Ed25519Verifier.from_public_pem({"cp-1": signer.public_pem()})


@pytest.fixture
def policy_dir(tmp_path: Path) -> Path:
    for name in PACKS:
        shutil.copy(ROOT / "policies" / name, tmp_path / name)
    return tmp_path


def _sign_all(directory: Path, signer: Ed25519Signer) -> list[Path]:
    paths = [directory / name for name in PACKS]
    for path in paths:
        signature = sign_pack(load_pack(path), signer)
        path.with_name(path.name + ".sig.json").write_text(signature.to_json())
    return paths


# --------------------------------------------------------- the encoding bug --


def test_the_baseline_pack_can_be_signed(signer: Ed25519Signer, node: Ed25519Verifier) -> None:
    """Regression: packs with per-tier postures used to crash the canonical encoder."""
    baseline = load_pack(ROOT / "policies" / "platform-baseline.json")
    assert baseline.tier_posture  # the part that used to break
    verify_pack(baseline, sign_pack(baseline, signer), node)


def test_enum_keys_encode_as_their_value() -> None:
    assert canonical_json({RiskTier.R3_CUSTOMER_IMPACT: "x"}) == '{"3":"x"}'


def test_keys_that_collide_after_encoding_are_refused() -> None:
    """Two different objects must never share an encoding, or they share a signature."""

    class Colour(Enum):
        RED = "red"

    with pytest.raises(CanonicalEncodingError, match="collide"):
        canonical_json({Colour.RED: 1, "red": 2})


# ------------------------------------------------------------- load_policy --


def test_unsigned_policy_loads_when_not_required(policy_dir: Path) -> None:
    loaded = load_policy([policy_dir / name for name in PACKS])
    assert not loaded.fully_verified
    assert [s.verified for s in loaded.sources] == [False, False]


def test_signed_policy_records_who_signed_it(
    policy_dir: Path, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    loaded = load_policy(_sign_all(policy_dir, signer), verifier=node, require_signed=True)
    assert loaded.fully_verified
    assert {(s.key_id, s.algorithm) for s in loaded.sources} == {("cp-1", "EdDSA")}


def test_requiring_signatures_without_a_key_is_refused(policy_dir: Path) -> None:
    with pytest.raises(ConfigurationError, match="no verification key"):
        load_policy([policy_dir / "ml-platform.json"], require_signed=True)


def test_an_unsigned_pack_is_refused_when_signatures_are_required(
    policy_dir: Path, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    paths = _sign_all(policy_dir, signer)
    paths[0].with_name(paths[0].name + ".sig.json").unlink()
    with pytest.raises(SignatureInvalid, match="has no signature"):
        load_policy(paths, verifier=node, require_signed=True)


def test_a_bad_signature_is_refused_even_when_signatures_are_optional(
    policy_dir: Path, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    """A tampered pack is evidence of tampering, whatever the mode."""
    paths = _sign_all(policy_dir, signer)
    paths[1].write_text(paths[1].read_text().replace('"max_risk_tier": 2', '"max_risk_tier": 4', 1))
    with pytest.raises(SignatureInvalid, match="does not match the digest"):
        load_policy(paths, verifier=node)


def test_the_environment_forces_strict_mode_and_cannot_be_overridden(
    policy_dir: Path, node: Ed25519Verifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REQUIRE_SIGNED_ENV, "1")
    with pytest.raises(SignatureInvalid, match="has no signature"):
        load_policy([policy_dir / "ml-platform.json"], verifier=node, require_signed=False)


# --------------------------------------------------------- Platform.local --


def test_a_strict_platform_refuses_an_in_memory_pack(policy_dir: Path) -> None:
    pack = load_pack(policy_dir / "ml-platform.json")
    with pytest.raises(SignatureInvalid, match="no verified provenance"):
        Platform.local(pack=pack, require_signed_policy=True)


def test_the_environment_makes_every_platform_strict(
    policy_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Code that never asks for strict mode still gets it when the deployment says so."""
    monkeypatch.setenv(REQUIRE_SIGNED_ENV, "true")
    with pytest.raises(SignatureInvalid):
        Platform.local(pack=load_pack(policy_dir / "ml-platform.json"))


def test_a_strict_platform_runs_on_verified_policy_and_says_so(
    policy_dir: Path, signer: Ed25519Signer, node: Ed25519Verifier
) -> None:
    loaded = load_policy(_sign_all(policy_dir, signer), verifier=node, require_signed=True)
    platform = Platform.local(
        policy=loaded, require_signed_policy=True, capabilities=list(SPARRING_CAPABILITIES)
    )
    assert [s.verified for s in platform.policy_sources] == [True, True]
    assert platform.policy.pack.artifact_digest == loaded.pack.artifact_digest


def test_a_strict_platform_refuses_unverified_loaded_policy(policy_dir: Path) -> None:
    loaded = load_policy([policy_dir / "ml-platform.json"])
    with pytest.raises(SignatureInvalid, match="not verified"):
        Platform.local(policy=loaded, require_signed_policy=True)


def test_pack_and_policy_are_mutually_exclusive(policy_dir: Path) -> None:
    loaded = load_policy([policy_dir / "ml-platform.json"])
    with pytest.raises(ConfigurationError, match="exactly one"):
        Platform.local(pack=loaded.pack, policy=loaded)
    with pytest.raises(ConfigurationError, match="exactly one"):
        Platform.local()


# --------------------------------------------------------------------- CLI --


def test_the_cli_refuses_tampered_policy_in_strict_mode(
    policy_dir: Path, signer: Ed25519Signer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    paths = _sign_all(policy_dir, signer)
    key = policy_dir / "cp-1.public.pem"
    key.write_bytes(signer.public_pem())
    base = ["--policy-key", str(key), "--policy-key-id", "cp-1"]
    for path in paths:
        base += ["--pack", str(path)]

    assert cli_main([*base, "--require-signed-policy", "policy-status"]) == 0
    paths[1].write_text(paths[1].read_text().replace('"max_risk_tier": 2', '"max_risk_tier": 4', 1))
    monkeypatch.setenv(REQUIRE_SIGNED_ENV, "1")
    simulate = ["policy-simulate", "open_model_review_case", "--arguments",
                '{"model_id":"m","summary":"s","severity":"advisory"}']
    assert cli_main([*base, *simulate]) == 2


def test_the_cli_reports_unsigned_policy_when_not_strict(policy_dir: Path) -> None:
    assert cli_main(["--pack", str(policy_dir / "ml-platform.json"), "policy-status"]) == 0
