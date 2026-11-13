"""The tiered policy decision point.

A single monolithic "sub-100ms policy engine" is the wrong abstraction: it
forces cheap, always-required checks to share a latency budget with expensive,
occasionally-required ones, and the usual resolution is to make the expensive
checks optional. Here the path is tiered instead. Deterministic authorization
(L0/L1) always runs. Transactional risk analysis (L2) runs when the action can
actually cost something. The exceptional path (L3) pauses the workflow for a
human and never times out into an allow.

Evaluation is pure: intent, delegation, capability and judgment signals in, a
``PolicyDecision`` out. No I/O, no model calls, no clock beyond the one it is
handed. That is what lets a recorded decision be replayed exactly, and what
lets the whole rule set be tested without a running platform.

The engine consumes judgment signals but never produces them. A caller that
wants an injection probability asks the judgment plane and passes the number
in. That separation is deliberate: it keeps "what did the model think" and
"what did the platform decide" as two auditable steps rather than one.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..contracts.action import ActionIntent
from ..contracts.delegation import Constraints, SignedDelegation
from ..contracts.policy import DecisionTier, Effect, Obligation, PolicyDecision
from ..contracts.risk import RiskTier
from ..ids import new_id
from ..registry.capability import Capability
from .pack import PolicyPack

ALLOWED_SIGNALS = frozenset(
    {
        "injection_probability",
        "egress_probability",
        "board_concern",
        "board_confidence",
        "groundedness",
        "proposed_risk_tier",
        "anomaly",
    }
)
"""Signal names the engine understands. An unknown signal is ignored rather
than trusted, so a caller cannot invent a signal name that happens to relax a
threshold."""


@dataclass(slots=True)
class _Window:
    events: deque[datetime]


class VelocityCounter:
    """Per-capability, per-principal rate tracking for the L2 path.

    Velocity is a transactional-risk signal, not a quota: the point is to catch
    "this agent has issued nineteen refunds in a minute", which no single
    action looks wrong on its own.
    """

    def __init__(self, window: timedelta = timedelta(minutes=1)) -> None:
        self._window = window
        self._counters: dict[tuple[str, str], _Window] = {}

    def record(self, *, capability_id: str, principal_id: str, now: datetime) -> int:
        """Record an attempt and return how many fall inside the window."""
        key = (capability_id, principal_id)
        window = self._counters.setdefault(key, _Window(deque()))
        cutoff = now - self._window
        while window.events and window.events[0] < cutoff:
            window.events.popleft()
        window.events.append(now)
        return len(window.events)

    def count(self, *, capability_id: str, principal_id: str, now: datetime) -> int:
        """Current count in the window without recording a new attempt."""
        window = self._counters.get((capability_id, principal_id))
        if window is None:
            return 0
        cutoff = now - self._window
        return sum(1 for moment in window.events if moment >= cutoff)


class PolicyEngine:
    """Evaluates one action against one policy pack."""

    def __init__(
        self,
        pack: PolicyPack,
        *,
        velocity: VelocityCounter | None = None,
    ) -> None:
        self._pack = pack
        self._velocity = velocity or VelocityCounter()

    @property
    def pack(self) -> PolicyPack:
        """The pack this engine evaluates against."""
        return self._pack

    @property
    def version(self) -> str:
        """The pack version recorded on every decision."""
        return f"{self._pack.name}@{self._pack.version}"

    def evaluate(
        self,
        *,
        intent: ActionIntent,
        delegation: SignedDelegation,
        capability: Capability,
        signals: dict[str, float] | None = None,
        now: datetime | None = None,
    ) -> PolicyDecision:
        """Decide whether this action may execute, and under what obligations."""
        moment = now or datetime.now(UTC)
        clean_signals = {
            name: value for name, value in (signals or {}).items() if name in ALLOWED_SIGNALS
        }
        body = delegation.body
        effective = body.constraints.meet(capability.registered_constraints)

        decision = self._l0(intent, delegation, capability, moment, clean_signals, effective)
        if decision is not None:
            return decision

        decision = self._l1(intent, delegation, capability, moment, clean_signals, effective)
        if decision is not None:
            return decision

        obligations = set(self._pack.posture_for(capability.risk_tier).obligations)
        obligations |= self._rule_obligations(intent, body.principal.roles, body.domain, capability)

        if capability.risk_tier.has_side_effect:
            decision = self._l2(
                intent, delegation, capability, moment, clean_signals, effective, obligations
            )
            if decision is not None:
                return decision

        return self._l3(
            intent, delegation, capability, moment, clean_signals, effective, obligations
        )

    # -- L0: local invariants -------------------------------------------------

    def _l0(
        self,
        intent: ActionIntent,
        delegation: SignedDelegation,
        capability: Capability,
        now: datetime,
        signals: dict[str, float],
        effective: Constraints,
    ) -> PolicyDecision | None:
        """Checks that need no remote dependency and can only ever deny."""
        body = delegation.body

        if capability.kill_switch:
            return self._deny(
                intent, capability, "capability_kill_switch", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                "the capability is disabled fleet-wide by its kill switch",
            )
        if (
            capability.id in self._pack.hard_deny_capabilities
            or capability.risk_tier is RiskTier.R5_PROHIBITED
        ):
            return self._deny(
                intent, capability, "prohibited_autonomy", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                "this action is on the prohibited-autonomy list and has no agent path",
            )
        if intent.tenant != body.tenant:
            return self._deny(
                intent, capability, "tenant_boundary", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                f"intent tenant {intent.tenant!r} is outside the delegation's tenant",
            )
        if intent.principal_id != body.principal.id:
            return self._deny(
                intent, capability, "principal_mismatch", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                "the intent names a different principal than the delegation",
            )
        if capability.id not in body.capabilities:
            return self._deny(
                intent, capability, "capability_not_delegated", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                f"the delegation does not carry {capability.id!r}",
            )
        if not body.is_live(now=now):
            return self._deny(
                intent, capability, "delegation_expired", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                "the delegation is outside its validity window",
            )
        if effective.max_risk_tier is not None and capability.risk_tier > effective.max_risk_tier:
            return self._deny(
                intent, capability, "risk_tier_above_delegation", DecisionTier.L0_LOCAL_INVARIANT,
                now, signals, effective,
                f"{capability.risk_tier.label} exceeds the delegated ceiling "
                f"{effective.max_risk_tier.label}",
            )
        return None

    # -- L1: cached authorization --------------------------------------------

    def _l1(
        self,
        intent: ActionIntent,
        delegation: SignedDelegation,
        capability: Capability,
        now: datetime,
        signals: dict[str, float],
        effective: Constraints,
    ) -> PolicyDecision | None:
        """Rule evaluation and scope checks. Deny-overrides, default deny."""
        body = delegation.body
        roles = body.principal.roles

        matched_allow = False
        for rule in self._pack.rules:
            if not rule.matches(
                capability_id=capability.id,
                roles=roles,
                purpose=intent.purpose,
                domain=body.domain,
                risk_tier=capability.risk_tier,
            ):
                continue
            if rule.effect is Effect.DENY:
                return self._deny(
                    intent, capability, rule.reason_code or f"denied_by_rule:{rule.id}",
                    DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                    rule.description or f"rule {rule.id} denies this action",
                )
            if rule.min_auth_strength is not None and (
                body.principal.auth_strength < rule.min_auth_strength
            ):
                return self._deny(
                    intent, capability, "insufficient_auth_strength",
                    DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                    f"rule {rule.id} requires stronger authentication than the session has",
                )
            if rule.effect is Effect.ALLOW:
                matched_allow = True

        if not matched_allow:
            # Default deny. An action nobody wrote a rule for is not an action
            # the platform has decided to permit.
            return self._deny(
                intent, capability, "no_matching_rule", DecisionTier.L1_CACHED_AUTHORIZATION,
                now, signals, effective,
                f"no rule in {self.version} permits {capability.id!r} for this caller",
            )

        posture = self._pack.posture_for(capability.risk_tier)
        if body.principal.auth_strength < posture.min_auth_strength:
            return self._deny(
                intent, capability, "insufficient_auth_strength",
                DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                f"{capability.risk_tier.label} requires stronger authentication",
            )
        if body.principal.auth_strength < capability.required_auth_strength:
            return self._deny(
                intent, capability, "insufficient_auth_strength",
                DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                f"{capability.id!r} requires stronger authentication",
            )
        if (posture.requires_attested_workload or capability.requires_attested_workload) and (
            not body.workload.attested
        ):
            return self._deny(
                intent, capability, "workload_not_attested",
                DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                "this capability requires an attested runtime image",
            )
        if not effective.permits("resources", intent.resource) and intent.resource is not None:
            return self._deny(
                intent, capability, "resource_out_of_scope",
                DecisionTier.L1_CACHED_AUTHORIZATION, now, signals, effective,
                f"resource {intent.resource!r} is outside the delegated scope",
            )
        return None

    # -- L2: transactional risk ----------------------------------------------

    def _l2(
        self,
        intent: ActionIntent,
        delegation: SignedDelegation,
        capability: Capability,
        now: datetime,
        signals: dict[str, float],
        effective: Constraints,
        obligations: set[Obligation],
    ) -> PolicyDecision | None:
        """Amount, destination, egress, velocity and judgment-signal checks."""
        body = delegation.body
        thresholds = self._pack.thresholds

        if not effective.permits_amount(intent.amount):
            return self._deny(
                intent, capability, "amount_above_bound", DecisionTier.L2_TRANSACTIONAL_RISK,
                now, signals, effective,
                f"amount {intent.amount} exceeds the effective bound {effective.max_amount}",
            )
        if intent.currency is not None and not effective.permits("currencies", intent.currency):
            return self._deny(
                intent, capability, "currency_not_permitted", DecisionTier.L2_TRANSACTIONAL_RISK,
                now, signals, effective,
                f"currency {intent.currency!r} is not in the permitted set",
            )
        if intent.destination is not None and not effective.permits(
            "destinations", intent.destination
        ):
            return self._deny(
                intent, capability, "destination_not_allowlisted",
                DecisionTier.L2_TRANSACTIONAL_RISK, now, signals, effective,
                f"destination {intent.destination!r} is not an approved egress destination",
            )

        injection = signals.get("injection_probability", 0.0)
        if injection >= thresholds.injection_deny:
            return self._deny(
                intent, capability, "prompt_injection_suspected",
                DecisionTier.L2_TRANSACTIONAL_RISK, now, signals, effective,
                f"injection probability {injection:.2f} is at or above the deny threshold",
            )
        egress = signals.get("egress_probability", 0.0)
        if egress >= thresholds.egress_deny:
            return self._deny(
                intent, capability, "egress_above_classification",
                DecisionTier.L2_TRANSACTIONAL_RISK, now, signals, effective,
                f"egress probability {egress:.2f} is at or above the deny threshold",
            )

        if capability.rate_limit_per_minute is not None:
            seen = self._velocity.record(
                capability_id=capability.id, principal_id=body.principal.id, now=now
            )
            if seen > capability.rate_limit_per_minute:
                return self._deny(
                    intent, capability, "velocity_limit", DecisionTier.L2_TRANSACTIONAL_RISK,
                    now, signals, effective,
                    f"{seen} calls to {capability.id!r} in the last minute exceeds the limit",
                )

        # A judgment plane may propose a *higher* risk tier than the registry
        # declared -- an unusually large or unusual-looking instance of an
        # ordinarily routine action. It may never propose a lower one.
        proposed = signals.get("proposed_risk_tier")
        if proposed is not None:
            # A Score answer is a probability-weighted mean over the rubric, so
            # an unambiguous "level 2" arrives as 2.16, not 2. Comparing the
            # raw mean against an integer tier would escalate almost every
            # action by a rounding artifact, so the discrete level is what the
            # decision uses -- and the raw value stays in the signals for audit.
            proposed_level = round(proposed)
            if proposed_level > capability.risk_tier.value:
                obligations.add(Obligation.REQUIRE_HUMAN_APPROVAL)
                obligations.add(Obligation.EMIT_ENHANCED_EVIDENCE)

        concern = signals.get("board_concern", 0.0)
        confidence = signals.get("board_confidence", 1.0)
        if (
            concern >= thresholds.board_block_review
            or confidence < thresholds.min_decision_confidence
        ):
            obligations.add(Obligation.REQUIRE_HUMAN_APPROVAL)

        if not capability.idempotent:
            obligations.add(Obligation.REQUIRE_IDEMPOTENCY_KEY)
        if capability.compensation_capability:
            obligations.add(Obligation.REQUIRE_COMPENSATION_DECLARED)
        return None

    # -- L3: exceptional ------------------------------------------------------

    def _l3(
        self,
        intent: ActionIntent,
        delegation: SignedDelegation,
        capability: Capability,
        now: datetime,
        signals: dict[str, float],
        effective: Constraints,
        obligations: set[Obligation],
    ) -> PolicyDecision:
        """The final effect, including routing to a human where required."""
        if capability.irreversible and capability.risk_tier >= RiskTier.R3_CUSTOMER_IMPACT:
            obligations.add(Obligation.REQUIRE_HUMAN_APPROVAL)
            obligations.add(Obligation.EMIT_ENHANCED_EVIDENCE)
        if capability.risk_tier >= RiskTier.R4_HIGH_CONSEQUENCE:
            obligations.add(Obligation.REQUIRE_HUMAN_APPROVAL)
            obligations.add(Obligation.REQUIRE_DUAL_CONTROL)
            obligations.add(Obligation.EMIT_ENHANCED_EVIDENCE)

        effect = Effect.REVIEW if Obligation.REQUIRE_HUMAN_APPROVAL in obligations else Effect.ALLOW
        tier_reached = (
            DecisionTier.L3_EXCEPTIONAL
            if effect is Effect.REVIEW
            else (
                DecisionTier.L2_TRANSACTIONAL_RISK
                if capability.risk_tier.has_side_effect
                else DecisionTier.L1_CACHED_AUTHORIZATION
            )
        )
        return PolicyDecision(
            decision_id=new_id("dec"),
            action_digest=intent.digest,
            effect=effect,
            risk_tier=capability.risk_tier,
            reason_code="approval_required" if effect is Effect.REVIEW else "allowed",
            policy_version=self.version,
            tier_reached=tier_reached,
            decided_at=now,
            obligations=frozenset(obligations),
            constraints=effective,
            explanation=(
                f"{capability.id} at {capability.risk_tier.label} "
                f"{'requires a human decision' if effect is Effect.REVIEW else 'is permitted'} "
                f"under {self.version}"
            ),
            signals=dict(signals),
        )

    # -- helpers --------------------------------------------------------------

    def _rule_obligations(
        self,
        intent: ActionIntent,
        roles: frozenset[str],
        domain: str,
        capability: Capability,
    ) -> set[Obligation]:
        """Union the obligations of every matching allow rule."""
        obligations: set[Obligation] = set()
        for rule in self._pack.rules:
            if rule.effect is Effect.ALLOW and rule.matches(
                capability_id=capability.id,
                roles=roles,
                purpose=intent.purpose,
                domain=domain,
                risk_tier=capability.risk_tier,
            ):
                obligations |= rule.obligations
        return obligations

    def _deny(
        self,
        intent: ActionIntent,
        capability: Capability,
        reason_code: str,
        tier: DecisionTier,
        now: datetime,
        signals: dict[str, float],
        effective: Constraints,
        explanation: str,
    ) -> PolicyDecision:
        """Build a denial. Every refusal in this engine goes through here."""
        return PolicyDecision(
            decision_id=new_id("dec"),
            action_digest=intent.digest,
            effect=Effect.DENY,
            risk_tier=capability.risk_tier,
            reason_code=reason_code,
            policy_version=self.version,
            tier_reached=tier,
            decided_at=now,
            obligations=frozenset(),
            constraints=effective,
            explanation=explanation,
            signals=dict(signals),
        )
