# Causeway documentation

**Models reason. The platform authorizes.**

## Reading order

If you have ten minutes, read [the overview](architecture/01-overview.md).
If you have an hour, read the overview and then the three decision records that make
this platform different from a framework:
[0002](decisions/0002-typed-judgment-not-parsed-prose.md),
[0003](decisions/0003-falsification-over-confirmation.md),
[0004](decisions/0004-a-board-aggregated-by-code.md).

### Architecture — what is built

| | |
|---|---|
| [00 North star](architecture/00-north-star.md) | The invariants, and what they cost |
| [01 Overview](architecture/01-overview.md) | The whole system in one document |
| [02 Contracts](architecture/02-contracts.md) | The stable seams every team builds against |
| [03 Authority](architecture/03-authority.md) | Identity, delegation, zero standing privilege |
| [04 Policy](architecture/04-policy.md) | Tiered decisions, risk tiers, policy packs |
| [05 Judgment](architecture/05-judgment.md) | Typed System One judgment; why not prose |
| [06 Verification](architecture/06-verification.md) | Falsification suites and the supervisor board |
| [07 Evidence](architecture/07-evidence.md) | The hash chain, replay, forensic export |
| [08 Runtime](architecture/08-runtime.md) | Workflow graphs, budgets, checkpoints, interrupts |
| [09 Knowledge](architecture/09-knowledge.md) | ACL-before-retrieval and scoped memory |
| [10 Operations](architecture/10-operations.md) | Resilience, cost, multi-region *(outline)* |

### Decisions — why it is built this way

Architecture decision records live in [`decisions/`](decisions/). Each one states the
problem, what was chosen, what was rejected, and what the choice costs.

### Guides — how to use it

| | |
|---|---|
| [Quickstart](guides/quickstart.md) | A governed run in twenty lines |
| [Build a capability](guides/build-a-capability.md) | The whole domain-team workflow |
| [Write falsifiers](guides/write-falsifiers.md) | How to attack your own claims well |
| [Policy packs](guides/policy-packs.md) | Rules as versioned, testable artifacts |
| [Evidence and replay](guides/evidence-and-replay.md) | Answering "why was this allowed?" |
| [Operating model](guides/operating-model.md) | Who owns what |

### Reference

API reference is generated from docstrings; see [`reference/`](reference/README.md).
Every public symbol in `src/causeway` carries a docstring that explains *why* it exists,
not only what it does, so reading the source is a supported way to learn the platform.

## Status

Prototype. Every control path documented here is implemented and tested; the storage,
signing and queueing implementations behind them are in-memory references. The honest
list of what needs replacing is in the README under *Production gaps*.
