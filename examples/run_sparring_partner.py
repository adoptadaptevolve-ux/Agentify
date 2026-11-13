"""Run the sparring partner golden path end to end.

No API key, no network, no services. Everything below runs against the local
sandbox, which exercises exactly the same control paths as a deployment.

Four scenarios, in order:

1. A weak proposal is taken apart, a review case is opened, and the escalation
   pauses for a human because the board is worried.
2. The human approves, the run resumes, and the notification goes out.
3. A strong proposal survives the suite and closes without escalation.
4. A hostile proposal carrying an injection attempt is refused, and the
   prohibited capability is denied even when it is asked for directly.

Finally the evidence chain is verified and one run is reconstructed from it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities.sparring_partner import SPARRING_WORKFLOW
from capabilities.sparring_partner.capabilities import (
    SPARRING_CAPABILITIES,
    cases,
    notifications,
)
from capabilities.sparring_partner.sandbox import (
    HOSTILE_PROPOSAL,
    ML_STANDARDS,
    STRONG_PROPOSAL,
    WEAK_PROPOSAL,
    sandbox_judge,
)

from causeway.contracts.human import DecisionOutcome
from causeway.contracts.identity import (
    AuthStrength,
    Principal,
    PrincipalKind,
    WorkloadIdentity,
)
from causeway.errors import PolicyDenied
from causeway.evidence.replay import reconstruct
from causeway.policy.pack import compose, load_pack
from causeway.runtime.budgets import RunBudget
from causeway.runtime.workflow import RunStatus, WorkflowRunner
from causeway.sdk.platform import Platform

ROOT = Path(__file__).resolve().parents[1]


def rule(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def build_platform() -> Platform:
    """Wire the local sandbox exactly as a team would in their own repo."""
    pack = compose(
        load_pack(ROOT / "policies" / "platform-baseline.json"),
        load_pack(ROOT / "policies" / "ml-platform.json"),
        name="ml-platform-effective",
        version="2026.09.18",
    )
    platform = Platform.local(
        pack=pack,
        capabilities=list(SPARRING_CAPABILITIES),
        judge=sandbox_judge(),
    )
    for chunk in ML_STANDARDS:
        platform.retrieval.ingest(chunk)
    return platform


ENGINEER = Principal(
    id="a.rivera",
    kind=PrincipalKind.HUMAN,
    tenant="acme",
    roles=frozenset({"ml-engineer"}),
    # Deliberately does NOT hold "model-risk-read": one standard is withheld,
    # which is what entitlement-aware retrieval looks like from the inside.
    entitlements=frozenset({"ml-standards-read"}),
    auth_strength=AuthStrength.MULTI_FACTOR,
    session_id="sess-1",
)

AGENT_WORKLOAD = WorkloadIdentity(
    service="sparring-partner",
    version="1.0.0",
    environment="sandbox",
    image_digest="sha256:demo",
    attested=True,
)

RUN_CAPABILITIES = frozenset(
    {"open_model_review_case", "close_model_review_case", "notify_model_owner"}
)


def begin(platform: Platform, purpose: str = "model_review"):
    """Start a governed run for the sparring partner."""
    return platform.begin(
        principal=ENGINEER,
        workload=AGENT_WORKLOAD,
        capabilities=RUN_CAPABILITIES,
        purpose=purpose,
        domain="ml-platform",
        budget=RunBudget(max_steps=20, max_tool_calls=6, max_judgment_calls=40),
        workflow_id=SPARRING_WORKFLOW.id,
        workflow_version=SPARRING_WORKFLOW.version,
    )


def show(state) -> None:
    """Print the outcome of a run in the terms a person would care about."""
    print(f"  status      : {state.status.value}")
    print(f"  steps       : {' -> '.join(state.history)}")
    if state.error:
        print(f"  error       : {state.error}")
    board = state.data.get("board", {})
    falsification = state.data.get("falsification", {})
    if falsification:
        print(f"  falsifiers  : {falsification.get('summary')}")
        print(f"  refuted     : {', '.join(falsification.get('refuted', [])) or 'none'}")
    if board:
        print(
            f"  board       : {board.get('outcome')} "
            f"(concern {board.get('concern', 0):.2f}, "
            f"confidence {board.get('confidence', 0):.2f}, "
            f"disagreement {board.get('disagreement', 0):.2f})"
        )
        print(f"  rationale   : {board.get('rationale')}")
    if state.data.get("withheld_standards"):
        print(f"  withheld    : {state.data['withheld_standards']} standard(s) not entitled")
    if state.data.get("severity"):
        print(f"  severity    : {state.data['severity']}")
    if state.data.get("case_id"):
        print(f"  case        : {state.data['case_id']}")
    if state.pending_approval_id:
        print(f"  paused on   : approval {state.pending_approval_id}")


def main() -> int:
    """Run every scenario."""
    platform = build_platform()
    runner = WorkflowRunner(SPARRING_WORKFLOW)

    rule("1. A weak proposal meets the sparring partner")
    run_one = begin(platform)
    state = runner.start(run_one, initial={"proposal": WEAK_PROPOSAL})
    show(state)

    rule("2. A human decides, and the run resumes where it paused")
    if state.status is not RunStatus.PAUSED:
        print("  the run did not pause; nothing to approve")
    # A consequential journey can pause more than once -- here, for the case
    # and again for the notification. Each pause is its own digest-bound item.
    while state.status is RunStatus.PAUSED:
        item = platform.approvals.item(state.pending_approval_id or "")
        print(f"  queue       : {item.queue}")
        print(f"  packet      : {item.packet.intent_summary}")
        print(f"  risk factors: {', '.join(item.packet.risk_factors[:3])}")
        print(f"  rollback    : {item.packet.rollback}")
        platform.approvals.decide(
            approval_id=item.approval_id,
            approver_id="m.okonkwo",
            approver_authority=frozenset({"model-risk-approver"}),
            outcome=DecisionOutcome.APPROVED,
            rationale="Findings are real; the owner should see them.",
        )
        state = runner.resume(run_one, state)
        print()
    show(state)
    print(f"  notified    : {state.data.get('notified')}")

    rule("3. A strong proposal survives the suite")
    run_two = begin(platform)
    state_two = runner.start(run_two, initial={"proposal": STRONG_PROPOSAL})
    show(state_two)

    rule("4. A hostile proposal, and a prohibited capability")
    run_three = begin(platform)
    state_three = runner.start(run_three, initial={"proposal": HOSTILE_PROPOSAL})
    show(state_three)

    # The sparring run was never granted these capabilities, so the first
    # refusal comes from delegation, before policy is even consulted.
    run_four = begin(platform)
    try:
        run_four.invoke_tool(
            "delete_experiment_lineage",
            {"experiment_id": "exp-8841"},
            purpose="model_review",
            idempotency_key="never",
        )
        print("  FAILURE: a prohibited capability executed")
        return 1
    except Exception as exc:
        print(f"  least authority : {type(exc).__name__} -- the run never held it")

    # Now grant them, to show the layer underneath. Defence in depth means the
    # second control has to hold on its own, not because the first one fired.
    over_privileged = platform.begin(
        principal=ENGINEER,
        workload=AGENT_WORKLOAD,
        capabilities=RUN_CAPABILITIES
        | {"delete_experiment_lineage", "promote_model_to_production"},
        purpose="model_review",
        domain="ml-platform",
        workflow_id=SPARRING_WORKFLOW.id,
        workflow_version=SPARRING_WORKFLOW.version,
    )
    try:
        over_privileged.invoke_tool(
            "delete_experiment_lineage",
            {"experiment_id": "exp-8841"},
            purpose="model_review",
            idempotency_key="never",
        )
        print("  FAILURE: a prohibited capability executed")
        return 1
    except PolicyDenied as denied:
        print(f"  prohibited      : policy refused, reason_code={denied.reason_code}")

    try:
        over_privileged.invoke_tool(
            "promote_model_to_production",
            {"model_id": "churn-propensity-v3", "validation_ref": "none"},
            purpose="model_review",
            idempotency_key="promote-1",
        )
        print("  FAILURE: promotion succeeded from a review run")
        return 1
    except PolicyDenied as denied:
        print(f"  promotion       : policy refused, reason_code={denied.reason_code}")

    rule("5. The evidence chain")
    platform.ledger.verify()
    print(f"  chain       : intact across {len(platform.ledger.events())} events")
    reconstruction = reconstruct(platform.ledger, run_one.run_id)
    print(f"  run         : {reconstruction.run_id}")
    print(f"  principal   : {reconstruction.principal_id}")
    print(f"  status      : {reconstruction.status}")
    print(f"  decisions   : {len(reconstruction.decisions)}")
    print(f"  actions     : {len(reconstruction.actions)}")
    print(f"  approvals   : {len(reconstruction.approvals)}")
    print(f"  judgments   : {len(reconstruction.judgments)}")
    print(f"  side effect : {reconstruction.had_side_effect}")

    rule("6. What actually happened in the world")
    for case_id, case in cases().items():
        print(f"  case {case_id}: {case['severity']} on {case['model']} ({case['status']})")
    for notification in notifications():
        print(f"  notified {notification['channel']} about {notification['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
