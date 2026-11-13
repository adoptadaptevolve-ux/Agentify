"""Delegation may only ever narrow.

This is the invariant that makes a compromised agent survivable: the worst it
can do is spend authority it already had. These tests try to widen along every
dimension the envelope carries.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from causeway.contracts.delegation import Constraints
from causeway.contracts.risk import RiskTier
from causeway.errors import AttenuationViolation
from causeway.sdk.platform import Platform
from causeway.sdk.run import AgentRun


def test_child_cannot_add_a_capability(run: AgentRun) -> None:
    """A subagent cannot receive a capability its parent does not hold."""
    with pytest.raises(AttenuationViolation, match="capabilities the parent does not hold"):
        run.spawn(
            capabilities=frozenset({"promote_model_to_production"}),
            purpose="escalate",
        )


def test_child_cannot_raise_an_amount_bound(platform: Platform, run: AgentRun) -> None:
    """A subagent cannot be given a larger spending bound than its parent."""
    bounded = platform.issuer.attenuate(
        run.delegation, constraints=Constraints(max_amount=Decimal("100"))
    )
    with pytest.raises(AttenuationViolation, match="broader than the parent"):
        platform.issuer.attenuate(
            bounded, constraints=Constraints(max_amount=Decimal("1000"))
        )


def test_child_cannot_raise_its_risk_ceiling(platform: Platform, run: AgentRun) -> None:
    """A subagent cannot be given a higher risk ceiling than its parent."""
    bounded = platform.issuer.attenuate(
        run.delegation, constraints=Constraints(max_risk_tier=RiskTier.R2_REVERSIBLE_WRITE)
    )
    with pytest.raises(AttenuationViolation):
        platform.issuer.attenuate(
            bounded, constraints=Constraints(max_risk_tier=RiskTier.R4_HIGH_CONSEQUENCE)
        )


def test_child_cannot_widen_a_destination_allowlist(
    platform: Platform, run: AgentRun
) -> None:
    """A subagent cannot add a destination its parent could not reach."""
    bounded = platform.issuer.attenuate(
        run.delegation, constraints=Constraints(destinations=frozenset({"queue-a"}))
    )
    with pytest.raises(AttenuationViolation):
        platform.issuer.attenuate(
            bounded, constraints=Constraints(destinations=frozenset({"queue-a", "queue-b"}))
        )


def test_child_cannot_outlive_its_parent(platform: Platform, run: AgentRun) -> None:
    """Lifetime attenuates too: a child expires no later than its parent."""
    child = platform.issuer.attenuate(run.delegation, lifetime=timedelta(days=7))
    assert child.body.expires_at <= run.delegation.body.expires_at


def test_narrowing_is_permitted_and_chains(platform: Platform, run: AgentRun) -> None:
    """A genuine narrowing succeeds and records its parent in the chain."""
    child = run.spawn(
        capabilities=frozenset({"open_model_review_case"}),
        purpose="record findings only",
        max_risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
    )
    body = child.delegation.body
    assert body.capabilities == frozenset({"open_model_review_case"})
    assert body.capabilities < run.delegation.body.capabilities
    assert body.parent_hash == run.delegation.body_digest
    assert body.depth == run.delegation.body.depth + 1


def test_an_envelope_cannot_be_edited_after_signing(
    platform: Platform, run: AgentRun
) -> None:
    """Tampering with a signed body invalidates it.

    The envelope is a frozen dataclass, so this reconstructs one with an extra
    capability -- which is the shape an attacker with memory access would try.
    """
    from dataclasses import replace

    forged = replace(
        run.delegation,
        body=replace(
            run.delegation.body,
            capabilities=run.delegation.body.capabilities | {"promote_model_to_production"},
        ),
    )
    assert not platform.delegation_verifier._signer.verify(
        forged.body, forged.signature, forged.key_id
    )
