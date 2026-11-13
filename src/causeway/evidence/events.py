"""Event type names.

Held in one place so that dashboards, forensic exports and tests agree on the
spelling, and so that renaming an event is a visible change rather than a
silent break in someone's audit query.
"""

from __future__ import annotations

from typing import Final

# Identity and authority
RUN_STARTED: Final = "run.started"
RUN_FINISHED: Final = "run.finished"
RUN_FAILED: Final = "run.failed"
DELEGATION_MINTED: Final = "delegation.minted"
DELEGATION_ATTENUATED: Final = "delegation.attenuated"
DELEGATION_REJECTED: Final = "delegation.rejected"

# Judgment plane
JUDGMENT_REQUESTED: Final = "judgment.requested"
JUDGMENT_ANSWERED: Final = "judgment.answered"
FALSIFICATION_RUN: Final = "judgment.falsification"
BOARD_DELIBERATED: Final = "judgment.board_deliberated"

# Policy
POLICY_EVALUATED: Final = "policy.evaluated"
POLICY_DENIED: Final = "policy.denied"

# Tool plane
ACTION_PROPOSED: Final = "tool.action_proposed"
ACTION_EXECUTED: Final = "tool.action_executed"
ACTION_DUPLICATE: Final = "tool.action_duplicate"
ACTION_FAILED: Final = "tool.action_failed"
ACTION_VERIFIED: Final = "tool.action_verified"
ACTION_UNVERIFIED: Final = "tool.action_unverified"
ACTION_COMPENSATED: Final = "tool.action_compensated"

# Human decision service
APPROVAL_REQUESTED: Final = "human.approval_requested"
APPROVAL_GRANTED: Final = "human.approval_granted"
APPROVAL_DENIED: Final = "human.approval_denied"
APPROVAL_EXPIRED: Final = "human.approval_expired"

# Knowledge plane
RETRIEVAL_PERFORMED: Final = "retrieval.performed"
MEMORY_READ: Final = "memory.read"
MEMORY_WRITTEN: Final = "memory.written"

# Outcome
OUTCOME_RECORDED: Final = "outcome.recorded"
