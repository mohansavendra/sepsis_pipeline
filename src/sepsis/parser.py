"""Step 1: parse the semi-structured `clinical_context` text into structured facts.

Design notes
- Deterministic regex parsing: the context format is machine-generated, so rules are
  reproducible and auditable. Every fact keeps its source line (`raw`) for evidence.
- Nothing is invented: if a section or value is absent it stays empty / None.
- Lab/vital names are matched on EXACT names to avoid look-alikes
  (e.g. "Platelet distribution width", urine bilirubin, "Microalbumin Creatinine Ratio").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

HEADER_RE = re.compile(r"^ENCOUNTER (\S+) — (\w+), (\d{4}-\d\d-\d\d \d\d:\d\d)")
PATIENT_RE = re.compile(r"^Patient: (\d+)-year-old, sex (\w)")
OBS_RE = re.compile(r"^\s+(\d{4}-\d\d-\d\d \d\d:\d\d)\s+(.*?): (-?\d+(?:\.\d+)?) (.*)$")
MED_RE = re.compile(r"^\s+(\d{4}-\d\d-\d\d \d\d:\d\d)\s+(.*\S)\s*$")

# canonical key -> exact observation name as it appears in the context
OBS_NAMES = {
    "temperature": "Body temperature",
    "heart_rate": "Heart rate",
    "resp_rate": "Respiratory rate",
    "wbc": "Leukocytes [#/volume] in Blood by Automated count",
    "creatinine": "Creatinine",
    "platelets": "Platelets [#/volume] in Blood by Automated count",
    "bilirubin_total": "Bilirubin.total [Mass/volume] in Serum or Plasma",
}
_NAME_TO_KEY = {v: k for k, v in OBS_NAMES.items()}


@dataclass
class Observation:
    time: str
    name: str
    value: float
    unit: str
    raw: str
    key: Optional[str] = None  # canonical key if it is one we use


@dataclass
class Medication:
    time: str
    name: str
    raw: str


@dataclass
class ParsedContext:
    encounter_id: Optional[str] = None
    encounter_class: Optional[str] = None
    encounter_time: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    reason: Optional[str] = None
    active_problems: list[str] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    medications: list[Medication] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # parse problems -> human review

    def obs(self, key: str) -> list[Observation]:
        return [o for o in self.observations if o.key == key]


def _split_problems(text: str) -> list[str]:
    text = text.strip().rstrip(".")
    if not text or text.lower() == "none recorded":
        return []
    return [p.strip() for p in text.split(", ") if p.strip()]


def parse_context(text: str) -> ParsedContext:
    pc = ParsedContext()
    section = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if (m := HEADER_RE.match(line)):
            pc.encounter_id, pc.encounter_class, pc.encounter_time = m.groups()
        elif (m := PATIENT_RE.match(line)):
            pc.age, pc.sex = int(m.group(1)), m.group(2)
        elif line.startswith("Reason for encounter:"):
            pc.reason = line.split(":", 1)[1].strip().rstrip(".")
        elif line.startswith("Active problems:"):
            pc.active_problems = _split_problems(line.split(":", 1)[1])
        elif line.startswith("Observations recorded"):
            section = "obs"
        elif line.startswith("Medications started"):
            section = None if "none recorded" in line.lower() else "meds"
        elif section == "obs":
            if (m := OBS_RE.match(line)):
                t, name, val, unit = m.groups()
                pc.observations.append(
                    Observation(t, name, float(val), unit.strip(), line.strip(), _NAME_TO_KEY.get(name))
                )
            else:
                pc.warnings.append(f"unparsed observation line: {line.strip()!r}")
        elif section == "meds":
            if (m := MED_RE.match(line)):
                pc.medications.append(Medication(m.group(1), m.group(2), line.strip()))
            else:
                pc.warnings.append(f"unparsed medication line: {line.strip()!r}")
    if pc.encounter_id is None:
        pc.warnings.append("missing encounter header")
    return pc


def load_records(records_path, labels_path=None) -> pd.DataFrame:
    """Load records.csv (+ optional dev labels) and validate required columns."""
    df = pd.read_csv(records_path)
    required = {"record_id", "split", "question_family", "question", "clinical_context",
                "model_answer_value", "model_answer_explanation"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"records file missing columns: {sorted(missing)}")
    if df.record_id.duplicated().any():
        raise ValueError("duplicate record_id values")
    if labels_path:
        df = df.merge(pd.read_csv(labels_path), on="record_id", how="left")
        # leakage guard: labels must exist only for dev rows
        assert df.loc[df.split != "dev", "correct_value"].isna().all(), "labels found for non-dev rows"
    return df