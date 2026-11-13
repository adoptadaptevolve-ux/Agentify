"""Evidence records.

Audit is part of the transaction, not an asynchronous best-effort log. Every
consequential step appends an event to a hash-chained ledger *before* the step
is allowed to be considered complete, so a run that produced a side effect
cannot also be a run with no record of it.

Events are deliberately thin on payload. High-cardinality or sensitive
arguments are stored as a digest plus a ``protected_ref`` into controlled
storage, so that the widely readable ledger proves *what happened* without
itself becoming an exfiltration target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..canonical import digest


class EventDomain(StrEnum):
    """Which plane an event came from.

    Fixed across frameworks and models so that organisation-wide analysis does
    not have to know which orchestration library produced a run.
    """

    IDENTITY = "identity"
    AGENT = "agent"
    MODEL = "model"
    JUDGMENT = "judgment"
    RETRIEVAL = "retrieval"
    MEMORY = "memory"
    POLICY = "policy"
    TOOL = "tool"
    HUMAN = "human"
    OUTCOME = "outcome"


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """One tamper-evident link in a run's evidence chain.

    ``prev_hash`` and ``event_hash`` form the chain. Recomputing every
    ``event_hash`` from its own contents and comparing against the stored chain
    is what makes a silent edit or deletion detectable -- see
    ``causeway.evidence.ledger.EvidenceLedger.verify``.
    """

    sequence: int
    prev_hash: str | None
    recorded_at: datetime
    domain: EventDomain
    event_type: str
    run_id: str
    tenant: str
    actor: str
    """Who or what caused this event: a principal id, workload or service name."""
    payload: dict[str, Any] = field(default_factory=dict)
    """Non-sensitive, low-cardinality facts safe to hold in the open ledger."""
    action_id: str | None = None
    action_digest: str | None = None
    correlation_id: str | None = None
    protected_ref: str | None = None
    """Reference into controlled storage for payloads too sensitive to inline."""
    protected_digest: str | None = None
    """Digest of the protected payload, so it can be proven unmodified later."""

    @property
    def event_hash(self) -> str:
        """The digest of this event's contents, including its predecessor."""
        return digest(
            {
                "sequence": self.sequence,
                "prev_hash": self.prev_hash,
                "recorded_at": self.recorded_at,
                "domain": self.domain,
                "event_type": self.event_type,
                "run_id": self.run_id,
                "tenant": self.tenant,
                "actor": self.actor,
                "payload": self.payload,
                "action_id": self.action_id,
                "action_digest": self.action_digest,
                "correlation_id": self.correlation_id,
                "protected_ref": self.protected_ref,
                "protected_digest": self.protected_digest,
            }
        )
