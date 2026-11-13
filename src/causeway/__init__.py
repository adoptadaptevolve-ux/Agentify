"""Causeway: an enterprise paved road for agentic AI.

The thesis in one line: **separate the freedom to reason from the authority to
act.** A model may reason with approved models and enterprise knowledge, but
every real-world side effect executes only through a typed capability, verified
delegation, deterministic policy, transaction guardrails and a complete
evidence trail.

The planes:

* ``contracts``  -- the stable trust seams. Data and protocols only.
* ``authority``  -- signed, attenuating delegation. The only place authority is minted.
* ``policy``     -- tiered, deterministic decisions from versioned artifacts.
* ``registry``   -- what the platform is willing to run, and with which models.
* ``judgment``   -- typed System One judgments. Informs decisions, never makes them.
* ``verify``     -- falsification suites and the supervisor board.
* ``gateway``    -- the transaction guard: the one enforcement point for side effects.
* ``knowledge``  -- entitlement-aware retrieval and scoped memory.
* ``human``      -- digest-bound approvals routed to domain queues.
* ``evidence``   -- the hash-chained ledger and run reconstruction.
* ``runtime``    -- explicit workflow graphs, budgets, checkpoints, interrupts.
* ``sdk``        -- the developer surface, where only governed operations exist.

Start at ``docs/architecture/01-overview.md``; build something at
``docs/guides/quickstart.md``.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .contracts import (
    ActionIntent,
    ApprovalItem,
    AuthStrength,
    Constraints,
    DataClass,
    DecisionOutcome,
    Delegation,
    Effect,
    EvidenceEvent,
    ExecutionReceipt,
    Obligation,
    PolicyDecision,
    Principal,
    PrincipalKind,
    RiskTier,
    SignedDelegation,
    WorkloadIdentity,
)
from .errors import (
    ApprovalRequired,
    AttenuationViolation,
    BudgetExhausted,
    CausewayError,
    DigestMismatch,
    PolicyDenied,
    ValidationFailed,
)
from .sdk import AgentRun, Platform

__all__ = [
    "ActionIntent",
    "AgentRun",
    "ApprovalItem",
    "ApprovalRequired",
    "AttenuationViolation",
    "AuthStrength",
    "BudgetExhausted",
    "CausewayError",
    "Constraints",
    "DataClass",
    "DecisionOutcome",
    "Delegation",
    "DigestMismatch",
    "Effect",
    "EvidenceEvent",
    "ExecutionReceipt",
    "Obligation",
    "Platform",
    "PolicyDecision",
    "PolicyDenied",
    "Principal",
    "PrincipalKind",
    "RiskTier",
    "SignedDelegation",
    "ValidationFailed",
    "WorkloadIdentity",
    "__version__",
]
