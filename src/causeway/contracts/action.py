"""Typed intent, action digests and execution receipts.

The most important boundary in the platform is between *proposal* and
*execution*. A model proposes a typed intent; the platform decides whether the
caller is authorized, what obligations apply, and whether the side effect may
execute. Nothing in this module can execute anything -- these are the data
types that cross that boundary.

``ActionIntent.digest`` is the identity of a proposed side effect. A policy
decision, a human approval and an execution receipt all bind to it, which is
what stops an approval for one payload being spent on another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..canonical import digest
from ..errors import ContractViolation
from .risk import RiskTier


@dataclass(frozen=True, slots=True)
class ActionIntent:
    """A typed request to change something in the world.

    ``arguments`` must already be normalised by the capability's schema before
    the digest is taken -- see ``causeway.registry.capability.Capability.normalise``.
    Normalising first is what makes the digest stable across clients that order
    keys differently or spell an amount as ``"10.00"`` rather than ``10``.

    The structured fields (``resource``, ``destination``, ``amount``) are
    promoted out of ``arguments`` because policy, delegation constraints and
    the human review packet all need them without understanding any particular
    capability's schema.
    """

    action_id: str
    run_id: str
    tenant: str
    capability: str
    arguments: dict[str, Any]
    purpose: str
    principal_id: str
    resource: str | None = None
    destination: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    idempotency_key: str | None = None
    parent_action_id: str | None = None

    def __post_init__(self) -> None:
        if not self.capability:
            raise ContractViolation("an intent must name a capability")
        if not self.purpose:
            raise ContractViolation("purpose-of-use is mandatory on every intent")
        if self.amount is not None and self.currency is None:
            raise ContractViolation("an amount without a currency cannot be bounded by policy")

    @property
    def digest(self) -> str:
        """The stable identity of this payload, within this run.

        Deliberately excludes ``action_id`` so that a retry of the same
        proposal reuses an existing approval, and deliberately includes
        ``run_id`` so that an approval granted in one run can never be spent in
        another. Also excludes ``idempotency_key``: two attempts at the same
        payload are the same action to an approver even when the caller keyed
        them differently.
        """
        return digest(
            {
                "capability": self.capability,
                "arguments": self.arguments,
                "purpose": self.purpose,
                "tenant": self.tenant,
                "run_id": self.run_id,
                "principal_id": self.principal_id,
                "resource": self.resource,
                "destination": self.destination,
                "amount": self.amount,
                "currency": self.currency,
            }
        )


class ResultClass(StrEnum):
    """The shape of an execution outcome, without its payload.

    Evidence records the class; the payload itself lives behind a protected
    reference, because receipts are widely readable and payloads are not.
    """

    SUCCESS = "success"
    DUPLICATE = "duplicate"  # idempotent replay; the prior receipt was returned
    FAILED = "failed"
    COMPENSATED = "compensated"
    UNVERIFIED = "unverified"  # executed, but the post-condition did not confirm


@dataclass(frozen=True, slots=True)
class CredentialGrant:
    """A just-in-time, audience-bound credential minted after a policy allow.

    Agents never hold long-lived secrets. The transaction guard requests a
    grant only once the decision is ``ALLOW``, scoped to exactly the resource
    the action named and expiring in the order of the action, not the session.
    """

    grant_id: str
    audience: str
    scope: frozenset[str]
    resource: str | None
    expires_at: datetime
    # The secret material itself is never stored on the grant. Reference
    # indirection keeps credentials out of evidence, logs and checkpoints.
    secret_ref: str


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """Proof of what actually happened, emitted as part of the transaction.

    A receipt is the *only* success value the transaction guard returns. If a
    caller does not hold a receipt, no side effect was authorised to occur.
    """

    action_id: str
    action_digest: str
    run_id: str
    capability: str
    risk_tier: RiskTier
    result_class: ResultClass
    executed_at: datetime
    policy_version: str
    decision_id: str
    evidence_ref: str
    idempotency_key: str | None = None
    approval_id: str | None = None
    output_digest: str | None = None
    verified: bool = False
    compensation_capability: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_reversible(self) -> bool:
        """Whether a compensating action was declared for this receipt."""
        return self.compensation_capability is not None
