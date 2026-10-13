import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sepsis.pipeline import run, evaluate_dev

REC, LAB = ROOT / "data/records.csv", ROOT / "data/dev_labels.csv"
PREDS = run(REC, LAB).set_index("record_id")


def err(rid):
    return PREDS.loc[rid, "error_type"] if isinstance(PREDS.loc[rid, "error_type"], str) else ""


def test_output_shape_and_columns():
    assert len(PREDS) == 100
    for c in ["predicted_value", "evidence", "model_answer_acceptable", "error_type"]:
        assert c in PREDS.columns
    assert PREDS.evidence.str.len().gt(0).all()
    ok = PREDS[PREDS.model_answer_acceptable]
    assert (ok.error_type.fillna("") == "").all()


def test_dev_metrics():
    m = evaluate_dev(PREDS.reset_index(), REC, LAB)
    assert m["prediction_accuracy"] == 1.0
    assert m["acceptability_agreement"] == 1.0


def test_hallucinated_lactate_detected():
    for rid in ["EVAL-0001", "EVAL-0009", "EVAL-0043", "EVAL-0094"]:
        assert err(rid) == "hallucinated_detail", rid
    assert "lactate" in PREDS.loc["EVAL-0001", "validation_notes"]


def test_dev_error_types():
    assert err("EVAL-0045") == "wrong_value"
    assert err("EVAL-0012") == "incomplete"    # right verdict, cites 1 of the 2 claimed criteria
    assert err("EVAL-0044") == "incomplete"    # right verdict, vacuous explanation


def test_correct_verdict_does_not_imply_acceptable():
    assert PREDS.loc["EVAL-0021", "predicted_value"] == False  # noqa: E712
    assert not PREDS.loc["EVAL-0021", "model_answer_acceptable"]


def test_review_routing():
    assert PREDS.loc["EVAL-0003", "human_review"]            # criteria 6 days apart
    assert "implausible creatinine" in PREDS.loc["EVAL-0082", "review_reasons"]
