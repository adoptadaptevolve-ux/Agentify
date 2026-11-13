"""Retries must not duplicate side effects, and a receipt is the only success."""

from __future__ import annotations

import pytest
from capabilities.sparring_partner.capabilities import cases

from causeway.contracts.action import ResultClass
from causeway.errors import ContractViolation, PolicyDenied
from causeway.sdk.run import AgentRun


def _open_case(run: AgentRun, key: str, summary: str = "findings"):
    return run.invoke_tool(
        "open_model_review_case",
        {"model_id": "churn-v3", "summary": summary, "severity": "advisory"},
        purpose="model_review",
        idempotency_key=key,
    )


def test_a_repeated_key_returns_the_first_receipt(run: AgentRun) -> None:
    """The second attempt is deduplicated, not executed."""
    first = _open_case(run, "case-1")
    second = _open_case(run, "case-1")

    assert first.result_class is ResultClass.SUCCESS
    assert second.result_class is ResultClass.DUPLICATE
    assert second.action_id == first.action_id
    assert len(cases()) == 1


def test_reusing_a_key_for_a_different_payload_is_refused(run: AgentRun) -> None:
    """Silently deduplicating a different action would suppress a real one."""
    _open_case(run, "case-2", summary="one")
    with pytest.raises(ContractViolation, match="different action"):
        _open_case(run, "case-2", summary="two")


def test_r2_requires_an_idempotency_key(run: AgentRun) -> None:
    """The tier posture attaches the obligation; the guard enforces it."""
    with pytest.raises(PolicyDenied, match="idempotency key"):
        run.invoke_tool(
            "open_model_review_case",
            {"model_id": "churn-v3", "summary": "s", "severity": "advisory"},
            purpose="model_review",
        )


def test_a_receipt_carries_its_evidence_reference(run: AgentRun) -> None:
    """A receipt points at the ledger entry that proves it."""
    receipt = _open_case(run, "case-3")
    assert receipt.evidence_ref.startswith("evidence:")
    assert receipt.verified is True
    assert receipt.is_reversible
    assert receipt.compensation_capability == "close_model_review_case"


def test_post_execution_verification_runs(run: AgentRun) -> None:
    """The capability's post-condition is what turns a call into a side effect."""
    receipt = _open_case(run, "case-4")
    assert cases()[receipt.notes[0].removeprefix("external_ref=")]["status"] == "open"


def test_semantic_validators_run_before_anything_else(run: AgentRun) -> None:
    """A blocking case with no findings never becomes an action at all."""
    from causeway.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="must list the findings"):
        run.invoke_tool(
            "open_model_review_case",
            {"model_id": "churn-v3", "summary": "s", "severity": "blocking", "findings": []},
            purpose="model_review",
            idempotency_key="case-5",
        )
    assert not cases()


def test_unknown_arguments_are_rejected(run: AgentRun) -> None:
    """Dropping an argument the caller meant is how approvals drift."""
    from causeway.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="unknown arguments"):
        run.invoke_tool(
            "open_model_review_case",
            {
                "model_id": "churn-v3",
                "summary": "s",
                "severity": "advisory",
                "bypass_review": True,
            },
            purpose="model_review",
            idempotency_key="case-6",
        )
