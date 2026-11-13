"""Human decision items.

Human approval is a scarce control. It is not the catch-all for ambiguous
agent behaviour: at enterprise scale even a very small review rate produces
alert fatigue and rubber-stamping, which converts a control into a formality.

Two rules keep it meaningful:

* **Authorization first.** A human cannot approve an action the caller was not
  authorized to request. Missing authority is a deny, not a review.
* **Digest binding.** An approval names one action digest. It cannot be
  replayed onto a different payload, and it cannot be granted against a
  natural-language plan that may mutate before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from .risk import RiskTier


class DecisionOutcome(StrEnum):
    """What a reviewer decided."""

    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    """The response window lapsed. Expiry denies; it never times out into allow."""


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    """What a reviewer is shown, so the decision is informed rather than nominal.

    Deliberately includes ``alternatives`` and ``rollback``: a reviewer who can
    only say yes or no to a single option, with no stated way back, is being
    asked to rubber-stamp.
    """

    intent_summary: str
    capability: str
    risk_tier: RiskTier
    resource: str | None
    destination: str | None
    amount_display: str | None
    delegation_scope: dict[str, Any]
    risk_factors: tuple[str, ...]
    policy_rationale: str
    sources: tuple[str, ...] = field(default_factory=tuple)
    alternatives: tuple[str, ...] = field(default_factory=tuple)
    rollback: str | None = None
    recommendation: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalItem:
    """One pending decision, bound to exactly one action digest."""

    approval_id: str
    action_digest: str
    run_id: str
    tenant: str
    queue: str
    """A domain queue -- payments, servicing, fraud, privacy -- never a generic
    "AI review" queue, which is where review quality goes to die."""
    requested_at: datetime
    expires_at: datetime
    packet: EvidencePacket
    required_approvals: int = 1
    """Two or more expresses dual control."""
    required_authority: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class SignedDecision:
    """A reviewer's decision, bound to the digest and to the reviewer."""

    approval_id: str
    action_digest: str
    outcome: DecisionOutcome
    approver_id: str
    approver_authority: frozenset[str]
    decided_at: datetime
    expires_at: datetime
    rationale: str = ""
    signature: str = ""

    def is_live(self, now: datetime) -> bool:
        """Whether the decision may still be spent."""
        return self.outcome is DecisionOutcome.APPROVED and now < self.expires_at
