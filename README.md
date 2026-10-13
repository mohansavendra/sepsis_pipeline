# SEP-1 Sepsis Criteria: Extraction and Model-Answer Validation

A deterministic, evidence-grounded pipeline that (1) answers four SEP-1 abstraction questions from a synthetic (Synthea) encounter summary and (2) audits an *other* AI model's answer to the same question, classifying it as acceptable or not and, if not, saying why.

> The original assignment brief is kept in [`docs/ASSIGNMENT.md`](docs/ASSIGNMENT.md). The evaluation report is [`REPORT.md`](REPORT.md).

## Quick start

Requires Python 3.10+ (developed and tested on 3.12).

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# run the pipeline: writes predictions.csv and dev_metrics.json to the repo root
PYTHONPATH=src python -m sepsis.pipeline      # Windows PowerShell: $env:PYTHONPATH="src"; python -m sepsis.pipeline

# run the tests
pytest -q
```

Running the pipeline performs these steps in order: (1) load and validate the input records, (2) answer each clinical question with evidence, (3) validate the other model's answer, (4) write `predictions.csv`, (5) evaluate the dev split and write `dev_metrics.json` (printed to the console as well).

Optional arguments: `--records`, `--labels`, `--out` (defaults: `data/records.csv`, `data/dev_labels.csv`, `./predictions.csv`).

No API key or network access is needed. See [Design decision: no LLM](#design-decision-no-llm).

## Repository layout

```
data/records.csv, dev_labels.csv    inputs (100 records: 20 dev, 80 test)
src/sepsis/parser.py                context text -> structured facts (regex, exact-name matching)
src/sepsis/rules.py                 four rule-based answerers + thresholds
src/sepsis/validate.py              audit of the other model's answer, error classification
src/sepsis/pipeline.py              orchestration, review routing, dev evaluation, CLI
tests/                              15 tests (parser, rules, validation, pipeline, synthetic wrong-reason cases)
predictions.csv                     output for all 100 records
dev_metrics.json                    dev-set metrics + test error-type counts
REPORT.md                           evaluation report
```

## How it works

1. **Parse** (`parser.py`). The `clinical_context` is machine-generated, so a regex parser turns it into observations (value, unit, timestamp, raw line), medications and problems. Lab/vital names are matched *exactly* so look-alikes are never picked up (urine bilirubin, platelet distribution width, creatinine ratios). Unparseable lines become warnings that route the record to human review.
2. **Answer** (`rules.py`). One rule function per question family, using the assignment's thresholds:
   - **SIRS**: at least 2 distinct criteria among temperature (>38.0 or <36.0 °C), heart rate (>90), respiratory rate (>20), WBC (>12 or <4).
   - **INFECTION**: an infectious condition explicitly named in the reason for encounter or active problems. Nothing is inferred from labs, symptoms or antibiotics.
   - **ANTIBIOTIC**: an antibiotic appears under "Medications started during this encounter"; the earliest start time is reported.
   - **ORGAN_DYSFUNCTION**: creatinine >2.0 mg/dL, platelets <100 ×10³/µL, or total bilirubin >2.0 mg/dL.

   Evidence strings contain only values and timestamps copied from the context. Absent parameters are reported as "not recorded".
3. **Validate the other model** (`validate.py`). Verdict and reasoning are judged separately. Error precedence when several problems exist:
   `wrong_value > hallucinated_detail > right_answer_wrong_reason > incomplete`.
   Checks include: verdict vs. rule result; every timestamp, number+unit, medication and condition in the explanation must exist in the context; cited values must actually meet the criterion; the number of criteria claimed must be substantiated; a "yes" must cite enough valid evidence; vacuous or self-contradictory explanations are flagged. Lower-precedence problems are still listed in `validation_notes`.
4. **Route for review** (`pipeline.py`). `human_review` is True when any of these hold: the model answer is unacceptable, an explanation sentence matched no known template, a parse warning occurred, SIRS criteria were met more than 24 h apart, creatinine >15 mg/dL (likely entry/unit error), SIRS not met with parameters missing, infection found only in the reason for encounter, or SIRS met on exactly two criteria with multiple measurements, no organ-dysfunction labs recorded, or an antibiotic named without a start time.
5. **Evaluate** (`pipeline.evaluate_dev`). Dev labels are read *only* here; prediction and validation code never touches them, and `load_records` asserts that labels exist only for dev rows.

## Output: `predictions.csv`

Required columns: `record_id`, `predicted_value`, `evidence`, `model_answer_acceptable`, `error_type` (blank when acceptable).
Extra columns for reviewers: `validation_notes`, `human_review`, `review_reasons`.

## Results

Measured on the 20 labeled dev records (details and caveats in [`REPORT.md`](REPORT.md)):

| Metric | Result |
|---|---|
| Prediction accuracy | 20/20 (5/5 in each family) |
| Acceptability agreement with dev labels | 20/20 (3 unacceptable answers caught, 0 missed, 0 false alarms) |
| Human-review rate | 40% dev, 48.8% test |

Dev accuracy is **not** an estimate of test accuracy: the dev set is tiny and was used to develop the rules. Test labels are hidden, so no test accuracy is claimed. On the 80 test records the validator flags 22 other-model answers (10 `wrong_value`, 8 `incomplete`, 4 `hallucinated_detail`); these are counts of flags, not verified results.

## Design decision: no LLM

The contexts and the other model's explanations follow fixed templates, and the thresholds are numeric, so deterministic rules are reproducible, auditable and free of PHI egress. The pipeline therefore contains **no LLM calls and no prompts**, and needs no stub or fallback. Where an LLM would be added (free-text clinical notes), it should only *extract* facts into the same `ParsedContext` structure; the threshold logic and evidence checks would stay deterministic.

## Limitations

- Synthetic, templated data. Results say nothing about clinical validity or readiness for real patients.
- The infection and antibiotic term lists are hand-written; they cover every condition and medication in these 100 records but will miss new vocabulary. The "unsupported source" watch-list (lactate, attending, ...) is also hand-written and incomplete by nature. The unrecognised-sentence flag is the safety net.
- SIRS and organ-dysfunction checks consider any measurement in the encounter; they do not apply a formal SEP-1 time window. Wide gaps are flagged, not resolved.
- `right_answer_wrong_reason` never fires on the supplied data, so it is covered only by synthetic unit tests (`tests/test_wrong_reason.py`), not by real records.
- The validator only understands the other model's known explanation templates; free-form explanations would mostly fall through to human review.

## Production sketch (Azure / Microsoft Fabric, monthly batch)

PHI stays in an approved tenant (private endpoints, RBAC with least privilege, encryption at rest and in transit, no calls to unapproved external AI services). Logs hold record IDs, pipeline/rule/prompt versions, timestamps, status and decisions, not clinical text; detailed audit tables are access-restricted. Abstractor-reviewed outcomes feed monthly accuracy tracking by question family, and changes to rules are promoted only after regression on reviewed data. See [`REPORT.md`](REPORT.md) §4.
