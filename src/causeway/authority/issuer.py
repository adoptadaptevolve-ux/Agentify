"""Minting delegation envelopes.

Envelopes are minted here and nowhere else. Two properties follow from that:

* the agent process never holds a signing key, so a compromised agent cannot
  forge authority even if it can call every function in this package; and
* every child envelope goes through ``Delegation.attenuate``, which refuses to
  widen, so the attenuation invariant is enforced by construction rather than
  by a reviewer noticing.

``root()`` is the only way to create authority from nothing, and it takes an
already-authenticated ``Principal``. It never takes a claim, a role name or a
user identifier out of model output.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..canonical import digest
from ..contracts.delegation import Constraints, Delegation, SignedDelegation
from ..contracts.identity import Principal, WorkloadIdentity
from ..ids import new_delegation_id, new_nonce
from .signer import Signer

DEFAULT_LIFETIME = timedelta(minutes=10)
"""Envelopes are short-lived by default: authority should expire in the order
of the task, not the session."""


class DelegationIssuer:
    """Mints root and attenuated delegation envelopes."""

    def __init__(self, signer: Signer, *, default_lifetime: timedelta = DEFAULT_LIFETIME) -> None:
        self._signer = signer
        self._default_lifetime = default_lifetime

    def root(
        self,
        *,
        principal: Principal,
        workload: WorkloadIdentity,
        run_id: str,
        domain: str,
        capabilities: frozenset[str],
        purpose: str,
        audience: str,
        policy_version: str,
        constraints: Constraints | None = None,
        lifetime: timedelta | None = None,
        agent_version: str = "unknown",
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        now: datetime | None = None,
    ) -> SignedDelegation:
        """Mint the root envelope for a run, from an authenticated principal."""
        moment = now or datetime.now(UTC)
        body = Delegation(
            delegation_id=new_delegation_id(),
            principal=principal,
            workload=workload,
            tenant=principal.tenant,
            domain=domain,
            run_id=run_id,
            capabilities=capabilities,
            purpose=purpose,
            constraints=constraints or Constraints(),
            audience=audience,
            nonce=new_nonce(),
            issued_at=moment,
            expires_at=moment + (lifetime or self._default_lifetime),
            policy_context_hash=digest({"policy_version": policy_version}),
            agent_version=agent_version,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            parent_hash=None,
            depth=0,
        )
        return self._sign(body)

    def attenuate(
        self,
        parent: SignedDelegation,
        *,
        capabilities: frozenset[str] | None = None,
        constraints: Constraints | None = None,
        purpose: str | None = None,
        audience: str | None = None,
        lifetime: timedelta | None = None,
        workload: WorkloadIdentity | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> SignedDelegation:
        """Derive and sign a strictly narrower child envelope.

        Used both for spawning a subagent and for narrowing authority down to a
        single action before it reaches the transaction guard. Raises
        ``AttenuationViolation`` on any attempt to widen.
        """
        moment = now or datetime.now(UTC)
        expires_at = None
        if lifetime is not None:
            expires_at = min(moment + lifetime, parent.body.expires_at)
        child = parent.body.attenuate(
            capabilities=capabilities,
            constraints=constraints,
            purpose=purpose,
            audience=audience,
            expires_at=expires_at,
            delegation_id=new_delegation_id(),
            nonce=new_nonce(),
            now=moment,
            workload=workload,
            idempotency_key=idempotency_key,
        )
        return self._sign(child)

    def _sign(self, body: Delegation) -> SignedDelegation:
        signature, key_id = self._signer.sign(body)
        return SignedDelegation(
            body=body,
            signature=signature,
            key_id=key_id,
            algorithm=self._signer.algorithm,
        )
