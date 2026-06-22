"""
compliance/safety_layer.py
==========================
Anbu Health AI — India Medical Compliance Layer
Patent: 202641043947 | Author: Rajaganapathy M, SRM University

Enforces:
  - Telemedicine Practice Guidelines 2020 (MoHFW + NMC)
  - DPDP Act 2023 (Digital Personal Data Protection)
  - IT Act 2000 / IT Rules 2021
  - Drug & Cosmetics Act (Schedule H/H1 blocking)
  - Consumer Protection Act 2019

Drop this file into: backend/compliance/safety_layer.py
Create empty:         backend/compliance/__init__.py
"""

import re
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 1. TELEMEDICINE GUIDELINES 2020 — Prohibited AI behaviours
#    Source: MoHFW Telemedicine Practice Guidelines, March 25 2020
#    These are things only a Registered Medical Practitioner (RMP) can do.
# ──────────────────────────────────────────────────────────────────────────────

PROHIBITED_PATTERNS: List[Tuple[str, str]] = [
    # Claiming to diagnose
    (r'\byou have\b.{0,40}\b(disease|disorder|cancer|diabetes|tuberculosis|HIV|hepatitis)\b', "diagnosis_claim"),
    (r'\bI diagnose\b', "diagnosis_claim"),
    (r'\bmy diagnosis\b', "diagnosis_claim"),
    # Writing prescriptions
    (r'\bprescription\b.{0,20}\b(for you|is:|below)\b', "prescription_claim"),
    (r'\bprescribe\b', "prescription_claim"),
    # Telling patient to stop existing medication
    (r'\b(stop|discontinue|don\'t take|avoid taking)\b.{0,30}\b(your|current|existing)\b.{0,20}\b(medicine|medication|tablets|insulin)\b', "stop_medication"),
    # 100% cure / guarantee language
    (r'\b(100%|hundred percent)\s+(cure|guaranteed|safe|effective)\b', "false_guarantee"),
    (r'\bguaranteed (cure|treatment|result)\b', "false_guarantee"),
    (r'\bno side effects\b', "false_safety_claim"),
    (r'\bcompletely safe\b', "false_safety_claim"),
    (r'\balways works\b', "false_safety_claim"),
    (r'\bnever causes\b', "false_safety_claim"),
    # Telling patient to avoid hospital/emergency
    (r'\b(no need|don\'t need|not necessary)\b.{0,30}\b(hospital|doctor|emergency)\b', "emergency_misdirection"),
    # Specific dosage for Schedule H1 drugs (RMP-only)
    (r'\btake\s+\d+\s*(mg|mcg|ml)\s+of\s+(warfarin|methotrexate|lithium|digoxin|phenytoin|clonazepam|alprazolam|tramadol|morphine|fentanyl|codeine)\b', "h1_drug_dosage"),
]

# ──────────────────────────────────────────────────────────────────────────────
# 2. EMERGENCY ESCALATION — Mandatory 108 referral
#    Telemedicine Guidelines 2020, Clause 3.5.5:
#    "In case of life-threatening emergency, the RMP must advise the patient
#     to immediately go to the nearest hospital / call 108."
#    We apply this standard to AI responses too.
# ──────────────────────────────────────────────────────────────────────────────

EMERGENCY_PATTERNS = [
    # English
    r'\bchest pain\b',
    r'\bheart attack\b',
    r'\bstroke\b',
    r'\bseizure\b',
    r'\bunconscious\b',
    r'\bnot breathing\b',
    r'\bdifficulty breathing\b',
    r'\bsevere bleeding\b',
    r'\bhead injury\b',
    r'\bpoisoning\b',
    r'\boverdose\b',
    r'\bsuicid\b',
    r'\bhigh fever.{0,20}child|child.{0,20}high fever\b',
    # Tamil
    r'\bமார்பு வலி\b',
    r'\bமூச்சு திணறல்\b',
    r'\bவலிப்பு\b',
    r'\bநினைவிழ\b',
    r'\bரத்தம் வருகிறது\b',
]

EMERGENCY_MSG_EN = (
    "🚨 EMERGENCY — Call 108 (Free Ambulance) immediately or go to the nearest "
    "Government Hospital. Do NOT wait. Do NOT rely on AI in an emergency."
)
EMERGENCY_MSG_TA = (
    "🚨 அவசரம் — உடனே 108 (Free Ambulance) call பண்ணுங்க அல்லது nearest "
    "Government Hospital போங்க. தாமதிக்காதீங்க. Emergencyல AI நம்பாதீங்க."
)

# ──────────────────────────────────────────────────────────────────────────────
# 3. SCHEDULE H / H1 DRUGS — Drug & Cosmetics Act
#    These must not be dispensed without a prescription.
#    Source: Drugs and Cosmetics (Amendment) Rules 2013
# ──────────────────────────────────────────────────────────────────────────────

