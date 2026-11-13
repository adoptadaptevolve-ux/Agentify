# 05 — The judgment plane

## What it is for

Wherever the platform needs a judgment — is this injection, is this claim supported by the
cited source, which capability does this request mean, how consequential is this action —
it asks a **System One** model a narrow *typed* question and branches on the typed answer.

It does not ask a chat model for prose and parse the prose.

## The three primitives

| Type | Question | Returns |
|---|---|---|
| `Noul` | "is this true?" | a calibrated probability in [0, 1] |
| `Choice` | "which of these?" | one option, the full distribution, a confidence |
| `Score` | "which level?" | a probability-weighted position on an ordered rubric, a legend, a confidence |

These mirror TypeSafe's System One primitives, because that is the shape the platform
needs and because it keeps the hosted adapter thin.

## Why this shape, specifically

**The answer space is closed.** A `Choice` can only return an option the platform
supplied. Intent routing therefore cannot name a capability that is not registered, and a
destination cannot be invented. Compare with structured output from a general model, where
the schema constrains the *shape* but the platform still has to validate that the value
means something.

**Uncertainty is a number.** `confidence` is a statistic computed from the distribution the
answer already gives you: concentrated means certain, spread means uncertain. That is a
value policy can threshold on, which is how the platform routes genuinely uncertain cases
to humans instead of guessing. Prose hedging ("this seems likely, though it's hard to say")
cannot be thresholded.

**Questions batch.** Independent questions about the same state evaluate in parallel in one
call, so decomposing a judgment into twenty narrow questions costs roughly what one broad
question costs. That is the property that makes falsification suites affordable — and a
check that is cheap runs on every action, while a check that is expensive runs only on the
ones that already look suspicious, which are not the ones that need it.

**Decomposition beats a broad question.** "Is this spam?" is a question a model can be
talked out of. "Does this message ask the reader to supply credentials?" is one it can
answer and a human can check. Weighting and combining the atomic answers happens in code,
so when priorities shift you adjust weights rather than rewriting a prompt.

## None of this is authorization

A judgment is evidence a deterministic policy decision may take into account. It is never
itself a decision. The platform's signal names are a closed set (`ALLOWED_SIGNALS`), and
the policy engine ignores anything else.

A judgment plane that cannot answer raises `JudgmentUnavailable`. It never returns a
default, a zero or a "probably fine", because every caller treats an unavailable judgment
as a reason to fail closed.

## The platform's own question sets

These live in `judgment/questions.py` rather than at each call site, because the wording of
a guardrail question is a control and should be reviewable in one place.

* **Injection** — three questions: does the content contain instructions addressed to an AI
  system; does it claim authority it cannot have; does it ask for data to be sent somewhere
  it names.
* **Egress** — does the outbound content carry data above the destination's permitted
  class; is it encoded or obfuscated in a way that does not match its stated purpose.
* **Routing** — a `Choice` whose option set *is* the capability registry.
* **Risk tier** — a `Score` over the R0–R5 rubric, whose indexes line up with the enum.
* **Groundedness** — one question per claim, because a response that is 90% supported and
  10% invented is not 90% good, and only per-claim questions find the 10%.

Each family collapses to one signal by taking its **maximum**. These are detectors: one
strong hit is the finding, and averaging it against the questions that found nothing is how
detectors get silenced.

## The offline judge

Every golden path in this repository runs with no API key and no network, because the
default judgment plane is a deterministic fixture.

That is a platform decision, not a convenience:

* **CI tests the platform, not the model.** An assertion that a prohibited action is denied
  should fail when the transaction guard regresses, never because a model phrased something
  differently today. Model quality is measured by the eval harness, against recorded cases,
  on its own schedule.
* **The local sandbox is genuinely local.** A developer runs the whole paved road before
  they have credentials.
* **Judgment is swappable.** The platform depends only on the `SystemOne` protocol, so
  changing providers is a constructor argument.

The fixture is cue-driven: a cue says "when a question that looks like *this* is asked about
a state that looks like *that*, push the answer *this* far in *that* direction".
Probabilities come from a logistic over matched weights, so it produces genuine uncertainty
rather than 0.0 and 1.0, and code that thresholds on confidence gets exercised.

It is a fixture. It is not a model, it does not generalise, and nothing in the platform
behaves differently because it is the judge.

## Recording

`RecordingJudge` wraps any `SystemOne` so every call lands in the evidence chain. Wrapping
rather than instrumenting each call site means a judgment cannot be made without being
recorded — there is no code path that asks a question and forgets to log it, because the
logging *is* the object the platform holds.

The state is recorded as a digest plus a protected reference, not inline: state can contain
retrieved customer data, and the ledger should not be the easiest place to read it.
