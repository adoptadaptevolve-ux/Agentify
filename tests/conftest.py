"""Shared fixtures.

Every test builds a real platform. There are no mocks of the transaction
guard, the policy engine or the evidence ledger, because those are the things
under test -- a suite that mocks its enforcement point proves only that the
mock behaves.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES, reset_stores
from capabilities.sparring_partner.sandbox import ML_STANDARDS, sandbox_judge

from causeway.contracts.identity import (
    AuthStrength,
    Principal,
    PrincipalKind,
    WorkloadIdentity,
)
from causeway.policy.pack import PolicyPack, compose, load_pack
from causeway.runtime.budgets import RunBudget
from causeway.sdk.platform import Platform
from causeway.sdk.run import AgentRun

ROOT = Path(__file__).resolve().parents[1]

REVIEW_CAPABILITIES = frozenset(
    {"open_model_review_case", "close_model_review_case", "notify_model_owner"}
)


@pytest.fixture(autouse=True)
def _clean_stores() -> None:
    """Reset the sparring partner's stand-in systems between tests."""
    reset_stores()


@pytest.fixture
def pack() -> PolicyPack:
    """The composed baseline + ML platform pack the demo uses."""
    return compose(
        load_pack(ROOT / "policies" / "platform-baseline.json"),
        load_pack(ROOT / "policies" / "ml-platform.json"),
        name="ml-platform-effective",
        version="test",
    )


@pytest.fixture
def platform(pack: PolicyPack) -> Platform:
    """A fully wired local platform with the sparring capabilities registered."""
    built = Platform.local(
        pack=pack,
        capabilities=list(SPARRING_CAPABILITIES),
        judge=sandbox_judge(),
    )
    for chunk in ML_STANDARDS:
        built.retrieval.ingest(chunk)
    return built


@pytest.fixture
def engineer() -> Principal:
    """An ML engineer with MFA and no model-risk entitlement."""
    return Principal(
        id="a.rivera",
        kind=PrincipalKind.HUMAN,
        tenant="acme",
        roles=frozenset({"ml-engineer"}),
        entitlements=frozenset({"ml-standards-read"}),
        auth_strength=AuthStrength.MULTI_FACTOR,
        session_id="sess-test",
    )


@pytest.fixture
def workload() -> WorkloadIdentity:
    """An attested sandbox workload."""
    return WorkloadIdentity(
        service="sparring-partner",
        version="1.0.0",
        environment="test",
        image_digest="sha256:test",
        attested=True,
    )


@pytest.fixture
def run(platform: Platform, engineer: Principal, workload: WorkloadIdentity) -> AgentRun:
    """A governed run holding the review capabilities."""
    return platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=REVIEW_CAPABILITIES,
        purpose="model_review",
        domain="ml-platform",
        budget=RunBudget(max_steps=30, max_tool_calls=10, max_judgment_calls=60),
    )
