# Causeway

**An enterprise paved road for agentic AI.** Models reason. The platform authorizes.

An LLM can plan, draft and argue. What it must never do is decide that it is allowed
to act. Causeway draws that line as a hard architectural boundary: a model proposes a
*typed intent*, and a separate, deterministic path decides whether the caller is
authorized, what obligations apply, and whether the side effect may execute.

Everything else in this repository follows from that one sentence.

> *Causeway is a working prototype, not a production system. It is complete enough to
> run, test and argue with — every control path below is real and exercised by the test
> suite — but the reference implementations of storage, signing and approval queues are
> in-memory. See [Production gaps](#production-gaps).*

---

## The seven invariants

These are the properties the platform will not trade away. Each one is enforced
structurally and asserted by a test, not left to code review.

| Invariant | Where it lives | Test |
|---|---|---|
| LLM output is never authorization | `policy/engine.py` takes typed *signals*, never decisions | `test_policy_fail_closed.py` |
| Delegation can only attenuate | `Delegation.attenuate()` is the only way to derive a child | `test_delegation_attenuation.py` |
| Every side effect crosses one enforcement point | `gateway/transaction_guard.py` | `test_obligations.py` |
| Approval binds to an action digest | `ActionIntent.digest`, re-derived before execution | `test_action_digest_binding.py` |
| Authorization uncertainty fails closed | default-deny rules; expiry denies; unknown obligations deny | `test_policy_fail_closed.py` |
| Retries never duplicate a side effect | `gateway/idempotency.py`, reserve-then-complete | `test_idempotency_and_receipts.py` |
| Evidence is part of the transaction | hash-chained ledger written inside the guard | `test_evidence_chain.py` |

## Three ideas that are not standard

Most of Causeway is a careful assembly of known enterprise patterns. Three things are
opinionated enough to be worth reading about before the rest.

### 1. Typed judgment, not parsed prose

Wherever the platform needs a judgment — is this injection, is this claim supported,
which capability does this mean — it asks a **System One** model a narrow *typed*
question and branches on the typed answer. Three primitives: `Noul` (is this true? → a
calibrated probability), `Choice` (which of these? → one option plus a distribution),
`Score` (which level? → a weighted position on a rubric).

This matters because the answer space is **closed** (a `Choice` can only return an
option the platform supplied, so a judgment can never name a capability or destination
that does not exist), uncertainty is a **number** policy can threshold on, and
independent questions **batch** into one call.

See [`docs/architecture/05-judgment.md`](docs/architecture/05-judgment.md).

### 2. Falsification, not confirmation

Asking a model "is this correct?" is a bad question, and it gets worse as models get
better: a capable model asked to confirm will find a reading under which the thing is
fine, because that is what was requested. The failure is not hallucination, it is
compliance.

So the platform never asks for confirmation. For each way a claim could be wrong, it
asks a separate question whose **yes is bad news**:

```
claim:      "the holdout set is uncontaminated"
falsifier:  "is there evidence that holdout rows also appear in training?"
```

A suite returns `SURVIVED`, `REFUTED` or `INCONCLUSIVE` — never `CORRECT`. Because the
questions batch, twenty falsifiers cost about what one costs, so the check is cheap
enough to run on every action rather than only the suspicious ones.

See [`docs/architecture/06-verification.md`](docs/architecture/06-verification.md).

### 3. A board of supervisors, aggregated by code

One "judge" model asked whether a proposal is acceptable produces a single number
dominated by whichever concern the prompt emphasised. Causeway runs a **board** instead:
several narrow supervisors, each with its own charter and questions, each returning its
own verdict — and they cannot see each other, because independent reviewers disagree in
useful ways.

**The aggregation is a pure function, not a model.** Any supervisor with veto authority
at the case's risk tier is decisive; otherwise the concern is a weighted mean *floored by
the highest concern from any supervisor that filed a named finding*, because averaging
away a concrete objection is how boards become rubber stamps. Low confidence or strong
disagreement routes to a human.

The board never authorizes anything. It emits two numbers — `board_concern` and
`board_confidence` — that the policy engine may take into account.

---

## Try it

```bash
pip install -e ".[dev]"
make demo     # the golden path, no API key, no network
make test     # 70 invariant tests
```

The demo runs a genAI **sparring partner**: an agent an ML team uses to stress-test a
proposed model change before review. Abridged output:

```
1. A weak proposal meets the sparring partner
  falsifiers  : model_change: refuted (6/10 falsifiers refuted, concern 0.90)
  refuted     : holdout_leakage, train_serve_skew, weak_baseline, evaluation_window, ...
  board       : review (concern 0.90, confidence 0.20, disagreement 0.90)
  withheld    : 1 standard(s) not entitled
  paused on   : approval apr_01M2TT...

2. A human decides, and the run resumes where it paused
  queue       : ml-platform-reviews
  status      : completed
  severity    : blocking     case: MRC-9GY7YGG1     notified: True

3. A strong proposal survives the suite
  falsifiers  : model_change: survived (0/10 falsifiers refuted, concern 0.02)
  board       : clear     severity: advisory     (no escalation, no human)

4. A hostile proposal, and a prohibited capability
  steps           : intake -> refuse
  least authority : AttenuationViolation -- the run never held it
  prohibited      : policy refused, reason_code=prohibited_autonomy
  promotion       : policy refused, reason_code=purpose_mismatch

5. The evidence chain
  chain       : intact across 63 events
  side effect : True
```

Everything above runs against a deterministic **offline judge**, so CI tests the
platform rather than a model's mood. Set `TYPESAFE_API_KEY` to run the same paths
against a hosted System One model.

### Inspect it from a terminal

```bash
causeway capabilities        # what the platform will execute, and at what tier
causeway falsifiers          # the suite, as reviewable text
causeway graph               # the workflow as Mermaid
causeway policy-simulate delete_experiment_lineage --arguments '{"experiment_id":"e"}'
causeway verify-evidence run.ledger.jsonl
```

---

## What a domain team writes

The sparring partner in [`capabilities/sparring_partner/`](capabilities/sparring_partner/)
is roughly four hundred lines: rubrics, falsifiers, supervisors, five typed capabilities
and a workflow graph. **No security code, no audit code, no approval plumbing.** That is
the measure of the paved road.

```python
run = platform.begin(
    principal=engineer,          # authenticated, from enterprise IAM
    workload=agent_identity,     # attested image, version, environment
    capabilities=frozenset({"open_model_review_case", "notify_model_owner"}),
    purpose="model_review",
    domain="ml-platform",
)

report = run.falsify(MODEL_CHANGE_SUITE, proposal)      # one batched judgment call
board  = run.deliberate(case, sparring_board())         # independent verdicts, code aggregation

receipt = run.invoke_tool(                              # guardrails, policy, approval,
    "open_model_review_case",                           # idempotency, verification and
    {"model_id": ..., "severity": "blocking", ...},     # evidence all happen here
    idempotency_key=f"case:{run.run_id}:{model_id}",
)
```

`invoke_tool` narrows the run's delegation to exactly one capability, sweeps the
arguments for injection and egress, feeds the results to the policy engine as signals,
discharges every obligation the decision returned, mints a short-lived credential,
executes, verifies the post-condition, and writes the receipt into the evidence chain.
If policy routes it to a human, it raises `ApprovalRequired`, the runner checkpoints,
and the run resumes in the same step once someone decides.

---

## Architecture

```
                    proposal  │  execution
                              │
  ┌────────────┐   ┌──────────┴───────────┐   ┌─────────────────┐
  │  workflow  │──▶│   transaction guard  │──▶│  typed capability│
  │   runtime  │   │  (the one PEP)       │   │  (narrow, tiered)│
  └─────┬──────┘   └──────────┬───────────┘   └────────┬────────┘
        │                     │                        │
   ┌────▼─────┐      ┌────────▼────────┐        ┌──────▼───────┐
   │ judgment │      │  policy engine  │        │  credentials │
   │  plane   │─sig─▶│  L0 → L1 → L2 → L3       │  (JIT, scoped)│
   │ (typed)  │      └────────┬────────┘        └──────────────┘
   └────┬─────┘               │
        │              ┌──────▼──────┐
   ┌────▼─────┐        │   human     │
   │  verify  │        │  decision   │   digest-bound, domain queues
   │ falsify  │        └─────────────┘
   │  board   │
   └──────────┘
                    ══════════════════════════
                     evidence: hash-chained,
                     written inside the txn
```

| Package | What it owns |
|---|---|
| `contracts/` | the stable trust seams — data and protocols only, no I/O |
| `authority/` | signed, attenuating delegation; the only place authority is minted |
| `policy/` | tiered deterministic decisions from versioned, signed artifacts |
| `registry/` | what the platform will run, and which models it may consult |
| `judgment/` | typed System One judgments, plus the offline fixture |
| `verify/` | falsification suites and the supervisor board |
| `gateway/` | the transaction guard, idempotency, JIT credentials |
| `knowledge/` | ACL-before-retrieval, scoped memory with provenance |
| `human/` | digest-bound approvals routed to domain queues |
| `evidence/` | the hash-chained ledger, replay and forensic export |
| `runtime/` | explicit workflow graphs, budgets, checkpoints, interrupts |
| `sdk/` | the developer surface, where only governed operations exist |

Full documentation: [`docs/`](docs/). Start with
[the overview](docs/architecture/01-overview.md), then
[the decision records](docs/decisions/) for *why*.

## Risk tiers

Read, reversible write, customer-impacting and privileged actions do not share one
control path. Every capability declares a tier at registration, and inherits that tier's
posture the day it is registered rather than depending on whoever registered it having
remembered the controls.

| Tier | Example | Posture |
|---|---|---|
| R0 informational | drafting, public knowledge | model gateway, content safety |
| R1 internal read | entitled search, summarisation | ACL-before-retrieval, citations |
| R2 reversible write | open a case, update metadata | idempotency, post-verification, rollback |
| R3 customer impact | notify an owner, bounded refund | step-up auth, destination allowlist, enhanced evidence |
| R4 high consequence | promote a model, change access | human approval, dual control, attested workload |
| R5 prohibited | destroy lineage | hard deny in the agent path |

## Production gaps

Honest list of what is a reference implementation here and needs replacing:

- **Signing** is HMAC with an in-process keyring. A symmetric key means any verifier can
  also mint. Production implements `Signer` against an HSM or KMS.
- **Evidence** is in-memory or single-node JSONL. Production needs append-only storage
  with independent retention and an external anchor.
- **Approvals, idempotency and nonces** are in-memory and single-process. All three need
  durable, replica-shared stores; a nonce store that forgets is a replay window, and an
  approval lost on restart is an action that silently never happens.
- **Retrieval** ranks by term overlap. Swap the matcher for hybrid search; keep the
  entitlement filter exactly as it is.
- **The offline judge** is a fixture, not a model. It exists so CI and the local sandbox
  never need a network.

## Standards this is designed to satisfy

Reference architecture anchors, not claims of certification: NIST AI RMF 1.0 and AI
600-1, NIST SP 800-207 (zero trust), OWASP Agentic AI threats and mitigations, FFIEC
architecture and operations guidance, and interagency model risk management guidance.
The platform's job is to *generate the evidence* those lifecycles need, rather than to
treat validation as a document-only exercise.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
