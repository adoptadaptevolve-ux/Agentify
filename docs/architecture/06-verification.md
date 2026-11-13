# 06 — Verification

Two mechanisms: **falsification suites**, which attack claims, and the **supervisor
board**, which convenes several narrow reviewers and combines them with code.

---

## Falsification

### The problem with "is this correct?"

Asking a model to confirm is a bad question, and it gets worse as models improve. A capable
model asked to confirm will find a reading under which the thing is fine, because that is
what the question requested. The failure is not hallucination — it is compliance.

### The discipline

For each way a claim could be wrong, ask a separate, narrow question whose **yes is bad
news**.

```
claim:      "the holdout set is uncontaminated"
falsifier:  "is there evidence in the state that holdout rows also appear in training?"
criteria:   true  -> records, users or time periods appear in both splits; the split is
                     random over rows that share an entity
            false -> the split is by entity or time, and was made before any fitting
```

### Why this works

1. **A failure mode has to be named to be checked.** Writing a falsifier forces the author
   to say what specifically could be wrong. That is most of the value before anything runs.
2. **Each answer is small enough to be true or false.** A reviewer can read one falsifier,
   one probability and one piece of state and agree or disagree. Nobody can do that with a
   paragraph of assessment.
3. **Nothing is ever proven.** A suite returns `SURVIVED` — *these falsifiers did not refute
   this claim* — never `CORRECT`. That is genuinely weaker than proof, and saying so keeps
   the platform honest.
4. **It is affordable.** The whole suite is one batched judgment call, so twenty falsifiers
   cost about what one does. `tests/test_falsify.py` asserts the batching.

### Verdicts

| Verdict | Meaning |
|---|---|
| `REFUTED` | at least one falsifier **decisively** crossed its threshold |
| `INCONCLUSIVE` | no decisive refutation, but some answer was too close to 0.5 to mean anything |
| `SURVIVED` | every answer was decisive, and none refuted |

Both halves of a refutation are required. A probability over the threshold that the judgment
plane is not confident about does not refute anything — it makes the run inconclusive, which
routes to a human. An answer of 0.49 against a threshold of 0.5 is a shrug, and 0.51 is the
same shrug; treating either as an answer is how a verifier becomes a rubber stamp in one
direction or a nuisance in the other.

### Thresholds and severity

Thresholds are **per falsifier**, because failure modes are not equally tolerable. In the
sparring partner's suite, leakage is set at 0.35 — a one-in-three chance of a contaminated
holdout should stop a promotion — while a documentation gap is set at 0.7, because blocking
on it would teach people to route around the sparring partner.

Severity weights the concern score (`ADVISORY` 0.4, `CONCERN` 0.7, `BLOCKING` 1.0), so a
blocking refutation at 0.6 outranks an advisory one at 1.0.

**A suite's concern is the maximum, not the mean.** A suite is a set of detectors, and
averaging a genuine hit against nineteen clean answers is how a real finding gets voted away
by the questions that found nothing.

### Suites are meant to grow

Every post-incident review that finds a failure the suite missed should end with a new
falsifier. `extend()` returns a new suite rather than mutating, so a bank is a versioned
record of what has actually gone wrong rather than what someone imagined might.

---

## The supervisor board

### The problem with one judge

A single model asked whether an agent's proposal is acceptable has to hold safety, policy,
evidence quality, domain correctness and business consequence in mind at once. It produces
one answer, dominated by whichever concern the prompt emphasised, and arriving as a single
number with no way to see which concern drove it.

### The design

Several supervisors, each with a narrow charter, its own questions and its own verdict.
**They do not talk to each other**, deliberately: independent reviewers who cannot see each
other's answers disagree in useful ways, and that disagreement is itself a signal.

This is the runtime form of *effective challenge* from model risk management — a standing
panel whose job is to find the problem, not to agree.

Built-in supervisors:

| Supervisor | Charter | Veto |
|---|---|---|
| `SafetySupervisor` | injection, goal hijacking, exfiltration | from R2 |
| `PolicySupervisor` | carries the deterministic decision onto the board (calls no model) | from R0 |
| `FalsificationSupervisor` | runs a suite | advises |
| `GroundednessSupervisor` | claims vs cited sources | advises |
| `RiskSupervisor` | is this more consequential than its registered tier? | advises |
| `DomainSupervisor` | a handful of Nouls for "we need someone watching for X" | configurable |

Veto authority is deliberately **unequal**. Safety and policy can stop a consequential
action outright, because their failure modes cannot be fixed afterwards. Groundedness and
domain review raise concern and route to a human, because being wrong about a claim is
recoverable and being too eager to block is its own failure. A board where everyone can veto
blocks everything; a board where nobody can is decoration.

### The aggregation is code

`SupervisorBoard.deliberate()` is a pure function of the verdicts. The same verdicts always
produce the same outcome, an auditor can read the rule in a dozen lines, and no amount of
persuasive text from any supervisor changes the arithmetic. A model that could be talked
into a different aggregation would put the board back to being one judge.

The rules, in order:

1. **A veto holder blocking at this case's risk tier is decisive.** One reviewer who is
   certain outranks a quiet majority, because the majority mostly did not look at what they
   saw.
2. **Otherwise the concern is a weighted mean, floored by the highest concern from any
   supervisor that filed a named finding.** Five quiet supervisors must not average away one
   reviewer's concrete objection — that is precisely how a board becomes a rubber stamp.
   Supervisors with nothing to say can move the mean; they cannot erase a finding.
3. **Low aggregate confidence routes to a human.** "We are not sure" is a reason to ask
   someone, not a reason to proceed.
4. **Strong disagreement routes to a human**, even when the mean is low. A board split
   between 0.1 and 0.9 has found something that averaging destroys.
5. Otherwise, clear.

### The board never authorizes

It emits exactly two numbers — `board_concern` and `board_confidence` — which the policy
engine may take into account alongside everything else it knows. Everything else the board
produced is for humans and for evidence.

A blocking board is a strong input to a deny. It is not the deny itself.

See the guide: [Write falsifiers](../guides/write-falsifiers.md).
