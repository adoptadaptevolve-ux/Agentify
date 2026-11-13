"""Compensation: undoing a failed run's completed side effects.

A multi-step journey that fails half way leaves the world in a state nobody
chose: the case was opened but the owner was never told, the refund was issued
but the ticket was never updated. Distributed systems call the fix a *saga*:
every reversible step declares how to undo itself, and when a later step fails
the completed ones are undone in reverse order.

Three rules make that safe rather than a second source of damage:

* **Undo is a governed action.** A compensation goes through the same
  transaction guard as anything else: delegation, policy, approval, idempotency
  and evidence all apply. The run must hold the compensating capability, and
  policy can still refuse it or route it to a human. Rolling back is not a
  licence to skip controls.
* **Undo is idempotent.** Each compensation carries the key
  ``compensate:<original action id>``. A retried rollback -- including one after
  a restart, when the platform keeps its idempotency store on disk -- returns
  the first rollback's receipt instead of undoing twice.
* **What cannot be undone is said out loud.** An irreversible action, a missing
  argument recipe, a refusal or a failed undo is reported per action on the
  run, so it goes to reconciliation instead of being assumed fine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..contracts.action import ResultClass
from ..errors import ApprovalRequired

if TYPE_CHECKING:  # pragma: no cover
    from ..sdk import AgentRun


def compensation_key(action_id: str) -> str:
    """The idempotency key that makes undoing ``action_id`` happen at most once."""
    return f"compensate:{action_id}"


@dataclass(frozen=True, slots=True)
class CompensationEntry:
    """One completed, reversible action and how to undo it.

    Recorded by the run when the action's receipt comes back, while the
    original arguments and the downstream reference are still at hand.
    """

    action_id: str
    capability: str
    compensation_capability: str
    arguments: Mapping[str, Any] | None
    """Arguments for the compensating call, or ``None`` if the capability
    declared what undoes it but not how to call it."""
    external_ref: str | None = None
    problem: str | None = None
    """Why ``arguments`` could not be derived, when it is ``None``."""


class CompensationStatus(StrEnum):
    """What happened when the platform tried to undo one action."""

    COMPENSATED = "compensated"
    AWAITING_APPROVAL = "awaiting_approval"
    """Policy routed the undo to a human. Nothing has been undone yet."""
    MANUAL = "manual"
    """The platform cannot run this undo itself; a person must."""
    FAILED = "failed"
    """The undo was attempted and refused or errored."""


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    """The result of undoing one action."""

    action_id: str
    capability: str
    compensation_capability: str
    status: CompensationStatus
    compensation_action_id: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CompensationReport:
    """Everything the platform did to unwind a failed run."""

    reason: str
    outcomes: tuple[CompensationOutcome, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Whether every recorded action was undone."""
        return all(o.status is CompensationStatus.COMPENSATED for o in self.outcomes)

    @property
    def outstanding(self) -> tuple[CompensationOutcome, ...]:
        """Actions still standing, for reconciliation."""
        return tuple(o for o in self.outcomes if o.status is not CompensationStatus.COMPENSATED)

    def describe(self) -> dict[str, Any]:
        """A JSON-ready summary for evidence."""
        return {
            "reason": self.reason,
            "complete": self.complete,
            "outcomes": [
                {
                    "action_id": o.action_id,
                    "capability": o.capability,
                    "compensation_capability": o.compensation_capability,
                    "status": o.status.value,
                    "compensation_action_id": o.compensation_action_id,
                    "detail": o.detail,
                }
                for o in self.outcomes
            ],
        }


def compensate_run(run: AgentRun, *, reason: str) -> CompensationReport:
    """Undo the run's completed reversible actions, newest first.

    Every recorded action is attempted even if an earlier undo fails: leaving
    more of the world changed because one rollback was refused would make the
    failure worse, not safer. Each outcome is reported individually.
    """
    outcomes: list[CompensationOutcome] = []
    for entry in reversed(run.compensations):
        outcomes.append(_undo(run, entry))
    return CompensationReport(reason=reason, outcomes=tuple(outcomes))


def _undo(run: AgentRun, entry: CompensationEntry) -> CompensationOutcome:
    base: dict[str, Any] = {
        "action_id": entry.action_id,
        "capability": entry.capability,
        "compensation_capability": entry.compensation_capability,
    }
    if entry.arguments is None:
        return CompensationOutcome(
            **base,
            status=CompensationStatus.MANUAL,
            detail=entry.problem
            or f"{entry.capability} names {entry.compensation_capability} as its undo "
            "but does not say how to call it",
        )
    try:
        receipt = run.compensate(entry)
    except ApprovalRequired as pause:
        return CompensationOutcome(
            **base,
            status=CompensationStatus.AWAITING_APPROVAL,
            detail=f"undo is waiting on approval {pause.approval_id}",
        )
    except Exception as exc:
        return CompensationOutcome(
            **base,
            status=CompensationStatus.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
        )
    if receipt.result_class not in (ResultClass.SUCCESS, ResultClass.DUPLICATE):
        return CompensationOutcome(
            **base,
            status=CompensationStatus.FAILED,
            compensation_action_id=receipt.action_id,
            detail=f"undo finished as {receipt.result_class.value}",
        )
    return CompensationOutcome(
        **base,
        status=CompensationStatus.COMPENSATED,
        compensation_action_id=receipt.action_id,
    )
