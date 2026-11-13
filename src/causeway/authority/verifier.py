"""Verifying a presented delegation envelope.

Every check here fails closed. A missing key, an unparseable envelope, a clock
that cannot be trusted and a genuinely expired envelope all produce a refusal,
never a downgraded allow. Nonce consumption is the replay defence: an envelope
minted for a single consequential action is spendable exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..contracts.delegation import SignedDelegation
from ..errors import (
    AudienceMismatch,
    DelegationExpired,
    ReplayDetected,
    SignatureInvalid,
)
from .signer import Signer

CLOCK_LEEWAY = timedelta(seconds=30)
"""Tolerance for clock skew between the minting and verifying services. Kept
small: a large leeway extends the life of a revoked or spent envelope."""


class NonceStore:
    """Records consumed nonces so an envelope cannot be replayed.

    In-memory reference implementation. A production deployment backs this with
    a store that is shared across replicas and that survives a restart, because
    a nonce store that forgets is a replay window.
    """

    def __init__(self) -> None:
        self._seen: dict[str, datetime] = {}

    def consume(self, nonce: str, *, expires_at: datetime) -> None:
        """Mark ``nonce`` as spent, or raise if it already was."""
        if nonce in self._seen:
            raise ReplayDetected(f"delegation nonce {nonce[:8]}... has already been spent")
        self._seen[nonce] = expires_at

    def purge(self, *, now: datetime | None = None) -> int:
        """Drop nonces whose envelopes have expired; returns how many went."""
        moment = now or datetime.now(UTC)
        stale = [nonce for nonce, expiry in self._seen.items() if expiry <= moment]
        for nonce in stale:
            del self._seen[nonce]
        return len(stale)


class DelegationVerifier:
    """Checks a presented envelope before any authorization is derived from it."""

    def __init__(
        self,
        signer: Signer,
        *,
        nonce_store: NonceStore | None = None,
        leeway: timedelta = CLOCK_LEEWAY,
    ) -> None:
        self._signer = signer
        self._nonces = nonce_store or NonceStore()
        self._leeway = leeway

    def verify(
        self,
        presented: SignedDelegation,
        *,
        audience: str,
        required_capability: str | None = None,
        consume_nonce: bool = False,
        now: datetime | None = None,
    ) -> None:
        """Validate ``presented``, raising the specific reason it is not usable.

        Order matters: the signature is checked first, because every other
        field is only meaningful once the envelope is known to be authentic.
        """
        moment = now or datetime.now(UTC)
        body = presented.body

        if not self._signer.verify(body, presented.signature, presented.key_id):
            raise SignatureInvalid(
                f"delegation {body.delegation_id} failed signature verification"
            )
        if not body.is_live(now=moment, leeway=self._leeway):
            raise DelegationExpired(
                f"delegation {body.delegation_id} is outside its validity window"
            )
        if body.audience != audience:
            raise AudienceMismatch(
                f"delegation {body.delegation_id} was minted for {body.audience!r}, "
                f"presented to {audience!r}"
            )
        if required_capability is not None and required_capability not in body.capabilities:
            # Not an AttenuationViolation: nothing was widened, the envelope
            # simply does not carry this capability. Policy will deny.
            raise SignatureInvalid(
                f"delegation {body.delegation_id} does not carry capability "
                f"{required_capability!r}"
            )
        if consume_nonce:
            self._nonces.consume(body.nonce, expires_at=body.expires_at)
