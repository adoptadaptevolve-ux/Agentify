"""Run reconstruction and forensic export.

The auditability test for this platform is not "are there logs?" but "can any
consequential run be reconstructed without log archaeology?". That means being
able to answer, from the ledger alone: who asked, what the agent saw, which
model, policy and tool versions ran, what changed, and who approved it.

``reconstruct()`` answers it as a structure; ``forensic_package()`` answers it
as a single exportable document for an incident, a model validation or an audit
sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..contracts.evidence import EventDomain, EvidenceEvent
from . import events as event_types
from .ledger import EvidenceLedger


@dataclass(slots=True)
class RunReconstruction:
    """Everything the ledger knows about one run, grouped for reading."""

    run_id: str
    tenant: str | None = None
    principal_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    status: str = "unknown"
    actions: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    judgments: list[dict[str, Any]] = field(default_factory=list)
    retrievals: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[EvidenceEvent] = field(default_factory=list)

    @property
    def had_side_effect(self) -> bool:
        """Whether any action in this run actually executed."""
        return any(action.get("result_class") == "success" for action in self.actions)

    @property
    def unverified_actions(self) -> list[dict[str, Any]]:
        """Actions that executed but whose post-condition did not confirm.

        These are the reconciliation queue: the side effect may or may not have
        landed, so they are neither retried blindly nor quietly forgotten.
        """
        return [action for action in self.actions if action.get("verified") is False]


def reconstruct(ledger: EvidenceLedger, run_id: str) -> RunReconstruction:
    """Rebuild one run from the evidence chain.

    Verifies the chain first: a reconstruction from a broken chain would be
    worse than no reconstruction, because it would look authoritative.
    """
    ledger.verify()
    reconstruction = RunReconstruction(run_id=run_id)
    for event in ledger.events(run_id=run_id):
        reconstruction.timeline.append(event)
        reconstruction.tenant = reconstruction.tenant or event.tenant
        payload = event.payload

        match event.event_type:
            case event_types.RUN_STARTED:
                reconstruction.started_at = payload.get("started_at")
                reconstruction.principal_id = payload.get("principal_id")
                reconstruction.status = "running"
            case event_types.RUN_FINISHED:
                reconstruction.finished_at = payload.get("finished_at")
                reconstruction.status = payload.get("status", "finished")
            case event_types.RUN_FAILED:
                reconstruction.finished_at = payload.get("finished_at")
                reconstruction.status = "failed"
            case event_types.POLICY_EVALUATED | event_types.POLICY_DENIED:
                reconstruction.decisions.append({**payload, "action_digest": event.action_digest})
            case (
                event_types.ACTION_EXECUTED
                | event_types.ACTION_DUPLICATE
                | event_types.ACTION_FAILED
                | event_types.ACTION_UNVERIFIED
                | event_types.ACTION_COMPENSATED
            ):
                reconstruction.actions.append(
                    {**payload, "action_id": event.action_id, "action_digest": event.action_digest}
                )
            case (
                event_types.APPROVAL_REQUESTED
                | event_types.APPROVAL_GRANTED
                | event_types.APPROVAL_DENIED
                | event_types.APPROVAL_EXPIRED
            ):
                reconstruction.approvals.append({**payload, "event_type": event.event_type})
            case event_types.RETRIEVAL_PERFORMED:
                reconstruction.retrievals.append(payload)
            case _:
                if event.domain is EventDomain.JUDGMENT:
                    reconstruction.judgments.append({**payload, "event_type": event.event_type})
    return reconstruction


def forensic_package(ledger: EvidenceLedger, run_id: str) -> dict[str, Any]:
    """Produce a single self-contained export for audit or incident review.

    Includes the chain head so a recipient can prove the package matches the
    ledger it was taken from, and the integrity verdict so that a package taken
    from a broken chain says so on its face.
    """
    chain_ok = True
    integrity_error: str | None = None
    try:
        ledger.verify()
    except Exception as exc:
        chain_ok = False
        integrity_error = str(exc)

    run_events = ledger.events(run_id=run_id)
    return {
        "run_id": run_id,
        "chain_intact": chain_ok,
        "integrity_error": integrity_error,
        "event_count": len(run_events),
        "chain_head": run_events[-1].event_hash if run_events else None,
        "events": [
            {
                "sequence": event.sequence,
                "recorded_at": event.recorded_at,
                "domain": event.domain,
                "event_type": event.event_type,
                "actor": event.actor,
                "action_id": event.action_id,
                "action_digest": event.action_digest,
                "payload": event.payload,
                "protected_ref": event.protected_ref,
                "protected_digest": event.protected_digest,
                "event_hash": event.event_hash,
            }
            for event in run_events
        ],
    }
