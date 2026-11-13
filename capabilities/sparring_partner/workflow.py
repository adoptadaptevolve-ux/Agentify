"""The sparring partner workflow.

An explicit graph, because the journey is consequential and because a reviewer
should be able to see it as a diagram:

    intake -> ground -> spar -> deliberate -> record -> escalate -> report
      |                                                    |
      +--> refuse (untrusted input tried to take over)     +--> report

Open-ended reasoning happens inside ``spar`` and ``deliberate``. What the model
never gets to decide is which node runs next.

Two steps have side effects (``record`` and ``escalate``), and the runner
checkpoints on both sides of each. ``escalate`` can pause the whole run waiting
on a human, and resume in the same step afterwards -- the idempotency key means
re-entering it cannot send a second notification.
"""

from __future__ import annotations

from typing import Any

from causeway.contracts.risk import RiskTier
from causeway.errors import PolicyDenied
from causeway.knowledge.memory import MemoryScope
from causeway.runtime.workflow import Step, StepOutcome, Workflow, WorkflowRunner
from causeway.sdk.run import AgentRun
from causeway.verify.supervisors import BoardOutcome, ReviewCase

from .falsifiers import MODEL_CHANGE_SUITE
from .supervisors import sparring_board

INJECTION_REFUSAL_THRESHOLD = 0.7
"""Matches the policy pack's deny threshold. The workflow stops early for the
same reason the policy engine would stop it later: there is no value in
spending judgment calls on a document that is trying to hijack the run."""


