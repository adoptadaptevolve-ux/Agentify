"""Just-in-time credential minting.

Agents never receive long-lived secrets. A capability handler is handed a
``CredentialGrant`` as an argument, and it is minted only after the policy
decision is ``ALLOW`` and every obligation is discharged -- so the window in
which usable credentials exist is the window in which the action is authorized,
and not a moment longer.

The grant carries a reference, never the secret itself, which keeps credential
material out of checkpoints, evidence and stack traces.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable

from ..contracts.action import ActionIntent, CredentialGrant
from ..contracts.policy import PolicyDecision
from ..ids import new_id
from ..registry.capability import Capability

DEFAULT_GRANT_LIFETIME = timedelta(minutes=2)
"""Credentials expire in the order of the action, not the session."""


@runtime_checkable
class CredentialBroker(Protocol):
    """Mints scoped, short-lived credentials for an authorized action."""

    def mint(
        self,
        *,
        intent: ActionIntent,
        capability: Capability,
        decision: PolicyDecision,
        audience: str,
    ) -> CredentialGrant:
        """Mint a grant for exactly this action."""
        ...


class LocalCredentialBroker:
    """Reference broker for the sandbox: issues references, holds no secrets."""

    def __init__(self, *, lifetime: timedelta = DEFAULT_GRANT_LIFETIME) -> None:
        self._lifetime = lifetime

    def mint(
        self,
        *,
        intent: ActionIntent,
        capability: Capability,
        decision: PolicyDecision,
        audience: str,
    ) -> CredentialGrant:
        """Mint a grant scoped to this capability and this resource."""
        if not decision.allows_execution:
            # Belt and braces: a broker that mints on a non-allow decision
            # would make every other check in the guard advisory.
            raise PermissionError("credentials are only minted after a policy allow")
        return CredentialGrant(
            grant_id=new_id("grant"),
            audience=audience,
            scope=frozenset({f"{capability.id}:invoke"}),
            resource=intent.resource,
            expires_at=datetime.now(UTC) + self._lifetime,
            secret_ref=f"vault://sandbox/{capability.id}/{intent.action_id}",
        )
