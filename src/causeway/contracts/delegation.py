"""Signed, attenuating delegation.

This module holds the single most important invariant in the platform:

    Delegation can only attenuate. A child may receive equal or narrower
    rights than its parent, never broader.

It is enforced structurally rather than by review. ``Delegation.attenuate()``
is the only way to derive a child, and it raises ``AttenuationViolation``
before a widened envelope exists to be signed. There is no code path that
produces a signed envelope holding rights its parent did not hold, so an agent
that is fully compromised still cannot mint itself authority: the worst it can
do is spend the authority it was already given.

The envelope body is what gets signed and hashed. Signatures live outside it in
``SignedDelegation`` so that the body digest is stable and can serve as the
``parent_hash`` of the next link in the chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..canonical import digest
from ..errors import AttenuationViolation, ContractViolation
from .identity import Principal, WorkloadIdentity
from .risk import RiskTier

# A constraint dimension set to None means "this envelope places no bound on
# that dimension". It does NOT mean "unbounded in the world": the capability
# registry and the policy pack place their own bounds, and the effective bound
# is the tightest of all of them. None here only means this link in the
# delegation chain did not narrow further.
Unbounded = None


@dataclass(frozen=True, slots=True)
class Constraints:
    """Resource, amount and destination bounds carried by a delegation.

    Each dimension is either ``None`` (this envelope does not narrow it) or a
    bound. Attenuation compares dimension by dimension; see :meth:`narrows`.
    """

    max_amount: Decimal | None = None
    currencies: frozenset[str] | None = None
    destinations: frozenset[str] | None = None
    resources: frozenset[str] | None = None
    data_classes: frozenset[str] | None = None
    max_risk_tier: RiskTier | None = None

    def narrows(self, parent: Constraints) -> bool:
        """Whether ``self`` is at least as restrictive as ``parent``."""
        if parent.max_amount is not None and (
            self.max_amount is None or self.max_amount > parent.max_amount
        ):
            return False
        if parent.max_risk_tier is not None and (
            self.max_risk_tier is None or self.max_risk_tier > parent.max_risk_tier
        ):
            return False
        for name in ("currencies", "destinations", "resources", "data_classes"):
            parent_set: frozenset[str] | None = getattr(parent, name)
            if parent_set is None:
                continue
            child_set: frozenset[str] | None = getattr(self, name)
            if child_set is None or not child_set <= parent_set:
                return False
        return True

    def meet(self, other: Constraints) -> Constraints:
        """Return the tightest constraint set implied by both.

        Used to fold a capability's registered bounds into a caller's request
        without ever loosening either.
        """
        def tighter_set(
            a: frozenset[str] | None, b: frozenset[str] | None
        ) -> frozenset[str] | None:
            if a is None:
                return b
            if b is None:
                return a
            return a & b

        amounts = [v for v in (self.max_amount, other.max_amount) if v is not None]
        tiers = [v for v in (self.max_risk_tier, other.max_risk_tier) if v is not None]
        return Constraints(
            max_amount=min(amounts) if amounts else None,
            currencies=tighter_set(self.currencies, other.currencies),
            destinations=tighter_set(self.destinations, other.destinations),
            resources=tighter_set(self.resources, other.resources),
            data_classes=tighter_set(self.data_classes, other.data_classes),
            max_risk_tier=min(tiers) if tiers else None,
        )

    def permits_amount(self, amount: Decimal | None) -> bool:
        """Whether ``amount`` is within the declared bound."""
        if amount is None:
            return True
        if self.max_amount is None:
            return True
        return amount <= self.max_amount

    def permits(self, dimension: str, value: str | None) -> bool:
        """Whether ``value`` is inside the allowlist for ``dimension``."""
        allowed: frozenset[str] | None = getattr(self, dimension)
        if allowed is None:
            return True
        if value is None:
            # A constrained dimension with no value supplied is ambiguous, and
            # authorization uncertainty fails closed.
            return False
        return value in allowed


@dataclass(frozen=True, slots=True)
class Delegation:
    """The signed body of a delegation envelope.

    Every field here is set by the platform from trusted inputs. None of them
    are ever populated from model output, retrieved documents or tool results.
    """

    delegation_id: str
    principal: Principal
    workload: WorkloadIdentity
    tenant: str
    domain: str
    run_id: str
    capabilities: frozenset[str]
    purpose: str
    constraints: Constraints
    audience: str
    nonce: str
    issued_at: datetime
    expires_at: datetime
    policy_context_hash: str
    agent_version: str = "unknown"
    workflow_id: str | None = None
    workflow_version: str | None = None
    parent_hash: str | None = None
    depth: int = 0
    idempotency_key: str | None = None
    labels: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.expires_at <= self.issued_at:
            raise ContractViolation("delegation expires before it is issued")
        if self.principal.tenant != self.tenant:
            raise ContractViolation("delegation tenant does not match the principal's tenant")
        if not self.capabilities:
            raise ContractViolation("a delegation with no capabilities grants nothing; refuse it")
        if not self.purpose:
            raise ContractViolation("purpose-of-use is mandatory")

    @property
    def body_digest(self) -> str:
        """The digest that is signed and that the next link chains onto."""
        return digest(self)

    def is_live(self, *, now: datetime | None = None, leeway: timedelta = timedelta(0)) -> bool:
        """Whether the envelope is inside its validity window."""
        moment = now or datetime.now(UTC)
        return self.issued_at - leeway <= moment < self.expires_at + leeway

    def attenuate(
        self,
        *,
        capabilities: frozenset[str] | None = None,
        constraints: Constraints | None = None,
        purpose: str | None = None,
        audience: str | None = None,
        expires_at: datetime | None = None,
        delegation_id: str,
        nonce: str,
        now: datetime | None = None,
        workload: WorkloadIdentity | None = None,
        idempotency_key: str | None = None,
    ) -> Delegation:
        """Derive a narrower child delegation.

        This is the only supported way to produce a child envelope, and it
        refuses every widening: extra capabilities, looser constraints, a later
        expiry, or a different principal. A subagent therefore inherits a
        strict subset of its parent's authority by construction.
        """
        moment = now or datetime.now(UTC)

        child_capabilities = self.capabilities if capabilities is None else capabilities
        if not child_capabilities <= self.capabilities:
            raise AttenuationViolation(
                "child delegation requests capabilities the parent does not hold: "
                f"{sorted(child_capabilities - self.capabilities)}"
            )

        child_constraints = self.constraints if constraints is None else constraints
        if not child_constraints.narrows(self.constraints):
            raise AttenuationViolation(
                "child delegation constraints are broader than the parent's"
            )

        child_expiry = self.expires_at if expires_at is None else expires_at
        if child_expiry > self.expires_at:
            raise AttenuationViolation("child delegation outlives its parent")
        if child_expiry <= moment:
            raise AttenuationViolation("child delegation would already be expired")

        return replace(
            self,
            delegation_id=delegation_id,
            capabilities=child_capabilities,
            constraints=child_constraints,
            purpose=purpose or self.purpose,
            audience=audience or self.audience,
            nonce=nonce,
            issued_at=moment,
            expires_at=child_expiry,
            parent_hash=self.body_digest,
            depth=self.depth + 1,
            workload=workload or self.workload,
            idempotency_key=idempotency_key,
        )


@dataclass(frozen=True, slots=True)
class SignedDelegation:
    """A delegation body plus the signature that makes it presentable.

    The signature is deliberately not part of the body: the body digest must
    stay stable so it can be the ``parent_hash`` of the next link and so the
    same body signed by a rotated key still chains identically.
    """

    body: Delegation
    signature: str
    key_id: str
    algorithm: str

    @property
    def body_digest(self) -> str:
        """Convenience accessor for the signed body's digest."""
        return self.body.body_digest
