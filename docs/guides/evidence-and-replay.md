# Evidence and replay

## The question this answers

Not "are there logs?" but **"can any consequential run be reconstructed without log
archaeology?"** — who asked, what the agent saw, which model, policy and tool versions ran,
what changed, and who approved it.

## Reconstruct a run

```python
from causeway.evidence.replay import reconstruct

r = reconstruct(platform.ledger, run_id)

r.principal_id          # who asked
r.status                # completed / failed / cancelled
r.decisions             # every policy decision, with reason code and version
r.actions               # every attempted side effect, with result class
r.approvals             # requested / granted / denied / expired
r.judgments             # every typed judgment, with model and answers
r.retrievals            # what was retrieved, what was withheld
r.timeline              # the raw chain, in order
r.had_side_effect       # did anything actually change
r.unverified_actions    # the reconciliation queue
```

`reconstruct` verifies the chain first. A reconstruction from a broken chain would be worse
than none, because it would look authoritative.

## `unverified_actions` is the important one

An action whose post-condition did not confirm executed but cannot be proven to have landed.
Those are neither retried blindly nor quietly forgotten: they go to reconciliation. A run whose
`unverified_actions` is non-empty is not a finished run, whatever its status says.

## Export for an incident or an audit sample

```python
from causeway.evidence.replay import forensic_package

package = forensic_package(platform.ledger, run_id)
package["chain_intact"]     # explicit integrity verdict
package["chain_head"]       # so a recipient can prove the package matches the ledger
package["integrity_error"]  # populated when the chain does not hold
```

A package taken from a broken chain says so on its face rather than silently omitting it.

## Verify a chain from outside this codebase

```bash
causeway verify-evidence run.ledger.jsonl
# chain intact: 63 events, head 4f2a91c7be03
```

The JSONL ledger writes one canonical JSON object per line, each carrying its event and its
computed hash. The CLI re-derives every hash from the line's own contents and checks the link
to its predecessor, so a reader who has never seen this code can verify it — and so can a
script in an audit pipeline.

Writes are flushed and `fsync`ed before `append` returns. An evidence record still in a buffer
when the process dies is not evidence.

## What is in the open ledger, and what is not

The ledger is widely readable. It therefore carries **low-cardinality, non-sensitive facts**:
capability, version, tier, reason code, decision id, policy version, result class, approver id,
citations, digests.

Sensitive payloads — capability arguments, judgment state, enhanced-evidence outputs — go
behind a `protected_ref` with a `protected_digest`. The open record proves *what happened*; the
protected store holds *what it said*, under its own access control.

`tests/test_evidence_chain.py` asserts that a sensitive summary passed as a capability argument
does not appear in the open payload.

## Answering "why was this allowed?"

Every decision event carries `policy_version`, `reason_code`, `tier_reached`, the obligations
and the signals. Because the policy engine is pure and packs are versioned, the answer is
reproducible: load that pack version, replay the recorded intent, get the same decision.

That property is why the engine does no I/O. An engine that called a model or read a database
mid-decision could not be replayed, and "why was this allowed?" would become "what did the
model think that afternoon?".

## Tampering

The chain makes a silent edit detectable, not impossible. Anyone with write access to a
single-writer ledger can rewrite the whole file — nothing in the ledger itself can prevent
that.

A production evidence plane needs append-only storage with independent retention and an
external anchor for the chain head, so the head a reader checks against is not stored by the
same party that could rewrite the file. That is named in
[10 Operations](../architecture/10-operations.md) and not built here.
