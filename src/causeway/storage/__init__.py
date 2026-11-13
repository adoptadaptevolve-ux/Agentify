"""Durable backing stores for state that must survive a restart.

The platform's reference stores are in-memory. Three of them hold state whose
loss is a control failure rather than an inconvenience:

* **approvals** -- an approval lost on restart is an action that silently never
  happens, or a reviewer's denial that silently disappears;
* **idempotency keys** -- a forgotten key lets a retry repeat a side effect;
* **delegation nonces** -- a forgotten nonce is a replay window.

This package backs all three with SQLite from the standard library, so the
trust-critical path still has no third-party dependency
(see ``docs/decisions/0006-zero-dependency-core.md``).

SQLite makes the state durable and safe across threads and processes on one
host. It is not a replica-shared store: several hosts need a networked database
behind the same interfaces.
"""

from .sqlite import (
    SqliteApprovalStore,
    SqliteIdempotencyStore,
    SqliteNonceStore,
    StateDatabase,
    open_state_db,
)

__all__ = [
    "SqliteApprovalStore",
    "SqliteIdempotencyStore",
    "SqliteNonceStore",
    "StateDatabase",
    "open_state_db",
]
