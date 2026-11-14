"""Ed25519 signing: the minting key and the checking key are different keys.

With HMAC every service that can check an envelope holds the secret that makes
one, so a break-in at any verifier is a break-in at the issuer. Ed25519 splits
them. The issuing service holds a private key and signs; every verifying
service holds only public keys, which can confirm a signature but cannot create
one.

Three classes:

* :class:`Ed25519Signer` -- private keys held in this process. Right when the
  issuer can keep a key in a secret store.
* :class:`ExternalEd25519Signer` -- the private key lives somewhere else (a
  cloud KMS, an HSM) and this process asks it to sign. Wiring a vault in is one
  function: ``sign_bytes(message) -> signature``.
* :class:`Ed25519Verifier` -- public keys only. This is what verifying services
  are given; it has no way to sign.

Needs the optional ``signing`` extra (``pip install 'causeway[signing]'``),
which pulls in the ``cryptography`` package. The core platform stays
dependency-free; see ``docs/decisions/0009-asymmetric-signing-as-an-optional-extra.md``.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from ..canonical import canonical_bytes
from ..contracts.delegation import Delegation
from ..errors import ConfigurationError

if TYPE_CHECKING:  # pragma: no cover
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

ALGORITHM = "EdDSA"
"""The JOSE name for Ed25519 signatures, recorded on every envelope."""

SIGNATURE_BYTES = 64


def _crypto() -> Any:
    """Import the ``cryptography`` primitives, or explain how to get them."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ConfigurationError(
            "Ed25519 signing needs the 'signing' extra: pip install 'causeway[signing]'"
        ) from exc
    return ed25519, serialization


def _encode(signature: bytes) -> str:
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")


def _decode(signature: str) -> bytes | None:
    try:
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == SIGNATURE_BYTES else None


class Ed25519Verifier:
    """Checks Ed25519 signatures. Holds public keys only, so it cannot mint."""

    algorithm = ALGORITHM

    def __init__(self, public_keys: Mapping[str, Ed25519PublicKey]) -> None:
        if not public_keys:
            raise ConfigurationError("an Ed25519 verifier needs at least one public key")
        self._keys: dict[str, Ed25519PublicKey] = dict(public_keys)

    @classmethod
    def from_public_pem(cls, keys: Mapping[str, bytes]) -> Ed25519Verifier:
        """Build a verifier from PEM-encoded public keys, keyed by key id."""
        ed25519, serialization = _crypto()
        loaded: dict[str, Ed25519PublicKey] = {}
        for key_id, pem in keys.items():
            key = serialization.load_pem_public_key(pem)
            if not isinstance(key, ed25519.Ed25519PublicKey):
                raise ConfigurationError(f"key {key_id!r} is not an Ed25519 public key")
            loaded[key_id] = key
        return cls(loaded)

    @property
    def key_ids(self) -> frozenset[str]:
        """The keys this verifier trusts."""
        return frozenset(self._keys)

    def add_key(self, key_id: str, public_key: Ed25519PublicKey) -> None:
        """Trust another public key, e.g. ahead of a rotation."""
        self._keys[key_id] = public_key

    def verify(self, body: Delegation, signature: str, key_id: str) -> bool:
        """Whether ``signature`` is valid for ``body`` under ``key_id``.

        Every failure -- unknown key, malformed or wrong-length signature, bad
        signature -- is ``False``. Nothing here can raise its way past a check.
        """
        key = self._keys.get(key_id)
        raw = _decode(signature)
        if key is None or raw is None:
            return False
        try:
            key.verify(raw, canonical_bytes(body))
        except Exception:
            return False
        return True


