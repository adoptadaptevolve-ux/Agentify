"""Canonical encoding and digests.

Everything the platform signs, approves, caches or audits is identified by the
digest of its *canonical* encoding, never by the bytes that happened to arrive
on the wire. Two structurally identical payloads must produce the same digest
whatever order their keys arrived in, or an attacker could re-order a dict and
present an approved action as a new one (and a reviewer could approve one
rendering of an action while a different rendering executes).

The rules, which the whole platform depends on:

* objects are encoded with sorted keys and no insignificant whitespace;
* text is NFC-normalised, so visually identical strings do not produce
  different digests depending on how the client composed them;
* ``Decimal`` is encoded as a string, never a float, because money must not
  round-trip through binary floating point;
* ``datetime`` is encoded as a UTC RFC 3339 string with a ``Z`` suffix;
* sets are encoded as sorted arrays;
* floats that are not finite are rejected rather than encoded as ``NaN``.

``digest()`` returns a prefixed, self-describing string ("sha256:...") so that
stored evidence records which algorithm produced them and the platform can
migrate without ambiguity.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

DIGEST_ALGORITHM: Final = "sha256"
_DIGEST_PREFIX: Final = f"{DIGEST_ALGORITHM}:"


class CanonicalEncodingError(TypeError):
    """A value cannot be canonically encoded, so it must not be signed."""


def _normalise(value: Any) -> Any:
    """Reduce ``value`` to the JSON subset the canonical encoder accepts."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalEncodingError(f"non-finite float cannot be canonicalised: {value!r}")
        # Floats are accepted but discouraged: use Decimal for anything that is
        # compared, summed or bounded by policy.
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalEncodingError(f"non-finite Decimal cannot be canonicalised: {value!r}")
        return str(value)
    if isinstance(value, datetime):
        return to_rfc3339(value)
    if isinstance(value, Enum):
        return _normalise(value.value)
    if isinstance(value, bytes):
        return value.hex()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _normalise(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalEncodingError(f"object keys must be strings, got {type(key)!r}")
            out[unicodedata.normalize("NFC", key)] = _normalise(item)
        return dict(sorted(out.items()))
    if isinstance(value, Set):
        # Sets have no inherent order, so sort their canonical renderings.
        return sorted((_normalise(item) for item in value), key=_sort_key)
    if isinstance(value, Sequence):
        return [_normalise(item) for item in value]
    raise CanonicalEncodingError(f"cannot canonicalise {type(value)!r}")


def _sort_key(value: Any) -> str:
    """Stable ordering key for set members of mixed type."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def to_rfc3339(moment: datetime) -> str:
    """Render ``moment`` as a UTC RFC 3339 timestamp with a ``Z`` suffix."""
    if moment.tzinfo is None:
        raise CanonicalEncodingError("naive datetimes are ambiguous; attach a timezone")
    return moment.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text for ``value``."""
    return json.dumps(
        _normalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Return the canonical encoding of ``value`` as UTF-8 bytes."""
    return canonical_json(value).encode("utf-8")


def digest(value: Any) -> str:
    """Return the self-describing digest of ``value``'s canonical encoding."""
    return _DIGEST_PREFIX + hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_of_bytes(payload: bytes) -> str:
    """Return the self-describing digest of raw ``payload`` bytes."""
    return _DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


def short(value: str, length: int = 12) -> str:
    """Shorten a digest for human-readable logs and CLI output.

    Never use the result for comparison; it is display only.
    """
    body = value.removeprefix(_DIGEST_PREFIX)
    return body[:length]