def _intake(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Treat the proposal as untrusted input and sweep it before reading it."""
    proposal = state["proposal"]
    reading = run.guardrails({"untrusted_proposal": proposal["summary"]})
    injection = reading.signals.get("injection_probability", 0.0)
    run.emit_evidence(
        "sparring.intake",
        {
            "model_id": proposal["model_id"],
            "injection_probability": injection,
            "author": proposal.get("author", "unknown"),
        },
    )
    if injection >= INJECTION_REFUSAL_THRESHOLD:
        return StepOutcome.go(
            "refuse", refusal_reason="untrusted content attempted to direct the run"
        )
    return StepOutcome.go("ground", guardrails=reading.signals)


def _ground(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Retrieve the team's own standards, filtered by the caller's entitlements."""
    proposal = state["proposal"]
    result = run.retrieve(
        f"evaluation split baseline segment promotion {proposal['model_id']}",
        purpose="model_review",
        limit=4,
    )
    return StepOutcome.go(
        "spar",
        sources=list(result.citations),
        standards=[chunk.text for chunk in result.chunks],
        withheld_standards=result.withheld,
    )


def _spar(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Try to refute every claim the proposal rests on. One batched call."""
    proposal = state["proposal"]
    subject = {
        "proposal": proposal["summary"],
        "claims": proposal.get("claims", {}),
        "experiment_refs": proposal.get("experiment_refs", []),
        "standards": state.get("standards", []),
    }
    report = run.falsify(MODEL_CHANGE_SUITE, subject, subject=proposal["model_id"])
    run.remember(
        f"sparring:{proposal['model_id']}",
        report.summary(),
        scope=MemoryScope.CASE,
        provenance=f"falsification:{MODEL_CHANGE_SUITE.id}",
        confidence=report.confidence,
    )
    return StepOutcome.go(
        "deliberate",
        falsification={
            "verdict": report.verdict.value,
            "concern": report.concern,
            "refuted": [finding.falsifier_id for finding in report.refutations],
            "blocking": [finding.falsifier_id for finding in report.blocking_refutations],
            "summary": report.summary(),
        },
    )


def _deliberate(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Convene the board. Independent verdicts, deterministic aggregation."""
    proposal = state["proposal"]
    case = ReviewCase.build(
        subject={
            "proposal": proposal["summary"],
            "claims": proposal.get("claims", {}),
            "standards": state.get("standards", []),
            "experiment_refs": proposal.get("experiment_refs", []),
        },
        risk_tier=RiskTier.R2_REVERSIBLE_WRITE,
        claims=proposal.get("claims", {}),
        sources=tuple(state.get("sources", ())),
    )
    decision = run.deliberate(case, sparring_board())
    return StepOutcome.go(
        "record",
        board={
            "outcome": decision.outcome.value,
            "concern": decision.concern,
            "confidence": decision.confidence,
            "disagreement": decision.disagreement,
            "rationale": decision.rationale,
            "findings": [f"{f.code}: {f.detail}" for f in decision.all_findings],
        },
        board_signals=decision.signals(),
    )


def _severity(state: dict[str, Any]) -> str:
    """Map the board and the suite onto the case severity the team uses."""
    falsification = state.get("falsification", {})
    board = state.get("board", {})
    if board.get("outcome") == BoardOutcome.BLOCK.value or falsification.get("blocking"):
        return "blocking"
    if falsification.get("refuted") or board.get("outcome") == BoardOutcome.REVIEW.value:
        return "concern"
    return "advisory"


def _record(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Open the review case. Reversible, verified after execution."""
    proposal = state["proposal"]
    severity = _severity(state)
    findings = list(state.get("falsification", {}).get("refuted", []))
    if severity == "blocking" and not findings:
        # The case schema refuses a blocking case with no findings, and the
        # board's own findings are the honest substitute.
        findings = [line.split(":", 1)[0] for line in state.get("board", {}).get("findings", [])]

    receipt = run.invoke_tool(
        "open_model_review_case",
        {
            "model_id": proposal["model_id"],
            "summary": state.get("falsification", {}).get("summary", "no findings"),
            "severity": severity,
            "findings": findings,
        },
        purpose="model_review",
        idempotency_key=f"case:{run.run_id}:{proposal['model_id']}",
        signals=state.get("board_signals"),
    )
    next_step = "escalate" if severity in ("blocking", "concern") else "report"
    return StepOutcome.go(
        next_step,
        severity=severity,
        case_id=receipt.notes[0].removeprefix("external_ref=") if receipt.notes else None,
        case_receipt={
            "action_id": receipt.action_id,
            "result": receipt.result_class.value,
            "verified": receipt.verified,
            "evidence_ref": receipt.evidence_ref,
        },
    )


def _escalate(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Tell the model owner. R3, so this is where a human may be pulled in.

    If policy routes this to review, ``invoke_tool`` raises ``ApprovalRequired``
    and the runner pauses the whole run here. Nothing about this function has to
    know that: it is written as though the call simply happens, and the platform
    supplies the pause, the checkpoint and the resume.
    """
    proposal = state["proposal"]
    findings = state.get("falsification", {}).get("refuted", [])
    try:
        receipt = run.invoke_tool(
            "notify_model_owner",
            {
                "model_id": proposal["model_id"],
                "message": (
                    f"The sparring partner rated this proposal {state['severity']}. "
                    f"Refuted: {', '.join(findings) or 'none'}. "
                    f"See case {state.get('case_id')}."
                ),
                "case_id": state.get("case_id") or "",
            },
            purpose="model_review",
            destination="model-owner-direct",
            idempotency_key=f"notify:{run.run_id}:{proposal['model_id']}",
            signals=state.get("board_signals"),
        )
    except PolicyDenied as denied:
        # A denial is a result, not a crash: the case is already open, and the
        # run reports that the owner was not notified and why.
        run.emit_evidence(
            "sparring.escalation_denied",
            {"model_id": proposal["model_id"], "reason_code": denied.reason_code},
        )
        return StepOutcome.go("report", notified=False, escalation_reason=denied.reason_code)
    return StepOutcome.go("report", notified=True, notify_action_id=receipt.action_id)


def _report(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Finish, recording the verdict the team will read."""
    run.emit_evidence(
        "sparring.completed",
        {
            "model_id": state["proposal"]["model_id"],
            "severity": state.get("severity"),
            "board_outcome": state.get("board", {}).get("outcome"),
            "notified": state.get("notified", False),
        },
    )
    return StepOutcome.finish()


def _refuse(run: AgentRun, state: dict[str, Any]) -> StepOutcome:
    """Stop without touching anything, and say why."""
    run.emit_evidence(
        "sparring.refused",
        {
            "model_id": state["proposal"]["model_id"],
            "reason": state.get("refusal_reason", "refused"),
        },
    )
    return StepOutcome.finish(severity="refused", notified=False)


SPARRING_WORKFLOW = Workflow(
    id="sparring_partner",
    version="1.0.0",
    start="intake",
    description="Stress-test a proposed model change, then record and escalate the findings",
    steps=(
        Step(
            id="intake",
            handler=_intake,
            description="Sweep the proposal as untrusted input",
            edges=frozenset({"ground", "refuse"}),
        ),
        Step(
            id="ground",
            handler=_ground,
            description="Retrieve the team's standards, entitlement-filtered",
            edges=frozenset({"spar"}),
        ),
        Step(
            id="spar",
            handler=_spar,
            description="Run the falsification suite",
            edges=frozenset({"deliberate"}),
        ),
        Step(
            id="deliberate",
            handler=_deliberate,
            description="Convene the supervisor board",
            edges=frozenset({"record"}),
        ),
        Step(
            id="record",
            handler=_record,
            description="Open the review case",
            edges=frozenset({"escalate", "report"}),
            has_side_effect=True,
            compensation="close_model_review_case",
        ),
        Step(
            id="escalate",
            handler=_escalate,
            description="Notify the model owner",
            edges=frozenset({"report"}),
            has_side_effect=True,
        ),
        Step(id="report", handler=_report, description="Record the verdict", terminal=True),
        Step(id="refuse", handler=_refuse, description="Refuse hijacked input", terminal=True),
    ),
)


def run_sparring_session(run: AgentRun, proposal: dict[str, Any]) -> Any:
    """Drive one sparring session to completion or to a pause."""
    runner = WorkflowRunner(SPARRING_WORKFLOW)
    return runner.start(run, initial={"proposal": proposal})