class Ed25519Signer:
    """Signs with Ed25519 private keys held in this process.

    Supports the same rotation model as ``HmacSigner``: new envelopes use the
    active key, older keys stay trusted until their envelopes have expired.
    """

    algorithm = ALGORITHM

    def __init__(
        self, keys: Mapping[str, Ed25519PrivateKey], active_key_id: str
    ) -> None:
        if active_key_id not in keys:
            raise ConfigurationError(f"active key {active_key_id!r} is not in the keyring")
        self._keys: dict[str, Ed25519PrivateKey] = dict(keys)
        self._active_key_id = active_key_id

    @classmethod
    def generate(cls, key_id: str = "ed25519-1") -> Ed25519Signer:
        """A signer with one freshly generated key."""
        ed25519, _ = _crypto()
        return cls({key_id: ed25519.Ed25519PrivateKey.generate()}, active_key_id=key_id)

    @classmethod
    def from_private_pem(
        cls, keys: Mapping[str, bytes], active_key_id: str, *, password: bytes | None = None
    ) -> Ed25519Signer:
        """Load PEM-encoded private keys, keyed by key id."""
        ed25519, serialization = _crypto()
        loaded: dict[str, Ed25519PrivateKey] = {}
        for key_id, pem in keys.items():
            key = serialization.load_pem_private_key(pem, password=password)
            if not isinstance(key, ed25519.Ed25519PrivateKey):
                raise ConfigurationError(f"key {key_id!r} is not an Ed25519 private key")
            loaded[key_id] = key
        return cls(loaded, active_key_id=active_key_id)

    @property
    def active_key_id(self) -> str:
        """The key new signatures are minted with."""
        return self._active_key_id

    def add_key(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        """Add a key without making it the active signing key."""
        self._keys[key_id] = private_key

    def rotate_to(self, key_id: str) -> None:
        """Make ``key_id`` the active signing key, keeping the old one for verify."""
        if key_id not in self._keys:
            raise ConfigurationError(f"cannot rotate to unknown key {key_id!r}")
        self._active_key_id = key_id

    def sign(self, body: Delegation) -> tuple[str, str]:
        """Return ``(signature, key_id)`` for ``body``."""
        key_id = self._active_key_id
        return _encode(self._keys[key_id].sign(canonical_bytes(body))), key_id

    def verify(self, body: Delegation, signature: str, key_id: str) -> bool:
        """Whether ``signature`` is valid for ``body`` under ``key_id``."""
        return self.verifier().verify(body, signature, key_id)

    def verifier(self) -> Ed25519Verifier:
        """The public half: what to give every service that checks envelopes."""
        return Ed25519Verifier({k: key.public_key() for k, key in self._keys.items()})

    def public_pem(self, key_id: str | None = None) -> bytes:
        """The PEM-encoded public key for ``key_id`` (default: the active key)."""
        _, serialization = _crypto()
        key = self._keys[key_id or self._active_key_id].public_key()
        pem: bytes = key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem

    def private_pem(self, key_id: str | None = None) -> bytes:
        """The PEM-encoded private key, unencrypted. Store it in a secret store."""
        _, serialization = _crypto()
        pem: bytes = self._keys[key_id or self._active_key_id].private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return pem


class ExternalEd25519Signer:
    """Signs by asking something else -- a cloud KMS, an HSM -- that holds the key.

    The private key never enters this process. ``sign_bytes`` receives the exact
    bytes to sign and returns the raw 64-byte Ed25519 signature; for a cloud
    vault it is a few lines calling that vault's sign API. ``public_pem`` is the
    vault's exported public key, used to build the verifier every checking
    service holds.
    """

    algorithm = ALGORITHM

    def __init__(
        self, *, key_id: str, sign_bytes: Callable[[bytes], bytes], public_pem: bytes
    ) -> None:
        self._key_id = key_id
        self._sign_bytes = sign_bytes
        self._verifier = Ed25519Verifier.from_public_pem({key_id: public_pem})

    @property
    def active_key_id(self) -> str:
        """The vault key every signature is minted with."""
        return self._key_id

    def sign(self, body: Delegation) -> tuple[str, str]:
        """Have the external key sign ``body``, and check the result before use."""
        signature = _encode(self._sign_bytes(canonical_bytes(body)))
        if not self._verifier.verify(body, signature, self._key_id):
            # A vault returning a signature its own public key rejects is a
            # misconfiguration; minting an unverifiable envelope would only
            # move the failure somewhere harder to diagnose.
            raise ConfigurationError(
                f"external key {self._key_id!r} returned a signature its public key rejects"
            )
        return signature, self._key_id

    def verify(self, body: Delegation, signature: str, key_id: str) -> bool:
        """Whether ``signature`` is valid for ``body`` under ``key_id``."""
        return self._verifier.verify(body, signature, key_id)

    def verifier(self) -> Ed25519Verifier:
        """The public half, for verifying services."""
        return self._verifier
