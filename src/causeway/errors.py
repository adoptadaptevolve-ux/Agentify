"""The platform's typed failure modes.

Two rules govern this module, and both come from the north-star invariant that
authorization uncertainty must fail closed:

1. Every refusal is a distinct type carrying a machine-readable ``reason_code``
   that appears verbatim in evidence. "Denied" and "we could not tell" are not
   the same outcome to an auditor, so they are not the same exception here.
2. Nothing in this module inherits from a success path. There is no exception
   a caller can swallow to obtain an allow; the only way to execute a side
   effect is for the transaction guard to return a receipt.
"""

from __future__ import annotations


class CausewayError(Exception):
    """Base class for every platform failure."""

    reason_code: str = "platform_error"


class ConfigurationError(CausewayError):
    """The platform is misconfigured; refuse rather than guess."""

    reason_code = "configuration_error"


class ContractViolation(CausewayError):
    """A caller broke a canonical contract (bad shape, missing field)."""

    reason_code = "contract_violation"


class AuthorityError(CausewayError):
    """Base class for anything wrong with identity, delegation or approval."""

    reason_code = "authority_error"


class SignatureInvalid(AuthorityError):
    """A delegation envelope's signature did not verify."""

    reason_code = "signature_invalid"


class DelegationExpired(AuthorityError):
    """A delegation envelope is outside its validity window."""

    reason_code = "delegation_expired"


class AttenuationViolation(AuthorityError):
    """A child delegation asked for rights its parent did not hold.

    Delegation may only narrow. This is raised at mint time, so an envelope
    that widens authority never exists to be presented.
    """

    reason_code = "attenuation_violation"


class AudienceMismatch(AuthorityError):
    """An envelope was presented to a service it was not minted for."""

    reason_code = "audience_mismatch"


class ReplayDetected(AuthorityError):
    """A nonce or approval was presented a second time."""

    reason_code = "replay_detected"


class DigestMismatch(AuthorityError):
    """An approval or policy decision does not bind to the action presented.

    This is the defence against approving one payload and executing another.
    """

    reason_code = "digest_mismatch"


class PolicyDenied(CausewayError):
    """A policy decision point denied the action."""

    reason_code = "policy_denied"

    def __init__(self, message: str, *, reason_code: str | None = None) -> None:
        super().__init__(message)
        if reason_code:
            self.reason_code = reason_code


class ApprovalRequired(CausewayError):
    """The action needs a human decision that has not been made yet.

    This is not a failure; it is the workflow's cue to checkpoint and pause.
    It is an exception so that it cannot be mistaken for a receipt.
    """

    reason_code = "approval_required"

    def __init__(self, message: str, *, approval_id: str, action_digest: str) -> None:
        super().__init__(message)
        self.approval_id = approval_id
        self.action_digest = action_digest


class BudgetExhausted(CausewayError):
    """A run exceeded a declared budget (steps, spend, tool calls, time)."""

    reason_code = "budget_exhausted"


class CapabilityNotRegistered(CausewayError):
    """The requested capability is not in the registry, so it cannot execute."""

    reason_code = "capability_not_registered"


class ValidationFailed(CausewayError):
    """Typed intent failed the capability's schema or semantic validators."""

    reason_code = "validation_failed"


class VerificationFailed(CausewayError):
    """A side effect could not be verified after execution.

    The action may or may not have happened; the run is marked for
    reconciliation rather than retried blindly.
    """

    reason_code = "verification_failed"


class JudgmentUnavailable(CausewayError):
    """The judgment plane could not answer, so the caller must fail closed."""

    reason_code = "judgment_unavailable"


class EvidenceChainBroken(CausewayError):
    """The evidence ledger failed its integrity check."""

    reason_code = "evidence_chain_broken"
