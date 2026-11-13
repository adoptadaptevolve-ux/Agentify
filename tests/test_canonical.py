"""Canonical encoding is what every digest, signature and approval rests on."""

from __future__ import annotations

from datetime import UTC, datetime, timezone
from decimal import Decimal

import pytest

from causeway.canonical import CanonicalEncodingError, canonical_json, digest


def test_key_order_does_not_change_the_digest() -> None:
    """Two spellings of the same object are the same action."""
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_set_order_does_not_change_the_digest() -> None:
    """Membership is what a set means, so its ordering must not matter."""
    assert digest({"x": {"a", "b"}}) == digest({"x": {"b", "a"}})


def test_decimals_are_encoded_as_strings() -> None:
    """Money must not round-trip through binary floating point."""
    assert canonical_json({"amount": Decimal("10.10")}) == '{"amount":"10.10"}'


def test_unicode_is_normalised() -> None:
    """Visually identical strings must produce identical digests."""
    composed = "café"  # e with acute
    decomposed = "café"  # e + combining acute
    assert composed != decomposed
    assert digest({"name": composed}) == digest({"name": decomposed})


def test_naive_datetimes_are_refused() -> None:
    """An ambiguous timestamp must not be signable."""
    with pytest.raises(CanonicalEncodingError, match="naive datetimes"):
        canonical_json({"at": datetime(2026, 1, 1)})


def test_timezones_are_normalised_to_utc() -> None:
    """The same instant in two zones is the same instant."""
    from datetime import timedelta

    utc = datetime(2026, 1, 1, 12, tzinfo=UTC)
    elsewhere = datetime(2026, 1, 1, 14, tzinfo=timezone(timedelta(hours=2)))
    assert digest({"at": utc}) == digest({"at": elsewhere})


def test_non_finite_floats_are_refused() -> None:
    """NaN has no canonical encoding and no business in a signed payload."""
    with pytest.raises(CanonicalEncodingError, match="non-finite"):
        canonical_json({"x": float("nan")})


def test_digests_are_self_describing() -> None:
    """Stored evidence records which algorithm produced it."""
    assert digest({"a": 1}).startswith("sha256:")
