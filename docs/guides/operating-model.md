# Operating model

Who owns what. This is the part of a platform that is not code, and it is usually why adoption
succeeds or fails.

## Ownership

| Central platform owns | Domain teams own | Independent risk / security owns |
|---|---|---|
| canonical contracts and SDK | the business workflow | risk and control standards |
| runtime adapters | domain prompts and rubrics | validation for material use |
| model, retrieval and tool gateways | approved knowledge sources | red-team methodology |
| identity and delegation | capability implementations | control testing and audit sampling |
| the policy platform | risk-tier proposal | effective challenge |
| the evidence plane | domain falsifiers and supervisors | waiver governance |
| eval and red-team harness | outcome metrics and on-call | |
| golden paths and starter kits | | |

The line worth defending: the platform owns *how authority, evidence and side effects work*.
Domain teams own *what the agent is for*. A domain team that finds itself writing security code
has found a gap in the paved road, and that is a platform bug.

## Forums

* **Platform architecture council** — canonical contracts, major RFCs, compatibility,
  deprecation windows.
* **AI / model risk forum** — risk taxonomy, validation thresholds, model and tool approval,
  material changes.
* **Security architecture and red team** — threat model, offensive findings, egress and identity
  controls, incident learnings.
* **Domain control owners** — per-domain policy packs and approval patterns.
* **Developer council** — adoption friction, starter kits, SDK ergonomics, exception burn-down.

## The exception path

An architecture with no exception path gets bypassed silently, which is worse. A waiver is
time-bounded and carries an owner, a rationale, a compensating control, a risk sign-off and an
expiry. Waivers are counted, published and burned down.

## What to measure

| Dimension | Metric |
|---|---|
| Adoption | % of new agentic apps on the canonical APIs; waiver count; time to onboard |
| Velocity | time from repo create to first governed tool call |
| Safety | unauthorized side effects (target: zero); policy bypass findings; blast radius |
| Quality | task success; citation precision; escalation rate; human correction rate |
| Reliability | p95/p99 latency; availability; recovery time; duplicate-action rate |
| Cost | cost per run; cost per successful task; cache hit rate; model mix |
| Governance | inventory and eval coverage; review currency; evidence completeness |
| Auditability | % of action traces complete; replay success; time to forensic package |

Two of these deserve particular attention because they are the ones that go wrong quietly:

**Human review rate and overturn rate.** If review rate climbs, thresholds are too tight and
the queue is becoming a formality. If overturn rate is near zero, reviewers are rubber-stamping
and the control is nominal. Both are measured from the evidence plane.

**Waiver count and age.** A platform with a growing waiver list is a platform people are
routing around. That is feedback about the paved road, not about the teams.

## Adoption

Start with two or three lighthouse domains representing different risk classes: a read-only
knowledge use case, a reversible-write operation, and one bounded customer-impacting
transaction. Build migration adapters so adoption does not require rewrites. Instrument
bypasses. Publish a monthly scorecard.

The highest-leverage control a platform has is adoption. **If the secure path is slower than
bypassing it, teams will bypass it and be right to.**
