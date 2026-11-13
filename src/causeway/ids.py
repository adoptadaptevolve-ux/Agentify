"""Identifiers and nonces.

Every run, action, delegation and evidence record carries a sortable,
prefixed identifier. The prefix makes identifiers self-describing in logs and
forensic exports ("act_..." is an action, "run_..." is a run), and the
time-ordered prefix means a lexical sort of identifiers is a chronological
sort, which matters when reconstructing a run from scattered evidence.

Identifiers are *not* secrets and are not authority. Possession of an action
identifier grants nothing; authority lives in the signed delegation envelope.
"""

from __future__ import annotations

import secrets
import time
from typing import Final

_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I/L/O/U
_RANDOM_CHARS: Final = 16


def _encode(number: int, width: int) -> str:
    chars = []
    for _ in range(width):
        number, remainder = divmod(number, len(_ALPHABET))
        chars.append(_ALPHABET[remainder])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Mint a time-sortable identifier with the given prefix."""
    timestamp = _encode(time.time_ns() // 1_000_000, 10)
    randomness = "".join(secrets.choice(_ALPHABET) for _ in range(_RANDOM_CHARS))
    return f"{prefix}_{timestamp}{randomness}"


def new_run_id() -> str:
    """Identifier for one workflow run."""
    return new_id("run")


def new_action_id() -> str:
    """Identifier for one attempted side effect."""
    return new_id("act")


def new_delegation_id() -> str:
    """Identifier for one delegation envelope."""
    return new_id("dlg")


def new_approval_id() -> str:
    """Identifier for one human decision item."""
    return new_id("apr")


def new_case_id() -> str:
    """Identifier for one supervisor-board deliberation."""
    return new_id("case")


def new_nonce() -> str:
    """Mint a single-use nonce for replay defence."""
    return secrets.token_urlsafe(24)


def new_idempotency_key() -> str:
    """Mint an idempotency key for a mutation the caller has not keyed itself."""
    return new_id("idem")
