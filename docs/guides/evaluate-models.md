# Evaluate models

The test suite runs against a deterministic fake judge on purpose
([ADR 0007](../decisions/0007-offline-judge-by-default.md)): CI tests the platform, not a
model's mood. The cost is that a green test suite says nothing about whether a real model is
any good at the platform's questions. `causeway eval` measures that.

## Run it

```bash
# Against the hosted model (needs TYPESAFE_API_KEY and the systemone extra)
causeway eval evals/cases --judge hosted --gates evals/gates.json --out report.json

# Compare with last week's report, and fail if anything got worse by more than 0.02
causeway eval evals/cases --judge hosted --baseline last-week.json --out report.json
```

The command exits non-zero when a gate fails or a metric regressed, so it can gate a model
upgrade the same way tests gate a code change.

`--judge hosted` never falls back to the fake judge. A missing key is an error, because
scoring the fixture while calling it the hosted model would report a quality nobody measured.

## What it asks

Exactly what production asks. A case names a platform question set (`"injection"`) or a
falsification suite (`{"falsifiers": "model_change"}`), so the model is scored on the same
wording, in the same batched call, at the same thresholds the runtime uses. Rewording a
production question changes what the eval measures, which is the point.

## What the numbers mean

| Metric | Question it answers | Why it matters here |
|---|---|---|
| accuracy | How often was the answer right at the platform's threshold? | The headline, and not enough on its own |
| brier | How far were the probabilities from the truth? | Policy thresholds on these probabilities, so confident mistakes cost more than hesitant ones |
| calibration_error | Does "0.8" mean right 80% of the time? | A badly calibrated model makes every threshold in every policy pack wrong |
| miss_rate | Of the real injections or refutations, how many were called fine? | The expensive error for a guardrail |
| false_alarm_rate | Of the clean cases, how many were flagged? | Every false alarm is reviewer time, and too many teach people to route around the platform |
| undecided_rate | How often was the answer too close to 0.5 to act on? | Undecided answers route to a human in production |
| error_rate | How often could the model not answer at all? | Unavailable judgments fail closed, so outages block work |

Each is reported overall, per family and per question, alongside the most confidently wrong
answers so a person can read the failures rather than just the totals.

## Gates are a risk decision

`evals/gates.json` sets limits overall and per family. `max_miss_rate: 0.05` on injection
means accepting that one injection in twenty gets past the first line of defence. That is a
decision for whoever owns the risk, made the same way as a policy threshold
([ADR 0008](../decisions/0008-policy-as-a-signed-artifact.md)), and the values shipped here
are placeholders for them to set.

A gate on a family with no cases fails rather than passing vacuously: a gate that quietly
stops applying is how coverage erodes.

## When it runs

* **Every PR**, against the fake judge and a committed baseline
  (`evals/baselines/sandbox.json`). This does not measure a model. It checks that the dataset
  is valid, and that a change has not made the fixture worse at the platform's own questions.
  Its first run found that the sandbox's exfiltration cue had never matched the production
  question's wording.
* **Weekly and on demand**, against the hosted model (`.github/workflows/eval.yml`), when a
  `TYPESAFE_API_KEY` repository secret is set. The report is kept as a build artifact; use one
  as `--baseline` for the next.

## Growing the dataset

The seed set in `evals/cases/` is small and hand-labelled. It proves the mechanism, not the
model. See [`evals/README.md`](../../evals/README.md) for the case format and labelling rules.
The rule that matters: **every miss becomes a case**. When a model lets something through, or
blocks something it should not have, add the state with the right label.

Harvesting cases from production is the next step. The evidence ledger records each judgment
with a digest of its state and a protected reference to the state itself, so an export with
access to protected storage can turn reviewed production judgments into labelled cases.
