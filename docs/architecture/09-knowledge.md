# 09 — Knowledge

## Retrieval: filter before, never after

> Filter before retrieval, not after, and never by asking the model to ignore what it should
> not have seen.

A model that has been shown an unauthorized document has been shown it. An instruction not to
use it is a request, and requests to models are not access control. So the ACL check happens
**in the query**, against the caller's real entitlements, before any chunk is loaded.

Every chunk carries the provenance that makes it citable: source, version, owner,
classification, freshness, entitlements, tenant. That is not decoration — a claim the platform
will repeat has to be traceable to a source, or the groundedness supervisor has nothing to
check it against.

**Withholding is reported, not hidden.** `RetrievalResult.withheld` counts matching chunks the
caller was not entitled to see. "There is material here you cannot see" is useful to a user and
to an auditor; hiding it makes retrieval look complete when it is not. The sparring partner
demo surfaces this: the ML engineer lacks `model-risk-read`, so the promotion standard is
withheld and the run says so.

Everything retrieved is **untrusted input**. A document is data, never policy — which is why
retrieved content goes through the safety supervisor before it can influence a consequential
action, and why nothing in the retrieval module can widen a delegation.

The reference matcher ranks by term overlap. The point of the module is the access boundary,
not the ranking function: production swaps the matcher for hybrid search and keeps the filter
exactly as it is.

## Memory: untrusted input the platform wrote to itself

That is the whole difficulty. A summary an agent stored last week reads exactly like a fact,
and nothing about its formatting says *a model made this up under time pressure*.

So every entry carries provenance, confidence and scope, and a model-generated summary is never
an authoritative record. `MemoryStore.write()` refuses `authoritative=True` on an entry whose
provenance starts with `model`. If a decision needs a fact, it reads the system of record;
memory only tells it where to look.

### Scopes have different lifecycles, explicitly

| Scope | Lifetime | Written by |
|---|---|---|
| `TASK` | 1 hour | the run, freely |
| `USER_PREFERENCE` | 1 year | the run, on the user's behalf |
| `CASE` | 90 days | the run, tied to a case's retention |
| `ENTERPRISE` | none | an ingestion process with review, never an agent mid-run |

Sharing lifecycle rules implicitly is how a user's stated preference ends up treated as an
enterprise policy.

`forget()` and `forget_scope()` exist because right-to-delete has to reach derived state too; a
deployment that caches memory downstream invalidates those caches here.

## Caching

Not implemented in the prototype, and worth stating the rule before anyone adds it: a cache key
must include the entitlement and policy context, must invalidate on ACL, policy or source
changes, and must never cross a tenant or security boundary. A retrieval cache keyed on the
query string alone is an access-control bypass with good latency.
