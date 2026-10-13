# Evaluation Report: SEP-1 Extraction and Model-Answer Validation

**Approach.** A deterministic parser and rule set answer each question with evidence copied from the clinical context. A rule-based validator then audits the *other model's* answer, judging the verdict and the reasoning separately. No LLM is used (see README). Data: 100 synthetic Synthea records (20 dev, 80 test; 25 per question family, 5 dev per family).

## 1. Development-set results (measured, n = 20)

| Metric | Result |
|---|---|
| Prediction accuracy vs `correct_value` | 20/20 (SIRS, INFECTION, ANTIBIOTIC, ORGAN_DYSFUNCTION: 5/5 each) |
| Agreement of our acceptability assessment with `model_answer_is_correct` | 20/20 |
| Unacceptable other-model answers caught / missed / false alarms | 3 / 0 / 0 |
| Error categories on dev | `wrong_value` 1 (EVAL-0045), `right_answer_wrong_reason` 1 (EVAL-0044), `incomplete` 1 (EVAL-0012) |
| Other model: verdict accuracy / acceptable answers (dev) | 19/20 / 17/20 |
| Records routed to human review | 8/20 (40%): 3 unacceptable answers, 2 SIRS "no" with no vitals/WBC recorded, 2 with no organ labs recorded, 1 implausible creatinine (39.6 mg/dL) |

**Evidence grounding.** Every prediction has a non-empty evidence string built only from parsed values and timestamps from the context (asserted in tests). The validator also checks every timestamp, number, medication and condition in the other model's explanation against the context. Two of the three unacceptable dev answers have the right verdict but weak reasoning, so a correct verdict alone is never accepted.

**Caveat.** 20 records is a small sample and the rules were refined with these records visible, so 20/20 is optimistic. A 95% Wilson lower bound for 20/20 is about 84% (about 57% for 5/5 per family), and even that assumes independent, unseen data, which dev is not.

## 2. Expected performance on unseen (test) data

No test labels exist, so **no test accuracy is reported.** What can be said:

- **Predictions.** Contexts follow one template and the rules are threshold comparisons with exact name matching. Likeliest test errors: unseen vocabulary (new infection or antibiotic names), not arithmetic. I expect performance near dev on this synthetic data, but that is an expectation, not a measurement.
- **Validator (descriptive counts, not accuracy).** Of 80 test answers, 58 were accepted and 22 flagged: `wrong_value` 10, `right_answer_wrong_reason` 8 (all correct verdicts with no evidence cited), `hallucinated_detail` 4, `incomplete` 0. By family (accepted / hallucinated / wrong reason / wrong value): ANTIBIOTIC 18/0/2/0, INFECTION 11/1/3/5, ORGAN_DYSFUNCTION 17/1/2/0, SIRS 12/2/1/5. All 4 hallucinations share one template (an invented lactate of 3.4 mmol/L and an "attending"), so recall on differently worded hallucinations is untested.
- **Review load.** 39/80 test records (48.8%) go to human review, mainly unacceptable answers and missing-data cases. This is high and should be tuned with abstractor feedback.

## 3. Failure cases

No pipeline prediction was wrong on dev, so cases 1 and 2 are other-model failures that the validator caught (EVAL-0045, the one wrong dev verdict, is in the table above), and case 3 is a limitation of the pipeline.

**Case 1: EVAL-0044 (INFECTION, dev), `right_answer_wrong_reason`.**
*Question and context:* "Is there explicit documentation of an infection? Do not infer." The reason for the encounter is chronic congestive heart failure; no active problems; only blood-pressure readings.
*Pipeline:* False. Evidence: "No infectious condition documented (reason: Chronic congestive heart failure (disorder); problems: none recorded)".
*Expected/identified error:* correct value is False and the other model also said False, but its explanation ("follows from the documented clinical picture and the overall trend of the recorded values") names no condition, value or time and could be pasted under any question.
*Why it failed:* right verdict with no supporting evidence at all.
*Corrective action:* label a correct verdict with no cited evidence `right_answer_wrong_reason` (partial evidence, as in Case 2, is `incomplete`) and send it to the abstractor.

**Case 2: EVAL-0012 (SIRS, dev), `incomplete`.**
*Question and context:* were two or more SIRS criteria present? Temperature 39.4 °C and WBC 1.5 ×10³/µL, both at 2008-03-14 06:49; heart rate and respiratory rate not recorded.
*Pipeline:* True; evidence lists both criteria with values and times.
*Expected/identified error:* the verdict is correct, but the other model wrote "2 SIRS criteria met" and cited only the WBC.
*Why it failed:* it claimed two criteria and substantiated one.
*Corrective action:* the validator compares the claimed count with the substantiated count, which is how this was caught.

**Case 3: EVAL-0003 (SIRS, test, unlabeled), pipeline limitation.**
*Question and context:* same SIRS question. Temperature 38.8 °C on 2007-11-08; heart rate 94/min and respiratory rate 12/min on 2007-11-14; no WBC.
*Pipeline:* True (temperature + heart rate); the other model also says True.
*Expected/identified error:* unknown; no label exists, so this record is not counted as right or wrong.
*Why it is a concern:* the two criteria were measured six days apart, and the rules accept any measurement in the encounter without a time window.
*Corrective action:* the record is already routed to human review ("met 6 days apart; confirm same clinical window"). The proper fix is to implement the SEP-1 time-window definition with clinical SME sign-off.

## 4. Production recommendations (monthly run on Azure / Fabric)

- **PHI protection.** Run only in an approved tenant with private endpoints, RBAC with least privilege, encryption at rest and in transit, and no identifiable text sent to unapproved external AI services. Keep clinical text out of general logs; restrict audit tables; follow retention policy.
- **Logging.** Per record: pseudonymous ID, pipeline/rule/prompt/model versions, timestamp, status, evidence, prediction, validation outcome and review status.
- **Accuracy monitoring.** Collect abstractor decisions and track accuracy and error rates monthly by question family, plus evidence-grounding failures and input-format drift. Promote rule or model changes only after regression on reviewed data.
- **Human review.** Always review missing or contradictory evidence, odd-unit or implausible values, near-threshold results, unsupported explanation details and wide SIRS time gaps. If an LLM extractor is added, calibrate any confidence score before using it for routing; high confidence must not bypass required review.
- **Before real use.** Validate definitions (time windows, units, antibiotic classes) with clinicians and test on real-format notes. Synthetic results do not establish clinical validity.
