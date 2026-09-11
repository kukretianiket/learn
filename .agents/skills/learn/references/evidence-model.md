# Evidence model

Topic states describe demonstrated ability, not exposure:

- `unseen`: no useful evidence yet.
- `introduced`: the learner received an explanation or worked example.
- `retrievable`: the learner produced unaided free recall or explanation.
- `applicable`: the learner independently succeeded on a new application.
- `robust`: delayed retrieval plus independent transfer succeeded.

MCQ performance alone may diagnose or reinforce but cannot establish `applicable` or `robust`. Recognition alone does not establish `retrievable` either. Confidence changes interpretation, not correctness: a confident error is a misconception signal; an unconfident correct answer may need another independent check.

## Evidence must match the claimed ability

Judge what the task actually required, not whether it was labeled `Check`, `explanation`, or `application`. Correctly substituting supplied definitions into a supplied target establishes at most that procedural skill. It does not establish recall of those definitions, understanding of their physical meaning, choice of the model, or transfer to a new situation. A cosmetic change of numbers or notation is not conceptual transfer.

Before recording a structured ability, state precisely what the learner had to choose, explain, or infer without the answer being supplied. Record only that ability. If the lesson targets a conceptual model and a task tested only incidental algebra, retain the procedural observation as a plain note; do not use it to justify conceptual mastery. If algebra is the actual learning goal, assess and record that narrower goal honestly. Routine calculation errors likewise do not by themselves prove a conceptual misconception or justify downgrading conceptual state.

An ordinary teaching check is formative, not an unannounced scored evaluation. Its response may inform the next teaching move; if retained as evidence, preserve its actual scope and the supplied scaffolding. Use an announced assessment when stronger mastery evidence is needed. Independence is relative to the claimed ability: a provided formula can permit an independent modeling decision while ruling out a claim of unaided formula recall.

For `finish`, represent strong evidence as objects in `demonstrated_abilities`:

```json
{
  "ability": "Explained why the gradient is the linear coefficient of first-order change",
  "evidence_type": "recall",
  "independent": true,
  "delayed": false,
  "evidence_refs": ["current"]
}
```

Allowed `evidence_type` values are `recall`, `explanation`, `application`, `transfer`, and `mcq`. Every structured evidence item must reference recorded learner Markdown. Use `current` for the learner response in the current runtime turn, `turn:<exact turn_id>` when that identifier is known, or a canonical learner `message_id`. Python resolves selectors to message IDs before storing the assessment. A string may be preserved as a note but cannot by itself justify a state above `introduced`.

For application or transfer, also provide:

```json
{
  "attempted": true,
  "successful": true,
  "independent": true,
  "delayed": false,
  "summary": "Applied the model to a new case",
  "evidence_refs": ["current"]
}
```

`retrievable` requires independent recall or explanation. `applicable` additionally requires successful independent application or transfer. `robust` requires delayed independent recall/explanation and delayed successful independent transfer. A requested state unsupported by the payload must be rejected rather than silently inflated.

Review scores:

- `0`: blank, guessed, or fundamentally wrong
- `1`: major hint required
- `2`: independently correct with a minor gap
- `3`: independently correct and transferred

A score by itself changes only the fixed review interval. It never promotes or downgrades mastery. To record a semantic review, supply the review evidence payload described in `state-schemas.md`; it is checked by the same evidence validator as lesson completion.

A lower state takes effect only when the semantic assessment explicitly sets `reassessment: true` and supplies `contrary_evidence_refs` resolving to learner responses. Otherwise, later explanations and partial checks preserve stronger prior state, abilities, and unresolved gaps. Robustness always requires recorded delayed independent retrieval and transfer evidence; a score of 3 is not a substitute.
