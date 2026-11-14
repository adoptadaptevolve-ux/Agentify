"""Loading policy with its provenance, and refusing unsigned policy when told to.

Signing a pack proves nothing unless something checks the signature before the
pack is used. This module is that check, and it records the answer:

* :func:`load_policy` loads one or more pack files, verifies any signature
  that is present, composes them, and returns a :class:`LoadedPolicy` that says
  for every source file whether it was verified and by which key.
* With ``require_signed=True`` it refuses -- before anything is composed or
  run -- any pack that is unsigned, signed with an unknown key or algorithm, or
  edited after signing.
* A signature that is present but does not verify is a refusal even when
  signatures are not required. A bad signature is evidence of tampering, and
  "signatures were optional" is not a reason to run a tampered pack.

Strict mode can be imposed from outside the code with the environment variable
``CAUSEWAY_REQUIRE_SIGNED_POLICY=1``. When it is set, no code path can turn it
off: ``load_policy`` and ``Platform.local`` both refuse unverified policy
whatever their arguments say, so a deployment can make signing mandatory
without trusting every caller to ask for it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import ConfigurationError, SignatureInvalid
from .pack import SIGNATURE_SUFFIX, PackSignature, PolicyPack, compose, load_pack, verify_pack

if TYPE_CHECKING:  # pragma: no cover
    from ..authority.signer import SignatureVerifier

REQUIRE_SIGNED_ENV = "CAUSEWAY_REQUIRE_SIGNED_POLICY"


def signed_policy_required() -> bool:
    """Whether the environment makes verified policy mandatory."""
    return os.environ.get(REQUIRE_SIGNED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class PackSource:
    """Where one pack came from and what is known about its signature."""

    path: str
    name: str
    version: str
    digest: str
    verified: bool
    key_id: str | None = None
    algorithm: str | None = None

    def describe(self) -> str:
        """One line for a terminal or a log."""
        status = (
            f"signed by {self.key_id} ({self.algorithm}), verified"
            if self.verified
            else "UNSIGNED or unverified"
        )
        return f"{self.name}@{self.version}  {status}  [{self.path}]"


@dataclass(frozen=True, slots=True)
class LoadedPolicy:
    """A composed pack plus the provenance of every file it was built from."""

    pack: PolicyPack
    sources: tuple[PackSource, ...]

    @property
    def fully_verified(self) -> bool:
        """Whether every source pack was verified against a trusted key."""
        return bool(self.sources) and all(source.verified for source in self.sources)


def load_policy(
    paths: Iterable[str | Path],
    *,
    verifier: SignatureVerifier | None = None,
    require_signed: bool = False,
    name: str = "composed",
    version: str = "loaded",
) -> LoadedPolicy:
    """Load, check and compose policy packs, recording each one's provenance.

    Strict when ``require_signed`` is true or the environment demands it: then
    a verifier is mandatory, and every pack must carry a signature file
    (``<pack>.sig.json``) that verifies against it.
    """
    strict = require_signed or signed_policy_required()
    files = [Path(path) for path in paths]
    if not files:
        raise ConfigurationError("no policy pack files given")
    if strict and verifier is None:
        raise ConfigurationError(
            "signed policy is required but no verification key was provided"
        )

    packs: list[PolicyPack] = []
    sources: list[PackSource] = []
    for file in files:
        pack = load_pack(file)
        signature_file = file.with_name(file.name + SIGNATURE_SUFFIX)
        source = PackSource(
            path=str(file),
            name=pack.name,
            version=pack.version,
            digest=pack.artifact_digest,
            verified=False,
        )
        if signature_file.exists() and verifier is not None:
            signature = PackSignature.from_json(signature_file.read_text(encoding="utf-8"))
            verify_pack(pack, signature, verifier)  # raises on any mismatch
            source = PackSource(
                path=str(file),
                name=pack.name,
                version=pack.version,
                digest=pack.artifact_digest,
                verified=True,
                key_id=signature.key_id,
                algorithm=signature.algorithm,
            )
        elif strict:
            raise SignatureInvalid(
                f"signed policy is required and {file} has no signature "
                f"(expected {signature_file})"
            )
        packs.append(pack)
        sources.append(source)

    composed = packs[0] if len(packs) == 1 else compose(*packs, name=name, version=version)
    return LoadedPolicy(pack=composed, sources=tuple(sources))


def enforce_policy_trust(
    sources: tuple[PackSource, ...], *, require_signed: bool = False
) -> None:
    """Refuse to proceed with unverified policy when signing is mandatory.

    Called by ``Platform.local`` so the rule holds even for code that builds a
    pack in memory and never goes through :func:`load_policy`.
    """
    if not (require_signed or signed_policy_required()):
        return
    if not sources:
        raise SignatureInvalid(
            "signed policy is required, but this policy has no verified provenance; "
            "load it with causeway.policy.load_policy and pass policy_sources"
        )
    unverified = [source.describe() for source in sources if not source.verified]
    if unverified:
        raise SignatureInvalid(
            "signed policy is required, but these packs are not verified: "
            + "; ".join(unverified)
        )
