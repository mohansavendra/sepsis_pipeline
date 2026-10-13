"""Synthetic cases for `right_answer_wrong_reason` (never occurs in the supplied data)."""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sepsis.parser import parse_context
from sepsis.rules import answer
from sepsis.validate import validate_model_answer

SIRS_CTX = """ENCOUNTER abc12345 — inpatient, 2020-01-01 10:00
Patient: 50-year-old, sex M.
Reason for encounter: Encounter for problem.
Active problems: none recorded.

Observations recorded during this encounter:
  2020-01-01 10:00   Body temperature: 38.6 Cel
  2020-01-01 10:00   Heart rate: 80.0 /min
  2020-01-01 10:00   Respiratory rate: 22.0 /min

Medications started during this encounter: none recorded.
"""

INF_CTX = """ENCOUNTER abc12345 — ambulatory, 2020-01-01 10:00
Patient: 50-year-old, sex M.
Reason for encounter: Viral sinusitis (disorder).
Active problems: Viral sinusitis (disorder), Osteoarthritis of knee.

Observations recorded during this encounter:
  2020-01-01 10:00   Heart rate: 70.0 /min

Medications started during this encounter: none recorded.
"""


def _validate(family, ctx, model_value, expl):
    pc = parse_context(ctx)
    rule = answer(family, pc)
    return rule, validate_model_answer(family, pc, rule, ctx, model_value, expl)


def test_sirs_cites_value_that_does_not_meet_threshold():
    # True is the right verdict (temp + RR), but HR 80 is cited as a criterion and does not meet >90
    rule, v = _validate("SIRS", SIRS_CTX, True,
                        "2 SIRS criteria met: heart rate 80.0 /min at 2020-01-01 10:00; "
                        "temperature 38.6 Cel at 2020-01-01 10:00. Two or more criteria were met.")
    assert rule.value is True
    assert not v.acceptable and v.error_type == "right_answer_wrong_reason"
    assert "heart_rate" in v.reasons_text


def test_infection_cites_non_infectious_condition():
    rule, v = _validate("INFECTION", INF_CTX, True,
                        "Documented infection(s): Osteoarthritis of knee.")
    assert rule.value is True
    assert v.error_type == "right_answer_wrong_reason"


def test_verdict_yes_but_explanation_argues_no():
    rule, v = _validate("INFECTION", INF_CTX, True, "No infectious condition is documented.")
    assert v.error_type == "right_answer_wrong_reason"


def test_sound_answer_is_accepted():
    rule, v = _validate("SIRS", SIRS_CTX, True,
                        "2 SIRS criteria met: temperature 38.6 Cel at 2020-01-01 10:00; "
                        "respiratory rate 22.0 /min at 2020-01-01 10:00. Two or more criteria were met.")
    assert v.acceptable and v.error_type == ""
