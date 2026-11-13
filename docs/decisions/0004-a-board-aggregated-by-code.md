# 0004 — A board of supervisors, aggregated by code

**Status:** Accepted · **Date:** 2026-09-18

## Context

Once verification is decomposed, something has to combine the pieces into a position. The
obvious move is to hand all the evidence to a model and ask for an overall assessment.

That reintroduces every problem decomposition solved. One model holding safety, policy,
evidence quality, domain correctness and business consequence at once produces one number
dominated by whichever concern the prompt emphasised, and no way to see which.

It also creates a new problem: the aggregation itself becomes something an attacker can argue
with. A persuasive finding, or persuasive text inside a retrieved document, can move the
overall verdict.

## Decision

Run a **board**: several supervisors, each with a narrow charter, its own questions and its own
verdict. They do not see each other's answers — independent reviewers disagree in useful ways,
and that disagreement is itself a signal.

**Aggregate with a pure function.** `SupervisorBoard.deliberate()` is code:

1. A veto holder blocking at this case's risk tier is decisive.
2. Otherwise, a weighted mean **floored by the highest concern from any supervisor that filed a
   named finding**.
3. Low aggregate confidence routes to a human.
4. Strong disagreement routes to a human.
5. Otherwise, clear.

Veto authority is unequal on purpose: safety and policy can stop a consequential action;
groundedness and domain review raise concern and route to a human.

The board emits exactly two signals — `board_concern` and `board_confidence`. It authorizes
nothing.

## Alternatives rejected

**A single judge model.** See Context.

**Majority vote.** Treats every supervisor as equally informed about every question, which is
the opposite of why they are narrow. A safety supervisor that found an injection attempt should
not be outvoted by four supervisors that were looking at something else.

**A weighted mean alone.** This was the first implementation, and it was wrong: five quiet
supervisors averaged away one reviewer's concrete finding. Hence the floor rule, which is
asserted by `tests/test_supervisors.py::test_a_named_finding_cannot_be_averaged_away`.

**Letting every supervisor veto.** A board where everyone can veto blocks everything, and the
team routes around it.

## Consequences

**Good.** The same verdicts always produce the same outcome. An auditor reads the rule in a
dozen lines. No amount of persuasive text changes the arithmetic. Disagreement between narrow
reviewers becomes a first-class escalation signal rather than noise to be averaged.

**Costs.**

* More judgment calls per decision — one per supervisor. Batching keeps each one cheap, but the
  board is not free, so it runs on consequential actions rather than every read.
* Aggregation parameters (weights, thresholds, veto tiers) are tuning knobs, and badly set ones
  produce either alert fatigue or a board that never fires.
* A board is only as good as its charters. Adding a supervisor for a concern nobody owns
  produces a reviewer with nothing useful to say, which moves the mean and is worse than not
  seating it.
