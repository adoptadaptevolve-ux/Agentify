"""A failed run undoes what it already did, newest first, through the guard."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES, cases

from causeway.contracts.action import ResultClass
from causeway.contracts.identity import Principal, WorkloadIdentity
from causeway.errors import ConfigurationError
from causeway.evidence import events as event_types
from causeway.evidence.replay import reconstruct
from causeway.policy.pack import PolicyPack
from causeway.registry.capability import Capability
from causeway.runtime import (
    CompensationEntry,
    CompensationStatus,
    RunBudget,
    RunStatus,
    Step,
    StepOutcome,
    Workflow,
    WorkflowRunner,
    compensate_run,
)
from causeway.sdk.platform import Platform
from causeway.sdk.run import AgentRun


def _open(run: AgentRun, model_id: str) -> str:
    receipt = run.invoke_tool(
        "open_model_review_case",
        {"model_id": model_id, "summary": "findings", "severity": "advisory"},
        idempotency_key=f"case:{run.run_id}:{model_id}",
    )
    return receipt.notes[0].removeprefix("external_ref=")


def _opening(model_id: str, next_step: str) -> Step:
    def handler(run: AgentRun, data: dict[str, Any]) -> StepOutcome:
        return StepOutcome.go(next_step, **{f"case_{model_id}": _open(run, model_id)})

    return Step(id=f"open_{model_id}", handler=handler, edges=frozenset({next_step}),
                has_side_effect=True, compensation="close_model_review_case")


def _explode(run: AgentRun, data: dict[str, Any]) -> StepOutcome:
    raise RuntimeError("downstream system rejected the notification")


def _done(run: AgentRun, data: dict[str, Any]) -> StepOutcome:
    return StepOutcome.finish()


def _workflow(*steps: Step) -> Workflow:
    return Workflow(id="saga-test", version="1", start=steps[0].id, steps=steps)


TWO_CASES_THEN_FAIL = _workflow(
    _opening("m1", "open_m2"),
    _opening("m2", "explode"),
    Step(id="explode", handler=_explode, edges=frozenset({"done"})),
    Step(id="done", handler=_done, terminal=True),
)


def test_a_failed_run_closes_the_cases_it_opened(run: AgentRun) -> None:
    state = WorkflowRunner(TWO_CASES_THEN_FAIL).start(run)

    assert state.status is RunStatus.COMPENSATED
    assert "downstream system rejected" in (state.error or "")
    assert state.compensation is not None and state.compensation.complete
    assert {case["status"] for case in cases().values()} == {"closed"}


def test_undo_runs_newest_first(run: AgentRun) -> None:
    state = WorkflowRunner(TWO_CASES_THEN_FAIL).start(run)

    assert state.compensation is not None
    undone = [o.action_id for o in state.compensation.outcomes]
    recorded = [entry.action_id for entry in run.compensations]
    assert undone == list(reversed(recorded))


def test_the_rollback_is_in_the_evidence_chain(run: AgentRun) -> None:
    WorkflowRunner(TWO_CASES_THEN_FAIL).start(run)

    events = run.ledger.events(run_id=run.run_id)
    assert sum(e.event_type == event_types.ACTION_COMPENSATED for e in events) == 2
    reconstruction = reconstruct(run.ledger, run.run_id)
    assert reconstruction.status == "compensated"
    failed = [e for e in events if e.event_type == event_types.RUN_FAILED][-1]
    assert failed.payload["compensation"]["complete"] is True


def test_a_successful_run_undoes_nothing(run: AgentRun) -> None:
    flow = _workflow(_opening("m1", "done"), Step(id="done", handler=_done, terminal=True))
    state = WorkflowRunner(flow).start(run)

    assert state.status is RunStatus.COMPLETED
    assert state.compensation is None
    assert {case["status"] for case in cases().values()} == {"open"}


def test_a_run_that_runs_out_of_budget_still_cleans_up(
    platform: Platform, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """Undo does not charge the budget, or a budget failure could never be unwound."""
    tight = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"open_model_review_case", "close_model_review_case"}),
        purpose="model_review",
        domain="ml-platform",
        budget=RunBudget(max_steps=10, max_tool_calls=1),
    )
    flow = _workflow(
        _opening("m1", "open_m2"),
        _opening("m2", "done"),
        Step(id="done", handler=_done, terminal=True),
    )
    state = WorkflowRunner(flow).start(tight)

    assert state.status is RunStatus.COMPENSATED
    assert {case["status"] for case in cases().values()} == {"closed"}


def test_without_authority_to_undo_the_action_is_left_for_a_person(
    platform: Platform, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """Rolling back is not a licence to skip controls: the run must hold the undo."""
    narrow = platform.begin(
        principal=engineer,
        workload=workload,
        capabilities=frozenset({"open_model_review_case"}),
        purpose="model_review",
        domain="ml-platform",
    )
    flow = _workflow(
        _opening("m1", "explode"),
        Step(id="explode", handler=_explode, edges=frozenset({"done"})),
        Step(id="done", handler=_done, terminal=True),
    )
    state = WorkflowRunner(flow).start(narrow)

    assert state.status is RunStatus.FAILED
    assert state.compensation is not None
    (outcome,) = state.compensation.outstanding
    assert outcome.status is CompensationStatus.FAILED
    assert "AttenuationViolation" in outcome.detail
    assert {case["status"] for case in cases().values()} == {"open"}


def test_an_undo_with_no_recipe_is_reported_as_manual(run: AgentRun) -> None:
    run.compensations.append(
        CompensationEntry(
            action_id="act_external",
            capability="refund_customer",
            compensation_capability="reverse_refund",
            arguments=None,
        )
    )
    report = compensate_run(run, reason="test")

    (outcome,) = report.outcomes
    assert outcome.status is CompensationStatus.MANUAL
    assert not report.complete


def test_undoing_twice_undoes_once(run: AgentRun) -> None:
    """The idempotency key on the undo turns a repeated rollback into a duplicate."""
    _open(run, "m1")
    first = compensate_run(run, reason="first")
    second = compensate_run(run, reason="retry")

    assert first.complete and second.complete
    assert (
        first.outcomes[0].compensation_action_id == second.outcomes[0].compensation_action_id
    )
    executed_closes = [
        e
        for e in run.ledger.events(run_id=run.run_id)
        if e.event_type == event_types.ACTION_EXECUTED
        and e.payload["capability"] == "close_model_review_case"
    ]
    assert len(executed_closes) == 1


def test_a_resumed_step_does_not_record_the_same_action_twice(run: AgentRun) -> None:
    _open(run, "m1")
    _open(run, "m1")  # duplicate receipt for the same action
    assert len(run.compensations) == 1


def test_cancel_can_undo_on_request(run: AgentRun) -> None:
    runner = WorkflowRunner(TWO_CASES_THEN_FAIL)
    _open(run, "m1")
    state = runner.cancel(
        run,
        runner_state(run),
        reason="operator stopped the run",
        compensate=True,
    )
    assert state.status is RunStatus.CANCELLED
    assert state.compensation is not None and state.compensation.complete
    assert {case["status"] for case in cases().values()} == {"closed"}


def test_cancel_leaves_actions_alone_by_default(run: AgentRun) -> None:
    _open(run, "m1")
    state = WorkflowRunner(TWO_CASES_THEN_FAIL).cancel(run, runner_state(run), reason="stop")
    assert state.compensation is None
    assert {case["status"] for case in cases().values()} == {"open"}


def runner_state(run: AgentRun) -> Any:
    from causeway.runtime import RunState

    return RunState(
        run_id=run.run_id,
        workflow_id="saga-test",
        workflow_version="1",
        status=RunStatus.RUNNING,
        current_step="explode",
    )


def test_an_undo_recipe_needs_something_to_call() -> None:
    template = SPARRING_CAPABILITIES[0]
    with pytest.raises(ConfigurationError, match="needs a compensation_capability"):
        Capability(
            id="x",
            version="1",
            owner="t",
            summary="s",
            risk_tier=template.risk_tier,
            arguments=(),
            handler=template.handler,
            compensation_arguments=lambda args, ref: {},
        )


def test_a_rollback_after_a_restart_does_not_undo_twice(
    pack: PolicyPack, tmp_path: Path, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """With durable state, the undo's idempotency key outlives the process."""
    state_path = tmp_path / "state.sqlite3"
    reports = []
    for _ in range(2):
        platform = Platform.local(
            pack=pack, capabilities=list(SPARRING_CAPABILITIES), state_path=state_path
        )
        try:
            run = platform.begin(
                principal=engineer,
                workload=workload,
                capabilities=frozenset({"open_model_review_case", "close_model_review_case"}),
                purpose="model_review",
                domain="ml-platform",
                run_id="run-saga-restart",
            )
            _open(run, "m1")  # first lifetime executes; second gets the original receipt
            reports.append(compensate_run(run, reason="failed"))
        finally:
            platform.close()

    first, second = reports
    assert first.complete and second.complete
    assert (
        first.outcomes[0].compensation_action_id == second.outcomes[0].compensation_action_id
    )
    assert len(cases()) == 1
    assert next(iter(cases().values()))["status"] == "closed"


def test_receipts_from_a_compensated_run_name_the_undo(run: AgentRun) -> None:
    receipt = run.invoke_tool(
        "open_model_review_case",
        {"model_id": "m9", "summary": "findings", "severity": "advisory"},
        idempotency_key="case:m9",
    )
    assert receipt.result_class is ResultClass.SUCCESS
    (entry,) = run.compensations
    assert entry.compensation_capability == "close_model_review_case"
    assert entry.arguments is not None and entry.arguments["case_id"] == entry.external_ref
