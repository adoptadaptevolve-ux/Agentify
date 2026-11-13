# API reference

The reference is the source. Every public symbol in `src/causeway/` carries a docstring that
explains *why* it exists, not only what it does, and reading the source is a supported way to
learn the platform.

Suggested reading order for a new contributor:

1. `contracts/` — all of it. It is data and protocols, no I/O, and it is the trust model.
2. `gateway/transaction_guard.py` — the one enforcement point, with the whole flow in order.
3. `policy/engine.py` — the tiered decision, and why it is pure.
4. `verify/falsify.py` and `verify/supervisors.py` — the two verification mechanisms.
5. `sdk/run.py` — what a domain team actually touches.
6. `capabilities/sparring_partner/` — a real application built on all of it.

To generate HTML, point any docstring-based tool at `src/causeway`; the codebase is fully
typed and `mypy --strict` clean, so signatures render without annotation gaps.
