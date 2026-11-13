# 08 — The agent runtime

## An explicit graph, not an open-ended plan

Regulated and consequential journeys run as a state machine. This is the biggest
reliability decision in the platform, and it is worth being clear about both sides.

**It costs flexibility.** A workflow can only go where its edges go, so a genuinely novel
situation ends the run rather than improvising through it.

**It buys everything else.** A graph can be checkpointed, so a run survives a restart. It
can be paused for a human decision and resumed without losing causal state. It can declare,
per step, whether a side effect happens and what compensates for it. It can be replayed,
because the control flow is data rather than whatever the model decided that afternoon. And
it can be reviewed by someone who is not an ML engineer, because it is a diagram.

Open-ended reasoning still happens — **inside a step**. A step may call a model, weigh
options and draft. What it may not do is decide what the *next* step is outside the declared
edges. A step that tries fails the run with a clear error rather than being allowed a new
edge at runtime.

`causeway graph` renders any workflow as Mermaid, and `Workflow.irreversible_steps` names
the commitment boundary: everything before it can be unwound, everything after cannot. Worth
knowing before a run, not during one.

## Budgets

An agent without a budget has an unbounded worst case. Budgets are the blast-radius limit
that makes a runaway run a bounded incident, not a cost-control feature bolted on afterwards.

`max_steps` · `max_tool_calls` · `max_judgment_calls` · `max_subagents` · `max_wall_time` ·
`max_spend` · `max_retrieved_chunks`

Every dimension fails closed: exhaustion raises. A run that continues past its budget has no
budget.

Defaults are deliberately small. Raising one is an explicit line in a workflow definition,
which makes the ceiling a reviewable decision rather than an accident of whatever the default
was.

Judgment calls are counted **per call, not per question**, because batching twenty falsifiers
into one call is the behaviour the platform wants and the budget must not punish it.

## Checkpoints and interrupts

The runner checkpoints **before and after** every side-effecting step. Before, so a crash
between the checkpoint and the receipt is recoverable rather than ambiguous.

Pausing is not an error path. When a step needs a human decision, the transaction guard raises
`ApprovalRequired`; the runner catches it, checkpoints, and marks the run `PAUSED` with the
approval it is waiting on. `resume()` re-enters the same step once the decision exists.

The step body knows nothing about this. It is written as though the call simply happens, and
the platform supplies the pause, the checkpoint and the resume. Re-entering is safe because
the step's idempotency key means re-running it cannot duplicate what it already did —
`tests/test_sparring_partner_e2e.py` asserts that the record step runs twice and opens exactly
one case.

## Cancellation

`cancel()` is a supported operation, not an exception. Kill switches and safe shutdown depend
on there being a way to stop a run that leaves the evidence chain intact and says why.

## Subagents

`AgentRun.spawn()` produces a child with strictly narrower authority, its own run id and its
own budget, sharing the parent's evidence chain so the causal trace stays whole. The
attenuation check runs at mint time, so a compromised subagent cannot reach a capability its
parent did not hold.

## Framework adapters

Nothing above standardises on one agent framework as the enterprise architecture. The
`Workflow`/`Step` runtime here is a reference implementation of the Agent Runtime contract.
LangGraph, Semantic Kernel, AutoGen, CrewAI or an internal runtime plug into the same
contracts: what must not change is the execution contract, the security model, the evidence
schema and the developer experience.
