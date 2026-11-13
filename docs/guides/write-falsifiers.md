# Write falsifiers

A falsification suite is only as good as the failure modes someone thought to name. This is the
part of the platform that takes domain judgment, so it gets its own guide.

## The one rule

**Phrase every question so that *yes* is bad news.**

It is easy to get backwards, and a backwards falsifier is worse than none: it looks like
coverage and reports the opposite of what it finds.

```
✗  "Is the evaluation split clean?"                    — a confirmation question
✓  "Is there evidence that evaluation data overlaps
    with training data?"                               — a falsifier
```

## Write the claim down first

```python
Falsifier(
    id="holdout_leakage",
    claim="The evaluation holdout is uncontaminated by training data",
    question="Is there evidence that evaluation data overlaps with training data, or that "
             "the split was made after feature engineering?",
    ...
)
```

The `claim` field is not documentation. It is what appears in the review packet when the
falsifier fires, and it is what a human agrees or disagrees with. If you cannot state the claim
in one sentence, the falsifier is aimed at more than one thing.

## Write down where the line is

```python
criteria={
    "true":  "Records, users or time periods appear in both splits; the split is random "
             "over rows that share an entity; features were computed before splitting",
    "false": "The split is by entity or time, and was made before any fitting",
}
```

Most disagreement between a falsifier and a reviewer is disagreement about where the line is.
Writing the line down is what makes a falsifier reviewable rather than a matter of taste — and
it is what lets someone who disagrees change the criteria rather than deleting the check.

## Set the threshold from the consequence

Thresholds are per falsifier because failure modes are not equally tolerable.

| Failure mode | Threshold | Why |
|---|---|---|
| holdout leakage | 0.35 | a one-in-three chance of a contaminated holdout should stop a promotion |
| weak baseline | 0.45 | the improvement may be a statement about the baseline |
| metric mismatch | 0.50 | recoverable, worth a conversation |
| undocumented limitations | 0.70 | blocking on a missing paragraph teaches people to route around you |

That last row matters more than it looks. A suite that blocks on cosmetics loses its audience,
and then it does not catch leakage either.

## Set severity for how it should be treated

`BLOCKING` refutations make the falsification supervisor take a `BLOCK` position.
`CONCERN` and `ADVISORY` raise the concern score with progressively less weight
(1.0 / 0.7 / 0.4).

Severity is a *domain* judgment about how bad the failure is. It is not the platform's control
decision — the board and the policy engine decide that.

## Record why

```python
rationale="Leakage is the failure that most reliably survives review, because every "
          "downstream number looks better because of it.",
```

The rationale is read by humans, never by the model. It is how the next person to touch the
suite knows whether a falsifier is load-bearing or a leftover.

## Aim at one thing

```
✗  "Are there problems with the evaluation methodology?"
✓  "Is the evaluation period too short, too old, or unusual compared with the conditions
    the model will run in?"
```

A broad falsifier gets a middling probability that means nothing. Since the whole suite is one
judgment call, splitting a broad question into three narrow ones costs essentially nothing —
so split it.

## Understand what INCONCLUSIVE means

A refutation requires crossing the threshold **and** a decisive answer. A probability of 0.51
against a threshold of 0.5 is a shrug, not a finding, and the suite returns `INCONCLUSIVE`
rather than pretending either way.

`INCONCLUSIVE` has to go somewhere. In the sparring partner it raises concern and routes to a
human. A team that treats it as a pass has removed the honesty the design bought.

## Grow the suite after every incident

```python
MODEL_CHANGE_SUITE.extend(
    Falsifier(
        id="feature_store_staleness",
        claim="Features are as fresh at serving time as they were in evaluation",
        question="Is there evidence the feature store lags behind the events the model scores?",
        threshold=0.4,
        severity=Severity.BLOCKING,
        rationale="Added after the 2026-08 incident: offline features were hours fresher "
                  "than online ones and nothing in the suite looked.",
    )
)
```

`extend()` returns a new suite rather than mutating, so the bank becomes a versioned record of
what has actually gone wrong rather than what someone imagined might.

That is the real measure of a suite's health: **when did it last grow, and what caused it?**

## Review them as text

```bash
causeway falsifiers model_change
```

A suite should be readable by a domain expert who does not read Python. If it is not, the
questions are probably too clever.
