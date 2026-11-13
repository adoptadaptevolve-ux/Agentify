"""``AgentRun``: the developer surface.

This is the whole SDK a domain team touches. Everything it exposes is a
governed operation -- there is no ``raw_model()``, no ``http()``, no way to
reach a credential. That is not a limitation the platform apologises for; it is
the product. A team that can only do governed things cannot accidentally do an
ungoverned one, and does not have to remember which is which.

Three behaviours are worth calling out because they are what "paved road" means
in practice:

* **Guardrails are automatic.** Any side-effecting capability gets the injection
  and egress sweep before policy sees it. A team does not opt in, and cannot
  forget.
* **Delegation narrows on every hop.** ``spawn`` produces a subagent with a
  strict subset of this run's authority, and ``invoke_tool`` narrows down to the
  single capability being called before the envelope reaches the gateway.
* **Budgets charge automatically.** Retrieval, judgment and tool calls are all
  metered by the call that performs them, so a runaway run stops on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from ..contracts.action import ExecutionReceipt
from ..contracts.delegation import Constraints, SignedDelegation
from ..contracts.evidence import EventDomain
from ..contracts.identity import DataClass, Principal, WorkloadIdentity
from ..contracts.judgment import JudgmentResult, Question, State
from ..contracts.risk import RiskTier
from ..evidence import events as event_types
from ..gateway.transaction_guard import ReviewContext
from ..ids import new_run_id
from ..judgment.questions import GuardrailReading, read_guardrails
from ..judgment.recording import RecordingJudge
from ..knowledge.memory import MemoryEntry, MemoryScope
from ..knowledge.retrieval import RetrievalResult
from ..runtime.budgets import BudgetLedger
from ..verify.falsify import FalsificationReport, FalsificationSuite
from ..verify.supervisors import BoardDecision, ReviewCase, Supervisor, SupervisorBoard

if TYPE_CHECKING:  # pragma: no cover
    from .platform import Platform

JUDGMENT_COST = Decimal("0.0002")
"""Nominal per-call cost charged against the run's spend budget. Replaced by
real gateway pricing in a deployment; present here so budget behaviour is
exercised in the sandbox."""


@dataclass(slots=True)
class AgentRun:
    """One governed run, and everything a domain team can do inside it."""

    platform: Platform
    run_id: str
    principal: Principal
    workload: WorkloadIdentity
    delegation: SignedDelegation
    budget: BudgetLedger
    domain: str
    parent_run_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def tenant(self) -> str:
        """The tenant this run belongs to."""
        return self.principal.tenant

    @property
    def ledger(self) -> Any:
        """The evidence ledger, for steps that record their own milestones."""
        return self.platform.ledger

    @property
    def capabilities(self) -> frozenset[str]:
        """The capabilities this run is allowed to reach at all."""
        return self.delegation.body.capabilities

    # -- knowledge ------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        *,
        purpose: str | None = None,
        limit: int = 5,
        max_classification: DataClass = DataClass.CONFIDENTIAL,
    ) -> RetrievalResult:
        """Retrieve, filtered by this principal's entitlements, and record it."""
        result = self.platform.retrieval.query(
            query,
            principal=self.principal,
            purpose=purpose or self.delegation.body.purpose,
            limit=limit,
            max_classification=max_classification,
        )
        self.budget.charge_retrieval(len(result.chunks))
        self.ledger.append(
            domain=EventDomain.RETRIEVAL,
            event_type=event_types.RETRIEVAL_PERFORMED,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload={
                "query": query,
                "purpose": result.purpose,
                "returned": len(result.chunks),
                "withheld": result.withheld,
                "citations": list(result.citations),
            },
        )
        return result

    def remember(
        self,
        key: str,
        value: str,
        *,
        scope: MemoryScope = MemoryScope.TASK,
        provenance: str = "model_summary",
        confidence: float = 0.7,
        ttl: timedelta | None = None,
        authoritative: bool = False,
    ) -> MemoryEntry:
        """Write a scoped memory entry with its provenance attached."""
        entry = self.platform.memory.write(
            key=key,
            value=value,
            scope=scope,
            owner=self.principal.id,
            tenant=self.tenant,
            provenance=provenance,
            confidence=confidence,
            ttl=ttl,
            authoritative=authoritative,
        )
        self.ledger.append(
            domain=EventDomain.MEMORY,
            event_type=event_types.MEMORY_WRITTEN,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload={
                "key": key,
                "scope": scope.value,
                "provenance": provenance,
                "confidence": confidence,
                "authoritative": authoritative,
            },
        )
        return entry

    def recall(self, key: str, *, scope: MemoryScope = MemoryScope.TASK) -> MemoryEntry | None:
        """Read a memory entry. Treat the result as untrusted input."""
        return self.platform.memory.read(key=key, scope=scope, tenant=self.tenant)

    # -- judgment -------------------------------------------------------------

    @property
    def judge(self) -> RecordingJudge:
        """The judgment plane, wrapped so every call lands in evidence."""
        return RecordingJudge(
            self.platform.judge,
            self.ledger,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
        )

    def ask(self, state: State, questions: dict[str, Question]) -> JudgmentResult:
        """Ask the judgment plane a batch of typed questions."""
        self.budget.charge_judgment(questions=len(questions), cost=JUDGMENT_COST)
        return self.judge.ask(state, questions)

    def falsify(
        self, suite: FalsificationSuite, state: State, *, subject: str = ""
    ) -> FalsificationReport:
        """Try to break a claim, and record what survived.

        The whole suite is one judgment call, so adding falsifiers costs
        essentially nothing. Add them.
        """
        self.budget.charge_judgment(questions=len(suite.falsifiers), cost=JUDGMENT_COST)
        report = suite.run(self.judge, state, subject=subject)
        self.ledger.append(
            domain=EventDomain.JUDGMENT,
            event_type=event_types.FALSIFICATION_RUN,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload={
                "suite": report.suite_id,
                "verdict": report.verdict.value,
                "concern": report.concern,
                "confidence": report.confidence,
                "refuted": [finding.falsifier_id for finding in report.refutations],
                "model": report.model,
            },
        )
        return report

    def deliberate(self, case: ReviewCase, supervisors: list[Supervisor]) -> BoardDecision:
        """Convene a supervisor board and record its aggregated decision."""
        board = SupervisorBoard(supervisors)
        self.budget.charge_judgment(questions=len(supervisors), cost=JUDGMENT_COST)
        decision = board.deliberate(case, self.judge)
        self.ledger.append(
            domain=EventDomain.JUDGMENT,
            event_type=event_types.BOARD_DELIBERATED,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload={
                "case_id": decision.case_id,
                "outcome": decision.outcome.value,
                "concern": decision.concern,
                "confidence": decision.confidence,
                "disagreement": decision.disagreement,
                "rationale": decision.rationale,
                "verdicts": [
                    {
                        "supervisor": verdict.supervisor_id,
                        "position": verdict.position.value,
                        "concern": verdict.concern,
                        "confidence": verdict.confidence,
                        "findings": [finding.code for finding in verdict.findings],
                    }
                    for verdict in decision.verdicts
                ],
            },
        )
        return decision

    def guardrails(
        self,
        state: State,
        *,
        destination: str | None = None,
        permitted_class: str = "internal",
        propose_risk_tier: bool = False,
    ) -> GuardrailReading:
        """Run the platform's injection and egress sweep over untrusted input."""
        self.budget.charge_judgment(cost=JUDGMENT_COST)
        return read_guardrails(
            self.judge,
            state,
            egress_destination=destination,
            egress_permitted_class=permitted_class,
            propose_risk_tier=propose_risk_tier,
        )

    # -- side effects ---------------------------------------------------------

    def invoke_tool(
        self,
        capability_id: str,
        arguments: dict[str, Any],
        *,
        purpose: str | None = None,
        resource: str | None = None,
        destination: str | None = None,
        amount: Decimal | None = None,
        currency: str | None = None,
        idempotency_key: str | None = None,
        supervisors: list[Supervisor] | None = None,
        claims: dict[str, str] | None = None,
        sources: tuple[str, ...] = (),
        review: ReviewContext | None = None,
        signals: dict[str, float] | None = None,
        skip_guardrails: bool = False,
    ) -> ExecutionReceipt:
        """Propose, guard and execute one typed capability.

        The guardrail sweep runs automatically for any capability that can
        change something, and a supervisor board runs when one is supplied.
        Both feed the policy engine as *signals*; neither of them decides.
        Policy decides.
        """
        self.budget.charge_tool_call()
        capability = self.platform.registry.get(capability_id)

        # Narrow the run's envelope to exactly this capability before it is
        # presented. The gateway then sees the least authority that can do the
        # job, rather than everything the run happens to hold.
        scoped = self.platform.issuer.attenuate(
            self.delegation,
            capabilities=frozenset({capability_id}),
            purpose=purpose or self.delegation.body.purpose,
            idempotency_key=idempotency_key,
        )

        combined: dict[str, float] = dict(signals or {})
        state: dict[str, Any] = {
            "capability": capability_id,
            "arguments": arguments,
            "resource": resource,
            "destination": destination,
        }

        if capability.risk_tier.has_side_effect and not skip_guardrails:
            reading = self.guardrails(
                state,
                destination=destination,
                permitted_class=(
                    max(capability.data_classes, key=lambda dc: list(DataClass).index(dc)).value
                ),
                propose_risk_tier=True,
            )
            combined |= reading.signals

        if supervisors:
            case = ReviewCase.build(
                subject=state,
                risk_tier=capability.risk_tier,
                claims=claims,
                sources=sources,
                destination=destination,
            )
            board = self.deliberate(case, supervisors)
            combined |= board.signals()
            if review is None and board.all_findings:
                review = ReviewContext(
                    risk_factors=tuple(
                        f"{finding.code}: {finding.detail}" for finding in board.all_findings[:5]
                    ),
                    sources=sources,
                    recommendation=board.rationale,
                )

        return self.platform.guard.invoke(
            capability_id=capability_id,
            arguments=arguments,
            delegation=scoped,
            purpose=purpose,
            resource=resource,
            destination=destination,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key,
            signals=combined,
            review=review,
        )

    def emit_evidence(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        domain: EventDomain = EventDomain.AGENT,
        protected: dict[str, Any] | None = None,
    ) -> None:
        """Record a domain milestone in the run's evidence chain."""
        self.ledger.append(
            domain=domain,
            event_type=event_type,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload=payload,
            protected=protected,
        )

    # -- subagents ------------------------------------------------------------

    def spawn(
        self,
        *,
        capabilities: frozenset[str],
        purpose: str,
        constraints: Constraints | None = None,
        max_risk_tier: RiskTier | None = None,
        lifetime: timedelta | None = None,
    ) -> AgentRun:
        """Spawn a subagent holding strictly narrower authority.

        ``DelegationIssuer.attenuate`` refuses any widening, so a subagent that
        is fully compromised still cannot reach a capability its parent did not
        hold. The child gets its own run id and its own budget, and shares the
        parent's evidence chain so the causal trace stays whole.
        """
        self.budget.charge_subagent()
        narrowed = constraints or self.delegation.body.constraints
        if max_risk_tier is not None:
            narrowed = narrowed.meet(Constraints(max_risk_tier=max_risk_tier))

        child_delegation = self.platform.issuer.attenuate(
            self.delegation,
            capabilities=capabilities,
            constraints=narrowed,
            purpose=purpose,
            lifetime=lifetime,
        )
        child_run_id = new_run_id()
        self.ledger.append(
            domain=EventDomain.IDENTITY,
            event_type=event_types.DELEGATION_ATTENUATED,
            run_id=self.run_id,
            tenant=self.tenant,
            actor=self.workload.service,
            payload={
                "child_run_id": child_run_id,
                "capabilities": sorted(capabilities),
                "purpose": purpose,
                "parent_hash": child_delegation.body.parent_hash,
                "depth": child_delegation.body.depth,
            },
        )
        return AgentRun(
            platform=self.platform,
            run_id=child_run_id,
            principal=self.principal,
            workload=self.workload,
            delegation=child_delegation,
            budget=BudgetLedger(self.budget.budget),
            domain=self.domain,
            parent_run_id=self.run_id,
        )
