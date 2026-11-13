"""Canonical platform contracts.

Teams are free to choose an approved orchestration implementation; they are not
free to invent new trust contracts. This package holds the stable seams: the
types that cross the proposal/execution boundary, and the protocols that every
platform service implements.

Everything here is data or a ``Protocol``. Nothing in this package performs
I/O, calls a model, or executes a side effect, which is what lets a team read
the whole trust model in one sitting.
"""

from .action import ActionIntent, CredentialGrant, ExecutionReceipt, ResultClass
from .delegation import Constraints, Delegation, SignedDelegation
from .evidence import EventDomain, EvidenceEvent
from .human import ApprovalItem, DecisionOutcome, EvidencePacket, SignedDecision
from .identity import AuthStrength, DataClass, Principal, PrincipalKind, WorkloadIdentity
from .judgment import (
    Answer,
    Choice,
    ChoiceAnswer,
    JudgmentResult,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
    State,
    SystemOne,
    Usage,
)
from .policy import DecisionTier, Effect, Obligation, PolicyDecision
from .risk import RiskTier

__all__ = [
    "ActionIntent",
    "Answer",
    "ApprovalItem",
    "AuthStrength",
    "Choice",
    "ChoiceAnswer",
    "Constraints",
    "CredentialGrant",
    "DataClass",
    "DecisionOutcome",
    "DecisionTier",
    "Delegation",
    "Effect",
    "EventDomain",
    "EvidenceEvent",
    "EvidencePacket",
    "ExecutionReceipt",
    "JudgmentResult",
    "Noul",
    "NoulAnswer",
    "Obligation",
    "PolicyDecision",
    "Principal",
    "PrincipalKind",
    "Question",
    "ResultClass",
    "RiskTier",
    "Score",
    "ScoreAnswer",
    "SignedDecision",
    "SignedDelegation",
    "State",
    "SystemOne",
    "Usage",
    "WorkloadIdentity",
]
