"""Every obligation the contract can express must be enforceable."""

from __future__ import annotations

from dataclasses import replace

import pytest

from causeway.contracts.policy import Effect, Obligation
from causeway.errors import PolicyDenied
from causeway.gateway.transaction_guard import HANDLED_OBLIGATIONS
from causeway.sdk.run import AgentRun


def test_the_gateway_handles_every_declared_obligation() -> None:
    """Adding an obligation without teaching the gateway must break the build.

    Without this test, a new control could be added to the policy contract,
    referenced in a pack, and silently do nothing in production.
    """
    assert frozenset(Obligation) == HANDLED_OBLIGATIONS


def test_an_unenforceable_obligation_denies(run: AgentRun) -> None:
    """A control the gateway cannot enforce is a denial, never a no-op."""
    proposal = run.platform.guard.propose(
        capability_id="open_model_review_case",
        arguments={"model_id": "m", "summary": "s", "severity": "advisory"},
        delegation=run.platform.issuer.attenuate(
            run.delegation, capabilities=frozenset({"open_model_review_case"})
        ),
        purpose="model_review",
        idempotency_key="k-1",
    )
    decision = run.platform.guard.decide(proposal)

    class Invented(str):
        """Stands in for an obligation this build does not know about."""

    tampered = replace(
        decision,
        effect=Effect.ALLOW,
        obligations=decision.obligations | {Invented("teleport_the_reviewer")},
    )
    with pytest.raises(PolicyDenied, match="cannot enforce"):
        run.platform.guard.execute(proposal, tampered)


def test_a_denied_decision_cannot_be_executed(run: AgentRun) -> None:
    """The guard re-reads the effect rather than trusting the caller's flow."""
    proposal = run.platform.guard.propose(
        capability_id="notify_model_owner",
        arguments={"model_id": "m", "message": "x"},
        delegation=run.platform.issuer.attenuate(
            run.delegation, capabilities=frozenset({"notify_model_owner"})
        ),
        purpose="model_review",
        destination="nowhere-good",
        idempotency_key="k-2",
    )
    decision = run.platform.guard.decide(proposal)
    assert decision.effect is Effect.DENY
    with pytest.raises(PolicyDenied):
        run.platform.guard.execute(proposal, decision)
