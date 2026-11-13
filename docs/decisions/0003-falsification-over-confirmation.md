# 0003 — Falsification, not confirmation

**Status:** Accepted · **Date:** 2026-09-18

## Context

Verification in agentic systems usually means asking a model to check the work: "is this
correct?", "does this look right?", "review this plan".

This fails in a way that gets *worse* as models improve. A capable model asked to confirm will
find a reading under which the thing is fine, because that is what the question requested. The
failure mode is not hallucination — it is compliance. And because the answer arrives as a
paragraph of assessment, a human reviewer cannot efficiently disagree with it.

## Decision

Never ask for confirmation. For each way a claim could be wrong, ask a separate, narrow
question whose **yes is bad news**.

```
claim:      "the holdout set is uncontaminated"
falsifier:  "is there evidence that holdout rows also appear in training?"
```

A `FalsificationSuite` is a bank of such questions with per-falsifier thresholds and
severities. It returns `SURVIVED`, `REFUTED` or `INCONCLUSIVE` — **never `CORRECT`**. The
vocabulary is deliberate: the platform can report that it failed to break a claim, and that is
genuinely weaker than proof.

A refutation requires both crossing the threshold *and* a decisive answer. A probability over
the threshold that the judgment plane is not confident about routes to a human rather than
counting as a finding.

## Alternatives rejected

**LLM-as-judge with a rubric.** Better than a bare "is this right?", but still one broad
judgment producing one number, dominated by whichever rubric item the prompt emphasised, and
still unreviewable at the item level.

**Self-critique loops.** "Now criticise your own answer." The same model, the same context, the
same blind spots. Produces plausible-looking critique that systematically misses what the model
got wrong for reasons it cannot see.

**Deterministic rule checks only.** Excellent where the rule is expressible. Most of the
failure modes worth catching — a weak baseline, a metric that does not track the outcome — are
judgments about a document, not properties of a schema.

## Consequences

**Good.** Every check is individually reviewable: one question, one probability, one piece of
state. Writing a falsifier forces the author to name the failure mode, which is most of the
value before anything runs. Suites become a versioned record of what has actually gone wrong.

**Costs.**

* Coverage is exactly the failure modes someone thought to write down. A suite that has never
  been extended after an incident is a suite that is quietly out of date.
* A badly aimed falsifier produces confident noise. Thresholds and severities have to be tuned,
  and tuning them is a risk decision, not an engineering one.
* `INCONCLUSIVE` is a real outcome that has to go somewhere. A team that treats it as a pass
  has removed the honesty the design bought.
