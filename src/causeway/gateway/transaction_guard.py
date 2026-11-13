"""The transaction guard: the platform's policy enforcement point.

Every side effect crosses here. There is no other way for an agent to change
the world: no direct SDK, no credential in an environment variable, no network
route around this module. That is the whole point of the design -- a single
place where authority, policy, approval, idempotency, evidence and verification
are enforced together, so that they cannot drift apart or be forgotten
individually.

The flow, and why it is in this order:

1. **Normalise, then digest.** Arguments are validated against the capability's
   schema and canonicalised *before* the action has an identity. A digest taken
   over un-normalised input would differ between two spellings of the same
   action, which would break approval binding and idempotency at once.
2. **Verify the envelope.** Signature, window, audience, capability presence.
   Nothing below this line looks at an unauthenticated field.
3. **Decide.** The tiered policy engine returns an effect, obligations and a
   reason code, bound to the digest.
4. **Discharge obligations.** Approval, dual control, step-up, attestation,
   idempotency key, declared compensation. An obligation the guard does not
   recognise is a denial -- a new control must never be a silent no-op.
5. **Mint credentials.** Only now, scoped and short-lived.
6. **Execute, verify, record.** The receipt and its evidence are written as
   part of the transaction, not after it.

Every exit from this module is either an ``ExecutionReceipt`` or an exception.
There is no third outcome, and no way to obtain a receipt without passing every
step above.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final

from ..authority.verifier import DelegationVerifier
from ..contracts.action import ActionIntent, ExecutionReceipt, ResultClass
from ..contracts.delegation import SignedDelegation
from ..contracts.evidence import EventDomain
from ..contracts.human import DecisionOutcome
from ..contracts.identity import AuthStrength
from ..contracts.policy import Effect, Obligation, PolicyDecision
from ..errors import (
    ApprovalRequired,
    ContractViolation,
    DigestMismatch,
    PolicyDenied,
    VerificationFailed,
)
from ..evidence import events as event_types
from ..evidence.ledger import EvidenceLedger
from ..human.decisions import HumanDecisionService, build_packet
from ..ids import new_action_id
from ..policy.engine import PolicyEngine
from ..registry.capability import Capability, CapabilityRegistry, CapabilityResult
from .credentials import CredentialBroker, LocalCredentialBroker
from .idempotency import IdempotencyStore

HANDLED_OBLIGATIONS: Final[frozenset[Obligation]] = frozenset(Obligation)
"""Obligations this guard knows how to discharge.

