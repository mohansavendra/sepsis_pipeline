import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sepsis.parser import load_records, parse_context


def test_load_and_shape():
    df = load_records(ROOT / "data/records.csv", ROOT / "data/dev_labels.csv")
    assert len(df) == 100 and (df.split == "dev").sum() == 20
    assert df.loc[df.split == "test", "correct_value"].isna().all()


def test_all_contexts_parse_without_warnings():
    df = load_records(ROOT / "data/records.csv")
    for rid, ctx in zip(df.record_id, df.clinical_context):
        pc = parse_context(ctx)
        assert not pc.warnings, (rid, pc.warnings)
        assert pc.encounter_id and pc.age is not None


def test_exact_name_matching_avoids_lookalikes():
    df = load_records(ROOT / "data/records.csv")
    pc = parse_context(df.iloc[0].clinical_context)  # EVAL-0001
    assert [o.value for o in pc.obs("platelets")] == [240.2]
    assert [o.value for o in pc.obs("bilirubin_total")] == [0.7]  # not urine strip 0.5
    assert [o.value for o in pc.obs("wbc")] == [0.8]
    assert [o.value for o in pc.obs("creatinine")] == [3.2]
    assert len(pc.medications) == 2