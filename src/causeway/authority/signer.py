"""Signing keys for delegation envelopes.

The reference implementation uses HMAC-SHA256 with an in-process keyring,
which is right for a prototype and for the local sandbox and wrong for
production: a symmetric key means any service that can verify an envelope can
also mint one. Production deployments use :mod:`causeway.authority.ed25519`
(or any ``Signer`` backed by an HSM, KMS or workload-identity signing service)
so the minting authority is a separate trust domain from the verifying
services, which hold only a :class:`SignatureVerifier`.

The ``key_id`` travels with every signature so keys can rotate without
invalidating envelopes that are still inside their validity window.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Protocol, runtime_checkable

from ..canonical import canonical_bytes
from ..contracts.delegation import Delegation
from ..errors import ConfigurationError


@runtime_checkable
class SignatureVerifier(Protocol):
    """Checks signatures over delegation bodies. Cannot mint them.

    This is all a verifying service should hold. With an asymmetric algorithm
    it is built from public keys only, so compromising a verifier yields
    nothing that can issue authority.
    """

    @property
    def algorithm(self) -> str:
        """The only algorithm this verifier accepts."""
        ...

    def verify(self, body: Delegation, signature: str, key_id: str) -> bool:
        """Whether ``signature`` is valid for ``body`` under ``key_id``."""
        ...


@runtime_checkable
class Signer(SignatureVerifier, Protocol):
    """Mints signatures over delegation bodies, and can check its own."""

    @property
    def active_key_id(self) -> str:
        """The key new signatures are minted with."""
        ...

    def sign(self, body: Delegation) -> tuple[str, str]:
        """Return ``(signature, key_id)`` for ``body``."""
        ...


class HmacSigner:
    """HMAC-SHA256 signer over the canonical encoding of a delegation body.

    Suitable for the local sandbox, tests and single-trust-domain prototypes.
    Signing and verifying use the same secret, so this one object is both the
    ``Signer`` and the ``SignatureVerifier`` -- which is exactly why it is not
    for production.
    """

    algorithm = "HS256"

    def __init__(
        self, keys: dict[str, bytes] | None = None, active_key_id: str = "local-1"
    ) -> None:
        self._keys: dict[str, bytes] = dict(keys or {active_key_id: secrets.token_bytes(32)})
        if active_key_id not in self._keys:
            raise ConfigurationError(f"active key {active_key_id!r} is not in the keyring")
        self._active_key_id = active_key_id

    @property
    def active_key_id(self) -> str:
        """The key new signatures are minted with."""
        return self._active_key_id

    def add_key(self, key_id: str, secret: bytes) -> None:
        """Add a verification key without making it the active signing key."""
        self._keys[key_id] = secret

    def rotate_to(self, key_id: str) -> None:
        """Make ``key_id`` the active signing key, keeping the old one for verify."""
        if key_id not in self._keys:
            raise ConfigurationError(f"cannot rotate to unknown key {key_id!r}")
        self._active_key_id = key_id

    def sign(self, body: Delegation) -> tuple[str, str]:
        """Return ``(signature, key_id)`` for ``body``."""
        key_id = self._active_key_id
        return self._mac(body, key_id), key_id

    def verify(self, body: Delegation, signature: str, key_id: str) -> bool:
        """Whether ``signature`` is valid for ``body`` under ``key_id``."""
        secret = self._keys.get(key_id)
        if secret is None:
            # An unknown key is a verification failure, never a pass-through.
            return False
        # Constant-time comparison: a timing oracle here would leak the MAC.
        return hmac.compare_digest(self._mac(body, key_id), signature)

    def _mac(self, body: Delegation, key_id: str) -> str:
        secret = self._keys[key_id]
        return hmac.new(secret, canonical_bytes(body), hashlib.sha256).hexdigest()