Compared against the full ``Obligation`` enum by the test suite: adding an
obligation to the contract without teaching the guard to enforce it must break
the build, not quietly weaken a control.
"""


@dataclass(frozen=True, slots=True)
class Proposal:
    """A normalised, typed action awaiting a decision.

    Holding the capability and the delegation alongside the intent means the
    decision and execution steps never have to look anything up again, and so
    cannot look up something different from what was normalised.
    """

    intent: ActionIntent
    capability: Capability
    delegation: SignedDelegation

    @property
    def digest(self) -> str:
        """The action digest everything downstream binds to."""
        return self.intent.digest


@dataclass(frozen=True, slots=True)
class ReviewContext:
    """Extra material for the human review packet.

    Supplied by the workflow, because only the workflow knows what it looked at
    and what it would do instead. A reviewer handed an action with no
    alternatives and no rollback is being asked to rubber-stamp.
    """

    risk_factors: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    recommendation: str | None = None
    summary: str | None = None


class TransactionGuard:
    """The single enforcement point for every consequential action."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        policy: PolicyEngine,
        delegation_verifier: DelegationVerifier,
        ledger: EvidenceLedger,
        approvals: HumanDecisionService,
        credentials: CredentialBroker | None = None,
        idempotency: IdempotencyStore | None = None,
        audience: str = "tool-gateway",
    ) -> None:
        self._registry = registry
        self._policy = policy
        self._delegation_verifier = delegation_verifier
        self._ledger = ledger
        self._approvals = approvals
        self._credentials = credentials or LocalCredentialBroker()
        self._idempotency = idempotency or IdempotencyStore()
        self._audience = audience

    @property
    def audience(self) -> str:
        """The audience delegations must be minted for to be spent here."""
        return self._audience

    # -- step 1: propose ------------------------------------------------------

    def propose(
        self,
        *,
        capability_id: str,
        arguments: dict[str, Any],
        delegation: SignedDelegation,
        purpose: str | None = None,
        resource: str | None = None,
        destination: str | None = None,
        amount: Decimal | None = None,
        currency: str | None = None,
        idempotency_key: str | None = None,
    ) -> Proposal:
        """Validate and normalise a proposed action, giving it an identity.

        An unregistered capability fails here, before policy is consulted: the
        platform does not evaluate rules about actions it has no implementation
        for, because a rule that appeared to permit one would be misleading.
        """
        capability = self._registry.get(capability_id)
        normalised = capability.normalise(arguments)
        body = delegation.body

        intent = ActionIntent(
            action_id=new_action_id(),
            run_id=body.run_id,
            tenant=body.tenant,
            capability=capability.id,
            arguments=normalised,
            purpose=purpose or body.purpose,
            principal_id=body.principal.id,
            resource=resource,
            destination=destination,
            amount=amount,
            currency=currency,
            idempotency_key=idempotency_key or body.idempotency_key,
        )
        self._ledger.append(
            domain=EventDomain.TOOL,
            event_type=event_types.ACTION_PROPOSED,
            run_id=intent.run_id,
            tenant=intent.tenant,
            actor=body.workload.service,
            action_id=intent.action_id,
            action_digest=intent.digest,
            payload={
                "capability": capability.id,
                "capability_version": capability.version,
                "risk_tier": capability.risk_tier.value,
                "purpose": intent.purpose,
                "resource": intent.resource,
                "destination": intent.destination,
                "argument_keys": sorted(normalised),
            },
            # The arguments themselves may be sensitive, so they are held
            # behind a protected reference rather than inlined in the ledger.
            protected={"arguments": normalised},
        )
        return Proposal(intent=intent, capability=capability, delegation=delegation)

    # -- step 2: decide -------------------------------------------------------

    def decide(
        self,
        proposal: Proposal,
        *,
        signals: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        """Verify the envelope and evaluate policy for this exact action."""
        moment = now or datetime.now(UTC)
        try:
            self._delegation_verifier.verify(
                proposal.delegation,
                audience=self._audience,
                required_capability=proposal.capability.id,
                now=moment,
            )
        except Exception as exc:
            self._ledger.append(
                domain=EventDomain.IDENTITY,
                event_type=event_types.DELEGATION_REJECTED,
                run_id=proposal.intent.run_id,
                tenant=proposal.intent.tenant,
                actor=proposal.delegation.body.workload.service,
                action_id=proposal.intent.action_id,
                action_digest=proposal.digest,
                payload={"reason": type(exc).__name__, "detail": str(exc)},
            )
            raise

        decision = self._policy.evaluate(
            intent=proposal.intent,
            delegation=proposal.delegation,
            capability=proposal.capability,
            signals=signals,
            now=moment,
        )
        self._ledger.append(
            domain=EventDomain.POLICY,
            event_type=(
                event_types.POLICY_DENIED
                if decision.effect is Effect.DENY
                else event_types.POLICY_EVALUATED
            ),
            run_id=proposal.intent.run_id,
            tenant=proposal.intent.tenant,
            actor="policy-engine",
            action_id=proposal.intent.action_id,
            action_digest=proposal.digest,
            payload={
                "decision_id": decision.decision_id,
                "effect": decision.effect.value,
                "reason_code": decision.reason_code,
                "risk_tier": decision.risk_tier.value,
                "policy_version": decision.policy_version,
                "tier_reached": decision.tier_reached.value,
                "obligations": sorted(o.value for o in decision.obligations),
                "signals": decision.signals,
                "explanation": decision.explanation,
            },
        )
        return decision

    def simulate(
        self,
        *,
        capability_id: str,
        arguments: dict[str, Any],
        delegation: SignedDelegation,
        signals: dict[str, float] | None = None,
        **proposal_kwargs: Any,
    ) -> PolicyDecision:
        """Evaluate an action without any possibility of executing it.

        This is what the policy simulator and CI policy tests call. It shares
        every code path with the real decision, which is the only way a
        simulation is worth anything.
        """
        proposal = self.propose(
            capability_id=capability_id,
            arguments=arguments,
            delegation=delegation,
            **proposal_kwargs,
        )
        return self.decide(proposal, signals=signals)

    # -- step 3: execute ------------------------------------------------------

    def execute(
        self,
        proposal: Proposal,
        decision: PolicyDecision,
        *,
        review: ReviewContext | None = None,
        now: datetime | None = None,
    ) -> ExecutionReceipt:
        """Discharge obligations, execute, verify and record."""
        moment = now or datetime.now(UTC)
        intent, capability = proposal.intent, proposal.capability

        # Re-derive rather than trust: the decision must bind to the action we
        # are actually about to run, not to one that was proposed earlier.
        if decision.action_digest != intent.digest:
            raise DigestMismatch(
                "the policy decision does not bind to this action; "
                "an approval or decision for one payload cannot be spent on another"
            )
        if decision.effect is Effect.DENY:
            raise PolicyDenied(
                f"{capability.id} denied: {decision.explanation}",
                reason_code=decision.reason_code,
            )

        unknown = decision.obligations - HANDLED_OBLIGATIONS
        if unknown:
            # str() rather than .value: an unrecognised obligation may not be
            # an enum member at all, and the refusal must not itself crash.
            raise PolicyDenied(
                f"policy returned obligations this gateway cannot enforce: "
                f"{sorted(str(getattr(o, 'value', o)) for o in unknown)}",
                reason_code="unenforceable_obligation",
            )

        self._discharge_static_obligations(proposal, decision)

        if (
            decision.effect is Effect.REVIEW
            or Obligation.REQUIRE_HUMAN_APPROVAL in decision.obligations
        ):
            approval_id = self._require_approval(proposal, decision, review, moment)
            # The approval is what turns REVIEW into ALLOW, and it does so
            # here, once, in the open. Nothing downstream re-derives it, and
            # the upgraded decision keeps the same id and digest so evidence
            # still ties the execution to the decision that was reviewed.
            decision = replace(
                decision,
                effect=Effect.ALLOW,
                reason_code="allowed_with_approval",
                explanation=f"{decision.explanation}; approved as {approval_id}",
            )
        else:
            approval_id = None

        key = intent.idempotency_key
        if key is not None:
            prior = self._idempotency.lookup(key, action_digest=intent.digest)
            if prior is not None:
                return self._duplicate_receipt(proposal, prior, moment)
            self._idempotency.reserve(key, action_digest=intent.digest, now=moment)

        grant = self._credentials.mint(
            intent=intent,
            capability=capability,
            decision=decision,
            audience=self._audience,
        )

        try:
            result = capability.handler(intent, grant)
        except Exception as exc:
            self._ledger.append(
                domain=EventDomain.TOOL,
                event_type=event_types.ACTION_FAILED,
                run_id=intent.run_id,
                tenant=intent.tenant,
                actor=proposal.delegation.body.workload.service,
                action_id=intent.action_id,
                action_digest=intent.digest,
                payload={
                    "capability": capability.id,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                    "result_class": ResultClass.FAILED.value,
                },
            )
            if key is not None:
                # The handler raised before producing a result, so nothing
                # landed and the caller may retry with the same key.
                self._idempotency.release(key)
            raise

        verified = self._verify(capability, decision, intent, result)
        receipt = ExecutionReceipt(
            action_id=intent.action_id,
            action_digest=intent.digest,
            run_id=intent.run_id,
            capability=capability.id,
            risk_tier=capability.risk_tier,
            result_class=ResultClass.SUCCESS if verified else ResultClass.UNVERIFIED,
            executed_at=moment,
            policy_version=decision.policy_version,
            decision_id=decision.decision_id,
            evidence_ref="",
            idempotency_key=key,
            approval_id=approval_id,
            output_digest=result.output_digest,
            verified=verified,
            compensation_capability=capability.compensation_capability,
            notes=(f"external_ref={result.external_ref}",) if result.external_ref else (),
        )

        event = self._ledger.append(
            domain=EventDomain.TOOL,
            event_type=(
                event_types.ACTION_EXECUTED if verified else event_types.ACTION_UNVERIFIED
            ),
            run_id=intent.run_id,
            tenant=intent.tenant,
            actor=proposal.delegation.body.workload.service,
            action_id=intent.action_id,
            action_digest=intent.digest,
            payload={
                "capability": capability.id,
                "capability_version": capability.version,
                "risk_tier": capability.risk_tier.value,
                "result_class": receipt.result_class.value,
                "verified": verified,
                "output_digest": result.output_digest,
                "external_ref": result.external_ref,
                "approval_id": approval_id,
                "decision_id": decision.decision_id,
                "policy_version": decision.policy_version,
                "idempotency_key": key,
            },
            protected=(
                {"output": result.output}
                if Obligation.EMIT_ENHANCED_EVIDENCE in decision.obligations
                else None
            ),
        )
        receipt = _with_evidence_ref(receipt, f"evidence:{event.sequence}:{event.event_hash}")

        if key is not None:
            self._idempotency.complete(key, receipt)
        if approval_id is not None:
            # Spend the approval so it cannot be replayed onto a later attempt.
            self._approvals.spend(intent.digest)

        if not verified:
            raise VerificationFailed(
                f"{capability.id} executed but its post-condition did not confirm; "
                f"action {intent.action_id} is queued for reconciliation, not retry"
            )
        return receipt

    def invoke(
        self,
        *,
        capability_id: str,
        arguments: dict[str, Any],
        delegation: SignedDelegation,
        signals: dict[str, float] | None = None,
        review: ReviewContext | None = None,
        **proposal_kwargs: Any,
    ) -> ExecutionReceipt:
        """Propose, decide and execute in one call. The common path."""
        proposal = self.propose(
            capability_id=capability_id,
            arguments=arguments,
            delegation=delegation,
            **proposal_kwargs,
        )
        decision = self.decide(proposal, signals=signals)
        return self.execute(proposal, decision, review=review)

    # -- obligations ----------------------------------------------------------

    def _discharge_static_obligations(
        self, proposal: Proposal, decision: PolicyDecision
    ) -> None:
        """Enforce the obligations that need no external party."""
        intent, capability = proposal.intent, proposal.capability
        principal = proposal.delegation.body.principal
        obligations = decision.obligations

        if Obligation.REQUIRE_IDEMPOTENCY_KEY in obligations and not intent.idempotency_key:
            raise PolicyDenied(
                f"{capability.id} requires an idempotency key and none was supplied",
                reason_code="missing_idempotency_key",
            )
        if (
            Obligation.REQUIRE_COMPENSATION_DECLARED in obligations
            and not capability.compensation_capability
        ):
            raise PolicyDenied(
                f"{capability.id} must declare a compensating action",
                reason_code="missing_compensation",
            )
        if (
            Obligation.REQUIRE_STEP_UP_AUTH in obligations
            and principal.auth_strength < AuthStrength.STEP_UP
        ):
            raise PolicyDenied(
                f"{capability.id} requires step-up authentication for this action",
                reason_code="step_up_required",
            )
        if (
            Obligation.REQUIRE_ATTESTED_WORKLOAD in obligations
            and not proposal.delegation.body.workload.attested
        ):
            raise PolicyDenied(
                f"{capability.id} requires an attested runtime image",
                reason_code="workload_not_attested",
            )
        # REDACT_PROTECTED_FIELDS, VERIFY_AFTER_EXECUTE and
        # EMIT_ENHANCED_EVIDENCE are discharged during execution, below.

    def _require_approval(
        self,
        proposal: Proposal,
        decision: PolicyDecision,
        review: ReviewContext | None,
        now: datetime,
    ) -> str:
        """Open or consume the human decision bound to this action digest."""
        intent, capability = proposal.intent, proposal.capability
        context = review or ReviewContext()
        dual = Obligation.REQUIRE_DUAL_CONTROL in decision.obligations

        item = self._approvals.request(
            action_digest=intent.digest,
            run_id=intent.run_id,
            tenant=intent.tenant,
            queue=capability.approval_queue,
            required_approvals=2 if dual else 1,
            packet=build_packet(
                intent_summary=context.summary
                or f"{capability.summary} ({capability.id})",
                capability=capability.id,
                risk_tier=capability.risk_tier,
                resource=intent.resource,
                destination=intent.destination,
                amount_display=(
                    f"{intent.amount} {intent.currency}" if intent.amount is not None else None
                ),
                delegation_scope={
                    "capabilities": sorted(proposal.delegation.body.capabilities),
                    "purpose": proposal.delegation.body.purpose,
                    "expires_at": proposal.delegation.body.expires_at.isoformat(),
                    "max_amount": str(decision.constraints.max_amount)
                    if decision.constraints.max_amount is not None
                    else None,
                },
                risk_factors=context.risk_factors
                or tuple(f"{name}={value:.2f}" for name, value in sorted(decision.signals.items())),
                policy_rationale=decision.explanation,
                sources=context.sources,
                alternatives=context.alternatives,
                rollback=capability.compensation_capability,
                recommendation=context.recommendation,
            ),
            now=now,
        )

        resolved = self._approvals.resolve(intent.digest, now=now)
        if resolved is None:
            self._ledger.append(
                domain=EventDomain.HUMAN,
                event_type=event_types.APPROVAL_REQUESTED,
                run_id=intent.run_id,
                tenant=intent.tenant,
                actor="human-decision-service",
                action_id=intent.action_id,
                action_digest=intent.digest,
                payload={
                    "approval_id": item.approval_id,
                    "queue": item.queue,
                    "required_approvals": item.required_approvals,
                    "expires_at": item.expires_at.isoformat(),
                },
            )
            raise ApprovalRequired(
                f"{capability.id} is waiting on {item.queue}",
                approval_id=item.approval_id,
                action_digest=intent.digest,
            )

        if resolved.action_digest != intent.digest:
            raise DigestMismatch("the approval on file is for a different action")
        if resolved.outcome is not DecisionOutcome.APPROVED:
            self._ledger.append(
                domain=EventDomain.HUMAN,
                event_type=(
                    event_types.APPROVAL_EXPIRED
                    if resolved.outcome is DecisionOutcome.EXPIRED
                    else event_types.APPROVAL_DENIED
                ),
                run_id=intent.run_id,
                tenant=intent.tenant,
                actor=resolved.approver_id,
                action_id=intent.action_id,
                action_digest=intent.digest,
                payload={
                    "approval_id": resolved.approval_id,
                    "outcome": resolved.outcome.value,
                    "rationale": resolved.rationale,
                },
            )
            raise PolicyDenied(
                f"{capability.id} was not approved: {resolved.outcome.value}",
                reason_code=f"approval_{resolved.outcome.value}",
            )
        if not resolved.is_live(now):
            raise PolicyDenied(
                f"the approval for {capability.id} has expired and must be re-requested",
                reason_code="approval_stale",
            )

        self._ledger.append(
            domain=EventDomain.HUMAN,
            event_type=event_types.APPROVAL_GRANTED,
            run_id=intent.run_id,
            tenant=intent.tenant,
            actor=resolved.approver_id,
            action_id=intent.action_id,
            action_digest=intent.digest,
            payload={
                "approval_id": resolved.approval_id,
                "approver_id": resolved.approver_id,
                "authority": sorted(resolved.approver_authority),
                "rationale": resolved.rationale,
                "signature": resolved.signature,
            },
        )
        return resolved.approval_id

    # -- verification ---------------------------------------------------------

    def _verify(
        self,
        capability: Capability,
        decision: PolicyDecision,
        intent: ActionIntent,
        result: CapabilityResult,
    ) -> bool:
        """Run the capability's post-condition when policy asks for one."""
        if Obligation.VERIFY_AFTER_EXECUTE not in decision.obligations:
            return True
        if capability.verifier is None:
            raise ContractViolation(
                f"{capability.id} is required to verify after execution but declares no verifier"
            )
        return bool(capability.verifier(intent, result))

    def _duplicate_receipt(
        self, proposal: Proposal, prior: ExecutionReceipt, now: datetime
    ) -> ExecutionReceipt:
        """Return the original receipt for a repeated idempotency key."""
        self._ledger.append(
            domain=EventDomain.TOOL,
            event_type=event_types.ACTION_DUPLICATE,
            run_id=proposal.intent.run_id,
            tenant=proposal.intent.tenant,
            actor=proposal.delegation.body.workload.service,
            action_id=proposal.intent.action_id,
            action_digest=proposal.digest,
            payload={
                "capability": proposal.capability.id,
                "original_action_id": prior.action_id,
                "idempotency_key": prior.idempotency_key,
                "result_class": ResultClass.DUPLICATE.value,
            },
        )
        return _as_duplicate(prior, now)


def _with_evidence_ref(receipt: ExecutionReceipt, evidence_ref: str) -> ExecutionReceipt:
    """Attach the evidence reference to a receipt."""
    return replace(receipt, evidence_ref=evidence_ref)


def _as_duplicate(receipt: ExecutionReceipt, now: datetime) -> ExecutionReceipt:
    """Render a prior receipt as the answer to a duplicate attempt.

    The original ``action_id`` and evidence reference are preserved, because
    the caller needs to be able to find the one execution that actually
    happened, not the attempt that was deduplicated.
    """
    from dataclasses import replace

    return replace(
        receipt,
        result_class=ResultClass.DUPLICATE,
        notes=(*receipt.notes, f"duplicate_at={now.isoformat()}"),
    )
