import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sepsis.parser import load_records, parse_context
from sepsis.rules import answer


def test_rules_match_dev_labels():
    df = load_records(ROOT / "data/records.csv", ROOT / "data/dev_labels.csv")
    dev = df[df.split == "dev"]
    for _, r in dev.iterrows():
        res = answer(r.question_family, parse_context(r.clinical_context))
        assert res.value == bool(r.correct_value), r.record_id
        assert res.evidence, r.record_id


def test_sirs_two_criteria_eval0001():
    df = load_records(ROOT / "data/records.csv")
    r = df.iloc[0]
    res = answer(r.question_family, parse_context(r.clinical_context))
    assert res.value is True
    assert "0.8" in res.evidence_text and "38.4" in res.evidence_text