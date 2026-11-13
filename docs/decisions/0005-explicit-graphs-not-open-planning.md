# 0005 — Explicit workflow graphs, not open-ended planning

**Status:** Accepted · **Date:** 2026-09-18

## Context

The appeal of an agent is that it works out what to do. The difficulty is that a system whose
control flow is decided at runtime by a model cannot be checkpointed meaningfully, cannot be
resumed after a human decision without ambiguity, cannot be replayed, and cannot be reviewed
by anyone who is not reading model outputs.

For a consequential journey — one that moves money, contacts a customer, or changes an account
— those four properties are not optional.

## Decision

Regulated and consequential journeys run as an **explicit graph**. Steps declare their edges,
whether they have a side effect, and what compensates for them. A step that returns a
destination outside its declared edges fails the run.

Open-ended reasoning happens **inside a step**: a step may call a model, weigh options and
draft. What it may not do is choose the next node.

## Alternatives rejected

**Free-form ReAct / planner loops.** Excellent for exploration, wrong for a journey that must
be auditable. A plan that changes between the approval and the execution is precisely what
digest binding exists to prevent.

**A graph generated per run by a model.** Has the shape of a graph and none of the benefits:
it cannot be reviewed before it runs, and "why did it do that?" still requires reading model
output.

**Hard-coded procedural flow.** Works, and is what most teams do first. It loses the
checkpoint/resume machinery, the commitment-boundary analysis and the diagram, all of which the
platform provides once the flow is data.

## Consequences

**Good.** Runs survive restarts. A run pauses for a human and resumes in the same step, safely,
because of the idempotency key. `causeway graph` renders the journey for a non-engineer.
`Workflow.irreversible_steps` names the commitment boundary before the run, not during it.

**Costs.**

* A genuinely novel situation ends the run rather than improvising. For an exploratory
  workload that is the wrong trade, and such workloads should stay at R0/R1 where they cost
  nothing.
* Graphs grow edges. A workflow with thirty steps and dense edges is no more reviewable than a
  planner; keeping them small is a discipline the platform does not enforce.
* Teams must think about their journey up front. That is usually good and occasionally
  premature.
