"""The hash-chained evidence ledger.

Evidence is part of the transaction, not a log written afterwards. The
transaction guard appends the policy decision and the execution receipt to this
ledger inside the same call that performs the side effect, so "the action
happened but nothing recorded it" is not a reachable state.

The chain is what makes the record tamper-evident. Each event's hash covers its
own contents *and* its predecessor's hash, so editing or removing any event
invalidates every hash after it. That does not stop someone with write access
from rewriting the whole file -- nothing in a single-writer ledger can -- but it
turns a silent edit into a detectable one, and ``verify()`` is cheap enough to
run on every read and in CI.

Two reference implementations are provided: in-memory for tests and the local
sandbox, and append-only JSONL for single-node runs and forensic export.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..canonical import canonical_json, digest
from ..contracts.evidence import EventDomain, EvidenceEvent
from ..errors import EvidenceChainBroken

REDACTED = "[redacted]"
"""Placeholder written into the open ledger in place of protected payloads."""


@runtime_checkable
class EvidenceLedger(Protocol):
    """Append-only, tamper-evident evidence storage."""

    def append(
        self,
        *,
        domain: EventDomain,
        event_type: str,
        run_id: str,
        tenant: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        action_id: str | None = None,
        action_digest: str | None = None,
        correlation_id: str | None = None,
        protected: dict[str, Any] | None = None,
    ) -> EvidenceEvent:
        """Append one event and return it, including its chain position."""
        ...

    def events(self, *, run_id: str | None = None) -> tuple[EvidenceEvent, ...]:
        """Read events, optionally narrowed to one run."""
        ...

    def verify(self) -> None:
        """Recompute the chain, raising ``EvidenceChainBroken`` if it does not hold."""
        ...


class _ChainWriter:
    """Shared chain arithmetic for the ledger implementations."""

    def __init__(self) -> None:
        self._events: list[EvidenceEvent] = []
        self._protected: dict[str, dict[str, Any]] = {}

    @property
    def head_hash(self) -> str | None:
        """The hash of the most recent event, or ``None`` for an empty chain."""
        return self._events[-1].event_hash if self._events else None

    def build(
        self,
        *,
        domain: EventDomain,
        event_type: str,
        run_id: str,
        tenant: str,
        actor: str,
        payload: dict[str, Any] | None,
        action_id: str | None,
        action_digest: str | None,
        correlation_id: str | None,
        protected: dict[str, Any] | None,
    ) -> EvidenceEvent:
        """Construct the next event in the chain and store any protected payload."""
        protected_ref: str | None = None
        protected_digest: str | None = None
        if protected is not None:
            protected_digest = digest(protected)
            protected_ref = f"protected:{run_id}:{len(self._events)}"
            # Held behind a reference rather than inlined, so the ledger itself
            # never becomes the easiest place to read customer data.
            self._protected[protected_ref] = protected

        event = EvidenceEvent(
            sequence=len(self._events),
            prev_hash=self.head_hash,
            recorded_at=datetime.now(UTC),
            domain=domain,
            event_type=event_type,
            run_id=run_id,
            tenant=tenant,
            actor=actor,
            payload=dict(payload or {}),
            action_id=action_id,
            action_digest=action_digest,
            correlation_id=correlation_id,
            protected_ref=protected_ref,
            protected_digest=protected_digest,
        )
        self._events.append(event)
        return event

    def verify_chain(self) -> None:
        """Recompute every link, raising on the first mismatch."""
        expected_prev: str | None = None
        for position, event in enumerate(self._events):
            if event.sequence != position:
                raise EvidenceChainBroken(
                    f"event at position {position} claims sequence {event.sequence}"
                )
            if event.prev_hash != expected_prev:
                raise EvidenceChainBroken(
                    f"event {event.sequence} does not chain onto its predecessor"
                )
            expected_prev = event.event_hash

    def reveal(self, protected_ref: str) -> dict[str, Any]:
        """Read a protected payload back, for an authorised forensic export."""
        try:
            return self._protected[protected_ref]
        except KeyError:
            raise EvidenceChainBroken(
                f"protected payload {protected_ref!r} is missing from the ledger"
            ) from None


class InMemoryEvidenceLedger(_ChainWriter):
    """Reference ledger for tests, the local sandbox and single-run demos."""

    def append(
        self,
        *,
        domain: EventDomain,
        event_type: str,
        run_id: str,
        tenant: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        action_id: str | None = None,
        action_digest: str | None = None,
        correlation_id: str | None = None,
        protected: dict[str, Any] | None = None,
    ) -> EvidenceEvent:
        """Append one event and return it, including its chain position."""
        return self.build(
            domain=domain,
            event_type=event_type,
            run_id=run_id,
            tenant=tenant,
            actor=actor,
            payload=payload,
            action_id=action_id,
            action_digest=action_digest,
            correlation_id=correlation_id,
            protected=protected,
        )

    def events(self, *, run_id: str | None = None) -> tuple[EvidenceEvent, ...]:
        """Read events, optionally narrowed to one run."""
        if run_id is None:
            return tuple(self._events)
        return tuple(event for event in self._events if event.run_id == run_id)

    def verify(self) -> None:
        """Recompute the chain, raising ``EvidenceChainBroken`` if it does not hold."""
        self.verify_chain()


class JsonlEvidenceLedger(InMemoryEvidenceLedger):
    """Append-only JSONL ledger, one canonical JSON object per line.

    Each line carries the event and its computed hash, so the file can be
    checked for integrity by a reader that has never seen this code. Writes are
    flushed and fsynced before ``append`` returns: an evidence record that is
    still in a buffer when the process dies is not evidence.
    """

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Where the ledger is written."""
        return self._path

    def append(
        self,
        *,
        domain: EventDomain,
        event_type: str,
        run_id: str,
        tenant: str,
        actor: str,
        payload: dict[str, Any] | None = None,
        action_id: str | None = None,
        action_digest: str | None = None,
        correlation_id: str | None = None,
        protected: dict[str, Any] | None = None,
    ) -> EvidenceEvent:
        """Append one event, durably, and return it."""
        event = super().append(
            domain=domain,
            event_type=event_type,
            run_id=run_id,
            tenant=tenant,
            actor=actor,
            payload=payload,
            action_id=action_id,
            action_digest=action_digest,
            correlation_id=correlation_id,
            protected=protected,
        )
        line = canonical_json({"event": event, "event_hash": event.event_hash})
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return event

    def read_raw(self) -> Iterator[dict[str, Any]]:
        """Stream the file back as parsed records, for external verification."""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
