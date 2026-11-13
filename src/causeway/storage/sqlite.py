"""SQLite-backed stores for approvals, idempotency keys and delegation nonces.

Each store keeps the exact semantics of its in-memory reference, and adds two
properties the reference cannot have:

* **Durability.** State is committed before a call returns, with
  ``synchronous=FULL``, so a crash or restart loses nothing that was
  acknowledged.
* **Atomic check-and-set.** Every read-then-write runs inside one
  ``BEGIN IMMEDIATE`` transaction, so two threads or two processes sharing the
  file cannot both reserve the same idempotency key or both spend the same
  nonce. Uniqueness is also enforced by the schema, so a race that slipped past
  the application check would still fail closed.

Values are stored as JSON written by this module, not pickled: the file stays
readable by an auditor, and loading it cannot execute code.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from os import PathLike
from typing import Any

from ..authority.verifier import NonceStore
from ..canonical import to_rfc3339
from ..contracts.action import ExecutionReceipt, ResultClass
from ..contracts.human import (
    ApprovalItem,
    DecisionOutcome,
    EvidencePacket,
    SignedDecision,
)
from ..contracts.risk import RiskTier
from ..errors import ContractViolation, ReplayDetected
from ..gateway.idempotency import DEFAULT_RETENTION, IdempotencyStore
from ..human.decisions import ApprovalRecord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS causeway_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nonces (
    nonce          TEXT PRIMARY KEY,
    expires_at     TEXT NOT NULL,
    expires_epoch  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    key             TEXT PRIMARY KEY,
    action_digest   TEXT NOT NULL,
    reserved_at     TEXT NOT NULL,
    reserved_epoch  REAL NOT NULL,
    receipt         TEXT
);
CREATE TABLE IF NOT EXISTS approval_items (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id    TEXT NOT NULL UNIQUE,
    action_digest  TEXT NOT NULL UNIQUE,
    queue          TEXT NOT NULL,
    item           TEXT NOT NULL,
    spent          INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS approval_decisions (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id  TEXT NOT NULL REFERENCES approval_items (approval_id),
    approver_id  TEXT NOT NULL,
    decision     TEXT NOT NULL,
    UNIQUE (approval_id, approver_id)
);
"""


