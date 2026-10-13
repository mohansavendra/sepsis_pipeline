"""Step 2: deterministic rule-based answerers. Each returns a RuleResult with
a yes/no value, evidence strings copied from the context, and review flags."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parser import ParsedContext

# ---- thresholds (from the assignment) ----
SIRS_TEMP_HIGH, SIRS_TEMP_LOW = 38.0, 36.0
SIRS_HR, SIRS_RR = 90.0, 20.0
SIRS_WBC_HIGH, SIRS_WBC_LOW = 12.0, 4.0
CREAT_MAX, PLT_MIN, BILI_MAX = 2.0, 100.0, 2.0

INFECTION_TERMS = re.compile(
    r"infection|sinusitis|otitis|pharyngitis|sore throat|cystitis|bronchitis|pneumonia|"
    r"cellulitis|sepsis|abscess|tonsillitis|meningitis|appendicitis|influenza|covid|"
    r"gastroenteritis|pyelonephritis|endocarditis|osteomyelitis", re.I)
ANTIBIOTIC_TERMS = re.compile(
    r"amoxicillin|clavulanate|penicillin|ampicillin|cefuroxime|cephalexin|ceftriaxone|"
    r"cefazolin|cefdinir|azithromycin|clarithromycin|erythromycin|doxycycline|"
    r"ciprofloxacin|levofloxacin|nitrofurantoin|trimethoprim|sulfamethoxazole|"
    r"vancomycin|metronidazole|clindamycin|gentamicin|piperacillin|meropenem", re.I)


@dataclass
class RuleResult:
    value: bool
    evidence: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)

    @property
    def evidence_text(self) -> str:
        return " | ".join(self.evidence)


def _fmt(o) -> str:
    return f"{o.name.split(' [')[0]} {o.value:g} {o.unit} at {o.time}"


def answer_sirs(pc: ParsedContext) -> RuleResult:
    met, flags, ev = [], [], []
    checks = [
        ("temperature", lambda v: v > SIRS_TEMP_HIGH or v < SIRS_TEMP_LOW, "temp >38.0 or <36.0 C"),
        ("heart_rate", lambda v: v > SIRS_HR, "HR >90"),
        ("resp_rate", lambda v: v > SIRS_RR, "RR >20"),
        ("wbc", lambda v: v > SIRS_WBC_HIGH or v < SIRS_WBC_LOW, "WBC >12 or <4"),
    ]
    missing = []
    for key, test, label in checks:
        obs = pc.obs(key)
        if not obs:
            missing.append(key)
            continue
        hits = [o for o in obs if test(o.value)]
        if hits:
            met.append(key)
            ev.extend(f"SIRS criterion met ({label}): {_fmt(o)}" for o in hits)
        else:
            ev.append(f"{key} measured, not met: " + "; ".join(_fmt(o) for o in obs))
    value = len(met) >= 2
    ev.insert(0, f"{len(met)} distinct SIRS criteria met ({', '.join(met) or 'none'})")
    if missing:
        ev.append("not recorded: " + ", ".join(missing))
        if not value:
            flags.append("SIRS not met but parameters missing: " + ", ".join(missing))
    if len(met) == 2 and any(len(pc.obs(k)) > 1 for k in met):
        flags.append("exactly 2 criteria; multiple measurements")
    return RuleResult(value, ev, flags)


def answer_infection(pc: ParsedContext) -> RuleResult:
    ev, flags = [], []
    hits = [p for p in pc.active_problems if INFECTION_TERMS.search(p)]
    reason_hit = bool(pc.reason and INFECTION_TERMS.search(pc.reason))
    if hits:
        ev.append("Infectious condition in active problems: " + "; ".join(hits))
    if reason_hit:
        ev.append(f"Infectious condition in reason for encounter: {pc.reason}")
    if not ev:
        ev.append(f"No infectious condition documented (reason: {pc.reason}; "
                  f"problems: {', '.join(pc.active_problems) or 'none recorded'})")
    if reason_hit and not hits:
        flags.append("infection only in reason for encounter, not in active problems")
    return RuleResult(bool(hits or reason_hit), ev, flags)


def answer_antibiotic(pc: ParsedContext) -> RuleResult:
    abx = [m for m in pc.medications if ANTIBIOTIC_TERMS.search(m.name)]
    if abx:
        first = min(abx, key=lambda m: m.time)
        ev = [f"Antibiotic started: {m.name} at {m.time}" for m in abx]
        ev.insert(0, f"Earliest antibiotic: {first.name} at {first.time}")
        return RuleResult(True, ev, [])
    ev = ["No antibiotic in 'Medications started during this encounter'"
          + (f" (other meds: {'; '.join(m.name for m in pc.medications)})" if pc.medications else " (none recorded)")]
    return RuleResult(False, ev, [])


def answer_organ(pc: ParsedContext) -> RuleResult:
    ev, flags = [], []
    checks = [("creatinine", lambda v: v > CREAT_MAX, "creatinine >2.0 mg/dL"),
              ("platelets", lambda v: v < PLT_MIN, "platelets <100 x10^3/uL"),
              ("bilirubin_total", lambda v: v > BILI_MAX, "total bilirubin >2.0 mg/dL")]
    met = False
    for key, test, label in checks:
        obs = pc.obs(key)
        if not obs:
            ev.append(f"{key} not recorded")
            continue
        hits = [o for o in obs if test(o.value)]
        met = met or bool(hits)
        ev.extend(f"Organ dysfunction criterion met ({label}): {_fmt(o)}" for o in hits)
        if not hits:
            ev.append(f"{key} not met: " + "; ".join(_fmt(o) for o in obs))
    if not met and not any(pc.obs(k) for k, _, _ in checks):
        flags.append("no organ-dysfunction labs recorded")
    return RuleResult(met, ev, flags)


ANSWERERS = {"SIRS": answer_sirs, "INFECTION": answer_infection,
             "ANTIBIOTIC": answer_antibiotic, "ORGAN_DYSFUNCTION": answer_organ}


def answer(family: str, pc: ParsedContext) -> RuleResult:
    return ANSWERERS[family](pc)