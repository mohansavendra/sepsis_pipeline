"""Step 3: validate the OTHER model's answer against the clinical context.

Two questions are kept separate (see README, "How it works"):
  1. Is the verdict right?   -> compare with our rule-based prediction.
  2. Is the reasoning sound? -> every detail in the explanation must be traceable to the
                                context, must actually satisfy the criterion it is cited
                                for, and must be enough to justify the verdict.

Error precedence when several problems exist (most severe first):
    wrong_value > hallucinated_detail > right_answer_wrong_reason > incomplete
Lower-precedence problems are still recorded in `reasons` for the reviewer.

Design notes
- Rule-based, no LLM: the other model's explanations are templated, so every claim can be
  parsed and checked deterministically against the parsed context.
- Sentences we cannot recognise are NOT silently trusted: they raise a human-review flag.
- The watch-list of "unsupported source" terms is hand-written and will not catch every
  possible hallucination; the unrecognised-sentence flag is the safety net.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parser import ParsedContext
from .rules import (ANTIBIOTIC_TERMS, BILI_MAX, CREAT_MAX, INFECTION_TERMS, PLT_MIN, RuleResult,
                    SIRS_HR, SIRS_RR, SIRS_TEMP_HIGH, SIRS_TEMP_LOW, SIRS_WBC_HIGH, SIRS_WBC_LOW)

TS = r"\d{4}-\d\d-\d\d \d\d:\d\d"
NUM = r"-?\d+(?:\.\d+)?"
TS_RE = re.compile(TS)

# ---- claim parsers -------------------------------------------------------------------
SIRS_CLAIM_RE = re.compile(
    rf"(white blood cell count|wbc|leukocytes?|body temperature|temperature|heart rate|respiratory rate)"
    rf"\s+({NUM})\s*([^\s;,]+)?(?:\s+at\s+({TS}))?", re.I)
ORGAN_CLAIM_RE = re.compile(
    rf"(creatinine|platelets?|total bilirubin|bilirubin)\s+({NUM})\s*([^\s;,()]+)?(?:\s+at\s+({TS}))?", re.I)
ABX_CLAIM_RE = re.compile(rf"Antibiotic administered:\s*(.+?)(?:,\s*started\s*({TS}))?\.?\s*$", re.I)
INFECTION_CLAIM_RE = re.compile(r"Documented infection\(s\):\s*(.+?)\.?\s*$", re.I)
SIRS_COUNT_RE = re.compile(r"(\d+)\s+SIRS criteria met", re.I)
# a number followed by a clinical unit = a quantitative claim that must exist in the context
NUM_UNIT_RE = re.compile(
    rf"({NUM})\s*(mg/dL|mmol/L|g/dL|g/L|U/L|10\*3/uL|10\*6/uL|/min|Cel|mL/min|fL|pg|mmHg|mm\[Hg\]|bpm|ng/mL)")

KEY_OF = {"white blood cell count": "wbc", "wbc": "wbc", "leukocyte": "wbc", "leukocytes": "wbc",
          "temperature": "temperature", "body temperature": "temperature",
          "heart rate": "heart_rate", "respiratory rate": "resp_rate",
          "creatinine": "creatinine", "platelet": "platelets", "platelets": "platelets",
          "bilirubin": "bilirubin_total", "total bilirubin": "bilirubin_total"}

SIRS_TESTS = {
    "temperature": lambda v: v > SIRS_TEMP_HIGH or v < SIRS_TEMP_LOW,
    "heart_rate": lambda v: v > SIRS_HR,
    "resp_rate": lambda v: v > SIRS_RR,
    "wbc": lambda v: v > SIRS_WBC_HIGH or v < SIRS_WBC_LOW,
}
ORGAN_TESTS = {
    "creatinine": lambda v: v > CREAT_MAX,
    "platelets": lambda v: v < PLT_MIN,
    "bilirubin_total": lambda v: v > BILI_MAX,
}

# Terms a model might invent as "supporting" evidence. Flagged only if absent from the context.
UNSUPPORTED_TERMS = re.compile(
    r"lactate|lactic|procalcitonin|c-reactive|\bcrp\b|culture|hypotensi|blood pressure|\bmap\b|"
    r"qsofa|\bsofa\b|troponin|\binr\b|attending|physician|nurse|clinician|consult|radiograph|"
    r"x-ray|ct scan|septic|chart review|discharge summary", re.I)

# ---- recognised sentence templates ---------------------------------------------------
POSITIVE_RE = {
    "SIRS": re.compile(r"^\d+ SIRS criteria met:|^Two or more criteria were met", re.I),
    "INFECTION": re.compile(r"^Documented infection\(s\):", re.I),
    "ANTIBIOTIC": re.compile(r"^Antibiotic administered:", re.I),
    "ORGAN_DYSFUNCTION": re.compile(r"^Organ dysfunction criteria met:", re.I),
}
NEGATIVE_RE = {
    "SIRS": re.compile(r"^No SIRS criteria were met|^Fewer than two criteria were met", re.I),
    "INFECTION": re.compile(r"^No infectious condition is documented", re.I),
    "ANTIBIOTIC": re.compile(r"^No antibiotic was administered", re.I),
    "ORGAN_DYSFUNCTION": re.compile(r"^No creatinine, platelet or bilirubin value met", re.I),
}
# recognised but contain no evidence at all
VACUOUS_RE = re.compile(r"^On review the criteria were (not )?met|^The determination follows from", re.I)

SEVERITY = ["wrong_value", "hallucinated_detail", "right_answer_wrong_reason", "incomplete"]


@dataclass
class Validation:
    acceptable: bool
    error_type: str = ""                       # blank when acceptable
    reasons: dict = field(default_factory=dict)  # error_type -> [explanations]
    review_flags: list = field(default_factory=list)

    @property
    def reasons_text(self) -> str:
        return " | ".join(f"[{t}] {r}" for t in SEVERITY for r in self.reasons.get(t, []))


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z])", text.strip()) if s.strip()]


def _grounded(pc: ParsedContext, key: str, value: float, time: str | None):
    """Return (found, note). found=False when value (or value+time) is not in the context."""
    obs = pc.obs(key)
    same_val = [o for o in obs if abs(o.value - value) < 1e-9]
    if not same_val:
        return False, f"{key} {value:g} not found in context"
    if time and not any(o.time == time for o in same_val):
        return False, f"{key} {value:g} exists but not at {time} (recorded: {', '.join(o.time for o in same_val)})"
    return True, ""


def validate_model_answer(family: str, pc: ParsedContext, rule: RuleResult, context_text: str,
                          model_value: bool, explanation: str) -> Validation:
    reasons: dict[str, list[str]] = {t: [] for t in SEVERITY}
    flags: list[str] = []
    expl = explanation or ""
    ctx_lower = context_text.lower()

    # ---- 1. verdict -------------------------------------------------------------
    if bool(model_value) != bool(rule.value):
        reasons["wrong_value"].append(
            f"model said {bool(model_value)}, context supports {bool(rule.value)}")

    sentences = _split_sentences(expl)
    pos_re, neg_re = POSITIVE_RE[family], NEGATIVE_RE[family]
    has_pos = any(pos_re.search(s) for s in sentences)
    has_neg = any(neg_re.search(s) for s in sentences)

    # ---- 2. hallucination: details that cannot be traced to the context ---------
    for ts in sorted(set(TS_RE.findall(expl))):
        if ts not in context_text:
            reasons["hallucinated_detail"].append(f"timestamp {ts} not in context")
    obs_pairs = {(round(o.value, 6), o.unit) for o in pc.observations}
    spans_checked = set()
    for m in NUM_UNIT_RE.finditer(re.sub(r"\(threshold [^)]*\)", "", expl)):
        v, unit = float(m.group(1)), m.group(2)
        if (round(v, 6), unit) not in obs_pairs and (v, unit) not in spans_checked:
            spans_checked.add((v, unit))
            reasons["hallucinated_detail"].append(f"value '{m.group(0)}' not in context")
    for term in sorted({m.group(0).lower() for m in UNSUPPORTED_TERMS.finditer(expl)}):
        if term not in ctx_lower:
            reasons["hallucinated_detail"].append(f"'{term}' cited but never mentioned in context")

    # ---- 3. family-specific claim checks (grounding + validity + sufficiency) ----
    valid_cited: set[str] = set()   # distinct, grounded, threshold-satisfying items cited
    if family in ("SIRS", "ORGAN_DYSFUNCTION"):
        regex, tests = (SIRS_CLAIM_RE, SIRS_TESTS) if family == "SIRS" else (ORGAN_CLAIM_RE, ORGAN_TESTS)
        for m in regex.finditer(re.sub(r"\(threshold [^)]*\)", "", expl)):
            key = KEY_OF[m.group(1).lower()]
            val, time = float(m.group(2)), m.group(4)
            ok, note = _grounded(pc, key, val, time)
            if not ok:
                # a bad timestamp on a real value is a hallucinated detail as well
                if note not in reasons["hallucinated_detail"]:
                    reasons["hallucinated_detail"].append(note)
            elif not tests[key](val):
                reasons["right_answer_wrong_reason"].append(
                    f"{key} {val:g} cited as meeting a criterion it does not meet")
            else:
                valid_cited.add(key)
        if family == "SIRS" and (cm := SIRS_COUNT_RE.search(expl)):
            claimed = int(cm.group(1))
            if len(valid_cited) < claimed:
                reasons["incomplete"].append(
                    f"claims {claimed} SIRS criteria but substantiates {len(valid_cited)} "
                    f"(rule found: {', '.join(k for k in SIRS_TESTS if any(SIRS_TESTS[k](o.value) for o in pc.obs(k))) or 'none'})")
        need = 2 if family == "SIRS" else 1
        if model_value and has_pos and len(valid_cited) < need and not reasons["incomplete"]:
            reasons["incomplete"].append(f"cites {len(valid_cited)} valid criterion(s); {need} needed to justify 'yes'")

    elif family == "INFECTION":
        known = [c for c in ([pc.reason] if pc.reason else []) + pc.active_problems]
        for m in INFECTION_CLAIM_RE.finditer(expl):
            claim = m.group(1)
            rest = claim
            for cond in sorted(set(known), key=len, reverse=True):
                if cond in rest:
                    rest = rest.replace(cond, "")
                    if INFECTION_TERMS.search(cond):
                        valid_cited.add(cond)
                    else:
                        reasons["right_answer_wrong_reason"].append(
                            f"'{cond}' is documented but is not an infectious condition")
            if rest.strip(" ;,."):
                reasons["hallucinated_detail"].append(f"condition '{rest.strip(' ;,.')}' not in problems/reason")
        if model_value and has_pos and not valid_cited and not reasons["hallucinated_detail"]:
            reasons["incomplete"].append("names no documented infectious condition")

    elif family == "ANTIBIOTIC":
        for s in sentences:
            m = ABX_CLAIM_RE.match(s)
            if not m:
                continue
            name, started = m.group(1).strip(), m.group(2)
            meds = [x for x in pc.medications if x.name.lower() == name.lower()]
            if not meds:
                reasons["hallucinated_detail"].append(f"medication '{name}' not in 'Medications started'")
            elif started and not any(x.time == started for x in meds):
                reasons["hallucinated_detail"].append(
                    f"'{name}' start time {started} differs from context ({meds[0].time})")
            elif not ANTIBIOTIC_TERMS.search(name):
                reasons["right_answer_wrong_reason"].append(f"'{name}' is not an antibiotic")
            else:
                valid_cited.add(name)
                if not started:
                    flags.append("antibiotic named without start time")
        if model_value and has_pos and not valid_cited and not reasons["hallucinated_detail"]:
            reasons["incomplete"].append("names no antibiotic")

    # ---- 4. internal consistency & vacuous answers -----------------------------------
    if not reasons["wrong_value"]:
        if model_value and has_neg and not has_pos:
            reasons["right_answer_wrong_reason"].append("verdict is yes but explanation argues no")
        if (not model_value) and has_pos and not has_neg:
            reasons["right_answer_wrong_reason"].append("verdict is no but explanation argues yes")
        if not has_pos and not has_neg:
            reasons["right_answer_wrong_reason"].append("verdict is correct but explanation cites no evidence")

    # ---- 5. unrecognised sentences are never silently trusted -------------------------
    for s in sentences:
        if not (pos_re.search(s) or neg_re.search(s) or VACUOUS_RE.search(s)):
            flags.append(f"unverified sentence in explanation: {s[:80]!r}")

    reasons = {t: v for t, v in reasons.items() if v}
    error = next((t for t in SEVERITY if t in reasons), "")
    return Validation(acceptable=not error, error_type=error, reasons=reasons, review_flags=flags)