class StateDatabase:
    """One SQLite file shared by the durable stores.

    A single connection guarded by a lock serves every thread in the process;
    other processes coordinate through SQLite's own file locking.
    """

    def __init__(self, path: str | PathLike[str], *, busy_timeout: timedelta) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            isolation_level=None,  # explicit transactions only
            check_same_thread=False,
            timeout=busy_timeout.total_seconds(),
        )
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = FULL")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        # executescript commits implicitly, so the schema is applied outside a
        # managed transaction; every statement in it is idempotent.
        with self._lock:
            self._conn.executescript(_SCHEMA)
        try:
            self._check_version()
        except BaseException:
            self._conn.close()
            raise

    def _check_version(self) -> None:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT value FROM causeway_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO causeway_meta (key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row[0]) != SCHEMA_VERSION:
                # Refuse rather than guess: reading state written under another
                # schema could misread a spent approval as unspent.
                raise ContractViolation(
                    f"state database {self.path} has schema version {row[0]}, "
                    f"this build expects {SCHEMA_VERSION}"
                )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block as one write-locked transaction; roll back on any error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def close(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> StateDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def open_state_db(
    path: str | PathLike[str], *, busy_timeout: timedelta = timedelta(seconds=5)
) -> StateDatabase:
    """Open (creating if needed) the durable state file at ``path``.

    ``busy_timeout`` bounds how long a write waits for another process holding
    the lock. Running out raises rather than proceeding, which fails closed.
    """
    return StateDatabase(path, busy_timeout=busy_timeout)


# ---------------------------------------------------------------- encoding --


def _ts(moment: datetime) -> str:
    return to_rfc3339(moment)


def _parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _receipt_to_json(receipt: ExecutionReceipt) -> str:
    return _dumps(
        {
            "action_id": receipt.action_id,
            "action_digest": receipt.action_digest,
            "run_id": receipt.run_id,
            "capability": receipt.capability,
            "risk_tier": int(receipt.risk_tier),
            "result_class": receipt.result_class.value,
            "executed_at": _ts(receipt.executed_at),
            "policy_version": receipt.policy_version,
            "decision_id": receipt.decision_id,
            "evidence_ref": receipt.evidence_ref,
            "idempotency_key": receipt.idempotency_key,
            "approval_id": receipt.approval_id,
            "output_digest": receipt.output_digest,
            "verified": receipt.verified,
            "compensation_capability": receipt.compensation_capability,
            "notes": list(receipt.notes),
        }
    )


def _receipt_from_json(text: str) -> ExecutionReceipt:
    data = json.loads(text)
    return ExecutionReceipt(
        action_id=data["action_id"],
        action_digest=data["action_digest"],
        run_id=data["run_id"],
        capability=data["capability"],
        risk_tier=RiskTier(data["risk_tier"]),
        result_class=ResultClass(data["result_class"]),
        executed_at=_parse_ts(data["executed_at"]),
        policy_version=data["policy_version"],
        decision_id=data["decision_id"],
        evidence_ref=data["evidence_ref"],
        idempotency_key=data["idempotency_key"],
        approval_id=data["approval_id"],
        output_digest=data["output_digest"],
        verified=data["verified"],
        compensation_capability=data["compensation_capability"],
        notes=tuple(data["notes"]),
    )


def _item_to_json(item: ApprovalItem) -> str:
    packet = item.packet
    return _dumps(
        {
            "approval_id": item.approval_id,
            "action_digest": item.action_digest,
            "run_id": item.run_id,
            "tenant": item.tenant,
            "queue": item.queue,
            "requested_at": _ts(item.requested_at),
            "expires_at": _ts(item.expires_at),
            "required_approvals": item.required_approvals,
            "required_authority": sorted(item.required_authority),
            "packet": {
                "intent_summary": packet.intent_summary,
                "capability": packet.capability,
                "risk_tier": int(packet.risk_tier),
                "resource": packet.resource,
                "destination": packet.destination,
                "amount_display": packet.amount_display,
                "delegation_scope": packet.delegation_scope,
                "risk_factors": list(packet.risk_factors),
                "policy_rationale": packet.policy_rationale,
                "sources": list(packet.sources),
                "alternatives": list(packet.alternatives),
                "rollback": packet.rollback,
                "recommendation": packet.recommendation,
            },
        }
    )


def _item_from_json(text: str) -> ApprovalItem:
    data = json.loads(text)
    p = data["packet"]
    return ApprovalItem(
        approval_id=data["approval_id"],
        action_digest=data["action_digest"],
        run_id=data["run_id"],
        tenant=data["tenant"],
        queue=data["queue"],
        requested_at=_parse_ts(data["requested_at"]),
        expires_at=_parse_ts(data["expires_at"]),
        required_approvals=data["required_approvals"],
        required_authority=frozenset(data["required_authority"]),
        packet=EvidencePacket(
            intent_summary=p["intent_summary"],
            capability=p["capability"],
            risk_tier=RiskTier(p["risk_tier"]),
            resource=p["resource"],
            destination=p["destination"],
            amount_display=p["amount_display"],
            delegation_scope=p["delegation_scope"],
            risk_factors=tuple(p["risk_factors"]),
            policy_rationale=p["policy_rationale"],
            sources=tuple(p["sources"]),
            alternatives=tuple(p["alternatives"]),
            rollback=p["rollback"],
            recommendation=p["recommendation"],
        ),
    )


def _decision_to_json(decision: SignedDecision) -> str:
    return _dumps(
        {
            "approval_id": decision.approval_id,
            "action_digest": decision.action_digest,
            "outcome": decision.outcome.value,
            "approver_id": decision.approver_id,
            "approver_authority": sorted(decision.approver_authority),
            "decided_at": _ts(decision.decided_at),
            "expires_at": _ts(decision.expires_at),
            "rationale": decision.rationale,
            "signature": decision.signature,
        }
    )


def _decision_from_json(text: str) -> SignedDecision:
    data = json.loads(text)
    return SignedDecision(
        approval_id=data["approval_id"],
        action_digest=data["action_digest"],
        outcome=DecisionOutcome(data["outcome"]),
        approver_id=data["approver_id"],
        approver_authority=frozenset(data["approver_authority"]),
        decided_at=_parse_ts(data["decided_at"]),
        expires_at=_parse_ts(data["expires_at"]),
        rationale=data["rationale"],
        signature=data["signature"],
    )


# ------------------------------------------------------------------ stores --


class SqliteNonceStore(NonceStore):
    """Spent delegation nonces, remembered across restarts."""

    def __init__(self, db: StateDatabase) -> None:
        self._db = db

    def consume(self, nonce: str, *, expires_at: datetime) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO nonces (nonce, expires_at, expires_epoch) VALUES (?, ?, ?)",
                    (nonce, _ts(expires_at), expires_at.timestamp()),
                )
        except sqlite3.IntegrityError:
            raise ReplayDetected(
                f"delegation nonce {nonce[:8]}... has already been spent"
            ) from None

    def purge(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM nonces WHERE expires_epoch <= ?", (moment.timestamp(),)
            )
            return cursor.rowcount


