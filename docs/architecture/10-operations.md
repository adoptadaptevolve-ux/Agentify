# 10 — Operations

> **Status: outline.** The prototype implements none of this. It is here because the
> production shape of these concerns changes decisions made elsewhere in the architecture, and
> a reader should know what is designed-for versus what is built.

## Isolation and blast radius

Tenant and domain cells, namespace isolation, service identities, network policy, quotas and
per-tenant budgets. One shared agent cluster with coarse multi-tenancy is the anti-pattern;
`RunBudget` and the capability registry's `rate_limit_per_minute` and `kill_switch` fields are
the per-run and per-capability halves of the same idea.

## Backpressure and autoscaling

Scale on queue depth, tool latency, model throughput, token demand and concurrency — not CPU
alone. Bounded queues, admission control, per-tool concurrency limits, retry budgets and
overload shedding.

## Policy during failure

Authorization remains enforced from signed or cached artifacts. If required context is
unavailable, deny or safe-degrade. The L0 tier is designed to have no remote dependency
precisely so that it still runs when everything else is failing.

## Resilience

Bulkheads, circuit breakers, dependency-specific timeouts, regional evacuation, controlled
failover and chaos testing. Documented RTO and RPO by service and action class, with restore
tests that include policy and evidence integrity.

## Data residency

Region-aware retrieval, memory, evidence and model endpoints, with an explicit cross-region
replication policy. `ModelCard.residency` is the hook; nothing enforces it yet.

## Cost

| Lever | Safe pattern |
|---|---|
| Policy | compiled bundles, local L0, bounded caches keyed by identity/purpose/resource, deep checks only on risk trigger |
| Model inference | route by capability and risk, compress context, cache prefixes, batch non-interactive work |
| Retrieval | hybrid search, filter early, entitlement-aware cache keys, freshness-aware invalidation |
| Tooling | connection pools, parallelize independent *reads*, never parallelize actions needing sequencing |
| Observability | sample high-volume low-risk detail; retain mandatory action and evidence events always |

Routing to a cheaper model is permitted only when the safety and capability policy allows it.
`ModelCatalog.resolve()` already refuses a fallback that does not meet the same data and risk
requirements as the model it replaces; it degrades or stops rather than silently moving a
workload onto an unvalidated endpoint.

## SLO classes

| Class | Example | Target behaviour |
|---|---|---|
| Interactive read | copilot, RAG | fast; fallback only to approved equivalents; no side effect |
| Reversible write | case or ticket update | moderate latency for policy and verification; idempotent retry |
| Financial or customer impact | refund, account workflow | safety and transaction integrity dominate latency |
| Human-gated | high-consequence action | minutes to hours by business SLA; queue durability dominates |

## Evaluation lifecycle

Also an outline. Every material workflow should carry a versioned eval contract covering
offline quality (task success, groundedness, citation quality, tool correctness,
structured-output validity, abstention), safety and adversarial suites (injection, indirect
injection, tool chaining, delegation abuse, exfiltration, poisoned retrieval, privilege
escalation), operational behaviour (latency, retry, dependency failure, idempotency,
rollback) and production telemetry (drift, denials, human corrections, near-misses).

The falsification suites in `verify/` are the runtime half of this. The offline harness that
runs them against recorded cases on a schedule is not built.
