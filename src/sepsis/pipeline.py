"""End-to-end run:  python -m sepsis.pipeline   (from the repo root, with PYTHONPATH=src)

1. load + validate records   2. rule-based prediction + evidence
3. validate the other model's answer   4. write predictions.csv   5. evaluate on dev only

Dev labels are used ONLY in `evaluate_dev`; nothing in prediction/validation reads them.
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

from .parser import load_records, parse_context
from .rules import answer
from .validate import validate_model_answer

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPREAD_HOURS = 24
IMPLAUSIBLE = {"creatinine": 15.0}  # mg/dL; above this the value is likely a unit/entry error


def _review_flags(family, pc, rule) -> list[str]:
    flags = list(rule.review_flags)
    if family == "SIRS" and rule.value:
        # SEP-1 criteria should co-occur; flag when the met measurements are far apart in time
        from .validate import SIRS_TESTS
        times = sorted(o.time for k, t in SIRS_TESTS.items() for o in pc.obs(k) if t(o.value))
        if len(times) >= 2:
            gap = (pd.Timestamp(times[-1]) - pd.Timestamp(times[0])).total_seconds() / 3600
            if gap > SPREAD_HOURS:
                flags.append(f"SIRS criteria met {gap / 24:.0f} days apart; confirm same clinical window")
    for key, limit in IMPLAUSIBLE.items():
        for o in pc.obs(key):
            if o.value > limit:
                flags.append(f"implausible {key} {o.value:g} {o.unit} at {o.time}; verify unit/entry")
    return flags


def run(records_path, labels_path=None) -> pd.DataFrame:
    df = load_records(records_path, labels_path)
    rows = []
    for _, r in df.iterrows():
        pc = parse_context(r.clinical_context)
        rule = answer(r.question_family, pc)
        v = validate_model_answer(r.question_family, pc, rule, r.clinical_context,
                                  bool(r.model_answer_value), r.model_answer_explanation)
        flags = _review_flags(r.question_family, pc, rule) + v.review_flags + pc.warnings
        if v.error_type:
            flags.append(f"model answer unacceptable ({v.error_type})")
        rows.append({
            "record_id": r.record_id,
            "predicted_value": bool(rule.value),
            "evidence": rule.evidence_text,
            "model_answer_acceptable": v.acceptable,
            "error_type": v.error_type,
            # extra columns (beyond the required five) for the reviewer
            "validation_notes": v.reasons_text,
            "human_review": bool(flags),
            "review_reasons": " | ".join(flags),
        })
    return pd.DataFrame(rows)


def evaluate_dev(preds: pd.DataFrame, records_path, labels_path) -> dict:
    df = load_records(records_path, labels_path)
    dev = df[df.split == "dev"].merge(preds, on="record_id")
    truth = dev.correct_value.astype(bool)
    out = {
        "n_dev": len(dev),
        "prediction_accuracy": float((dev.predicted_value == truth).mean()),
        "prediction_accuracy_by_family": {
            f: float((g.predicted_value == g.correct_value.astype(bool)).mean())
            for f, g in dev.groupby("question_family")},
        "acceptability_agreement": float(
            (dev.model_answer_acceptable == dev.model_answer_is_correct.astype(bool)).mean()),
        "acceptability_confusion": {
            "true_unacceptable_caught": int(((~dev.model_answer_acceptable) & (~dev.model_answer_is_correct.astype(bool))).sum()),
            "true_unacceptable_missed": int((dev.model_answer_acceptable & (~dev.model_answer_is_correct.astype(bool))).sum()),
            "false_alarms": int(((~dev.model_answer_acceptable) & dev.model_answer_is_correct.astype(bool)).sum()),
        },
        "dev_error_types": dev.loc[dev.error_type != "", "error_type"].value_counts().to_dict(),
        "dev_human_review_rate": float(dev.human_review.mean()),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", default=ROOT / "data/records.csv")
    ap.add_argument("--labels", default=ROOT / "data/dev_labels.csv")
    ap.add_argument("--out", default=ROOT / "predictions.csv")
    a = ap.parse_args()

    preds = run(a.records, a.labels)
    preds.to_csv(a.out, index=False)
    metrics = evaluate_dev(preds, a.records, a.labels)
    metrics["test_error_types"] = preds[preds.record_id.isin(
        load_records(a.records).query("split == 'test'").record_id)].query("error_type != ''").error_type.value_counts().to_dict()
    (pathlib.Path(a.out).parent / "dev_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"wrote {a.out} ({len(preds)} rows)")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
