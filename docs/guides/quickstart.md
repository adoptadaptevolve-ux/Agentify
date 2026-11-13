# Quickstart

```bash
git clone https://github.com/calm-trader/agentic-ai-platform
cd agentic-ai-platform
pip install -e ".[dev]"
make demo    # no API key, no network
make test
```

## A governed run in twenty lines

```python
from causeway.contracts.identity import AuthStrength, Principal, PrincipalKind, WorkloadIdentity
from causeway.policy.pack import compose, load_pack
from causeway.sdk.platform import Platform

platform = Platform.local(
    pack=compose(
        load_pack("policies/platform-baseline.json"),
        load_pack("policies/ml-platform.json"),
        name="ml-platform-effective",
        version="2026.09.18",
    ),
    capabilities=list(SPARRING_CAPABILITIES),
)

run = platform.begin(
    principal=Principal(
        id="a.rivera", kind=PrincipalKind.HUMAN, tenant="acme",
        roles=frozenset({"ml-engineer"}),
        auth_strength=AuthStrength.MULTI_FACTOR,
    ),
    workload=WorkloadIdentity(service="my-agent", version="1.0.0", environment="dev"),
    capabilities=frozenset({"open_model_review_case"}),   # the ceiling for the whole run
    purpose="model_review",                               # purpose-of-use is a boundary
    domain="ml-platform",
)

receipt = run.invoke_tool(
    "open_model_review_case",
    {"model_id": "churn-v3", "summary": "leakage suspected", "severity": "concern"},
    idempotency_key=f"case:{run.run_id}:churn-v3",
)
print(receipt.result_class, receipt.evidence_ref)
```

That single `invoke_tool` call narrowed the delegation to one capability, swept the arguments
for injection and egress, evaluated four policy tiers, discharged every obligation, minted a
scoped credential, executed, ran the post-condition, and wrote two events into a hash-chained
ledger.

## Things that will happen on your first run

**`PolicyDenied: no_matching_rule`.** The packs default-deny. Add an allow rule for your
capability, your principal's role and your purpose. See [Policy packs](policy-packs.md).

**`PolicyDenied: missing_idempotency_key`.** R2 and above require one. Make it deterministic
from the run and the subject, not random, or a resumed step will open a second record.

**`ApprovalRequired`.** Policy routed the action to a human. In a workflow the runner catches
this and pauses; outside one, catch it, act as the reviewer via
`platform.approvals.decide(...)`, and call again.

**`ValidationFailed: unknown arguments`.** Arguments are rejected rather than dropped. Silently
ignoring a field the caller thought was meaningful is how an approver ends up reviewing a
different action than the one that executes.

## Inspect what you built

```bash
causeway capabilities
causeway policy-simulate open_model_review_case \
    --arguments '{"model_id":"m","summary":"s","severity":"advisory"}' \
    --role ml-engineer --purpose model_review
causeway graph
causeway falsifiers model_change
```

`policy-simulate` runs the real decision path, so what it prints is what would happen.

## Next

* [Build a capability](build-a-capability.md) — the full domain-team workflow.
* [Write falsifiers](write-falsifiers.md) — the part that takes judgment.
* [The overview](../architecture/01-overview.md) — how it all fits together.
