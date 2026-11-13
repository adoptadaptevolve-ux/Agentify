"""The human decision service.

Human approval is the most expensive control the platform has and the easiest
to ruin. Route everything ambiguous to a queue and the queue stops being read;
route it to a generic "AI review" queue and it is read by people who cannot
judge it. So this service is built around four rules:

* **Authorization first.** A reviewer is only ever asked about actions the
  caller was already authorized to request. Missing authority is a deny, which
  the policy engine has already returned before anything reaches here.
* **Digest binding.** A decision names one action digest and is spent once.
  There is no approval of a plan, a description or an intent-in-principle.
* **Domain queues.** Items route to the team that can actually judge them.
* **Expiry denies.** A lapsed window is a denial, never a timeout into allow.

Where items are kept is a separate concern behind :class:`ApprovalStore`. The
default :class:`InMemoryApprovalStore` is right for the sandbox and wrong for
production: an approval that is lost on restart is an action that silently
never happens. :class:`causeway.storage.SqliteApprovalStore` keeps items, decisions
and spent markers on disk, so a restart resumes exactly where it stopped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ..canonical import digest
from ..contracts.human import (
    ApprovalItem,
    DecisionOutcome,
    EvidencePacket,
    SignedDecision,
)
from ..contracts.risk import RiskTier
from ..errors import ContractViolation, DigestMismatch, ReplayDetected
from ..ids import new_approval_id

DEFAULT_WINDOW = timedelta(hours=4)
"""How long a reviewer has before the item expires and denies."""

DEFAULT_DECISION_VALIDITY = timedelta(minutes=30)
"""How long an approval may be spent after it is granted. Short, because the
world the reviewer looked at goes stale."""


@dataclass(slots=True)
class ApprovalRecord:
    """One item with everything decided about it so far."""

    item: ApprovalItem
    decisions: list[SignedDecision] = field(default_factory=list)
    spent: bool = False


class ApprovalStore(Protocol):
    """Where approval items, decisions and spent markers live.

    The service holds all of the rules; a store only remembers. Each method is a
    single write or read so a durable store can make every one atomic.
    """

    def approval_for_digest(self, action_digest: str) -> str | None:
        """The approval id opened for ``action_digest``, if any."""
        ...

    def load(self, approval_id: str) -> ApprovalRecord | None:
        """The full record for ``approval_id``, if it exists."""
        ...

    def insert(self, item: ApprovalItem) -> ApprovalItem:
        """Store a new item, or return the existing one for the same digest.

        Returning the existing item, rather than failing, keeps two concurrent
        requests for the same action from opening two queue entries.
        """
        ...

    def add_decision(self, decision: SignedDecision) -> None:
        """Append one reviewer's decision.

        Raises :class:`ReplayDetected` if that reviewer already decided this
        item, so the dual-control rule holds even under a race.
        """
        ...

    def mark_spent(self, approval_id: str) -> None:
        """Record that the approval has been consumed."""
        ...

    def records(self) -> list[ApprovalRecord]:
        """Every record, for queue views."""
        ...


class InMemoryApprovalStore:
    """Reference store: a pair of dicts. Lost on restart."""

    def __init__(self) -> None:
        self._by_approval: dict[str, ApprovalRecord] = {}
        self._by_digest: dict[str, str] = {}

    def approval_for_digest(self, action_digest: str) -> str | None:
        return self._by_digest.get(action_digest)

    def load(self, approval_id: str) -> ApprovalRecord | None:
        return self._by_approval.get(approval_id)

    def insert(self, item: ApprovalItem) -> ApprovalItem:
        existing = self._by_digest.get(item.action_digest)
        if existing is not None:
            return self._by_approval[existing].item
        self._by_approval[item.approval_id] = ApprovalRecord(item=item)
        self._by_digest[item.action_digest] = item.approval_id
        return item

    def add_decision(self, decision: SignedDecision) -> None:
        record = self._by_approval[decision.approval_id]
        if any(prior.approver_id == decision.approver_id for prior in record.decisions):
            raise ReplayDetected(
                f"{decision.approver_id} has already decided approval {decision.approval_id}"
            )
        record.decisions.append(decision)

    def mark_spent(self, approval_id: str) -> None:
        self._by_approval[approval_id].spent = True

    def records(self) -> list[ApprovalRecord]:
        return list(self._by_approval.values())


class HumanDecisionService:
    """A digest-bound approval queue with dual control and expiry."""

    def __init__(
        self,
        *,
        window: timedelta = DEFAULT_WINDOW,
        validity: timedelta = DEFAULT_DECISION_VALIDITY,
        store: ApprovalStore | None = None,
    ) -> None:
        self._window = window
        self._validity = validity
        self._store: ApprovalStore = store if store is not None else InMemoryApprovalStore()

    def request(
        self,
        *,
        action_digest: str,
        run_id: str,
        tenant: str,
        queue: str,
        packet: EvidencePacket,
        required_approvals: int = 1,
        required_authority: frozenset[str] = frozenset(),
        now: datetime | None = None,
    ) -> ApprovalItem:
        """Open a review item for one action digest.

        Re-requesting the same digest returns the existing item rather than
        opening a second one: the same action retried must not produce two
        queue entries that two reviewers each approve half of.
        """
        moment = now or datetime.now(UTC)
        existing_id = self._store.approval_for_digest(action_digest)
        if existing_id is not None:
            return self._require(existing_id).item

        item = ApprovalItem(
            approval_id=new_approval_id(),
            action_digest=action_digest,
            run_id=run_id,
            tenant=tenant,
            queue=queue,
            requested_at=moment,
            expires_at=moment + self._window,
            packet=packet,
            required_approvals=required_approvals,
            required_authority=required_authority,
        )
        return self._store.insert(item)

    def decide(
        self,
        *,
        approval_id: str,
        approver_id: str,
        approver_authority: frozenset[str],
        outcome: DecisionOutcome,
        rationale: str = "",
        now: datetime | None = None,
    ) -> SignedDecision:
        """Record one reviewer's decision on an item."""
        moment = now or datetime.now(UTC)
        record = self._require(approval_id)
        item = record.item

        if moment >= item.expires_at:
            raise ContractViolation(
                f"approval {approval_id} expired at {item.expires_at.isoformat()}"
            )
        if item.required_authority and not (item.required_authority & approver_authority):
            raise ContractViolation(
                f"{approver_id} lacks the authority this item requires: "
                f"{sorted(item.required_authority)}"
            )
        if any(prior.approver_id == approver_id for prior in record.decisions):
            # Dual control means two people, not one person twice.
            raise ReplayDetected(f"{approver_id} has already decided approval {approval_id}")

        decision = SignedDecision(
            approval_id=approval_id,
            action_digest=item.action_digest,
            outcome=outcome,
            approver_id=approver_id,
            approver_authority=approver_authority,
            decided_at=moment,
            expires_at=moment + self._validity,
            rationale=rationale,
            signature=digest(
                {
                    "approval_id": approval_id,
                    "action_digest": item.action_digest,
                    "outcome": outcome.value,
                    "approver_id": approver_id,
                    "decided_at": moment,
                }
            ),
        )
        self._store.add_decision(decision)
        return decision

    def resolve(
        self, action_digest: str, *, now: datetime | None = None
    ) -> SignedDecision | None:
        """Return a spendable approval for ``action_digest``, if there is one.

        Returns ``None`` when the item is pending, short of its dual-control
        quorum, or already spent. Returns an ``EXPIRED`` decision when the
        window lapsed, so the caller records a denial rather than a silence.
        """
        moment = now or datetime.now(UTC)
        approval_id = self._store.approval_for_digest(action_digest)
        if approval_id is None:
            return None
        record = self._require(approval_id)
        item = record.item

        denials = [d for d in record.decisions if d.outcome is DecisionOutcome.DENIED]
        if denials:
            return denials[0]
        if record.spent:
            return None
        if moment >= item.expires_at:
            return SignedDecision(
                approval_id=approval_id,
                action_digest=action_digest,
                outcome=DecisionOutcome.EXPIRED,
                approver_id="system",
                approver_authority=frozenset(),
                decided_at=item.expires_at,
                expires_at=item.expires_at,
                rationale="the review window lapsed without a decision",
            )

        approvals = [
            d
            for d in record.decisions
            if d.outcome is DecisionOutcome.APPROVED and d.is_live(moment)
        ]
        if len(approvals) < item.required_approvals:
            return None
        # Return the last approval: its validity window is the binding one.
        return approvals[-1]

    def spend(self, action_digest: str) -> None:
        """Mark an approval as consumed so it cannot be replayed.

        Called by the transaction guard after the side effect succeeds.
        """
        approval_id = self._store.approval_for_digest(action_digest)
        if approval_id is None:
            raise DigestMismatch(f"no approval item for digest {action_digest}")
        self._store.mark_spent(approval_id)

    def pending(self, *, queue: str | None = None) -> tuple[ApprovalItem, ...]:
        """Items still awaiting a decision, for a queue dashboard."""
        return tuple(
            record.item
            for record in self._store.records()
            if not record.spent
            and len(record.decisions) < record.item.required_approvals
            and (queue is None or record.item.queue == queue)
        )

    def item(self, approval_id: str) -> ApprovalItem:
        """Fetch one item."""
        return self._require(approval_id).item

    def _require(self, approval_id: str) -> ApprovalRecord:
        record = self._store.load(approval_id)
        if record is None:
            raise ContractViolation(f"unknown approval {approval_id!r}")
        return record


def build_packet(
    *,
    intent_summary: str,
    capability: str,
    risk_tier: RiskTier,
    resource: str | None,
    destination: str | None,
    amount_display: str | None,
    delegation_scope: dict[str, object],
    risk_factors: tuple[str, ...],
    policy_rationale: str,
    sources: tuple[str, ...] = (),
    alternatives: tuple[str, ...] = (),
    rollback: str | None = None,
    recommendation: str | None = None,
) -> EvidencePacket:
    """Assemble the packet a reviewer sees.

    Everything here exists so the reviewer can disagree: the alternatives, the
    rollback and the explicit risk factors are what turn "approve?" into a
    decision rather than a confirmation.
    """
    return EvidencePacket(
        intent_summary=intent_summary,
        capability=capability,
        risk_tier=risk_tier,
        resource=resource,
        destination=destination,
        amount_display=amount_display,
        delegation_scope=delegation_scope,
        risk_factors=risk_factors,
        policy_rationale=policy_rationale,
        sources=sources,
        alternatives=alternatives,
        rollback=rollback,
        recommendation=recommendation,
    )
