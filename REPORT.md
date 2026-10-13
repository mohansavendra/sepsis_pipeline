# Evaluation Report: SEP-1 Extraction and Model-Answer Validation

**Approach.** Deterministic parser and rules answer each question with evidence copied from the context; a rule-based validator audits the other model's answer (verdict and reasoning separately). No LLM is used (see README). Data: 100 synthetic records (20 dev, 80 test; 25 per question family, 5 dev per family).

## 1. Development-set results (measured, n = 20)

| Metric | Result |
|---|---|
| Prediction accuracy vs `correct_value` | 20/20 (SIRS, INFECTION, ANTIBIOTIC, ORGAN_DYSFUNCTION: 5/5 each) |
| Acceptability agreement with `model_answer_is_correct` | 20/20 |
| Unacceptable answers caught / missed / false alarms | 3 / 0 / 0 |
| Dev error types found | `incomplete` 2, `wrong_value` 1 |
| Other model's verdict accuracy (dev) | 19/20; acceptable by label 17/20 |
| Records routed to human review | 8/20 (40%) |

Evidence grounding: every prediction carries a non-empty evidence string built only from parsed values and timestamps (asserted by tests). Correct verdicts are not enough for acceptability: two of the three dev failures have the right verdict but bad reasoning.

**Caveat.** 20/20 is a small sample, and the rules and validator were refined with these records visible, so it is optimistic. A 95% Wilson lower bound for 20/20 is about 84% (about 57% for 5/5 per family), and that bound assumes independent, unseen data, which dev is not.

## 2. Expected performance on unseen (test) data

No test labels exist, so **no test accuracy is reported**. What can be said:

- *Predictions.* Contexts follow one template and the rules are plain threshold comparisons with exact name matching, so errors on test are most likely to come from vocabulary not seen in dev (new infection or antibiotic names), not from arithmetic. I expect performance close to dev on this synthetic data, but this is an expectation, not a measurement. Real-world notes would need re-validation.
- *Validator.* On the 80 test records it flagged 22 other-model answers (10 `wrong_value`, 8 `incomplete`, 4 `hallucinated_detail`); 58 were accepted. The 4 hallucinations share one template (an invented lactate of 3.4 mmol/L and an "attending"), so recall on differently worded hallucinations is untested. Pipeline and other-model verdicts disagree on 10/80 test records, consistent with the 10 `wrong_value` flags; that is agreement between two systems, not accuracy.
- *Review load.* 39/80 test records (48.8%) are routed to human review, which is high and should be tuned with abstractor feedback before real use.

## 3. Failure cases

No pipeline prediction was wrong on dev, so the cases below are failures of the *other model* that the validator caught (1, 2) and a limitation of the pipeline itself (3).

**1. EVAL-0012 (SIRS, dev), `incomplete`.** *Question:* were two or more SIRS criteria present? *Context:* temperature 39.4 °C and WBC 1.5 ×10³/µL, both at 2008-03-14 06:49. *Pipeline:* True, 2 criteria (temperature, WBC) with values and times. *Other model:* True, "2 SIRS criteria met" but cites only WBC. *Why it failed:* right verdict, but it claims two criteria and substantiates one. *Fix:* none needed in the pipeline; the validator compares the claimed count with the substantiated count. Production action: send such answers to abstractors as "evidence incomplete", not "wrong".

**2. EVAL-0044 (INFECTION, dev), `incomplete`.** *Question:* is an infection explicitly documented? *Context:* reason "Chronic congestive heart failure", no active problems, only blood pressure readings. *Pipeline:* False ("No infectious condition documented"). *Other model:* False, explanation "follows from the documented clinical picture and the overall trend of the recorded values". *Why it failed:* verdict correct, but the explanation names no evidence and cites a "trend" that cannot exist. *Fix:* the validator flags explanations that match no evidence template; a correct verdict alone is never accepted.

**3. EVAL-0003 (SIRS, test, unlabeled), pipeline limitation.** *Context:* temperature 38.8 °C on 2007-11-08, heart rate 94/min on 2007-11-14 (six days apart), RR 12, no WBC. *Pipeline:* True (2 criteria), and the other model agrees. *Concern:* SIRS criteria are normally expected to co-occur; the rules accept any measurement in the encounter and do not apply a time window. *Action taken:* the pipeline routes it to human review ("met 6 days apart; confirm same clinical window"). *Proper fix:* implement the SEP-1 time-window definition and confirm it with clinical SMEs; until then the true answer is unknown, so this record is not counted as correct or incorrect. (A related case, EVAL-0082, is flagged for an implausible creatinine of 39.6 mg/dL; the verdict is unaffected because 3.1 mg/dL already exceeds 2.0.)

## 4. Production recommendations (monthly run on Azure / Fabric)

- **PHI.** Run only in an approved tenant: private endpoints, RBAC with least privilege, encryption at rest and in transit, no identifiable text to unapproved external AI services. Keep clinical text out of general logs; restrict and monitor detailed audit tables; apply retention and deletion policy.
- **Traceability.** Per record, log pseudonymous ID, pipeline/rule/prompt/model versions, timestamp, status, evidence, prediction, validation outcome and review status.
- **Accuracy monitoring.** Collect abstractor decisions, then track accuracy and error rates per question family, evidence-grounding failures and input-format drift each month. Do not promote rule or model changes without regression on reviewed data.
- **Human review.** Always review missing or contradictory evidence, malformed or odd-unit values, near-threshold results, unsupported explanation details and wide SIRS time gaps. If an LLM extractor is added for free text, calibrate any confidence score before using it for routing, and never let high confidence bypass required review.
- **Before real use.** Validate definitions (time windows, units, antibiotic classes) with clinical staff, and test on real-format notes. Synthetic results do not establish clinical validity.
