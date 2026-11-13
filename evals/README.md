# Evaluation cases

Labelled cases that score a judgment model on the questions the platform actually asks.
Run them with:

```bash
causeway eval evals/cases --judge hosted --gates evals/gates.json --out report.json
```

See [the guide](../docs/guides/evaluate-models.md) for what the numbers mean.

## What is here

| Path | What it is |
|---|---|
| `cases/injection.jsonl` | Untrusted text, labelled for the three injection questions the guardrails ask |
| `cases/model_change.jsonl` | Model-change proposals, labelled per falsifier in the `model_change` suite |
| `cases/proposal_integrity.jsonl` | The same proposals, labelled for the `proposal_integrity` suite |
| `gates.json` | Quality limits a model must meet. **Placeholders for a risk owner to set.** |
| `baselines/sandbox.json` | The offline sandbox judge's scores, so CI notices when a change makes the fixture worse |

This seed set is small. It proves the mechanism works, not that any model is good.
Coverage comes from adding cases, and the best source of cases is what went wrong.

## Adding a case

One JSON object per line:

```json
{"id": "inj-016", "family": "injection", "state": "the text the model will see",
 "questions": "injection",
 "expect": {"injection_instruction": true, "injection_authority": false,
            "injection_exfiltration": false},
 "source": "incident INC-1234", "notes": "why the labels are what they are"}
```

- **`questions`**: a named platform set (`"injection"`, `"risk_tier"`), a falsification suite
  (`{"falsifiers": "model_change"}`), or inline questions in wire format.
- **`expect`**: a label for **every** question. `true`/`false` for a yes/no question, an option
  name for a choice, a level index for a score. Use `null` only when a careful reader genuinely
  cannot decide from the text, and say why in `notes`.
- Label from the text, never from what a model answered. A label copied from a model's answer
  measures nothing.

## The rule that keeps this useful

**Every miss becomes a case.** When a model lets something through in production, or blocks
something it should not have, add the state as a case with the right label. The dataset should
be a record of what has actually gone wrong, the same way falsification suites are.

After changing cases, refresh the sandbox baseline:

```bash
causeway eval evals/cases --judge sandbox --out evals/baselines/sandbox.json
```
