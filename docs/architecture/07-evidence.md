# 07 — Evidence

## Evidence is part of the transaction

Not a log written afterwards. The transaction guard appends the policy decision and the
execution receipt inside the same call that performs the side effect, so *"the action
happened but nothing recorded it"* is not a reachable state.

The audit test for this platform is not "are there logs?" but **"can any consequential run
be reconstructed without log archaeology?"** — who asked, what the agent saw, which model,
policy and tool versions ran, what changed, and who approved it.

## The hash chain

Each event's hash covers its own contents *and* its predecessor's hash. Editing or removing
any event invalidates every hash after it.

This does not stop someone with write access from rewriting the whole file — nothing in a
single-writer ledger can. It turns a silent edit into a **detectable** one, and `verify()` is
cheap enough to run on every read and in CI.

`causeway verify-evidence run.ledger.jsonl` re-derives the chain from the file alone, so a
reader who has never seen this codebase can check it.

## Protected payloads

Events are deliberately thin. High-cardinality or sensitive arguments are stored as a digest
plus a `protected_ref` into controlled storage, so the widely readable ledger proves *what
happened* without becoming the easiest place to read customer data.

`tests/test_evidence_chain.py` asserts that a sensitive argument does not appear in the open
payload of the event that records the action that carried it.

## Event domains

Fixed across frameworks and models, so organisation-wide analysis does not have to know which
orchestration library produced a run:

`identity` · `agent` · `model` · `judgment` · `retrieval` · `memory` · `policy` · `tool` ·
`human` · `outcome`

Event type names live in one module (`evidence/events.py`) so dashboards, forensic exports and
tests agree on the spelling, and renaming an event is a visible change rather than a silent
break in someone's audit query.

## Replay and forensics

`reconstruct(ledger, run_id)` rebuilds one run into decisions, actions, approvals, judgments,
retrievals and a timeline. It **verifies the chain first**: a reconstruction from a broken
chain would be worse than no reconstruction, because it would look authoritative.

`forensic_package(ledger, run_id)` produces a single self-contained export with the chain head
and an explicit integrity verdict, so a package taken from a broken chain says so on its face.

`RunReconstruction.unverified_actions` is the reconciliation queue: actions that executed but
whose post-condition did not confirm. The side effect may or may not have landed, so they are
neither retried blindly nor quietly forgotten.

## What still needs building

The in-memory and JSONL ledgers are reference implementations. A production evidence plane
needs append-only storage with independent retention, an external anchor for the chain head,
and access-controlled protected storage with its own audit trail. See the README's
*Production gaps*.