SCHEDULE_H1_DRUGS = {
    # Anticoagulants
    "warfarin", "heparin", "rivaroxaban", "apixaban",
    # Immunosuppressants
    "methotrexate", "cyclosporine", "tacrolimus",
    # Psychiatric / Controlled
    "lithium", "clonazepam", "alprazolam", "diazepam", "lorazepam",
    "haloperidol", "clozapine", "quetiapine",
    # Cardiac
    "digoxin", "amiodarone",
    # Anticonvulsants
    "phenytoin", "carbamazepine", "valproate", "levetiracetam",
    # Opioids
    "tramadol", "morphine", "fentanyl", "codeine", "oxycodone",
    # Antibiotics (Schedule H)
    "rifampicin", "isoniazid", "ethambutol",  # TB drugs — require DOTS supervision
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. DPDP ACT 2023 — Sensitive data redaction
#    Section 2(t): "sensitive personal data" includes health data.
#    Section 8(7): Must not retain data beyond purpose.
# ──────────────────────────────────────────────────────────────────────────────

SENSITIVE_DATA_REDACTION = [
    (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', '[AADHAAR REDACTED]'),   # Aadhaar
    (r'\b[A-Z]{5}\d{4}[A-Z]\b', '[PAN REDACTED]'),                  # PAN card
    (r'\b\d{10}\b', '[MOBILE REDACTED]'),                            # Mobile number (10-digit)
]

# ──────────────────────────────────────────────────────────────────────────────
# 5. MANDATORY DISCLAIMERS — Telemedicine Guidelines 2020
#    Every AI health response must carry a clear disclaimer.
# ──────────────────────────────────────────────────────────────────────────────

COMPLIANCE_DISCLAIMER = {
    "en": (
        "ℹ️ Anbu Health AI provides health information only — not medical advice, diagnosis, "
        "or prescription. For any treatment or medicine, consult a Registered Medical "
        "Practitioner (RMP / Doctor). In an emergency, call 108."
    ),
    "ta": (
        "ℹ️ Anbu Health AI health information மட்டும் தரும் — medical advice, diagnosis "
        "அல்லது prescription இல்ல. மருந்து அல்லது treatment-க்கு Registered Doctor கிட்ட "
        "போங்க. Emergency-ல 108 call பண்ணுங்க."
    ),
}

# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def is_schedule_h1_drug(drug_name: str) -> bool:
    """Check if a drug name matches Schedule H/H1 list."""
    name_lower = drug_name.lower().strip()
    return any(h1 in name_lower for h1 in SCHEDULE_H1_DRUGS)


def redact_sensitive_data(text: str) -> str:
    """
    Redact Aadhaar, PAN before storing to database.
    DPDP Act 2023 — data minimization principle.
    """
    for pattern, replacement in SENSITIVE_DATA_REDACTION:
        text = re.sub(pattern, replacement, text)
    return text


def detect_prohibited_content(answer: str) -> List[str]:
    """
    Check AI answer for Telemedicine Guidelines violations.
    Returns list of violation type strings (empty = clean).
    """
    violations = []
    answer_lower = answer.lower()
    for pattern, vtype in PROHIBITED_PATTERNS:
        if re.search(pattern, answer_lower, re.IGNORECASE):
            violations.append(vtype)
    return list(set(violations))  # deduplicate


def detect_emergency(question: str, answer: str) -> bool:
    """Return True if emergency keywords found in question or answer."""
    combined = (question + " " + answer).lower()
    return any(re.search(p, combined, re.IGNORECASE) for p in EMERGENCY_PATTERNS)


def apply_compliance(
    question: str,
    final_answer: str,
    lang: str = "en",
    mode: str = "general",
) -> Dict:
    """
    Main compliance gate — call this on EVERY /api/analyze response
    before returning to the user.

    Returns:
        {
            "final_answer": str,           # safe, possibly overridden answer
            "compliance_disclaimer": str,  # mandatory disclaimer string
            "emergency_alert": str | None, # emergency banner text or None
            "violations_found": bool,      # True if AI output violated rules
            "violations": list,            # violation types (for logging only)
        }
    """
    # Step 1: Detect violations in AI answer
    violations = detect_prohibited_content(final_answer)

    # Step 2: Emergency detection
    is_emergency = detect_emergency(question, final_answer)
    emergency_msg: Optional[str] = None
    if is_emergency:
        emergency_msg = EMERGENCY_MSG_TA if lang == "ta" else EMERGENCY_MSG_EN
        logger.warning(f"[COMPLIANCE] Emergency detected — question: {question[:80]}")

    # Step 3: If violations found, replace with safe override
    safe_answer = final_answer
    if violations:
        logger.warning(f"[COMPLIANCE] Violations={violations} mode={mode}")
        safe_answer = (
            "நான் general health information மட்டும் சொல்ல முடியும். "
            "Diagnosis அல்லது prescription-க்கு Registered Doctor கிட்ட போங்க. "
            "Emergency-ல 108 call பண்ணுங்க."
        ) if lang == "ta" else (
            "I can share general health information only. "
            "For diagnosis or prescription, please consult a Registered Medical Practitioner. "
            "In an emergency, call 108."
        )

    # Step 4: Pick disclaimer language
    disclaimer = COMPLIANCE_DISCLAIMER.get(lang, COMPLIANCE_DISCLAIMER["en"])

    return {
        "final_answer": safe_answer,
        "compliance_disclaimer": disclaimer,
        "emergency_alert": emergency_msg,
        "violations_found": len(violations) > 0,
        "violations": violations,  # log internally, never expose to frontend
    }
