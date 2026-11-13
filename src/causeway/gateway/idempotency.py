"""Idempotency for side effects.

Retries and failovers must not duplicate transfers, refunds, messages or
mutations. The rule the platform enforces is narrow and absolute: a mutation
carries an idempotency key, and a second attempt with the same key returns the
first attempt's receipt instead of executing again.

The subtle part is the in-flight window. A naive store that only records
*completed* attempts lets a retry fire while the first attempt is still
running, which is exactly when a duplicate is most likely. So a key is reserved
before execution and released only when the outcome is known.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..contracts.action import ExecutionReceipt
from ..errors import ContractViolation

DEFAULT_RETENTION = timedelta(hours=24)
"""How long a key's outcome is remembered. Long enough to cover every retry
path the platform has, including a human retrying by hand the next morning."""


@dataclass(slots=True)
class _Entry:
    action_digest: str
    reserved_at: datetime
    receipt: ExecutionReceipt | None = None


class IdempotencyStore:
    """Reference in-memory store. Production needs a shared, durable one."""

    def __init__(self, *, retention: timedelta = DEFAULT_RETENTION) -> None:
        self._entries: dict[str, _Entry] = {}
        self._retention = retention

    def lookup(self, key: str, *, action_digest: str) -> ExecutionReceipt | None:
        """Return the prior receipt for ``key``, if the action completed.

        Raises when the same key is presented for a *different* payload: that
        is a caller bug, and silently treating it as a duplicate would suppress
        a real action that was meant to happen.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.action_digest != action_digest:
            raise ContractViolation(
                f"idempotency key {key!r} was already used for a different action; "
                "reusing a key across payloads would suppress a real action"
            )
        return entry.receipt

    def reserve(self, key: str, *, action_digest: str, now: datetime | None = None) -> None:
        """Claim ``key`` before execution begins."""
        moment = now or datetime.now(UTC)
        existing = self._entries.get(key)
        if existing is not None and existing.receipt is None:
            raise ContractViolation(
                f"idempotency key {key!r} is already in flight; "
                "a concurrent retry would duplicate the side effect"
            )
        self._entries[key] = _Entry(action_digest=action_digest, reserved_at=moment)

    def complete(self, key: str, receipt: ExecutionReceipt) -> None:
        """Record the outcome for ``key``."""
        entry = self._entries.get(key)
        if entry is None:
            raise ContractViolation(f"idempotency key {key!r} was never reserved")
        entry.receipt = receipt

    def release(self, key: str) -> None:
        """Drop a reservation whose action failed before producing a receipt.

        Releasing lets the caller retry. Failures that may have landed a side
        effect are *not* released -- they keep the reservation and surface as
        unverified, for reconciliation rather than blind retry.
        """
        entry = self._entries.get(key)
        if entry is not None and entry.receipt is None:
            del self._entries[key]

    def purge(self, *, now: datetime | None = None) -> int:
        """Drop entries past their retention window; returns how many went."""
        moment = now or datetime.now(UTC)
        cutoff = moment - self._retention
        stale = [
            key
            for key, entry in self._entries.items()
            if entry.receipt is not None and entry.reserved_at < cutoff
        ]
        for key in stale:
            del self._entries[key]
        return len(stale)
