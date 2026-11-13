"""Approvals, idempotency keys and nonces survive a restart.

Each test writes state through one set of objects, throws them away, and reads
it back through fresh ones opened on the same file -- which is what a process
restart looks like to the platform.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from capabilities.sparring_partner.capabilities import SPARRING_CAPABILITIES, cases

from causeway.contracts.action import ResultClass
from causeway.contracts.human import ApprovalItem, DecisionOutcome
from causeway.contracts.identity import Principal, WorkloadIdentity
from causeway.contracts.risk import RiskTier
from causeway.errors import ContractViolation, ReplayDetected
from causeway.human import HumanDecisionService, build_packet
from causeway.policy.pack import PolicyPack
from causeway.sdk.platform import Platform
from causeway.storage import (
    SqliteApprovalStore,
    SqliteIdempotencyStore,
    SqliteNonceStore,
    StateDatabase,
    open_state_db,
)

REVIEW_CAPABILITIES = frozenset(
    {"open_model_review_case", "close_model_review_case", "notify_model_owner"}
)
T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


_OPENED: list[StateDatabase] = []


def _db(path: Path) -> StateDatabase:
    """Open the state file and remember to close it when the test ends."""
    db = open_state_db(path)
    _OPENED.append(db)
    return db


@pytest.fixture(autouse=True)
def _close_databases() -> Iterator[None]:
    yield
    while _OPENED:
        _OPENED.pop().close()


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.sqlite3"


def _approvals(path: Path) -> HumanDecisionService:
    """A fresh service on ``path`` -- a restart, as far as the service can tell."""
    return HumanDecisionService(store=SqliteApprovalStore(_db(path)))


def _request(service: HumanDecisionService, *, required: int = 1) -> ApprovalItem:
    return service.request(
        action_digest=DIGEST,
        run_id="run-1",
        tenant="acme",
        queue="ml-platform-reviews",
        packet=build_packet(
            intent_summary="notify the model owner",
            capability="notify_model_owner",
            risk_tier=RiskTier.R3_CUSTOMER_IMPACT,
            resource="churn-v3",
            destination="model-owner-direct",
            amount_display=None,
            delegation_scope={"capabilities": ["notify_model_owner"], "max_amount": None},
            risk_factors=("board concern 0.8",),
            policy_rationale="approval_required",
            alternatives=("open a case instead",),
            rollback="none: a message cannot be unsent",
        ),
        required_approvals=required,
        required_authority=frozenset({"model-risk"}),
        now=T0,
    )


# ---------------------------------------------------------------- approvals --


def test_a_pending_approval_survives_a_restart(state_path: Path) -> None:
    """An item opened before the restart is still in the queue after it, intact."""
    item = _request(_approvals(state_path))

    after = _approvals(state_path)
    assert after.pending(queue="ml-platform-reviews") == (item,)
    assert after.item(item.approval_id) == item
    assert after.resolve(DIGEST, now=T0) is None  # still waiting on a reviewer


def test_re_requesting_after_a_restart_reuses_the_item(state_path: Path) -> None:
    """A retried action must not open a second queue entry."""
    first = _request(_approvals(state_path))
    second = _request(_approvals(state_path))
    assert second.approval_id == first.approval_id


def test_a_decision_made_before_a_restart_is_spendable_after_it(state_path: Path) -> None:
    item = _request(_approvals(state_path))
    decision = _approvals(state_path).decide(
        approval_id=item.approval_id,
        approver_id="j.okafor",
        approver_authority=frozenset({"model-risk"}),
        outcome=DecisionOutcome.APPROVED,
        now=T0 + timedelta(minutes=5),
    )

    resolved = _approvals(state_path).resolve(DIGEST, now=T0 + timedelta(minutes=6))
    assert resolved == decision


def test_a_spent_approval_stays_spent_across_a_restart(state_path: Path) -> None:
    """Otherwise a restart would turn one approval into two executions."""
    item = _request(_approvals(state_path))
    service = _approvals(state_path)
    service.decide(
        approval_id=item.approval_id,
        approver_id="j.okafor",
        approver_authority=frozenset({"model-risk"}),
        outcome=DecisionOutcome.APPROVED,
        now=T0 + timedelta(minutes=5),
    )
    service.spend(DIGEST)

    after = _approvals(state_path)
    assert after.resolve(DIGEST, now=T0 + timedelta(minutes=6)) is None
    assert after.pending() == ()


def test_a_denial_survives_a_restart(state_path: Path) -> None:
    item = _request(_approvals(state_path))
    _approvals(state_path).decide(
        approval_id=item.approval_id,
        approver_id="j.okafor",
        approver_authority=frozenset({"model-risk"}),
        outcome=DecisionOutcome.DENIED,
        rationale="leakage not addressed",
        now=T0 + timedelta(minutes=5),
    )

    resolved = _approvals(state_path).resolve(DIGEST, now=T0 + timedelta(minutes=6))
    assert resolved is not None
    assert resolved.outcome is DecisionOutcome.DENIED
    assert resolved.rationale == "leakage not addressed"


def test_dual_control_holds_across_a_restart(state_path: Path) -> None:
    """The same reviewer cannot supply both halves by deciding again after a restart."""
    item = _request(_approvals(state_path), required=2)
    _approvals(state_path).decide(
        approval_id=item.approval_id,
        approver_id="j.okafor",
        approver_authority=frozenset({"model-risk"}),
        outcome=DecisionOutcome.APPROVED,
        now=T0 + timedelta(minutes=5),
    )
    with pytest.raises(ReplayDetected):
        _approvals(state_path).decide(
            approval_id=item.approval_id,
            approver_id="j.okafor",
            approver_authority=frozenset({"model-risk"}),
            outcome=DecisionOutcome.APPROVED,
            now=T0 + timedelta(minutes=6),
        )
    assert _approvals(state_path).resolve(DIGEST, now=T0 + timedelta(minutes=7)) is None


def test_expiry_still_denies_after_a_restart(state_path: Path) -> None:
    _request(_approvals(state_path))
    resolved = _approvals(state_path).resolve(DIGEST, now=T0 + timedelta(hours=5))
    assert resolved is not None
    assert resolved.outcome is DecisionOutcome.EXPIRED


# -------------------------------------------------------------- idempotency --


def _durable_platform(pack: PolicyPack, state_path: Path) -> Platform:
    return Platform.local(
        pack=pack, capabilities=list(SPARRING_CAPABILITIES), state_path=state_path
    )


def test_a_retry_after_a_restart_returns_the_original_receipt(
    pack: PolicyPack, state_path: Path, engineer: Principal, workload: WorkloadIdentity
) -> None:
    """The side effect happens once, even though the process that ran it is gone."""
    receipts = []
    for _ in range(2):  # two platform lifetimes on the same state file
        platform = _durable_platform(pack, state_path)
        _OPENED.append(platform.state)  # type: ignore[arg-type]
        run = platform.begin(
            principal=engineer,
            workload=workload,
            capabilities=REVIEW_CAPABILITIES,
            purpose="model_review",
            domain="ml-platform",
            run_id="run-durable-1",
        )
        receipts.append(
            run.invoke_tool(
                "open_model_review_case",
                {"model_id": "churn-v3", "summary": "findings", "severity": "advisory"},
                purpose="model_review",
                idempotency_key="case:run-durable-1:churn-v3",
            )
        )

    first, second = receipts
    assert first.result_class is ResultClass.SUCCESS
    assert second.result_class is ResultClass.DUPLICATE
    assert second.action_id == first.action_id
    assert len(cases()) == 1


def test_an_in_flight_reservation_is_not_freed_by_a_restart(state_path: Path) -> None:
    """The side effect may have landed, so the key goes to reconciliation, not a retry."""
    SqliteIdempotencyStore(_db(state_path)).reserve("k", action_digest=DIGEST, now=T0)

    after = SqliteIdempotencyStore(_db(state_path))
    with pytest.raises(ContractViolation, match="already in flight"):
        after.reserve("k", action_digest=DIGEST, now=T0)
    with pytest.raises(ContractViolation, match="different action"):
        after.lookup("k", action_digest="sha256:" + "b" * 64)


def test_only_one_of_two_processes_can_reserve_a_key(state_path: Path) -> None:
    """Two independent connections stand in for two worker processes."""
    stores = [SqliteIdempotencyStore(_db(state_path)) for _ in range(8)]
    outcomes: list[str] = []
    barrier = threading.Barrier(len(stores))

    def attempt(store: SqliteIdempotencyStore) -> None:
        barrier.wait()
        try:
            store.reserve("contended", action_digest=DIGEST, now=T0)
            outcomes.append("reserved")
        except ContractViolation:
            outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(s,)) for s in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("reserved") == 1
    assert outcomes.count("refused") == len(stores) - 1


# ------------------------------------------------------------------- nonces --


def test_a_spent_nonce_stays_spent_across_a_restart(state_path: Path) -> None:
    """A nonce store that forgets is a replay window."""
    SqliteNonceStore(_db(state_path)).consume("n-1", expires_at=T0)
    with pytest.raises(ReplayDetected):
        SqliteNonceStore(_db(state_path)).consume("n-1", expires_at=T0)


def test_expired_nonces_are_purged(state_path: Path) -> None:
    store = SqliteNonceStore(_db(state_path))
    store.consume("old", expires_at=T0)
    store.consume("live", expires_at=T0 + timedelta(hours=1))
    assert store.purge(now=T0 + timedelta(minutes=1)) == 1
    with pytest.raises(ReplayDetected):
        store.consume("live", expires_at=T0 + timedelta(hours=1))


# ------------------------------------------------------------------- schema --


def test_a_state_file_from_another_schema_version_is_refused(state_path: Path) -> None:
    """Misreading old state could turn a spent approval back into a live one."""
    open_state_db(state_path).close()
    with closing(sqlite3.connect(state_path)) as conn, conn:
        conn.execute("UPDATE causeway_meta SET value = '99' WHERE key = 'schema_version'")
    with pytest.raises(ContractViolation, match="schema version 99"):
        open_state_db(state_path)
