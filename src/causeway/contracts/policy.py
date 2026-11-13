"""Policy decisions: the platform's answer to "may this happen?".

A policy decision is produced by deterministic code from trusted state. It is
never produced by a language model, and no model output can create, widen or
substitute one. The judgment plane may *inform* a decision by supplying typed
signals (a proposed risk tier, an injection probability), but the decision
itself -- the effect, the obligations and the reason code -- comes from the
policy engine.

Decisions are tiered, because a single monolithic "sub-100ms policy engine" is
the wrong abstraction: cheap deterministic authorization is always enforced,
and expensive contextual analysis runs only where risk requires it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum, StrEnum

from .delegation import Constraints
from .risk import RiskTier


class Effect(StrEnum):
    """What the policy engine decided.

    ``REVIEW`` is a first-class outcome, not a soft deny: it routes to the
    human decision service with an evidence packet. Nothing here can express
    "probably fine" -- an unresolved decision is ``DENY``.
    """

    ALLOW = "allow"
    DENY = "deny"
    REVIEW = "review"


class DecisionTier(IntEnum):
    """How deep the evaluation had to go before it could answer.

    Recorded on every decision so that the platform can measure how often the
    expensive paths run, and so an auditor can see which checks actually ran
    for a given action rather than which checks exist.
    """

    L0_LOCAL_INVARIANT = 0
    """Token validity, capability presence, hard deny, action shape, tenant."""

    L1_CACHED_AUTHORIZATION = 1
    """ABAC/RBAC, purpose, data class, resource scope, obligations."""

    L2_TRANSACTIONAL_RISK = 2
    """Amount, destination, egress, anomaly, velocity, privilege expansion."""

    L3_EXCEPTIONAL = 3
    """Irreversible, high-loss, regulatory or novel. Never times out into allow."""


class Obligation(StrEnum):
    """A condition the caller must satisfy for an ALLOW to remain valid.

    Obligations are how policy says "yes, but". The transaction guard enforces
    every obligation it receives and refuses any it does not recognise, so
    adding an obligation to a policy pack can never silently become a no-op.
    """

    REQUIRE_HUMAN_APPROVAL = "require_human_approval"
    REQUIRE_DUAL_CONTROL = "require_dual_control"
    REQUIRE_STEP_UP_AUTH = "require_step_up_auth"
    REQUIRE_ATTESTED_WORKLOAD = "require_attested_workload"
    REQUIRE_IDEMPOTENCY_KEY = "require_idempotency_key"
    REQUIRE_COMPENSATION_DECLARED = "require_compensation_declared"
    VERIFY_AFTER_EXECUTE = "verify_after_execute"
    EMIT_ENHANCED_EVIDENCE = "emit_enhanced_evidence"
    REDACT_PROTECTED_FIELDS = "redact_protected_fields"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """The signed-off answer for one action digest.

    Binding to ``action_digest`` is what makes a decision unspendable on any
    other payload: the transaction guard re-derives the digest from the intent
    it is about to execute and refuses if it differs.
    """

    decision_id: str
    action_digest: str
    effect: Effect
    risk_tier: RiskTier
    reason_code: str
    policy_version: str
    tier_reached: DecisionTier
    decided_at: datetime
    obligations: frozenset[Obligation] = field(default_factory=frozenset)
    constraints: Constraints = field(default_factory=Constraints)
    explanation: str = ""
    signals: dict[str, float] = field(default_factory=dict)
    """Typed judgment signals that informed the decision, for replay and audit."""

    @property
    def allows_execution(self) -> bool:
        """Whether execution may proceed once obligations are discharged."""
        return self.effect is Effect.ALLOW
