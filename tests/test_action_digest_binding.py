"""An approval or decision for one payload cannot be spent on another."""

from __future__ import annotations

from dataclasses import replace

import pytest

from causeway.errors import DigestMismatch
from causeway.sdk.run import AgentRun


def _proposal(run: AgentRun, summary: str):
    return run.platform.guard.propose(
        capability_id="open_model_review_case",
        arguments={"model_id": "m-1", "summary": summary, "severity": "advisory"},
        delegation=run.platform.issuer.attenuate(
            run.delegation, capabilities=frozenset({"open_model_review_case"})
        ),
        purpose="model_review",
        idempotency_key="case-1",
    )


def test_argument_order_produces_the_same_digest(run: AgentRun) -> None:
    """Normalisation happens before the digest, so spelling does not matter."""
    first = run.platform.guard.propose(
        capability_id="open_model_review_case",
        arguments={"model_id": "m-1", "summary": "s", "severity": "advisory"},
        delegation=run.delegation,
        purpose="model_review",
    )
    second = run.platform.guard.propose(
        capability_id="open_model_review_case",
        arguments={"severity": "advisory", "summary": " s ", "model_id": "m-1"},
        delegation=run.delegation,
        purpose="model_review",
    )
    assert first.digest == second.digest
    assert first.intent.action_id != second.intent.action_id


def test_changing_an_argument_changes_the_digest(run: AgentRun) -> None:
    """A different payload is a different action."""
    assert _proposal(run, "one").digest != _proposal(run, "two").digest


def test_a_decision_cannot_be_spent_on_a_different_action(run: AgentRun) -> None:
    """The guard re-derives the digest rather than trusting the decision."""
    approved = _proposal(run, "the reviewed summary")
    decision = run.platform.guard.decide(approved)

    substituted = _proposal(run, "a different summary entirely")
    with pytest.raises(DigestMismatch, match="does not bind to this action"):
        run.platform.guard.execute(substituted, decision)


def test_a_forged_decision_digest_is_rejected(run: AgentRun) -> None:
    """Editing the decision's digest to match does not help.

    The digest on a forged decision matches the intent, but the intent's own
    digest is recomputed from its contents, so the two only agree when the
    payload really is the one that was decided.
    """
    proposal = _proposal(run, "original")
    decision = run.platform.guard.decide(proposal)
    other = _proposal(run, "swapped")
    forged = replace(decision, action_digest=other.digest)

    # The forged decision now binds to `other`, so executing `proposal` fails.
    with pytest.raises(DigestMismatch):
        run.platform.guard.execute(proposal, forged)
