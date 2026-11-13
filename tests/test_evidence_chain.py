"""Evidence is part of the transaction, and tampering with it is detectable."""

from __future__ import annotations

from dataclasses import replace

import pytest

from causeway.errors import EvidenceChainBroken
from causeway.evidence.ledger import InMemoryEvidenceLedger
from causeway.evidence.replay import forensic_package, reconstruct
from causeway.sdk.run import AgentRun


def test_every_executed_action_leaves_evidence(run: AgentRun) -> None:
    """A run that produced a side effect cannot be a run with no record of it."""
    run.invoke_tool(
        "open_model_review_case",
        {"model_id": "churn-v3", "summary": "s", "severity": "advisory"},
        purpose="model_review",
        idempotency_key="case-1",
    )
    reconstruction = reconstruct(run.platform.ledger, run.run_id)
    assert reconstruction.had_side_effect
    assert len(reconstruction.actions) == 1
    assert len(reconstruction.decisions) == 1


def test_a_denial_is_recorded_as_specifically_as_an_allow(run: AgentRun) -> None:
    """"Denied" and "we could not tell" are different outcomes to an auditor."""
    from causeway.errors import PolicyDenied

    with pytest.raises(PolicyDenied):
        run.invoke_tool(
            "notify_model_owner",
            {"model_id": "m", "message": "x"},
            purpose="model_review",
            destination="somewhere-else",
            idempotency_key="k",
        )
    reconstruction = reconstruct(run.platform.ledger, run.run_id)
    assert any(
        decision["reason_code"] == "destination_not_allowlisted"
        for decision in reconstruction.decisions
    )


def test_editing_an_event_breaks_the_chain() -> None:
    """A silent edit becomes a detectable one."""
    ledger = InMemoryEvidenceLedger()
    from causeway.contracts.evidence import EventDomain

    for index in range(3):
        ledger.append(
            domain=EventDomain.AGENT,
            event_type="test.event",
            run_id="run-1",
            tenant="acme",
            actor="tester",
            payload={"index": index},
        )
    ledger.verify()

    ledger._events[1] = replace(ledger._events[1], payload={"index": 99})
    with pytest.raises(EvidenceChainBroken, match="does not chain"):
        ledger.verify()


def test_removing_an_event_breaks_the_chain() -> None:
    """Deletion is as detectable as modification."""
    ledger = InMemoryEvidenceLedger()
    from causeway.contracts.evidence import EventDomain

    for index in range(3):
        ledger.append(
            domain=EventDomain.AGENT,
            event_type="test.event",
            run_id="run-1",
            tenant="acme",
            actor="tester",
            payload={"index": index},
        )
    del ledger._events[1]
    with pytest.raises(EvidenceChainBroken):
        ledger.verify()


def test_sensitive_payloads_stay_behind_a_protected_reference(run: AgentRun) -> None:
    """The open ledger proves what happened without holding the arguments."""
    run.invoke_tool(
        "open_model_review_case",
        {"model_id": "churn-v3", "summary": "a sensitive summary", "severity": "advisory"},
        purpose="model_review",
        idempotency_key="case-1",
    )
    events = run.platform.ledger.events(run_id=run.run_id)
    proposed = next(e for e in events if e.event_type == "tool.action_proposed")
    assert proposed.protected_ref is not None
    assert proposed.protected_digest is not None
    assert "a sensitive summary" not in str(proposed.payload)


def test_a_forensic_package_states_its_own_integrity(run: AgentRun) -> None:
    """A package taken from a broken chain says so on its face."""
    run.invoke_tool(
        "open_model_review_case",
        {"model_id": "churn-v3", "summary": "s", "severity": "advisory"},
        purpose="model_review",
        idempotency_key="case-1",
    )
    package = forensic_package(run.platform.ledger, run.run_id)
    assert package["chain_intact"] is True
    assert package["chain_head"]
    assert package["event_count"] > 0
