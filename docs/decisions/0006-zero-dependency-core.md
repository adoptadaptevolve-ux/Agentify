# 0006 — A zero-dependency trust-critical core

**Status:** Accepted · **Date:** 2026-09-18

## Context

The platform's job is to be the thing an enterprise trusts. Canonical encoding, hashing,
signing, the evidence chain and the policy engine are the parts where a supply-chain
compromise would be invisible and total.

Meanwhile, the obvious conveniences — a validation library for the contracts, a settings
library for configuration, a serialisation library for the ledger — each bring a transitive
tree.

## Decision

`causeway` has **no required third-party dependencies**. Everything the trust-critical path
needs is in the standard library: `hashlib`, `hmac`, `json`, `dataclasses`, `decimal`,
`unicodedata`, `secrets`.

Optional extras are exactly two: `systemone` (the hosted judgment SDK) and `dev` (pytest,
mypy, ruff).

## Alternatives rejected

**Pydantic for the contracts.** Better ergonomics, better error messages, and a genuinely
tempting call. Rejected because the contracts package is the thing an auditor reads to
understand the trust model, and frozen dataclasses with explicit `__post_init__` checks are
readable by someone who has never used the library. The validation the platform actually
depends on — attenuation, digest binding, obligation completeness — is custom logic that a
validation library would not have provided anyway.

**A YAML policy pack format.** More pleasant to write than JSON. Rejected for the dependency;
`tomllib` was considered and rejected because nested rule structures read badly in TOML. JSON
is adequate and universal.

**A structured logging library for evidence.** The evidence plane is not logging. It is a
hash-chained ledger with its own integrity contract, and building it on a logging library
would invite treating it like logs.

## Consequences

**Good.** `pip install causeway` pulls nothing. The trust-critical path has no supply chain of
its own. The code is readable without knowing any framework. Auditing what the platform depends
on is a five-second exercise.

**Costs.**

* More hand-written validation, and worse error messages than a validation library would give.
* Canonical encoding had to be written and tested rather than adopted. `tests/test_canonical.py`
  exists because of this decision.
* Teams that already standardise on Pydantic will wrap the contracts. That is fine — the
  contracts are data — but it is duplicated effort at the edge.
