"""Wiring: one object that holds the platform's services.

The highest-leverage control a platform has is adoption. If the secure path is
slower than bypassing it, teams will bypass it and be right to. So the paved
road has to be the shortest road: ``Platform.local()`` returns a fully wired
platform -- registry, policy, authority, evidence, approvals, judgment,
retrieval, memory -- with no configuration, no credentials and no network.

The same construction runs in production with different implementations behind
the same protocols: a KMS-backed signer, a durable ledger, a real approval
queue, a hosted judgment plane. The domain code does not change, because it was
written against the contracts rather than against the reference implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from ..authority.issuer import DelegationIssuer
from ..authority.signer import HmacSigner, Signer
from ..authority.verifier import DelegationVerifier, NonceStore
from ..contracts.delegation import Constraints
from ..contracts.identity import Principal, WorkloadIdentity
from ..contracts.judgment import SystemOne
from ..evidence.ledger import EvidenceLedger, InMemoryEvidenceLedger
from ..gateway.credentials import CredentialBroker, LocalCredentialBroker
from ..gateway.idempotency import IdempotencyStore
from ..gateway.transaction_guard import TransactionGuard
from ..human.decisions import HumanDecisionService
from ..ids import new_run_id
from ..judgment import default_judge
from ..knowledge.memory import MemoryStore
from ..knowledge.retrieval import RetrievalGateway
from ..policy.engine import PolicyEngine
from ..policy.pack import PolicyPack
from ..registry.capability import Capability, CapabilityRegistry
from ..registry.models import ModelCatalog
from ..runtime.budgets import BudgetLedger, RunBudget

if TYPE_CHECKING:  # pragma: no cover
    from .run import AgentRun

TOOL_GATEWAY_AUDIENCE = "tool-gateway"


@dataclass(slots=True)
class Platform:
    """Every platform service, wired together."""

    registry: CapabilityRegistry
    policy: PolicyEngine
    ledger: EvidenceLedger
    approvals: HumanDecisionService
    guard: TransactionGuard
    judge: SystemOne
    issuer: DelegationIssuer
    delegation_verifier: DelegationVerifier
    retrieval: RetrievalGateway = field(default_factory=RetrievalGateway)
    memory: MemoryStore = field(default_factory=MemoryStore)
    models: ModelCatalog = field(default_factory=ModelCatalog)
    audience: str = TOOL_GATEWAY_AUDIENCE

    @staticmethod
    def local(
        *,
        pack: PolicyPack,
        capabilities: list[Capability] | None = None,
        judge: SystemOne | None = None,
        signer: Signer | None = None,
        ledger: EvidenceLedger | None = None,
        credentials: CredentialBroker | None = None,
        models: ModelCatalog | None = None,
        delegation_lifetime: timedelta = timedelta(minutes=10),
    ) -> Platform:
        """Build the local sandbox: in-memory everything, offline judgment.

        This is what a developer gets from ``causeway init`` and what CI runs.
        Its behaviour is identical to production on every control path; only
        the backing stores differ.
        """
        active_signer = signer or HmacSigner()
        registry = CapabilityRegistry(capabilities or [])
        engine = PolicyEngine(pack)
        evidence = ledger or InMemoryEvidenceLedger()
        approvals = HumanDecisionService()
        verifier = DelegationVerifier(active_signer, nonce_store=NonceStore())
        return Platform(
            registry=registry,
            policy=engine,
            ledger=evidence,
            approvals=approvals,
            guard=TransactionGuard(
                registry=registry,
                policy=engine,
                delegation_verifier=verifier,
                ledger=evidence,
                approvals=approvals,
                credentials=credentials or LocalCredentialBroker(),
                idempotency=IdempotencyStore(),
                audience=TOOL_GATEWAY_AUDIENCE,
            ),
            judge=judge or default_judge(),
            issuer=DelegationIssuer(active_signer, default_lifetime=delegation_lifetime),
            delegation_verifier=verifier,
            models=models or ModelCatalog(),
        )

    def begin(
        self,
        *,
        principal: Principal,
        workload: WorkloadIdentity,
        capabilities: frozenset[str],
        purpose: str,
        domain: str,
        constraints: Constraints | None = None,
        budget: RunBudget | None = None,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        run_id: str | None = None,
    ) -> AgentRun:
        """Start a run: mint its root delegation and hand back the SDK handle.

        The capabilities named here are the *ceiling* for the whole run,
        including anything it spawns. A run cannot later decide it needs one
        more capability; that decision belongs to whoever authorised the run.
        """
        unknown = sorted(capabilities - self.registry.ids())
        if unknown:
            raise ValueError(f"run requests unregistered capabilities: {unknown}")

        identifier = run_id or new_run_id()
        delegation = self.issuer.root(
            principal=principal,
            workload=workload,
            run_id=identifier,
            domain=domain,
            capabilities=capabilities,
            purpose=purpose,
            audience=self.audience,
            policy_version=self.policy.version,
            constraints=constraints,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        )
        from .run import AgentRun  # imported here to keep the module graph acyclic

        return AgentRun(
            platform=self,
            run_id=identifier,
            principal=principal,
            workload=workload,
            delegation=delegation,
            budget=BudgetLedger(budget or RunBudget()),
            domain=domain,
        )