class SqliteIdempotencyStore(IdempotencyStore):
    """Idempotency reservations and receipts, remembered across restarts.

    A reservation that was in flight when the process died stays reserved after
    restart. That is deliberate: the side effect may have landed, so the key
    goes to reconciliation rather than being silently freed for a retry.
    """

    def __init__(self, db: StateDatabase, *, retention: timedelta = DEFAULT_RETENTION) -> None:
        self._db = db
        self._retention = retention

    def lookup(self, key: str, *, action_digest: str) -> ExecutionReceipt | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT action_digest, receipt FROM idempotency WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        if row[0] != action_digest:
            raise ContractViolation(
                f"idempotency key {key!r} was already used for a different action; "
                "reusing a key across payloads would suppress a real action"
            )
        return None if row[1] is None else _receipt_from_json(row[1])

    def reserve(self, key: str, *, action_digest: str, now: datetime | None = None) -> None:
        moment = now or datetime.now(UTC)
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT receipt FROM idempotency WHERE key = ?", (key,)
            ).fetchone()
            if row is not None and row[0] is None:
                raise ContractViolation(
                    f"idempotency key {key!r} is already in flight; "
                    "a concurrent retry would duplicate the side effect"
                )
            conn.execute(
                "INSERT OR REPLACE INTO idempotency "
                "(key, action_digest, reserved_at, reserved_epoch, receipt) "
                "VALUES (?, ?, ?, ?, NULL)",
                (key, action_digest, _ts(moment), moment.timestamp()),
            )

    def complete(self, key: str, receipt: ExecutionReceipt) -> None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE idempotency SET receipt = ? WHERE key = ?",
                (_receipt_to_json(receipt), key),
            )
            if cursor.rowcount == 0:
                raise ContractViolation(f"idempotency key {key!r} was never reserved")

    def release(self, key: str) -> None:
        with self._db.transaction() as conn:
            conn.execute("DELETE FROM idempotency WHERE key = ? AND receipt IS NULL", (key,))

    def purge(self, *, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        cutoff = (moment - self._retention).timestamp()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM idempotency WHERE receipt IS NOT NULL AND reserved_epoch < ?",
                (cutoff,),
            )
            return cursor.rowcount


class SqliteApprovalStore:
    """Approval items, reviewer decisions and spent markers, kept on disk.

    Implements :class:`causeway.human.ApprovalStore`.
    """

    def __init__(self, db: StateDatabase) -> None:
        self._db = db

    def approval_for_digest(self, action_digest: str) -> str | None:
        with self._db.transaction() as conn:
            row = conn.execute(
                "SELECT approval_id FROM approval_items WHERE action_digest = ?",
                (action_digest,),
            ).fetchone()
        return None if row is None else str(row[0])

    def load(self, approval_id: str) -> ApprovalRecord | None:
        with self._db.transaction() as conn:
            return self._load(conn, approval_id)

    def insert(self, item: ApprovalItem) -> ApprovalItem:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO approval_items (approval_id, action_digest, queue, item) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (action_digest) DO NOTHING",
                (item.approval_id, item.action_digest, item.queue, _item_to_json(item)),
            )
            row = conn.execute(
                "SELECT item FROM approval_items WHERE action_digest = ?",
                (item.action_digest,),
            ).fetchone()
        return _item_from_json(row[0])

    def add_decision(self, decision: SignedDecision) -> None:
        try:
            with self._db.transaction() as conn:
                conn.execute(
                    "INSERT INTO approval_decisions (approval_id, approver_id, decision) "
                    "VALUES (?, ?, ?)",
                    (decision.approval_id, decision.approver_id, _decision_to_json(decision)),
                )
        except sqlite3.IntegrityError:
            raise ReplayDetected(
                f"{decision.approver_id} has already decided approval {decision.approval_id}"
            ) from None

    def mark_spent(self, approval_id: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE approval_items SET spent = 1 WHERE approval_id = ?", (approval_id,)
            )

    def records(self) -> list[ApprovalRecord]:
        with self._db.transaction() as conn:
            ids = [
                str(row[0])
                for row in conn.execute("SELECT approval_id FROM approval_items ORDER BY seq")
            ]
            loaded = [self._load(conn, approval_id) for approval_id in ids]
        return [record for record in loaded if record is not None]

    @staticmethod
    def _load(conn: sqlite3.Connection, approval_id: str) -> ApprovalRecord | None:
        row = conn.execute(
            "SELECT item, spent FROM approval_items WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        if row is None:
            return None
        decisions = [
            _decision_from_json(d[0])
            for d in conn.execute(
                "SELECT decision FROM approval_decisions WHERE approval_id = ? ORDER BY seq",
                (approval_id,),
            )
        ]
        return ApprovalRecord(item=_item_from_json(row[0]), decisions=decisions, spent=bool(row[1]))
