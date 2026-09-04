# Evidence model

Topic states describe demonstrated ability, not exposure:

- `unseen`: no useful evidence yet.
- `introduced`: the learner received an explanation or worked example.
- `retrievable`: the learner produced unaided free recall or explanation.
- `applicable`: the learner independently succeeded on a new application.
- `robust`: delayed retrieval plus independent transfer succeeded.

MCQ performance alone may diagnose or reinforce but cannot establish `applicable` or `robust`. Recognition alone does not establish `retrievable` either. Confidence changes interpretation, not correctness: a confident error is a misconception signal; an unconfident correct answer may need another independent check.

For `finish`, represent strong evidence as objects in `demonstrated_abilities`:

```json
{
  "ability": "Explained why the gradient is the linear coefficient of first-order change",
  "evidence_type": "recall",
  "independent": true,
  "delayed": false
}
```

Allowed `evidence_type` values are `recall`, `explanation`, `application`, `transfer`, and `mcq`. A string may be preserved as a note but cannot by itself justify a state above `introduced`.

For application or transfer, also provide:

```json
{
  "attempted": true,
  "successful": true,
  "independent": true,
  "delayed": false,
  "summary": "Applied the model to a new case"
}
```

`retrievable` requires independent recall or explanation. `applicable` additionally requires successful independent application or transfer. `robust` requires delayed independent recall/explanation and delayed successful independent transfer. A requested state unsupported by the payload must be rejected rather than silently inflated.

Review scores:

- `0`: blank, guessed, or fundamentally wrong
- `1`: major hint required
- `2`: independently correct with a minor gap
- `3`: independently correct and transferred

A later score of 0 or 1 may reduce state. A delayed score of 3 can establish robustness when the review includes transfer.
