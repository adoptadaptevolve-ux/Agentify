"""The golden path, end to end, against the offline judge.

These are the tests a platform team would point a new domain team at: they show
what the paved road does without anyone writing security code.
"""

from __future__ import annotations

import pytest
from capabilities.sparring_partner import SPARRING_WORKFLOW
from capabilities.sparring_partner.capabilities import cases, notifications
from capabilities.sparring_partner.sandbox import (
    HOSTILE_PROPOSAL,
    STRONG_PROPOSAL,
    WEAK_PROPOSAL,
)

from causeway.contracts.human import DecisionOutcome
from causeway.evidence.replay import forensic_package, reconstruct
from causeway.runtime.workflow import RunStatus, WorkflowRunner
from causeway.sdk.platform import Platform
from causeway.sdk.run import AgentRun


@pytest.fixture
def runner() -> WorkflowRunner:
    """The sparring partner's workflow runner."""
    return WorkflowRunner(SPARRING_WORKFLOW)


def _approve_until_done(platform: Platform, runner: WorkflowRunner, run: AgentRun, state):
    """Act as the reviewer for every pause the run reaches."""
    while state.status is RunStatus.PAUSED:
        item = platform.approvals.item(state.pending_approval_id or "")
        platform.approvals.decide(
            approval_id=item.approval_id,
            approver_id="m.okonkwo",
            approver_authority=frozenset({"model-risk-approver"}),
            outcome=DecisionOutcome.APPROVED,
            rationale="reviewed",
        )
        state = runner.resume(run, state)
    return state


def test_a_weak_proposal_is_refuted_and_escalated(
    platform: Platform, run: AgentRun, runner: WorkflowRunner
) -> None:
    """The suite finds real problems, and the run pauses for a human."""
    state = runner.start(run, initial={"proposal": WEAK_PROPOSAL})

    assert state.status is RunStatus.PAUSED
    assert state.pending_approval_id
    assert state.data["falsification"]["verdict"] == "refuted"
    refuted = set(state.data["falsification"]["refuted"])
    assert {"holdout_leakage", "weak_baseline", "train_serve_skew"} <= refuted

    state = _approve_until_done(platform, runner, run, state)
    assert state.status is RunStatus.COMPLETED
    assert state.data["severity"] == "blocking"
    assert state.data["notified"] is True
    assert len(cases()) == 1
    assert len(notifications()) == 1


def test_a_strong_proposal_survives_and_does_not_escalate(
    run: AgentRun, runner: WorkflowRunner
) -> None:
    """A proposal that answers every falsifier clears without a human."""
    state = runner.start(run, initial={"proposal": STRONG_PROPOSAL})

    assert state.status is RunStatus.COMPLETED
    assert state.data["falsification"]["verdict"] == "survived"
    assert state.data["board"]["outcome"] == "clear"
    assert state.data["severity"] == "advisory"
    assert "escalate" not in state.history
    assert not notifications()


def test_a_hijacked_proposal_is_refused_before_anything_happens(
    run: AgentRun, runner: WorkflowRunner
) -> None:
    """Untrusted content that tries to direct the run gets no side effect."""
    state = runner.start(run, initial={"proposal": HOSTILE_PROPOSAL})

    assert state.status is RunStatus.COMPLETED
    assert state.history == ["intake", "refuse"]
    assert not cases()
    assert not notifications()


def test_resuming_does_not_duplicate_the_side_effect(
    platform: Platform, run: AgentRun, runner: WorkflowRunner
) -> None:
    """The paused step re-runs on resume; idempotency makes that safe."""
    state = runner.start(run, initial={"proposal": WEAK_PROPOSAL})
    assert state.history.count("record") == 1

    state = _approve_until_done(platform, runner, run, state)
    # The record step ran twice -- once before the pause, once after -- and
    # opened exactly one case.
    assert state.history.count("record") == 2
    assert len(cases()) == 1


def test_a_run_reconstructs_from_evidence_alone(
    platform: Platform, run: AgentRun, runner: WorkflowRunner
) -> None:
    """Who asked, what ran, what was decided, who approved, what changed."""
    state = runner.start(run, initial={"proposal": WEAK_PROPOSAL})
    _approve_until_done(platform, runner, run, state)

    reconstruction = reconstruct(platform.ledger, run.run_id)
    assert reconstruction.principal_id == "a.rivera"
    assert reconstruction.status == "completed"
    assert reconstruction.had_side_effect
    assert reconstruction.decisions
    assert reconstruction.approvals
    assert reconstruction.judgments
    assert not reconstruction.unverified_actions

    package = forensic_package(platform.ledger, run.run_id)
    assert package["chain_intact"] is True


def test_entitlements_withhold_what_the_caller_cannot_see(
    run: AgentRun, runner: WorkflowRunner
) -> None:
    """The engineer lacks model-risk-read, so one standard is filtered out.

    Withholding is reported rather than hidden: retrieval that looks complete
    when it is not is worse than retrieval that says what it held back.
    """
    state = runner.start(run, initial={"proposal": STRONG_PROPOSAL})
    assert state.data["withheld_standards"] == 1
    assert all("MLS-010" not in text for text in state.data["standards"])


def test_the_workflow_graph_is_well_formed() -> None:
    """Every edge points somewhere, and the commitment boundary is visible."""
    irreversible = {step.id for step in SPARRING_WORKFLOW.irreversible_steps}
    assert irreversible == {"escalate"}
    assert "flowchart TD" in SPARRING_WORKFLOW.as_mermaid()
